"""Payment regressions include repeated frames and synthetic network failures."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.alas import AzurLaneAutoScript
from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from tasks.secret_shop.recognition import ShopCurrencyOcr
from tasks.secret_shop.payment import ShopPayment
from tests.support.offline import ControlledClock
from tests.support.secret_shop import Frame, ShopReplay


class PaymentReplayTests(unittest.TestCase):
    def run_shop(self, frames, **config):
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock, **config)
            result = shop.run(skip_first_screenshot=True)
        return shop, result

    def test_disappearing_confirmation_without_debit_never_counts_purchase(self):
        before = Frame(target='covenant')
        frames = [before, before, Frame(page='buy'), Frame(page='unknown')]
        frames += [before] * 75
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock)
            with self.assertRaises(GameStuckError):
                shop.run(skip_first_screenshot=True)
        self.assertEqual(shop.covenant_bought, 0)
        self.assertEqual([name for _, name in shop.device.actions].count('BUY_CONFIRM'), 1)

    def test_refresh_timeout_never_permits_second_payment(self):
        before = Frame()
        frames = [before] * 5 + [Frame(page='refresh')]
        frames += [Frame(stable=False)] * 75
        frames += [before] * 5 + [Frame(page='refresh')]
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock, SecretShop_OnlyFree=False)
            with self.assertRaises(GameStuckError):
                shop.run(skip_first_screenshot=True)
        self.assertEqual(shop.refresh_count, 0)
        self.assertEqual([name for _, name in shop.device.actions].count('REFRESH_CONFIRM'), 1)

    def assert_uncertain(self, frames, **config):
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock, **config)
            with self.assertRaises(GameStuckError):
                shop.run(skip_first_screenshot=True)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought, shop.refresh_count), (0, 0, 0))
        self.assertEqual(shop.config.delays, [])
        self.assertEqual(shop.device.actions[-1][1], 'diagnostic')
        return shop

    def test_network_error_and_delayed_balance_buy_only_once(self):
        before = Frame(target='covenant')
        after = Frame(balance=(816000, 100), target='covenant')
        frames = [before] * 2 + [Frame(page='buy')] * 3
        frames += [Frame(page='network'), Frame(page='unknown')] + [before] * 8 + [after] * 8
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual(shop.covenant_bought, 1)
        self.assertEqual(shop.device.actions, [(1, 'covenant'), (2, 'BUY_CONFIRM'),
                                               (5, 'network_retry'), (18, 'swipe')])
        self.assertEqual(shop.written_balances[-1]['gold'].value, 816000)

    def test_mystic_network_error_with_unchanged_frames_then_exact_debit(self):
        before = Frame(target='mystic')
        after = Frame(balance=(720000, 100), target='mystic')
        frames = [before] * 2 + [Frame(page='buy'), Frame(page='network')] + [before] * 4 + [after] * 8
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought), (0, 1))
        self.assertEqual([name for _, name in shop.device.actions], ['mystic', 'BUY_CONFIRM', 'network_retry', 'swipe'])

    def test_refresh_delayed_debit_is_counted_once_and_enforces_limit(self):
        before = Frame()
        after = Frame(balance=(1000000, 97))
        frames = [before] * 4 + [Frame(page='refresh')] * 3 + [Frame(page='network')]
        frames += [before] * 22 + [after] * 10
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual(shop.device.actions, [(1, 'swipe'), (3, 'REFRESH'), (4, 'REFRESH_CONFIRM'),
                                               (7, 'network_retry'), (33, 'swipe')])

    def test_debit_waits_for_list_recovery_before_next_action(self):
        before = Frame()
        after = Frame(balance=(1000000, 97))
        frames = [before] * 4 + [Frame(page='refresh')]
        frames += [Frame(balance=(1000000, 97), stable=False)] * 8 + [after] * 10
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual(shop.device.actions[-1], (18, 'swipe'))

    def test_one_frame_false_debit_cannot_count_purchase(self):
        before = Frame(target='covenant')
        frames = [before] * 2 + [Frame(page='buy'), Frame(balance=(816000, 100))] + [before] * 75
        shop = self.assert_uncertain(frames)
        self.assertEqual([name for _, name in shop.device.actions].count('BUY_CONFIRM'), 1)

    def test_unreadable_frame_resets_payment_evidence(self):
        before = Frame(target='covenant')
        after = Frame(balance=(816000, 100), target='covenant')
        frames = [before] * 2 + [Frame(page='buy'), after, Frame(balance=None), after, Frame(page='unknown')]
        frames += [after] * 8
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual(shop.covenant_bought, 1)
        self.assertEqual(shop.device.actions[-1], (10, 'swipe'))

    def test_wrong_gold_debit_stops_without_retry(self):
        for balance in ((815999, 100), (816001, 100), (632000, 100), (1100000, 100)):
            with self.subTest(balance=balance):
                frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
                frames += [Frame(balance=balance)] * 75
                shop = self.assert_uncertain(frames)
                self.assertEqual([name for _, name in shop.device.actions].count('BUY_CONFIRM'), 1)

    def test_multiple_refresh_debit_is_not_silently_counted_as_one(self):
        frames = [Frame()] * 4 + [Frame(page='refresh')] + [Frame(balance=(1000000, 94))] * 75
        shop = self.assert_uncertain(frames, SecretShop_OnlyFree=False)
        self.assertEqual([name for _, name in shop.device.actions].count('REFRESH_CONFIRM'), 1)

    def test_missing_pre_payment_balance_never_clicks(self):
        shop, result = self.run_shop([Frame(balance=None, target='covenant')] * 125)
        self.assertFalse(result)
        self.assertEqual(shop.device.actions, [])
        self.assertEqual(shop.config.delays, [{'minute': 10}])

    def test_unstable_pre_payment_balance_must_be_observed_again(self):
        frames = [Frame(target='covenant'), Frame(balance=None, target='covenant'),
                  Frame(balance=(900000, 100), target='covenant'),
                  Frame(balance=(900000, 100), target='covenant'), Frame(page='buy')]
        frames += [Frame(balance=(716000, 100))] * 10
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual(shop.device.actions[0], (3, 'covenant'))
        self.assertEqual(shop.covenant_bought, 1)

    def test_insufficient_gold_exits_before_purchase_or_refresh(self):
        for gold in (0, 183999):
            with self.subTest(gold=gold):
                shop, result = self.run_shop([Frame(balance=(gold, 100), target='covenant')] * 4,
                                             SecretShop_OnlyFree=False)
                self.assertTrue(result)
                self.assertEqual(shop.device.actions, [])

    def test_only_expensive_item_enabled_requires_its_full_price(self):
        shop, result = self.run_shop([Frame(balance=(200000, 100), target='mystic')] * 4,
                                     SecretShop_BuyCovenantBookmark=False, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.device.actions, [])

    def test_gold_exactly_equal_to_price_can_be_spent_to_zero(self):
        frames = [Frame(balance=(184000, 100), target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(0, 100))] * 6
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual(shop.covenant_bought, 1)
        self.assertEqual([name for _, name in shop.device.actions], ['covenant', 'BUY_CONFIRM'])

    def test_insufficient_skystones_stops_after_scanning(self):
        for skystone in (0, 2):
            with self.subTest(skystone=skystone):
                shop, result = self.run_shop([Frame(balance=(1000000, skystone))] * 6,
                                             SecretShop_OnlyFree=False)
                self.assertTrue(result)
                self.assertEqual(shop.device.actions, [(1, 'swipe')])

    def test_refresh_exact_balance_can_reach_zero(self):
        frames = [Frame(balance=(1000000, 3))] * 4 + [Frame(page='refresh')]
        frames += [Frame(balance=(1000000, 0))] * 10
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False, SecretShop_MaxRefresh=10)
        self.assertTrue(result)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual([name for _, name in shop.device.actions].count('REFRESH_CONFIRM'), 1)

    def test_free_only_and_zero_refresh_limit_never_open_refresh(self):
        for config in ({'SecretShop_OnlyFree': True},
                       {'SecretShop_OnlyFree': False, 'SecretShop_MaxRefresh': 0}):
            with self.subTest(config=config):
                shop, result = self.run_shop([Frame()] * 6, **config)
                self.assertTrue(result)
                self.assertEqual(shop.device.actions, [(1, 'swipe')])

    def test_all_items_disabled_never_spends(self):
        shop, result = self.run_shop([Frame(target='covenant')], SecretShop_BuyCovenantBookmark=False,
                                     SecretShop_BuyMysticMedal=False, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.device.actions, [])

    def test_dialog_without_baseline_is_never_confirmed(self):
        for page in ('buy', 'refresh'):
            with self.subTest(page=page):
                shop = self.assert_uncertain([Frame(page=page)] * 4)
                self.assertEqual(shop.device.actions, [(0, 'diagnostic')])

    def test_balance_change_without_confirmation_is_not_our_payment(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(balance=(816000, 100))] * 75
        shop = self.assert_uncertain(frames)
        self.assertEqual([name for _, name in shop.device.actions], ['covenant', 'diagnostic'])

    def test_confirmation_click_error_preserves_unknown_payment(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock)
            original_click = shop.device.click

            def click(button):
                original_click(button)
                if button.name == 'BUY_CONFIRM':
                    raise GameStuckError('transport failed after sending click')

            with patch.object(shop.device, 'click', side_effect=click):
                with self.assertRaises(GameStuckError):
                    shop.run(skip_first_screenshot=True)
        self.assertEqual(shop.covenant_bought, 0)
        self.assertTrue(shop._payment.submitted)
        self.assertEqual([name for _, name in shop.device.actions], ['covenant', 'BUY_CONFIRM'])

    def test_screenshot_error_after_confirmation_reaches_restart_handler(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock)
            original_screenshot = shop.device.screenshot

            def screenshot():
                if shop.device.index == 2:
                    raise GameStuckError('screenshot failed after confirmation')
                original_screenshot()

            with patch.object(shop.device, 'screenshot', side_effect=screenshot):
                self.assert_restart_scheduled(shop)
        self.assertEqual(shop.covenant_bought, 0)
        self.assertEqual([name for _, name in shop.device.actions].count('BUY_CONFIRM'), 1)

    def assert_restart_scheduled(self, shop):
        # Run the actual scheduler exception dispatcher. Only its external
        # effects are replaced, so a human-takeover regression cannot pass.
        runner = object.__new__(AzurLaneAutoScript)
        runner.config = SimpleNamespace(task_call=Mock())
        runner.device = SimpleNamespace(screenshot=Mock(), screenshot_tracking=[],
                                        package='offline', sleep=Mock())
        runner.save_error_log = Mock()
        runner.secret_shop = lambda: shop.run(skip_first_screenshot=True)
        with patch('module.alas.handle_notify') as notify:
            self.assertFalse(runner.run('secret_shop'))
        runner.config.task_call.assert_called_once_with('Restart')
        runner.save_error_log.assert_called_once_with()
        runner.device.sleep.assert_called_once_with(10)
        notify.assert_not_called()

    def test_missed_purchase_confirmation_retries_until_debit(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy', seconds=0.5)] * 6
        frames += [Frame(balance=(816000, 100))] * 8
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual(shop.covenant_bought, 1)
        self.assertEqual(shop.device.actions, [
            (1, 'covenant'), (2, 'BUY_CONFIRM'), (7, 'BUY_CONFIRM'), (11, 'swipe'),
        ])

    def test_missed_refresh_confirmation_retries_without_new_refresh(self):
        frames = [Frame()] * 4 + [Frame(page='refresh', seconds=0.5)] * 6
        frames += [Frame(balance=(1000000, 97))] * 8
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual(shop.device.actions, [
            (1, 'swipe'), (3, 'REFRESH'), (4, 'REFRESH_CONFIRM'), (9, 'REFRESH_CONFIRM'), (15, 'swipe'),
        ])

    def test_missed_entry_click_retries_current_visible_item(self):
        frames = [Frame(target='covenant')] * 5 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, 100))] * 8
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual(shop.covenant_bought, 1)
        self.assertEqual(shop.device.actions, [
            (1, 'covenant'), (4, 'covenant'), (5, 'BUY_CONFIRM'), (9, 'swipe'),
        ])

    def test_missed_refresh_entry_retries_until_confirmation_appears(self):
        frames = [Frame()] * 7 + [Frame(page='refresh')]
        frames += [Frame(balance=(1000000, 97))] * 12
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual(shop.device.actions, [
            (1, 'swipe'), (3, 'REFRESH'), (6, 'REFRESH'), (7, 'REFRESH_CONFIRM'), (14, 'swipe'),
        ])

    def test_entry_retry_requires_same_item_and_unchanged_balance(self):
        for frame in (Frame(target='mystic'), Frame(target=None),
                      Frame(target='covenant', stable=False),
                      Frame(target='covenant', balance=None)):
            with self.subTest(frame=frame):
                shop = self.assert_uncertain([Frame(target='covenant')] * 2 + [frame] * 75)
                self.assertEqual(shop.device.actions[0], (1, 'covenant'))
                self.assertEqual(len(shop.device.actions), 2)

    def test_persistent_confirmation_retries_do_not_postpone_restart(self):
        for kind, page, before_count in (('covenant', 'buy', 2), ('refresh', 'refresh', 4)):
            with self.subTest(kind=kind), ControlledClock() as clock, \
                    patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
                before = Frame(target='covenant' if kind == 'covenant' else None)
                shop = ShopReplay([before] * before_count + [Frame(page=page)] * 75,
                                  clock, SecretShop_OnlyFree=False)
                self.assert_restart_scheduled(shop)
                confirm = 'BUY_CONFIRM' if kind == 'covenant' else 'REFRESH_CONFIRM'
                clicks = [index for index, name in shop.device.actions if name == confirm]
                self.assertGreater(len(clicks), 1)
                self.assertTrue(all(later - earlier == 3 for earlier, later in zip(clicks, clicks[1:])))
                self.assertEqual(shop.device.index, before_count + 46)
                self.assertEqual((shop.covenant_bought, shop.refresh_count), (0, 0))
                self.assertEqual(shop.config.delays, [])

    def test_framework_click_guard_still_restarts_after_confirmation(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')] * 8
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock)
            original_click = shop.device.click

            def click(button):
                original_click(button)
                if button.name == 'BUY_CONFIRM' and shop.device.index > 2:
                    raise GameTooManyClickError('confirmation remained visible')

            with patch.object(shop.device, 'click', side_effect=click):
                self.assert_restart_scheduled(shop)
        self.assertEqual(shop.covenant_bought, 0)
        self.assertEqual(shop.device.actions, [(1, 'covenant'), (2, 'BUY_CONFIRM'), (5, 'BUY_CONFIRM')])

    def test_refreshed_round_allows_same_item_but_never_second_refresh(self):
        before = Frame(target='covenant')
        first = Frame(balance=(816000, 100), target='covenant')
        refreshed = Frame(balance=(816000, 97), target='covenant', goods=(5, 6, 7, 8))
        second = Frame(balance=(632000, 97), target='covenant')
        frames = [before] * 2 + [Frame(page='buy')] + [first] * 6
        frames += [Frame(page='refresh')] + [refreshed] * 6 + [Frame(page='buy')] + [second] * 8
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.refresh_count), (2, 1))
        self.assertEqual([name for _, name in shop.device.actions], [
            'covenant', 'BUY_CONFIRM', 'swipe', 'REFRESH', 'REFRESH_CONFIRM',
            'covenant', 'BUY_CONFIRM', 'swipe',
        ])

    def test_successful_purchase_reads_gold_before_and_after_only(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, None))] * 8
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual(shop.covenant_bought, 1)
        self.assertEqual(shop.currency_reads, [(1, 'gold'), (4, 'gold')])

    def test_ordinary_scan_and_image_confirmed_refresh_do_not_read_resources(self):
        frames = [Frame()] * 4 + [Frame(page='refresh')]
        frames += [Frame(goods=(5, 6, 7, 8))] * 9
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual(shop.currency_reads, [])
        self.assertEqual(shop.device.actions, [(1, 'swipe'), (3, 'REFRESH'), (4, 'REFRESH_CONFIRM'), (9, 'swipe')])

    def test_unchanged_resource_pixels_do_not_repeat_ocr_while_waiting(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(target='covenant')] * 20 + [Frame(balance=(816000, 100))] * 8
        shop, result = self.run_shop(frames)
        self.assertTrue(result)
        self.assertEqual(shop.currency_reads, [(1, 'gold'), (4, 'gold'), (24, 'gold')])
        self.assertEqual(shop.covenant_bought, 1)

    def test_unchanged_goods_use_stone_fallback_without_gold_ocr(self):
        frames = [Frame()] * 4 + [Frame(page='refresh')]
        frames += [Frame(balance=(1000000, 97))] * 12
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual([key for _, key in shop.currency_reads], ['skystone', 'skystone'])

    def test_unreadable_refresh_baseline_does_not_ocr_each_frame_or_count_success(self):
        frames = [Frame()] * 4 + [Frame(page='refresh')]
        frames += [Frame(seconds=0.1)] * 70 + [Frame(page='disconnect')]
        reads = []
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock, SecretShop_OnlyFree=False)

            def unreadable(key, image=None):
                reads.append((clock.now, key))
                return None

            with patch.object(shop, '_read_shop_currency', side_effect=unreadable):
                self.assert_restart_scheduled(shop)
        self.assertGreaterEqual(len(reads), 2)
        self.assertLessEqual(len(reads), 5)
        self.assertTrue(all(key == 'skystone' for _, key in reads))
        self.assertTrue(all(later[0] - earlier[0] >= 1 for earlier, later in zip(reads, reads[1:])))
        self.assertEqual(shop.refresh_count, 0)

    def test_one_changed_goods_frame_is_not_a_refresh(self):
        frames = [Frame()] * 4 + [Frame(page='refresh')]
        frames += [Frame(goods=(5, 6, 7, 8))] + [Frame()] * 75
        shop = self.assert_uncertain(frames, SecretShop_OnlyFree=False)
        self.assertEqual(shop.refresh_count, 0)

    def test_disconnect_during_purchase_reaches_restart_immediately(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy'), Frame(page='disconnect')]
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock)
            self.assert_restart_scheduled(shop)
        self.assertEqual(shop.device.index, 3)
        self.assertEqual(shop.covenant_bought, 0)

    def test_purchase_goal_scans_the_current_round_before_exiting_without_refresh(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, 100), target='covenant')] * 8
        shop, result = self.run_shop(frames, SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=1, SecretShop_TargetMystic=0,
                                     SecretShop_OnlyFree=False, SecretShop_MaxRefresh=0)
        self.assertTrue(result)
        self.assertEqual(shop.covenant_bought, 1)
        self.assertEqual([name for _, name in shop.device.actions], ['covenant', 'BUY_CONFIRM', 'swipe'])
        self.assertEqual(shop.config.SecretShopRuntime_Session, {})

    def test_reached_top_goal_still_buys_an_enabled_item_hidden_at_the_bottom(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, 100))] * 4
        frames += [Frame(balance=(816000, 100), target='mystic')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(536000, 100))] * 8
        shop, result = self.run_shop(frames, SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=1, SecretShop_TargetMystic=0,
                                     SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought, shop.refresh_count), (1, 1, 0))
        self.assertEqual([name for _, name in shop.device.actions], [
            'covenant', 'BUY_CONFIRM', 'swipe', 'mystic', 'BUY_CONFIRM',
        ])
        self.assertTrue(shop._scrolled)
        self.assertIsNone(shop._payment)

    def test_goal_reached_at_bottom_finishes_without_another_scroll_or_refresh(self):
        frames = [Frame()] * 2 + [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, 100))] * 8
        shop, result = self.run_shop(frames, SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=1, SecretShop_TargetMystic=0,
                                     SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.covenant_bought, 1)
        self.assertEqual([name for _, name in shop.device.actions], ['swipe', 'covenant', 'BUY_CONFIRM'])

    def test_purchase_goal_ignores_refresh_count_and_requires_each_selected_target(self):
        before = Frame(target='covenant')
        first = Frame(balance=(816000, 100), target='covenant')
        refreshed = Frame(balance=(816000, 97), target='mystic', goods=(5, 6, 7, 8))
        second = Frame(balance=(536000, 97), target='mystic', goods=(5, 6, 7, 8))
        frames = [before] * 2 + [Frame(page='buy')] + [first] * 6
        frames += [Frame(page='refresh')] + [refreshed] * 6 + [Frame(page='buy')] + [second] * 8
        shop, result = self.run_shop(frames, SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=1, SecretShop_TargetMystic=1,
                                     SecretShop_OnlyFree=False, SecretShop_MaxRefresh=0)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought, shop.refresh_count), (1, 1, 1))
        self.assertEqual([name for _, name in shop.device.actions], [
            'covenant', 'BUY_CONFIRM', 'swipe', 'REFRESH', 'REFRESH_CONFIRM', 'mystic', 'BUY_CONFIRM', 'swipe',
        ])

    def test_purchase_goal_requires_a_positive_target_on_a_selected_item(self):
        for covenant, mystic, buy_covenant, buy_mystic in (
            (0, 0, True, True), (0, 1, True, False), (1, 0, False, True), (1, 1, False, False),
        ):
            with self.subTest(targets=(covenant, mystic), selected=(buy_covenant, buy_mystic)):
                with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto') as navigate:
                    shop = ShopReplay([Frame()], clock, SecretShop_CompletionMode='PurchaseCount',
                                      SecretShop_TargetCovenant=covenant, SecretShop_TargetMystic=mystic,
                                      SecretShop_BuyCovenantBookmark=buy_covenant,
                                      SecretShop_BuyMysticMedal=buy_mystic, SecretShop_OnlyFree=False)
                    with patch.object(shop.device, 'app_is_running') as running:
                        with self.assertRaisesRegex(RequestHumanTakeover, 'positive purchase target'):
                            shop.run(skip_first_screenshot=True)
                    running.assert_not_called()
                    navigate.assert_not_called()
                self.assertEqual(shop.device.actions, [])
                self.assertEqual(shop.currency_reads, [])
                self.assertEqual(shop.config.delays, [])

    def test_zero_target_item_is_bought_while_waiting_for_the_other_goal(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, 100), target='mystic')] * 4 + [Frame(page='buy')]
        frames += [Frame(balance=(536000, 100))] * 8
        shop, result = self.run_shop(frames, SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=0, SecretShop_TargetMystic=1,
                                     SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought), (1, 1))
        self.assertEqual([name for _, name in shop.device.actions], [
            'covenant', 'BUY_CONFIRM', 'mystic', 'BUY_CONFIRM', 'swipe',
        ])

    def test_completed_item_target_does_not_disable_buying_until_all_goals_finish(self):
        before = Frame(balance=(2000000, 100), target='covenant')
        first = Frame(balance=(1816000, 100))
        refreshed = Frame(balance=(1816000, 97), target='covenant', goods=(5, 6, 7, 8))
        second = Frame(balance=(1632000, 97), target='mystic', goods=(5, 6, 7, 8))
        final = Frame(balance=(1352000, 97), goods=(5, 6, 7, 8))
        frames = [before] * 2 + [Frame(page='buy')] + [first] * 6
        frames += [Frame(page='refresh')] + [refreshed] * 5 + [Frame(page='buy')]
        frames += [second] * 4 + [Frame(page='buy')] + [final] * 8
        shop, result = self.run_shop(frames, SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=1, SecretShop_TargetMystic=1,
                                     SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought, shop.refresh_count), (2, 1, 1))
        self.assertEqual([name for _, name in shop.device.actions], [
            'covenant', 'BUY_CONFIRM', 'swipe', 'REFRESH', 'REFRESH_CONFIRM',
            'covenant', 'BUY_CONFIRM', 'mystic', 'BUY_CONFIRM', 'swipe',
        ])

    def test_disabled_item_positive_target_does_not_block_selected_goal(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, 100), target='mystic')] * 8
        shop, result = self.run_shop(frames, SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=1, SecretShop_TargetMystic=10,
                                     SecretShop_BuyMysticMedal=False, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought), (1, 0))
        self.assertEqual([name for _, name in shop.device.actions], ['covenant', 'BUY_CONFIRM', 'swipe'])

    def test_confirmed_purchase_goal_progress_survives_restart(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, 100))] * 3 + [Frame(page='disconnect')]
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            shop = ShopReplay(frames, clock, SecretShop_CompletionMode='PurchaseCount',
                              SecretShop_TargetCovenant=1, SecretShop_TargetMystic=1,
                              SecretShop_OnlyFree=False)
            self.assert_restart_scheduled(shop)
            saved = shop.config.SecretShopRuntime_Session
            resumed = ShopReplay([Frame(balance=(816000, 100), target='mystic')] * 2 + [Frame(page='buy')]
                                 + [Frame(balance=(536000, 100))] * 8, clock,
                                 SecretShop_CompletionMode='PurchaseCount', SecretShop_TargetCovenant=1,
                                 SecretShop_TargetMystic=1, SecretShopRuntime_Session=saved,
                                 SecretShop_OnlyFree=False)
            self.assertTrue(resumed.run(skip_first_screenshot=True))
        self.assertEqual((resumed.covenant_bought, resumed.mystic_bought), (1, 1))
        self.assertEqual([name for _, name in resumed.device.actions], ['mystic', 'BUY_CONFIRM', 'swipe'])

    def test_changed_purchase_goal_starts_a_new_progress_record(self):
        stale = {'goal': ['PurchaseCount', 2, 1, True, True, False], 'covenant': 2, 'mystic': 1, 'refresh': 8}
        shop, result = self.run_shop([Frame(balance=(1000000, 2))] * 8,
                                     SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=1, SecretShop_TargetMystic=0,
                                     SecretShopRuntime_Session=stale, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought), (0, 0))

    def test_free_only_ignores_hidden_purchase_targets_but_honors_buy_selection(self):
        frames = [Frame(target='covenant')] * 2 + [Frame(page='buy')]
        frames += [Frame(balance=(816000, 100), target='mystic')] * 8
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=True,
                                     SecretShop_CompletionMode='PurchaseCount',
                                     SecretShop_TargetCovenant=0, SecretShop_TargetMystic=0,
                                     SecretShop_BuyMysticMedal=False)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.mystic_bought, shop.refresh_count), (1, 0, 0))
        self.assertEqual([name for _, name in shop.device.actions], ['covenant', 'BUY_CONFIRM', 'swipe'])
        self.assertEqual(shop.config.SecretShop_CompletionMode, 'PurchaseCount')
        self.assertEqual(shop.config.SecretShop_TargetCovenant, 0)

    def test_currency_parser_rejects_truncated_groups_and_nondigits(self):
        for text in ('196,389,22', '196,573,22', '1249园', '.', ''):
            with self.subTest(text=text):
                self.assertIsNone(ShopCurrencyOcr.parse_amount(text))
        self.assertEqual(ShopCurrencyOcr.parse_amount('196,389,222'), 196389222)
        self.assertEqual(ShopCurrencyOcr.parse_amount('0'), 0)

    def test_missing_spent_currency_baseline_cannot_confirm_a_payment(self):
        for kind in ('covenant', 'refresh'):
            with self.subTest(kind=kind):
                payment = ShopPayment(kind, (None, None), submitted=True)
                self.assertFalse(payment.observe((816000, 97)))

    def test_completion_configuration_defaults_and_visibility(self):
        from module.config.config_updater import ConfigGenerator, ConfigUpdater
        argument = ConfigGenerator().argument['SecretShop']
        self.assertEqual(argument['CompletionMode']['value'], 'RefreshCount')
        self.assertEqual(argument['CompletionMode']['option'], ['RefreshCount', 'PurchaseCount'])
        updater = ConfigUpdater()
        hidden = updater.get_hidden_args({'SecretShop': {'SecretShop': {
            'OnlyFree': False, 'CompletionMode': 'PurchaseCount', 'BuyMysticMedal': False,
        }}})
        self.assertIn('SecretShop.SecretShop.MaxRefresh', hidden)
        self.assertIn('SecretShop.SecretShop.TargetMystic', hidden)
        self.assertNotIn('SecretShop.SecretShop.TargetCovenant', hidden)
        hidden = updater.get_hidden_args({'SecretShop': {'SecretShop': {
            'OnlyFree': False, 'CompletionMode': 'RefreshCount',
        }}})
        self.assertIn('SecretShop.SecretShop.TargetCovenant', hidden)
        self.assertIn('SecretShop.SecretShop.TargetMystic', hidden)
        self.assertNotIn('SecretShop.SecretShop.CompletionMode', hidden)
        self.assertNotIn('SecretShop.SecretShop.MaxRefresh', hidden)
        for mode in ('RefreshCount', 'PurchaseCount'):
            for free_options in ({}, {'OnlyFree': True}):
                with self.subTest(mode=mode, free_options=free_options):
                    hidden = updater.get_hidden_args({'SecretShop': {'SecretShop': {
                        **free_options, 'CompletionMode': mode,
                    }}})
                    for key in ('CompletionMode', 'MaxRefresh', 'TargetCovenant', 'TargetMystic'):
                        self.assertIn(f'SecretShop.SecretShop.{key}', hidden)
                    for key in ('BuyCovenantBookmark', 'BuyMysticMedal'):
                        self.assertNotIn(f'SecretShop.SecretShop.{key}', hidden)
