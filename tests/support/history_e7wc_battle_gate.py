# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline screenshots, single-click claiming, and overseas calendar regressions."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

from tasks.activity.legacy.e7wc_battle_gate_2026_09_12.e7wc_battle_gate import (
    E7wcBattleGate, E7WC_BATTLE_GATE_CHECK, E7WC_LEFT_REWARD_AVAILABLE, E7WC_RIGHT_REWARD_AVAILABLE,
)
from tasks.activity.assets.assets_activity_special_26_9_12 import E7WC_BATTLE_GATE_SELECTED
from tasks.activity.common_activity import CommonActivityBatch

# Preserve the three-event sidebar contract only in historical replay inputs.
# Production must never regain the retired event through this fixture table.
HISTORICAL_ACTIVITIES = {
    **CommonActivityBatch.ACTIVITIES,
    "e7wc_battle_gate": ("激战门", E7WC_BATTLE_GATE_SELECTED, E7wcBattleGate),
}

SCREENSHOTS = Path(__file__).parent / "screenshots/e7wc_battle_gate"
KOHARU = Path("C:/Users/rea1m/Documents/MuMu共享文件夹/Screenshots/MuMu-20260917-173824-447.png")
EVENT_ID = "e7wc_battle_gate_2026_09_10"
NOW = datetime.fromisoformat("2026-09-18T12:00:00+08:00")


class Config:
    SpecialActivity_BuyHucheMysticMedals = False
    Emulator_PackageName = "com.stove.epic7.google"
    Emulator_GameLanguage = "auto"
    Scheduler_ServerUpdate = "02:00"
    LimitedActivity_GetE7wcBattleGateReward = True

    def __init__(self):
        self.values = {}
        self.delays = []

    def cross_get(self, path, default=None):
        return self.values.get(path, default)

    def cross_set(self, path, value):
        self.values[path] = value

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)


class Claim(E7wcBattleGate):
    def __init__(self, frames):
        self.config = Config()
        self.activity_id = EVENT_ID
        self.frames = frames
        self.frame = -1
        self.clicks = []
        self.popups = 0
        self.routes = []
        self.device = SimpleNamespace(screenshot=self.screenshot, click=self.click,
                                      app_is_running=lambda: True)
        self.device.screenshot()

    def screenshot(self):
        self.frame += 1
        if self.frame >= len(self.frames):
            raise AssertionError("Flow failed to terminate on supplied frames")

    def click(self, button):
        self.clicks.append(button.name)

    def select_activity(self, keyword, button):
        return True

    def ui_goto(self, page, **kwargs):
        self.routes.append(page)

    def match_template_color(self, button, **kwargs):
        frame = self.frames[self.frame]
        if button is E7WC_BATTLE_GATE_CHECK:
            return frame in ("available", "received", "left", "right")
        if button is E7WC_LEFT_REWARD_AVAILABLE:
            return frame in ("available", "right")
        raise AssertionError("Unexpected template")

    def reward_received(self, reward):
        frame = self.frames[self.frame]
        return frame == "received" or (
            frame == "left" and reward is E7WC_LEFT_REWARD_AVAILABLE
        ) or (frame == "right" and reward is E7WC_RIGHT_REWARD_AVAILABLE)

    def handle_touch_to_close(self, **kwargs):
        if self.frames[self.frame] == "popup":
            self.popups += 1
            return True
        return False

    def handle_network_error(self):
        return False







__all__ = ['Claim']
