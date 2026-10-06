"""Add a radar/weather view to the Home Assistant Lovelace dashboard.

Safe by design: it reads the current config, appends ONE new view (idempotent -
re-running replaces the view it owns instead of duplicating it) and saves.
Never touches existing views.
"""
import json
import os
import time
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

VIEW = {
    "type": "sections",
    "view_layout": {"grid_area": "ha_weather"},
    "title": "ฝน & อากาศ",
    "path": VIEW_ID,
    "icon": "mdi:weather-rainy",
    "max_columns": 4,
    "cards": [
        {"type": "picture",
         "image": "/local/radar/latest.png?v={{ now().timestamp()|int }}",
         "name": "เรดาร์ฝน 30 กม. รอบบ้าน"},
        {"type": "tile", "entity_id": "binary_sensor.radar_rain_now",
         "name": "ฝนที่บ้าน", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "binary_sensor.radar_rain_approaching",
         "name": "ฝนกำลังมา", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "sensor.radar_rain_class",
         "name": "ระดับฝน", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "sensor.radar_rain_near",
         "name": "ฝนใน 30 กม.", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "sensor.rain_next_60min_mm",
         "name": "ฝน 1 ชม. ข้างหน้า", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "sensor.rain_soon_in_min",
         "name": "ฝนมาในอีก", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "binary_sensor.rain_soon",
         "name": "nowcast มีฝน", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "binary_sensor.flash_flood_watch",
         "name": "เฝ้าระวังน้ำท่วม", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "sensor.pm25_home",
         "name": "PM2.5 บ้าน", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "sensor.pm25_pcd",
         "name": "PM2.5 สถานี PCD", "color": "blue", "show_entity_picture": False},
        {"type": "tile", "entity_id": "sensor.rain24_home",
         "name": "ฝน 24 ชม.", "color": "blue", "show_entity_picture": False},

        {"type": "entities", "title": "กรมฝนหลวง (TMD)",
         "entities": ["sensor.tmd_rain_24h_pct", "sensor.tmd_7d_rain_pct",
                      "sensor.tmd_forecast_7d", "sensor.tmd_warning"]},
        {"type": "entities", "title": "รายละเอียดเรดาร์",
         "show_header_toggle": False,
         "entities": [
             {"entity": "sensor.radar_rain_near", "name": "ฝนใน 30 กม. (%)"},
             {"entity": "sensor.radar_rain_class", "name": "ระดับฝนใกล้บ้าน"},
             {"entity": "sensor.radar_zoom", "name": "ความละเอียดเรดาร์ (zoom)"},
             {"entity": "binary_sensor.radar_rain_now", "name": "ฝนตกที่บ้าน"},
             {"entity": "binary_sensor.radar_rain_approaching", "name": "ฝนกำลังจะมาถึง"},
             {"entity": "sensor.rain_next_60min_mm", "name": "ฝน 1 ชม. ข้างหน้า (mm)"},
             {"entity": "sensor.rain_soon_in_min",
              "name": "ฝนมาในอีก (นาที, -1 = ยังไม่มีสัญญาณ)"},
             {"entity": "binary_sensor.rain_soon", "name": "nowcast มีฝนภายใน 1 ชม."},
             {"entity": "binary_sensor.flash_flood_watch", "name": "เฝ้าระวังน้ำท่วมฉับพลัน"},
         ]},
        {"type": "entities", "title": "คุณตั้งค่าแจ้งเตือน",
         "show_header_toggle": False,
         "entities": [
             {"entity": "input_boolean.weather_alerts_enabled", "name": "เปิดการแจ้งเตือน"},
             {"entity": "input_number.pm25_alert_threshold", "name": "เกณฑ์ PM2.5 (µg/m³)"},
             {"entity": "input_number.rain_alert_threshold", "name": "เกณฑ์ฝน 24 ชม. (mm)"},
         ]},
    ],
}


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
    views = cfg.get("views") or []
    print("current views:", [v.get("title") or v.get("path") for v in views])

    new_view = json.loads(json.dumps(VIEW))
    replaced = False
    for i, v in enumerate(views):
        if v.get("path") == VIEW_ID:
            new_view["view_layout"] = v.get("view_layout", new_view.get("view_layout"))
            views[i] = new_view
            replaced = True
            break
    if not replaced:
        views.append(new_view)
    cfg["views"] = views
    cfg.setdefault("background_color", "#111417")
    cfg.setdefault("theme", "default-dark")

    ws.send(json.dumps({"id": 2, "type": "lovelace/config/save", "config": cfg}))
    resp = json.loads(ws.recv())
    print("save:", "OK" if resp.get("success") else resp)
    print("views now:", [v.get("title") or v.get("path") for v in cfg["views"]])
    ws.close()
    return 0 if resp.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())