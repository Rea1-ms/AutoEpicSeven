# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline regressions for malformed fast-combat counts and bounded adjustment."""
import unittest
from pathlib import Path
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

from tests.support.history_september_runtime_fixes import Frames, timers
from tasks.dungeon.prepare import CombatPrepare, CombatPrepareCounter
from tasks.dungeon.assets.assets_dungeon_fast_combat import (
    FAST_COMBAT_TIMES_MINUS,
    FAST_COMBAT_TIMES_PLUS,
)


def parse_frame(ocr, image):
    # Substitute recognition only; exercise the actual counter postprocessor,
    # format parser and fast-combat range validation for every raw OCR value.
    return ocr.format_result(ocr.after_process(image["raw"]))


class CountPrepare(Frames, CombatPrepare):
    def __init__(self, frames):
        super().__init__(frames)
        self.actions = []
        self.device.multi_click = self.multi_click

    def _ocr_lang(self):
        return "cn"

    def _handle_dungeon_additional(self):
        return self.frame.get("popup", False)

    def appear(self, button):
        return self.frame.get("buttons", True)

    def multi_click(self, button, n, interval):
        self.actions.append((self.index, button.name, n))


def make_frames(*raws, **extra):
    return [{"raw": raw, **extra} for raw in raws]


class CounterParsingTests(unittest.TestCase):
    def read(self, raw):
        prepare = CountPrepare(make_frames(raw))
        with patch.object(CombatPrepareCounter, "ocr_single_line", parse_frame):
            return prepare._ocr_fast_combat_current_times()

    def test_valid_counts_and_known_character_substitutions(self):
        for raw, expected in (("1/10", 1), ("3/10", 3), ("10/10", 10),
                              (" 1 / 10 ", 1), ("I/lO", 1), ("4/5", 4)):
            with self.subTest(raw=raw):
                self.assertEqual(self.read(raw), expected)

    def test_missing_separator_is_never_guessed_or_clamped(self):
        for raw in ("110", "410", "1010", "1", "10", "", "unreadable"):
            with self.subTest(raw=raw):
                self.assertEqual(self.read(raw), 0)

    def test_invalid_per_launch_range_is_rejected(self):
        for raw in ("0/10", "1/0", "11/10", "110/10", "1/99", "4/3", "20/20"):
            with self.subTest(raw=raw):
                self.assertEqual(self.read(raw), 0)


class CountAdjustmentTests(unittest.TestCase):
    def run_count(self, frames, target=4, getter=None):
        prepare = CountPrepare(frames)
        with patch("tasks.dungeon.prepare.Timer", timers(lambda: prepare.index)), patch.object(
            CombatPrepareCounter, "ocr_single_line", parse_frame
        ):
            result = prepare._set_prepare_count(
                target,
                getter or prepare._ocr_fast_combat_current_times,
                FAST_COMBAT_TIMES_PLUS,
                FAST_COMBAT_TIMES_MINUS,
                "FastCombatCurrentTimes",
                max_count=prepare.COMBAT_MAX_FAST_COUNT,
            )
        return prepare, result

    def test_reported_missing_slash_does_not_click_minus_at_minimum(self):
        prepare, result = self.run_count(make_frames("1/10", "110", "1/10", "4/10"))
        self.assertTrue(result)
        self.assertEqual(prepare.actions, [(2, "FAST_COMBAT_TIMES_PLUS", 3)])

    def test_large_increase_reads_new_screenshot_between_small_batches(self):
        prepare, result = self.run_count(make_frames("1/10", "1/10", "4/10", "7/10", "10/10"), target=10)
        self.assertTrue(result)
        self.assertEqual(prepare.actions, [(1, "FAST_COMBAT_TIMES_PLUS", 3),
                                         (2, "FAST_COMBAT_TIMES_PLUS", 3),
                                         (3, "FAST_COMBAT_TIMES_PLUS", 3)])

    def test_large_decrease_reads_new_screenshot_between_small_batches(self):
        prepare, result = self.run_count(make_frames("10/10", "10/10", "7/10", "4/10", "1/10"), target=1)
        self.assertTrue(result)
        self.assertEqual(prepare.actions, [(1, "FAST_COMBAT_TIMES_MINUS", 3),
                                         (2, "FAST_COMBAT_TIMES_MINUS", 3),
                                         (3, "FAST_COMBAT_TIMES_MINUS", 3)])

    def test_partial_button_response_uses_observed_count(self):
        prepare, result = self.run_count(make_frames("1/10", "1/10", "2/10", "4/10"))
        self.assertTrue(result)
        self.assertEqual(prepare.actions, [(1, "FAST_COMBAT_TIMES_PLUS", 3),
                                         (2, "FAST_COMBAT_TIMES_PLUS", 2)])

    def test_overshoot_is_corrected_from_new_reading(self):
        prepare, result = self.run_count(make_frames("1/10", "1/10", "5/10", "4/10"))
        self.assertTrue(result)
        self.assertEqual(prepare.actions, [(1, "FAST_COMBAT_TIMES_PLUS", 3),
                                         (2, "FAST_COMBAT_TIMES_MINUS", 1)])

    def test_already_at_target_needs_no_button_or_click(self):
        prepare, result = self.run_count(make_frames("4/10", buttons=False))
        self.assertTrue(result)
        self.assertEqual(prepare.actions, [])

    def test_invalid_post_click_frame_waits_for_valid_count(self):
        prepare, result = self.run_count(make_frames("1/10", "1/10", "110", "4/10"))
        self.assertTrue(result)
        self.assertEqual(prepare.actions, [(1, "FAST_COMBAT_TIMES_PLUS", 3)])

    def test_clicking_without_progress_does_not_restart_stall_deadline(self):
        prepare, result = self.run_count(make_frames(*(["1/10"] * 20)))
        self.assertFalse(result)
        self.assertEqual(prepare.index, 3)
        self.assertEqual(len(prepare.actions), 2)
        self.assertTrue(all(count <= 3 for _, _, count in prepare.actions))

    def test_invalid_counter_never_clicks_and_eventually_fails(self):
        prepare, result = self.run_count(make_frames(*(["110"] * 20)))
        self.assertFalse(result)
        self.assertEqual(prepare.index, 18)
        self.assertEqual(prepare.actions, [])

    def test_adjustment_also_rejects_out_of_range_getter(self):
        prepare, result = self.run_count(make_frames(*(["unused"] * 20)), getter=lambda: 110)
        self.assertFalse(result)
        self.assertEqual(prepare.actions, [])

    def test_invalid_target_does_not_click(self):
        for target in (0, -1, 11, 110):
            with self.subTest(target=target):
                prepare, result = self.run_count(make_frames("1/10"), target=target)
                self.assertFalse(result)
                self.assertEqual(prepare.actions, [])

    def test_repeated_popups_do_not_extend_deadline_forever(self):
        prepare, result = self.run_count(make_frames(*(["1/10"] * 20), popup=True))
        self.assertFalse(result)
        self.assertEqual(prepare.index, 18)
        self.assertEqual(prepare.actions, [])

    def test_oscillating_counts_do_not_extend_deadline_forever(self):
        prepare, result = self.run_count(make_frames(*(["1/10", "2/10"] * 10)))
        self.assertFalse(result)
        self.assertEqual(prepare.index, 18)
        self.assertTrue(all(count <= 3 for _, _, count in prepare.actions))
