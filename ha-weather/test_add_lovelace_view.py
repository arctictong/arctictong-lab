"""Tests for add_lovelace_view.py. Run with: python test_add_lovelace_view.py

The dashboard is config, not code, so most of it can only be checked
structurally - but the status card is a Jinja template that Home Assistant
renders live, and a typo there is a broken card on the wall. Those are rendered
here with a real Jinja engine, per state, the same way the caption tests work.

Offline: nothing here touches Home Assistant or the network.
"""
import datetime
import json
import os
import sys
import unittest

import jinja2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import add_lovelace_view as lv

ENV = jinja2.Environment(undefined=jinja2.StrictUndefined)

LIVE = {
    "sensor.rain_next_60min_mm": "0.2",
    "sensor.pm25_home": "13.0",
    "sensor.pm25_level": "ดี",
    "sensor.pm25_source": "แบบจำลอง CAMS",
    "sensor.pm25_pcd": "12.0",
    "sensor.rain_at_home": "ไม่มีฝน",
    "sensor.radar_rain_near": "54.26",
    "sensor.rain24_home": "6.4",
    "sensor.rain_soon_in_min": "-1",
    "sensor.rain_models_agree": "3",
    "sensor.rain_models_total": "3",
    "sensor.rain_chance": "89",
    "sensor.radar_zoom": "7",
    "binary_sensor.flash_flood_watch": "off",
    "binary_sensor.radar_rain_now": "off",
    "binary_sensor.radar_echo_near": "off",
    "binary_sensor.radar_rain_approaching": "off",
}


def render(tpl, states=None):
    s = dict(LIVE)
    s.update(states or {})
    return ENV.from_string(tpl).render(
        states=lambda e, s=s: s.get(e, "unavailable"),
        is_state=lambda e, v, s=s: s.get(e) == v,
        now=lambda: datetime.datetime(2026, 10, 6, 9, 47),
        state_attr=lambda *a: None, float=float, int=int)


class TestViewStructure(unittest.TestCase):
    """A sections view reads `sections`. The previous version wrote `cards`,
    which that view type ignores - so the view it added was empty."""

    def test_the_main_view_is_a_sections_view_with_sections(self):
        self.assertEqual(lv.VIEW["type"], "sections")
        self.assertIn("sections", lv.VIEW)
        self.assertNotIn("cards", lv.VIEW,
                         "a sections view reads `sections`, not `cards`")

    def test_every_section_is_a_grid_with_cards(self):
        for i, sec in enumerate(lv.VIEW["sections"]):
            with self.subTest(section=i):
                self.assertEqual(sec["type"], "grid")
                self.assertTrue(sec.get("cards"))

    def test_the_radar_is_the_first_thing(self):
        first = lv.VIEW["sections"][0]["cards"][0]
        self.assertEqual(first["type"], "picture")
        self.assertIn("/local/radar/latest.png", first["image"])

    def test_two_columns_so_it_pairs_on_desktop_and_stacks_on_mobile(self):
        self.assertEqual(lv.VIEW["max_columns"], 2)

    def test_the_status_card_leads_the_second_column(self):
        second = lv.VIEW["sections"][1]["cards"]
        self.assertEqual(second[0]["type"], "markdown")

    def test_only_two_numbers_are_shown_as_metrics(self):
        metrics = [c for c in lv.VIEW["sections"][1]["cards"]
                   if c["type"] == "glance"]
        self.assertEqual(len(metrics), 1)
        ents = [e["entity"] for e in metrics[0]["entities"]]
        self.assertEqual(ents, ["sensor.rain_next_60min_mm", "sensor.pm25_home"])

    def test_the_technical_field_is_not_on_the_main_view(self):
        blob = json.dumps(lv.VIEW)
        self.assertNotIn("radar_zoom", blob,
                         "developer detail belongs on the tech subview")


class TestTechSubview(unittest.TestCase):
    def test_it_is_a_subview_so_it_stays_off_the_tab_bar(self):
        self.assertTrue(lv.VIEW_TECH.get("subview"))

    def test_it_carries_the_technical_field(self):
        self.assertIn("radar_zoom", json.dumps(lv.VIEW_TECH))

    def test_the_main_view_links_to_it(self):
        cards = json.dumps(lv.VIEW["sections"][1]["cards"])
        self.assertIn(lv.TECH_ID, cards)

    def test_it_does_not_collide_with_the_main_path(self):
        self.assertNotEqual(lv.VIEW["path"], lv.VIEW_TECH["path"])


class TestStatusMarkdown(unittest.TestCase):
    """The live Jinja. Each state must read correctly and none may print a
    sentinel."""

    def test_the_default_state_says_dry(self):
        out = render(lv.status_markdown())
        self.assertIn("ยังไม่มีฝนที่บ้าน", out)

    def test_a_nearby_echo_says_coming_not_here(self):
        out = render(lv.status_markdown(),
                     {"binary_sensor.radar_echo_near": "on"})
        self.assertIn("ตรวจพบกลุ่มฝนใกล้บ้าน", out)
        self.assertIn('alert-type="warning"', out)
        self.assertNotIn("ฝนตกที่บ้านตอนนี้", out)

    def test_rain_over_the_house_says_now(self):
        out = render(lv.status_markdown(),
                     {"binary_sensor.radar_rain_now": "on",
                      "sensor.rain_at_home": "ฝนปานกลาง"})
        self.assertIn("ฝนตกที่บ้านตอนนี้", out)
        self.assertIn("ฝนปานกลาง", out)
        self.assertIn('alert-type="info"', out)

    def test_a_flood_watch_outranks_everything(self):
        out = render(lv.status_markdown(),
                     {"binary_sensor.flash_flood_watch": "on",
                      "binary_sensor.radar_rain_now": "on"})
        self.assertIn("เฝ้าระวังน้ำท่วมฉับพลัน", out)
        self.assertIn('alert-type="error"', out)

    def test_model_only_says_so(self):
        out = render(lv.status_markdown(),
                     {"binary_sensor.radar_rain_approaching": "on"})
        self.assertIn("แบบจำลองคาดว่าฝนจะมา", out)

    def test_a_missing_summary_says_loading_not_dry(self):
        """A fresh install has no summary yet; claiming "no rain" would be a
        fabricated answer."""
        out = render(lv.status_markdown(),
                     {"sensor.rain_next_60min_mm": "unknown"})
        self.assertIn("กำลังโหลด", out)
        self.assertNotIn("ยังไม่มีฝนที่บ้าน", out)

    def test_the_no_estimate_sentinel_is_never_printed(self):
        """-1 means "no estimate"; it must not appear on the wall."""
        for state in ([], [{"binary_sensor.radar_echo_near": "on"}],
                      [{"binary_sensor.radar_rain_now": "on"}],
                      [{"binary_sensor.radar_rain_approaching": "on"}],
                      [{"binary_sensor.flash_flood_watch": "on"}]):
            with self.subTest(state=state):
                out = render(lv.status_markdown(), state[0] if state else None)
                self.assertNotIn("-1", out)

    def test_a_real_wait_time_is_shown(self):
        out = render(lv.status_markdown(),
                     {"binary_sensor.radar_rain_now": "on",
                      "sensor.rain_soon_in_min": "30"})
        self.assertIn("คาดว่าต่ออีก ~30 นาที", out)

    def test_the_consensus_line_appears_when_there_is_one(self):
        out = render(lv.status_markdown())
        self.assertIn("โมเดล 3/3", out)
        self.assertIn("โอกาส 89%", out)

    def test_the_consensus_line_is_hidden_when_there_is_none(self):
        out = render(lv.status_markdown(),
                     {"sensor.rain_models_agree": "-1",
                      "sensor.rain_chance": "-1"})
        self.assertNotIn("โมเดล", out)

    def test_no_sentinel_survives_any_state(self):
        combos = [
            {},
            {"binary_sensor.radar_echo_near": "on"},
            {"binary_sensor.radar_rain_approaching": "on"},
            {"binary_sensor.radar_rain_now": "on"},
            {"binary_sensor.flash_flood_watch": "on"},
            {"sensor.rain_at_home": "unavailable"},
            {"sensor.radar_rain_near": "unknown"},
            {"sensor.rain_models_agree": "-1"},
        ]
        for c in combos:
            with self.subTest(state=c):
                out = render(lv.status_markdown(), c)
                for bad in ("None", "unavailable", "unknown", "-1"):
                    self.assertNotIn(bad, out,
                                     "%r leaked into: %r" % (bad, out))


class TestMergeViews(unittest.TestCase):
    """Only the views this script owns may change."""

    def test_a_missing_view_is_appended(self):
        out = lv.merge_views([{"path": "other", "title": "ของเดิม"}], lv.WANTED)
        self.assertEqual([v["path"] for v in out],
                         ["other", lv.VIEW_ID, lv.TECH_ID])

    def test_an_owned_view_is_replaced_in_place(self):
        existing = [{"path": "a"}, {"path": lv.VIEW_ID, "title": "เก่า"},
                    {"path": "b"}]
        out = lv.merge_views(existing, lv.WANTED)
        self.assertEqual(out[0], {"path": "a"})
        self.assertEqual(out[2], {"path": "b"})
        self.assertEqual(out[1]["title"], lv.VIEW["title"])
        self.assertEqual(len(out), 4)

    def test_a_foreign_view_is_never_touched(self):
        existing = [{"path": "mine", "title": "ห้ามแตะ", "cards": [1, 2, 3]}]
        out = lv.merge_views(existing, lv.WANTED)
        self.assertEqual(out[0], existing[0])

    def test_the_grid_position_survives_a_replacement(self):
        """view_layout is how HA records where the tab sits; dropping it moves
        the view in the dashboard."""
        existing = [{"path": lv.VIEW_ID,
                     "view_layout": {"grid_area": "somewhere"}}]
        out = lv.merge_views(existing, lv.WANTED)
        self.assertEqual(out[0]["view_layout"], {"grid_area": "somewhere"})

    def test_running_twice_changes_nothing_the_second_time(self):
        once = lv.merge_views([], lv.WANTED)
        twice = lv.merge_views(once, lv.WANTED)
        self.assertEqual(once, twice)

    def test_an_existing_view_object_is_not_mutated(self):
        wanted = [{"path": "x", "title": "a"}]
        holder = [{"path": "x", "title": "old", "view_layout": {"g": 1}}]
        lv.merge_views(holder, wanted)
        self.assertEqual(holder[0]["title"], "old")


if __name__ == "__main__":
    unittest.main(verbosity=2)
