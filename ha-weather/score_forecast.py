#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score the Open-Meteo models against what actually happened at home.

radar_notify.py appends one line per build to forecast_log.jsonl. This reads
that log and asks the question the model choice was a guess about: which of
ecmwf_ifs025 / icon_seamless / gfs_seamless is actually right at this location?

Run it with no arguments to use the default path, or point it somewhere:

    python score_forecast.py                      # /config/www/radar/forecast_log.jsonl
    python score_forecast.py path/to/log.jsonl
    python score_forecast.py --model ecmwf_ifs025 --min-hours 24

WHAT THE LABEL IS, AND IS NOT
-----------------------------
The observation is the radar, not the gauge. sensor radar "rain_now" is the
RainViewer echo within ~1.5 km of home, sampled every 15 minutes. It is
independent of the models, which is what makes the comparison meaningful, but
it is not ground truth: radar sees the beam, not the raingauge. Treat the
numbers as "does this model agree with an independent observation", not as
absolute skill.

The ThaiWater gauge (BKK021, ~7.8 km) is a rolling 24 h total, so only a
*positive* change between two reads confirms that rain fell. A zero change
does not prove it stayed dry, so the gauge is used only to corroborate events,
never to score dry hours.

WHY THE HORIZON MATTERS
-----------------------
A model's mm60 is a forecast for the next hour, so the label for record i is
"did the radar see rain at any point in the next hour" - records i..i+4. The
records must be close together in time; gaps caused by a failed build are
skipped rather than treated as dry.
"""
import json
import os
import sys
from datetime import datetime

DEFAULT_LOG = os.environ.get("RADAR_LOG", "/config/www/radar/forecast_log.jsonl")
HORIZON_SLOTS = 4          # 4 x 15 min = the hour a mm60 forecast covers
MAX_GAP_MIN = 25           # a longer gap means the build failed; skip the window
MODEL_MM = 0.1             # mm in the hour that counts as "rain predicted"
MODEL_PROB = 50            # percent that also counts as "rain predicted"


def parse_time(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def load(path):
    """Read the JSONL log, skipping anything malformed or without a time."""
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue                      # a torn last line is expected
            r["_t"] = parse_time(r.get("t"))
            if r["_t"] is not None:
                out.append(r)
    out.sort(key=lambda r: r["_t"])
    return out


def models_in(records):
    seen = []
    for r in records:
        for m in (r.get("m") or {}):
            if m not in seen:
                seen.append(m)
    return seen


def predicted(rec, model):
    """Did this model call for rain in its next hour?"""
    d = (rec.get("m") or {}).get(model) or {}
    mm = d.get("mm60")
    pr = d.get("prob")
    return bool((mm or 0) >= MODEL_MM or (pr or 0) >= MODEL_PROB)


def consensus_predicted(rec):
    """Did the shipped disagreement-dict call for rain? Same majority rule
    radar_notify uses, recomputed from the logged per-model values."""
    m = rec.get("m") or {}
    need = rec.get("need") or 2
    if not m:
        return None
    return sum(1 for k in m if predicted(rec, k)) >= need


def observe(records, i):
    """Was rain observed from record i through the next hour?

    True/False, or None when the window is incomplete - a build failed inside
    it, or the log ends. An unknown hour must never be scored as a dry hour,
    or every gap in the log would be counted as a model success.

    The window is inclusive of i: a mm60 forecast is valid from t, so rain
    observed at t belongs to it.
    """
    start = records[i]["_t"]
    seen = 0
    rained = False
    for j in range(i, min(i + HORIZON_SLOTS + 1, len(records))):
        if j > i:
            step = (records[j]["_t"]
                    - records[j - 1]["_t"]).total_seconds() / 60.0
            if step > MAX_GAP_MIN:
                return None                   # a missing build breaks the hour
        if (records[j]["_t"] - start).total_seconds() / 60.0 \
                > HORIZON_SLOTS * 15 + MAX_GAP_MIN:
            break
        seen += 1
        if records[j].get("rain_now"):
            rained = True
    if seen < HORIZON_SLOTS + 1:
        return None                           # too little of the hour covered
    return rained


def score(records):
    """Per-model confusion counts against the radar label."""
    names = models_in(records)
    keys = names + ["consensus"]
    res = {k: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for k in keys}
    windows = 0
    for i in range(len(records)):
        rained = observe(records, i)
        if rained is None:
            continue
        windows += 1
        for k in keys:
            pred = (consensus_predicted(records[i]) if k == "consensus"
                    else predicted(records[i], k))
            if pred is None:
                continue
            if pred and rained:
                res[k]["tp"] += 1
            elif pred and not rained:
                res[k]["fp"] += 1
            elif rained:
                res[k]["fn"] += 1
            else:
                res[k]["tn"] += 1
    return res, windows


def gauge_events(records):
    """Hours the gauge itself confirms, as a sanity check on the radar label.

    A positive change in the rolling 24 h total means rain fell; anything else
    is ambiguous and is not counted.
    """
    confirmed = 0
    for a, b in zip(records, records[1:]):
        ga, gb = a.get("gauge24"), b.get("gauge24")
        if ga is None or gb is None:
            continue
        gap = (b["_t"] - a["_t"]).total_seconds() / 60.0
        if 0 < gap <= 45 and gb - ga > 0.1:
            confirmed += 1
    return confirmed


def pct(n, d):
    return "  -  " if not d else "%5.1f%%" % (100.0 * n / d)


def report(res, windows, records, min_hours=0):
    print("windows scored      : %d  (a window needs %d contiguous records)"
          % (windows, HORIZON_SLOTS + 1))
    if windows < min_hours:
        print("\nNOT ENOUGH DATA YET: %d of %d windows. The ranking below is"
              % (windows, min_hours))
        print("not meaningful until there are at least %d. Let it run."
              % min_hours)
    g = gauge_events(records)
    print("gauge-confirmed rain: %d transitions (rolling 24 h total rose)"
          % g)
    print()
    hdr = ("model", "hit", "false alarm", "miss", "correct dry",
           "precision", "recall")
    print("%-16s %5s %11s %5s %11s %11s %8s" % hdr)
    print("-" * 74)
    rows = sorted(res.items(),
                  key=lambda kv: -(kv[1]["tp"] * 2 - kv[1]["fp"] * 1
                                   - kv[1]["fn"] * 2))
    for name, c in rows:
        prec = pct(c["tp"], c["tp"] + c["fp"])
        rec = pct(c["tp"], c["tp"] + c["fn"])
        print("%-16s %5d %11d %5d %11d %11s %8s"
              % (name, c["tp"], c["fp"], c["fn"], c["tn"], prec, rec))
    print()
    print("precision = of the hours it called rain, how many the radar agreed")
    print("recall    = of the hours the radar saw rain, how many it called")
    print("A model that never calls rain scores 0% recall and never a false")
    print("alarm; read the counts together, not one column alone.")


def main(argv):
    args = [a for a in argv[1:]]
    path = DEFAULT_LOG
    min_hours = 0
    if "--model" in args:
        i = args.index("--model")
        wanted = args[i + 1]
        del args[i:i + 2]
        print("filtering to model: %s\n" % wanted)
    else:
        wanted = None
    if "--min-hours" in args:
        i = args.index("--min-hours")
        min_hours = int(args[i + 1])
        del args[i:i + 2]
    if args:
        path = args[0]

    if not os.path.exists(path):
        print("no log at %s" % path)
        print("radar_notify.py writes it on every build; let it run for a while.")
        return 1
    records = load(path)
    if not records:
        print("log at %s holds no usable records" % path)
        return 1
    print("log        : %s" % path)
    print("records    : %d  (%s .. %s)"
          % (len(records), records[0]["_t"].strftime("%Y-%m-%d %H:%M"),
             records[-1]["_t"].strftime("%Y-%m-%d %H:%M")))
    res, windows = score(records)
    if wanted:
        res = {k: v for k, v in res.items() if k == wanted} or res
    report(res, windows, records, min_hours)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
