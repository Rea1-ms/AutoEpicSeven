# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline replay of server-repeat settlement returning to the shop."""
from datetime import datetime, timedelta
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

import module.config.server as server
server.set_lang("global_cn")
from tests.support.history_september_runtime_fixes import timers
from tasks.base.page import (
    page_main, page_store, page_secret_shop, page_pets, page_combat_prepare,
    page_combat_common, page_gacha,
)
from tasks.dungeon.repeat import CombatRepeatMixin

OVER = "REPEAT_COMBAT_OVER"
WINDOW = "SETTLEMENT_WINDOW_CHECK"
SETTLE = "SETTLEMENT_SETTLE"
CLOSE = "SETTLEMENT_CLOSE"
TOUCH = "TOUCH_TO_CLOSE"
PROCESSING = "SETTLEMENT_PROCESSING"


def frame(at, *assets, page=page_store, **extra):
    return {"at": at, "assets": assets, "page": page, **extra}


class Watch(CombatRepeatMixin):
    COMBAT_CHECK_SIMILARITY = 0.8
    COMBAT_WATCH_TIMEOUT_SECONDS = 20
    COMBAT_MISSING_CHECK_CONFIRM_SECONDS = 1

    def __init__(self, frames):
        self.frames, self.index = frames, -1
        self.actions, self.click_times = [], {}
        self.session = {"active": True, "expected_finish_at": (datetime.now() + timedelta(days=1)).isoformat()}
        self.ui_current = page_main  # Deliberately stale: do not trust it.
        self.device = SimpleNamespace(screenshot=self.screenshot, image=None)
        self.screenshot()

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        self.index += 1
        if self.index >= 100:
            raise AssertionError("Settlement did not finish or time out")
        if self.index == len(self.frames):
            self.frames.append({**self.frames[-1], "at": self.frames[-1]["at"] + 1})
        self.device.image = self.frame

    def _combat_runtime_session(self):
        return self.session

    def _combat_runtime_set(self, session):
        self.session = session

    def _ocr_lang(self):
        return "cn"

    def match_template_luma(self, button, similarity):
        return self.appear(button)

    def _has_repeat_combat_check(self):
        return "REPEAT_COMBAT_CHECK" in self.frame["assets"] or self.frame.get("running", False)

    def appear(self, button):
        return button.name in self.frame["assets"]

    def appear_then_click(self, button, interval):
        if not self.appear(button):
            return False
        at = self.frame["at"]
        if at - self.click_times.get(button.name, -100) < interval:
            return False
        self.click_times[button.name] = at
        self.actions.append((at, button.name))
        return True

    def _is_repeat_result_window(self):
        return WINDOW in self.frame["assets"]

    def _is_repeat_combat_over(self):
        return OVER in self.frame["assets"]

    def _is_repeat_combat_running(self):
        return self.frame.get("running", False)

    def _handle_dungeon_network_error(self, interval):
        return self.frame.get("network", False)

    def _handle_dungeon_additional(self):
        return False

    def is_in_main(self, interval=0):
        return self.frame["page"] is page_main

    def ui_page_appear(self, page, interval=0):
        assert interval == 0, "Page detection must not share click cooldowns"
        return self.frame["page"] is page

    def _is_in_dungeon_context(self):
        return self.frame.get("dungeon", False)


def run_watch(frames):
    watch = Watch(frames)
    with patch("tasks.dungeon.repeat.Timer", timers(lambda: watch.frame["at"])):
        result = watch._watch_repeat_combat()
    return watch, result


class SettlementReturnTests(unittest.TestCase):
    def test_reported_shop_sequence_finishes_without_back_navigation(self):
        watch, result = run_watch([
            frame(0, OVER), frame(.3, WINDOW, SETTLE), frame(1.4, WINDOW, SETTLE),
            frame(2.5, WINDOW, CLOSE), frame(2.7, TOUCH),
            frame(3, WINDOW, CLOSE), frame(3.6, WINDOW, CLOSE),
            frame(3.9), frame(4.4),
        ])
        self.assertEqual(result, "finished")
        self.assertEqual(watch.index, 8)
        self.assertEqual(watch.actions, [(0, OVER), (.3, SETTLE), (1.4, SETTLE),
                                         (2.5, CLOSE), (2.7, TOUCH), (3.6, CLOSE)])

    def test_other_supported_return_pages_use_existing_capability(self):
        for page in (page_main, page_secret_shop, page_pets, page_combat_common):
            with self.subTest(page=str(page)):
                watch, result = run_watch([
                    frame(0, WINDOW, CLOSE, page=page), frame(.1, WINDOW, CLOSE, page=page),
                    frame(.3, page=page, dungeon=True), frame(.8, page=page, dungeon=True),
                ])
                self.assertEqual(result, "finished")
                self.assertEqual(watch.actions, [(.1, CLOSE)])

    def test_reward_cooldown_does_not_expose_underlying_shop_as_finished(self):
        watch, result = run_watch([
            frame(0, OVER), frame(.1, WINDOW, CLOSE), frame(.2, TOUCH),
            frame(.4, TOUCH), frame(.9, TOUCH), frame(1.3), frame(1.9),
        ])
        self.assertEqual(result, "finished")
        self.assertEqual(watch.index, 6)
        self.assertEqual(watch.actions, [(0, OVER), (.1, CLOSE), (.2, TOUCH)])

    def test_delayed_result_window_resets_clean_page_confirmation(self):
        watch, result = run_watch([
            frame(0, OVER), frame(.1, WINDOW, CLOSE), frame(.2),
            frame(.4, WINDOW, CLOSE), frame(.9, WINDOW, CLOSE),
            frame(1.2, WINDOW, CLOSE), frame(1.5), frame(1.8), frame(2.1),
        ])
        self.assertEqual(result, "finished")
        self.assertEqual(watch.index, 8)

    def test_close_control_blocks_finish_even_if_window_title_is_missed(self):
        watch, result = run_watch([
            frame(0, OVER), frame(.1, CLOSE), frame(.3),
            frame(.8, CLOSE), frame(1.2, CLOSE), frame(1.5), frame(2),
        ])
        self.assertEqual(result, "finished")
        self.assertEqual(watch.index, 6)

    def test_processing_reappears_after_close_then_resumes_settlement(self):
        watch, result = run_watch([
            frame(0, OVER), frame(.1, CLOSE), frame(.3, PROCESSING),
            frame(.9, PROCESSING), frame(1.5, CLOSE), frame(1.8), frame(2.3),
        ])
        self.assertEqual(result, "finished")
        self.assertEqual(watch.index, 6)

    def test_completed_marker_reappears_and_must_be_settled(self):
        watch, result = run_watch([
            frame(0, OVER), frame(.1, CLOSE), frame(1.3, OVER), frame(1.4, OVER),
            frame(1.8, WINDOW, CLOSE), frame(2.1), frame(2.6),
        ])
        self.assertEqual(result, "finished")
        self.assertIn((1.4, OVER), watch.actions)
        self.assertEqual(watch.index, 6)

    def test_running_marker_prevents_clearing_a_session(self):
        watch, result = run_watch([
            frame(0, OVER), frame(.1, CLOSE), frame(.3, running=True),
        ])
        self.assertEqual(result, "running")
        self.assertEqual(watch.index, 2)

    def test_unknown_screen_never_becomes_finished(self):
        watch, result = run_watch([
            frame(0, OVER), frame(.1, CLOSE), frame(.3, page=None),
        ])
        self.assertEqual(result, "running")
        self.assertGreaterEqual(watch.frame["at"], 20)

    def test_unsupported_page_never_becomes_finished(self):
        for page in (page_gacha, page_combat_prepare):
            with self.subTest(page=str(page)):
                _, result = run_watch([
                    frame(0, OVER), frame(.1, CLOSE), frame(.3, page=page),
                ])
                self.assertEqual(result, "running")

    def test_network_error_keeps_session_active(self):
        _, result = run_watch([
            frame(0, OVER), frame(.1, CLOSE), frame(.3, network=True),
        ])
        self.assertEqual(result, "running")

    def test_existing_stale_session_can_be_reported_lost_from_shop(self):
        watch, result = run_watch([frame(0), frame(.5), frame(1.1)])
        self.assertEqual(result, "lost")
        self.assertEqual(watch.index, 2)
        self.assertEqual(watch.actions, [])

    def test_reward_popup_does_not_count_toward_missing_session_confirmation(self):
        watch, result = run_watch([
            frame(0), frame(.5, TOUCH), frame(.9, TOUCH),
            frame(1.4, TOUCH), frame(1.7), frame(2.3), frame(2.8),
        ])
        self.assertEqual(result, "lost")
        self.assertEqual(watch.index, 6)

    def test_active_run_still_returns_running_without_clicking(self):
        watch, result = run_watch([frame(0, running=True)])
        self.assertEqual(result, "running")
        self.assertEqual(watch.actions, [])
