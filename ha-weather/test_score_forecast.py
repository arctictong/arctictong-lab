"""Tests for score_forecast.py. Run with: python test_score_forecast.py

The scorer decides which model gets believed, so its arithmetic and its
treatment of gaps both matter. Everything here is offline: the log is built in
memory and nothing touches the network.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_forecast as sc

T0 = datetime(2026, 10, 6, 9, 0)


def mk(spec, start=T0, step=15):
    """Build records from a compact spec: [(rain_now, gauge, models), ...].

    models is {name: mm60} with the probability left at 0, since the amount is
    what most tests are about.
    """
    out = []
    for i, item in enumerate(spec):
        rain_now, gauge, models = item
        out.append({
            "t": (start + timedelta(minutes=i * step)).isoformat(),
            "_t": start + timedelta(minutes=i * step),
            "rain_now": rain_now,
            "gauge24": gauge,
            "need": 2,
            "m": {k: {"mm60": v, "prob": 0} for k, v in (models or {}).items()},
        })
    return out


WET = {"ecmwf_ifs025": 0.5, "icon_seamless": 0.5, "gfs_seamless": 0.5}
DRY = {"ecmwf_ifs025": 0.0, "icon_seamless": 0.0, "gfs_seamless": 0.0}


class TestObserve(unittest.TestCase):
    """The label the whole comparison hangs on."""

    def test_a_full_dry_hour_is_false(self):
        recs = mk([(False, 0, DRY)] * 6)
        self.assertIs(sc.observe(recs, 0), False)

    def test_rain_anywhere_in_the_hour_is_true(self):
        recs = mk([(False, 0, DRY), (False, 0, DRY), (True, 0, DRY),
                   (False, 0, DRY), (False, 0, DRY), (False, 0, DRY)])
        self.assertIs(sc.observe(recs, 0), True)

    def test_the_last_records_cannot_form_a_window(self):
        """The final hour of the log is incomplete, so it is unknown - not
        dry."""
        recs = mk([(False, 0, DRY)] * 6)
        self.assertIsNone(sc.observe(recs, 4))
        self.assertIsNone(sc.observe(recs, 5))

    def test_a_gap_inside_the_hour_is_unknown(self):
        """A missed build must not be scored as a dry hour: that would count
        every outage as a model success."""
        recs = mk([(False, 0, DRY)] * 4)
        recs += mk([(False, 0, DRY)] * 4, start=T0 + timedelta(minutes=60 + 90))
        for r in recs:
            r["_t"] = sc.parse_time(r["t"])
        self.assertIsNone(sc.observe(recs, 0))

    def test_too_short_a_log_is_unknown(self):
        recs = mk([(False, 0, DRY)] * 3)
        self.assertIsNone(sc.observe(recs, 0))


class TestPredicted(unittest.TestCase):
    def test_amount_alone_predicts_rain(self):
        r = {"m": {"x": {"mm60": 0.5, "prob": 0}}}
        self.assertTrue(sc.predicted(r, "x"))

    def test_probability_alone_predicts_rain(self):
        r = {"m": {"x": {"mm60": 0.0, "prob": 80}}}
        self.assertTrue(sc.predicted(r, "x"))

    def test_below_both_thresholds_is_dry(self):
        r = {"m": {"x": {"mm60": 0.05, "prob": 40}}}
        self.assertFalse(sc.predicted(r, "x"))

    def test_a_missing_model_is_dry_not_a_crash(self):
        self.assertFalse(sc.predicted({"m": {}}, "nope"))
        self.assertFalse(sc.predicted({}, "nope"))


class TestConsensusPredicted(unittest.TestCase):
    def test_majority_agrees(self):
        r = {"need": 2, "m": {"a": {"mm60": 0.5, "prob": 0},
                              "b": {"mm60": 0.5, "prob": 0},
                              "c": {"mm60": 0.0, "prob": 0}}}
        self.assertTrue(sc.consensus_predicted(r))

    def test_one_model_is_not_a_majority(self):
        r = {"need": 2, "m": {"a": {"mm60": 0.5, "prob": 0},
                              "b": {"mm60": 0.0, "prob": 0},
                              "c": {"mm60": 0.0, "prob": 0}}}
        self.assertFalse(sc.consensus_predicted(r))

    def test_no_models_is_unknown(self):
        self.assertIsNone(sc.consensus_predicted({"need": 2, "m": {}}))


class TestScore(unittest.TestCase):
    def test_a_perfect_model_scores_all_hits(self):
        # rain everywhere, every model agrees
        recs = mk([(True, 0, WET)] * 6)
        res, windows = sc.score(recs)
        self.assertEqual(windows, 2)
        self.assertEqual(res["ecmwf_ifs025"], {"tp": 2, "fp": 0, "fn": 0, "tn": 0})

    def test_a_model_that_never_calls_rain_only_misses(self):
        recs = mk([(True, 0, DRY)] * 6)
        res, _ = sc.score(recs)
        self.assertEqual(res["gfs_seamless"]["tp"], 0)
        self.assertEqual(res["gfs_seamless"]["fn"], 2)

    def test_crying_wolf_is_counted_as_false_alarms(self):
        recs = mk([(False, 0, WET)] * 6)
        res, _ = sc.score(recs)
        self.assertEqual(res["ecmwf_ifs025"]["fp"], 2)
        self.assertEqual(res["ecmwf_ifs025"]["tp"], 0)

    def test_a_dry_hour_everyone_gets_right_is_a_true_negative(self):
        recs = mk([(False, 0, DRY)] * 6)
        res, _ = sc.score(recs)
        self.assertEqual(res["ecmwf_ifs025"]["tn"], 2)

    def test_the_consensus_is_scored_alongside_the_models(self):
        recs = mk([(True, 0, WET)] * 6)
        res, _ = sc.score(recs)
        self.assertIn("consensus", res)
        self.assertEqual(res["consensus"]["tp"], 2)

    def test_the_consensus_beats_a_model_that_disagrees_with_itself(self):
        """Two models right, one wrong: the majority is right where the odd
        model is not - the reason the majority rule exists."""
        split = {"ecmwf_ifs025": 0.5, "icon_seamless": 0.5, "gfs_seamless": 0.0}
        recs = mk([(True, 0, split)] * 6)
        res, _ = sc.score(recs)
        self.assertEqual(res["consensus"]["tp"], 2)
        self.assertEqual(res["gfs_seamless"]["fn"], 2)


class TestGaugeEvents(unittest.TestCase):
    def test_a_rise_in_the_rolling_total_confirms_rain(self):
        recs = mk([(True, 5.0, WET), (True, 5.4, WET)])
        self.assertEqual(sc.gauge_events(recs), 1)

    def test_a_fall_is_old_rain_rolling_off_and_is_not_counted(self):
        """The total is a rolling 24 h sum, so it drops as old rain leaves the
        window; that says nothing about whether it just rained."""
        recs = mk([(False, 5.0, DRY), (False, 4.6, DRY)])
        self.assertEqual(sc.gauge_events(recs), 0)

    def test_a_missing_gauge_reading_is_skipped(self):
        recs = mk([(False, None, DRY), (False, 5.0, DRY)])
        self.assertEqual(sc.gauge_events(recs), 0)


class TestLoad(unittest.TestCase):
    def _write(self, lines):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return path

    def test_a_torn_last_line_is_skipped(self):
        """The log is appended to while it may be read, so a partial line is
        expected and must not crash the scorer."""
        good = json.dumps({"t": T0.isoformat(), "rain_now": False})
        path = self._write([good, '{"t": "2026-10-06T09:1'])
        try:
            self.assertEqual(len(sc.load(path)), 1)
        finally:
            os.unlink(path)

    def test_records_without_a_usable_time_are_dropped(self):
        path = self._write([json.dumps({"t": "not-a-time"}),
                            json.dumps({"rain_now": True})])
        try:
            self.assertEqual(sc.load(path), [])
        finally:
            os.unlink(path)

    def test_records_come_back_in_time_order(self):
        late = json.dumps({"t": (T0 + timedelta(hours=1)).isoformat()})
        early = json.dumps({"t": T0.isoformat()})
        path = self._write([late, early])
        try:
            out = sc.load(path)
            self.assertEqual([r["t"] for r in out], [T0.isoformat(),
                                                     (T0 + timedelta(hours=1)).isoformat()])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
