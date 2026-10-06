#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radar_notify.py - rain radar map + point nowcast for the home location.

Runs inside the Home Assistant Core container (python3 + Pillow, no numpy).

Data sources
  base map : OpenStreetMap tiles, restyled dark, cached on disk
  radar    : RainViewer (past frames + nowcast frames when published)
  point    : Open-Meteo minutely_15 precipitation at home (2 h nowcast)
  forecast : TMD (กรมฝนหลวง) 24 h narrative + 7 day rain cover + warnings
  watch    : HII 24 h flash-flood watch list

Resolution
  RainViewer serves no real tiles above zoom 7 (z8+ return a byte-identical
  placeholder), but it happily renders *large* tiles at z7: 4096_4096 gives
  ~74 m/px, i.e. sixteen times the detail of the usual 256_256 tile. The map is
  therefore rendered 1:1 at that native resolution and cropped tight around
  home instead of showing hundreds of kilometres of empty sky.

Outputs
  /config/www/radar/latest.png     image (served at /local/radar/latest.png)
  /config/www/radar/summary.json   stats read by HA command_line sensors
"""
import colorsys
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# ---------------------------------------------------------------------------
HOME_LAT = float(os.environ.get("RADAR_HOME_LAT", "13.828773"))
HOME_LON = float(os.environ.get("RADAR_HOME_LON", "100.6545224"))
OUT = os.environ.get("RADAR_OUT", "/config/www/radar/latest.png")
SUMMARY = os.environ.get("RADAR_SUMMARY", "/config/www/radar/summary.json")
CACHE = os.environ.get("RADAR_CACHE", "/config/www/radar/cache")
FONT_DIR = os.environ.get("RADAR_FONTS", "/config/scripts/fonts")

SIZE = int(os.environ.get("RADAR_SIZE", "950"))
# Ground width of the image in km. 0 = use SIZE pixels at the radar's native
# resolution (sharpest possible). A smaller value crops tighter but magnifies.
VIEW_KM = float(os.environ.get("RADAR_VIEW_KM", "0"))
ZOOM_WANT = [int(z) for z in os.environ.get("RADAR_ZOOMS", "11,10,9,8,7").split(",")]
# RainViewer tile edge lengths to try, largest (sharpest) first.
TILE_SIZES = [int(s) for s in os.environ.get("RADAR_TILE_SIZES", "4096,2048,1024,512,256").split(",")]
TILE = 256
RADIUS_KM = float(os.environ.get("RADAR_RADIUS_KM", "30"))
INNER_KM = float(os.environ.get("RADAR_INNER_KM", "15"))
NEAR_KM = float(os.environ.get("RADAR_NEAR_KM", "10"))
# Two apertures around home, because they answer different questions and the
# caption has to say which one it used:
#   RAIN_NOW_M  - echoed *over* the house; "raining at home now" is defensible
#   RAIN_NEAR_M - echoed somewhere close; "rain is near, likely soon"
# One 1.5 km aperture for both was the bug: an echo 1.4 km away was announced
# as rain at the house.
RAIN_NOW_M = float(os.environ.get("RADAR_RAIN_NOW_M", "700"))
RAIN_NEAR_M = float(os.environ.get("RADAR_RAIN_NEAR_M", "1500"))
PALETTE = os.environ.get("RADAR_PALETTE", "2")
SCHEME = os.environ.get("RADAR_SCHEME", "1_1")
TREND_FRAMES = int(os.environ.get("RADAR_TREND_FRAMES", "4"))
CACHE_TTL = 6 * 3600
UA = "ha-home-weather-radar/2.0 (personal home automation)"

BASE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
RV_INDEX = "https://api.rainviewer.com/public/weather-maps.json"
# Open-Meteo nowcast: several models and a small neighbourhood, all in one
# request (verified: 9 points x 3 models x 2 variables fits in a ~430 char URL).
#
# A single model at a single point is a bad estimator for Thai rain. Bangkok
# convection is a 5-15 km cell, while a model grid box is 13-25 km, so one grid
# point either has the cell or does not. Measured one afternoon: best_match
# said 0.0 mm while JMA said 0.2 and ICON 0.3 at the same point, and a
# neighbouring point read 0.2 where home read 0.0.
#
# JMA is deliberately absent: it is the coarsest (~55 km) and publishes no
# precipitation_probability at 15-minute resolution - its series comes back
# null - so it cannot take part in the probability test.
OM_MODELS = tuple(os.environ.get(
    "RADAR_OM_MODELS", "ecmwf_ifs025,icon_seamless,gfs_seamless").split(","))
OM_CONSENSUS = int(os.environ.get("RADAR_OM_CONSENSUS", "2"))   # majority of 3
OM_RAIN_MM15 = float(os.environ.get("RADAR_OM_RAIN_MM15", "0.1"))  # mm / 15 min
OM_PROB_PCT = float(os.environ.get("RADAR_OM_PROB_PCT", "50"))     # percent
OM_NEIGHBOUR_DEG = float(os.environ.get("RADAR_OM_NEIGHBOUR", "0.05"))  # ~5.5 km
OM_HORIZON_SLOTS = int(os.environ.get("RADAR_OM_HORIZON", "8"))    # 2 hours
# How many 15-minute slots of the past to fetch. Verified against the API:
# past_minutely_15=N prepends exactly N slots, so "now" is index N and no time
# parsing is needed. Every index the consensus reads is relative to that.
OM_PAST_SLOTS = int(os.environ.get("RADAR_OM_PAST", "4"))          # 1 hour back
OM_URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
          "&minutely_15=precipitation,precipitation_probability"
          "&forecast_minutely_15=" + str(OM_HORIZON_SLOTS)
          + "&past_minutely_15=" + str(OM_PAST_SLOTS)
          + "&models=" + ",".join(OM_MODELS)
          + "&timezone=Asia%2FBangkok")


def om_points():
    """3x3 grid of query points around home, home itself in the middle."""
    d = OM_NEIGHBOUR_DEG
    return [(HOME_LAT + dy, HOME_LON + dx)
            for dy in (-d, 0.0, d) for dx in (-d, 0.0, d)]
TMD_BASE = "https://data.tmd.go.th/api/%s/v%s/?uid=api&ukey=api12345"
HII_FF24 = ("https://api.hii.or.th/v2/4UQaYnf0Bx4fXPYyCdDRbqHyXH9Ixvd2nVUjaN1cLBY="
            "/warning/flashflood-24h")
# ThaiWater's nearest rain gauge (about 7.8 km). The YAML has a REST sensor for
# the same station; the script reads it too so the verification log is
# self-contained and can be scored offline, without the HA recorder.
THAIWATER_RAIN24 = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h"
RAIN_STATION = os.environ.get("RADAR_RAIN_STATION", "BKK021")

# Model-verification log: one JSON line per build, used to work out which
# model is actually best here rather than assuming one. Trimmed to the newest
# half once it passes the size cap (~200 days at 15-minute records).
FORECAST_LOG = os.environ.get("RADAR_LOG", "/config/www/radar/forecast_log.jsonl")
FORECAST_LOG_MAX = int(os.environ.get("RADAR_LOG_MAX_BYTES", str(4 * 1024 * 1024)))
HOME_PROVINCE = "กรุงเทพ"
HOME_DISTRICT = "บึงกุ่ม"
TZ = timezone(timedelta(hours=7), "ICT")

# RainViewer's opaque echo ramp, weakest first. Every entry is a colour that
# actually appears in the tiles, in the order intensity rises:
#
#   pale cyan -> deep blue -> yellow -> orange -> red
#
# RainViewer publishes no dBZ table, so an intensity class is a colour's RANK
# on this ramp, not a reflectivity reading. The rank was established from the
# tiles rather than guessed: the palest cyan has 59% of its pixels touching
# the coverage edge while every other blue has under 5%, which puts it at the
# weak fringe; the blues adjoin yellow, yellow adjoins orange, and within the
# red band the darker the colour the stronger the echo.
#
# Kept as a table rather than hue/saturation arithmetic because the two were
# previously conflated: saturation is HIGH at the strong (deep blue) end and
# LOW at the weak (pale cyan) end, so any saturation floor deletes the
# lightest rain. See classify() for how the coverage mask is excluded.
_RAMP = (
    (136, 221, 238), (108, 209, 235), (81, 197, 232), (54, 186, 229),
    (27, 174, 226), (0, 163, 224), (0, 154, 213), (0, 145, 202),
    (0, 136, 191), (0, 127, 180), (0, 119, 170), (0, 112, 163),
    (0, 105, 156), (0, 98, 149), (0, 91, 142), (0, 85, 136),
    (0, 81, 128), (0, 78, 120), (0, 74, 112), (0, 71, 104),
    (255, 238, 0), (255, 224, 0), (255, 210, 0), (255, 197, 0),
    (255, 183, 0), (255, 170, 0), (255, 159, 0), (255, 149, 0),
    (255, 139, 0), (255, 129, 0),
    (255, 68, 0), (242, 54, 0), (230, 40, 0), (217, 27, 0),
    (205, 13, 0), (193, 0, 0), (168, 0, 0), (143, 0, 0),
    (118, 0, 0), (93, 0, 0),
)
# 40 stations cut into 5 equal classes.
_RAMP_CLASS = {c: i * 5 // len(_RAMP) for i, c in enumerate(_RAMP)}

# Legend swatches are taken from the ramp itself so a swatch can never claim a
# colour the classifier rejects.
LEGEND = [
    (0, "เบา", _RAMP[2]),
    (1, "ปานกลาง", _RAMP[9]),
    (2, "หนัก", _RAMP[22]),
    (3, "หนักมาก", _RAMP[28]),
    (4, "รุนแรง", _RAMP[38]),
]
LEVEL_TH = ["ไม่มีฝน", "ฝนเบา", "ฝนปานกลาง", "ฝนหนัก", "ฝนหนักมาก", "ฝนรุนแรง"]
LEVEL_EN = ["no rain", "light", "moderate", "heavy", "very heavy", "extreme"]


def log(*a):
    print(*a, flush=True)


_MEMO = {}


# HA terminates a shell_command after 60 s and there is no setting to change it
# (docs, 2026.9: "There is no option to alter this behavior"), so the run has to
# police itself. A network that times out rather than refusing is what blows the
# budget: the tile probe alone could spend 5 sizes x 3 attempts x 120 s, and the
# process would be killed before summary.json was ever written - leaving the
# dashboard quietly serving stale data with nothing to indicate it.
# OPEN_BUDGET leaves room for the render and the write.
OPEN_BUDGET = float(os.environ.get("RADAR_BUDGET_S", "50"))

# Sampling ceiling for class_histogram when no explicit grid is given. The
# mosaic is SIZE x SIZE, so this normally means "analyse at native resolution".
# Above 1200 it stops being worth the time; a 1200 px pass is ~1.5 s.
ANALYSIS_MAX = int(os.environ.get("RADAR_ANALYSIS_PX", "1200"))

_DEADLINE = None


def set_deadline(budget=None):
    """Start the run clock. build() calls this; tests may not."""
    global _DEADLINE
    _DEADLINE = time.monotonic() + (OPEN_BUDGET if budget is None else budget)
    return _DEADLINE


def deadline_remaining():
    """Seconds left before HA kills the process, or None when untimed."""
    return None if _DEADLINE is None else _DEADLINE - time.monotonic()


def budget_left(need=0.0):
    """True when at least `need` more seconds remain, or when untimed."""
    left = deadline_remaining()
    return left is None or left > need


def fetch(url, timeout=45):
    if url in _MEMO:
        return _MEMO[url]
    # never let one request outlive the run budget: a slow source must degrade
    # through its own fallback, not take the whole run down with it
    left = deadline_remaining()
    if left is not None:
        if left <= 1:
            raise TimeoutError("run budget exhausted")
        timeout = min(timeout, left)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    # only big tiles are worth holding: a 4096 px radar tile is 1.4 MB
    if len(data) > 200000:
        _MEMO[url] = data
    return data


def fetch_cached(url, z, x, y, ttl=CACHE_TTL):
    """OSM tiles barely change - cache them to stay fast and polite."""
    os.makedirs(CACHE, exist_ok=True)
    key = os.path.join(CACHE, "osm_%d_%d_%d.png" % (z, x, y))
    if os.path.exists(key) and time.time() - os.path.getmtime(key) < ttl:
        with open(key, "rb") as fh:
            return fh.read()
    data = fetch(url)
    tmp = key + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, key)
    return data


def latlon_to_world_px(lat, lon, z, tpx=TILE):
    """Home position inside the tile grid, in pixels of a tpx x tpx tile."""
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n * tpx
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n * tpx
    return x, y


def meters_per_pixel(lat, z, tpx=TILE):
    """Ground resolution of one pixel, for tiles of tpx x tpx pixels."""
    return 156543.03392 * math.cos(math.radians(lat)) / (2.0 ** z) * (TILE / float(tpx))


def zoom_for_mpp(lat, mpp):
    """Deepest slippy zoom whose 256 px tiles are at least as detailed as mpp."""
    z = math.log2(156543.03392 * math.cos(math.radians(lat)) / max(mpp, 0.1))
    return max(0, min(18, int(round(z))))


def darken(tile):
    g = ImageOps.invert(tile.convert("L"))
    base = ImageOps.colorize(g, black=(16, 20, 27), white=(222, 230, 240)).convert("RGBA")
    # OSM raster tiles are drawn for 256 px; crisp them up a little.
    return base.filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=3))


def mosaic(url_template, z, cx, cy, size, tpx=TILE, style=None, cached=False, workers=8):
    """size x size RGBA image centred on tile-grid position (cx,cy)."""
    x0 = cx - size / 2.0
    y0 = cy - size / 2.0
    tx0 = int(math.floor(x0 / tpx))
    ty0 = int(math.floor(y0 / tpx))
    tx1 = int(math.floor((x0 + size - 1) / tpx))
    ty1 = int(math.floor((y0 + size - 1) / tpx))
    coords = [(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)]

    def grab(txy):
        tx, ty = txy
        try:
            url = url_template.format(z=z, x=tx, y=ty)
            raw = fetch_cached(url, z, tx, ty) if cached else fetch(url)
            t = Image.open(io.BytesIO(raw))
            if t.size != (tpx, tpx):
                t = t.resize((tpx, tpx), Image.LANCZOS)
            t = t.convert("RGBA")
            if style:
                t = style(t)
            return tx, ty, t
        except Exception as e:
            log("  tile %s,%s failed: %s" % (tx, ty, e))
            return tx, ty, None

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for tx, ty, t in ex.map(grab, coords):
            if t is None:
                continue
            img.paste(t, (int(round(tx * tpx - x0)), int(round(ty * tpx - y0))), t)
            ok += 1
    return img, ok


# --- rain intensity ---------------------------------------------------------
def classify(r, g, b):
    """RainViewer radar pixel -> intensity class 0..4, or -1 when not rain.

    The class is the colour's rank on _RAMP, so it keeps working if RainViewer
    shifts the palette slightly: an off-ramp colour is placed at the nearest
    station instead of being thrown away.

    The coverage mask is the translucent warm-grey ramp (hue 42-48, s <= 0.34)
    and is rejected on hue and saturation before the ramp is consulted. A
    saturation floor on its own cannot do this job, because the pale end of
    the echo ramp sits at s = 0.43.
    """
    hit = _RAMP_CLASS.get((r, g, b))
    if hit is not None:
        return hit
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    hd = h * 360.0
    if v < 0.30:
        return -1
    if not (170.0 <= hd <= 230.0) and s < 0.60:
        return -1                     # warm and washed out -> coverage mask
    best, best_d = 0, None
    for i, (rr, gg, bb) in enumerate(_RAMP):
        d = (rr - r) ** 2 + (gg - g) ** 2 + (bb - b) ** 2
        if best_d is None or d < best_d:
            best, best_d = i, d
    return _RAMP_CLASS[_RAMP[best]]


def choose_grid(requested, width, ceiling=None):
    """Sampling density for class_histogram.

    An explicit `requested` wins - the trend loop caps it for speed. Otherwise
    analyse at the image's own resolution, capped by `ceiling` for cost.

    Undersampling is not a harmless approximation here: at 300 samples across a
    950 px mosaic, two source columns in three are never read, so a 1 px echo
    landing on an unread column is not measured at all - not even as zero.
    """
    if requested:
        return min(requested, width)
    return min(width, ceiling or ANALYSIS_MAX)


def class_histogram(img, mpp, bands, grid=None):
    """Bucket echoes into intensity classes per radius band.

    bands = [(r_inner_km, r_outer_km, key), ...]. The analysis runs at the
    image's own resolution unless `grid` caps it.
    """
    w = img.size[0]
    grid = choose_grid(grid, img.size[0])
    small = img.convert("RGB").resize((grid, grid), Image.NEAREST)
    sx = float(grid) / w
    buf = small.tobytes()
    c = grid / 2.0
    keys = [k for _, _, k in bands]
    ri2 = [((r0 * 1000.0 / mpp) * sx) ** 2 for r0, _, _ in bands]
    ro2 = [((r1 * 1000.0 / mpp) * sx) ** 2 for _, r1, _ in bands]
    hits = dict.fromkeys(keys, 0)
    tot = dict.fromkeys(keys, 0)
    worst = dict.fromkeys(keys, 0)
    nb = len(bands)
    for y in range(grid):
        dy2 = (y - c) ** 2
        row = y * grid * 3
        for x in range(grid):
            d2 = (x - c) ** 2 + dy2
            inside = False
            for i in range(nb):
                if ri2[i] <= d2 <= ro2[i]:
                    inside = True
                    tot[keys[i]] += 1
            if not inside:
                continue
            o = row + x * 3
            cl = classify(buf[o], buf[o + 1], buf[o + 2])
            if cl < 0:
                continue
            for i in range(nb):
                if ri2[i] <= d2 <= ro2[i]:
                    hits[keys[i]] += 1
                    if cl + 1 > worst[keys[i]]:
                        worst[keys[i]] = cl + 1
    return {k: {"pct": round(100.0 * hits[k] / max(tot[k], 1), 2), "class": worst[k]}
            for k in keys}


def home_class(tile_url, z, cx, cy, mpp, tpx=TILE, radius_m=None):
    """Strongest echo class within `radius_m` of home, -1 when there is none.

    The radius is explicit because it decides what the answer *means*: an echo
    anywhere inside a 1.5 km circle is not rain over the house, and calling it
    "raining at home now" was wrong often enough to be noticed. build() asks
    twice - tight for overhead, wider for nearby.
    """
    radius_m = RAIN_NEAR_M if radius_m is None else radius_m
    r = max(0, min(1200, int(round(radius_m / mpp))))
    tx = int(cx // tpx)
    ty = int(cy // tpx)
    qx = int(cx - tx * tpx)
    qy = int(cy - ty * tpx)
    try:
        t = Image.open(io.BytesIO(fetch(tile_url.format(z=z, x=tx, y=ty)))).convert("RGB")
    except Exception as e:
        log("  home tile failed: %s" % e)
        return -1
    best = -1
    px = t.load()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > r * r + 1:
                continue
            x = min(max(qx + dx, 0), t.width - 1)
            y = min(max(qy + dy, 0), t.height - 1)
            cl = classify(*px[x, y])
            if cl > best:
                best = cl
    return best


def zoom_has_data(tile_url, z, tx, ty):
    """True when this zoom serves real radar (placeholders repeat byte for byte)."""
    try:
        a = fetch(tile_url.format(z=z, x=tx, y=ty))
        b = fetch(tile_url.format(z=z, x=tx + 7, y=ty - 7))
    except Exception as e:
        log("  zoom %d probe failed: %s" % (z, e))
        return False
    if a != b:
        return True
    try:
        im = Image.open(io.BytesIO(a)).convert("RGBA")
        if im.split()[3].getextrema()[1] > 20:
            log("  zoom %d: placeholder tile rejected" % z)
            return False
        return True          # byte-identical but empty -> genuinely rainless
    except Exception:
        return False


def pick_zoom(tile_url, lat, lon):
    for z in ZOOM_WANT:
        cx, cy = latlon_to_world_px(lat, lon, z)
        if zoom_has_data(tile_url, z, int(cx // TILE), int(cy // TILE)):
            log("radar zoom %d selected" % z)
            return z
    log("no radar zoom available")
    return None


def pick_tile_size(make_url, z, tx, ty):
    """Largest tile edge RainViewer really serves at this zoom.

    Asking for 4096 and silently receiving a 256 px placeholder would undo the
    whole point, so the decoded size is verified.

    Bounded by the run budget: sizes x attempts x timeout here is the single
    biggest way for a sick network to outlast HA's 60 s kill.
    """
    for s in TILE_SIZES:
        for attempt in range(3):
            if not budget_left(1.0):
                log("tile probe out of budget; falling back to %d px" % TILE)
                return TILE
            try:
                data = fetch(make_url(s, z, tx, ty), 120)
                im = Image.open(io.BytesIO(data))
                if im.size != (s, s):
                    log("  tile size %d not served (got %sx%s)" % (s, im.size[0], im.size[1]))
                    break
                log("radar tile size %d px" % s)
                return s
            except Exception as e:
                # a frame that was just generated can answer 410 for a while
                log("  tile size %d attempt %d failed: %s" % (s, attempt + 1, e))
                if not budget_left(4.0):
                    log("tile probe out of budget; falling back to %d px" % TILE)
                    return TILE
                time.sleep(3)
    return TILE


def _om_val(row, key, i):
    """Value at slot i, or 0.0 when the series is absent or null there.

    Open-Meteo returns null for a model that has no data at 15-minute
    resolution, so a missing series must read as "no rain", not crash.
    """
    series = (row.get("minutely_15") or {}).get(key)
    if not series or i >= len(series):
        return 0.0
    v = series[i]
    return 0.0 if v is None else float(v)


def om_consensus(rows, models=None, need=None, now_index=None, past=None):
    """Collapse the Open-Meteo response into one nowcast decision.

    `rows` is the list of per-point responses; home is the middle one.

    **Indexing.** The request asks for OM_PAST_SLOTS slots of the past, which
    Open-Meteo prepends, so index `now_index` is the current 15 minutes and
    everything before it is history. Votes, the horizon and the ETA are all
    measured from there; only the past-hour total looks behind it.

    For each slot a model counts as predicting rain when *any* point in the
    neighbourhood shows at least OM_RAIN_MM15 of precipitation, or at least
    OM_PROB_PCT chance of it. Rain is then expected for the slot when at least
    `need` models agree - the majority rule, so one outlier model cannot raise
    an alert on its own.

    The amounts are separate questions and are answered at home, not across the
    neighbourhood: `mm60` is the multi-model mean over the first hour ahead and
    `mm_past60` the mean over the hour behind, because "how much at the house"
    is a point question and the mean is the standard ensemble estimate.

    Returns a dict; ok is False when the response holds nothing usable.
    """
    models = list(models or OM_MODELS)
    need = OM_CONSENSUS if need is None else need
    now_index = OM_PAST_SLOTS if now_index is None else now_index
    past = OM_PAST_SLOTS if past is None else past
    empty = {"ok": False, "rain_soon": False, "minutes": None, "mm60": -1,
             "mm_past60": -1, "prob": None, "votes": [],
             "models": len(models), "need": need, "per_model": {}}
    if not rows:
        return empty
    series = (rows[0].get("minutely_15") or {})
    n = len(series.get("time") or [])
    if not n:
        return empty

    home = rows[len(rows) // 2]
    ahead = range(now_index, n)
    votes = []
    for i in ahead:
        c = 0
        for m in models:
            amt = "precipitation_%s" % m
            prb = "precipitation_probability_%s" % m
            if any(_om_val(r, amt, i) >= OM_RAIN_MM15
                   or _om_val(r, prb, i) >= OM_PROB_PCT for r in rows):
                c += 1
        votes.append(c)

    first = next((j for j, v in enumerate(votes) if v >= need), None)
    ahead4 = list(range(now_index, min(now_index + 4, n)))
    behind4 = list(range(max(0, now_index - past), now_index))
    mm60 = round(sum(_om_val(home, "precipitation_%s" % m, i)
                     for m in models for i in ahead4)
                 / max(1, len(models)), 2)
    mm_past = round(sum(_om_val(home, "precipitation_%s" % m, i)
                        for m in models for i in behind4)
                    / max(1, len(models)), 2)
    prob = max((_om_val(home, "precipitation_probability_%s" % m, i)
                for m in models for i in ahead), default=0.0)
    # per model, so the verification log can score each one separately rather
    # than only the consensus they add up to
    per_model = {}
    for m in models:
        per_model[m] = {
            "mm60": round(sum(_om_val(home, "precipitation_%s" % m, i)
                              for i in ahead4), 2),
            "prob": int(max((_om_val(
                home, "precipitation_probability_%s" % m, i)
                for i in ahead), default=0.0)),
        }
    return {"ok": True,
            "rain_soon": first is not None,
            "minutes": None if first is None else first * 15,
            "mm60": mm60,
            "mm_past60": mm_past,
            "prob": int(prob),
            "votes": votes,
            "models": len(models),
            "need": need,
            "per_model": per_model}


def openmeteo_nowcast():
    """Open-Meteo nowcast: a neighbourhood consensus of several models.

    Returns the dict from om_consensus(). On failure mm60 becomes the -1
    sentinel rather than null, because command_line renders a null as the
    literal string "None" on sensor.rain_next_60min_mm; minutes stays None
    because a radar estimate also competes for the earliest ETA and -1 would
    win that comparison.
    """
    pts = om_points()
    url = OM_URL.format(lat=",".join("%.6f" % p[0] for p in pts),
                        lon=",".join("%.6f" % p[1] for p in pts))
    try:
        d = json.loads(fetch(url))
        rows = d if isinstance(d, list) else [d]
        out = om_consensus(rows)
        if not out["ok"]:
            log("open-meteo: response held no usable series")
        return out
    except Exception as e:
        log("open-meteo failed: %s" % e)
        return {"ok": False, "rain_soon": False, "minutes": None, "mm60": -1,
                "mm_past60": -1, "prob": None, "votes": [],
                "models": len(OM_MODELS), "need": OM_CONSENSUS,
                "per_model": {}}


def thaiwater_rain24():
    """ThaiWater 24 h rainfall for the nearest station (BKK021).

    A real gauge about 7.8 km from home, and the only ground truth available.
    It is a rolling 24 h total, so only a *positive* change between two reads
    means rain actually fell; a zero or negative change is ambiguous, which is
    why the verification log also records the radar flag.
    """
    try:
        d = json.loads(fetch(THAIWATER_RAIN24))
        for r in d.get("data") or []:
            st = r.get("station") or {}
            if st.get("tele_station_oldcode") == RAIN_STATION:
                v = r.get("rain_24h")
                return None if v is None else float(v)
        log("thaiwater: station %s not in the response" % RAIN_STATION)
    except Exception as e:
        log("thaiwater failed: %s" % e)
    return None


def forecast_record(summary):
    """One line of the model-verification log.

    Kept small on purpose: the timestamp, the independent observations (radar
    at home, the gauge total), and each model's own next-hour numbers. The
    gauge is a rolling 24 h total so only its positive changes are usable; the
    radar flag is what makes a dry hour count as dry at all.

    The point is to answer, after a few weeks, which model is actually best at
    this location - instead of trusting a vendor's reputation for it.
    """
    return {
        "t": summary.get("updated"),
        "rain_now": bool(summary.get("rain_now")),
        "cover30": summary.get("coverage_30km"),
        "gauge24": summary.get("rain24_gauge"),
        "models": summary.get("om_models"),
        "need": summary.get("om_need"),
        "m": {k: {"mm60": v.get("mm60"), "prob": v.get("prob")}
              for k, v in (summary.get("om_per_model") or {}).items()},
    }


def append_forecast_log(record, path=None, max_bytes=None):
    """Append one JSON line, trimming the file when it grows too large.

    The append itself is atomic enough (a single small write, and the only
    reader tolerates a partial last line); the trim is not, but losing one
    15-minute sample to a crash is not worth a second temp file and rename.
    """
    path = path or FORECAST_LOG
    max_bytes = FORECAST_LOG_MAX if max_bytes is None else max_bytes
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        if max_bytes and os.path.getsize(path) > max_bytes:
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            keep = lines[len(lines) // 2:]           # drop the oldest half
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(keep)
            log("forecast log trimmed to %d lines" % len(keep))
    except Exception as e:
        log("forecast log write failed: %s" % e)


def hii_flashflood_watch():
    """True when HII lists a flash-flood watch covering home.

    Tri-state: None means the feed could not be read. For a warning product
    "we could not check" must never be rounded down to "all clear", so build()
    holds the last known value instead of publishing False.
    """
    try:
        d = json.loads(fetch(HII_FF24))
        for a in d.get("area") or []:
            prov = str(a.get("province") or "")
            district = str(a.get("amphoe") or "") + str(a.get("tambon") or "")
            if HOME_PROVINCE in prov and (HOME_DISTRICT in district or not district.strip()):
                return True
        return False
    except Exception as e:
        log("hii flashflood failed: %s" % e)
        return None


def tmd_forecast():
    """กรมฝนหลวง: 24 h narrative for Bangkok + 7 day rain cover + warnings.

    Returns (values, failed). "failed" is required because None means two
    different things in this dict: the upstream fetch broke, or the answer
    genuinely is "no warning". Only the first may fall back to the previous
    run's value - carrying a stale warning forward would keep announcing a
    storm that has already passed.
    """
    out = {"tmd_24h": None, "tmd_24h_rain_pct": None, "tmd_7d_rain_pct": None,
           "tmd_7d_desc": None, "tmd_warning": None}
    failed = set()
    try:
        root = ET.fromstring(fetch(TMD_BASE % ("DailyForecast", 2)))
        found = False
        for rf in root.find("DailyForecast").findall("RegionForecast"):
            if HOME_PROVINCE in (rf.findtext("RegionNameThai") or ""):
                desc = (rf.findtext("DescriptionThai") or "").strip()
                out["tmd_24h"] = " ".join(desc.split())
                m = re.search(r"ร้อยละ\s*(\d+)", desc)
                if m:
                    out["tmd_24h_rain_pct"] = int(m.group(1))
                found = True
                break
        if not found:
            failed.update(("tmd_24h", "tmd_24h_rain_pct"))
    except Exception as e:
        log("tmd daily failed: %s" % e)
        failed.update(("tmd_24h", "tmd_24h_rain_pct"))
    try:
        root = ET.fromstring(fetch(TMD_BASE % ("WeatherForecast7Days", 2)))
        found = False
        for p in root.find("Provinces"):
            if HOME_PROVINCE in (p.findtext("ProvinceNameThai") or ""):
                f = p.find("SevenDaysForecast")
                if f is not None:
                    out["tmd_7d_rain_pct"] = int(float(f.findtext("PercentRainCover") or 0))
                    out["tmd_7d_desc"] = (f.findtext("DescriptionThai") or "").strip()
                found = True
                break
        if not found:
            failed.update(("tmd_7d_rain_pct", "tmd_7d_desc"))
    except Exception as e:
        log("tmd 7day failed: %s" % e)
        failed.update(("tmd_7d_rain_pct", "tmd_7d_desc"))
    try:
        root = ET.fromstring(fetch(TMD_BASE % ("WeatherWarningNews", 2)))
        for w in root.iter("Warning"):
            # only warn when the headline itself names Bangkok: many national
            # bulletins list every province in the body text
            title = (w.findtext("TitleThai") or "").strip()
            if HOME_PROVINCE in title:
                out["tmd_warning"] = title
                break
    except Exception as e:
        log("tmd warning failed: %s" % e)
        failed.add("tmd_warning")
    return out, failed


def load_font(name, size):
    for f in ("Sarabun-" + name + ".ttf", "NotoSansThai-Regular.ttf"):
        path = os.path.join(FONT_DIR, f)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def write_atomic(path, data, mode=0o644):
    """Write via a temp file + rename so a reader never sees a partial file.

    mkstemp creates the file 0600. Both callers write into /config/www, which
    is served publicly and was previously world-readable, so the mode is set
    explicitly rather than inherited from the temp file - otherwise the output
    is only readable because HA Core happens to run as root.
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_previous_summary():
    """Last run's summary, so a failed upstream fetch does not blank a sensor.

    TMD and the HII endpoint reset connections intermittently. Writing their
    failure straight out as JSON null turns the next HA render into the literal
    string "None", because command_line renders a missing key that way. Holding
    the previous value costs one stale cycle instead of a broken sensor.
    """
    try:
        with open(SUMMARY, "rb") as fh:
            prev = json.loads(fh.read().decode("utf-8"))
        return prev if isinstance(prev, dict) else {}
    except Exception:
        return {}


def carry_forward(fresh, failed, prev):
    """Keep the last good value for any key whose fetch failed this run.

    Only keys named in `failed` fall back. A key that legitimately came back
    empty - "no warning" is None - must keep its None, otherwise a cleared
    warning would be announced forever. A key that has never had a value stays
    None too, so a broken source on a fresh install is visible as missing
    rather than invented.
    """
    out = {}
    for k, v in fresh.items():
        if v is None and k in failed and prev.get(k) is not None:
            out[k] = prev[k]
        else:
            out[k] = v
    return out


def carry_flag(fresh, prev):
    """Hold the last known value for a tri-state warning flag.

    `fresh` is True, False, or None for "could not check". A warning product
    must never round unknown down to all-clear, so None holds `prev` instead of
    publishing False. A genuine False still wins: a cleared watch is news.
    """
    return fresh if fresh is not None else bool(prev)


def build():
    t0 = time.time()
    # HA kills the process at 60 s; start the clock so a slow source degrades
    # instead of costing the whole cycle
    set_deadline()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    index = {"host": "https://tilecache.rainviewer.com", "past": [], "nowcast": []}
    try:
        d = json.loads(fetch(RV_INDEX))
        index["host"] = d.get("host") or index["host"]
        index["past"] = d.get("radar", {}).get("past") or []
        index["nowcast"] = d.get("radar", {}).get("nowcast") or []
    except Exception as e:
        log("rainviewer index failed: %s" % e)

    past, nowcast = index["past"], index["nowcast"]
    host, path = index["host"], (past[-1]["path"] if past else "")

    def make_url(s, z="{z}", x="{x}", y="{y}"):
        # RainViewer spells the tile size "<width>_<height>", e.g. 4096_4096
        return "%s%s/%d_%d/%s/%s/%s/%s/%s.png" % (host, path, s, s, z, x, y,
                                                  PALETTE, SCHEME)

    frame_time = past[-1].get("time") if past else None
    zoom = pick_zoom(make_url(TILE, "{z}", "{x}", "{y}"), HOME_LAT, HOME_LON) if past else None

    tile_px = TILE
    if zoom:
        cx0, cy0 = latlon_to_world_px(HOME_LAT, HOME_LON, zoom)
        tile_px = pick_tile_size(make_url, zoom, int(cx0 // TILE), int(cy0 // TILE))

    mpp = meters_per_pixel(HOME_LAT, zoom or 7, tile_px)
    size = int(round(VIEW_KM * 1000.0 / mpp)) if VIEW_KM else SIZE
    size = max(360, min(2048, size))
    view_km = size * mpp / 1000.0
    base_zoom = zoom_for_mpp(HOME_LAT, mpp)
    log("render %dx%d px  %.0f m/px  view %.1f km  base z%d  radar z%d/%dpx"
        % (size, size, mpp, view_km, base_zoom, zoom or 0, tile_px))

    bcx, bcy = latlon_to_world_px(HOME_LAT, HOME_LON, base_zoom)
    try:
        base, n_base = mosaic(BASE_URL, base_zoom, bcx, bcy, size, TILE,
                              style=darken, cached=True)
        log("base: %d tiles @z%d" % (n_base, base_zoom))
    except Exception as e:
        log("base failed: %s" % e)
        base = Image.new("RGBA", (size, size), (16, 20, 27, 255))
    canvas = base.convert("RGBA")

    cover30 = inner15 = 0.0
    cls30 = 0
    trend = None
    rain_now = False
    rain_near = False
    now_cls = -1
    if past and zoom:
        tmpl = make_url(tile_px, "{z}", "{x}", "{y}")
        rcx, rcy = latlon_to_world_px(HOME_LAT, HOME_LON, zoom, tile_px)
        workers = 3 if tile_px >= 2048 else 8
        radar, n_rv = mosaic(tmpl, zoom, rcx, rcy, size, tile_px, workers=workers)
        log("radar: %d tiles @z%d/%dpx" % (n_rv, zoom, tile_px))
        canvas = Image.alpha_composite(canvas, radar)
        st = class_histogram(radar, mpp,
                             [(0.0, RADIUS_KM, "cover30"),
                              (0.0, INNER_KM, "inner15")])
        cover30, cls30 = st["cover30"]["pct"], st["cover30"]["class"]
        inner15 = st["inner15"]["pct"]
        now_cls = home_class(tmpl, zoom, rcx, rcy, mpp, tile_px, RAIN_NOW_M)
        rain_now = now_cls >= 0
        rain_near = home_class(tmpl, zoom, rcx, rcy, mpp, tile_px,
                               RAIN_NEAR_M) >= 0
        log("coverage30=%.2f%% (class %d) inner15=%.2f%% now=%s(%d) near=%s"
            % (cover30, cls30, inner15, rain_now, now_cls, rain_near))

        # trend: inner-15 km coverage across older frames, measured on small
        # tiles so it costs a couple of fetches per frame instead of a full one
        trend = None
        tsize = 384
        ts_px = 1024 if tile_px >= 1024 else tile_px
        tcx, tcy = latlon_to_world_px(HOME_LAT, HOME_LON, zoom, ts_px)
        tmpp = meters_per_pixel(HOME_LAT, zoom, ts_px)
        olds, inner_now = [], None
        for f in past[-(TREND_FRAMES + 1):]:
            t2 = ("%s%s/%d_%d/{z}/{x}/{y}/%s/%s.png"
                  % (host, f["path"], ts_px, ts_px, PALETTE, SCHEME))
            try:
                im, _ = mosaic(t2, zoom, tcx, tcy, tsize, ts_px)
                v = class_histogram(im, tmpp, [(0.0, INNER_KM, "i")],
                                    grid=256)["i"]["pct"]
                if f is past[-1]:
                    inner_now = v
                else:
                    olds.append(v)
            except Exception:
                pass
        if olds and inner_now is not None:
            trend = round(inner_now - min(olds), 2)
            log("trend inner15: past=%s now=%.2f delta=%s" % (olds, inner_now, trend))

    om = openmeteo_nowcast()
    rain_soon = bool(om["rain_soon"])
    soon_min = om["minutes"]
    om_mm60 = om["mm60"]
    om_past60 = om["mm_past60"]
    om_ok = bool(om["ok"])
    # -1 instead of null: HA's command_line platform turns a rendered "None"
    # into `unavailable`, so the sentinel keeps the sensor numeric.
    if soon_min is None:
        soon_min = -1
    if om_mm60 is None:
        om_mm60 = -1
    if om_past60 is None:
        om_past60 = -1
    log("open-meteo: %d/%d models, votes=%s, prob=%s%%, mm60=%s, past60=%s, eta=%s"
        % (max(om["votes"], default=0), om["models"], om["votes"], om["prob"],
           om_mm60, om_past60, soon_min))
    # The alert fires on either signal - the model consensus OR the radar
    # trend - deliberately: a miss by one source must not mean no alert. An
    # echo within RAIN_NEAR_M also counts, but only while rain_now is false, so
    # "near" and "overhead" stay distinct states rather than both firing.
    trend_up = bool(trend is not None and trend >= 3.0 and inner15 >= 1.0)
    approaching = bool((not rain_now)
                       and (rain_soon or trend_up or rain_near))
    prev = read_previous_summary()
    # A flash-flood feed failure is "unknown", never "no watch". Hold the last
    # known value rather than publishing False, which reads as all-clear.
    ff_fresh = hii_flashflood_watch()
    ff_watch = carry_flag(ff_fresh, prev.get("flash_flood_watch"))
    if ff_fresh is None:
        log("hii flashflood unknown; holding flash_flood_watch=%s" % ff_watch)
    tmd_values, tmd_failed = tmd_forecast()
    tmd = carry_forward(tmd_values, tmd_failed, prev)
    if tmd_failed:
        log("tmd keys held from previous run: %s" % sorted(tmd_failed))
    # a real gauge, for the model-verification log; None when unreachable
    gauge24 = thaiwater_rain24()

    # ---------------- annotations ----------------
    d = ImageDraw.Draw(canvas, "RGBA")
    c = size / 2.0
    f_small, f_med, f_big = load_font("Regular", 19), load_font("SemiBold", 23), load_font("Bold", 27)

    for km, colour, width, dash in ((RADIUS_KM, (255, 255, 255, 175), 2, False),
                                    (NEAR_KM, (255, 255, 255, 85), 1, True)):
        rp = (km * 1000.0) / mpp
        if 8 < rp < size:
            d.ellipse([c - rp, c - rp, c + rp, c + rp], outline=colour, width=width)
            if dash:
                step = max(14, int(rp * 0.12))
                ang = 0.0
                while ang < 360.0:
                    a0 = math.radians(ang)
                    a1 = math.radians(min(360.0, ang + 5.0))
                    d.line([c + rp * math.cos(a0), c + rp * math.sin(a0),
                            c + rp * math.cos(a1), c + rp * math.sin(a1)],
                           fill=colour, width=width)
                    ang += step
            d.text((c + 7, c - rp + 4), "%g km" % km, font=f_small,
                   fill=(255, 255, 255, 195))

    r = 9
    d.ellipse([c - r, c - r, c + r, c + r], fill=(255, 64, 64, 255),
              outline=(255, 255, 255, 255), width=3)
    d.text((c + 15, c + 4), "บ้าน", font=f_med, fill=(255, 255, 255, 240))

    d.polygon([(34, 66), (22, 96), (34, 88), (46, 96)], fill=(255, 255, 255, 225))
    d.text((28, 99), "N", font=f_med, fill=(255, 255, 255, 235))

    # intensity legend, lifted clear of the scale bar below it
    lw, lh, lx, ly = 150, 26, 22, size - 196
    d.rounded_rectangle([lx - 8, ly - 22, lx + lw, ly + lh * len(LEGEND) + 6],
                        radius=8, fill=(0, 0, 0, 150))
    d.text((lx, ly - 19), "ระดับฝน", font=f_med, fill=(255, 255, 255, 235))
    for i, (_, label, colour) in enumerate(LEGEND):
        yy = ly + i * lh
        d.rectangle([lx, yy, lx + 30, yy + 18], fill=colour + (255,))
        d.text((lx + 38, yy - 2), label, font=f_small, fill=(255, 255, 255, 240))

    # scale bar: pick a round distance that is at least 70 px wide
    km_options = [1, 2, 5, 10, 20, 50, 100]
    scale_km = next((k for k in km_options if (k * 1000.0) / mpp >= 70), 100)
    bar = (scale_km * 1000.0) / mpp
    xb, yb = 30, size - 42
    d.line([xb, yb, xb + bar, yb], fill=(255, 255, 255, 235), width=4)
    for xx in (xb, xb + bar):
        d.line([xx, yb - 8, xx, yb + 8], fill=(255, 255, 255, 235), width=3)
    d.text((xb, yb + 10), "%g km" % scale_km, font=f_small, fill=(255, 255, 255, 235))

    ts = datetime.fromtimestamp(frame_time, TZ).strftime("%d/%m %H:%M") if frame_time else "-"
    d.rectangle([0, 0, size, 38], fill=(0, 0, 0, 140))
    d.text((12, 6), "เรดาร์ฝน - %s" % ts, font=f_big, fill=(255, 255, 255, 245))
    status = LEVEL_TH[cls30] if cover30 >= 0.5 else "ไม่มีฝนใน %g กม." % RADIUS_KM
    d.text((size - 300, 8), status, font=f_med, fill=(255, 210, 120, 245))

    note = ("แผนที่ OSM z%d | เรดาร์ RainViewer z%d (%d ม./พิกเซล) | "
            "nowcast Open-Meteo | พยากรณ์ TMD" % (base_zoom, zoom or 0, int(mpp)))
    d.text((size - 490, size - 26), note, font=f_small, fill=(255, 255, 255, 150))

    # atomic, like summary.json: a Telegram send that races this build must
    # never attach a half-written PNG.
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "PNG", optimize=True)
    write_atomic(OUT, buf.getvalue())

    summary = {
        "updated": datetime.now(TZ).isoformat(),
        "frame_time": frame_time,
        "radar_zoom": zoom,
        "radar_tile_px": tile_px,
        "radar_mpp": round(mpp, 1),
        "view_km": round(view_km, 1),
        "base_zoom": base_zoom,
        "radar_ok": bool(zoom),
        "rain_now": rain_now,
        "rain_near": rain_near,
        "rain_now_class": now_cls,
        # the intensity *over the house*, not the 30 km maximum: cls30 can be
        # "รุนแรง" from a storm 25 km away while nothing is falling here
        "rain_now_label": LEVEL_TH[now_cls + 1] if now_cls >= 0 else LEVEL_TH[0],
        "rain_soon": rain_soon,
        "rain_soon_in_min": soon_min,
        "rain_next_60min_mm": om_mm60,
        "rain_past_60min_mm": om_past60,
        "approaching": approaching,
        "coverage_30km": cover30,
        "coverage_inner15km": inner15,
        "trend_inner15km": trend,
        "rain_class_30km": cls30,
        "rain_class_label": LEVEL_TH[cls30],
        "rain_class_label_en": LEVEL_EN[cls30],
        "rainviewer_nowcast": bool(nowcast),
        "openmeteo_ok": om_ok,
        "om_models": om["models"],
        "om_need": om["need"],
        "om_votes": om["votes"],
        "om_prob": om["prob"],
        "om_per_model": om["per_model"],
        "rain24_gauge": gauge24,
        "flash_flood_watch": ff_watch,
    }
    summary.update(tmd)
    write_atomic(SUMMARY, json.dumps(summary, ensure_ascii=False).encode("utf-8"))
    append_forecast_log(forecast_record(summary))
    log("saved %s (%dB) z=%s/%dpx %.0fm/px view=%.0fkm cls=%s cover=%.1f%% soon=%s(%smin) "
        "mm60=%s approaching=%s in %.1fs"
        % (OUT, os.path.getsize(OUT), zoom, tile_px, mpp, view_km, LEVEL_TH[cls30],
           cover30, rain_soon, soon_min, om_mm60, approaching, time.time() - t0))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(build())
    except Exception as e:
        log("FATAL:", repr(e))
        sys.exit(1)