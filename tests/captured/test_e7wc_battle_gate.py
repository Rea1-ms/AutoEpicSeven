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
from tasks.activity.legacy.e7wc_battle_gate_2026_09_12.e7wc_battle_gate import (
    E7wcBattleGate, E7WC_BATTLE_GATE_CHECK, E7WC_BATTLE_GATE_SELECTED,
    E7WC_LEFT_REWARD_AVAILABLE, E7WC_RIGHT_REWARD_AVAILABLE,
)

SCREENSHOTS = input_root('e7wc_battle_gate')
KOHARU = input_root("koharu_raffle") / "available.png"
EVENT_ID = "e7wc_battle_gate_2026_09_10"
NOW = datetime.fromisoformat("2026-09-18T12:00:00+08:00")


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
