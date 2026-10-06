"""Tests for radar_notify.py. Run with: python test_radar_notify.py

Everything here is offline: no RainViewer, no Open-Meteo, no TMD. The geometry
and palette facts pinned down here were established against the live tiles with
throwaway probe scripts; these are the regression tests, so a later edit cannot
quietly change the map's scale, the crop, or which pixels count as rain.

What this cannot check is whether RainViewer changes its palette.
test_ramp_still_looks_like_the_palette() at least fails loudly if it does,
instead of silently reporting every pixel as rain.
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
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import radar_notify as rn
from PIL import Image

HOME_LAT, HOME_LON = rn.HOME_LAT, rn.HOME_LON


class TestGeometry(unittest.TestCase):
    """The map's scale bars, rings and labels all come from these three
    functions. If they drift, the image stops telling the truth."""

    def test_meters_per_pixel_matches_web_mercator(self):
        for z in (7, 10, 11):
            for tpx in (256, 1024, 4096):
                expected = (156543.03392 * math.cos(math.radians(HOME_LAT))
                            / (2.0 ** z) * (256.0 / tpx))
                self.assertAlmostEqual(
                    rn.meters_per_pixel(HOME_LAT, z, tpx), expected, places=6,
                    msg="mpp mismatch at z%d tpx%d" % (z, tpx))

    def test_mpp_halves_per_zoom_level(self):
        for z in range(1, 12):
            self.assertAlmostEqual(
                rn.meters_per_pixel(0.0, z, 256),
                rn.meters_per_pixel(0.0, z + 1, 256) * 2.0, places=6)

    def test_bigger_tiles_are_finer_at_the_same_zoom(self):
        coarse = rn.meters_per_pixel(HOME_LAT, 7, 256)
        for tpx in (512, 1024, 2048, 4096):
            fine = rn.meters_per_pixel(HOME_LAT, 7, tpx)
            self.assertAlmostEqual(fine * (tpx // 256), coarse, places=6)
            self.assertLess(fine, coarse)

    def test_zoom_for_mpp_round_trips(self):
        """The base map must be at least as detailed as the radar layer,
        otherwise the two do not register and the rings lie."""
        for tpx in (256, 1024, 4096):
            mpp = rn.meters_per_pixel(HOME_LAT, 7, tpx)
            z = rn.zoom_for_mpp(HOME_LAT, mpp)
            base_mpp = rn.meters_per_pixel(HOME_LAT, z, rn.TILE)
            self.assertLessEqual(base_mpp, mpp * 1.05,
                                 "base map coarser than radar at tpx%d" % tpx)

    def test_home_is_inside_the_world_square(self):
        for z in (7, 11):
            x, y = rn.latlon_to_world_px(HOME_LAT, HOME_LON, z)
            n = 2.0 ** z * rn.TILE
            self.assertTrue(0 <= x <= n, "x out of range at z%d" % z)
            self.assertTrue(0 <= y <= n, "y out of range at z%d" % z)

    def test_bangkok_tile_reference(self):
        """A known-good tile coordinate. Without this a sign slip in the
        mercator projection hides behind an image that still looks plausible."""
        x, y = rn.latlon_to_world_px(HOME_LAT, HOME_LON, 7, 1024)
        self.assertEqual(int(x // 1024), 99)
        self.assertEqual(int(y // 1024), 59)

    def test_deployed_crop_shows_the_alert_radius(self):
        """At the shipped settings the 30 km ring must be drawn inside the
        frame. The draw code silently skips a ring that will not fit, so this
        is the only thing standing between a bad RADAR_VIEW_KM and a map that
        quietly stops showing what the sensors measure."""
        mpp = rn.meters_per_pixel(HOME_LAT, 7, 4096)
        if rn.VIEW_KM:
            size = max(360, min(2048, int(round(rn.VIEW_KM * 1000.0 / mpp))))
        else:
            size = rn.SIZE
        self.assertGreater(size / 2.0, rn.RADIUS_KM * 1000.0 / mpp,
                           "30 km ring falls outside the frame")
        self.assertGreater(size * mpp / 1000.0, rn.RADIUS_KM * 2,
                           "frame is smaller than twice the alert radius")

    def test_view_km_never_upsamples_silently(self):
        """VIEW_KM is allowed to magnify, but the clamp must not pretend a tiny
        value was honoured: below the floor the real ground width changes."""
        mpp = rn.meters_per_pixel(HOME_LAT, 7, 4096)
        floor_px = 360
        self.assertLess(floor_px * mpp / 1000.0,
                        max(rn.VIEW_KM, rn.RADIUS_KM * 2),
                        "the size clamp silently overrides VIEW_KM")


class TestPalette(unittest.TestCase):
    """classify() decides what counts as rain and how strong it is. Both were
    wrong at once once: the pale end of the ramp was rejected outright, and the
    blue band was ordered backwards."""

    def test_ramp_is_monotonic_in_intensity(self):
        sats, values = [], []
        for c in rn._RAMP:
            h, s, v = colorsys.rgb_to_hsv(*[x / 255.0 for x in c])
            sats.append((h * 360, s, v))
        # blue end: pale (low saturation) first, deepening into the core
        blues = [x[1] for x in sats if 170 <= x[0] <= 230]
        self.assertEqual(blues, sorted(blues),
                         "blue ramp is no longer monotonic in saturation")
        # warm end: yellow first (hue 56), then orange, then red (hue 0)
        warm = [x[0] for x in sats if x[0] < 170]
        self.assertEqual(warm, sorted(warm, reverse=True),
                         "warm ramp is not yellow -> orange -> red")

    def test_ramp_still_looks_like_the_palette(self):
        """RainViewer could change the palette. The ramp here is a copy, so
        check it still describes blue -> yellow -> orange -> red."""
        bands = set()
        for c in rn._RAMP:
            h = colorsys.rgb_to_hsv(*[x / 255.0 for x in c])[0] * 360
            if 170 <= h <= 230:
                bands.add("blue")
            elif 40 <= h < 65:
                bands.add("yellow")
            elif 20 <= h < 40:
                bands.add("orange")
            elif h < 20:
                bands.add("red")
        self.assertEqual(bands, {"blue", "yellow", "orange", "red"},
                         "ramp no longer spans blue/yellow/orange/red: %s" % bands)

    def test_pale_cyan_is_light_rain_not_dropped(self):
        """(136,221,238) is the weakest echo on the ramp. It used to classify
        as -1, which deleted 19.9% of the blue band from the coverage figure."""
        self.assertEqual(rn.classify(136, 221, 238), 0)
        self.assertEqual(rn.classify(108, 209, 235), 0)

    def test_deep_blue_is_stronger_than_pale_cyan(self):
        pale = rn.classify(136, 221, 238)
        deep = rn.classify(0, 71, 104)
        self.assertGreater(deep, pale, "blue band is ordered backwards again")

    def test_class_never_dips_along_the_ramp(self):
        prev = -1
        for c in rn._RAMP:
            k = rn.classify(*c)
            self.assertGreaterEqual(k, prev, "class dipped at %s" % (c,))
            prev = k

    def test_every_ramp_colour_is_rain(self):
        for c in rn._RAMP:
            self.assertGreaterEqual(rn.classify(*c), 0, "%s rejected" % (c,))

    def test_all_five_classes_are_reachable(self):
        seen = {rn.classify(*c) for c in rn._RAMP}
        self.assertEqual(seen, {0, 1, 2, 3, 4},
                         "legend promises 5 levels, ramp gives %s" % sorted(seen))

    def test_coverage_mask_is_rejected(self):
        """The mask is the translucent warm-grey ramp around the radar disc.
        Every sample here was lifted out of real tiles."""
        for c in ((222, 208, 151), (218, 204, 147), (214, 200, 143),
                  (210, 196, 139), (206, 192, 135), (194, 180, 130),
                  (182, 169, 126), (170, 158, 121), (158, 147, 117),
                  (142, 133, 111), (124, 117, 101), (111, 107, 95),
                  (99, 97, 89)):
            self.assertEqual(rn.classify(*c), -1,
                             "coverage mask leaked through: %s" % (c,))

    def test_transparent_background_is_not_rain(self):
        self.assertEqual(rn.classify(0, 0, 0), -1)

    def test_off_ramp_colour_lands_nearest_station(self):
        """Antialiased pixels between two stations must not be thrown away."""
        mid = tuple((a + b) // 2 for a, b in zip(rn._RAMP[4], rn._RAMP[5]))
        self.assertGreaterEqual(rn.classify(*mid), 0)
        self.assertLessEqual(rn.classify(*mid), 1)

    def test_legend_swatches_are_classifiable(self):
        """A swatch must never advertise a colour the classifier rejects."""
        for level, _th, rgb in rn.LEGEND:
            self.assertEqual(rn.classify(*rgb), level,
                             "legend swatch %s classifies as %d, not %d"
                             % (rgb, rn.classify(*rgb), level))

    def test_level_labels_cover_every_class(self):
        """class_histogram reports class+1, with 0 reserved for 'no rain'."""
        self.assertEqual(len(rn.LEVEL_TH), 6)
        self.assertEqual(len(rn.LEVEL_EN), 6)
        self.assertEqual(rn.LEVEL_TH[0], "ไม่มีฝน")
        self.assertEqual(rn.LEVEL_EN[0], "no rain")
        self.assertEqual(len(rn.LEGEND), 5)


class TestHistogram(unittest.TestCase):
    """class_histogram produces the percentages that both the Telegram caption
    and the HA sensors read, so the geometry inside it matters."""

    def _disc(self, radius_px, colour, grid):
        im = Image.new("RGBA", (grid, grid), (0, 0, 0, 0))
        px = im.load()
        c = grid // 2
        for y in range(grid):
            for x in range(grid):
                if (x - c) ** 2 + (y - c) ** 2 <= radius_px ** 2:
                    px[x, y] = tuple(colour) + (255,)
        return im

    # 200 m/px puts the 30 km band exactly on a 150 px radius in a 300 grid,
    # so a disc that fills the band is also the whole sampled area.
    MPP = 200.0
    GRID = 300
    RADIUS_PX = int(30.0 * 1000.0 / MPP)

    def _band(self, im, mpp=None):
        return rn.class_histogram(im, mpp or self.MPP,
                                  [(0.0, 30.0, "c")], 30.0)["c"]

    def test_disc_filling_the_band_reads_full_coverage(self):
        st = self._band(self._disc(self.RADIUS_PX, (0, 71, 104), self.GRID))
        self.assertGreater(st["pct"], 95.0)
        self.assertEqual(st["class"], 3)      # deepest blue is class 2, +1

    def test_class_escalates_with_the_ramp(self):
        """The reported class has to climb as the ramp strengthens, otherwise
        'ฝนหนัก' and 'ฝนรุนแรง' are the same word twice."""
        seen = {}
        for c in rn._RAMP:
            seen.setdefault(rn.classify(*c), c)
        self.assertEqual(sorted(seen), [0, 1, 2, 3, 4])
        strongest = max(rn._RAMP, key=lambda c: (rn.classify(*c), sum(c)))
        self.assertEqual(rn.classify(*strongest), 4)

    def test_empty_frame_reads_zero_percent(self):
        st = self._band(Image.new("RGBA", (self.GRID, self.GRID), (0, 0, 0, 0)))
        self.assertEqual(st["pct"], 0.0)

    def test_light_rain_counts_where_the_old_gate_ignored_it(self):
        st = self._band(self._disc(self.RADIUS_PX, (136, 221, 238), self.GRID))
        self.assertGreater(st["pct"], 95.0)

    def test_worst_class_is_the_strongest_present(self):
        pale = self._band(self._disc(self.RADIUS_PX, (136, 221, 238), self.GRID))
        self.assertEqual(pale["class"], 1)          # class 0, +1
        bright = self._band(self._disc(self.RADIUS_PX, (255, 68, 0), self.GRID))
        self.assertEqual(bright["class"], 4)

    def test_coverage_mask_does_not_count_as_rain(self):
        st = self._band(self._disc(self.RADIUS_PX, (222, 208, 151), self.GRID))
        self.assertEqual(st["pct"], 0.0, "mask counted as rain coverage")

    def test_inner_band_can_exceed_the_outer(self):
        """Rain inside 15 km but nothing outside: the inner band reads 100%
        while the 30 km disc reads low. That asymmetry is the point of the
        two bands - it is what separates 'raining now' from 'raining nearby'."""
        inner_px = int(15.0 * 1000.0 / self.MPP)
        im = self._disc(inner_px, (0, 71, 104), self.GRID)
        st = rn.class_histogram(im, self.MPP,
                                [(0.0, 30.0, "outer"), (0.0, 15.0, "inner")],
                                30.0)
        self.assertGreater(st["inner"]["pct"], 95.0)
        self.assertLess(st["outer"]["pct"], 60.0)

    def test_a_ring_of_rain_outside_counts_only_outward(self):
        """The mirror case: rain in the 15-30 km shell with a clear centre."""
        grid = self.GRID
        im = Image.new("RGBA", (grid, grid), (0, 0, 0, 0))
        px = im.load()
        c = grid // 2
        for y in range(grid):
            for x in range(grid):
                d = math.hypot(x - c, y - c)
                if int(15.0 * 1000.0 / self.MPP) <= d <= self.RADIUS_PX:
                    px[x, y] = (0, 71, 104, 255)
        st = rn.class_histogram(im, self.MPP,
                                [(0.0, 30.0, "outer"), (15.0, 30.0, "shell")],
                                30.0)
        self.assertGreater(st["shell"]["pct"], 50.0)
        self.assertLess(st["outer"]["pct"], st["shell"]["pct"] + 1.0)


class TestMosaic(unittest.TestCase):
    """mosaic() stitches tiles around a centre point. An off-by-one here moves
    the home marker off the middle of the crop or leaves a half-tile gap at the
    edge. Neither is visible to the eye, so it is pinned down here.

    Each synthetic tile is filled with a solid colour derived from its grid
    coordinates, so the assembled image can be checked tile by tile.
    """

    @staticmethod
    def _tile_colour(tx, ty):
        return ((tx * 37) % 256, (ty * 53) % 256, 128, 255)

    def _serve(self, tpx):
        """Replace rn.fetch with a function returning encoded synthetic tiles."""
        buf = {}

        def fake_fetch(url, timeout=45):
            x = int(url.split("x=")[1].split("&")[0])
            y = int(url.split("y=")[1])
            key = (x, y)
            if key not in buf:
                out = io.BytesIO()
                Image.new("RGBA", (tpx, tpx), self._tile_colour(x, y)).save(out, "PNG")
                buf[key] = out.getvalue()
            return buf[key]

        return fake_fetch, buf

    def _mosaic(self, cx, cy, size, tpx, workers=1):
        fake, buf = self._serve(tpx)
        original = rn.fetch
        rn.fetch = fake
        try:
            img, n = rn.mosaic("https://e/{z}/x={x}&y={y}", 7, cx, cy,
                               size, tpx, workers=workers)
        finally:
            rn.fetch = original
        return img, n, buf

    def test_every_tile_lands_where_the_grid_says(self):
        tpx, size = 256, 400
        for cx, cy in ((1000.0, 1000.0), (1024.0, 1024.0), (900.5, 1100.25)):
            img, n, buf = self._mosaic(cx, cy, size, tpx)
            self.assertEqual(n, len(buf))
            self.assertEqual(img.size, (size, size))
            x0, y0 = cx - size / 2.0, cy - size / 2.0
            for (tx, ty) in buf:
                # a point well inside this tile, in assembled-image coordinates
                px = int(round(tx * tpx - x0)) + tpx // 2
                py = int(round(ty * tpx - y0)) + tpx // 2
                if 0 <= px < size and 0 <= py < size:
                    self.assertEqual(
                        img.getpixel((px, py))[:3], self._tile_colour(tx, ty)[:3],
                        "tile %d,%d misplaced at cx=%s cy=%s" % (tx, ty, cx, cy))

    def test_centre_of_the_frame_is_the_centre_of_the_crop(self):
        """The home marker is drawn dead centre, so the mosaic has to agree or
        the marker sits on the wrong side of the rain."""
        tpx, size = 256, 400
        cx, cy = 900.5, 1100.25
        img, _n, _buf = self._mosaic(cx, cy, size, tpx)
        centre = img.getpixel((size // 2, size // 2))[:3]
        self.assertEqual(centre, self._tile_colour(int(cx // tpx),
                                                    int(cy // tpx))[:3])

    def test_no_gaps_between_tiles(self):
        tpx, size = 256, 400
        img, _n, _buf = self._mosaic(1000.0, 1000.0, size, tpx)
        self.assertEqual(img.getextrema()[3], (255, 255),
                         "assembled image has transparent gaps")

    def test_big_tiles_need_fewer_requests(self):
        """The whole point of the 4096 px tiles. Counting attempts instead of
        building them keeps 4096 px tiles cheap in the test."""
        def attempts(tpx, size=950):
            asked = []

            def failing(url, timeout=45):
                asked.append(url)
                raise OSError("counting only")

            original = rn.fetch
            rn.fetch = failing
            try:
                rn.mosaic("https://e/{z}/x={x}&y={y}", 7, 100000.0, 60000.0,
                          size, tpx, workers=1)
            finally:
                rn.fetch = original
            return len(asked)

        self.assertLess(attempts(4096), attempts(1024))
        self.assertLess(attempts(1024), attempts(256))

    def test_a_failing_tile_is_skipped_not_fatal(self):
        """One 410 from a just-generated frame must not lose the whole map."""
        def flaky(url, timeout=45):
            x = int(url.split("x=")[1].split("&")[0])
            y = int(url.split("y=")[1])
            if x == 3:              # a tile the crop straddles
                raise OSError("HTTP 410")
            out = io.BytesIO()
            Image.new("RGBA", (256, 256), self._tile_colour(x, y)).save(out, "PNG")
            return out.getvalue()

        original = rn.fetch
        rn.fetch = flaky
        try:
            img, n = rn.mosaic("https://e/{z}/x={x}&y={y}", 7, 1000.0, 1000.0,
                               400, 256, workers=1)
        finally:
            rn.fetch = original
        self.assertLess(n, 4)
        self.assertIsNotNone(img)


class FakeImage:
    """Stands in for a decoded PIL image; only .size matters to the probe."""

    def __init__(self, size):
        self.size = size


class TestTileSizeProbe(unittest.TestCase):
    """RainViewer answers an unsupported tile size with a 256 px placeholder
    instead of an error, so the decoded size is the only honest test."""

    def _run(self, served):
        """served(s) -> edge actually decoded, or None to raise."""
        def make_url(s, z=None, x=None, y=None):
            return "url:%d" % s

        original_fetch = rn.fetch
        original_open = rn.Image.open
        original_sleep = rn.time.sleep

        def fake_fetch(url, timeout=45):
            # RainViewer answers an unsupported size with a *smaller* image
            # rather than an error, so the payload reports what it really is.
            got = served(int(url.split(":")[1]))
            if got is None:
                raise OSError("HTTP 410")
            return str(got).encode()

        def fake_open(bio):
            edge = int(bio.read())
            return FakeImage((edge, edge))

        rn.fetch = fake_fetch
        rn.Image.open = fake_open
        rn.time.sleep = lambda s: None
        try:
            return rn.pick_tile_size(make_url, 7, 100, 59)
        finally:
            rn.fetch = original_fetch
            rn.Image.open = original_open
            rn.time.sleep = original_sleep

    def test_placeholder_is_rejected(self):
        """4096 and 2048 answer with a 256 px placeholder, so the probe keeps
        stepping down until a real size turns up."""
        self.assertEqual(self._run(lambda s: str(s) if s <= 1024 else "256"), 1024)

    def test_largest_supported_size_wins(self):
        self.assertEqual(self._run(lambda s: str(s)), max(rn.TILE_SIZES))

    def test_everything_failing_falls_back_to_256(self):
        self.assertEqual(self._run(lambda s: None), rn.TILE)

    def test_a_flaky_frame_is_retried_before_giving_up(self):
        """A frame that was just generated can 410 for a while; a transient
        failure must not cost the whole tile size."""
        state = {"n": 0}

        def served(s):
            state["n"] += 1
            return None if state["n"] <= 2 else str(s)

        self.assertEqual(self._run(served), max(rn.TILE_SIZES))


class TestCarryForward(unittest.TestCase):
    """TMD resets connections intermittently. When it does, the value must not
    reach the sensors as null: command_line renders a missing key as the
    literal string "None", which is what a dashboard ends up showing."""

    def test_a_failed_fetch_keeps_the_last_good_value(self):
        prev = {"tmd_24h": "ฝน 80%", "tmd_24h_rain_pct": 80}
        fresh = {"tmd_24h": None, "tmd_24h_rain_pct": None,
                 "tmd_warning": "มีประกาศเตือน"}
        failed = {"tmd_24h", "tmd_24h_rain_pct"}
        out = rn.carry_forward(fresh, failed, prev)
        self.assertEqual(out["tmd_24h"], "ฝน 80%")
        self.assertEqual(out["tmd_24h_rain_pct"], 80)
        self.assertEqual(out["tmd_warning"], "มีประกาศเตือน",
                         "a fresh value must win over the old one")

    def test_a_warning_clearing_is_not_suppressed(self):
        """'no warning' is a real reading, not a failure. Holding the previous
        warning would keep announcing a storm that has already passed, so a
        cleared warning must NOT be listed in `failed`."""
        prev = {"tmd_warning": "ประกาศเตือนฝนหนัก"}
        fresh = {"tmd_warning": None}
        self.assertIsNone(
            rn.carry_forward(fresh, set(), prev)["tmd_warning"],
            "a cleared warning was resurrected from the previous run")

    def test_zero_is_a_value_not_a_failure(self):
        prev = {"tmd_24h_rain_pct": 80}
        fresh = {"tmd_24h_rain_pct": 0}
        self.assertEqual(
            rn.carry_forward(fresh, set(), prev)["tmd_24h_rain_pct"], 0)

    def test_a_value_is_never_invented(self):
        out = rn.carry_forward({"tmd_24h": None}, {"tmd_24h"}, {})
        self.assertIsNone(out["tmd_24h"],
                          "a fresh install with a broken source must read empty")

    def test_a_key_missing_from_the_failure_set_never_falls_back(self):
        prev = {"tmd_7d_rain_pct": 60}
        fresh = {"tmd_7d_rain_pct": None}
        self.assertIsNone(
            rn.carry_forward(fresh, set(), prev)["tmd_7d_rain_pct"])

    def test_no_previous_file_is_not_fatal(self):
        original = rn.SUMMARY
        rn.SUMMARY = os.path.join(tempfile.gettempdir(), "no-such-summary.json")
        try:
            self.assertEqual(rn.read_previous_summary(), {})
        finally:
            rn.SUMMARY = original

    def test_corrupt_previous_file_is_not_fatal(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, b"{not json")
        os.close(fd)
        original = rn.SUMMARY
        rn.SUMMARY = path
        try:
            self.assertEqual(rn.read_previous_summary(), {})
        finally:
            rn.SUMMARY = original
            os.unlink(path)

    def test_previous_file_is_read_back(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, json.dumps({"tmd_24h": "ฝน 60%"}).encode("utf-8"))
        os.close(fd)
        original = rn.SUMMARY
        rn.SUMMARY = path
        try:
            self.assertEqual(rn.read_previous_summary()["tmd_24h"], "ฝน 60%")
        finally:
            rn.SUMMARY = original
            os.unlink(path)

    def test_a_previous_summary_of_the_wrong_shape_is_ignored(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, b'"just a string"')
        os.close(fd)
        original = rn.SUMMARY
        rn.SUMMARY = path
        try:
            self.assertEqual(rn.read_previous_summary(), {})
        finally:
            rn.SUMMARY = original
            os.unlink(path)


class TestHomeClass(unittest.TestCase):
    """home_class() answers 'is it raining on the roof right now', which is
    what drives binary_sensor.radar_rain_now."""

    def test_radius_covers_about_1_5km_at_every_resolution(self):
        for tpx in (256, 512, 1024, 2048, 4096):
            mpp = rn.meters_per_pixel(HOME_LAT, 7, tpx)
            r = max(0, min(1200, int(round(1500.0 / mpp))))
            self.assertAlmostEqual(r * mpp, 1500.0, delta=mpp * 1.5,
                                   msg="home radius wrong at tpx%d" % tpx)

    def test_failed_fetch_reads_as_no_rain(self):
        original = rn.fetch

        def boom(url, timeout=45):
            raise OSError("network down")

        rn.fetch = boom
        try:
            cx, cy = rn.latlon_to_world_px(HOME_LAT, HOME_LON, 7, 4096)
            self.assertEqual(rn.home_class("https://e/{z}/{x}/{y}", 7, cx, cy,
                                           74.2, 4096), -1)
        finally:
            rn.fetch = original


class TestChooseGrid(unittest.TestCase):
    """class_histogram's grid argument was overwritten unconditionally, so the
    trend loop's grid=256 was silently ignored and the 300/540 default used
    instead. The density is what the caller asked for, or it is a bug."""

    def test_an_explicit_grid_wins(self):
        self.assertEqual(rn.choose_grid(202.0, 256, 4096), 256,
                         "the caller's grid was ignored again")

    def test_default_density_targets_100_samples_across(self):
        self.assertEqual(rn.choose_grid(202.0, None, 4096), 300)
        self.assertEqual(rn.choose_grid(50.0, None, 4096), 540)

    def test_never_exceeds_the_image_width(self):
        self.assertEqual(rn.choose_grid(202.0, 4096, 950), 950)
        self.assertEqual(rn.choose_grid(202.0, None, 200), 200)

    def test_the_deployed_trend_call_asks_for_256(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "radar_notify.py"), encoding="utf-8").read()
        self.assertIn("grid=256", src)


class TestCarryFlag(unittest.TestCase):
    """A tri-state warning flag: None means 'could not check', which must never
    be rounded down to 'all clear'."""

    def test_unknown_holds_the_previous_value(self):
        self.assertTrue(rn.carry_flag(None, True))
        self.assertFalse(rn.carry_flag(None, False))

    def test_a_genuine_all_clear_wins(self):
        self.assertFalse(rn.carry_flag(False, True),
                         "a cleared watch must be announced, not held")

    def test_a_genuine_watch_wins(self):
        self.assertTrue(rn.carry_flag(True, False))

    def test_nothing_known_yet_reads_as_off(self):
        self.assertFalse(rn.carry_flag(None, None))


class TestHiiTriState(unittest.TestCase):
    """HII is a warning feed. A failure has to be distinguishable from 'no
    watch', or the dashboard reports all-clear on no evidence."""

    def _patch(self, value):
        orig = rn.fetch
        if isinstance(value, Exception):
            def boom(*a, **k):
                raise value
            rn.fetch = boom
        else:
            rn.fetch = lambda *a, **k: value
        return orig

    def test_a_failed_fetch_is_unknown_not_all_clear(self):
        orig = self._patch(RuntimeError("connection reset"))
        try:
            self.assertIsNone(rn.hii_flashflood_watch(),
                              "a broken HII feed was reported as 'no watch'")
        finally:
            rn.fetch = orig

    def test_a_malformed_body_is_unknown(self):
        orig = self._patch(b"{not json")
        try:
            self.assertIsNone(rn.hii_flashflood_watch())
        finally:
            rn.fetch = orig

    def test_no_listed_areas_is_a_genuine_false(self):
        orig = self._patch(b'{"area": []}')
        try:
            self.assertIs(rn.hii_flashflood_watch(), False)
        finally:
            rn.fetch = orig

    def test_a_listed_home_area_is_true(self):
        payload = json.dumps({"area": [
            {"province": rn.HOME_PROVINCE, "amphoe": rn.HOME_DISTRICT,
             "tambon": ""}]}).encode("utf-8")
        orig = self._patch(payload)
        try:
            self.assertIs(rn.hii_flashflood_watch(), True)
        finally:
            rn.fetch = orig

    def test_an_unknown_becomes_the_previous_value(self):
        """The build() contract, exercised end to end through both helpers."""
        orig = self._patch(RuntimeError("connection reset"))
        try:
            fresh = rn.hii_flashflood_watch()
        finally:
            rn.fetch = orig
        self.assertTrue(rn.carry_flag(fresh, True),
                        "an HII outage cleared a standing flood watch")


class TestOpenMeteoSentinel(unittest.TestCase):
    """Open-Meteo going down must not write null for the rainfall total: a null
    reaches sensor.rain_next_60min_mm as the literal string "None"."""

    def test_failure_uses_the_mm_sentinel(self):
        orig = rn.fetch
        rn.fetch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            soon, minutes, mm60, ok = rn.openmeteo_nowcast()
        finally:
            rn.fetch = orig
        self.assertFalse(ok)
        self.assertEqual(mm60, -1, "a null would render as the string None")
        self.assertIsNone(minutes,
                          "minutes must stay None so a radar ETA can still win")

    def test_a_dry_forecast_is_zero_not_a_sentinel(self):
        payload = json.dumps({"minutely_15": {
            "precipitation": [0, 0, 0, 0, 0, 0]}}).encode("utf-8")
        orig = rn.fetch
        rn.fetch = lambda *a, **k: payload
        try:
            soon, minutes, mm60, ok = rn.openmeteo_nowcast()
        finally:
            rn.fetch = orig
        self.assertTrue(ok)
        self.assertFalse(soon)
        self.assertEqual(mm60, 0.0)
        self.assertIsNone(minutes)


class TestYamlSentinels(unittest.TestCase):
    """summary.json can hold JSON null for a source that failed this run, and
    HA's command_line platform renders a missing key as the literal string
    "None", which then reaches the dashboard and the Telegram group. Every
    template that reads a nullable field needs a default() guard.

    This replaces a test that asserted None == None: it named all the TMD keys
    but never read the YAML, so it passed while rain_next_60min_mm shipped
    "None" to the group.
    """

    HERE = os.path.dirname(os.path.abspath(__file__))
    # every summary.json key that can be null when its source fails
    NULLABLE = ["radar_zoom", "rain_next_60min_mm", "tmd_24h_rain_pct",
                "tmd_7d_rain_pct", "tmd_24h", "tmd_7d_desc", "tmd_warning"]

    def _yaml(self):
        with open(os.path.join(self.HERE, "weather_radar.yaml"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_every_nullable_template_has_a_default(self):
        text = self._yaml()
        for key in self.NULLABLE:
            with self.subTest(key=key):
                # \b stops tmd_24h from also matching tmd_24h_rain_pct
                pat = re.compile(r"value_json\.%s\b" % re.escape(key))
                lines = [l for l in text.splitlines() if pat.search(l)]
                self.assertTrue(lines, "no value_template reads %s" % key)
                for l in lines:
                    self.assertIn("default(", l,
                                  "%s can be null but has no default() "
                                  "guard:\n  %s" % (key, l.strip()))

    def test_the_second_argument_is_present(self):
        """default(x) alone replaces only an undefined key, not an explicit
        null. The `true` is what makes the guard work; do not drop it."""
        text = self._yaml()
        guarded = [l for l in text.splitlines()
                   if "value_json." in l and "default(" in l]
        self.assertTrue(guarded, "no guarded templates found at all")
        for l in guarded:
            with self.subTest(line=l.strip()):
                self.assertIn(", true)", l)


class TestNoHardcodedCredential(unittest.TestCase):
    """A JWT committed to the repo is a live credential in git history. The
    repo's own convention (dashboard/README.md) forbids embedding keys."""

    HERE = os.path.dirname(os.path.abspath(__file__))

    def test_no_script_embeds_a_jwt(self):
        for name in ("make_automations.py", "add_lovelace_view.py",
                     "radar_notify.py", "test_radar_notify.py",
                     "test_captions.py"):
            with self.subTest(file=name):
                with open(os.path.join(self.HERE, name),
                          encoding="utf-8") as fh:
                    src = fh.read()
                self.assertIsNone(
                    re.search(r"eyJ[A-Za-z0-9_\-]{10,}\.", src),
                    "%s embeds what looks like a JWT" % name)

    def test_the_deploy_scripts_read_the_token_from_the_environment(self):
        for name in ("make_automations.py", "add_lovelace_view.py"):
            with self.subTest(file=name):
                with open(os.path.join(self.HERE, name),
                          encoding="utf-8") as fh:
                    src = fh.read()
                self.assertIn("HA_TOKEN", src)
                self.assertIn("require_token", src)


class TestWriteAtomic(unittest.TestCase):
    """summary.json and latest.png are read by HA and by Telegram while the
    build may still be running, so neither may be replaced in place."""

    def test_replaces_content_and_leaves_no_temp_file(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "out.bin")
        rn.write_atomic(path, b"first")
        rn.write_atomic(path, b"second")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), b"second")
        self.assertEqual(os.listdir(d), ["out.bin"],
                         "an atomic write left a temp file behind")

    def test_the_output_mode_is_set_explicitly(self):
        """mkstemp defaults to 0600; /config/www is served publicly, so the
        mode must be set rather than inherited. Asserted through the chmod call
        because Windows does not report POSIX mode bits."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "out.bin")
        seen = []
        original = os.chmod

        def spy(p, m):
            seen.append(m)
            return original(p, m)

        os.chmod = spy
        try:
            rn.write_atomic(path, b"x", 0o644)
        finally:
            os.chmod = original
        self.assertIn(0o644, seen, "write_atomic never set the file mode")

    @unittest.skipIf(os.name == "nt", "Windows has no POSIX permission bits")
    def test_the_output_is_world_readable(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "out.bin")
        rn.write_atomic(path, b"x")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

    def test_a_failed_write_leaves_no_temp_file_behind(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "out.bin")

        class Boom:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def write(self, data):
                raise OSError("disk full")

        original = os.fdopen

        def fake_fdopen(fd, mode):
            os.close(fd)
            return Boom()

        os.fdopen = fake_fdopen
        try:
            with self.assertRaises(OSError):
                rn.write_atomic(path, b"x")
        finally:
            os.fdopen = original
        self.assertEqual(os.listdir(d), [],
                         "a failed write left a temp file behind")
        self.assertFalse(os.path.exists(path))

    def test_png_write_goes_through_write_atomic(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "radar_notify.py"), encoding="utf-8").read()
        self.assertIn("write_atomic(OUT", src,
                      "latest.png is written non-atomically again")


class TestRunBudget(unittest.TestCase):
    """HA terminates a shell_command at 60 s and the docs are explicit that
    "there is no option to alter this behavior", so the run has to police
    itself. Before this, a network that timed out rather than refused could
    spend ~30 min in the tile probe alone; HA would kill the process before
    summary.json was written, leaving stale data with no indication."""

    def setUp(self):
        self._saved = rn._DEADLINE

    def tearDown(self):
        rn._DEADLINE = self._saved

    def test_an_untimed_run_never_refuses(self):
        rn._DEADLINE = None
        self.assertIsNone(rn.deadline_remaining())
        self.assertTrue(rn.budget_left(10 ** 9))

    def test_the_budget_counts_down(self):
        rn._DEADLINE = time.monotonic() + 30
        self.assertGreater(rn.deadline_remaining(), 25)
        self.assertTrue(rn.budget_left(5))
        self.assertFalse(rn.budget_left(60))

    def test_set_deadline_honours_its_argument(self):
        rn.set_deadline(12)
        self.assertLessEqual(rn.deadline_remaining(), 12)
        self.assertTrue(rn.budget_left(1))

    def test_an_exhausted_budget_blocks_a_request(self):
        rn._DEADLINE = time.monotonic() - 1
        with self.assertRaises(TimeoutError):
            rn.fetch("http://127.0.0.1:9/never")

    def test_the_probe_gives_up_without_fetching(self):
        """Out of budget the probe must return at once, not make one more
        120 s request."""
        rn._DEADLINE = time.monotonic() - 1
        calls = []

        def never(url, timeout=45):
            calls.append(url)
            raise AssertionError("fetch called with no budget left")

        orig = rn.fetch
        rn.fetch = never
        try:
            got = rn.pick_tile_size(
                lambda s, z=None, x=None, y=None: "u:%d" % s, 7, 0, 0)
        finally:
            rn.fetch = orig
        self.assertEqual(got, rn.TILE)
        self.assertEqual(calls, [],
                         "the probe made a request it had no budget for")

    def test_the_probe_cannot_overrun_its_budget(self):
        """The regression: 5 sizes x 3 attempts, each retrying, used to run for
        minutes. With a deadline it must return well inside it."""
        rn.set_deadline(2)
        ticks = []

        def failing(url, timeout=45):
            ticks.append(timeout)
            raise OSError("timed out")

        orig_fetch, orig_sleep = rn.fetch, rn.time.sleep
        rn.fetch = failing
        rn.time.sleep = lambda s: None
        started = time.monotonic()
        try:
            got = rn.pick_tile_size(
                lambda s, z=None, x=None, y=None: "u:%d" % s, 7, 0, 0)
        finally:
            rn.fetch = orig_fetch
            rn.time.sleep = orig_sleep
        elapsed = time.monotonic() - started
        self.assertEqual(got, rn.TILE)
        self.assertLess(elapsed, 1.0,
                        "the probe ran %.2fs against a 2s budget" % elapsed)

    def test_a_request_is_clamped_to_what_is_left(self):
        """A 120 s tile request must be shortened to the remaining budget, or
        the clamp is decorative."""
        rn.set_deadline(3)
        seen = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"ok"

        def fake_urlopen(req, timeout):
            seen["timeout"] = timeout
            return FakeResp()

        orig = rn.urllib.request.urlopen
        rn.urllib.request.urlopen = fake_urlopen
        try:
            rn.fetch("http://example.invalid/clamp", 120)
        finally:
            rn.urllib.request.urlopen = orig
        self.assertLessEqual(seen["timeout"], 3.0,
                             "a 120s request was not clamped to the budget")

    def test_build_starts_the_clock(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "radar_notify.py"), encoding="utf-8").read()
        self.assertIn("set_deadline()", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)