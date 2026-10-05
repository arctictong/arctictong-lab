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
PALETTE = os.environ.get("RADAR_PALETTE", "2")
SCHEME = os.environ.get("RADAR_SCHEME", "1_1")
TREND_FRAMES = int(os.environ.get("RADAR_TREND_FRAMES", "4"))
CACHE_TTL = 6 * 3600
UA = "ha-home-weather-radar/2.0 (personal home automation)"

BASE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
RV_INDEX = "https://api.rainviewer.com/public/weather-maps.json"
OM_URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
          "&minutely_15=precipitation&forecast_minutely_15=8&timezone=Asia%2FBangkok")
TMD_BASE = "https://data.tmd.go.th/api/%s/v%s/?uid=api&ukey=api12345"
HII_FF24 = ("https://api.hii.or.th/v2/4UQaYnf0Bx4fXPYyCdDRbqHyXH9Ixvd2nVUjaN1cLBY="
            "/warning/flashflood-24h")
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


def fetch(url, timeout=45):
    if url in _MEMO:
        return _MEMO[url]
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


def class_histogram(img, mpp, bands, ref_km, grid=None):
    """Bucket echoes into intensity classes per radius band.

    bands = [(r_inner_km, r_outer_km, key), ...]; ref_km sets the sampling
    density so the reference circle keeps at least ~100 samples across.
    """
    w = img.size[0]
    ref_px = (ref_km * 1000.0) / mpp
    grid = 300 if ref_px >= 100 else 540
    grid = min(grid if grid else 512, img.size[0])
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


def home_class(tile_url, z, cx, cy, mpp, tpx=TILE):
    """Strongest echo class within ~1.5 km of home, -1 when there is none."""
    r = max(0, min(1200, int(round(1500.0 / mpp))))
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
    """
    for s in TILE_SIZES:
        for attempt in range(3):
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
                time.sleep(3)
    return TILE


def openmeteo_nowcast():
    try:
        d = json.loads(fetch(OM_URL.format(lat=HOME_LAT, lon=HOME_LON)))
        precip = d.get("minutely_15", {}).get("precipitation", [])
        rain_soon, minutes = False, None
        for i, p in enumerate(precip[:6]):
            if p and p > 0.1:
                rain_soon, minutes = True, i * 15
                break
        return rain_soon, minutes, round(sum(p or 0 for p in precip[:4]), 2), True
    except Exception as e:
        log("open-meteo failed: %s" % e)
        return False, None, None, False


def hii_flashflood_watch():
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
        return False


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


def write_atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


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


def build():
    t0 = time.time()
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
    rv_soon = False
    rv_min = None
    if past and zoom:
        tmpl = make_url(tile_px, "{z}", "{x}", "{y}")
        rcx, rcy = latlon_to_world_px(HOME_LAT, HOME_LON, zoom, tile_px)
        workers = 3 if tile_px >= 2048 else 8
        radar, n_rv = mosaic(tmpl, zoom, rcx, rcy, size, tile_px, workers=workers)
        log("radar: %d tiles @z%d/%dpx" % (n_rv, zoom, tile_px))
        canvas = Image.alpha_composite(canvas, radar)
        st = class_histogram(radar, mpp,
                             [(0.0, RADIUS_KM, "cover30"),
                              (0.0, INNER_KM, "inner15")],
                             RADIUS_KM)
        cover30, cls30 = st["cover30"]["pct"], st["cover30"]["class"]
        inner15 = st["inner15"]["pct"]
        rain_now = home_class(tmpl, zoom, rcx, rcy, mpp, tile_px) >= 0
        log("coverage30=%.2f%% (class %d) inner15=%.2f%% now=%s"
            % (cover30, cls30, inner15, rain_now))

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
                                    INNER_KM, grid=256)["i"]["pct"]
                if f is past[-1]:
                    inner_now = v
                else:
                    olds.append(v)
            except Exception:
                pass
        if olds and inner_now is not None:
            trend = round(inner_now - min(olds), 2)
            log("trend inner15: past=%s now=%.2f delta=%s" % (olds, inner_now, trend))

    if nowcast and past and zoom:
        f = nowcast[-1]
        tmpl = ("%s%s/%d_%d/{z}/{x}/{y}/%s/%s.png"
                % (host, f["path"], tile_px, tile_px, PALETTE, SCHEME))
        rcx, rcy = latlon_to_world_px(HOME_LAT, HOME_LON, zoom, tile_px)
        rv_soon = home_class(tmpl, zoom, rcx, rcy, mpp, tile_px) >= 0
        if f.get("time") and past[-1].get("time"):
            rv_min = int(round((f["time"] - past[-1]["time"]) / 60.0))

    om_soon, om_min, om_mm60, om_ok = openmeteo_nowcast()
    rain_soon = rv_soon or om_soon
    soon_min = None
    for m in (om_min, rv_min):
        if m is not None and (soon_min is None or m < soon_min):
            soon_min = m
    # -1 instead of null: HA's command_line platform turns a rendered "None"
    # into `unavailable`, so the sentinel keeps the sensor numeric.
    if soon_min is None:
        soon_min = -1

    trend_up = bool(trend is not None and trend >= 3.0 and inner15 >= 1.0)
    approaching = bool((not rain_now) and (rain_soon or trend_up))
    ff_watch = hii_flashflood_watch()
    tmd_values, tmd_failed = tmd_forecast()
    tmd = carry_forward(tmd_values, tmd_failed, read_previous_summary())
    if tmd_failed:
        log("tmd keys held from previous run: %s" % sorted(tmd_failed))

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

    canvas.convert("RGB").save(OUT, "PNG", optimize=True)

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
        "rain_soon": rain_soon,
        "rain_soon_in_min": soon_min,
        "rain_next_60min_mm": om_mm60,
        "approaching": approaching,
        "coverage_30km": cover30,
        "coverage_inner15km": inner15,
        "trend_inner15km": trend,
        "rain_class_30km": cls30,
        "rain_class_label": LEVEL_TH[cls30],
        "rain_class_label_en": LEVEL_EN[cls30],
        "rainviewer_nowcast": bool(nowcast),
        "openmeteo_ok": om_ok,
        "flash_flood_watch": ff_watch,
    }
    summary.update(tmd)
    write_atomic(SUMMARY, json.dumps(summary, ensure_ascii=False).encode("utf-8"))
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