"""Replay recognized sanctuary frames without connecting to a game device."""

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.support.exploration import ControlledClock, record_action
from module.exception import ScriptError
from tasks.sanctuary.monthly import SanctuaryMonthlyMixin
from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import CUSTODY, PURIFY


class SmartReplay(SanctuaryMonthlyMixin):
    """Provide explicit UI observations; clicks never advance the frame stream."""

    def __init__(self, frames, clock):
        self.frames = iter(frames)
        self.clock = clock
        self.current = {}
        self.actions = []
        self.device = SimpleNamespace(image=None, screenshot=self.screenshot,
                                      click=self.click, click_record_clear=Mock())

    def screenshot(self):
        try:
            self.current = next(self.frames)
        except StopIteration:
            raise AssertionError('Smart custody did not finish within the replay') from None
        self.clock.advance(1)

    def click(self, button):
        self.actions.append(record_action(str(button)))

    def appear(self, button, **kwargs):
        return bool(self.current.get(str(button), False))

    def appear_then_click(self, button, **kwargs):
        if self.appear(button):
            self.click(button)
            return True
        return False

    def interval_is_reached(self, *args, **kwargs):
        return True

    def interval_reset(self, *args, **kwargs):
        pass

    def _ocr_lang(self):
        return 'cn'

    def _ocr_purify_times(self, *args, **kwargs):
        balance = self.current.get('balance', 100)
        return balance, 10 - balance, 10, 'not_full'

    def _ocr_heart_level(self, _ocr):
        return self.current.get('level', 1)

    def _detect_current_reward_tier(self, _ocr):
        return self.current.get('tier')

    def _is_monthly_claimed(self):
        return bool(self.current.get('claimed'))

    def _is_monthly_deposit_box_full(self):
        return bool(self.current.get('full'))

    def _monthly_deposit_box_ready(self, _timer):
        return not self.current.get('full') and not self.current.get('box_unknown')

    def handle_popup_cancel(self, **kwargs):
        if self.current.get('popup'):
            self.actions.append(record_action('POPUP_CANCEL'))
            return True
        return False

    def handle_touch_to_close(self, **kwargs):
        return False

    def ui_additional(self):
        return False

    def handle_network_error(self):
        return False

    def _wait_monthly_level_up_settle(self):
        return True


def reward(tier='A', **changes):
    return {'level': 1, 'tier': tier, 'balance': 100, 'CUSTODY': True, **changes}


class SmartCustodyTests(unittest.TestCase):
    def replay(self, frames, expected_status, expected_actions, *, settle=None):
        with ControlledClock() as clock, ExitStack() as patches:
            task = SmartReplay(frames, clock)
            patches.enter_context(patch.object(CUSTODY, 'match_color',
                                               side_effect=lambda *a, **k: task.current.get('CUSTODY', False)))
            patches.enter_context(patch.object(PURIFY, 'match_template_luma', return_value=True))
            if settle is not None:
                patches.enter_context(patch.object(task, '_wait_monthly_custody_settle', return_value=settle))
            result = task._monthly_purify_smart()
            self.assertEqual(result, expected_status)
            self.assertEqual(task.actions, expected_actions)
            return task

    def test_low_level_a_is_stored_without_high_value_popup(self):
        # Two unchanged result frames after the custody click must not authorize
        # purification. Only the later stored marker settles that transaction.
        frames = [reward()] * 6 + [reward(ALREADY_STORED=True), {'full': True}]
        self.replay(frames, 'full', ['CUSTODY'])

    def test_low_level_b_is_refreshed_then_a_is_stored(self):
        frames = ([reward('B')] * 3 + [reward(balance=90)] * 4
                  + [reward(ALREADY_STORED=True), {'full': True}])
        self.replay(frames, 'full', ['PURIFY', 'CUSTODY'])

    def test_higher_levels_keep_game_popup_policy(self):
        for level in (2, 3, 6, 11):
            with self.subTest(level=level):
                frames = ([reward(level=level)] * 3 + [{'popup': True}]
                          + [reward('S', level=level), reward('S', ALREADY_STORED=True), {'full': True}])
                self.replay(frames, 'full', ['PURIFY', 'POPUP_CANCEL', 'CUSTODY'])

    def test_level_up_rereads_level_before_accepting_a(self):
        frames = ([reward('B', LEVEL_UP=True)] * 3 + [reward(level=2)] * 3 + [{'full': True}])
        self.replay(frames, 'full', ['LEVEL_UP', 'PURIFY'])

    def test_already_stored_a_is_not_stored_twice(self):
        frames = [reward(ALREADY_STORED=True, CUSTODY=False)] * 3 + [{'full': True}]
        self.replay(frames, 'full', ['PURIFY'])

    def test_a_is_preserved_even_when_purify_resources_are_exhausted(self):
        frames = ([reward(balance=0)] * 4 + [reward(balance=0, ALREADY_STORED=True)]
                  + [reward(balance=0, ALREADY_STORED=True, CUSTODY=False)] * 3)
        self.replay(frames, 'exhausted', ['CUSTODY'])

    def test_failed_custody_keeps_purification_blocked(self):
        frames = [reward()] * 4 + [reward('B', CUSTODY=False)] * 3 + [{'full': True}]
        self.replay(frames, 'full', ['CUSTODY'], settle=False)

    def test_flickering_a_does_not_override_stable_b(self):
        frames = [reward('B'), reward(), reward(), reward('B'), reward('B'), reward('B'), {'full': True}]
        self.replay(frames, 'full', ['PURIFY'])

    def test_unknown_level_or_reward_cannot_authorize_refresh(self):
        for unknown in (reward(level=None), reward(tier=None)):
            with self.subTest(observation=unknown), ControlledClock() as clock:
                task = SmartReplay([unknown] * 35, clock)
                with patch.object(PURIFY, 'match_template_luma', return_value=True), \
                        patch.object(CUSTODY, 'match_color', return_value=True):
                    with self.assertRaisesRegex(ScriptError, 'level|tier'):
                        task._monthly_purify_smart()
                self.assertEqual(task.actions, [])


if __name__ == '__main__':
    unittest.main()
