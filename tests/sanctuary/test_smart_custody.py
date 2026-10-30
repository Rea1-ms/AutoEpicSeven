"""Replay recognized sanctuary frames without connecting to a game device."""

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.support.offline import ControlledClock, record_action
from module.config import server

server.set_lang('global_cn')
from module.exception import ScriptError  # noqa: E402
from tasks.sanctuary.monthly import SanctuaryMonthlyMixin  # noqa: E402
from tasks.sanctuary.monthly_deposit import deposit_tiers_increased  # noqa: E402
from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import CUSTODY, PURIFY  # noqa: E402


class SmartReplay(SanctuaryMonthlyMixin):
    """Provide explicit UI observations; clicks never advance the frame stream."""

    def __init__(self, frames, clock):
        self.frames = iter(frames)
        self.clock = clock
        self.current = {}
        self.frames_read = 0
        self.actions = []
        self.device = SimpleNamespace(image=None, screenshot=self.screenshot,
                                      click=self.click, click_record_clear=Mock())

    def screenshot(self):
        try:
            self.current = next(self.frames)
        except StopIteration:
            raise AssertionError('Smart custody did not finish within the replay') from None
        self.frames_read += 1
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
    def replay(self, frames, expected_status, expected_actions, *, settle=None, custody_click=True):
        with ControlledClock() as clock, ExitStack() as patches:
            task = SmartReplay(frames, clock)
            patches.enter_context(patch.object(CUSTODY, 'match_color',
                                               side_effect=lambda *a, **k: task.current.get('CUSTODY', False)))
            patches.enter_context(patch.object(PURIFY, 'match_template_luma', return_value=True))
            patches.enter_context(patch('tasks.sanctuary.monthly.match_deposit_tiers',
                                       side_effect=lambda image: task.current.get('deposit_tiers', (None,) * 5)))
            if not custody_click:
                appear_then_click = task.appear_then_click
                patches.enter_context(patch.object(task, 'appear_then_click', side_effect=lambda button, **kwargs:
                                                   False if str(button) == 'CUSTODY'
                                                   else appear_then_click(button, **kwargs)))
            if settle is not None:
                patches.enter_context(patch.object(task, '_wait_monthly_custody_settle', return_value=settle))
            result = task._monthly_purify_smart()
            self.assertEqual(result, expected_status)
            self.assertEqual(task.actions, expected_actions)
            return task

    def settle(self, frames, *, deposit_before=None):
        with ControlledClock() as clock:
            task = SmartReplay(frames, clock)
            with patch('tasks.sanctuary.monthly.match_deposit_tiers',
                       side_effect=lambda image: task.current.get('deposit_tiers', (None,) * 5)):
                result = task._wait_monthly_custody_settle(Mock(), deposit_before=deposit_before)
            self.assertEqual(task.actions, [])
            return result, task

    def test_low_level_a_is_stored_without_high_value_popup(self):
        # Two unchanged result frames after the custody click must not authorize
        # purification. Only a stable increase in deposit tiers settles it.
        frames = ([reward()] * 6
                  + [reward(CUSTODY=False, deposit_tiers=('A', None, None, None, None))] * 2
                  + [{'full': True}])
        self.replay(frames, 'full', ['CUSTODY'])

    def test_low_level_b_is_refreshed_then_a_is_stored(self):
        frames = ([reward('B')] * 3 + [reward(balance=90)] * 5
                  + [reward(CUSTODY=False, deposit_tiers=('A', None, None, None, None))] * 2
                  + [{'full': True}])
        self.replay(frames, 'full', ['PURIFY', 'CUSTODY'])

    def test_higher_levels_keep_game_popup_policy(self):
        for level in (2, 3, 6, 11):
            with self.subTest(level=level):
                frames = ([reward(level=level)] * 3 + [{'popup': True}]
                          + [reward('S', level=level)] * 2
                          + [reward('S', level=level, CUSTODY=False,
                                    deposit_tiers=('S', None, None, None, None))] * 2
                          + [{'full': True}])
                self.replay(frames, 'full', ['PURIFY', 'POPUP_CANCEL', 'CUSTODY'])

    def test_level_up_rereads_level_before_accepting_a(self):
        frames = ([reward('B', LEVEL_UP=True)] * 3 + [reward(level=2)] * 3 + [{'full': True}])
        self.replay(frames, 'full', ['LEVEL_UP', 'PURIFY'])

    def test_already_stored_a_is_not_stored_twice(self):
        stored = reward(CUSTODY=False, deposit_tiers=('A', None, None, None, None))
        frames = [reward()] * 5 + [stored] * 5 + [{'full': True}]
        self.replay(frames, 'full', ['CUSTODY', 'PURIFY'])

    def test_a_is_preserved_even_when_purify_resources_are_exhausted(self):
        frames = ([reward(balance=0)] * 5
                  + [reward(balance=0, CUSTODY=False, deposit_tiers=('A', None, None, None, None))] * 5)
        self.replay(frames, 'exhausted', ['CUSTODY'])

    def test_failed_custody_keeps_purification_blocked(self):
        frames = [reward()] * 5 + [reward('B', CUSTODY=False)] * 3 + [{'full': True}]
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

    def test_deposit_increase_accepts_one_known_tier_in_an_empty_position(self):
        before = ('A', None, 'B', None, None)
        for tier in ('S', 'A', 'B'):
            for slot in (1, 3, 4):
                with self.subTest(tier=tier, slot=slot):
                    after = list(before)
                    after[slot] = tier
                    self.assertTrue(deposit_tiers_increased(before, tuple(after)))

    def test_deposit_increase_rejects_missing_replaced_unchanged_or_multiple_tiers(self):
        before = ('S', 'A', None, None, None)
        invalid = {
            'unchanged': before,
            'lost_existing': (None, 'A', 'B', 'B', None),
            'replaced_existing': ('B', 'A', 'S', None, None),
            'moved_existing': (None, 'A', 'S', 'B', None),
            'multiple_added': ('S', 'A', 'B', 'S', None),
            'unknown_tier_added': ('S', 'A', 'SS', None, None),
        }
        for observation, after in invalid.items():
            with self.subTest(observation=observation):
                self.assertFalse(deposit_tiers_increased(before, after))

    def test_custody_settle_accepts_two_stable_deposit_increase_frames(self):
        before = ('B', None, None, None, None)
        for tier in ('S', 'A', 'B'):
            with self.subTest(tier=tier):
                after = ('B', tier, None, None, None)
                result, task = self.settle([{'deposit_tiers': after}] * 2, deposit_before=before)
                self.assertTrue(result)
                self.assertEqual(task.frames_read, 2)

    def test_custody_settle_rejects_a_single_flickering_increase(self):
        before = ('B', None, None, None, None)
        after = ('B', 'S', None, None, None)
        frames = [{'deposit_tiers': after}] + [{'deposit_tiers': before}] * 15
        result, task = self.settle(frames, deposit_before=before)
        self.assertFalse(result)
        self.assertEqual(task.frames_read, 16)

    def test_custody_settle_restarts_stability_when_added_tier_changes(self):
        before = ('B', None, None, None, None)
        frames = [{'deposit_tiers': ('B', tier, None, None, None)} for tier in ('S', 'A', 'A')]
        result, task = self.settle(frames, deposit_before=before)
        self.assertTrue(result)
        self.assertEqual(task.frames_read, 3)

    def test_custody_settle_keeps_claimed_confirmation(self):
        for before in (None, ('B', None, None, None, None)):
            with self.subTest(baseline=before):
                result, task = self.settle([{'claimed': True}], deposit_before=before)
                self.assertTrue(result)
                self.assertEqual(task.frames_read, 1)

    def test_duplicate_custody_notice_cannot_confirm_a_new_deposit(self):
        before = ('B', None, None, None, None)
        result, _ = self.settle([{'ALREADY_STORED': True, 'deposit_tiers': before}] * 16,
                                deposit_before=before)
        self.assertFalse(result)

    def test_high_level_custody_increase_resumes_without_a_stored_marker(self):
        before = ('B', None, None, None, None)
        after = ('B', 'S', None, None, None)
        frames = ([reward(level=11, deposit_tiers=before)] * 3 + [{'popup': True}]
                  + [reward('S', level=11, deposit_tiers=before)] * 2
                  + [reward('S', level=11, CUSTODY=False, deposit_tiers=after)] * 2
                  + [reward('B', level=11, CUSTODY=False, deposit_tiers=after)] * 3
                  + [{'full': True}])
        self.replay(frames, 'full', ['PURIFY', 'POPUP_CANCEL', 'CUSTODY', 'PURIFY'])

    def test_delayed_deposit_increase_recovers_after_timeout_with_a_gray_button(self):
        before = ('B', None, None, None, None)
        after = ('B', 'S', None, None, None)
        # Sixteen unchanged screenshots exhaust both parts of Timer(5, count=15).
        # The pending transaction must retain its pre-click observation even
        # though CUSTODY is gray when the deposit update finally arrives.
        frames = ([reward(level=11, deposit_tiers=before)] * 3 + [{'popup': True}]
                  + [reward('S', level=11, deposit_tiers=before)] * 2
                  + [reward('S', level=11, CUSTODY=False, deposit_tiers=before)] * 16
                  + [reward('S', level=11, CUSTODY=False, deposit_tiers=after)] * 3
                  + [reward('B', level=11, CUSTODY=False, deposit_tiers=after)] * 3
                  + [{'full': True}])
        task = self.replay(frames, 'full', ['PURIFY', 'POPUP_CANCEL', 'CUSTODY', 'PURIFY'])
        self.assertGreater(task.frames_read, 21)

    def test_unconfirmed_or_failed_custody_never_resumes_purify(self):
        before = ('B', None, None, None, None)
        for click_succeeds in (True, False):
            with self.subTest(click_succeeds=click_succeeds):
                first = ([reward(level=11, deposit_tiers=before)] * 3 + [{'popup': True}]
                         + [reward('S', level=11, deposit_tiers=before)] * 2)
                remaining = [reward('S', level=11, CUSTODY=not click_succeeds,
                                    deposit_tiers=before)] * 150
                actions = ['PURIFY', 'POPUP_CANCEL'] + (['CUSTODY'] if click_succeeds else [])
                self.replay(first + remaining, 'failed', actions, custody_click=click_succeeds)

    def test_fixed_tier_custody_passes_the_observation_taken_before_the_click(self):
        before = ('S', 'B', None, None, None)
        with ControlledClock() as clock:
            task = SmartReplay([reward('S')] * 2 + [{'full': True}], clock)
            task.config = SimpleNamespace(SanctuaryMonthly_RewardTier='S')

            def observe_before_click(image):
                self.assertNotIn('CUSTODY', task.actions)
                return before

            with patch.object(CUSTODY, 'match_color', return_value=True), \
                    patch.object(PURIFY, 'match_template_luma', return_value=True), \
                    patch('tasks.sanctuary.monthly.match_deposit_tiers',
                          side_effect=observe_before_click) as observe, \
                    patch.object(task, '_wait_monthly_custody_settle', return_value=True) as settle:
                self.assertEqual(task._monthly_purify(), 'full')
            self.assertEqual(task.actions, ['CUSTODY'])
            self.assertEqual(observe.call_count, 2)
            settle.assert_called_once()
            args, kwargs = settle.call_args
            self.assertEqual(args[1] if len(args) > 1 else kwargs['deposit_before'], before)

    def test_low_level_custody_cleared_reward_resumes_without_a_notice(self):
        after = ('A', None, None, None, None)
        cleared = reward(tier=None, CUSTODY=False, deposit_tiers=after)
        frames = [reward()] * 5 + [cleared] * 5 + [{'full': True}]
        self.replay(frames, 'full', ['CUSTODY', 'PURIFY'])

    def test_preclick_tier_flicker_is_stabilized_before_custody(self):
        before = ('B', None, None, None, None)
        after = ('B', 'S', None, None, None)
        frames = ([reward(level=11)] * 3 + [{'popup': True}]
                  + [reward('S', level=11, deposit_tiers=(None,) * 5)]
                  + [reward('S', level=11, deposit_tiers=before)] * 2
                  + [reward('S', level=11, CUSTODY=False, deposit_tiers=after)] * 2
                  + [{'full': True}])
        task = self.replay(frames, 'full', ['PURIFY', 'POPUP_CANCEL', 'CUSTODY'])
        self.assertEqual(task.frames_read, 10)


if __name__ == '__main__':
    unittest.main()
