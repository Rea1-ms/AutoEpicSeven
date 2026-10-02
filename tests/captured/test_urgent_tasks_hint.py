# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")

"""Offline regressions for Urgent Tasks prepare hints, including incomplete OCR."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
from tests.support.history_september_runtime_fixes import Frames
from tests.support.history_fixtures import build_combat
from tasks.dungeon.prepare import CombatPrepare
from tasks.dungeon.urgent_tasks import UrgentTasksNavigateMixin
from tasks.dungeon.assets.assets_dungeon_configs_urgent_tasks import OCR_URGENT_COMBAT_TIMES_REMAINING


def frame(at, hint=False, count=1, page="prepare", fast=True):
    return dict(at=at, hint=hint, count=count, page=page, fast=fast)


class Flow(Frames, UrgentTasksNavigateMixin, CombatPrepare):
    COMBAT_STATE_COLOR_THRESHOLD = 30

    def __init__(self, frames):
        Frames.__init__(self, frames)
        self.actions = []
        self.reads = []
        self.last_hint_click = -100
        self.device.click = self.click
        self.device.multi_click = self.multi_click

    def click(self, button):
        self.actions.append((self.frame['at'], button.name, 1))

    def multi_click(self, button, n, interval):
        assert not self.frame['hint'], "Counter clicked through the hint"
        self.actions.append((self.frame['at'], button.name, n))

    def interval_is_reached(self, button, interval):
        return self.frame['at'] - self.last_hint_click >= interval

    def interval_reset(self, button, interval):
        self.last_hint_click = self.frame['at']

    def match_color(self, button, threshold):
        if button is OCR_URGENT_COMBAT_TIMES_REMAINING:
            return self.frame['hint']
        return True

    def _is_urgent_tasks_target_prepare_page(self):
        return self.frame['page'] == 'prepare'

    _is_urgent_tasks_prepare_page = _is_urgent_tasks_target_prepare_page
    _is_prepare_page = _is_urgent_tasks_target_prepare_page

    def _is_urgent_tasks_detail_page(self):
        return self.frame['page'] == 'detail'

    def _urgent_tasks_difficulty(self):
        return 'Superior'

    def _urgent_tasks_remaining(self):
        self.reads.append((self.frame['at'], 'daily'))
        return 4

    def appear_then_click(self, button, **kwargs):
        if self.frame['page'] == 'detail' and button.name == 'READY_TO_FIGHT':
            self.click(button)
            return True
        return False

    def appear(self, button):
        return True

    def _is_combat_urgent_board(self):
        return False

    _is_combat_general_board = _is_combat_urgent_board
    _is_combat_season_board = _is_combat_urgent_board

    def is_in_main(self, **kwargs):
        return False

    def _handle_dungeon_additional(self):
        return False

    def _inspect_urgent_tasks_resource(self, key):
        assert not self.frame['hint'], "Resource read before closing the hint"
        self.reads.append((self.frame['at'], key))
        return SimpleNamespace(value=300)

    def _is_fast_combat_locked(self):
        return False

    def _is_fast_combat_on(self):
        return self.frame['fast']

    def _is_fast_combat_off(self):
        return not self.frame['fast']

    def _ensure_fast_combat_state(self, enabled):
        assert not self.frame['hint'], "Mode toggled before closing the hint"
        if not self.frame['fast']:
            self.actions.append((self.frame['at'], 'enable', 1))
        return self.frame['fast']

    def _ocr_fast_combat_remaining_times(self):
        assert not self.frame['hint'], "Fast charges read before closing the hint"
        self.reads.append((self.frame['at'], 'remaining'))
        return 10

    def _ocr_fast_combat_current_times(self):
        assert not self.frame['hint'], "Fast count read before closing the hint"
        self.reads.append((self.frame['at'], 'count'))
        return self.frame['count']

    def _combat_stage_stamina_cost(self):
        return 30

    def _combat_fast_count(self):
        return 4




class HintScreenshotTests(unittest.TestCase):
    def test_all_supplied_hint_screenshots_are_detected_without_ocr(self):
        names = ('normal-prepare.png', 'normal-prepare-large.png', 'superior-prepare.png',
                 'superior-prepare-large.png', 'superior-fast-on.png', 'superior-fast-off.png',
                 'prepare-times-hint.png')
        for name in names:
            with self.subTest(name=name):
                combat = build_combat(name, 'Superior')
                combat.device.screenshot = lambda: None
                combat.device.screenshot()
                clicks = []
                combat.device.click = lambda button: clicks.append(button.name)
                combat.interval_is_reached = lambda *args, **kwargs: True
                combat.interval_reset = lambda *args, **kwargs: None
                with patch('tasks.dungeon.urgent_tasks.DigitCounter.ocr_single_line',
                           side_effect=AssertionError('Hint dismissal must not depend on numeric OCR')):
                    self.assertTrue(combat._is_urgent_tasks_times_hint())
                    self.assertFalse(combat._is_urgent_tasks_prepare_ready())
                    self.assertTrue(combat._dismiss_urgent_tasks_times_hint())
                self.assertEqual(clicks, ['OCR_URGENT_COMBAT_TIMES_REMAINING'])
                combat.interval_is_reached = lambda *args, **kwargs: False
                self.assertFalse(combat._dismiss_urgent_tasks_times_hint())
                self.assertFalse(combat._is_urgent_tasks_prepare_ready())
                self.assertEqual(len(clicks), 1)

    def test_unrelated_pages_do_not_report_ready_prepare(self):
        for name in ('superior-detail.png', 'battle-auto-on.png', 'battle-result.png'):
            with self.subTest(name=name):
                combat = build_combat(name, 'Superior')
                combat.device.screenshot = lambda: None
                combat.device.screenshot()
                self.assertFalse(combat._is_urgent_tasks_prepare_ready())
