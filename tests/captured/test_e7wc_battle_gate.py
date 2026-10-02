# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Offline screenshots, single-click claiming, and overseas calendar regressions."""
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]

import module.config.server as server
from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_LIST
from tasks.activity.e7wc_battle_gate import (
    E7wcBattleGate, E7WC_BATTLE_GATE_CHECK, E7WC_BATTLE_GATE_SELECTED,
    E7WC_LEFT_REWARD_AVAILABLE, E7WC_RIGHT_REWARD_AVAILABLE,
)

SCREENSHOTS = input_root('e7wc_battle_gate')
KOHARU = input_root("koharu_raffle") / "available.png"
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




class ScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.available = load_image(str(SCREENSHOTS / "battle_gate_available.png"))
        cls.received = load_image(str(SCREENSHOTS / "battle_gate_received.png"))
        cls.koharu = load_image(str(KOHARU))

    def setUp(self):
        server.set_lang("global_cn")
        self.claim = object.__new__(E7wcBattleGate)
        self.claim.device = SimpleNamespace(image=None, stuck_record_add=lambda button: None)

    def test_single_green_template_matches_both_items_only_when_claimed(self):
        for image, expected in ((self.available, False), (self.received, True), (self.koharu, False)):
            self.claim.device.image = image
            for reward in (E7WC_LEFT_REWARD_AVAILABLE, E7WC_RIGHT_REWARD_AVAILABLE):
                self.assertEqual(self.claim.reward_received(reward), expected, reward.name)

    def test_page_and_selected_templates_reject_other_event(self):
        for image, expected in ((self.available, True), (self.received, True), (self.koharu, False)):
            self.claim.device.image = image
            self.assertEqual(self.claim.match_template_color(E7WC_BATTLE_GATE_CHECK), expected)
            self.assertEqual(self.claim._activity_selected(E7WC_BATTLE_GATE_SELECTED), expected)

    def test_leaf_template_matches_available(self):
        self.claim.device.image = self.available
        self.assertTrue(self.claim.match_template_color(E7WC_LEFT_REWARD_AVAILABLE))

    def test_common_sidebar_search_restores_bounds_after_success_and_exception(self):
        self.claim.device.image = self.available
        button = E7WC_BATTLE_GATE_SELECTED
        searches = [b.search for b in button.iter_buttons()]
        self.assertTrue(self.claim._activity_selected(button))
        self.assertEqual([b.search for b in button.iter_buttons()], searches)
        with patch.object(self.claim, "match_template_color", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                self.claim._activity_selected(button)
        self.assertEqual([b.search for b in button.iter_buttons()], searches)
        self.assertEqual(button.button_offset, (0, 0))
        self.assertFalse(Path("assets/global_cn/activity/special/26_9_12/E7WC_BATTLE_GATE_SELECTED.SEARCH.png").exists())

    def test_real_ocr_stable_prefix_finds_event(self):
        from module.ocr.ocr import Ocr
        for image in (self.available, self.received, self.koharu):
            rows = Ocr(COMMON_ACTIVITY_LIST, lang="cn").detect_and_ocr(image)
            matches = [r for r in rows if "激战门" in r.ocr_text]
            self.assertEqual(len(matches), 1, [r.ocr_text for r in rows])
