"""Tests for the Telegram captions. Run with: python test_captions.py

A caption is a Jinja template that Home Assistant renders at send time, so a
typo is not caught by importing the module - it surfaces as a broken message to
the group. These tests render every caption through a real Jinja2 engine with
stubbed HA helpers, and separately assert that the sentinel values the sensors
fall back to (see the convention block in weather_radar.yaml) never reach the
message.

Offline: nothing here touches the network or Home Assistant.
"""
import datetime
import os
import sys
import unittest

import jinja2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_automations as ma

# Home Assistant exposes states() and is_state() as globals; undefined as
# StrictUndefined so a typo in an entity_id fails the test instead of rendering
# an empty string, which is what HA itself does with a bad entity reference.
ENV = jinja2.Environment(undefined=jinja2.StrictUndefined)

LIVE = {
    "sensor.pm25_home": "42",
    "sensor.pm25_level": "ปานกลาง",
    "sensor.pm25_pcd": "38",
    "sensor.rain24_home": "3.2",
    "binary_sensor.radar_rain_now": "off",
    "binary_sensor.radar_rain_approaching": "off",
    "binary_sensor.radar_echo_near": "off",
    "sensor.radar_rain_near": "40.75",
    "sensor.radar_rain_class": "ฝนปานกลาง",
    "sensor.rain_at_home": "ไม่มีฝน",
    "sensor.rain_next_60min_mm": "0",
    "sensor.rain_past_60min_mm": "0",
    "sensor.rain_soon_in_min": "-1",
    "binary_sensor.flash_flood_watch": "off",
    "sensor.tmd_rain_24h_pct": "80",
    "sensor.tmd_rain_7d_pct": "60",
    "sensor.tmd_forecast_7d": "ฝนกระจาย",
    "sensor.tmd_warning": "ไม่มีเตือน",
    "sensor.pm25_trend": "0",
    "sensor.rain_now_mm": "0",
    "input_number.pm25_alert_threshold": "50",
    "input_number.rain_alert_threshold": "10",
}

# Everything the sensor layer can hand a caption when a source is broken.
SENTINELS = ["-1", "None", "unknown", "unavailable", "ไม่มีข้อมูล", ""]
# The literal string HA's command_line platform produces for a missing key. This
# is the bug the README previously described wrongly: a TMD outage put "None"
# into the dashboard and would have put it into the group message.
LITERAL_NONE = "None"


def render(template, states):
    """Render a caption the way HA's Jinja environment would."""
    def _states(entity):
        if entity not in states:
            raise jinja2.UndefinedError("no such entity: %s" % entity)
        return states[entity]

    def _is_state(entity, value):
        return states.get(entity) == value

    return ENV.from_string(template).render(
        states=_states,
        is_state=_is_state,
        now=lambda: datetime.datetime(2026, 10, 5, 20, 15),
        state_attr=lambda *a: None,
        float=float,
        int=int,
    )


class TestCaptionsRender(unittest.TestCase):
    def test_every_caption_parses(self):
        """A syntax error here becomes a broken message to the group."""
        for name in ("SUMMARY_CAPTION", "APPROACH_CAPTION",
                     "RAIN_NOW_CAPTION", "PM25_CAPTION"):
            with self.subTest(caption=name):
                tpl = getattr(ma, name)
                self.assertTrue(tpl.strip(), "%s is empty" % name)
                render(tpl, dict(LIVE))

    def test_captions_reference_only_known_entities(self):
        """Catches a renamed sensor: StrictUndefined raises on a missing id."""
        for name in ("SUMMARY_CAPTION", "APPROACH_CAPTION",
                     "RAIN_NOW_CAPTION", "PM25_CAPTION"):
            with self.subTest(caption=name):
                render(getattr(ma, name), dict(LIVE))

    def test_summary_caption_has_the_expected_sections(self):
        out = render(ma.SUMMARY_CAPTION, dict(LIVE))
        for needle in ("PM2.5 บ้าน", "PM2.5 สถานี PCD", "ฝน 24 ชม.",
                       "เรดาร์ 30 กม.", "ฝน 1 ชม.ข้างหน้า",
                       "กรมฝนหลวง 24 ชม.", "กรมฝนหลวง 7 วัน"):
            self.assertIn(needle, out, "summary caption lost %r" % needle)

    def test_rain_now_caption_says_it_is_raining(self):
        states = dict(LIVE)
        states["binary_sensor.radar_rain_now"] = "on"
        out = render(ma.SUMMARY_CAPTION, states)
        self.assertIn("ฝนตกที่บ้าน", out)
        self.assertNotIn("ยังไม่มีฝน", out)

    def test_approaching_caption_when_the_sensor_is_on(self):
        states = dict(LIVE)
        states["binary_sensor.radar_rain_approaching"] = "on"
        out = render(ma.SUMMARY_CAPTION, states)
        self.assertIn("ฝนกำลังจะมา", out)

    def test_flash_flood_watch_appears_only_when_on(self):
        self.assertNotIn("น้ำท่วมฉับพลัน",
                         render(ma.SUMMARY_CAPTION, dict(LIVE)))
        states = dict(LIVE)
        states["binary_sensor.flash_flood_watch"] = "on"
        self.assertIn("น้ำท่วมฉับพลัน",
                      render(ma.SUMMARY_CAPTION, states))

    def test_intensity_line_only_when_there_is_echo(self):
        states = dict(LIVE)
        states["sensor.radar_rain_near"] = "0"
        self.assertNotIn("ฝนปานกลาง", render(ma.SUMMARY_CAPTION, states))
        states["sensor.radar_rain_near"] = "12.5"
        self.assertIn("ฝนปานกลาง", render(ma.SUMMARY_CAPTION, states))

    def test_warning_is_announced_when_present(self):
        states = dict(LIVE)
        states["sensor.tmd_warning"] = "ประกาศเตือนฝนหนัก"
        self.assertIn("ประกาศเตือนฝนหนัก",
                      render(ma.SUMMARY_CAPTION, states))


class TestSentinelsNeverReachTheMessage(unittest.TestCase):
    """The point of this file. When TMD or the radar index fails, summary.json
    holds JSON null, HA's command_line sensor renders a missing key as the
    literal string "None", and that string is what a caption would send."""

    def _all_tmd_broken(self):
        states = dict(LIVE)
        for key in states:
            if "tmd" in key:
                states[key] = "-1"
        states["sensor.tmd_forecast_7d"] = "ไม่มีข้อมูล"
        states["sensor.tmd_warning"] = "ไม่มีเตือน"
        return states

    def test_summary_caption_with_every_tmd_source_down(self):
        out = render(ma.SUMMARY_CAPTION, self._all_tmd_broken())
        for bad in ("-1", LITERAL_NONE, "unknown", "unavailable"):
            self.assertNotIn(bad, out,
                             "sentinel %r reached the caption: %r" % (bad, out))
        self.assertIn("ไม่มีข้อมูล", out,
                      "a missing TMD reading must be stated, not dropped")

    def test_summary_caption_if_a_yaml_guard_was_removed(self):
        """Defence in depth: the caption must survive even a template that lost
        its default(), which is what actually happened before this fix."""
        states = dict(LIVE)
        states["sensor.tmd_forecast_7d"] = LITERAL_NONE
        states["sensor.tmd_warning"] = LITERAL_NONE
        out = render(ma.SUMMARY_CAPTION, states)
        for bad in (LITERAL_NONE, "-1"):
            self.assertNotIn(bad, out,
                             "sentinel %r reached the caption: %r" % (bad, out))

    def test_approach_caption_with_every_tmd_source_down(self):
        out = render(ma.APPROACH_CAPTION, self._all_tmd_broken())
        for bad in ("-1", LITERAL_NONE, "unknown", "unavailable"):
            self.assertNotIn(bad, out,
                             "sentinel %r reached the caption: %r" % (bad, out))

    def test_pct_helper_substitutes_for_every_sentinel(self):
        for value in SENTINELS:
            with self.subTest(state=value):
                out = render(ma.tmd_pct("ฝน", "sensor.tmd_rain_24h_pct"),
                             {"sensor.tmd_rain_24h_pct": value})
                self.assertIn("ไม่มีข้อมูล", out)
                for bad in ("-1", LITERAL_NONE):
                    self.assertNotIn(bad, out)

    def test_pct_helper_shows_a_real_reading(self):
        out = render(ma.tmd_pct("ฝน", "sensor.tmd_rain_24h_pct"),
                     {"sensor.tmd_rain_24h_pct": "80"})
        self.assertEqual(out.strip(), "ฝน 80%")

    def test_pct_helper_treats_zero_as_a_reading(self):
        """0% is a real forecast - a dry spell - not a failure."""
        out = render(ma.tmd_pct("ฝน", "sensor.tmd_rain_24h_pct"),
                     {"sensor.tmd_rain_24h_pct": "0"})
        self.assertEqual(out.strip(), "ฝน 0%")

    def test_text_helper_hides_every_sentinel(self):
        for value in SENTINELS:
            with self.subTest(state=value):
                out = render(ma.tmd_text("", "sensor.tmd_forecast_7d"),
                             {"sensor.tmd_forecast_7d": value})
                self.assertEqual(out.strip(), "",
                                 "sentinel %r was printed" % value)

    def test_text_helper_shows_a_real_reading(self):
        out = render(ma.tmd_text("", "sensor.tmd_forecast_7d"),
                     {"sensor.tmd_forecast_7d": "ฝนกระจาย"})
        self.assertIn("ฝนกระจาย", out)


class TestNonTmdSensorsAreFiltered(unittest.TestCase):
    """The sentinel work started with TMD, but PM2.5, ThaiWater and Open-Meteo
    fail the same way: the sensor holds 'unknown' or the literal string "None"
    and the caption used to interpolate it raw. This is the gap the README
    wrongly claimed test_captions.py already covered."""

    # the non-TMD sensors every caption reads, and where they surface
    WATCHED = ["sensor.pm25_home", "sensor.pm25_pcd", "sensor.rain24_home",
               "sensor.rain_next_60min_mm", "sensor.rain_past_60min_mm"]

    def test_no_sentinel_reaches_the_summary_caption(self):
        for entity in self.WATCHED:
            for value in SENTINELS:
                with self.subTest(entity=entity, state=value):
                    states = dict(LIVE)
                    states[entity] = value
                    out = render(ma.SUMMARY_CAPTION, states)
                    for bad in ("-1", LITERAL_NONE, "unknown", "unavailable"):
                        self.assertNotIn(
                            bad, out,
                            "%s=%r leaked %r into: %r"
                            % (entity, value, bad, out))

    def test_a_real_reading_still_shows(self):
        states = dict(LIVE)
        states["sensor.pm25_pcd"] = "38"
        out = render(ma.SUMMARY_CAPTION, states)
        self.assertIn("38 µg/m³", out)

    def test_the_mm_sentinel_reads_as_missing_not_as_minus_one(self):
        states = dict(LIVE)
        states["sensor.rain_next_60min_mm"] = "-1"
        out = render(ma.SUMMARY_CAPTION, states)
        self.assertNotIn("-1 mm", out)
        self.assertIn("ไม่มีข้อมูล", out)

    def test_zero_mm_is_a_real_reading(self):
        states = dict(LIVE)
        states["sensor.rain_next_60min_mm"] = "0"
        out = render(ma.SUMMARY_CAPTION, states)
        self.assertIn("0 mm", out)

    def test_the_alert_captions_are_filtered_too(self):
        for tpl in (ma.PM25_CAPTION, ma.RAIN24_CAPTION, ma.RAIN_NOW_CAPTION,
                    ma.APPROACH_CAPTION):
            for entity in self.WATCHED:
                with self.subTest(entity=entity, caption=tpl[:24]):
                    states = dict(LIVE)
                    states[entity] = LITERAL_NONE
                    out = render(tpl, states)
                    for bad in (LITERAL_NONE, "unknown", "unavailable"):
                        self.assertNotIn(bad, out)

    def test_every_caption_filter_list_is_the_one_shared_list(self):
        """The sentinel set used to be copied in four places and had already
        drifted apart, so one caption could filter a value another passed."""
        self.assertIn("unknown", ma.MISSING)
        self.assertIn("unavailable", ma.MISSING)
        self.assertIn("none", [m.lower() for m in ma.MISSING])
        # no hand-maintained copy of the list may survive in a caption literal
        for attr in ("SUMMARY_CAPTION", "APPROACH_CAPTION", "RAIN_NOW_CAPTION",
                     "PM25_CAPTION", "RAIN24_CAPTION"):
            with self.subTest(caption=attr):
                self.assertNotIn("'None', 'unknown'", getattr(ma, attr))

    def test_zero_minutes_is_not_shown_as_a_wait_time(self):
        """rain_soon_in_min uses -1 for 'unknown'; 0 must not render as
        'in 0 minutes'."""
        states = dict(LIVE)
        states["binary_sensor.radar_rain_approaching"] = "on"
        states["sensor.rain_soon_in_min"] = "-1"
        out = render(ma.APPROACH_CAPTION, states)
        self.assertNotIn("ในอีก -1", out)


class TestPm25Provenance(unittest.TestCase):
    """sensor.pm25_home is Open-Meteo CAMS - a model at roughly 11 km
    resolution, not a measurement - while sensor.pm25_pcd is a real station
    about 3.8 km away. The caption presented the model as plain "home", which
    overstates it and hides when the station is the better reading."""

    def test_the_model_is_labelled_as_a_model(self):
        out = render(ma.SUMMARY_CAPTION, dict(LIVE))
        self.assertIn("PM2.5 บ้าน (แบบจำลอง)", out)
        self.assertIn("PM2.5 สถานี PCD (วัดจริง)", out)

    def test_the_label_is_not_duplicated(self):
        """pm25_home_line() carries its own label, so a caller must not add a
        second one - that produced 'PM2.5 บ้าน: PM2.5 บ้าน (แบบจำลอง): 17.6'."""
        out = render(ma.SUMMARY_CAPTION, dict(LIVE))
        self.assertEqual(out.count("PM2.5 บ้าน"), 1,
                         "the PM2.5 label is repeated:\n%s" % out)
        self.assertEqual(out.count("PM2.5 สถานี PCD"), 1)

    def test_the_station_stands_in_when_the_model_is_down(self):
        states = dict(LIVE)
        states["sensor.pm25_home"] = "unavailable"
        states["sensor.pm25_pcd"] = "88"
        out = render(ma.SUMMARY_CAPTION, states)
        self.assertIn("สถานี PCD แทน", out)
        self.assertIn("88", out)
        self.assertNotIn("unavailable", out)

    def test_both_sources_down_says_missing_not_zero(self):
        states = dict(LIVE)
        states["sensor.pm25_home"] = "unavailable"
        states["sensor.pm25_pcd"] = "unknown"
        out = render(ma.SUMMARY_CAPTION, states)
        self.assertIn("PM2.5 บ้าน: ไม่มีข้อมูล", out)
        self.assertNotIn("0 µg/m³", out)

    def test_the_alert_caption_is_labelled_too(self):
        out = render(ma.PM25_CAPTION, dict(LIVE))
        self.assertIn("(แบบจำลอง)", out)
        self.assertIn("(วัดจริง)", out)

    def test_the_alert_prefers_the_model_then_the_station(self):
        cond = [a for a in ma.AUTOMATIONS if a["id"] == "weather_pm25_alert"][0]
        tpl = cond["condition"][1]["value_template"]
        thr = {"input_number.pm25_alert_threshold": "50"}

        cases = [
            ({"sensor.pm25_home": "80", "sensor.pm25_pcd": "10"}, "true",
             "model above threshold"),
            ({"sensor.pm25_home": "unavailable", "sensor.pm25_pcd": "80"}, "true",
             "model down, station above threshold"),
            ({"sensor.pm25_home": "10", "sensor.pm25_pcd": "10"}, "false",
             "both below threshold"),
            ({"sensor.pm25_home": "unavailable", "sensor.pm25_pcd": "unknown"},
             "false", "no reading at all"),
        ]
        for states, want, why in cases:
            with self.subTest(case=why):
                got = render(tpl, {**states, **thr}).strip().lower()
                self.assertEqual(got, want, "%s -> %r" % (why, got))

    def test_the_alert_triggers_on_the_station_too(self):
        """The model can go unavailable on its own; the alert must not depend
        on it staying up."""
        cond = [a for a in ma.AUTOMATIONS if a["id"] == "weather_pm25_alert"][0]
        ents = set()
        for t in cond["trigger"]:
            e = t.get("entity_id")
            if isinstance(e, list):
                ents.update(e)
            elif e:
                ents.add(e)
        self.assertIn("sensor.pm25_pcd", ents)


try:
    import yaml
except ImportError:                     # pragma: no cover
    yaml = None


@unittest.skipIf(yaml is None, "pyyaml not installed")
class TestYamlPm25Templates(unittest.TestCase):
    """The level and source sensors live in weather_radar.yaml as Jinja, which
    nothing else in this file can see. A typo there is a broken entity, so they
    are rendered here too."""

    YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "weather_radar.yaml")

    def _sensors(self):
        with open(self.YAML, encoding="utf-8") as fh:
            d = yaml.safe_load(fh.read())
        return {s["unique_id"]: s["state"] for s in d["template"][0]["sensor"]}

    def _render(self, tpl, states):
        return render(tpl, states).strip()

    def test_both_templates_parse_and_render(self):
        for uid, tpl in self._sensors().items():
            with self.subTest(sensor=uid):
                self.assertTrue(self._render(tpl, dict(LIVE)))

    def test_the_level_falls_back_to_the_station(self):
        s = self._sensors()
        self.assertEqual(
            self._render(s["pm25_home_level"],
                         {"sensor.pm25_home": "17.6", "sensor.pm25_pcd": "16"}),
            "ดี")
        # the model is down: the station must be used, not blanked
        self.assertEqual(
            self._render(s["pm25_home_level"],
                         {"sensor.pm25_home": "unavailable",
                          "sensor.pm25_pcd": "88"}),
            "มีผลต่อสุขภาพ")
        self.assertEqual(
            self._render(s["pm25_home_level"],
                         {"sensor.pm25_home": "unavailable",
                          "sensor.pm25_pcd": "unknown"}),
            "ไม่มีข้อมูล")

    def test_a_negative_reading_is_not_a_level(self):
        """A negative concentration used to parse as a number and would have
        reported a level for it."""
        s = self._sensors()
        for uid in ("pm25_home_level", "pm25_home_source"):
            with self.subTest(sensor=uid):
                self.assertEqual(
                    self._render(s[uid], {"sensor.pm25_home": "-1",
                                          "sensor.pm25_pcd": "-1"}),
                    "ไม่มีข้อมูล")

    def test_the_source_names_the_model_or_the_station(self):
        s = self._sensors()
        self.assertEqual(
            self._render(s["pm25_home_source"],
                         {"sensor.pm25_home": "17.6", "sensor.pm25_pcd": "16"}),
            "แบบจำลอง CAMS")
        self.assertIn(
            "สถานี PCD",
            self._render(s["pm25_home_source"],
                         {"sensor.pm25_home": "unavailable",
                          "sensor.pm25_pcd": "88"}))


class TestRainWordingMatchesTheEvidence(unittest.TestCase):
    """The 9:47 alert said "ฝนตกที่บ้านตอนนี้" on the strength of an echo 1.4 km
    away. The wording has to follow the aperture, not the other way round."""

    def test_near_echo_says_rain_is_coming_not_that_it_is_here(self):
        states = dict(LIVE)
        states["binary_sensor.radar_echo_near"] = "on"
        states["binary_sensor.radar_rain_now"] = "off"
        out = render(ma.APPROACH_CAPTION, states)
        self.assertIn("ตรวจพบกลุ่มฝนใกล้บ้าน", out)
        self.assertNotIn("ตกที่บ้านตอนนี้", out)

    def test_model_only_says_so_rather_than_claiming_an_echo(self):
        """approaching also fires on the model consensus with no radar echo at
        all; claiming to have seen a cell would be a lie."""
        states = dict(LIVE)
        states["binary_sensor.radar_echo_near"] = "off"
        out = render(ma.APPROACH_CAPTION, states)
        self.assertIn("แบบจำลองคาดว่าฝนจะมา", out)
        self.assertNotIn("ตรวจพบกลุ่มฝนใกล้บ้าน", out)

    def test_a_known_eta_is_stated_first(self):
        states = dict(LIVE)
        states["sensor.rain_soon_in_min"] = "30"
        states["binary_sensor.radar_echo_near"] = "on"
        out = render(ma.APPROACH_CAPTION, states)
        self.assertIn("ในอีก ~30 นาที", out)

    def test_the_now_caption_reports_the_echo_over_the_house(self):
        """It used to print the 30 km maximum, so a storm 25 km away could be
        reported as the intensity at home."""
        states = dict(LIVE)
        states["sensor.rain_at_home"] = "ฝนหนักมาก"
        states["sensor.radar_rain_class"] = "ฝนรุนแรง"      # a distant storm
        out = render(ma.RAIN_NOW_CAPTION, states)
        self.assertIn("ความแรงที่บ้าน: ฝนหนักมาก", out)
        self.assertNotIn("ฝนรุนแรง", out)

    def test_the_now_caption_never_prints_a_bare_sentinel(self):
        states = dict(LIVE)
        states["sensor.rain_at_home"] = "unavailable"
        out = render(ma.RAIN_NOW_CAPTION, states)
        self.assertNotIn("unavailable", out)


class TestPastHourLine(unittest.TestCase):
    """"Rain in the last hour" is only news when it actually rained. A dry
    hour and a failed fetch must both produce no line at all, not a zero and
    not a -1."""

    def _out(self, value):
        states = dict(LIVE)
        states["sensor.rain_past_60min_mm"] = value
        return render(ma.SUMMARY_CAPTION, states)

    def test_shown_when_rain_fell(self):
        self.assertIn("ฝน 1 ชม. ที่ผ่านมา: 1.5", self._out("1.5"))

    def test_hidden_when_the_hour_was_dry(self):
        self.assertNotIn("ที่ผ่านมา", self._out("0"))

    def test_hidden_when_the_fetch_failed(self):
        out = self._out("-1")
        self.assertNotIn("ที่ผ่านมา", out)
        self.assertNotIn("-1", out)

    def test_hidden_when_the_sensor_is_unknown(self):
        self.assertNotIn("ที่ผ่านมา", self._out("unknown"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
