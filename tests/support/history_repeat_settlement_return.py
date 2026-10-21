# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")


"""Offline replay of server-repeat settlement returning to the shop."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

import module.config.server as server
server.set_lang("global_cn")
from tests.support.history_september_runtime_fixes import timers
from tasks.base.page import (
    page_main, page_store,
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

__all__ = ['CLOSE', 'OVER', 'SETTLE', 'WINDOW', 'Watch', 'frame']
