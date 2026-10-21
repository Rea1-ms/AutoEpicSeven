# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Offline Koharu task claims, event dispatch, and battle-triggered rechecks."""
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

WORKTREE = Path(__file__).resolve().parents[2]

import module.config.server as server
from module.ocr.ocr import Ocr
from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_LIST
from tasks.activity.legacy.koharu_raffle_2026_09_17.koharu_raffle import (
    KoharuRaffle, CHUN_ALL_TASK_DONE, CHUN_GATE_CHECK, CHUN_GATE_SELECTED,
    CHUN_TASK_REWARD_NONE, CHUN_TASK_REWARD_PENDING,
)

EVENT_ID = 'koharu_raffle_2026_09_17'
NOW = datetime.fromisoformat('2026-09-19T12:00:00+08:00')
SCREENSHOTS = input_root('koharu_raffle')


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.claim = object.__new__(KoharuRaffle)
        self.claim.device = SimpleNamespace(image=None, stuck_record_add=lambda button: None)

    def test_available_screenshot_confirms_page_sidebar_and_only_claim_state(self):
        self.claim.device.image = load_image(str(SCREENSHOTS / 'available.png'))
        self.assertTrue(self.claim.match_template_color(CHUN_GATE_CHECK))
        self.assertTrue(self.claim._activity_selected(CHUN_GATE_SELECTED))
        self.assertTrue(self.claim.match_template_color(CHUN_TASK_REWARD_PENDING))
        self.assertFalse(self.claim.match_template_color(CHUN_TASK_REWARD_NONE))
        self.assertFalse(self.claim.match_template_color(CHUN_ALL_TASK_DONE))

    def test_first_row_state_templates_are_mutually_exclusive(self):
        states = (CHUN_TASK_REWARD_PENDING, CHUN_TASK_REWARD_NONE, CHUN_ALL_TASK_DONE)
        for source in states:
            self.claim.device.image = load_image(source.matched_button.file)
            for target in states:
                self.assertEqual(self.claim.match_template_color(target), source is target, (source.name, target.name))

    def test_claimed_row_lower_in_scrolled_list_is_not_all_done(self):
        self.claim.device.image = load_image(str(SCREENSHOTS / 'scrolled_claimed_row.png'))
        self.assertTrue(self.claim.match_template_color(CHUN_GATE_CHECK))
        self.assertFalse(self.claim.match_template_color(CHUN_ALL_TASK_DONE))

    def test_other_event_cannot_be_claimed(self):
        self.claim.device.image = load_image(str(input_root("e7wc_battle_gate") / "battle_gate_available.png"))
        self.assertFalse(self.claim.match_template_color(CHUN_GATE_CHECK))
        self.assertFalse(self.claim._activity_selected(CHUN_GATE_SELECTED))

    def test_real_sidebar_ocr_finds_raffle(self):
        image = load_image(str(SCREENSHOTS / 'available.png'))
        rows = Ocr(COMMON_ACTIVITY_LIST, lang='cn').detect_and_ocr(image)
        self.assertEqual(len([r for r in rows if '收集抽奖券' in r.ocr_text]), 1)
