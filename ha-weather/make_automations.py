import json
import os
import urllib.error
import urllib.request


def require_token():
    """The HA long-lived token, read from the environment.

    Never hardcode it here: a committed token is a live credential sitting in
    git history for anyone with repo access, and the repo's own convention
    (dashboard/README.md: "ไม่มี key ฝังใน git") forbids it. Read lazily so that
    importing this module - which the caption tests do - needs no credential.
    """
    tok = os.environ.get("HA_TOKEN") or os.environ.get("HASS_TOKEN")
    if not tok:
        raise SystemExit(
            "set HA_TOKEN in the environment before running this script "
            "(the token must not live in the repo)")
    return tok


BASE = os.environ.get("HA_BASE", "http://192.168.1.248:8123")
CHAT = "1448647267"
RADAR_FILE = "/config/www/radar/latest.png"

ENABLED = {"condition": "state", "entity_id": "input_boolean.weather_alerts_enabled", "state": "on"}

# Every state a sensor can hold when it has no usable reading. One list, so a
# new failure token is added in one place: the sentinel filters and the caption
# helpers below all read from it.
MISSING = ["unknown", "unavailable", "none", "null", "-1", "",
           "ไม่มีข้อมูล", "ไม่มีเตือน"]


def _jinja_list(items):
    return "[" + ", ".join("'%s'" % i.replace("'", "") for i in items) + "]"


MISSING_JINJA = _jinja_list(MISSING)


def value_or(entity, unit="", empty="ไม่มีข้อมูล"):
    """Jinja: the sensor's reading, or a stand-in when it has none.

    Filters on the value in one place, so no caption can print the literal
    string "None", "-1" or "unknown" at the Telegram group.
    """
    return ("{%% if states('%s')|lower not in %s %%}{{ states('%s') }}%s"
            "{%% else %%}%s{%% endif %%}"
            % (entity, MISSING_JINJA, entity, unit, empty))


def pm25_home_line():
    """Home PM2.5 with its provenance stated.

    sensor.pm25_home is Open-Meteo CAMS - a model at roughly 11 km resolution,
    not a measurement - while sensor.pm25_pcd is a real station about 3.8 km
    away. Presenting the model as plain "home" overstates it, so the source is
    named; the station stands in when the model is unavailable, rather than the
    line going blank while a usable reading exists.

    Availability is tested with float(none), which returns None when the state
    will not parse, plus a >= 0 guard: a negative concentration is not a
    reading. No dependency on is_number being spelled the right way, and the
    raw state is still what gets displayed.
    """
    return ("{% set m = states('sensor.pm25_home') %}"
            "{% set p = states('sensor.pm25_pcd') %}"
            "{% set mv = m|float(none) %}"
            "{% set pv = p|float(none) %}"
            "{% if mv is not none and mv >= 0 %}"
            "PM2.5 บ้าน (แบบจำลอง): {{ m }} µg/m³"
            "{% elif pv is not none and pv >= 0 %}"
            "PM2.5 บ้าน (สถานี PCD แทน): {{ p }} µg/m³"
            "{% else %}PM2.5 บ้าน: ไม่มีข้อมูล{% endif %}")


def pm25_alert_condition():
    """Jinja for the PM2.5 alert: the model value when available, else the
    station, compared against the threshold. An Open-Meteo outage must not
    silence an alert while a real ground reading is above the line."""
    return ("{% set m = states('sensor.pm25_home')|float(none) %}"
            "{% set p = states('sensor.pm25_pcd')|float(none) %}"
            "{{ (m if (m is not none and m >= 0) else "
            "(p if (p is not none and p >= 0) else 0)) "
            "> states('input_number.pm25_alert_threshold')|float(0) }}")


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
    """A TMD percentage, or a readable stand-in when TMD could not be read.

    Uses a numeric test rather than the shared missing-list: a percentage that
    is not a number (a text state leaking in) must be replaced too. 0 is a real
    forecast - a dry spell - so the test is >= 0, not > 0.
    """
    return ("%s {%% if states('%s')|int(-1) >= 0 %%}{{ states('%s') }}%%"
            "{%% else %%}ไม่มีข้อมูล{%% endif %%}\n" % (label, entity, entity))


def tmd_text(label, entity):
    """A TMD text field, hidden rather than printed when it is missing."""
    return ("{%% if states('%s')|lower not in %s %%}%s {{ states('%s') }}\n"
            "{%% endif %%}" % (entity, MISSING_JINJA, label, entity))


SUMMARY_CAPTION = (
    "🌦️ สรุปอากาศบ้าน (บึงกุ่ม)\n"
    "{{ now().strftime('%d/%m/%Y %H:%M') }}\n"
    "\n"
    # pm25_home_line() carries its own "PM2.5 บ้าน (...)" label
    + pm25_home_line()
    + " (" + value_or("sensor.pm25_level", empty="-") + ")\n"
    "PM2.5 สถานี PCD (วัดจริง): " + value_or("sensor.pm25_pcd", " µg/m³") + "\n"
    "ฝน 24 ชม. (สถานีบ้าน): " + value_or("sensor.rain24_home", " mm") + "\n"
    "เรดาร์ 30 กม.: {{ 'ฝนตกที่บ้าน' if is_state('binary_sensor.radar_rain_now','on') "
    "else ('ฝนกำลังจะมา' if is_state('binary_sensor.radar_rain_approaching','on') else 'ยังไม่มีฝน') }}"
    + " (" + value_or("sensor.radar_rain_near", "%", empty="-") + ")"
    + has_rain_level() + "\n"
    "ฝน 1 ชม.ข้างหน้า: " + value_or("sensor.rain_next_60min_mm", " mm")
    + "{% if is_state('binary_sensor.radar_rain_approaching','on') and states('sensor.rain_soon_in_min')|int(0) > 0 %}"
    " · ในอีก {{ states('sensor.rain_soon_in_min') }} นาที{% endif %}\n"
    "{% if is_state('binary_sensor.flash_flood_watch','on') %}⚠️ พื้นที่เฝ้าระวังน้ำท่วมฉับพลัน (HII 24 ชม.)\n{% endif %}"
    + tmd_pct("พยากรณ์ กรมฝนหลวง 24 ชม.: ฝน", "sensor.tmd_rain_24h_pct")
    + tmd_pct("พยากรณ์ กรมฝนหลวง 7 วัน: ฝน", "sensor.tmd_rain_7d_pct")
    + tmd_text("", "sensor.tmd_forecast_7d")
    + "{% if states('sensor.tmd_warning')|lower not in " + MISSING_JINJA + " %}"
    "⚠️ ประกาศเตือน กรมฝนหลวง: {{ states('sensor.tmd_warning') }}\n{% endif %}"
)

APPROACH_CAPTION = (
    "🌧️ ฝนกำลังจะมาถึงบ้าน\n"
    "{{ now().strftime('%d/%m/%Y %H:%M') }}\n"
    "\n"
    "{% if states('sensor.rain_soon_in_min')|int(0) > 0 %}"
    "คาดว่าฝนจะมาถึงใน ~{{ states('sensor.rain_soon_in_min')|int }} นาที"
    "{% else %}กำลังเข้าใกล้บ้าน (ยังไม่มี nowcast ที่แม่นยำ){% endif %}\n"
    "ฝน 1 ชม.ข้างหน้า: " + value_or("sensor.rain_next_60min_mm", " mm") + "\n"
    "เรดาร์ 30 กม.: " + value_or("sensor.radar_rain_near", "%", empty="-")
    + has_rain_level() + "\n"
    + tmd_pct("พยากรณ์ กรมฝนหลวง 24 ชม.: ฝน", "sensor.tmd_rain_24h_pct").rstrip("\n")
    + " ของพื้นที่\n"
    + pm25_home_line()
)

RAIN_NOW_CAPTION = (
    "☔ ฝนตกที่บ้านตอนนี้\n"
    "{{ now().strftime('%d/%m/%Y %H:%M') }}\n"
    "\n"
    "ฝน 1 ชม.ข้างหน้า: " + value_or("sensor.rain_next_60min_mm", " mm") + "\n"
    "เรดาร์ 30 กม.: " + value_or("sensor.radar_rain_near", "%", empty="-")
    + has_rain_level() + "\n"
    "ฝน 24 ชม. (สถานีบ้าน): " + value_or("sensor.rain24_home", " mm")
)

PM25_CAPTION = (
    "⚠️ PM2.5 บ้านสูง\n{{ now().strftime('%d/%m/%Y %H:%M') }}\n\n"
    + pm25_home_line()
    + " (" + value_or("sensor.pm25_level", empty="-") + ")\n"
    "เกณฑ์: {{ states('input_number.pm25_alert_threshold')|int }} µg/m³\n"
    "PM2.5 สถานี PCD (วัดจริง): " + value_or("sensor.pm25_pcd", " µg/m³")
)

RAIN24_CAPTION = (
    "🌧️ ฝน 24 ชม. สูง\n{{ now().strftime('%d/%m/%Y %H:%M') }}\n\n"
    "ฝน 24 ชม. (สถานีบ้าน): " + value_or("sensor.rain24_home", " mm") + "\n"
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
     # the model can go unavailable on its own, so the station also triggers:
     # an alert must not depend on one of the two sources staying up
     "trigger": [{"platform": "state", "entity_id": "sensor.pm25_home"},
                 {"platform": "state", "entity_id": "sensor.pm25_pcd"}],
     "condition": [
         ENABLED,
         {"condition": "template",
          "value_template": pm25_alert_condition()},
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
    token = require_token()
    for a in AUTOMATIONS:
        aid = a["id"]
        req = urllib.request.Request(f"{BASE}/api/config/automation/config/{aid}",
            data=json.dumps(a, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                print(f"{aid}: {r.status} {r.read().decode(errors='replace')[:80]}")
        except urllib.error.HTTPError as e:
            print(f"{aid}: {e.code} {e.read().decode(errors='replace')[:200]}")