"""Payment regressions include repeated frames and synthetic network failures."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.alas import AzurLaneAutoScript
from module.exception import GameStuckError, GameTooManyClickError
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
        self.assertEqual(shop.device.actions[-1], (16, 'swipe'))

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

    def test_wrong_debit_or_other_currency_change_stops_without_retry(self):
        for balance in ((815999, 100), (816001, 100), (816000, 97), (632000, 100), (1100000, 100)):
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
        frames = [Frame(target='covenant'), Frame(balance=None),
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
            (1, 'swipe'), (3, 'REFRESH'), (4, 'REFRESH_CONFIRM'), (9, 'REFRESH_CONFIRM'), (13, 'swipe'),
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
        frames += [Frame(balance=(1000000, 97))] * 8
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual(shop.device.actions, [
            (1, 'swipe'), (3, 'REFRESH'), (6, 'REFRESH'), (7, 'REFRESH_CONFIRM'), (11, 'swipe'),
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
        refreshed = Frame(balance=(816000, 97), target='covenant')
        second = Frame(balance=(632000, 97), target='covenant')
        frames = [before] * 2 + [Frame(page='buy')] + [first] * 6
        frames += [Frame(page='refresh')] + [refreshed] * 4 + [Frame(page='buy')] + [second] * 8
        shop, result = self.run_shop(frames, SecretShop_OnlyFree=False)
        self.assertTrue(result)
        self.assertEqual((shop.covenant_bought, shop.refresh_count), (2, 1))
        self.assertEqual([name for _, name in shop.device.actions], [
            'covenant', 'BUY_CONFIRM', 'swipe', 'REFRESH', 'REFRESH_CONFIRM',
            'covenant', 'BUY_CONFIRM', 'swipe',
        ])
