"""Real shop screenshots and explicit single-purchase replays, without a device."""

import json
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from module.config import server
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ControlledClock, fixture_image

server.set_lang('global_cn')

from tasks.store.current import CurrentStore  # noqa: E402
from tasks.store.friendship_gift import GiftCard, GiftPopup, PRICES, parse_amount, price_for_remaining  # noqa: E402
from tasks.store.purchase import ItemPurchasePlan, PurchaseResult  # noqa: E402
from tasks.store.assets.assets_store_friendship_gift import FRIENDSHIP_GIFT_ITEM  # noqa: E402
from tasks.store.assets.assets_store_actions import (  # noqa: E402
    BUY_CONFIRM_SINGLE, BUY_CONFIRM_MULTI, BUY_MIN, BUY_MAX, BUY_TIMES_MINUS, BUY_TIMES_PLUS,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'tests/fixtures/historical/manifest.json'


def gift_plan(target):
    return ItemPurchasePlan('friendship_gift_selection_chest', FRIENDSHIP_GIFT_ITEM,
                            desired_quantity=target, quantity_strategy='target', purchase_limit=10,
                            direct_click=True, single_purchase=True)


def shelf(remaining=10, balance=100000, price=None, **extra):
    if price is None:
        price = price_for_remaining(remaining)
    return scene('FREE_STORE_CHECK', 'FRIENDSHIP_GIFT_STORE_SELECTED',
                 card=GiftCard(remaining, price, balance), **extra)


def popup(remaining=10, balance=100000, price=None, **extra):
    return scene('FRIENDSHIP_GIFT_POPUP_CHECK', 'FRIENDSHIP_GIFT_POPUP_ITEM',
                 'FRIENDSHIP_GIFT_CONFIRM', 'FRIENDSHIP_GIFT_CANCEL', 'FREE_STORE_CHECK',
                 popup=GiftPopup(remaining, price or price_for_remaining(remaining), balance), **extra)


def captured(fixture):
    task = object.__new__(CurrentStore)
    task.config = SimpleNamespace(Emulator_GameLanguage='auto')
    task.device = SimpleNamespace(image=fixture_image(MANIFEST, fixture), stuck_record_add=Mock())
    task.interval_timer = {}
    return task


class CapturedGiftTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_real_shelves_prove_two_debits_and_rising_prices(self):
        cards = []
        for key, remaining, price, balance in (('gift-shelf-10', 10, 200, 92482),
                                             ('gift-shelf-9', 9, 400, 92282),
                                             ('gift-shelf-8', 8, 800, 91882)):
            task = captured(key)
            self.assertTrue(task._shop_ready())
            self.assertFalse(task._popup_ready())
            card = task._read_card()
            self.assertEqual(card, GiftCard(remaining, price, balance))
            cards.append(card)
        for before, after in zip(cards, cards[1:]):
            self.assertEqual(after.remaining, before.remaining - 1)
            self.assertEqual(after.balance, before.balance - before.price)

    def test_real_single_popup_has_eight_remaining_and_costs_eight_hundred(self):
        task = captured('gift-popup-8')
        self.assertTrue(task._popup_ready())
        # The old tab check also matches the dimmed background. Its active
        # highlight must identify a usable store before any click or exit.
        self.assertFalse(task._shop_ready())
        self.assertEqual(task._read_popup(), GiftPopup(8, 800, 91882))
        self.assertTrue(task.match_template_color(task_gift_confirm()))

    def test_price_information_overlay_is_not_purchase_evidence(self):
        task = captured('gift-price-info')
        self.assertIsNone(task._read_popup())

    def test_friendship_resource_bar_tracks_horizontal_position(self):
        original = captured('gift-shelf-10').device.image
        for offset in (-80, 60):
            task = captured('gift-shelf-10')
            # Explicit transformed pixels exercise the shared icon matcher;
            # these are not presented as additional emulator captures.
            image = original.copy()
            image[12:52, 228:1057] = 0
            image[12:52, 228 + offset:977 + offset] = original[12:52, 228:977]
            task.device.image = image
            self.assertEqual(task._balance(), 92482)

    def test_missing_boundary_icon_does_not_read_a_fixed_friendship_box(self):
        task = captured('gift-shelf-10')
        task.device.image[12:52, 530:575] = 0
        self.assertIsNone(task._balance())

    def test_item_fields_clipped_by_the_viewport_are_not_accepted(self):
        task = captured('gift-shelf-10')
        task.device.image = task.device.image[:, :870]
        self.assertIsNone(task._read_card())


def task_gift_confirm():
    from tasks.store.assets.assets_store_friendship_gift import FRIENDSHIP_GIFT_CONFIRM
    return FRIENDSHIP_GIFT_CONFIRM


class GiftReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, frames, guards=True):
        replay = InteractionReplay(CurrentStore, frames, self.clock, guards=guards)
        task = replay.task
        task._read_card = lambda: replay.frame.get('card')
        task._read_popup = lambda: replay.frame.get('popup')
        task._match_purchaseable_item = lambda asset: replay.frame.get('card') is not None
        task._has_item = lambda asset, image: replay.frame.get('item_visible', replay.frame.get('card') is not None)
        task.match_color = replay.appear
        task._save_debug_image = Mock()
        for name, asset in (('single', BUY_CONFIRM_SINGLE), ('multi', BUY_CONFIRM_MULTI),
                            ('min', BUY_MIN), ('max', BUY_MAX), ('times_minus', BUY_TIMES_MINUS),
                            ('times_plus', BUY_TIMES_PLUS)):
            setattr(task, f'buy_{"confirm_" if name in ("single", "multi") else ""}{name}_asset', asset)
        return replay

    def assert_payments(self, replay, expected):
        self.assertEqual(len(replay.clicks('FRIENDSHIP_GIFT_CONFIRM')), expected, replay.actions)
        for forbidden in ('BUY_MAX', 'BUY_MIN', 'BUY_TIMES_PLUS', 'BUY_TIMES_MINUS',
                          'BUY_CONFIRM_MULTI', 'BUY_CONFIRM_SINGLE'):
            self.assertFalse(replay.clicks(forbidden), replay.actions)

    def test_two_single_purchases_use_new_price_after_verified_debit(self):
        r = self.replay([shelf()] * 2 + [popup()] * 3 + [shelf(9, 99800)] * 4
                        + [popup(9, 99800)] * 3 + [shelf(8, 99400)] * 2)
        result = r.task._purchase_item(gift_plan(2))
        self.assertEqual((result.success, result.quantity), (True, 2))
        self.assert_payments(r, 2)
        self.assertEqual(r.clicks('FRIENDSHIP_GIFT_ITEM'), [0, 5])

    def test_manual_purchases_count_toward_weekly_target(self):
        r = self.replay([shelf(8)] * 2 + [popup(8)] * 3 + [shelf(7, 99200)] * 2)
        self.assertEqual(r.task._purchase_item(gift_plan(3)).quantity, 1)
        self.assert_payments(r, 1)

    def test_verified_target_exits_on_current_screenshot(self):
        r = self.replay([shelf(8)])
        result = r.task._purchase_item(gift_plan(2))
        self.assertEqual((result.success, result.quantity), (True, 0))
        self.assertFalse(r.actions)
        self.assertEqual(r.index, 0)

    def test_price_plateau_still_requires_each_stock_reduction(self):
        r = self.replay([shelf(5)] * 2 + [popup(5)] * 3 + [shelf(5)] * 6
                        + [shelf(4, 93600)] * 4 + [popup(4, 93600)] * 3 + [shelf(3, 87200)] * 2)
        result = r.task._purchase_item(gift_plan(7))
        self.assertEqual((result.success, result.quantity), (True, 2))
        self.assert_payments(r, 2)
        self.assertGreater(r.clicks('FRIENDSHIP_GIFT_ITEM')[1], 10)

    def test_stock_and_balance_updates_must_agree_before_another_payment(self):
        delayed = [shelf(9, 100000)] * 4 + [shelf(10, 99800)] * 4
        r = self.replay([shelf()] * 2 + [popup()] * 3 + delayed + [shelf(9, 99800)] * 2)
        result = r.task._purchase_item(gift_plan(1))
        self.assertEqual((result.success, result.quantity), (True, 1))
        self.assertEqual(len(r.clicks('FRIENDSHIP_GIFT_ITEM')), 1)
        self.assert_payments(r, 1)

    def test_balance_shortfall_after_first_purchase_leaves_remaining_target(self):
        r = self.replay([shelf(balance=500)] * 2 + [popup(balance=500)] * 3 + [shelf(9, 300)] * 4)
        result = r.task._purchase_item(gift_plan(2))
        self.assertEqual((result.success, result.quantity), (True, 1))
        self.assert_payments(r, 1)

    def test_insufficient_balance_never_opens_a_purchase(self):
        r = self.replay([shelf(balance=199)] * 2)
        self.assertTrue(r.task._purchase_item(gift_plan(10)).success)
        self.assertFalse(r.actions)

    def test_dialog_rechecks_balance_before_payment(self):
        r = self.replay([shelf()] * 2 + [popup(balance=199)] * 4 + [shelf(balance=199)] * 2)
        self.assertTrue(r.task._purchase_item(gift_plan(1)).success)
        self.assertTrue(r.clicks('FRIENDSHIP_GIFT_CANCEL'))
        self.assert_payments(r, 0)

    def test_popup_price_or_stock_mismatch_cancels_and_reports_failure(self):
        for dialog in (popup(price=400), popup(9)):
            with self.subTest(dialog=dialog):
                r = self.replay([shelf()] * 2 + [dialog] * 4 + [shelf()] * 2)
                self.assertFalse(r.task._purchase_item(gift_plan(1)).success)
                self.assertTrue(r.clicks('FRIENDSHIP_GIFT_CANCEL'))
                self.assert_payments(r, 0)

    def test_unexpected_live_card_price_is_not_paid(self):
        r = self.replay([shelf(price=400)] * 2)
        self.assertFalse(r.task._purchase_item(gift_plan(1)).success)
        self.assertFalse(r.actions)

    def test_dropped_card_click_retries_with_production_interval(self):
        r = self.replay([shelf()] * 8 + [popup()] * 3 + [shelf(9, 99800)] * 2)
        self.assertTrue(r.task._purchase_item(gift_plan(1)).success)
        r.assert_retried(self, 'FRIENDSHIP_GIFT_ITEM', 2)
        self.assert_payments(r, 1)

    def test_unresolved_confirm_does_not_pay_again_or_claim_completion(self):
        r = self.replay([shelf()] * 2 + [popup()] * 145, guards=False)
        result = r.task._purchase_item(gift_plan(2))
        self.assertEqual((result.success, result.quantity), (False, 0))
        self.assert_payments(r, 1)

    def test_stale_popup_is_cancelled_before_a_fresh_card_is_selected(self):
        r = self.replay([popup()] * 4 + [shelf()] * 3 + [popup()] * 3 + [shelf(9, 99800)] * 2)
        self.assertEqual(r.task._purchase_item(gift_plan(1)).quantity, 1)
        self.assertLess(r.clicks('FRIENDSHIP_GIFT_CANCEL')[0], r.clicks('FRIENDSHIP_GIFT_ITEM')[0])
        self.assert_payments(r, 1)

    def test_network_overlay_keeps_payment_intent_and_breaks_stability(self):
        network = scene('NETWORK_ERROR_ABNORMAL')
        r = self.replay([shelf()] * 2 + [popup()] * 3 + [network, shelf(9, 99800)])
        r.task.handle_network_error = Mock(return_value=False)
        def additional():
            self.assertNotIn('NETWORK_ERROR_ABNORMAL', r.frame['buttons'])
            return False
        r.task.ui_additional = Mock(side_effect=additional)
        self.assertEqual(r.task._purchase_item(gift_plan(1)).quantity, 1)
        r.task.handle_network_error.assert_called_once_with()
        self.assertEqual(r.index, 6)
        self.assert_payments(r, 1)

    def test_dimmed_store_does_not_finish_or_click_the_background(self):
        dimmed = scene('FREE_STORE_CHECK', card=GiftCard(8, 800, 99400))
        r = self.replay([dimmed] * 6 + [shelf(8, 99400)])
        self.assertTrue(r.task._purchase_item(gift_plan(2)).success)
        self.assertEqual(r.index, 6)
        self.assertFalse(r.actions)

    def test_visible_unreadable_card_does_not_scroll_or_pay(self):
        unreadable = scene('FREE_STORE_CHECK', 'FRIENDSHIP_GIFT_STORE_SELECTED',
                           card=None, item_visible=True)
        r = self.replay([unreadable] * 6 + [shelf(8)])
        self.assertTrue(r.task._purchase_item(gift_plan(2)).success)
        self.assertEqual(r.index, 6)
        self.assertFalse(r.actions)

    def test_unknown_payment_result_never_scrolls_or_opens_another_gift(self):
        unreadable = scene('FREE_STORE_CHECK', 'FRIENDSHIP_GIFT_STORE_SELECTED', card=None)
        r = self.replay([shelf()] * 2 + [popup()] * 3 + [unreadable] * 130, guards=False)
        result = r.task._purchase_item(gift_plan(2))
        self.assertEqual((result.success, result.quantity), (False, 0))
        self.assertEqual(len(r.clicks('FRIENDSHIP_GIFT_ITEM')), 1)
        self.assertFalse(r.clicks('swipe'))
        self.assert_payments(r, 1)

    def test_last_gift_is_confirmed_only_by_zero_stock_and_exact_debit(self):
        r = self.replay([shelf(1)] * 2 + [popup(1)] * 3 + [shelf(0, 93600)] * 2)
        self.assertEqual(r.task._purchase_item(gift_plan(10)).quantity, 1)
        self.assert_payments(r, 1)

    def test_missing_target_search_is_bounded_and_never_confirms(self):
        r = self.replay([scene('FREE_STORE_CHECK', 'FRIENDSHIP_GIFT_STORE_SELECTED', card=None)] * 145,
                        guards=False)
        self.assertFalse(r.task._purchase_item(gift_plan(1)).success)
        self.assertEqual(sum(action == 'swipe' for _, action in r.actions), 6)
        self.assert_payments(r, 0)


class GiftIntegrationTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_gift_uses_existing_free_store_plan_and_target_limit(self):
        config = SimpleNamespace(Emulator_PackageName='com.stove.epic7.google',
                                 StoreDaily_BuyDailyFreeItem=False, StoreDaily_BuyFriendshipMobility40=False,
                                 StoreDaily_BuyFriendshipArenaFlag=False,
                                 StoreWeekly_BuyFriendshipArtifactEnhancementStone=0,
                                 StoreWeekly_BuyFriendshipGiftSelectionChest=12)
        task = CurrentStore(config, device=SimpleNamespace())
        items = task._enabled_items(task._build_free_store_items())
        self.assertEqual(items, [gift_plan(10)])

    def test_existing_page_runner_reuses_entry_statistics_and_settle(self):
        task = object.__new__(CurrentStore)
        task.device = SimpleNamespace(screenshot=Mock())
        task.purchase_stats = {}
        task._wait_purchase_cooldown_before_switch = Mock()
        task._record_purchase_time = Mock()
        task._wait_store_ready_after_purchase = Mock(return_value=True)
        task._item_ready_for_purchase = Mock(return_value=False)
        task._purchase_item = Mock(return_value=PurchaseResult(True, 2))
        enter = Mock()
        self.assertTrue(task._run_store_page_items('free store', enter, [gift_plan(2)]))
        enter.assert_called_once_with(skip_first_screenshot=True)
        task._purchase_item.assert_called_once_with(gift_plan(2))
        self.assertEqual(task.purchase_stats, {'friendship_gift_selection_chest': 2})
        task._wait_store_ready_after_purchase.assert_called_once_with()

    def test_failed_payment_stops_the_existing_page_runner(self):
        task = object.__new__(CurrentStore)
        task.device = SimpleNamespace(screenshot=Mock())
        task._wait_purchase_cooldown_before_switch = Mock()
        task._purchase_item = Mock(return_value=PurchaseResult(False, 0))
        self.assertFalse(task._run_store_page_items('free store', Mock(), [gift_plan(2)]))

    def test_failed_sequence_keeps_previously_verified_purchase_count(self):
        task = object.__new__(CurrentStore)
        task.device = SimpleNamespace(screenshot=Mock())
        task.purchase_stats = {}
        task._wait_purchase_cooldown_before_switch = Mock()
        task._purchase_item = Mock(return_value=PurchaseResult(False, 1))
        self.assertFalse(task._run_store_page_items('free store', Mock(), [gift_plan(2)]))
        self.assertEqual(task.purchase_stats, {'friendship_gift_selection_chest': 1})

    def test_price_schedule_and_strict_amounts(self):
        self.assertEqual(PRICES, (200, 400, 800, 1600, 3200, 6400, 6400, 6400, 6400, 6400))
        for remaining, expected in zip(range(10, 0, -1), PRICES):
            self.assertEqual(price_for_remaining(remaining), expected)
        self.assertIsNone(price_for_remaining(0))
        self.assertIsNone(price_for_remaining(11))
        self.assertEqual(parse_amount('6,400'), 6400)
        for value in ('6,40', '6400金币', '6O0', '', '-200'):
            self.assertIsNone(parse_amount(value))

    def test_translation_source_does_not_leak_into_runtime_arguments(self):
        from module.config.config_updater import ConfigGenerator
        import yaml
        source = yaml.safe_load((ROOT / 'module/config/argument/argument.yaml').read_text(encoding='utf-8'))
        gift = source['StoreWeekly']['BuyFriendshipGiftSelectionChest']
        self.assertEqual(gift['value'], 0)
        self.assertEqual(gift['option'], list(range(11)))
        self.assertEqual(set(gift), {'value', 'option'})
        argument = ConfigGenerator().argument['StoreWeekly']['BuyFriendshipGiftSelectionChest']
        self.assertNotIn('i18n', argument)
        self.assertEqual(argument['value'], 0)

    def test_generated_target_defaults_locales_and_existing_selection(self):
        from module.config.config_generated import GeneratedConfig
        from module.config.config_updater import ConfigUpdater
        key = 'BuyFriendshipGiftSelectionChest'
        self.assertEqual(GeneratedConfig.StoreWeekly_BuyFriendshipGiftSelectionChest, 0)
        args = json.loads((ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        self.assertEqual(args['Store']['StoreWeekly'][key]['option'], list(range(11)))
        self.assertNotIn('i18n', args['Store']['StoreWeekly'][key])
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        self.assertEqual(template['Store']['StoreWeekly'][key], 0)
        for lang in ('zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'es-ES'):
            locale = json.loads((ROOT / f'module/config/i18n/{lang}.json').read_text(encoding='utf-8'))
            entry = locale['StoreWeekly'][key]
            self.assertTrue(entry['name'])
            self.assertTrue(entry['help'])
            self.assertNotIn('StoreWeekly.', entry['name'])
            self.assertEqual([entry[str(n)] for n in range(11)], list(map(str, range(11))))
        # ConfigUpdater receives only explicit memory data, never an account file.
        updated = ConfigUpdater().config_update({'Store': {'StoreWeekly': {key: 7}}})
        self.assertEqual(updated['Store']['StoreWeekly'][key], 7)

    def test_unsupported_servers_import_and_keep_gift_disabled(self):
        script = """
from types import SimpleNamespace
from module.config import server
server.set_lang({lang!r})
from tasks.store.current import CurrentStore
task = CurrentStore(SimpleNamespace(Emulator_PackageName={package!r},
    StoreDaily_BuyDailyFreeItem=False, StoreDaily_BuyFriendshipMobility40=False,
    StoreDaily_BuyFriendshipArenaFlag=False, StoreWeekly_BuyFriendshipArtifactEnhancementStone=0,
    StoreWeekly_BuyFriendshipGiftSelectionChest=10), device=SimpleNamespace())
assert not task._enabled_items(task._build_free_store_items())
"""
        for lang, package in (('cn', 'com.zlongame.cn.epicseven'), ('global_en', 'com.stove.epic7.google')):
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c',
                                     script.format(lang=lang, package=package)],
                                    cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
