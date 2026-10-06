# HA Weather Radar — Bueng Kum, Bangkok

Rain radar + PM2.5 + nowcast "rain approaching home" alerts for Home Assistant,
delivered to Telegram with a rendered map image.

Home coordinates: **13.828773, 100.6545224** (Bueng Kum, Bangkok)
Alert radius: **30 km** (solid ring) plus a **10 km** dashed inner ring and scale bar

## What is in this repo

| File | Purpose |
|---|---|
| `radar_notify.py` | Runs inside the HA Core container. Builds `/local/radar/latest.png` + `summary.json`. |
| `weather_radar.yaml` | HA package: `shell_command`, all `command_line` sensors, REST sensors for PM2.5/rain, thresholds. |
| `make_automations.py` | Creates/updates the 7 automations with Thai Telegram captions. |
| `add_lovelace_view.py` | Adds the "ฝน & อากาศ" Lovelace view (idempotent, keeps existing views). |
| `test_radar_notify.py` | 69 offline tests: geometry, palette/ramp, histogram, grid selection, mosaic, tile-size probe, TMD carry-forward, HII tri-state, the Open-Meteo sentinel, the YAML sentinel guards, home class. |
| `test_captions.py` | 23 offline tests: every caption renders through real Jinja2, and no sentinel (`-1`, `"None"`, `unknown`, `unavailable`) can reach a message from a TMD **or** a PM2.5/rain/Open-Meteo sensor. |
| `fonts/Sarabun-*.ttf` | Thai+Latin font so the image can render Thai labels. |

Run the tests with `python test_radar_notify.py; python test_captions.py`
(neither touches the network or Home Assistant; `test_captions.py` needs
`jinja2`, which HA already ships).

## Data sources

| What | Source | Notes |
|---|---|---|
| Radar tiles | RainViewer `api.rainviewer.com/public/weather-maps.json` | **Only zoom 4–7 serve real tiles**; z8+ returns a byte-identical "not supported" placeholder, which the script rejects automatically. At z7 RainViewer also serves *oversized* tiles — the script requests `4096_4096`, which is the same footprint as a `256_256` tile at **16× the detail (74 m/px)**. Verified: the 4096 tile downsampled to 256 matches the 256 tile at zero offset, so it is a genuine high-resolution render, not an upscaled one. |
| Base map | OpenStreetMap tiles, restyled dark, cached 6 h in `/config/www/radar/cache` | |
| Point nowcast | Open-Meteo `minutely_15=precipitation` (2 h @ 15 min) | primary "is rain coming" signal |
| Rain nowcast | RainViewer `nowcast` frames when published | **discontinued** — the index reports 0 nowcast frames, so this path is dormant |
| Rain trend | 5-frame coverage trend of the inner 15 km ring | sampled at the selected zoom, capped at grid=256 for speed |
| Official forecast | **TMD (กรมฝนหลวง)** `data.tmd.go.th/api/` with the public demo credentials `uid=api&ukey=api12345` | 24 h Bangkok narrative + %rain, 7-day %rain, official warnings |
| Flash-flood watch | HII `api.hii.or.th` `warning/flashflood-24h` | tri-state: a failed fetch is *unknown*, so the previous value is held rather than reported as "no watch" |
| PM2.5 | Open-Meteo air quality (at home) + PCD Air4Thai station `bkp77t` (`verify_ssl: false`) | GISTDA is behind Incapsula, not used |
| Rain 24 h | ThaiWater `api-v3.thaiwater.net` station `BKK021` | |

## Rain intensity levels

RainViewer returns a fixed palette: **40 discrete echo colours** (pale cyan →
blue → yellow → orange → red, i.e. light → heavy) plus a **translucent
warm-grey coverage mask**. Measured from real frames: 20 of the 40 echo
colours are blue/cyan (hue 190–200), 4 yellow, 6 orange, 10 red, and **zero
magenta** — an earlier version of this README claimed a magenta band.

Classification is a **rank lookup along that ramp**, not hue/saturation
arithmetic. `_RAMP` lists the stations in observed intensity order and
`_RAMP_CLASS` cuts them into 5 equal classes of 8:

| Class | `_RAMP` stations | Representative colour | Thai label |
|---|---|---|---|
| 1 | 0–7 | `(81, 197, 232)` pale cyan | เบา |
| 2 | 8–15 | `(0, 127, 180)` mid blue | ปานกลาง |
| 3 | 16–23 | `(255, 210, 0)` dark blue → yellow | หนัก |
| 4 | 24–31 | `(255, 139, 0)` orange → bright red | หนักมาก |
| 5 | 32–39 | `(118, 0, 0)` dark red | รุนแรง |

A pixel that is an exact station colour is a dict lookup. An off-ramp colour is
placed at the nearest station, so the script keeps working if RainViewer shifts
the palette slightly.

**Why a table and not hue/saturation.** Saturation runs *opposite* to
intensity at the blue end — it is high at the strong deep-blue end and low at
the weak pale-cyan end. A single saturation floor therefore deletes the
lightest rain: the old `s < 0.55` gate threw away `(108, 209, 235)`
(s = 0.54) and `(136, 221, 238)` (s = 0.43), **26,873 px — 19.9 % of the blue
band** — and it also had the blue and cyan bands ranked backwards.

The coverage mask is excluded separately, on **hue and saturation together**:
it is warm (hue 42–48) and desaturated (s ≤ 0.34), so the mask test is
`not (170 ≤ hue ≤ 230) and s < 0.60`. That rejects 25 translucent warm-grey
stations without touching the pale cyan end. Verified: 0 of 30,913 mask
pixels leak, and pale cyan is confirmed as real rain rather than mask — alpha
255 vs the mask's alpha < 255, 0 % adjacency to transparency, and 59.2 % of its
pixels touching the coverage edge versus ≤ 4.1 % for every other blue.

**Effect of the fix.** Across 12 archived frames `coverage_30km` rose from a
mean of 33.58 % to 40.75 % (**+7.18 pp**, per-frame +0.47 to +12.32 pp).
**No pixel that counted as rain before stopped counting as rain**; 18,101
previously-ignored pixels now count. Transitions seen: `-1 → 0` (pale cyan
recovered), `1 → 0` (blue band re-ranked), `0 → 1`.

The legend is drawn on the image above the scale bar (bottom-left), with each
swatch taken from `_RAMP` itself, so a swatch cannot advertise a colour the
classifier rejects. This is checked against the deployed PNG: all 5 swatches
sample at 100 % purity and `classify()` returns exactly the class each claims.
The strongest class inside the 30 km ring is exposed as
`sensor.radar_rain_class`.

> No dBZ numbers are claimed: RainViewer does not publish a colour→dBZ table for
> the current palette, so levels are relative.
>
> `rain_class_30km` is a **maximum** over a 30 km disc, so a single cell of
> intense rain reports ฝนรุนแรง even when the disc is 99 % clear. 3 of 12
> archived frames now report class 5 where the old, mis-ranked code reported 4.

## summary.json fields

```
updated, frame_time, radar_zoom, radar_tile_px, radar_mpp, view_km, base_zoom, radar_ok,
rain_now, rain_soon, rain_soon_in_min, rain_next_60min_mm, approaching,
coverage_30km, coverage_inner15km, trend_inner15km,
rain_class_30km, rain_class_label, rain_class_label_en,
rainviewer_nowcast, openmeteo_ok, flash_flood_watch,
tmd_24h, tmd_24h_rain_pct, tmd_7d_rain_pct, tmd_7d_desc, tmd_warning
```

`rain_soon_in_min` is **`-1` when there is no nowcast signal at all** — never
`null`. HA's `command_line` platform turns a rendered `"None"` into
`unavailable`, so the script writes a numeric sentinel instead.

## Deployment

```bash
# 1. fonts (Thai labels on the image)
scp fonts/Sarabun-*.ttf  hassio@HA:/config/scripts/fonts/

# 2. script
scp radar_notify.py        hassio@HA:/config/scripts/radar_notify.py

# 3. package  ->  /config/packages/weather_radar.yaml
#    and add to configuration.yaml:
#      packages: !include_dir_named packages

# 4. check + restart
ha core check          # or POST /api/config/core/check_config
```

Then create the automations and dashboard:

```bash
python make_automations.py      # POSTs the 7 automations with Thai captions
python add_lovelace_view.py     # adds the "ฝน & อากาศ" view
```

Both deploy scripts now read their credential from the environment — **never
commit a token**. Export it first:

```bash
export HA_TOKEN='<long-lived access token>'      # PowerShell: $env:HA_TOKEN='...'
python make_automations.py
python add_lovelace_view.py
```

`HA_BASE` (default `http://192.168.1.248:8123`) and `HA_WS_URL` override the
endpoints. Any token that has been pasted into a chat or committed once is
compromised and **must be revoked** — removing it from a later commit does not
remove it from git history.

## Automations

| Alias | Trigger |
|---|---|
| รีเฟรชเรดาร์เป็นระยะ | every 15 min (rebuilds image + JSON) |
| สรุปอากาศเช้า | 06:00 |
| สรุปอากาศเย็น | 17:00 |
| แจ้งเตือน PM2.5 บ้าน สูง | `sensor.pm25_home` over threshold, 2 h cooldown |
| แจ้งเตือนฝน 24 ชม. สูง | `sensor.rain24_home` over threshold, 2 h cooldown |
| แจ้งเตือน ฝนกำลังจะมาถึงบ้าน | `binary_sensor.radar_rain_approaching` off→on, 1 h cooldown |
| แจ้งเตือน ฝนตกที่บ้าน | `binary_sensor.radar_rain_now` off→on, 1 h cooldown |

All gated by `input_boolean.weather_alerts_enabled`.

## Environment overrides

`radar_notify.py` reads env vars if you want to tweak without editing:
`RADAR_HOME_LAT`, `RADAR_HOME_LON`, `RADAR_SIZE` (px, default 950),
`RADAR_VIEW_KM` (ground width of the image; `0` = native, i.e. no upscaling),
`RADAR_ZOOMS` (default `11,10,9,8,7`),
`RADAR_TILE_SIZES` (default `4096,2048,1024,512,256`),
`RADAR_RADIUS_KM`, `RADAR_INNER_KM`, `RADAR_NEAR_KM`,
`RADAR_PALETTE`, `RADAR_SCHEME`, `RADAR_TREND_FRAMES`,
`RADAR_OUT`, `RADAR_SUMMARY`, `RADAR_CACHE`, `RADAR_FONTS`.

## Crop and resolution

The image is cropped to the area around the house, not a whole-province view.
Rendering is driven by one number — the radar's ground resolution:

* `4096_4096` tiles at z7 → **74 m/px**. At the default 950 px that is a
  **70.5 km wide** frame, so the 30 km alert ring fills most of the width
  (404 px radius) and the 10 km dashed ring sits comfortably inside it.
* The OSM base map zoom is derived from the same number (`zoom_for_mpp`), so
  base and radar always share a resolution and stay registered — currently z11
  at 74.2 m/px, confirmed identical to the radar scale.
* To zoom further in, set `RADAR_VIEW_KM` (e.g. `40` for a 40 km frame). That
  upsamples the radar, so the picture softens; the default of `0` renders at
  native resolution and upscales nothing.
* Requesting a tile size RainViewer does not serve returns a 256 px image
  instead, so the decoded size is checked and the next size down is tried
  (`4096 → 2048 → 1024 → …`). A frame that was just generated can answer HTTP
  410 briefly, hence the retries.

## Notes / limitations

* The Core container has **no system fonts**; the Sarabun TTFs in
  `/config/scripts/fonts` are required, otherwise PIL falls back to a tiny bitmap font.
* **No numpy** in the Core container — coverage/histogram sampling is pure Python.
* RainViewer cut off composite tiles and nowcast frames for free users and caps
  zoom at 7, so resolution comes from oversized z7 tiles rather than deeper zooms.
  If RainViewer restores z8+ the script picks it up with no code change.
* TMD occasionally resets the connection mid-run (`WinError 10054`). Each of the
  three TMD blocks is now tracked individually: `tmd_forecast()` returns
  `(values, failed)` and only keys named in `failed` fall back to the previous
  run's value via `carry_forward()`. The distinction matters because `None`
  means two different things here — the fetch broke, *or* the answer genuinely
  is "no warning". Carrying a stale warning forward would announce a storm that
  has already passed, so a cleared warning is never resurrected.
* TMD being unreachable **did** previously put the literal string `None` on the
  dashboard: the `command_line` platform renders a missing key as `"None"`, and
  `sensor.tmd_forecast_24h` had no `default()` guard. Proven live by writing
  `null` into `summary.json` and forcing `update_entity`. Every nullable field
  now has a sentinel (see the convention block at the top of
  `weather_radar.yaml`), and the captions filter sentinels independently.
* **`rain_next_60min_mm` had the same hole and was missed by the first pass.**
  `openmeteo_nowcast()` returned `None` for the total on failure and its
  template had no `default()` guard, so an Open-Meteo outage would have put
  `None mm` in every morning and evening message. The failure path now returns
  the `-1` sentinel, the template carries `default(-1, true)`, and
  `test_captions.py` covers the PM2.5/rain/Open-Meteo sensors rather than only
  TMD. Minutes is deliberately left `None`: a radar estimate still competes
  with it, and a `-1` would win that comparison and be reported as the ETA.
* **Flash-flood is tri-state.** `hii_flashflood_watch()` returns `None` when the
  feed cannot be read, and `carry_flag()` holds the previous value. A failed
  fetch used to return `False`, i.e. "no watch", which for a warning product
  reads as all-clear on no evidence. A genuine `False` still wins — a cleared
  watch is news.
* `latest.png` and `summary.json` are both written atomically (`write_atomic`),
  so a Telegram send racing the build cannot attach a half-written image.
* `class_histogram()` honours its `grid` argument. It used to overwrite it
  unconditionally, so the trend loop's `grid=256` silently became 300/540.
* `pick_tile_size()` can spend `5 sizes × 3 attempts × fetch(timeout=120)` ≈
  30 min if the network times out rather than refusing, and
  `shell_command.weather_radar_build` sets no `timeout:` — so HA's default
  (~60 s) can kill the build before `write_atomic` runs, leaving the dashboard
  serving stale data with no indication. Worth an explicit `timeout:`.
* **RainViewer nowcast is discontinued** — `radar nowcast frames: 0`. The
  nowcast code path and `sensor.rain_soon_in_min` are therefore effectively
  dead: the sensor parks at `-1` ("unknown") and `binary_sensor.radar_rain_soon`
  never turns on. `sensor.rain_next_60min_mm` comes from Open-Meteo and is
  unaffected.
* The green band of the old README palette (hue 80–170) **never appears** in
  real frames: 0 px across 3 frames × 3 tiles.
* `rain_class_30km` is a max over the 30 km disc, not an average — see the
  intensity section.
* The script runs ~21 s cold (parallel tile fetches, cached OSM tiles afterwards).