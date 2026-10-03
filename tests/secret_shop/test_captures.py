"""Reviewed global-cn pixels; gaps and repeated screenshots are explicit."""
import unittest
from unittest.mock import patch

from module.config import server

server.set_lang('global_cn')

from tasks.secret_shop.assets.assets_secret_shop import BUY_CONFIRM, REFRESH_CONFIRM  # noqa: E402
from tasks.base.assets.assets_base_popup import NETWORK_ERROR_ABNORMAL, NETWORK_ERROR_DISCONNECT  # noqa: E402
from tasks.secret_shop.payment import ShopPayment  # noqa: E402
from tasks.secret_shop.recognition import ShopGoods, same_currency_image  # noqa: E402
from tasks.secret_shop.secret_shop import SecretShop  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402
from tests.support.secret_shop import (  # noqa: E402
    CapturedReplayDevice, Config, captures, pixel_task,
    capture,
)

FIXTURE_CASES = {
    'test_page_and_confirmation_states': 'all',
    'test_visible_resource_balances': [
        '20261002-151247-016', '20261002-151254-745', '20261002-151257-334',
        '20261002-151259-623', '20261002-151308-133', '20261002-151314-999',
        '20261002-151322-511', '20261002-151325-876', '20261002-151328-156',
        '20261002-151345-195', '20261002-151351-715', '20261002-151441-164',
        '20261002-151456-902', '20261002-151504-141',
        '20261002-201411-848', '20261002-221022-016', '20261003-125942-495',
    ],
    'test_available_and_sold_targets_in_both_scroll_positions': [
        '20261001-174909-267', '20261002-151247-016', '20261002-151254-745',
        '20261002-151257-334', '20261002-151259-623', '20261002-151308-133',
        '20261002-151314-999', '20261002-151322-511', '20261002-151325-876',
        '20261002-151328-156', '20261002-151345-195', '20261002-151351-715',
        '20261002-151441-164', '20261002-151456-902', '20261002-151504-141',
        '20261002-201411-848', '20261002-221022-016',
        '20261003-125942-495', '20261003-125720-050',
    ],
    'test_covenant_and_mystic_debits_match_the_displayed_price': [
        '20261002-151247-016', '20261002-151257-334',
        '20261002-151314-999', '20261002-151325-876',
        '20261002-151345-195', '20261002-151351-715',
        '20261002-151456-902', '20261002-151504-141',
    ],
    'test_first_refresh_has_exactly_one_debit': ['20261002-151259-623', '20261002-151308-133'],
    'test_gaps_between_refresh_captures_do_not_prove_one_payment': [
        '20261002-151351-715', '20261002-151441-164', '20261002-151456-902',
    ],
    'test_captured_covenant_purchase_and_sold_filter': [
        '20261002-151247-016', '20261002-151251-082', '20261002-151257-334', '20261002-151259-623',
    ],
    'test_captured_mystic_purchase_and_sold_filter': [
        '20261002-151314-999', '20261002-151318-854', '20261002-151325-876', '20261002-151328-156',
    ],
    'test_captured_bottom_purchase': ['20261002-151345-195', '20261002-151349-222', '20261002-151351-715'],
    'test_captured_refresh_confirms_exact_debit': ['20261002-151259-623', '20261002-151302-626', '20261002-151308-133'],
    'test_captured_purchase_retries_persistent_confirmation': [
        '20261002-151247-016', '20261002-151251-082', '20261002-151257-334', '20261002-151259-623',
    ],
    'test_captured_refresh_retries_persistent_confirmation': [
        '20261002-151259-623', '20261002-151302-626', '20261002-151308-133',
    ],
    'test_repeated_trailing_digit_balances': ['20261002-201411-848', '20261002-221022-016'],
    'test_goods_snapshot_ignores_sale_dimming_and_rejects_new_goods': [
        '20261002-151247-016', '20261002-151257-334', '20261002-151308-133',
        '20261002-151259-623', '20261002-201411-848', '20261002-221022-016',
    ],
    'test_captured_visual_refresh_does_not_call_ocr': [
        '20261002-151257-334', '20261002-151259-623', '20261002-151302-626', '20261002-151308-133',
    ],
    'test_currency_image_cache_ignores_background_and_detects_last_digit_change': [
        '20261002-151254-745', '20261002-151257-334', '20261002-151308-133', '20261002-151314-999',
    ],
    'test_captured_reached_goal_top_still_requests_bottom_scan': ['20261003-125942-495'],
    'test_network_error_popup_is_abnormal_and_blocks_the_shop': [
        '20261003-125720-050', '20261003-125942-495',
    ],
    'test_captured_refresh_network_error_keeps_one_pending_payment': [
        '20261002-151259-623', '20261002-151302-626',
        '20261003-125720-050', '20261002-151308-133',
    ],
}


class CapturedShopTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_page_and_confirmation_states(self):
        for fixture_id, item in captures().items():
            with self.subTest(scene=item['scene']):
                shop = pixel_task(fixture_id)
                scene = item['scene']
                buy = scene.endswith('_confirm') and not scene.startswith('refresh')
                refresh = scene.endswith('_confirm') and scene.startswith('refresh')
                blocked = scene in ('blacksmith_negative', 'refresh_network_abnormal')
                self.assertEqual(shop._shop_is_ready(), not (buy or refresh or blocked))
                self.assertEqual(shop.appear(BUY_CONFIRM), buy)
                self.assertEqual(shop.appear(REFRESH_CONFIRM), refresh)

    def test_visible_resource_balances(self):
        for fixture_id, item in captures().items():
            if item['scene'].endswith('_confirm') or item['scene'] in (
                'blacksmith_negative', 'refresh_network_abnormal',
            ):
                continue
            with self.subTest(scene=item['scene']):
                self.assertEqual(pixel_task(fixture_id)._read_shop_balance(), tuple(item['expected_balance']))

    def test_repeated_trailing_digit_balances(self):
        for fixture_id, expected in (('20261002-201411-848', (196573222, 1258)),
                                     ('20261002-221022-016', (196389222, 1249))):
            with self.subTest(fixture=fixture_id):
                self.assertEqual(pixel_task(fixture_id)._read_shop_balance(), expected)

    def test_captured_reached_goal_top_still_requests_bottom_scan(self):
        # This capture shows only the sold top row; no bottom capture from this
        # round was supplied. Verify its next action, and stop at that boundary.
        frames = ['20261003-125942-495'] * 4
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            device = CapturedReplayDevice(frames, clock)
            config = Config(SecretShop_OnlyFree=False, SecretShop_CompletionMode='PurchaseCount',
                            SecretShop_TargetCovenant=1, SecretShop_TargetMystic=1)
            shop = SecretShop(config, device)
            shop.covenant_bought = shop.mystic_bought = 1
            shop._save_progress()

            class CaptureBoundary(Exception):
                pass

            with patch.object(device, 'swipe', side_effect=CaptureBoundary), \
                    patch.object(shop, '_read_shop_currency', side_effect=AssertionError('No eligible item')), \
                    patch.object(shop, '_delay_to_auto_refresh') as delay:
                with self.assertRaises(CaptureBoundary):
                    shop.run(skip_first_screenshot=True)
                delay.assert_not_called()
        self.assertEqual(device.actions, [])
        self.assertEqual(shop.refresh_count, 0)
        self.assertEqual(config.SecretShopRuntime_Session['covenant'], 1)

    def test_network_error_popup_is_abnormal_and_blocks_the_shop(self):
        shop = pixel_task('20261003-125720-050')
        self.assertTrue(shop.appear(NETWORK_ERROR_ABNORMAL))
        self.assertFalse(shop.appear(NETWORK_ERROR_DISCONNECT))
        self.assertFalse(shop._shop_is_ready())
        self.assertFalse(shop.appear(BUY_CONFIRM))
        self.assertFalse(shop.appear(REFRESH_CONFIRM))
        clean = pixel_task('20261003-125942-495')
        self.assertFalse(clean.appear(NETWORK_ERROR_ABNORMAL))
        self.assertFalse(clean.appear(NETWORK_ERROR_DISCONNECT))

    def test_captured_refresh_network_error_keeps_one_pending_payment(self):
        # The actual popup is inserted into an explicit replay of older shop
        # captures. It is recognition evidence, not continuous footage of one
        # transaction; its dimmed resources must never become payment evidence.
        frames = ['20261002-151259-623'] * 3 + ['20261002-151302-626'] * 2
        frames += ['20261003-125720-050'] * 2 + ['20261002-151259-623'] * 3
        frames += ['20261002-151308-133'] * 10
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'):
            device = CapturedReplayDevice(frames, clock)
            shop = SecretShop(Config(SecretShop_OnlyFree=False), device)
            shop._scrolled = True
            handled = []
            original = shop.handle_network_error

            def network(**kwargs):
                payment = shop._payment
                changed = original(**kwargs)
                if changed:
                    self.assertIs(shop._payment, payment)
                    self.assertEqual(payment.kind, 'refresh')
                    self.assertTrue(payment.submitted)
                    self.assertEqual(shop.refresh_count, 0)
                    handled.append(device.index)
                return changed

            class CaptureBoundary(Exception):
                pass

            with patch.object(shop, 'handle_network_error', side_effect=network), \
                    patch.object(device, 'swipe', side_effect=CaptureBoundary):
                with self.assertRaises(CaptureBoundary):
                    shop.run(skip_first_screenshot=True)
        self.assertEqual(handled, [5])
        self.assertEqual([name for _, name in device.actions], [
            'REFRESH', 'REFRESH_CONFIRM', 'sleep(3)', 'TOUCH_TO_CLOSE',
        ])
        self.assertEqual(shop.refresh_count, 1)
        self.assertIsNone(shop._payment)
        self.assertEqual(shop.config.stored.Skystone.value, 1367)

    def test_currency_image_cache_ignores_background_and_detects_last_digit_change(self):
        before = pixel_task('20261002-151254-745')._currency_image('gold')
        same_amount = pixel_task('20261002-151257-334')._currency_image('gold')
        self.assertTrue(same_currency_image(before, same_amount))
        before = pixel_task('20261002-151308-133')._currency_image('skystone')
        changed = pixel_task('20261002-151314-999')._currency_image('skystone')
        self.assertFalse(same_currency_image(before, changed))

    def test_captured_visual_refresh_does_not_call_ocr(self):
        frames = ['20261002-151257-334'] * 3 + ['20261002-151259-623'] * 3
        frames += ['20261002-151302-626'] * 2 + ['20261002-151308-133'] * 10
        with ControlledClock() as clock:
            device = CapturedReplayDevice(frames, clock)
            shop = SecretShop(Config(SecretShop_OnlyFree=False), device)

            class CaptureBoundary(Exception):
                pass

            def swipe(*args, **kwargs):
                if shop.refresh_count:
                    raise CaptureBoundary
                device.actions.append((device.index, 'swipe'))

            with patch('tasks.secret_shop.secret_shop.UI.ui_goto'), \
                    patch.object(device, 'swipe', side_effect=swipe), \
                    patch.object(shop, '_read_shop_currency', side_effect=AssertionError('Visual path must not OCR')):
                with self.assertRaises(CaptureBoundary):
                    shop.run(skip_first_screenshot=True)
        self.assertEqual(shop.refresh_count, 1)
        self.assertEqual([name for _, name in device.actions], ['swipe', 'REFRESH', 'REFRESH_CONFIRM'])

    def test_goods_snapshot_ignores_sale_dimming_and_rejects_new_goods(self):
        before = ShopGoods.capture(capture('20261002-151247-016'))
        self.assertTrue(before.matches(ShopGoods.capture(capture('20261002-151257-334'))))
        for fixture_id in ('20261002-151308-133', '20261002-151259-623',
                           '20261002-201411-848', '20261002-221022-016'):
            with self.subTest(fixture=fixture_id):
                self.assertFalse(before.matches(ShopGoods.capture(capture(fixture_id))))

    def test_available_and_sold_targets_in_both_scroll_positions(self):
        expected = {
            'covenant_top_before': ['covenant'], 'mystic_top_before': ['mystic'],
            'covenant_bottom_before': ['covenant'], 'covenant_later_before': ['covenant'],
        }
        for fixture_id, item in captures().items():
            if item['scene'].endswith('_confirm'):
                continue
            with self.subTest(scene=item['scene']):
                shop = pixel_task(fixture_id)
                shop._scrolled = 'bottom' in item['scene']
                found = shop._find_target_buy_buttons()
                self.assertEqual([kind for kind, _ in found], expected.get(item['scene'], []))
                for _, button in found:
                    self.assertGreater(button.button[0], 1100)
                    self.assertLessEqual(button.button[3], 690)

    def test_covenant_and_mystic_debits_match_the_displayed_price(self):
        pairs = (
            ('20261002-151247-016', '20261002-151257-334', 184000),
            ('20261002-151314-999', '20261002-151325-876', 280000),
            ('20261002-151345-195', '20261002-151351-715', 184000),
            ('20261002-151456-902', '20261002-151504-141', 184000),
        )
        for before_id, after_id, cost in pairs:
            with self.subTest(before=before_id):
                before, after = (pixel_task(i)._read_shop_balance() for i in (before_id, after_id))
                self.assertEqual((before[0] - after[0], before[1] - after[1]), (cost, 0))

    def test_first_refresh_has_exactly_one_debit(self):
        before = pixel_task('20261002-151259-623')._read_shop_balance()
        after = pixel_task('20261002-151308-133')._read_shop_balance()
        self.assertEqual((before[0] - after[0], before[1] - after[1]), (0, 3))

    def test_gaps_between_refresh_captures_do_not_prove_one_payment(self):
        balances = [pixel_task(i)._read_shop_balance() for i in (
            '20261002-151351-715', '20261002-151441-164', '20261002-151456-902')]
        for before, after, actual_spend in zip(balances, balances[1:], (9, 12)):
            with self.subTest(spend=actual_spend):
                self.assertEqual(before[1] - after[1], actual_spend)
                payment = ShopPayment('refresh', before, submitted=True)
                self.assertFalse(payment.observe(after))
                self.assertFalse(payment.observe(after))

    def run_captured_purchase(self, before, popup, after, bottom=None, popup_frames=2):
        # Repetition means fresh observations of this supplied static view;
        # these are not independent adjacent frames of a recorded session.
        frames = [before] * 3 + [popup] * popup_frames + [after] * 5
        if bottom:
            frames += [bottom] * 5
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'), \
                patch.object(SecretShop, '_delay_to_auto_refresh'):
            device = CapturedReplayDevice(frames, clock)
            shop = SecretShop(Config(), device)
            shop._scrolled = bottom is None
            self.assertTrue(shop.run(skip_first_screenshot=True))
        self.assertEqual([index for index, name in device.actions if name == 'BUY_CONFIRM'],
                         [3] if popup_frames == 2 else [3, 6])
        self.assertNotIn('REFRESH_CONFIRM', [name for _, name in device.actions])
        self.assertIsNone(shop._payment)
        return shop

    def test_captured_covenant_purchase_and_sold_filter(self):
        shop = self.run_captured_purchase('20261002-151247-016', '20261002-151251-082',
                                         '20261002-151257-334', '20261002-151259-623')
        self.assertEqual((shop.covenant_bought, shop.mystic_bought), (1, 0))
        self.assertEqual(shop.config.stored.Gold.value, 196344384)

    def test_captured_mystic_purchase_and_sold_filter(self):
        shop = self.run_captured_purchase('20261002-151314-999', '20261002-151318-854',
                                         '20261002-151325-876', '20261002-151328-156')
        self.assertEqual((shop.covenant_bought, shop.mystic_bought), (0, 1))

    def test_captured_bottom_purchase(self):
        shop = self.run_captured_purchase('20261002-151345-195', '20261002-151349-222',
                                         '20261002-151351-715')
        self.assertEqual(shop.covenant_bought, 1)

    def test_captured_purchase_retries_persistent_confirmation(self):
        shop = self.run_captured_purchase('20261002-151247-016', '20261002-151251-082',
                                         '20261002-151257-334', '20261002-151259-623', popup_frames=6)
        self.assertEqual(shop.covenant_bought, 1)

    def run_captured_refresh(self, popup_frames):
        frames = ['20261002-151259-623'] * 3 + ['20261002-151302-626'] * popup_frames
        frames += ['20261002-151308-133'] * 10
        with ControlledClock() as clock, patch('tasks.secret_shop.secret_shop.UI.ui_goto'), \
                patch.object(SecretShop, '_delay_to_auto_refresh'):
            device = CapturedReplayDevice(frames, clock)
            shop = SecretShop(Config(SecretShop_OnlyFree=False), device)
            shop._scrolled = True
            # The supplied sequence ends at the refreshed top list. Stop this
            # pixel replay at the next swipe; a synthetic replay separately
            # covers scanning the bottom and enforcing the refresh limit.
            class CaptureBoundary(Exception):
                pass

            with patch.object(device, 'swipe', side_effect=CaptureBoundary):
                with self.assertRaises(CaptureBoundary):
                    shop.run(skip_first_screenshot=True)
        self.assertEqual(shop.refresh_count, 1)
        self.assertIsNone(shop._payment)
        self.assertEqual([index for index, name in device.actions if name == 'REFRESH_CONFIRM'],
                         [3] if popup_frames == 2 else [3, 6])
        self.assertEqual(shop.config.stored.Skystone.value, 1367)

    def test_captured_refresh_confirms_exact_debit(self):
        self.run_captured_refresh(popup_frames=2)

    def test_captured_refresh_retries_persistent_confirmation(self):
        self.run_captured_refresh(popup_frames=6)
