"""Daily free goods: captured observations and explicit interrupted purchases."""

import unittest
from contextlib import ExitStack

from module.config import server
from tests.support.interaction import scene
from tests.support.offline import ControlledClock, record_frame
from tests.support.store_purchase import (
    AVAILABLE,
    CLAIMED,
    REWARD,
    StorePurchaseReplay,
    captured_frame,
    free_plan,
)

server.set_lang('global_cn')

from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.store.assets.assets_store_items import DAILY_FREE_ITEM


def shelf(*, available=False):
    return scene('FREE_STORE_CHECK', *(['DAILY_FREE_ITEM'] if available else []))


class PurchaseReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, frames):
        return StorePurchaseReplay(frames, self.clock)

    def assert_no_confirm(self, replay):
        self.assertFalse(replay.clicks('BUY_CONFIRM_SINGLE'), replay.actions)
        self.assertFalse(replay.clicks('BUY_CONFIRM_MULTI'), replay.actions)

    def assert_local_failure(self, replay):
        started = self.clock.now
        result = replay.task._purchase_item(free_plan())
        self.assertEqual((result.success, result.quantity), (False, 0))
        self.assertLess(self.clock.now - started, 60, replay.actions)
        return result

    def test_real_captures_distinguish_available_reward_and_claimed_store(self):
        for fixture, available, reward, store in (
            (AVAILABLE, True, False, True), (REWARD, False, True, False),
            (CLAIMED, False, False, True),
        ):
            with self.subTest(fixture=fixture):
                r = self.replay([captured_frame(fixture)])
                self.assertEqual(r.task._match_purchaseable_item(DAILY_FREE_ITEM), available)
                self.assertEqual(r.task.appear(TOUCH_TO_CLOSE), reward)
                self.assertEqual(r.task._is_on_any_store_page(), store)
                self.assertEqual(r.task._detect_purchase_popup_layout(), 'unknown')

    def test_real_direct_reward_retries_close_without_confirm_or_reopening_item(self):
        r = self.replay([captured_frame(AVAILABLE)] * 8 + [captured_frame(REWARD)] * 6
                        + [captured_frame(CLAIMED)] * 160)
        result = r.task._purchase_item(free_plan())
        self.assertEqual((result.success, result.quantity), (True, 1))
        self.assertEqual(r.index, 14)
        self.assert_no_confirm(r)
        r.assert_retried(self, 'DAILY_FREE_ITEM', 2)
        r.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        self.assertTrue(all(index < 8 for index in r.clicks('DAILY_FREE_ITEM')), r.actions)
        self.assertTrue(all(8 <= index < 14 for index in r.clicks('TOUCH_TO_CLOSE')), r.actions)

    def test_inheritance_store_captures_still_require_active_colors(self):
        for fixture in ('legacy-store-inheritance-charms', 'legacy-store-inheritance-cooldown-20260925'):
            with self.subTest(fixture=fixture):
                r = self.replay([captured_frame(fixture)])
                self.assertTrue(r.task._is_on_any_store_page())
                # Explicit synthetic overlay on the same capture verifies the
                # shared store predicate, without presenting a new real frame.
                r.image = (r.image * 0.3).astype('uint8')
                record_frame(f'synthetic:dimmed-{fixture}')
                self.assertFalse(r.task._is_on_any_store_page())

    def test_simulated_close_artwork_disappearing_does_not_accept_dimmed_store(self):
        r = self.replay([captured_frame(AVAILABLE)] * 2 + [captured_frame(REWARD)] * 5
                        + [captured_frame(REWARD, hide_close=True)] * 5
                        + [captured_frame(CLAIMED)])
        result = r.task._purchase_item(free_plan())
        self.assertEqual((result.success, result.quantity), (True, 1))
        self.assertEqual(r.index, 12)
        self.assert_no_confirm(r)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])
        self.assertTrue(all(index < 7 for index in r.clicks('TOUCH_TO_CLOSE')), r.actions)

    def test_confirm_path_waits_through_store_frames_for_delayed_reward(self):
        r = self.replay([shelf(available=True)] * 2 + [scene('BUY_CONFIRM_SINGLE')] * 4
                        + [shelf()] * 6 + [scene('TOUCH_TO_CLOSE')] * 6 + [shelf()])
        result = r.task._purchase_item(free_plan())
        self.assertEqual((result.success, result.quantity), (True, 1))
        self.assertEqual(r.index, 18)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])
        self.assertTrue(r.clicks('BUY_CONFIRM_SINGLE'), r.actions)
        self.assertTrue(all(2 <= index < 6 for index in r.clicks('BUY_CONFIRM_SINGLE')), r.actions)
        r.assert_retried(self, 'TOUCH_TO_CLOSE', 2)

    def test_reward_seen_blocks_late_confirm_and_item_clicks(self):
        r = self.replay([shelf(available=True)] * 2 + [scene('TOUCH_TO_CLOSE')] * 5
                        + [scene('BUY_CONFIRM_SINGLE', 'DAILY_FREE_ITEM')] * 5 + [shelf()])
        result = r.task._purchase_item(free_plan())
        self.assertEqual((result.success, result.quantity), (True, 1))
        self.assertEqual(r.index, 12)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])
        self.assert_no_confirm(r)

    def test_closed_reward_waits_for_store_after_loading(self):
        r = self.replay([shelf(available=True)] * 2 + [scene('TOUCH_TO_CLOSE')] * 5
                        + [scene(state='loading')] * 8 + [shelf()])
        result = r.task._purchase_item(free_plan())
        self.assertEqual((result.success, result.quantity), (True, 1))
        self.assertEqual(r.index, 15)
        self.assert_no_confirm(r)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])

    def test_closed_reward_without_return_to_store_fails_locally(self):
        r = self.replay([shelf(available=True)] * 2 + [scene('TOUCH_TO_CLOSE')] * 5
                        + [scene(state='loading')] * 160)
        self.assert_local_failure(r)
        self.assert_no_confirm(r)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])

    def test_network_close_is_not_reward_evidence(self):
        r = self.replay([shelf(available=True)] * 2
                        + [scene('NETWORK_ERROR_ABNORMAL', 'TOUCH_TO_CLOSE')] * 8
                        + [shelf()] * 160)
        self.assert_local_failure(r)
        self.assertTrue(r.clicks('TOUCH_TO_CLOSE'), r.actions)
        self.assertTrue(all(2 <= index < 10 for index in r.clicks('TOUCH_TO_CLOSE')), r.actions)
        self.assert_no_confirm(r)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])

    def test_store_return_without_reward_is_not_success(self):
        r = self.replay([shelf(available=True)] * 2 + [scene('BUY_CONFIRM_SINGLE')] * 4
                        + [shelf()] * 160)
        self.assert_local_failure(r)
        self.assertTrue(r.clicks('BUY_CONFIRM_SINGLE'), r.actions)
        self.assertFalse(r.clicks('TOUCH_TO_CLOSE'), r.actions)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])

    def test_unknown_observation_after_item_click_fails_before_device_restart(self):
        r = self.replay([shelf(available=True)] * 2 + [scene(state='unknown')] * 160)
        self.assert_local_failure(r)
        self.assert_no_confirm(r)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])
        self.assertFalse(r.clicks('TOUCH_TO_CLOSE'), r.actions)

    def test_persistent_available_item_cannot_extend_total_claim_timeout(self):
        r = self.replay([shelf(available=True)] * 160)
        started = self.clock.now
        self.assert_local_failure(r)
        self.assertLessEqual(self.clock.now - started, 21)
        r.assert_retried(self, 'DAILY_FREE_ITEM', 2)
        self.assert_no_confirm(r)
        self.assertFalse(r.clicks('TOUCH_TO_CLOSE'), r.actions)

    def test_persistent_reward_cannot_extend_total_claim_timeout(self):
        r = self.replay([shelf(available=True)] * 2 + [scene('TOUCH_TO_CLOSE')] * 160)
        started = self.clock.now
        self.assert_local_failure(r)
        self.assertLessEqual(self.clock.now - started, 21)
        r.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        self.assert_no_confirm(r)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])

    def test_persistent_confirmation_fails_locally_before_click_guard(self):
        r = self.replay([shelf(available=True)] * 2 + [scene('BUY_CONFIRM_SINGLE')] * 160)
        started = self.clock.now
        self.assert_local_failure(r)
        self.assertLessEqual(self.clock.now - started, 21)
        r.assert_retried(self, 'BUY_CONFIRM_SINGLE', 2)
        self.assertLess(len(r.clicks('BUY_CONFIRM_SINGLE')), 12)
        self.assertFalse(r.clicks('TOUCH_TO_CLOSE'), r.actions)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])

    def test_slow_network_frames_cannot_extend_total_claim_timeout(self):
        r = self.replay([shelf(available=True)] * 2
                        + [scene('NETWORK_ERROR_ABNORMAL', 'TOUCH_TO_CLOSE', seconds=4)] * 40)
        self.assert_local_failure(r)
        # The deadline is checked on the next screenshot. Each explicit slow
        # frame takes four seconds; network recovery uses its real three-second
        # wait through the controlled clock, rather than a mocked handler.
        # The claim deadline starts at the actual first click, not before the
        # screenshot that locates the item. Record that action time explicitly.
        started = next(at for name, at in r.action_times if name == 'DAILY_FREE_ITEM')
        self.assertLessEqual(self.clock.now - started, 25)
        self.assertGreaterEqual(len(r.clicks('TOUCH_TO_CLOSE')), 2)
        self.assert_no_confirm(r)
        self.assertEqual(r.clicks('DAILY_FREE_ITEM'), [1])
