# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline OCR and state replays for server-repeat progress scheduling."""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
import module.config.server as server
server.set_lang("global_cn")
from module.base.utils import load_image
from tasks.dungeon.assets.assets_dungeon_repeat_settlement import (
    OCR_SETTLEMENT_PROGRESS, OCR_SETTLEMENT_TIME_SPENT,
)
from tasks.dungeon.dungeon import Combat
from tasks.dungeon.execute import CombatExecuteMixin
from tasks.dungeon.repeat_progress import (
    RepeatCombatProgress, RepeatElapsedOcr, RepeatProgressOcr,
    read_repeat_combat_progress,
)
from tests.support.history_repeat_settlement_return import Watch, frame, WINDOW, CLOSE, SETTLE, OVER
from tests.support.history_september_runtime_fixes import timers

CHECK = "REPEAT_COMBAT_CHECK"
INTERRUPT = "SETTLEMENT_INTERRUPT"
X = "AD_BUFF_X_CLOSE"
NOW = datetime(2026, 9, 19, 20)
SAMPLE = RepeatCombatProgress(3, 200, timedelta(minutes=4, seconds=45))


class ProgressWatch(Watch):
    _is_repeat_combat_running = CombatExecuteMixin._is_repeat_combat_running

    def __init__(self, frames, expected=None):
        super().__init__(frames)
        self.session = {"active": True, "mode": "repeat_server", "domain": "UrgentTasks", "resume_normal": False}
        if expected is not None:
            self.session["expected_finish_at"] = expected
        self.delays = []
        self.config = SimpleNamespace(task_delay=lambda **kwargs: self.delays.append(kwargs))
        self.COMBAT_CHECK_SIMILARITY = .8
        self.COMBAT_BACKGROUND_CHECK_MINUTES = 1


def run_progress(frames, expected=None):
    server.set_lang("global_cn")
    watch = ProgressWatch(frames, expected=expected)

    class Clock(datetime):
        @classmethod
        def now(cls):
            return NOW + timedelta(seconds=watch.frame["at"])

    with patch("tasks.dungeon.repeat.Timer", timers(lambda: watch.frame["at"])), patch(
        "tasks.dungeon.repeat.datetime", Clock
    ), patch("tasks.dungeon.repeat.read_repeat_combat_progress", side_effect=lambda image, lang: image.get("progress", SAMPLE)) as ocr:
        result = watch._watch_repeat_combat()
        watch._delay_running_repeat_combat()
    return watch, result, ocr.call_count


class ParsingTests(unittest.TestCase):
    def test_counter_accepts_label_and_bare_digits(self):
        ocr = RepeatProgressOcr(OCR_SETTLEMENT_PROGRESS, lang="cn")
        for text in ("战斗进度：3/200", "战斗进度: 3 / 200", "3／200", "3/200"):
            with self.subTest(text=text):
                self.assertEqual(ocr.format_result(text), (3, 200))

    def test_counter_rejects_invalid_data(self):
        ocr = RepeatProgressOcr(OCR_SETTLEMENT_PROGRESS, lang="cn")
        for text in ("", "3200", "-1/200", "201/200", "3/0", "3/200/500", "3/200x", "3/2O0"):
            with self.subTest(text=text):
                self.assertIsNone(ocr.format_result(text))

    def test_zero_and_full_counters_are_valid_samples(self):
        ocr = RepeatProgressOcr(OCR_SETTLEMENT_PROGRESS, lang="cn")
        self.assertEqual(ocr.format_result("0/200"), (0, 200))
        self.assertEqual(ocr.format_result("200/200"), (200, 200))

    def test_elapsed_is_strict_hh_mm_ss(self):
        ocr = RepeatElapsedOcr(OCR_SETTLEMENT_TIME_SPENT, lang="cn")
        self.assertEqual(ocr.format_result("00:04:45"), timedelta(seconds=285))
        self.assertEqual(ocr.format_result("01：02：03"), timedelta(seconds=3723))
        self.assertEqual(ocr.format_result("00:00:00"), timedelta())
        for text in ("", "04:45", "00:64:45", "00:04:99", "-1:04:45", "00:04:45x"):
            with self.subTest(text=text):
                self.assertIsNone(ocr.format_result(text))

    def test_estimate_uses_elapsed_per_completed_battle(self):
        self.assertEqual(SAMPLE.estimated_remaining, timedelta(hours=5, minutes=11, seconds=55))
        self.assertEqual(RepeatCombatProgress(3, 5, timedelta(seconds=10)).estimated_remaining, timedelta(seconds=7))

    def test_no_estimate_before_first_completion_or_at_full_counter(self):
        for sample in (RepeatCombatProgress(0, 200, timedelta(seconds=10)), RepeatCombatProgress(200, 200, timedelta(hours=1)), RepeatCombatProgress(3, 200, timedelta())):
            self.assertIsNone(sample.estimated_remaining)

    def test_real_supplied_asset_crops_in_both_clients(self):
        for lang in ("cn", "global_cn"):
            with self.subTest(lang=lang):
                server.set_lang(lang)
                image = np.maximum(load_image(OCR_SETTLEMENT_PROGRESS.buttons[0].file), load_image(OCR_SETTLEMENT_TIME_SPENT.buttons[0].file))
                self.assertEqual(read_repeat_combat_progress(image, "cn"), SAMPLE)
        server.set_lang("global_cn")

    def test_blank_image_does_not_become_zero_progress(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertIsNone(read_repeat_combat_progress(image, "cn"))


class ProgressFlowTests(unittest.TestCase):
    def test_adopted_run_opens_reads_closes_and_preserves_session(self):
        watch, result, reads = run_progress([
            frame(0, CHECK), frame(.2, WINDOW, INTERRUPT, X), frame(.4, CHECK),
        ])
        self.assertEqual(result, "running")
        self.assertEqual(watch.actions, [(0, CHECK), (.2, X)])
        self.assertEqual(reads, 1)
        self.assertTrue(watch.session["active"])
        self.assertFalse(watch.session["resume_normal"])
        self.assertEqual(watch.session["domain"], "UrgentTasks")
        self.assertEqual(watch.session["progress"]["elapsed_seconds"], 285)
        expected = NOW + timedelta(hours=5, minutes=12, seconds=55)
        self.assertEqual(watch.delays, [{"target": expected}])

    def test_unexpired_menu_estimate_avoids_extra_window(self):
        expected = (NOW + timedelta(hours=1)).isoformat()
        watch, result, reads = run_progress([frame(0, CHECK)], expected)
        self.assertEqual(result, "running")
        self.assertEqual(watch.actions, [])
        self.assertEqual(reads, 0)
        self.assertEqual(watch.delays, [{"target": datetime.fromisoformat(expected)}])

    def test_expired_and_invalid_estimates_are_refreshed(self):
        for expected in ((NOW - timedelta(minutes=1)).isoformat(), "bad-date", "2026-09-20T10:00:00+08:00"):
            with self.subTest(expected=expected):
                watch, result, reads = run_progress([
                    frame(0, CHECK), frame(.2, WINDOW, INTERRUPT, X), frame(.4, CHECK),
                ], expected)
                self.assertEqual(result, "running")
                self.assertEqual(reads, 1)
                self.assertIn("target", watch.delays[0])

    def test_already_open_detail_does_not_settle_or_interrupt(self):
        watch, result, _ = run_progress([
            frame(0, WINDOW, INTERRUPT, X, SETTLE), frame(.3, CHECK),
        ])
        self.assertEqual(result, "running")
        self.assertEqual(watch.actions, [(0, X)])

    def test_dropped_open_and_close_clicks_retry_on_fresh_frames(self):
        watch, result, reads = run_progress([
            frame(0, CHECK), frame(.2, CHECK), frame(1.1, CHECK),
            frame(1.3, WINDOW, INTERRUPT, X), frame(1.5, WINDOW, INTERRUPT, X),
            frame(2.4, WINDOW, INTERRUPT, X), frame(2.6, CHECK),
        ])
        self.assertEqual(result, "running")
        self.assertEqual(watch.actions, [(0, CHECK), (1.1, CHECK), (1.3, X), (2.4, X)])
        self.assertEqual(reads, 1)

    def test_loading_detail_does_not_report_the_known_run_lost(self):
        watch, result, _ = run_progress([
            frame(0, CHECK), frame(.2), frame(1.4), frame(2.5),
            frame(3, WINDOW, INTERRUPT, X), frame(3.2, CHECK),
        ])
        self.assertEqual(result, "running")
        self.assertEqual(watch.index, 5)
        self.assertEqual(watch.actions, [(0, CHECK), (3, X)])
        self.assertTrue(watch.session["active"])

    def test_invalid_ocr_retries_then_closes_without_inventing_progress(self):
        watch, result, reads = run_progress([
            frame(0, WINDOW, INTERRUPT, X, progress=None),
            frame(1, WINDOW, INTERRUPT, X, progress=None),
            frame(3.1, WINDOW, INTERRUPT, X, progress=None), frame(3.3, CHECK),
        ])
        self.assertEqual(result, "running")
        self.assertEqual(reads, 3)
        self.assertEqual(watch.actions, [(3.1, X)])
        self.assertNotIn("progress", watch.session)
        self.assertEqual(watch.delays, [{"minute": 1}])

    def test_invalid_ocr_preserves_existing_future_estimate(self):
        expected = (NOW + timedelta(hours=1)).isoformat()
        watch, _, _ = run_progress([
            frame(0, WINDOW, INTERRUPT, X, progress=None),
            frame(3.1, WINDOW, INTERRUPT, X, progress=None), frame(3.3, CHECK),
        ], expected)
        self.assertEqual(watch.session["expected_finish_at"], expected)

    def test_zero_or_full_progress_keeps_active_and_uses_short_polling(self):
        for sample in (RepeatCombatProgress(0, 200, timedelta(seconds=10)), RepeatCombatProgress(200, 200, timedelta(hours=1)), RepeatCombatProgress(3, 200, timedelta())):
            with self.subTest(sample=sample):
                watch, result, _ = run_progress([
                    frame(0, WINDOW, INTERRUPT, X, progress=sample), frame(.2, CHECK),
                ], (NOW + timedelta(hours=1)).isoformat())
                self.assertEqual(result, "running")
                self.assertTrue(watch.session["active"])
                self.assertNotIn("expected_finish_at", watch.session)
                self.assertEqual(watch.delays, [{"minute": 1}])

    def test_completion_during_ocr_retry_switches_to_settlement(self):
        watch, result, _ = run_progress([
            frame(0, WINDOW, INTERRUPT, X, progress=None), frame(.2, WINDOW, SETTLE),
            frame(.4, WINDOW, SETTLE), frame(.6, WINDOW, CLOSE), frame(.8), frame(1.3),
        ])
        self.assertEqual(result, "finished")
        self.assertEqual(watch.actions, [(.4, SETTLE), (.6, CLOSE)])

    def test_completed_run_does_not_read_progress_or_click_generic_x(self):
        watch, result, reads = run_progress([
            frame(0, OVER), frame(.2, WINDOW, SETTLE, X), frame(.4, WINDOW, CLOSE, X),
            frame(.6), frame(1.1),
        ])
        self.assertEqual(result, "finished")
        self.assertEqual(reads, 0)
        self.assertEqual(watch.actions, [(0, OVER), (.2, SETTLE), (.4, CLOSE)])

    def test_unknown_screen_after_close_does_not_finish_session(self):
        watch, result, _ = run_progress([
            frame(0, WINDOW, INTERRUPT, X), frame(.2, page=None),
        ])
        self.assertEqual(result, "running")
        self.assertTrue(watch.session["active"])

    def test_missing_x_does_not_press_interrupt(self):
        watch, result, reads = run_progress([frame(0, WINDOW, INTERRUPT)])
        self.assertEqual(result, "running")
        self.assertEqual(watch.actions, [])
        self.assertEqual(reads, 1)


class EntryTests(unittest.TestCase):
    def test_detail_is_a_valid_adoption_context_before_navigation(self):
        combat = object.__new__(Combat)
        combat._combat_runtime_active = lambda: False
        combat._uses_server_repeat_combat = lambda: True
        combat._is_repeat_result_window = lambda: True
        combat.ui_get_current_page = Mock(side_effect=AssertionError("Must not route through an open detail"))
        combat._prepare_background_repeat_check_context()
        combat.ui_get_current_page.assert_not_called()

    def test_task_keeps_open_running_detail_for_watcher(self):
        server.set_lang("global_cn")
        combat = object.__new__(Combat)
        combat.config = SimpleNamespace(Emulator_PackageName="com.stove.epic7.google")
        combat.device = SimpleNamespace(image=object(), app_is_running=lambda: True)
        combat._prepare_background_repeat_check_context = Mock()
        combat._adopt_existing_background_repeat_combat = Mock()
        combat._combat_runtime_active = lambda: True
        combat._combat_runtime_session = lambda: {"active": True, "state": "running"}
        combat._is_repeat_result_window = lambda: True
        combat.is_in_main = lambda interval: False
        combat.ui_goto_main = Mock(side_effect=AssertionError("Detail must be handled before navigation"))
        combat._watch_repeat_combat = Mock(return_value="running")
        combat._delay_running_repeat_combat = Mock()
        self.assertTrue(combat.run())
        combat.ui_goto_main.assert_not_called()
        combat._watch_repeat_combat.assert_called_once_with(skip_first_screenshot=True)
        combat._delay_running_repeat_combat.assert_called_once()
