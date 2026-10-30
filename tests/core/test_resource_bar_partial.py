"""Independent resource fields, fresh-frame retries, and dashboard writes."""

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.config import server
from module.config.stored.classes import StoredInt, StoredStamina
from tests.support.offline import ControlledClock, fixture_image

server.set_lang("global_cn")

from tasks.base.resource_bar import (
    RESOURCE_BAR_LAYOUT_MAIN,
    ResourceBarMixin,
    ResourceBarSpec,
    ResourceBarValue,
    get_resource_bar_segment_area,
)
from tasks.base.resource_scheduling import ResourceCoordinator
from tests.support.resources import ResourceConfig, resource_values

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests/fixtures/arena/manifest.json"


def reader_for_image(image):
    reader = ResourceBarMixin()
    reader.config = SimpleNamespace(Emulator_GameLanguage="cn")
    reader.device = SimpleNamespace(image=image, screenshot=Mock())
    reader.image_crop = lambda area, copy=False: reader.device.image[
        area[1]:area[3], area[0]:area[2]
    ]
    return reader


def icon_set(*, missing=(), positions=None):
    positions = positions or {}
    return {
        key: SimpleNamespace(
            area=(x, 13, x + 24, 47),
            button_offset=(positions.get(key, x) - x, 0),
            load_search=Mock(),
            match_template=Mock(return_value=key not in missing),
        )
        for key, x in zip(RESOURCE_BAR_LAYOUT_MAIN, (300, 500, 700))
    }


class PartialResourceBarTests(unittest.TestCase):
    def setUp(self):
        self.reader = reader_for_image(np.zeros((720, 1280, 3), dtype=np.uint8))

    def inspect(self, *, missing=(), positions=None, texts=None, limits=None):
        texts = texts or {"arena_flag": "0/5", "stamina": "8/360", "skystone": "2122"}
        read_keys = []

        def ocr(*args, name, **kwargs):
            key = name.rsplit(".", 1)[1]
            read_keys.append(key)
            return SimpleNamespace(ocr_single_line=lambda *args, **kwargs: texts[key])

        with patch("tasks.base.resource_bar.OcrResourceBar", side_effect=ocr):
            result = self.reader.inspect_resource_bar_status(
                RESOURCE_BAR_LAYOUT_MAIN, "Main", icons=icon_set(missing=missing, positions=positions),
                segment_right_limits=limits,
            )
        return result, read_keys

    def test_missing_flag_preserves_independently_anchored_stamina_and_skystone(self):
        result, keys = self.inspect(missing=("arena_flag",))
        self.assertEqual(keys, ["stamina", "skystone"])
        self.assertEqual(set(result.final), {"stamina", "skystone"})
        self.assertEqual((result.final["stamina"].value, result.final["stamina"].total), (8, 360))
        self.assertEqual(result.final["skystone"].value, 2122)

    def test_missing_middle_icon_never_uses_default_or_later_boundary(self):
        result, keys = self.inspect(missing=("stamina",))
        self.assertEqual(keys, ["skystone"])
        self.assertEqual(set(result.final), {"skystone"})

    def test_missing_last_icon_retains_only_fields_with_complete_boundaries(self):
        result, keys = self.inspect(missing=("skystone",))
        self.assertEqual(keys, ["arena_flag"])
        self.assertEqual(set(result.final), {"arena_flag"})
        self.assertEqual(result.final["arena_flag"].value, 0)

    def test_invalid_counter_does_not_discard_other_valid_fields(self):
        result, keys = self.inspect(texts={"arena_flag": "2/6", "stamina": "8/360", "skystone": "2122"})
        self.assertEqual(keys, list(RESOURCE_BAR_LAYOUT_MAIN))
        self.assertEqual(set(result.final), {"stamina", "skystone"})

    def test_zero_and_overflow_values_remain_valid(self):
        for current in (0, 415):
            with self.subTest(current=current):
                result, _ = self.inspect(texts={"arena_flag": "0/5", "stamina": f"{current}/360", "skystone": "0"})
                self.assertEqual(result.final["stamina"].value, current)
                self.assertEqual(result.final["skystone"].value, 0)

    def test_missing_icons_never_read_from_stale_offsets(self):
        result, keys = self.inspect(missing=RESOURCE_BAR_LAYOUT_MAIN)
        self.assertIsNone(result.final)
        self.assertEqual(keys, [])

    def test_wrong_order_or_overlap_rejects_even_a_partial_frame(self):
        for sky_position in (400, 510):
            with self.subTest(sky_position=sky_position):
                result, keys = self.inspect(missing=("arena_flag",), positions={"skystone": sky_position})
                self.assertIsNone(result.final)
                self.assertEqual(keys, [])
                self.assertIn("order_violation:skystone", result.icon_matches)

    def test_explicit_partial_offsets_require_both_boundaries(self):
        icons = icon_set()
        for offsets in ({}, {"arena_flag": (0, 0)}, {"stamina": (0, 0)}):
            with self.subTest(offsets=offsets):
                self.assertIsNone(get_resource_bar_segment_area(
                    RESOURCE_BAR_LAYOUT_MAIN, "arena_flag", icon_offsets=offsets, icons=icons,
                ))

    def test_invalid_extra_boundary_does_not_discard_other_fields(self):
        result, keys = self.inspect(limits={"arena_flag": 300})
        self.assertEqual(keys, ["stamina", "skystone"])
        self.assertEqual(set(result.final), {"stamina", "skystone"})

    def test_retry_recovers_complete_frame_without_merging_old_values(self):
        frames = [resource_values(stamina=(415, 360)),
                  resource_values(arena_flag=(0, 5), stamina=(8, 360), skystone=2122)]
        with ControlledClock() as clock:
            self.reader.device.screenshot.side_effect = lambda: clock.advance(1)
            self.reader.inspect_resource_bar_status = Mock(side_effect=[
                SimpleNamespace(final=frame, icon_matches=[]) for frame in frames
            ])
            result = self.reader.ocr_resource_bar_status(RESOURCE_BAR_LAYOUT_MAIN, "Main", timeout_seconds=2, timeout_count=0)
        self.assertEqual(result["stamina"].value, 8)
        self.assertEqual(set(result), set(RESOURCE_BAR_LAYOUT_MAIN))
        self.reader.device.screenshot.assert_called_once()

    def test_timeout_returns_only_the_latest_partial_frame(self):
        for latest in (resource_values(skystone=2122), None):
            with self.subTest(latest=latest), ControlledClock() as clock:
                self.reader.device.screenshot.side_effect = lambda: clock.advance(2)
                self.reader.inspect_resource_bar_status = Mock(side_effect=[
                    SimpleNamespace(final=resource_values(stamina=(415, 360)), icon_matches=[]),
                    SimpleNamespace(final=latest, icon_matches=[]),
                ])
                result = self.reader.ocr_resource_bar_status(
                    RESOURCE_BAR_LAYOUT_MAIN, "Main", timeout_seconds=1, timeout_count=0,
                )
                self.assertEqual(result, latest)

    def test_pending_refresh_retries_missing_fields_within_frame_budget(self):
        self.reader.config = ResourceConfig()
        self.reader.config.stored = SimpleNamespace()
        sync = ResourceCoordinator(self.reader.config)
        sync.request("Store")
        self.reader.is_in_main = Mock(return_value=True)
        self.reader.inspect_resource_bar_status = Mock(side_effect=[
            SimpleNamespace(final=resource_values(stamina=(8, 360), skystone=2122)),
            SimpleNamespace(final=resource_values(arena_flag=(0, 5))),
        ])
        self.assertTrue(self.reader.sync_pending_resources())
        self.assertFalse(sync.pending)
        self.assertEqual(self.reader.device.screenshot.call_count, 2)


class ResourceDashboardWriteTests(unittest.TestCase):
    def setUp(self):
        self.config = ResourceConfig()
        self.config.auto_update = False
        self.config.cross_set("DataUpdate.Dashboard.Stamina", {
            "time": datetime(2026, 10, 26, 12, 3, 12), "value": 415, "total": 360,
        })
        stamina = StoredStamina("DataUpdate.Dashboard.Stamina")
        stamina._bind(self.config)
        skystone = StoredInt("DataUpdate.Dashboard.Skystone")
        skystone._bind(self.config)
        self.config.stored = SimpleNamespace(Stamina=stamina, Skystone=skystone)
        self.reader = ResourceBarMixin()
        self.reader.config = self.config

    def test_repeat_integer_updates_stamina_without_erasing_observed_cap(self):
        for current in (8, 0):
            with self.subTest(current=current):
                value = ResourceBarValue(ResourceBarSpec("stamina", "int"), str(current), current)
                self.assertTrue(self.reader.write_resource_bar_status({"stamina": value}, source="server_repeat"))
                self.assertEqual((self.config.stored.Stamina.value, self.config.stored.Stamina.total), (current, 360))
                observed = ResourceCoordinator(self.config).observation("stamina")
                self.assertEqual((observed["value"], observed["total"]), (current, 0))
                self.assertEqual(observed["source"], "server_repeat")

    def test_integer_snapshot_maps_standard_currency_and_keeps_unknown_resource_separate(self):
        parsed = {
            "skystone": ResourceBarValue(ResourceBarSpec("skystone", "int"), "2122", 2122),
            "leif": ResourceBarValue(ResourceBarSpec("leif", "int"), "62", 62),
        }
        self.assertTrue(self.reader.write_resource_bar_status(parsed, source="pet_repeat"))
        self.assertEqual(self.config.stored.Skystone.value, 2122)
        self.assertEqual(self.config.stored.Stamina.value, 415)
        self.assertEqual(ResourceCoordinator(self.config).observation("leif")["value"], 62)


class CapturedPartialResourceBarTests(unittest.TestCase):
    def test_real_main_without_flag_reads_stamina_and_skystone(self):
        reader = reader_for_image(fixture_image(MANIFEST, "20261019-201526-416"))
        result = reader.inspect_resource_bar_status(RESOURCE_BAR_LAYOUT_MAIN, "Main")
        self.assertIn("arena_flag=miss", result.icon_matches)
        self.assertEqual(set(result.final), {"stamina", "skystone"})
        self.assertEqual((result.final["stamina"].value, result.final["stamina"].total), (360, 360))
        self.assertEqual(result.final["skystone"].value, 902)

    def test_real_main_missing_right_anchor_does_not_guess_stamina_boundary(self):
        reader = reader_for_image(fixture_image(MANIFEST, "20261019-201526-416"))
        # Explicitly masked pixels are a negative test derived from this real
        # capture, not a second device capture or a guessed numeric field.
        reader.device.image[12:52, 887:914] = 0
        result = reader.inspect_resource_bar_status(RESOURCE_BAR_LAYOUT_MAIN, "Main")
        self.assertIsNone(result.final)
        self.assertIn("skystone=miss", result.icon_matches)
