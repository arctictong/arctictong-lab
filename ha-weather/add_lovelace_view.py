"""Add the rain/weather views to the Home Assistant Lovelace dashboard.

Safe by design: reads the current config, replaces only the views this script
owns (matched by path) and appends the rest. Never reorders or edits a view it
does not own, and a replaced view keeps its `view_layout` so its position in
the dashboard grid survives.

Two views are written:

  ha-weather       the main one - answer first, then the two numbers, then
                   everything else on the same page
  ha-weather-tech  a subview for the raw sensors, reached from a button at the
                   bottom of the main view

The previous version set `cards:` on a `type: sections` view, which is not a
key that view type reads - so the view it added was empty. `sections:` is the
correct key and is what this writes.
"""
import json
import os
import urllib.request

import websocket


def require_token():
    """The HA long-lived token, read from the environment.

    Never hardcode it: a committed token is a live credential sitting in git
    history for anyone with repo access, and the repo's own convention
    (dashboard/README.md: "ไม่มี key ฝังใน git") forbids it.
    """
    tok = os.environ.get("HA_TOKEN") or os.environ.get("HASS_TOKEN")
    if not tok:
        raise SystemExit(
            "set HA_TOKEN in the environment before running this script "
            "(the token must not live in the repo)")
    return tok


URL = os.environ.get("HA_WS_URL", "ws://192.168.1.248:8123/api/websocket")
VIEW_ID = "ha-weather"
TECH_ID = "ha-weather-tech"

RADAR_URL = "/local/radar/latest.png?v={{ now().timestamp()|int }}"


def models_line():
    """A quiet confidence line: how many models agree, and the chance.

    -1 is the "no consensus" sentinel and is filtered here rather than shown,
    so the dashboard never displays it.
    """
    return (
        "{% set a = states('sensor.rain_models_agree')|int(-1) %}"
        "{% if a >= 0 %}"
        "<br>โมเดล {{ a }}/{{ states('sensor.rain_models_total') }}"
        "{% set p = states('sensor.rain_chance')|int(-1) %}"
        "{% if p >= 0 %} · โอกาส {{ p }}%{% endif %}"
        "{% endif %}"
    )


def status_markdown():
    """The single sentence that answers "what is happening at home".

    Ordered most specific first. The wording and the colours match the Telegram
    captions, so the two never disagree about what the state is called.

    `rain_soon_in_min` is only mentioned when it is above zero; -1 means "no
    estimate" and must never be printed.
    """
    return (
        # a fresh install has no summary yet; say so rather than claim dry
        "{% if states('sensor.rain_next_60min_mm') in ['unknown','unavailable'] %}"
        "<ha-alert alert-type=\"info\">กำลังโหลดข้อมูลเรดาร์…</ha-alert>"
        "{% elif is_state('binary_sensor.flash_flood_watch','on') %}"
        "<ha-alert alert-type=\"error\">⚠️ <b>เฝ้าระวังน้ำท่วมฉับพลัน</b><br>"
        "มีประกาศจาก HII สำหรับพื้นที่นี้ (24 ชม.)</ha-alert>"
        "{% elif is_state('binary_sensor.radar_rain_now','on') %}"
        "<ha-alert alert-type=\"info\">☔ <b>ฝนตกที่บ้านตอนนี้</b><br>"
        "ความแรงที่บ้าน {{ states('sensor.rain_at_home') }}"
        "{% if states('sensor.rain_soon_in_min')|int(0) > 0 %}"
        " · คาดว่าต่ออีก ~{{ states('sensor.rain_soon_in_min')|int }} นาที"
        "{% endif %}</ha-alert>"
        "{% elif is_state('binary_sensor.radar_echo_near','on') %}"
        "<ha-alert alert-type=\"warning\">🌧️ <b>ตรวจพบกลุ่มฝนใกล้บ้าน</b><br>"
        "คาดว่าจะตกในไม่ช้า</ha-alert>"
        "{% elif is_state('binary_sensor.radar_rain_approaching','on') %}"
        "<ha-alert alert-type=\"warning\">⏱️ <b>คาดว่าฝนจะมา</b><br>"
        "{% if states('sensor.rain_soon_in_min')|int(0) > 0 %}"
        "อีกประมาณ {{ states('sensor.rain_soon_in_min')|int }} นาที"
        "{% else %}แบบจำลองคาดว่าฝนจะมา แต่ยังไม่พบกลุ่มฝนใกล้บ้าน{% endif %}"
        "</ha-alert>"
        "{% else %}"
        "<b>ยังไม่มีฝนที่บ้าน</b><br>ไม่พบกลุ่มฝนใน 30 กม."
        "{% endif %}" + models_line()
    )


STATUS_CARD = {"type": "markdown", "card_size": 3,
               "content": status_markdown()}

RADAR_CARD = {
    "type": "picture",
    "image": RADAR_URL,
    "name": "เรดาร์ฝน 30 กม. รอบบ้าน",
    # tapping opens the full-resolution image rather than nothing
    "tap_action": {"action": "url", "url_path": RADAR_URL},
}

# Only the two numbers that are asked for most; everything else is a row below.
METRICS_CARD = {
    "type": "glance",
    "columns": 2,
    "show_icon": False,
    "entities": [
        {"entity": "sensor.rain_next_60min_mm", "name": "ฝน 1 ชม."},
        {"entity": "sensor.pm25_home", "name": "PM2.5"},
    ],
}

RAIN_CARD = {
    "type": "entities", "title": "ฝน", "show_header_toggle": False,
    "entities": [
        {"entity": "sensor.rain_at_home", "name": "ความแรงที่บ้าน"},
        {"entity": "sensor.radar_rain_near", "name": "ฝนใน 30 กม."},
        {"entity": "sensor.rain_past_60min_mm", "name": "ฝน 1 ชม. ที่ผ่านมา"},
        {"entity": "sensor.rain24_home", "name": "ฝน 24 ชม. (สถานีบ้าน)"},
    ],
}

TMD_CARD = {
    "type": "entities", "title": "กรมฝนหลวง", "show_header_toggle": False,
    "entities": [
        {"entity": "sensor.tmd_rain_24h_pct", "name": "พยากรณ์ 24 ชม."},
        {"entity": "sensor.tmd_rain_7d_pct", "name": "พยากรณ์ 7 วัน"},
        {"entity": "sensor.tmd_warning", "name": "ประกาศเตือน"},
        {"entity": "sensor.tmd_forecast_24h", "name": "รายละเอียด 24 ชม."},
    ],
}

AIR_CARD = {
    "type": "entities", "title": "คุณภาพอากาศ", "show_header_toggle": False,
    "entities": [
        {"entity": "sensor.pm25_home", "name": "PM2.5 บ้าน (แบบจำลอง)"},
        {"entity": "sensor.pm25_level", "name": "ระดับ"},
        {"entity": "sensor.pm25_source", "name": "แหล่งข้อมูล"},
        {"entity": "sensor.pm25_pcd", "name": "PM2.5 สถานี PCD (วัดจริง)"},
    ],
}

SETTINGS_CARD = {
    "type": "entities", "title": "ตั้งค่าการแจ้งเตือน",
    "show_header_toggle": False,
    "entities": [
        {"entity": "input_boolean.weather_alerts_enabled", "name": "เปิดการแจ้งเตือน"},
        {"entity": "input_number.pm25_alert_threshold", "name": "เกณฑ์ PM2.5"},
        {"entity": "input_number.rain_alert_threshold", "name": "เกณฑ์ฝน 24 ชม."},
    ],
}

TECH_BUTTON = {
    "type": "button", "name": "ข้อมูลเทคนิค", "icon": "mdi:radar",
    "show_state": False, "show_name": True,
    "tap_action": {"action": "navigate", "navigation_path": "/" + TECH_ID},
}

VIEW = {
    "type": "sections",
    "view_layout": {"grid_area": "ha_weather"},
    "title": "ฝน & อากาศ",
    "path": VIEW_ID,
    "icon": "mdi:weather-rainy",
    "max_columns": 2,
    # `sections`, not `cards`: a sections view reads this key, and the old
    # script's `cards` is why the view came out empty.
    "sections": [
        {"type": "grid", "cards": [RADAR_CARD]},
        {"type": "grid", "cards": [STATUS_CARD, METRICS_CARD, RAIN_CARD,
                                   TMD_CARD, AIR_CARD, SETTINGS_CARD,
                                   TECH_BUTTON]},
    ],
}

VIEW_TECH = {
    "type": "sections",
    "title": "ข้อมูลเทคนิค",
    "path": TECH_ID,
    "icon": "mdi:radar",
    "subview": True,
    "max_columns": 2,
    "sections": [
        {"type": "grid", "cards": [
            {"type": "entities", "title": "เรดาร์",
             "show_header_toggle": False,
             "entities": [
                 {"entity": "sensor.radar_zoom", "name": "zoom"},
                 {"entity": "sensor.radar_rain_near", "name": "ฝนใน 30 กม. (%)"},
                 {"entity": "sensor.rain_at_home", "name": "ความแรงที่บ้าน"},
             ]},
            {"type": "entities", "title": "สัญญาณฝน",
             "show_header_toggle": False,
             "entities": [
                 {"entity": "binary_sensor.radar_rain_now", "name": "ฝนตกที่บ้าน (700 ม.)"},
                 {"entity": "binary_sensor.radar_echo_near", "name": "กลุ่มฝนใกล้บ้าน (1.5 กม.)"},
                 {"entity": "binary_sensor.radar_rain_approaching", "name": "ฝนกำลังมา"},
                 {"entity": "sensor.rain_motion", "name": "การเคลื่อนที่ของกลุ่มฝน"},
                 {"entity": "sensor.rain_motion_eta", "name": "ถึงบ้านใน (นาที, -1 = ไม่มี)"},
                 {"entity": "binary_sensor.rain_soon", "name": "โมเดลเห็นฝนใน 2 ชม."},
                 {"entity": "sensor.rain_soon_in_min",
                  "name": "ฝนมาในอีก (นาที, -1 = ไม่มีข้อมูล)"},
             ]},
        ]},
        {"type": "grid", "cards": [
            {"type": "entities", "title": "โมเดล",
             "show_header_toggle": False,
             "entities": [
                 {"entity": "sensor.rain_models_agree", "name": "โมเดลที่เห็นฝน"},
                 {"entity": "sensor.rain_models_total", "name": "จำนวนโมเดล"},
                 {"entity": "sensor.rain_chance", "name": "โอกาสฝน"},
                 {"entity": "sensor.rain_next_60min_mm", "name": "ฝน 1 ชม. (มม.)"},
             ]},
            {"type": "entities", "title": "ค่าดิบ",
             "show_header_toggle": False,
             "entities": [
                 {"entity": "sensor.pm10_home", "name": "PM10"},
                 {"entity": "sensor.pm25_pcd", "name": "PM2.5 PCD"},
                 {"entity": "sensor.rain24_home", "name": "ฝน 24 ชม. (มม.)"},
                 {"entity": "sensor.tmd_forecast_7d", "name": "TMD 7 วัน"},
             ]},
        ]},
    ],
}

WANTED = [VIEW, VIEW_TECH]


def merge_views(views, wanted):
    """Replace the views we own by path, append the ones we do not, keep order.

    A replaced view keeps its existing `view_layout`, which is how HA records
    where the view sits in the dashboard's grid; dropping it would move the
    tab. A view whose path we do not own is passed through untouched.
    """
    out = list(views)
    index = {v.get("path"): i for i, v in enumerate(out)
             if isinstance(v, dict) and v.get("path")}
    for want in wanted:
        want = json.loads(json.dumps(want))
        path = want.get("path")
        if path in index:
            old = out[index[path]]
            if isinstance(old, dict) and "view_layout" in old:
                want["view_layout"] = old["view_layout"]
            out[index[path]] = want
        else:
            index[path] = len(out)
            out.append(want)
    return out


def main():
    ws = websocket.create_connection(URL, timeout=30)
    ws.recv()
    ws.send(json.dumps({"type": "auth", "access_token": require_token()}))
    auth = json.loads(ws.recv())
    if auth.get("type") != "auth_ok":
        raise SystemExit("auth failed: %s" % auth)

    ws.send(json.dumps({"id": 1, "type": "lovelace/config"}))
    resp = json.loads(ws.recv())
    if not resp.get("success"):
        print("lovelace/config not readable:", resp)
        print("-> dashboard is probably YAML mode; no changes made.")
        ws.close()
        return 1
    cfg = resp["result"]
    # validate before use: the old order called cfg.get() first, so a non-dict
    # config raised AttributeError instead of reaching this branch
    if not isinstance(cfg, dict) or "views" not in cfg:
        print("unexpected config shape:", type(cfg))
        ws.close()
        return 1

    before = [v.get("title") or v.get("path") for v in cfg.get("views") or []]
    print("views before:", before)
    cfg["views"] = merge_views(cfg.get("views") or [], WANTED)
    print("views after :", [v.get("title") or v.get("path")
                            for v in cfg["views"]])

    ws.send(json.dumps({"id": 2, "type": "lovelace/config/save",
                        "config": cfg}))
    resp = json.loads(ws.recv())
    print("save:", "OK" if resp.get("success") else resp)
    ws.close()
    return 0 if resp.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
