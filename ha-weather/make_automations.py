import json
import urllib.error
import urllib.request

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI3MDdjYjU5MDk0NzE0ZTRlODIzOWUyMjIzMDlhN2NlNiIsImlhdCI6MTc5MTE4ODMwMSwiZXhwIjoyMTA2NTQ4MzAxfQ.kbgES4h4XfYz29m406WsqgZZJmwLn18ZuiBVtJ_hQDA"
BASE = "http://192.168.1.248:8123"
CHAT = "1448647267"
RADAR_FILE = "/config/www/radar/latest.png"

ENABLED = {"condition": "state", "entity_id": "input_boolean.weather_alerts_enabled", "state": "on"}

NOT_SET = ["unknown", "unavailable", "none", "ไม่มีเตือน", ""]


def send_photo(caption):
    return [
        {"service": "shell_command.weather_radar_build"},
        {"delay": {"seconds": 3}},
        {"service": "telegram_bot.send_photo",
         "data": {"chat_id": CHAT, "file": RADAR_FILE, "caption": caption}},
    ]


def cooldown(aid, secs=7200):
    return {
        "condition": "template",
        "value_template": (
            "{{ state_attr('automation.%s','last_triggered') is none "
            "or (now() - state_attr('automation.%s','last_triggered')).total_seconds() > %d }}"
            % (aid, aid, secs)
        ),
    }


def has_rain_level():
    """Only show the intensity line when the 30 km ring actually has echoes."""
    return ("{% if states('sensor.radar_rain_near')|float(0) > 0 %}"
            " · {{ states('sensor.radar_rain_class') }}{% endif %}")


# sensor.tmd_* fall back to the sentinel below when the TMD fetch failed, and to
# the literal string "None" if a template ever loses its default() guard. All
# three have to be filtered, or the caption ships the sentinel to the group.
def tmd_pct(label, entity):
    """A TMD percentage, or a readable stand-in when TMD could not be read."""
    return ("%s {%% if states('%s')|int(-1) >= 0 %%}{{ states('%s') }}%%"
            "{%% else %%}ไม่มีข้อมูล{%% endif %%}\n" % (label, entity, entity))


def tmd_text(label, entity, empty):
    """A TMD text field, hidden rather than printed when it is missing."""
    return ("{%% if states('%s') not in ['%s', '-1', 'None', 'unknown', "
            "'unavailable', ''] %%}%s {{ states('%s') }}\n{%% endif %%}"
            % (entity, empty, label, entity))


SUMMARY_CAPTION = (
    "🌦️ สรุปอากาศบ้าน (บึงกุ่ม)\n"
    "{{ now().strftime('%d/%m/%Y %H:%M') }}\n"
    "\n"
    "PM2.5 บ้าน: {{ states('sensor.pm25_home') }} µg/m³ ({{ states('sensor.pm25_level') }})\n"
    "PM2.5 สถานี PCD: {{ states('sensor.pm25_pcd') }} µg/m³\n"
    "ฝน 24 ชม. (สถานีบ้าน): {{ states('sensor.rain24_home') }} mm\n"
    "เรดาร์ 30 กม.: {{ 'ฝนตกที่บ้าน' if is_state('binary_sensor.radar_rain_now','on') "
    "else ('ฝนกำลังจะมา' if is_state('binary_sensor.radar_rain_approaching','on') else 'ยังไม่มีฝน') }}"
    " ({{ states('sensor.radar_rain_near') }}%)"
    + has_rain_level() + "\n"
    "ฝน 1 ชม.ข้างหน้า: {{ states('sensor.rain_next_60min_mm') }} mm"
    "{% if is_state('binary_sensor.radar_rain_approaching','on') and states('sensor.rain_soon_in_min')|int(0) > 0 %}"
    " · ในอีก {{ states('sensor.rain_soon_in_min') }} นาที{% endif %}\n"
    "{% if is_state('binary_sensor.flash_flood_watch','on') %}⚠️ พื้นที่เฝ้าระวังน้ำท่วมฉับพลัน (HII 24 ชม.)\n{% endif %}"
    + tmd_pct("พยากรณ์ กรมฝนหลวง 24 ชม.: ฝน", "sensor.tmd_rain_24h_pct")
    + tmd_pct("พยากรณ์ กรมฝนหลวง 7 วัน: ฝน", "sensor.tmd_rain_7d_pct")
    + tmd_text("", "sensor.tmd_forecast_7d", "ไม่มีข้อมูล")
    + "{% if states('sensor.tmd_warning') not in ['ไม่มีเตือน','ไม่มีข้อมูล','-1','None','unknown','unavailable',''] %}"
    "⚠️ ประกาศเตือน กรมฝนหลวง: {{ states('sensor.tmd_warning') }}\n{% endif %}"
)

APPROACH_CAPTION = (
    "🌧️ ฝนกำลังจะมาถึงบ้าน\n"
    "{{ now().strftime('%d/%m/%Y %H:%M') }}\n"
    "\n"
    "{% if states('sensor.rain_soon_in_min')|int(0) > 0 %}"
    "คาดว่าฝนจะมาถึงใน ~{{ states('sensor.rain_soon_in_min')|int }} นาที"
    "{% else %}กำลังเข้าใกล้บ้าน (ยังไม่มี nowcast ที่แม่นยำ){% endif %}\n"
    "ฝน 1 ชม.ข้างหน้า: {{ states('sensor.rain_next_60min_mm') }} mm\n"
    "เรดาร์ 30 กม.: {{ states('sensor.radar_rain_near') }}%" + has_rain_level() + "\n"
    + tmd_pct("พยากรณ์ กรมฝนหลวง 24 ชม.: ฝน", "sensor.tmd_rain_24h_pct").rstrip("\n")
    + " ของพื้นที่\n"
    "PM2.5 บ้าน: {{ states('sensor.pm25_home') }} µg/m³"
)

RAIN_NOW_CAPTION = (
    "☔ ฝนตกที่บ้านตอนนี้\n"
    "{{ now().strftime('%d/%m/%Y %H:%M') }}\n"
    "\n"
    "ฝน 1 ชม.ข้างหน้า: {{ states('sensor.rain_next_60min_mm') }} mm\n"
    "เรดาร์ 30 กม.: {{ states('sensor.radar_rain_near') }}%" + has_rain_level() + "\n"
    "ฝน 24 ชม. (สถานีบ้าน): {{ states('sensor.rain24_home') }} mm"
)

PM25_CAPTION = (
    "⚠️ PM2.5 บ้านสูง\n{{ now().strftime('%d/%m/%Y %H:%M') }}\n\n"
    "PM2.5 บ้าน: {{ states('sensor.pm25_home') }} µg/m³ ({{ states('sensor.pm25_level') }})\n"
    "เกณฑ์: {{ states('input_number.pm25_alert_threshold')|int }} µg/m³\n"
    "PM2.5 สถานี PCD: {{ states('sensor.pm25_pcd') }} µg/m³"
)

RAIN24_CAPTION = (
    "🌧️ ฝน 24 ชม. สูง\n{{ now().strftime('%d/%m/%Y %H:%M') }}\n\n"
    "ฝน 24 ชม. (สถานีบ้าน): {{ states('sensor.rain24_home') }} mm\n"
    "เกณฑ์: {{ states('input_number.rain_alert_threshold')|int }} mm"
)

AUTOMATIONS = [
    {"id": "weather_radar_refresh",
     "alias": "รีเฟรชเรดาร์เป็นระยะ",
     "description": "สร้างภาพเรดาร์ + อัปเดตสถานะฝน ทุก 15 นาที",
     "trigger": [{"platform": "time_pattern", "minutes": "/15"}],
     "condition": [ENABLED],
     "action": [{"service": "shell_command.weather_radar_build"}],
     "mode": "single"},

    {"id": "weather_summary_morning",
     "alias": "สรุปอากาศเช้า (เรดาร์+PM2.5+ฝน+กรมฝนหลวง)",
     "trigger": [{"platform": "time", "at": "06:00:00"}],
     "condition": [ENABLED],
     "action": send_photo(SUMMARY_CAPTION), "mode": "single"},

    {"id": "weather_summary_evening",
     "alias": "สรุปอากาศเย็น (เรดาร์+PM2.5+ฝน+กรมฝนหลวง)",
     "trigger": [{"platform": "time", "at": "17:00:00"}],
     "condition": [ENABLED],
     "action": send_photo(SUMMARY_CAPTION), "mode": "single"},

    {"id": "weather_pm25_alert",
     "alias": "แจ้งเตือน PM2.5 บ้าน สูง",
     "trigger": [{"platform": "state", "entity_id": "sensor.pm25_home"}],
     "condition": [
         ENABLED,
         {"condition": "template",
          "value_template": "{{ states('sensor.pm25_home')|float(0) > states('input_number.pm25_alert_threshold')|float(0) }}"},
         cooldown("weather_pm25_alert")],
     "action": send_photo(PM25_CAPTION), "mode": "single", "max_exceeded": "silent"},

    {"id": "weather_rain_alert",
     "alias": "แจ้งเตือนฝน 24 ชม. สูง",
     "trigger": [{"platform": "state", "entity_id": "sensor.rain24_home"}],
     "condition": [
         ENABLED,
         {"condition": "template",
          "value_template": "{{ states('sensor.rain24_home')|float(0) > states('input_number.rain_alert_threshold')|float(0) }}"},
         cooldown("weather_rain_alert")],
     "action": send_photo(RAIN24_CAPTION), "mode": "single", "max_exceeded": "silent"},

    {"id": "weather_rain_approaching",
     "alias": "แจ้งเตือน ฝนกำลังจะมาถึงบ้าน",
     "trigger": [{"platform": "state", "entity_id": "binary_sensor.radar_rain_approaching",
                  "from": "off", "to": "on"}],
     "condition": [ENABLED, cooldown("weather_rain_approaching", 3600)],
     "action": send_photo(APPROACH_CAPTION), "mode": "single", "max_exceeded": "silent"},

    {"id": "weather_rain_now",
     "alias": "แจ้งเตือน ฝนตกที่บ้าน",
     "trigger": [{"platform": "state", "entity_id": "binary_sensor.radar_rain_now",
                  "from": "off", "to": "on"}],
     "condition": [ENABLED, cooldown("weather_rain_now", 3600)],
     "action": send_photo(RAIN_NOW_CAPTION), "mode": "single", "max_exceeded": "silent"},
]

if __name__ == "__main__":
    for a in AUTOMATIONS:
        aid = a["id"]
        req = urllib.request.Request(f"{BASE}/api/config/automation/config/{aid}",
            data=json.dumps(a, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                print(f"{aid}: {r.status} {r.read().decode(errors='replace')[:80]}")
        except urllib.error.HTTPError as e:
            print(f"{aid}: {e.code} {e.read().decode(errors='replace')[:200]}")