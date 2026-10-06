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
| `add_lovelace_view.py` | Adds the "ฝน & อากาศ" view and its technical subview (idempotent: replaces only the views it owns). |
| `test_radar_notify.py` | 106 offline tests: geometry, palette/ramp, histogram, grid selection, mosaic, tile-size probe, the run time budget, the model consensus, the verification log, TMD carry-forward, HII tri-state, the Open-Meteo sentinel, the YAML sentinel guards, home class. |
| `test_captions.py` | 34 offline tests: every caption renders through real Jinja2, and no sentinel (`-1`, `"None"`, `unknown`, `unavailable`) can reach a message from a TMD **or** a PM2.5/rain/Open-Meteo sensor. |
| `test_score_forecast.py` | 24 offline tests: the verification label (gaps and the log end are *unknown*, not dry), the two prediction thresholds, the majority rule, all four confusion counts, and a torn log line being skipped. |
| `score_forecast.py` | Scores each Open-Meteo model against what actually happened. Run it on the HA host or offline; see "Which model is right". |
| `fonts/Sarabun-*.ttf` | Thai+Latin font so the image can render Thai labels. |

Run the tests with `python test_radar_notify.py; python test_captions.py;
python test_score_forecast.py` — they touch neither the network nor Home
Assistant (`test_captions.py` needs `jinja2`, which HA already ships).

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
| Rain 24 h | ThaiWater `api-v3.thaiwater.net` station `BKK021` | a real gauge ~7.8 km away. Also read by `radar_notify.py` itself so the verification log is self-contained |

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

## Which model is right

The nowcast is a **majority vote of three Open-Meteo models over a 3x3
neighbourhood** (see the constants at the top of `radar_notify.py`). A single
model at a single point is a poor estimator for Thai rain: a Bangkok convective
cell is 5-15 km across while a model grid box is 13-25 km. Measured at one
moment, same point:

| | next 2 h, per 15 min |
|---|---|
| `best_match`, single point (previously) | `[0,0,0,0,0,0,0,0]` → **dry** |
| 3 models, 9 points (now) | `[3,3,3,3,3,3,3,3]` → **rain now, 0.6 mm/h, 90 %** |

The old configuration missed rain that was falling. The models are
`ecmwf_ifs025`, `icon_seamless` and `gfs_seamless`; **JMA is deliberately
absent** because it is the coarsest (~55 km) and publishes no
`precipitation_probability` at 15-minute resolution.

But *which* three is an assumption, so the system collects the evidence to
check it. Every build appends a line to `forecast_log.jsonl` with each model's
own next-hour numbers plus two independent observations, and
`score_forecast.py` turns that into per-model truth counts:

```bash
python score_forecast.py /config/www/radar/forecast_log.jsonl
python score_forecast.py --min-hours 200        # refuse to rank before then
```

**Read the caveats before trusting the output.** The label is the **radar**, not
the gauge — it is independent of the models, which is what makes the comparison
meaningful, but it sees the beam and not the raingauge. The ThaiWater gauge is a
**rolling 24 h total**, so only a *positive* change between two reads confirms
rain; a zero change does not prove it stayed dry, so the gauge corroborates
events and never scores dry hours. And a window needs five contiguous records:
a missed build makes the hour *unknown* and the window is skipped, so an outage
is never counted as a model success. Expect the ranking to be meaningless for
the first few weeks.

## summary.json fields

```
updated, frame_time, radar_zoom, radar_tile_px, radar_mpp, view_km, base_zoom, radar_ok,
rain_now, rain_near, rain_now_class, rain_now_label, rain_soon, rain_soon_in_min,
rain_next_60min_mm, rain_past_60min_mm, approaching,
coverage_30km, coverage_inner15km, trend_inner15km,
rain_class_30km, rain_class_label, rain_class_label_en,
rainviewer_nowcast, openmeteo_ok, om_models, om_need, om_votes, om_prob,
om_per_model, rain24_gauge, flash_flood_watch,
tmd_24h, tmd_24h_rain_pct, tmd_7d_rain_pct, tmd_7d_desc, tmd_warning
```

`om_votes` is the per-slot model vote count and `om_per_model` the per-model
next-hour mm and probability; both feed `score_forecast.py`. `rain24_gauge` is
the ThaiWater reading the script takes for the same log, separate from
`sensor.rain24_home`.

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

## Dashboard

The view is built by `add_lovelace_view.py`, which replaces only the views it
owns (matched by `path`) and leaves every other view alone.

- **`ha-weather`** — the radar first, then one status sentence, then the two
  numbers that get asked for most (rain in the next hour, PM2.5). Everything
  else is a labelled row on the same page; the technical fields are not here.
- **`ha-weather-tech`** — a subview for `radar_zoom`, the raw signal flags and
  the model agreement. Reached from a button at the bottom of the main view, so
  a sentinel such as `-1` is never on the wall.

`max_columns: 2` is what makes it work on both: Home Assistant pairs the two
sections on a desktop and stacks them on a phone from the same config.

The status sentence is one markdown card whose wording **and colour** match the
Telegram captions, so the two never disagree about what the state is called.
The colour comes from `<ha-alert alert-type=...>`, a core Home Assistant
component, so there is no theme or HACS card to install:

| state | alert-type | sentence |
|---|---|---|
| nothing nearby | *(plain text)* | ยังไม่มีฝนที่บ้าน |
| echo within 1.5 km | `warning` | ตรวจพบกลุ่มฝนใกล้บ้าน |
| model consensus only | `warning` | คาดว่าฝนจะมา |
| echo over the house | `info` | ฝนตกที่บ้านตอนนี้ |
| HII flash-flood watch | `error` | เฝ้าระวังน้ำท่วมฉับพลัน |

> **A sections view reads `sections`, not `cards`.** The first version of this
> script wrote `cards` on a `type: sections` view, which that view type does
> not look at, so the view it added rendered nothing at all. If you ever rewrite
> this, the shape is `sections: [{type: grid, cards: [...]}]`.

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

`radar_notify.py` reads these if you want to tweak without editing. Defaults are
the ones in the code.

**Location and output**

| Var | Default |
|---|---|
| `RADAR_HOME_LAT` / `RADAR_HOME_LON` | `13.828773` / `100.6545224` |
| `RADAR_OUT` | `/config/www/radar/latest.png` |
| `RADAR_SUMMARY` | `/config/www/radar/summary.json` |
| `RADAR_CACHE` | `/config/www/radar/cache` |
| `RADAR_FONTS` | `/config/scripts/fonts` |

**Image**

| Var | Default | Notes |
|---|---|---|
| `RADAR_SIZE` | `950` | px, before `VIEW_KM` |
| `RADAR_VIEW_KM` | `0` | ground width; `0` = native, no upscaling |
| `RADAR_ZOOMS` | `11,10,9,8,7` | tried deepest-first |
| `RADAR_TILE_SIZES` | `4096,2048,1024,512,256` | largest RainViewer really serves wins |
| `RADAR_RADIUS_KM` | `30` | solid ring |
| `RADAR_INNER_KM` | `15` | stats band |
| `RADAR_NEAR_KM` | `10` | dashed ring |
| `RADAR_PALETTE` / `RADAR_SCHEME` | `2` / `1_1` | |
| `RADAR_TREND_FRAMES` | `4` | older frames compared for the trend |

**Rain at home — the two apertures**

| Var | Default | Notes |
|---|---|---|
| `RADAR_RAIN_NOW_M` | `700` | echoed *over* the house: "raining at home now" |
| `RADAR_RAIN_NEAR_M` | `1500` | echoed nearby: "rain is near" |

**Nowcast consensus** — this is the group to tune from `score_forecast.py`

| Var | Default | Notes |
|---|---|---|
| `RADAR_OM_MODELS` | `ecmwf_ifs025,icon_seamless,gfs_seamless` | JMA is excluded: coarsest, and no 15-minute probability |
| `RADAR_OM_CONSENSUS` | `2` | of 3 models — the majority rule |
| `RADAR_OM_RAIN_MM15` | `0.1` | mm in a slot that counts as rain |
| `RADAR_OM_PROB_PCT` | `50` | chance that also counts as rain |
| `RADAR_OM_NEIGHBOUR` | `0.05` | degrees; the 3×3 spans ~11 km |
| `RADAR_OM_HORIZON` | `8` | 15-minute slots ahead (2 h) |
| `RADAR_OM_PAST` | `4` | 15-minute slots behind (1 h) — this is the shift: past slots are prepended, so "now" is this index |

**Verification log**

| Var | Default | Notes |
|---|---|---|
| `RADAR_RAIN_STATION` | `BKK021` | ThaiWater gauge the log records |
| `RADAR_LOG` | `/config/www/radar/forecast_log.jsonl` | |
| `RADAR_LOG_MAX_BYTES` | `4194304` | trimmed to the newest half past this |

**Echo motion** — from two frames; see "Which model is right" for what it feeds

| Var | Default | Notes |
|---|---|---|
| `RADAR_ECHO_GRID` | `64` | cells across for the echo mask |
| `RADAR_ECHO_MAX_SHIFT` | `10` | cells searched per frame — bounds the fastest resolvable motion |
| `RADAR_ECHO_MIN_CELLS` | `6` | below this there is too little echo to answer |
| `RADAR_ECHO_MIN_OVERLAP` | `6` | below this the match is a guess, so no answer is given |
| `RADAR_ECHO_MIN_MARGIN` | `3` | cells a shift must explain beyond standing still, or the motion is called unmeasurable |

**Run safety**

| Var | Default | Notes |
|---|---|---|
| `RADAR_BUDGET_S` | `50` | must stay under HA's fixed 60 s shell_command kill |
| `RADAR_ANALYSIS_PX` | `1200` | ceiling for the histogram sampling resolution |

> Raising `RADAR_OM_PROB_PCT` or lowering `RADAR_OM_CONSENSUS` changes how often
> the nowcast fires. Do not tune them by feel: the log and `score_forecast.py`
> exist to answer that question with data.

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
* **The run polices its own time budget.** HA terminates a `shell_command`
  after 60 s and the docs are explicit that "there is no option to alter this
  behavior", so there is no `timeout:` key to set. A network that times out
  rather than refusing was the way to blow it: `pick_tile_size()` alone could
  spend `5 sizes × 3 attempts × fetch(timeout=120)` ≈ 30 min, and HA would kill
  the process before `write_atomic()` ran — leaving the dashboard quietly
  serving stale data with nothing to indicate it. `build()` now calls
  `set_deadline()` (default 50 s, `RADAR_BUDGET_S`), `fetch()` clamps every
  request to the time left and refuses once it is gone, and the probe returns
  early. Each source then degrades through the fallback it already had — the
  trend reads `None`, TMD carries forward, flash-flood holds, Open-Meteo writes
  its `-1` sentinel — instead of dragging the whole run past the kill.
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