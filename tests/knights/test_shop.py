"""Captured pixels and explicit synthetic replays; no real device or account."""

import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import yaml

from module.config import server
from tests.support.offline import ControlledClock, fixture_image, record_action, record_frame

server.set_lang('global_cn')

from module.base.button import ClickButton  # noqa: E402
from module.exception import RequestHumanTakeover  # noqa: E402
from tasks.knights.shop import (  # noqa: E402
    ITEMS, KnightsShop, PurchaseAttempt, ShopCard, BUY_CONFIRM_MULTI,
    BUY_CONFIRM_SINGLE, enabled_purchases, parse_amount, parse_stock, shop_period,
    BUY_MIN, BUY_MAX, BUY_TIMES_PLUS, BUY_TIMES_MINUS,
)
from tasks.store.purchase import resolve_period_purchase_quantity  # noqa: E402
from tasks.base.ui import UI  # noqa: E402
from tasks.knights.assets.assets_knights_activity_entries import ACTIVITY_PANEL_CHECK  # noqa: E402
from tasks.knights.assets.assets_knights_shop import (  # noqa: E402
    SHOP_PURCHASE_FINAL_CHECK, SHOP_PURCHASE_FINAL_CONFIRM, SHOP_PURCHASE_FINAL_CANCEL,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'tests/fixtures/knights/manifest.json'
CAPTURES = ('20261001-171607-666', '20261001-171621-454', '20261001-171626-703',
            '20261002-160820-406', '20261002-160823-507', '20261002-160938-463')
BLOOM = next(item for item in ITEMS if item.option.endswith('EpicSpiritBloom'))
MOLAGORA_ITEM = next(item for item in ITEMS if item.option.endswith('_Molagora'))
FINAL_CAPTURE = '20261003-100049-079'
REORDERED_CAPTURES = ('20261003-110305-160', '20261003-110308-852',
                      '20261003-110314-759', '20261003-110323-175')
PAID_CATALYST_CAPTURE = '20261003-184342-562'
CATALYST = next(item for item in ITEMS if item.option.endswith('_CatalystChest'))


def captured_task(fixture):
    task = KnightsShop.__new__(KnightsShop)
    task.config = SimpleNamespace(Emulator_GameLanguage='auto')
    task.device = SimpleNamespace(image=fixture_image(MANIFEST, fixture))
    task.appear = lambda asset, **kwargs: asset.match_template(task.device.image)
    return task


def history_task(**options):
    task = KnightsShop.__new__(KnightsShop)
    task.config = SimpleNamespace(
        Emulator_PackageName='OVERSEA-Play', Scheduler_ServerUpdate='02:00',
        **options,
    )
    return task


class CapturedShopTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_catalyst_five_stock_after_payment_is_read_without_letter_guessing(self):
        task = captured_task(PAID_CATALYST_CAPTURE)
        cards = task._cards(CATALYST)
        self.assertIsNotNone(cards)
        self.assertEqual(len(cards), 1)
        self.assertEqual((cards[0].remaining, cards[0].active, cards[0].price), (5, True, 180))
        self.assertEqual(cards[0].button.button, (379, 344, 538, 373))
        # The regression must fix the input pixels, not guess a letter's
        # meaning and silently accept unverified stock as payment evidence.
        self.assertIsNone(parse_stock('G/10'))

    def test_shop_body_requires_product_images_after_tab_selection(self):
        task = captured_task(CAPTURES[4])
        self.assertTrue(task._shop_content_ready())
        task.device.image[143:649, 289:1224] = 0
        self.assertTrue(task._on_shop())
        self.assertFalse(task._shop_content_ready())
        self.assertFalse(captured_task(CAPTURES[3])._shop_content_ready())

    def test_all_eighteen_products_have_correct_stock_currency_and_price(self):
        found = set()
        for fid in (CAPTURES[0], CAPTURES[1], CAPTURES[4]):
            task = captured_task(fid)
            for item in ITEMS:
                cards = task._cards(item)
                self.assertIsNotNone(cards, item.option)
                if cards:
                    self.assertEqual(len(cards), 1, item.option)
                    self.assertEqual(cards[0].price, item.price, item.option)
                    self.assertTrue(cards[0].active)
                    self.assertGreater(cards[0].remaining, 0)
                    self.assertGreaterEqual(cards[0].button.button[1], 340)
                    found.add(item.option)
        self.assertEqual(found, {item.option for item in ITEMS})

    def test_identical_reforging_titles_use_different_periods_and_currencies(self):
        task = captured_task(CAPTURES[1])
        weekly, monthly = [item for item in ITEMS if item.option.endswith('_ReforgingStoneChest')]
        week_card, month_card = task._cards(weekly)[0], task._cards(monthly)[0]
        self.assertEqual((week_card.remaining, week_card.price, week_card.button.button), (1, 150, (599, 601, 758, 630)))
        self.assertEqual((month_card.remaining, month_card.price, month_card.button.button), (4, 350, (819, 344, 978, 373)))

    def test_sold_out_is_observed_zero_not_ocr_failure(self):
        task = captured_task(CAPTURES[2])
        item = next(item for item in ITEMS if item.option == 'KnightsShopMonthly_ReforgingStoneChest')
        card = task._cards(item)[0]
        self.assertEqual(card.remaining, 0)
        self.assertFalse(card.active)
        self.assertIsNone(card.price)

    def test_support_and_dimmed_popup_are_not_a_usable_shop(self):
        for fid in (CAPTURES[3], CAPTURES[5]):
            task = captured_task(fid)
            self.assertFalse(task._on_shop())
            self.assertEqual(task._cards(BLOOM), [])

    def test_multi_purchase_popup_uses_buttons_and_numeric_counter_without_text_ocr(self):
        task = captured_task(CAPTURES[5])
        task._text = Mock(side_effect=AssertionError('popup text OCR is forbidden'))
        with patch('tasks.knights.shop.Ocr.detect_and_ocr', side_effect=AssertionError('item text OCR is forbidden')):
            self.assertEqual(task._popup_evidence(PurchaseAttempt(BLOOM, 10, 1), *task._popup()), (1, 10))
        self.assertIsNone(task._popup_evidence(PurchaseAttempt(BLOOM, 9, 1), *task._popup()))

    def test_balances_are_read_with_normalized_ocr_language(self):
        task = captured_task(CAPTURES[4])
        for currency, expected in (('armband', 275), ('crest', 900), ('proof', 70542)):
            item = next(item for item in ITEMS if item.currency == currency)
            self.assertEqual(task._balance(item), expected)

    def test_horizontal_translation_moves_only_the_corresponding_price_button(self):
        task = captured_task(CAPTURES[0])
        original = task.device.image.copy()
        task.device.image[145:640, 289:1224] = 0
        task.device.image[145:640, 409:1224] = original[145:640, 289:1104]
        card = task._cards(BLOOM)[0]
        self.assertEqual(card.button.button, (505, 601, 664, 630))
        self.assertEqual(card.price, 600)

    def test_clipped_card_is_excluded_even_when_the_title_is_visible(self):
        task = captured_task(CAPTURES[0])
        original = task.device.image.copy()
        task.device.image[145:640, 289:1224] = 0
        task.device.image[145:640, 289:1144] = original[145:640, 369:1224]
        self.assertEqual(task._cards(BLOOM), [])

    def test_corrupt_stock_is_unknown_and_wrong_currency_cannot_supply_a_price(self):
        task = captured_task(CAPTURES[0])
        task.device.image[436:466, 495:558] = 0
        self.assertIsNone(task._cards(BLOOM))
        task = captured_task(CAPTURES[0])
        task.device.image[601:630, 430:458] = 0
        card = task._cards(BLOOM)[0]
        self.assertIsNone(card.price)

    def test_small_swipes_overlap_the_previous_view_in_both_directions(self):
        task = KnightsShop.__new__(KnightsShop)
        task.device = SimpleNamespace(swipe=Mock())
        task._swipe()
        task._swipe(backwards=True)
        self.assertEqual([call.args for call in task.device.swipe.call_args_list],
                         [((1060, 392), (788, 392)), ((788, 392), (1060, 392))])
        self.assertEqual(task.SCROLL_START[0] - task.SCROLL_END[0], 340 * 0.8)
        self.assertTrue(all(call.kwargs == {'duration': (0.3, 0.35)}
                            for call in task.device.swipe.call_args_list))

    def test_reordered_partial_tail_is_not_a_finished_scan(self):
        task = captured_task(REORDERED_CAPTURES[0])
        monthly = next(item for item in ITEMS if item.option == 'KnightsShopMonthly_ReforgingStoneChest')
        # The former last item is now inside the list; the sold-out monthly
        # box is still clipped at the right. A title cannot prove the edge.
        self.assertEqual(task._cards(monthly), [])
        # One forward step shows the tail. The business loop must continue
        # past the former end marker and read the sold-out card's own stock,
        # without any initial backwards probes.
        with ControlledClock() as clock:
            replay = captured_replay_task([REORDERED_CAPTURES[0]] * 4 + [REORDERED_CAPTURES[1]] * 4, clock)
            self.assertTrue(replay._execute_shop([(monthly, 5)]))
            self.assertEqual([action[1] for action in replay.device.actions], ['swipe'])
            self.assertEqual(replay.device.actions[-1][2], ((1060, 392), (788, 392)))

    def test_mystic_is_recognized_beside_sold_out_molagora_by_image(self):
        task = captured_task(REORDERED_CAPTURES[2])
        mystic = task._cards(ITEMS[0])[0]
        molagora = task._cards(MOLAGORA_ITEM)[0]
        self.assertEqual((mystic.remaining, mystic.active, mystic.price), (1, True, 200))
        self.assertEqual(mystic.button.button, (718, 344, 877, 373))
        self.assertEqual((molagora.remaining, molagora.active, molagora.price), (0, False, None))

    def test_four_digit_crest_balance_moves_the_whole_header(self):
        task = captured_task(REORDERED_CAPTURES[2])
        for currency, expected in (('armband', 275), ('crest', 1010), ('proof', 70177)):
            item = next(item for item in ITEMS if item.currency == currency)
            self.assertEqual(task._balance(item), expected, currency)

    def test_balance_icons_move_with_numbers_and_reject_missing_or_duplicate_icons(self):
        task = captured_task(REORDERED_CAPTURES[2])
        original = task.device.image.copy()
        task.device.image[76:110, 650:1210] = 0
        task.device.image[76:110, 650:1150] = original[76:110, 710:1210]
        for currency, expected in (('armband', 275), ('crest', 1010), ('proof', 70177)):
            item = next(item for item in ITEMS if item.currency == currency)
            self.assertEqual(task._balance(item), expected, currency)
        task = captured_task(REORDERED_CAPTURES[2])
        task.device.image[81:103, 1041:1064] = 0
        self.assertIsNone(task._balance(ITEMS[0]))
        task = captured_task(REORDERED_CAPTURES[2])
        task.device.image[81:103, 900:923] = task.device.image[81:103, 1041:1064]
        self.assertIsNone(task._balance(ITEMS[0]))

    def test_sold_out_monthly_box_at_tail_keeps_its_own_period(self):
        task = captured_task(REORDERED_CAPTURES[1])
        weekly, monthly = [item for item in ITEMS if item.option.endswith('_ReforgingStoneChest')]
        week_card, month_card = task._cards(weekly)[0], task._cards(monthly)[0]
        self.assertEqual((week_card.remaining, week_card.price), (1, 150))
        self.assertEqual((month_card.remaining, month_card.active, month_card.price), (0, False, None))
        self.assertEqual(month_card.button.button, (1039, 344, 1198, 373))
        # A preceding view can contain the already bought molagora without
        # showing mystic at all. Continue searching rather than completing.
        preceding = captured_task(REORDERED_CAPTURES[3])
        self.assertEqual(preceding._cards(ITEMS[0]), [])
        self.assertEqual(preceding._cards(MOLAGORA_ITEM)[0].remaining, 0)

    def test_activity_panel_is_not_closed_by_the_generic_ad_handler(self):
        for fid in CAPTURES[:5]:
            task = captured_task(fid)
            task.handle_ui_recovery = Mock(return_value=False)
            task.handle_ad_buff_x_close = Mock(side_effect=AssertionError('activity panel treated as ad'))
            self.assertTrue(ACTIVITY_PANEL_CHECK.match_template_color(task.device.image, threshold=25))
            self.assertFalse(UI.ui_additional(task))
            task.handle_ad_buff_x_close.assert_not_called()

    def test_dimmed_activity_panel_does_not_block_real_popup_handlers(self):
        task = captured_task(CAPTURES[5])
        task.handle_ui_recovery = Mock(return_value=False)
        task.appear_then_click = Mock(return_value=False)
        task.handle_broadcast = Mock(return_value=False)
        task.handle_skip_tutorial = Mock(return_value=False)
        task.handle_ad_buff_x_close = Mock(return_value=True)
        self.assertFalse(ACTIVITY_PANEL_CHECK.match_template_color(task.device.image, threshold=25))
        self.assertTrue(UI.ui_additional(task))
        task.handle_ad_buff_x_close.assert_called_once()

    def test_captured_final_confirmation_matches_dialog_and_button_without_ocr(self):
        task = captured_task(FINAL_CAPTURE)
        task._text = Mock(side_effect=AssertionError('final text OCR is forbidden'))
        self.assertFalse(task._on_shop())
        self.assertTrue(task.appear(SHOP_PURCHASE_FINAL_CHECK))
        self.assertTrue(task.appear(SHOP_PURCHASE_FINAL_CONFIRM))
        self.assertTrue(task.appear(SHOP_PURCHASE_FINAL_CANCEL))
        with patch('tasks.knights.shop.Ocr', side_effect=AssertionError('final OCR is forbidden')):
            self.assertTrue(task._final_popup_evidence(PurchaseAttempt(MOLAGORA_ITEM, 5, 5, confirmed=True)))

    def test_final_confirmation_is_distinct_from_selection_and_rejects_wrong_context(self):
        for fid in CAPTURES:
            self.assertFalse(captured_task(fid).appear(SHOP_PURCHASE_FINAL_CHECK))
        task = captured_task(FINAL_CAPTURE)
        for attempt in (PurchaseAttempt(MOLAGORA_ITEM, 5, 5), PurchaseAttempt(MOLAGORA_ITEM, 5, 0, confirmed=True),
                        PurchaseAttempt(MOLAGORA_ITEM, 4, 5)):
            self.assertIsNone(task._final_popup_evidence(attempt))
        task.device.image[499:563, 786:878] = 0
        self.assertIsNone(task._final_popup_evidence(PurchaseAttempt(MOLAGORA_ITEM, 5, 5, confirmed=True)))

    def test_manastone_levels_use_images_even_when_titles_cannot_be_read(self):
        task = captured_task(CAPTURES[4])
        original_text = task._text
        def numeric_only(area, name):
            self.assertNotIn('Title', name)
            return original_text(area, name)
        task._text = numeric_only
        level85, level88 = [item for item in ITEMS if 'ManastoneChest' in item.option]
        card85, card88 = task._cards(level85)[0], task._cards(level88)[0]
        self.assertEqual((card85.price, card88.price), (70, 80))
        self.assertEqual((card85.button.button[1], card88.button.button[1]), (344, 601))

    def test_manastone_level_match_moves_with_the_card_and_rejects_missing_digits(self):
        task = captured_task(CAPTURES[4])
        original = task.device.image.copy()
        task.device.image[145:640, 289:1224] = 0
        task.device.image[145:640, 409:1224] = original[145:640, 289:1104]
        level85, level88 = [item for item in ITEMS if 'ManastoneChest' in item.option]
        self.assertEqual(task._cards(level85)[0].price, 70)
        self.assertEqual(task._cards(level88)[0].price, 80)
        task.device.image[153:176, 703:728] = 0
        task.device.image[410:433, 703:728] = 0
        self.assertEqual(task._cards(level85), [])
        self.assertEqual(task._cards(level88), [])


class ShopRuleTests(unittest.TestCase):
    def test_period_records_change_at_server_week_and_month_refresh(self):
        cases = (
            (ITEMS[0], datetime(2026, 10, 5, 1, 59, 59), 'week:2026-09-28'),
            (ITEMS[0], datetime(2026, 10, 5, 2), 'week:2026-10-05'),
            (BLOOM, datetime(2026, 10, 1, 1, 59, 59), 'month:2026-09'),
            (BLOOM, datetime(2026, 10, 1, 2), 'month:2026-10'),
            (BLOOM, datetime(2027, 1, 1, 1, 59, 59), 'month:2026-12'),
            (BLOOM, datetime(2027, 1, 1, 2), 'month:2027-01'),
            (BLOOM, datetime(2028, 3, 1, 1, 59, 59), 'month:2028-02'),
        )
        for item, now, expected in cases:
            self.assertEqual(shop_period(item, now, '02:00', timedelta()), expected)
            self.assertEqual(shop_period(item, now + timedelta(hours=7), '02:00', timedelta(hours=7)), expected)

    def test_cached_week_and_month_expire_independently(self):
        task = history_task(**{ITEMS[0].option: 1, BLOOM.option: 2})
        with patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
            task._record_shop_purchase(ITEMS[0], 1, datetime(2026, 9, 30, 12))
            task._record_shop_purchase(BLOOM, 2, datetime(2026, 9, 30, 12))
            self.assertEqual(task._pending_shop_purchases(datetime(2026, 10, 1, 1, 59)), [])
            self.assertEqual(task._pending_shop_purchases(datetime(2026, 10, 1, 2)), [(BLOOM, 2)])
            task._record_shop_purchase(BLOOM, 2, datetime(2026, 10, 1, 3))
            self.assertEqual(task._pending_shop_purchases(datetime(2026, 10, 5, 2)), [(ITEMS[0], 1)])

    def test_changing_same_period_target_uses_observed_purchase_count(self):
        task = history_task(**{BLOOM.option: 3})
        now = datetime(2026, 10, 3, 12)
        with patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
            task._record_shop_purchase(BLOOM, 4, now)
            old_records = task.config.KnightsShopRuntime_Purchases
            self.assertEqual(task._pending_shop_purchases(now), [])
            setattr(task.config, BLOOM.option, 5)
            self.assertEqual(task._pending_shop_purchases(now), [(BLOOM, 5)])
            task._record_shop_purchase(BLOOM, 5, now)
            self.assertEqual(old_records[BLOOM.option]['purchased'], 4)
            self.assertEqual(task._pending_shop_purchases(now), [])
            setattr(task.config, BLOOM.option, 0)
            self.assertEqual(task._pending_shop_purchases(now), [])

    def test_invalid_future_or_other_server_records_do_not_skip(self):
        task = history_task(**{BLOOM.option: 3})
        now = datetime(2026, 10, 3, 12)
        with patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
            task._record_shop_purchase(BLOOM, 3, now)
            valid = dict(task.config.KnightsShopRuntime_Purchases[BLOOM.option])
            bad = [None, [], 'bad', {}, *[
                dict(valid, **changes) for changes in (
                    {'purchased': True}, {'purchased': '3'}, {'purchased': -1}, {'purchased': 11},
                    {'period': 'month:2026-11'}, {'scope': 'CN-Official|02:00'},
                    {'scope': 'OVERSEA-Play|03:00'}, {'checked_at': 'bad'},
                    {'checked_at': '2026-10-03T13:00:00'}, {'checked_at': '2026-10-03T12:00:00+08:00'},
                    {'checked_at': '2026-09-03T12:00:00'},
                )
            ]]
            for record in bad:
                task.config.KnightsShopRuntime_Purchases = {BLOOM.option: record}
                self.assertEqual(task._pending_shop_purchases(now), [(BLOOM, 3)], record)
            for records in (None, [], 'bad'):
                task.config.KnightsShopRuntime_Purchases = records
                self.assertEqual(task._pending_shop_purchases(now), [(BLOOM, 3)])

    def test_runtime_schema_is_hidden_bound_and_preserves_records(self):
        from module.config.config_updater import ConfigUpdater
        args = json.loads((ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        arg = args['Knights']['KnightsShopRuntime']['Purchases']
        self.assertEqual((arg['display'], arg['type'], arg['value'], arg['keepvalue']), ('hide', 'dict', {}, True))
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        self.assertEqual(template['Knights']['KnightsShopRuntime']['Purchases'], {})
        record = {'sample': {'period': 'month:2026-10', 'purchased': 3}}
        template['Knights']['KnightsShopRuntime']['Purchases'] = record
        updated = ConfigUpdater().config_update(template)
        self.assertEqual(updated['Knights']['KnightsShopRuntime']['Purchases'], record)

    def test_bound_config_saves_records_and_reload_keeps_profiles_separate(self):
        from module.config.config import AzurLaneConfig
        from module.config.config_updater import ConfigUpdater
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        template['Alas']['Emulator']['PackageName'] = 'OVERSEA-Play'
        template['Knights']['KnightsShopMonthly']['EpicSpiritBloom'] = 3
        # All account reads/writes use this explicit in-memory JSON store.
        # Exercise the actual binding/save/reload path without opening a real
        # profile or treating a SimpleNamespace mutation as persistence proof.
        profiles = {name: json.dumps(template) for name in ('offline-a', 'offline-b')}
        writes = []
        def read(config, name, **kwargs):
            return ConfigUpdater().config_update(json.loads(profiles[name]))
        def write(config, name, data, **kwargs):
            writes.append(name)
            profiles[name] = json.dumps(data, default=lambda value: value.isoformat())
        now = datetime(2026, 10, 3, 12)
        with patch.object(AzurLaneConfig, 'read_file', read), patch.object(AzurLaneConfig, 'write_file', write), \
                patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
            task = history_task()
            task.config = AzurLaneConfig('offline-a', task='Knights')
            task._record_shop_purchase(BLOOM, 3, now)
            self.assertEqual(writes, ['offline-a'])
            task.config = AzurLaneConfig('offline-a', task='Knights')
            self.assertEqual(task._pending_shop_purchases(now), [])
            task.config = AzurLaneConfig('offline-b', task='Knights')
            self.assertEqual(task._pending_shop_purchases(now), [(BLOOM, 3)])

    def test_logged_molagora_counter_noise_uses_existing_store_parser(self):
        task = KnightsShop.__new__(KnightsShop)
        task.config = SimpleNamespace(Emulator_GameLanguage='cn')
        task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        task.appear = Mock(return_value=True)
        task._text = Mock(side_effect=AssertionError('popup text OCR is forbidden'))
        with patch('tasks.store.purchase.StorePurchaseCounterOcr.model') as model:
            for raw in ('1/5双精灵', '¥1/5'):
                model.ocr_single_line.return_value = (raw, 0.99)
                self.assertEqual(task._popup_evidence(PurchaseAttempt(MOLAGORA_ITEM, 5, 5),
                                                     BUY_CONFIRM_MULTI, True), (1, 5))

    def test_single_popup_requires_matched_button_and_uses_no_text_ocr(self):
        task = KnightsShop.__new__(KnightsShop)
        task.config = SimpleNamespace(Emulator_GameLanguage='cn')
        task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        task.appear = Mock(return_value=True)
        task._text = Mock(side_effect=AssertionError('single popup text OCR is forbidden'))
        with patch('tasks.knights.shop.Ocr', side_effect=AssertionError('single popup OCR is forbidden')):
            result = task._popup_evidence(PurchaseAttempt(ITEMS[0], 1, 1), BUY_CONFIRM_SINGLE, False)
        self.assertEqual(result, (1, 1))
        task.appear.return_value = False
        self.assertIsNone(task._popup_evidence(PurchaseAttempt(ITEMS[0], 1, 1), BUY_CONFIRM_SINGLE, False))
        task._text.assert_not_called()

    def test_selection_counter_rejects_invalid_values_and_other_stock_totals(self):
        task = KnightsShop.__new__(KnightsShop)
        task.config = SimpleNamespace(Emulator_GameLanguage='cn')
        task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        task.appear = Mock(return_value=True)
        with patch('tasks.knights.shop.ocr_purchase_counter') as counter:
            for values in ((0, 0, 0), (0, 5, 5), (6, -1, 5), (1, 3, 4), (5, 5, 10)):
                counter.return_value = values
                self.assertIsNone(task._popup_evidence(PurchaseAttempt(MOLAGORA_ITEM, 5, 5), BUY_CONFIRM_MULTI, True))
        self.assertIsNone(task._popup_evidence(PurchaseAttempt(ITEMS[0], 2, 1), BUY_CONFIRM_SINGLE, False))

    def test_final_confirmation_requires_selected_context_and_visible_button(self):
        task = KnightsShop.__new__(KnightsShop)
        task.config = SimpleNamespace(Emulator_GameLanguage='cn')
        task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        task.appear = Mock(return_value=False)
        with patch('tasks.knights.shop.Ocr', side_effect=AssertionError('final OCR is forbidden')):
            self.assertIsNone(task._final_popup_evidence(PurchaseAttempt(MOLAGORA_ITEM, 5, 5, confirmed=True)))
            task.appear.return_value = True
            self.assertIsNone(task._final_popup_evidence(PurchaseAttempt(MOLAGORA_ITEM, 5, 5)))
            self.assertIsNone(task._final_popup_evidence(PurchaseAttempt(MOLAGORA_ITEM, 5, 6, confirmed=True)))
            self.assertTrue(task._final_popup_evidence(PurchaseAttempt(MOLAGORA_ITEM, 5, 5, confirmed=True)))

    def test_period_target_counts_manual_purchases_and_resets_from_game_stock(self):
        for desired, limit, remaining, expected in ((3, 5, 4, (1, 2)), (3, 5, 2, (3, 0)), (3, 5, 5, (0, 3)), (1, 1, 0, (1, 0))):
            self.assertEqual(resolve_period_purchase_quantity(desired, limit, remaining), expected)

    def test_missing_invalid_or_disabled_options_never_enable_a_purchase(self):
        self.assertEqual(enabled_purchases(SimpleNamespace()), [])
        for value in (0, -1, True, 'bad', None):
            self.assertEqual(enabled_purchases(SimpleNamespace(**{BLOOM.option: value})), [])
        self.assertEqual(enabled_purchases(SimpleNamespace(**{BLOOM.option: 50})), [(BLOOM, 10)])

    def test_stock_and_amount_parsers_keep_zero_separate_from_failure(self):
        self.assertEqual(parse_stock('0/5'), (0, 5))
        self.assertEqual(parse_stock(' 10 ／ 10 '), (10, 10))
        for value in ('', 'O/5', '5', '6/5', '0/0', '1/50', '1/5abc'):
            self.assertIsNone(parse_stock(value))
        self.assertEqual(parse_amount('0'), 0)
        self.assertEqual(parse_amount('70,542'), 70542)
        for value in ('', '。', '10,50', '-1', '1.5', 'O', '余额20'):
            self.assertIsNone(parse_amount(value))

    def test_source_options_are_bound_disabled_and_cover_exact_period_limits(self):
        arguments = yaml.safe_load((ROOT / 'module/config/argument/argument.yaml').read_text(encoding='utf-8'))
        tasks = yaml.safe_load((ROOT / 'module/config/argument/task.yaml').read_text(encoding='utf-8'))
        groups = tasks['Daily']['tasks']['Knights']
        for item in ITEMS:
            group, option = item.option.split('_')
            self.assertIn(group, groups)
            self.assertEqual(arguments[group][option]['value'], 0)
            self.assertEqual(arguments[group][option]['option'], list(range(item.limit + 1)))
        self.assertEqual(len(ITEMS), 18)

    def test_all_ui_languages_have_human_readable_names_and_help(self):
        for path in (ROOT / 'module/config/i18n').glob('*.json'):
            data = json.loads(path.read_text(encoding='utf-8'))
            for item in ITEMS:
                group, option = item.option.split('_')
                self.assertTrue(data[group][option]['name'])
                self.assertTrue(data[group][option]['help'])
                self.assertNotIn(group + '.', data[group][option]['name'])


def shop_frame(remaining=10, *, view=1, left=True, right=False, present=True, price=600, balance=99999, active=True, unknown=False):
    return dict(kind='shop', remaining=remaining, view=view, left=left, right=right, present=present, price=price, balance=balance, active=active, unknown=unknown)


def popup_frame(current=1, total=10, *, valid=True, multi=True):
    return dict(kind='popup', evidence=(current, total) if valid else None, multi=multi)


def final_frame(*, valid=True):
    return dict(kind='final', evidence=True if valid else None)


class Replay:
    def __init__(self, frames, clock):
        self.frames = frames
        self.clock = clock
        self.index = 0
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.actions = []
        record_frame('knights-synthetic:0')

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Replay exhausted before the shop reached a verified result')
        self.clock.advance(1)
        record_frame(f'knights-synthetic:{self.index}')

    def click(self, button):
        action = (self.index, str(button), tuple(button.button))
        self.actions.append(action)
        record_action(action)

    def multi_click(self, button, n, interval):
        # Record one submitted batch. It deliberately does not advance the
        # counter or frames: only the next explicit input proves progress.
        action = (self.index, 'multi_click', (str(button), n, interval))
        self.actions.append(action)
        record_action(action)

    def swipe(self, *args, **kwargs):
        action = (self.index, 'swipe', args)
        self.actions.append(action)
        record_action(action)


def replay_task(frames, clock):
    task = KnightsShop.__new__(KnightsShop)
    device = Replay(frames, clock)
    task.device = device
    task.config = SimpleNamespace(Emulator_GameLanguage='cn')
    task._on_shop = lambda: device.frame['kind'] == 'shop'
    # A normal synthetic shop frame contains rendered non-target products
    # even when its enabled target is absent. Transition tests override this
    # independently; clicks never manufacture rendered content or stock.
    task._shop_content_ready = lambda: device.frame['kind'] == 'shop'
    task._popup = lambda: ((BUY_CONFIRM_MULTI if device.frame['multi'] else BUY_CONFIRM_SINGLE, device.frame['multi']) if device.frame['kind'] == 'popup' else None)
    task._popup_evidence = lambda *args: device.frame['evidence']
    task.appear = lambda asset, **kwargs: (
        asset in (SHOP_PURCHASE_FINAL_CHECK, SHOP_PURCHASE_FINAL_CONFIRM) and device.frame['kind'] == 'final'
        or asset in (BUY_CONFIRM_MULTI, BUY_CONFIRM_SINGLE, BUY_MIN, BUY_MAX, BUY_TIMES_PLUS, BUY_TIMES_MINUS)
        and device.frame['kind'] == 'popup'
    )
    task._final_popup_evidence = lambda *args: device.frame['evidence']
    task._title_strip = lambda: np.full((2, 2, 3), device.frame['view'] * 50, dtype=np.int16)
    task._balance = lambda item: device.frame['balance']
    task._network_visible = lambda: device.frame['kind'] == 'network'
    task._recover_shop = lambda: record_action('recover_shop')
    def cards(item):
        frame = device.frame
        if 'cards' in frame:
            if item.option not in frame['cards']:
                return []
            values = frame['cards'][item.option]
            if values is None:
                return None
            remaining, active, price = values
            return [ShopCard(ClickButton((385, 601, 544, 630), name=f'PRICE_{item.asset.name}'),
                             remaining, active, price)]
        if frame['unknown']:
            return None
        if not frame['present']:
            return []
        return [ShopCard(ClickButton((385, 601, 544, 630), name='PRICE'), frame['remaining'], frame['active'], frame['price'])]
    task._cards = cards
    def click_visible(asset, **kwargs):
        device.click(asset)
        return True
    task.appear_then_click = click_visible
    task.handle_network_error = lambda: False
    task.handle_touch_to_close = lambda **kwargs: False
    return task


def captured_replay_task(fixture_ids, clock):
    """Use real image matching on an explicitly simulated capture sequence."""
    task = replay_task([shop_frame() for _ in fixture_ids], clock)
    for method in ('_cards', '_on_shop', '_title_strip', '_shop_content_ready'):
        delattr(task, method)
    task.appear = lambda asset, **kwargs: asset.match_template(task.device.image)
    screenshot = task.device.screenshot

    def load_frame():
        screenshot()
        task.device.image = fixture_image(MANIFEST, fixture_ids[task.device.index])

    task.device.screenshot = load_frame
    task.device.image = fixture_image(MANIFEST, fixture_ids[0])
    return task


class ShopReplayTests(unittest.TestCase):
    def test_tab_transition_with_no_product_images_never_swipes(self):
        initial = shop_frame(present=False)
        initial['content_ready'] = False
        ready = shop_frame(0, active=False)
        with ControlledClock() as clock:
            task = replay_task([initial] * 5 + [ready] * 4, clock)
            task._shop_content_ready = lambda: task.device.frame.get('content_ready', True)
            self.assertTrue(task._execute_shop([(BLOOM, 10)]))
            self.assertEqual(task.device.actions, [])

    def test_unrendered_shop_times_out_without_swiping_or_recording_completion(self):
        with ControlledClock() as clock:
            task = replay_task([shop_frame(present=False)] * 110, clock)
            task._shop_content_ready = lambda: False
            with self.assertRaises(RequestHumanTakeover):
                task._execute_shop([(BLOOM, 10)])
            self.assertEqual(task.device.actions, [])
            self.assertFalse(hasattr(task.config, 'KnightsShopRuntime_Purchases'))

    def test_recovered_tab_waits_for_product_images_and_retains_payment(self):
        unrendered = shop_frame(remaining=9)
        unrendered['content_ready'] = False
        frames = ([shop_frame()] * 2 + [popup_frame()] * 4 + [dict(kind='network')]
                  + [unrendered] * 5 + [shop_frame(9)] * 4)
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            task._shop_content_ready = lambda: task.device.frame.get('content_ready', True)
            task.handle_network_error = Mock(return_value=True)
            task._recover_shop = Mock()
            self.assertTrue(task._execute_shop([(BLOOM, 1)]))
            self.assertEqual([a[1] for a in task.device.actions], ['PRICE', 'BUY_CONFIRM_MULTI'])
            task._recover_shop.assert_called_once_with()
            self.assertEqual(task.config.KnightsShopRuntime_Purchases[BLOOM.option]['purchased'], 1)

    def test_fresh_tab_does_not_swipe_during_initial_fast_frames(self):
        frames = [shop_frame(present=False)] * 18 + [shop_frame(0, active=False)] * 5
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            screenshot = task.device.screenshot
            def fast_frame():
                screenshot()
                clock.advance(-0.9)
            task.device.screenshot = fast_frame
            self.assertTrue(task._execute_shop([(BLOOM, 10)]))
            self.assertEqual(task.device.actions, [])

    def test_paid_catalyst_real_stock_completes_record_without_buying_again(self):
        before = ([shop_frame(10, price=180)] * 2 + [popup_frame(5, 10)] * 4
                  + [final_frame()] * 5)
        frames = before + [shop_frame(5, price=180)] * 4
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            task.device.image = fixture_image(MANIFEST, PAID_CATALYST_CAPTURE)
            synthetic_cards = task._cards
            # Selection and final dialog frames are explicitly simulated;
            # payment completion alone uses the user's captured stock pixels.
            task._cards = lambda item: (KnightsShop._cards(task, item) if task.device.index >= len(before)
                                        else synthetic_cards(item))
            self.assertTrue(task._execute_shop([(CATALYST, 5)]))
            self.assertEqual([a[1] for a in task.device.actions],
                             ['PRICE', 'BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM'])
            self.assertEqual(task.config.KnightsShopRuntime_Purchases[CATALYST.option]['purchased'], 5)

    def test_logged_quantity_increments_use_bounded_batches_then_fresh_confirmation(self):
        frames = ([shop_frame()] * 2 + [popup_frame(1)] * 2 + [popup_frame(4)] * 5
                  + [popup_frame(5)] * 6 + [final_frame()] * 5 + [shop_frame(5)] * 4)
        with ControlledClock() as clock, patch('tasks.store.purchase.StorePurchaseCounterOcr.model') as model:
            task = replay_task(frames, clock)
            del task._popup_evidence
            reads = []
            def counter(image):
                reads.append(task.device.index)
                current, total = task.device.frame['evidence']
                return f'¥{current}/{total}', 0.99
            model.ocr_single_line.side_effect = counter
            self.assertTrue(task._execute_shop([(BLOOM, 5)]))
            batches = [action for action in task.device.actions if action[1] == 'multi_click']
            self.assertEqual([action[2] for action in batches],
                             [('BUY_TIMES_PLUS', 3, (0.2, 0.3)), ('BUY_TIMES_PLUS', 1, (0.2, 0.3))])
            self.assertEqual([action[1] for action in task.device.actions],
                             ['PRICE', 'multi_click', 'multi_click', 'BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM'])
            for index, _, _ in batches:
                self.assertNotIn(index + 1, reads)
                self.assertNotIn(index + 2, reads)
            self.assertGreater(task.device.actions[-2][0], batches[-1][0] + 2)

    def test_partial_quantity_batch_recalculates_only_remaining_difference(self):
        frames = ([shop_frame()] * 2 + [popup_frame(1)] * 2 + [popup_frame(3)] * 5
                  + [popup_frame(5)] * 6 + [shop_frame(5)] * 4)
        actions = self.run_frames(frames, target=5)
        self.assertEqual([a[2][1] for a in actions if a[1] == 'multi_click'], [3, 2])
        self.assertEqual([a[1] for a in actions].count('BUY_CONFIRM_MULTI'), 1)

    def test_quantity_decrements_are_batched_and_rechecked(self):
        frames = ([shop_frame()] * 2 + [popup_frame(9)] * 2 + [popup_frame(6)] * 5
                  + [popup_frame(5)] * 6 + [shop_frame(5)] * 4)
        actions = self.run_frames(frames, target=5)
        self.assertEqual([a[2] for a in actions if a[1] == 'multi_click'],
                         [('BUY_TIMES_MINUS', 3, (0.2, 0.3)), ('BUY_TIMES_MINUS', 1, (0.2, 0.3))])

    def test_one_inconsistent_quantity_frame_cannot_start_a_batch(self):
        frames = ([shop_frame()] * 2 + [popup_frame(9)] + [popup_frame(1)] * 2
                  + [popup_frame(4)] * 6 + [shop_frame(6)] * 4)
        actions = self.run_frames(frames, target=4)
        self.assertEqual([a[2] for a in actions if a[1] == 'multi_click'], [('BUY_TIMES_PLUS', 3, (0.2, 0.3))])

    def test_quantity_batch_wait_requires_elapsed_time_even_on_fast_screenshots(self):
        frames = [shop_frame()] * 2 + [popup_frame(1)] * 110
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            reads, batch_times = [], []
            evidence = task._popup_evidence
            def counter(*args):
                reads.append(clock.now)
                return evidence(*args)
            task._popup_evidence = counter
            multi_click = task.device.multi_click
            def batch(*args, **kwargs):
                batch_times.append(clock.now)
                multi_click(*args, **kwargs)
            task.device.multi_click = batch
            screenshot = task.device.screenshot
            def fast_frame():
                screenshot()
                clock.advance(-0.9)
            task.device.screenshot = fast_frame
            # A short replay ending during an unresolved selection is not
            # completion. Check the timing and forbidden payment actions.
            with self.assertRaisesRegex(AssertionError, 'Replay exhausted'):
                task._execute_shop([(BLOOM, 4)])
            self.assertTrue(batch_times)
            for started in batch_times:
                self.assertTrue(all(read <= started or read - started > 0.6 for read in reads))
            self.assertFalse(any(a[1] in ('BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM') for a in task.device.actions))

    def test_quantity_batch_evidence_is_reset_by_an_unknown_overlay(self):
        frames = ([shop_frame()] * 2 + [popup_frame(1)] + [dict(kind='overlay')]
                  + [popup_frame(1)] + [popup_frame(valid=False)] * 110)
        actions = self.run_frames(frames, target=4, failure=True)
        self.assertEqual([action[1] for action in actions], ['PRICE'])

    def test_missing_quantity_button_or_bad_counter_cannot_submit_a_batch(self):
        cases = ([popup_frame(1)] * 110, [popup_frame(1, 9)] * 110, [popup_frame(valid=False)] * 110)
        for popups in cases:
            with ControlledClock() as clock:
                task = replay_task([shop_frame()] * 2 + popups, clock)
                # Keep the real counter/stock validation for the wrong total.
                if popups[0]['evidence'] == (1, 9):
                    task._popup_evidence = lambda attempt, *args: KnightsShop._popup_evidence(task, attempt, *args)
                    with patch('tasks.knights.shop.ocr_purchase_counter', return_value=(1, 8, 9)):
                        with self.assertRaises(RequestHumanTakeover):
                            task._execute_shop([(BLOOM, 5)])
                else:
                    visible = task.appear
                    task.appear = lambda asset, **kw: asset != BUY_TIMES_PLUS and visible(asset, **kw)
                    with self.assertRaises(RequestHumanTakeover):
                        task._execute_shop([(BLOOM, 5)])
                self.assertEqual([a[1] for a in task.device.actions], ['PRICE'])

    def test_unresponsive_quantity_batches_timeout_without_payment_or_completion(self):
        frames = [shop_frame()] * 2 + [popup_frame(1)] * 110
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            with self.assertRaises(RequestHumanTakeover):
                task._execute_shop([(BLOOM, 5)])
            batches = [a for a in task.device.actions if a[1] == 'multi_click']
            self.assertTrue(batches)
            self.assertTrue(all(a[2][1] == 3 for a in batches))
            self.assertLess(len(batches), 30)
            self.assertFalse(any(a[1] in ('BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM') for a in task.device.actions))
            self.assertEqual(getattr(task.config, 'KnightsShopRuntime_Purchases', {}), {})

    def test_network_after_quantity_batch_rechecks_counter_before_payment(self):
        frames = ([shop_frame()] * 2 + [popup_frame(1)] * 2 + [dict(kind='network')]
                  + [popup_frame(4)] * 6 + [shop_frame(6)] * 4)
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            task.handle_network_error = Mock(return_value=True)
            task._recover_shop = Mock()
            self.assertTrue(task._execute_shop([(BLOOM, 4)]))
            task._recover_shop.assert_called_once()
            self.assertEqual([a[1] for a in task.device.actions], ['PRICE', 'multi_click', 'BUY_CONFIRM_MULTI'])
            self.assertGreater(task.device.actions[-1][0], 5)

    def test_manual_and_sold_out_stock_are_recorded_for_next_run(self):
        now = datetime(2026, 10, 3, 12)
        for remaining, target in ((7, 3), (0, 10)):
            with ControlledClock() as clock, patch('tasks.knights.shop.datetime', wraps=datetime) as dates, \
                    patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
                dates.now.return_value = now
                task = replay_task([shop_frame(remaining)] * 4, clock)
                setattr(task.config, BLOOM.option, target)
                self.assertTrue(task._execute_shop([(BLOOM, target)]))
                self.assertEqual(task.config.KnightsShopRuntime_Purchases[BLOOM.option]['purchased'], 10 - remaining)
                self.assertEqual(task._pending_shop_purchases(now), [])
                self.assertEqual(task.device.actions, [])

    def test_records_require_verified_stock_and_partial_purchase_stays_pending(self):
        now = datetime(2026, 10, 3, 12)
        frames = [shop_frame(balance=600)] * 2 + [popup_frame()] * 4 + [shop_frame(9)] * 4
        with ControlledClock() as clock, patch('tasks.knights.shop.datetime', wraps=datetime) as dates, \
                patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
            dates.now.return_value = now
            task = replay_task(frames, clock)
            setattr(task.config, BLOOM.option, 10)
            snapshots = []
            screenshot = task.device.screenshot
            def observe():
                snapshots.append((task.device.index, getattr(task.config, 'KnightsShopRuntime_Purchases', {})))
                screenshot()
            task.device.screenshot = observe
            self.assertTrue(task._execute_shop([(BLOOM, 10)]))
            self.assertTrue(all(not records for index, records in snapshots if index <= 7))
            self.assertEqual(task.config.KnightsShopRuntime_Purchases[BLOOM.option]['purchased'], 1)
            self.assertEqual(task._pending_shop_purchases(now), [(BLOOM, 10)])

    def test_insufficient_currency_or_unverified_payment_cannot_complete_record(self):
        now = datetime(2026, 10, 3, 12)
        for frames, failure in (
            ([shop_frame(balance=599)] * 4, False),
            ([shop_frame()] * 2 + [popup_frame()] * 4 + [shop_frame()] * 110, True),
            ([shop_frame()] * 2 + [popup_frame(valid=False)] * 110, True),
        ):
            with ControlledClock() as clock:
                task = replay_task(frames, clock)
                setattr(task.config, BLOOM.option, 1)
                if failure:
                    with self.assertRaises(RequestHumanTakeover):
                        task._execute_shop([(BLOOM, 1)])
                else:
                    self.assertTrue(task._execute_shop([(BLOOM, 1)]))
                self.assertEqual(getattr(task.config, 'KnightsShopRuntime_Purchases', {}), {})
                self.assertEqual(task._pending_shop_purchases(now), [(BLOOM, 1)])

    def test_completed_item_record_survives_later_item_failure(self):
        frame = shop_frame()
        frame['cards'] = {ITEMS[0].option: (0, False, None), BLOOM.option: None}
        with ControlledClock() as clock:
            task = replay_task([frame] * 110, clock)
            with self.assertRaises(RequestHumanTakeover):
                task._execute_shop([(ITEMS[0], 1), (BLOOM, 1)])
            self.assertEqual(task.config.KnightsShopRuntime_Purchases[ITEMS[0].option]['purchased'], 1)
            self.assertNotIn(BLOOM.option, task.config.KnightsShopRuntime_Purchases)

    def test_run_spanning_refresh_does_not_mark_new_period_complete(self):
        before = datetime(2026, 10, 1, 1, 59, 59)
        after = datetime(2026, 10, 1, 2, 0, 5)
        with ControlledClock() as clock, patch('tasks.knights.shop.datetime', wraps=datetime) as dates, \
                patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
            dates.now.side_effect = [before, after]
            task = replay_task([shop_frame(7)] * 4, clock)
            task.config.Emulator_PackageName = 'OVERSEA-Play'
            task.config.Scheduler_ServerUpdate = '02:00'
            setattr(task.config, BLOOM.option, 3)
            self.assertTrue(task._execute_shop([(BLOOM, 3)]))
            self.assertEqual(task._pending_shop_purchases(before), [])
            self.assertEqual(task._pending_shop_purchases(), [(BLOOM, 3)])

    def test_completed_records_skip_shop_before_navigation(self):
        now = datetime(2026, 10, 3, 12)
        task = history_task(**{ITEMS[0].option: 1, BLOOM.option: 3})
        task.ui_goto = Mock(side_effect=AssertionError('completed shop entered'))
        task._execute_shop = Mock(side_effect=AssertionError('completed shop scanned'))
        with patch('tasks.knights.shop.datetime', wraps=datetime) as dates, \
                patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
            dates.now.return_value = now
            task._record_shop_purchase(ITEMS[0], 1, now)
            task._record_shop_purchase(BLOOM, 3, now)
            self.assertTrue(task.run_shop())
        task.ui_goto.assert_not_called()
        task._execute_shop.assert_not_called()

    def test_completed_shop_only_skips_guild_entry_but_other_subtasks_continue(self):
        from tasks.knights.knights import Knights
        now = datetime(2026, 10, 3, 12)
        for weekly in (False, True):
            task = Knights.__new__(Knights)
            task.config = SimpleNamespace(
                Emulator_PackageName='OVERSEA-Play', Scheduler_ServerUpdate='02:00',
                Knights_ClaimSigninRateReward=False, Knights_WeeklyTask=weekly, Knights_Support=False,
                KnightsTeamBattle_TeamBattle=False, Knights_WorldBoss=False, task_delay=Mock(),
                **{BLOOM.option: 3},
            )
            task.device = SimpleNamespace(app_is_running=lambda: True)
            for name in ('_reset_team_battle_status_runtime', '_settle_knights_home', 'ui_goto'):
                setattr(task, name, Mock())
            task._enter_knights = Mock(return_value=True)
            task.run_shop = Mock(side_effect=AssertionError('completed shop scheduled'))
            task.run_weekly_task = Mock(return_value=True)
            task._get_team_battle_next_delay_target = Mock(return_value=None)
            with patch('tasks.knights.shop.datetime', wraps=datetime) as dates, \
                    patch('tasks.knights.shop.server_time_offset', return_value=timedelta()):
                dates.now.return_value = now
                task._record_shop_purchase(BLOOM, 3, now)
                self.assertTrue(task.run())
            self.assertEqual(task._enter_knights.call_count, int(weekly))
            self.assertEqual(task.run_weekly_task.call_count, int(weekly))
            task.config.task_delay.assert_called_once_with(server_update=True)

    def run_frames(self, frames, target=1, item=BLOOM, failure=False):
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            if failure:
                with self.assertRaises(RequestHumanTakeover):
                    task._execute_shop([(item, target)])
            else:
                self.assertTrue(task._execute_shop([(item, target)]))
            return task.device.actions

    def test_repeated_popup_and_delayed_stock_never_repeat_payment(self):
        frames = [shop_frame()] * 2 + [popup_frame()] * 6 + [shop_frame()] * 4 + [shop_frame(9)] * 4
        actions = self.run_frames(frames)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_MULTI'])
        self.assertEqual(actions[0][2], (385, 601, 544, 630))

    def test_multiple_purchase_handles_second_confirmation_before_stock_proof(self):
        frames = ([shop_frame(5, price=250)] * 2 + [popup_frame(1, 5)] * 3
                  + [popup_frame(5, 5)] * 3 + [final_frame()] * 6
                  + [shop_frame(5, price=250)] * 3 + [shop_frame(0, active=False)] * 4)
        actions = self.run_frames(frames, target=5, item=MOLAGORA_ITEM)
        self.assertEqual([a[1] for a in actions],
                         ['PRICE', 'BUY_MAX', 'BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM'])
        self.assertEqual(actions[-1][2], (787, 511, 881, 552))

    def test_final_confirmation_replay_uses_captured_pixels_and_real_validation(self):
        frames = ([shop_frame(5, price=250)] * 2 + [popup_frame(1, 5)] * 3
                  + [popup_frame(5, 5)] * 3 + [final_frame()] * 6 + [shop_frame(0, active=False)] * 4)
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            # Selection and resulting stock are explicitly simulated. Only
            # the final stage uses this one real capture, repeated to model
            # fresh observations; it is not a live adjacent-frame recording.
            task.device.image = fixture_image(MANIFEST, FINAL_CAPTURE)
            task.appear = lambda asset, **kw: (
                task.device.frame['kind'] == 'final' and asset.match_template(task.device.image)
                or task.device.frame['kind'] == 'popup' and asset in (BUY_MIN, BUY_MAX, BUY_TIMES_PLUS, BUY_TIMES_MINUS)
            )
            del task._final_popup_evidence
            self.assertTrue(task._execute_shop([(MOLAGORA_ITEM, 5)]))
            self.assertEqual([a[1] for a in task.device.actions],
                             ['PRICE', 'BUY_MAX', 'BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM'])

    def test_repeated_final_dialog_times_out_without_repeating_either_confirmation(self):
        frames = [shop_frame(5, price=250)] * 2 + [popup_frame(5, 5)] * 4 + [final_frame()] * 110
        actions = self.run_frames(frames, target=5, item=MOLAGORA_ITEM, failure=True)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM'])

    def test_second_confirmation_still_requires_stock_reduction(self):
        frames = ([shop_frame(5, price=250)] * 2 + [popup_frame(5, 5)] * 4 + [final_frame()] * 5
                  + [shop_frame(5, price=250)] * 110)
        actions = self.run_frames(frames, target=5, item=MOLAGORA_ITEM, failure=True)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM'])

    def test_unreadable_final_evidence_never_pays_or_uses_generic_handlers(self):
        frames = [shop_frame(5, price=250)] * 2 + [popup_frame(5, 5)] * 4 + [final_frame(valid=False)] * 110
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            task.handle_touch_to_close = Mock(side_effect=AssertionError('generic handler consumed final dialog'))
            with self.assertRaises(RequestHumanTakeover):
                task._execute_shop([(MOLAGORA_ITEM, 5)])
            self.assertEqual([a[1] for a in task.device.actions], ['PRICE', 'BUY_CONFIRM_MULTI'])
            task.handle_touch_to_close.assert_not_called()

    def test_one_valid_final_frame_is_insufficient_to_pay(self):
        frames = ([shop_frame(5, price=250)] * 2 + [popup_frame(5, 5)] * 4
                  + [final_frame(valid=False)] * 4 + [final_frame()] + [final_frame(valid=False)] * 110)
        actions = self.run_frames(frames, target=5, item=MOLAGORA_ITEM, failure=True)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_MULTI'])

    def test_final_evidence_is_reset_by_intervening_selection_frames(self):
        frames = ([shop_frame(5, price=250)] * 2 + [popup_frame(5, 5)] * 4
                  + [final_frame()] + [popup_frame(5, 5)] * 3 + [final_frame()]
                  + [final_frame(valid=False)] * 110)
        actions = self.run_frames(frames, target=5, item=MOLAGORA_ITEM, failure=True)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_MULTI'])

    def test_stale_final_confirmation_is_canceled_before_scanning(self):
        frames = [final_frame()] + [shop_frame(0, active=False)] * 4
        self.assertEqual([a[1] for a in self.run_frames(frames, target=5, item=MOLAGORA_ITEM)],
                         ['SHOP_PURCHASE_FINAL_CANCEL'])

    def test_unexpected_final_dialog_cannot_bypass_selection_confirmation(self):
        frames = [shop_frame(5, price=250)] * 2 + [final_frame()] * 110
        actions = self.run_frames(frames, target=5, item=MOLAGORA_ITEM, failure=True)
        self.assertEqual([a[1] for a in actions], ['PRICE'])

    def test_network_recovery_retains_both_confirmation_latches(self):
        frames = ([shop_frame(5, price=250)] * 2 + [popup_frame(5, 5)] * 4 + [final_frame()] * 5
                  + [dict(kind='network')] + [final_frame()] * 4 + [shop_frame(0, active=False)] * 4)
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            task.handle_network_error = Mock(return_value=True)
            task._recover_shop = Mock()
            self.assertTrue(task._execute_shop([(MOLAGORA_ITEM, 5)]))
            task._recover_shop.assert_called_once_with()
            self.assertEqual([a[1] for a in task.device.actions],
                             ['PRICE', 'BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM'])

    def test_max_is_confirmed_by_fresh_quantity_before_payment(self):
        frames = [shop_frame()] * 2 + [popup_frame()] * 3 + [popup_frame(10)] * 3 + [shop_frame(0, active=False)] * 4
        actions = self.run_frames(frames, target=10)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_MAX', 'BUY_CONFIRM_MULTI'])
        self.assertGreater(actions[-1][0], 4)

    def test_price_click_can_fail_then_retry_without_counting_a_purchase(self):
        frames = [shop_frame()] * 5 + [popup_frame()] * 4 + [shop_frame(9)] * 4
        actions = self.run_frames(frames)
        self.assertGreaterEqual([a[1] for a in actions].count('PRICE'), 2)
        self.assertEqual([a[1] for a in actions].count('BUY_CONFIRM_MULTI'), 1)

    def test_unacknowledged_payment_fails_instead_of_rebuying(self):
        frames = [shop_frame()] * 2 + [popup_frame()] * 110
        actions = self.run_frames(frames, failure=True)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_MULTI'])

    def test_unchanged_shop_after_payment_is_not_success(self):
        frames = [shop_frame()] * 2 + [popup_frame()] * 4 + [shop_frame()] * 110
        actions = self.run_frames(frames, failure=True)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_MULTI'])

    def test_unreadable_selection_counter_cannot_pay(self):
        frames = [shop_frame()] * 2 + [popup_frame(valid=False)] * 110
        actions = self.run_frames(frames, failure=True)
        self.assertEqual([a[1] for a in actions], ['PRICE'])

    def test_already_reached_sold_out_and_insufficient_balance_do_not_click(self):
        for frame, target in ((shop_frame(7), 3), (shop_frame(0, active=False), 10), (shop_frame(balance=599), 1)):
            self.assertEqual(self.run_frames([frame] * 4, target=target), [])

    def test_affordable_partial_purchase_is_capped_before_dialog(self):
        frames = [shop_frame(balance=600)] * 2 + [popup_frame()] * 4 + [shop_frame(9)] * 4
        actions = self.run_frames(frames, target=10)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_MULTI'])

    def test_stale_dialog_is_canceled_before_scanning(self):
        frames = [popup_frame()] + [shop_frame(0, active=False)] * 4
        actions = self.run_frames(frames, target=10)
        self.assertEqual([a[1] for a in actions], ['SHOP_PURCHASE_CANCEL'])

    def test_single_quantity_layout_requires_stock_decrease(self):
        item = ITEMS[0]
        frames = [shop_frame(1, price=200)] * 2 + [popup_frame(1, 1, multi=False)] * 4 + [shop_frame(0, active=False)] * 4
        actions = self.run_frames(frames, item=item)
        self.assertEqual([a[1] for a in actions], ['PRICE', 'BUY_CONFIRM_SINGLE'])

    def test_single_purchase_uses_image_confirmation_and_pays_once_without_ocr(self):
        frames = [shop_frame(1, price=200)] * 2 + [popup_frame(1, 1, multi=False)] * 6 + [shop_frame(0, active=False)] * 4
        with ControlledClock() as clock, patch('tasks.knights.shop.Ocr', side_effect=AssertionError('popup OCR is forbidden')):
            task = replay_task(frames, clock)
            # Item/card and popup visibility are explicitly simulated; the
            # production confirmation path must not read any dialog text.
            del task._popup_evidence
            task._text = Mock(side_effect=AssertionError('popup text OCR is forbidden'))
            self.assertTrue(task._execute_shop([(ITEMS[0], 1)]))
            self.assertEqual([a[1] for a in task.device.actions], ['PRICE', 'BUY_CONFIRM_SINGLE'])

    def test_logged_counter_noise_adjusts_to_max_and_confirms_both_stages(self):
        frames = ([shop_frame(5, price=250)] * 2 + [popup_frame(1, 5)] * 3
                  + [popup_frame(5, 5)] * 3 + [final_frame()] * 5 + [shop_frame(0, active=False)] * 4)
        with ControlledClock() as clock, patch('tasks.store.purchase.StorePurchaseCounterOcr.model') as model:
            task = replay_task(frames, clock)
            del task._popup_evidence
            del task._final_popup_evidence
            model.ocr_single_line.side_effect = lambda image: (f"¥{task.device.frame['evidence'][0]}/5双精灵", 0.99)
            task._text = Mock(side_effect=AssertionError('popup text OCR is forbidden'))
            with patch('tasks.knights.shop.Ocr', side_effect=AssertionError('popup text OCR is forbidden')):
                self.assertTrue(task._execute_shop([(MOLAGORA_ITEM, 5)]))
            self.assertEqual([a[1] for a in task.device.actions],
                             ['PRICE', 'BUY_MAX', 'BUY_CONFIRM_MULTI', 'SHOP_PURCHASE_FINAL_CONFIRM'])

    def test_old_scroll_frames_are_not_scanned_and_click_uses_settled_view(self):
        initial = shop_frame(present=False)
        shifted = shop_frame(view=2, left=False)
        frames = [initial] * 4 + [shifted] * 3 + [popup_frame()] * 4 + [shop_frame(9, view=2, left=False)] * 4
        actions = self.run_frames(frames)
        self.assertEqual(actions[0][1], 'swipe')
        self.assertGreaterEqual(next(a[0] for a in actions if a[1] == 'PRICE'), 5)

    def test_unreadable_stock_never_scrolls_past_or_buys(self):
        self.assertEqual(self.run_frames([shop_frame(unknown=True)] * 110, failure=True), [])

    def test_network_overlay_blocks_payment_even_while_handler_is_on_cooldown(self):
        frames = [shop_frame()] * 2 + [popup_frame()] * 4 + [dict(kind='network')] * 3 + [shop_frame(9)] * 4
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            # A retry on cooldown returns False although the overlay still
            # blocks the page. Then login completes and the shop is revisited.
            task.handle_network_error = Mock(side_effect=[False, False, True])
            task._recover_shop = Mock()
            self.assertTrue(task._execute_shop([(BLOOM, 1)]))
            task._recover_shop.assert_called_once_with()
            self.assertEqual([a[1] for a in task.device.actions], ['PRICE', 'BUY_CONFIRM_MULTI'])

    def test_missing_enabled_item_at_known_end_is_an_error(self):
        actions = self.run_frames([shop_frame(present=False, right=True)] * 24, failure=True)
        self.assertEqual([a[1] for a in actions], ['swipe'] * 2)
        self.assertEqual([a[2] for a in actions], [((1060, 392), (788, 392))] * 2)

    def test_login_reset_relocates_pending_purchase_without_clearing_payment(self):
        frames = ([shop_frame()] * 2 + [popup_frame()] * 4 + [dict(kind='network')]
                  + [shop_frame(view=2, present=False)] * 4
                  + [shop_frame(9, view=3, left=False)] * 4)
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            task.handle_network_error = Mock(return_value=True)
            task._recover_shop = Mock()
            self.assertTrue(task._execute_shop([(BLOOM, 1)]))
            self.assertEqual([a[1] for a in task.device.actions], ['PRICE', 'BUY_CONFIRM_MULTI', 'swipe'])
            self.assertEqual(task.device.actions[-1][2], ((1060, 392), (788, 392)))

    def test_fresh_entry_scans_forward_without_probing_left_edge(self):
        # Tab entry starts at the head unless a previous large fling is
        # still moving. The controlled short-step loop must not compensate
        # for that unsupported situation by always probing backwards first.
        frames = [shop_frame(view=1, present=False)] * 4 + [shop_frame(0, view=2, active=False)] * 4
        actions = self.run_frames(frames)
        self.assertEqual([action[1] for action in actions], ['swipe'])
        self.assertEqual(actions[0][2], ((1060, 392), (788, 392)))

    def test_scroll_that_never_moves_has_a_bounded_retry(self):
        actions = self.run_frames([shop_frame(present=False)] * 110, failure=True)
        self.assertEqual(len(actions), 2)
        self.assertTrue(all(a[1] == 'swipe' for a in actions))
        self.assertTrue(all(a[2] == ((1060, 392), (788, 392)) for a in actions))

    def test_one_stationary_swipe_does_not_end_a_search(self):
        # Entry no longer borrows the fast-first-click interval. Keep the
        # old viewport through both separately issued, settled swipe probes;
        # only their later explicit target frame can complete the search.
        frames = [shop_frame(present=False)] * 8 + [shop_frame(0, view=2, active=False)] * 4
        actions = self.run_frames(frames, target=10)
        self.assertEqual([a[1] for a in actions], ['swipe', 'swipe'])

    def test_unstable_titles_time_out_without_buying_or_scrolling(self):
        frames = [shop_frame(view=1 + index % 2) for index in range(110)]
        self.assertEqual(self.run_frames(frames, failure=True), [])

    def test_small_step_search_can_pass_twelve_views_without_skipping_a_target(self):
        frames = []
        for view in range(1, 18):
            frames += [shop_frame(view=view, present=False)] * 5
        frames += [shop_frame(0, view=18, active=False)] * 4
        actions = self.run_frames(frames, target=10)
        self.assertGreater(len(actions), 12)
        self.assertLessEqual(len(actions), 32)
        self.assertTrue(all(a[1] == 'swipe' for a in actions))

    def test_moving_view_has_a_bounded_scan_and_never_completes_missing_items(self):
        frames = []
        for view in range(1, 40):
            frames += [shop_frame(view=view, present=False)] * 5
        actions = self.run_frames(frames, failure=True)
        self.assertEqual(len(actions), 32)
        self.assertTrue(all(a[1] == 'swipe' for a in actions))

    def test_sold_out_first_item_can_move_to_tail_without_marking_left_boundary(self):
        reset = next(item for item in ITEMS if item.option.endswith('_EquipmentResetStone'))
        initial = shop_frame(view=2)
        initial['cards'] = {}
        tail = shop_frame(view=3)
        tail['cards'] = {reset.option: (0, False, None)}
        with ControlledClock() as clock:
            task = replay_task([initial] * 4 + [tail] * 4, clock)
            self.assertTrue(task._execute_shop([(reset, 1)]))
            self.assertEqual([a[1] for a in task.device.actions], ['swipe'])
            self.assertEqual(task.device.actions[-1][2], ((1060, 392), (788, 392)))

    def test_purchase_reordering_relocates_stock_then_searches_shifted_items(self):
        mystic = ITEMS[0]
        initial = shop_frame(view=2)
        initial['cards'] = {mystic.option: (1, True, 200)}
        shifted = shop_frame(view=3)
        shifted['cards'] = {BLOOM.option: (10, True, 600)}
        tail = shop_frame(view=4)
        tail['cards'] = {mystic.option: (0, False, None)}
        preceding = shop_frame(view=2)
        preceding['cards'] = {BLOOM.option: (10, True, 600)}
        bought = shop_frame(view=2)
        bought['cards'] = {BLOOM.option: (9, True, 600)}
        frames = ([initial] * 2 + [popup_frame(1, 1, multi=False)] * 4 + [shifted] * 4
                  + [tail] * 14 + [preceding] * 3 + [popup_frame()] * 4 + [bought] * 4)
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            self.assertTrue(task._execute_shop([(mystic, 1), (BLOOM, 1)]))
            self.assertEqual([a[1] for a in task.device.actions],
                             ['PRICE_MYSTIC_MEDALS', 'BUY_CONFIRM_SINGLE', 'swipe', 'swipe', 'swipe', 'swipe',
                              'PRICE_EPIC_SPIRIT_BLOOM', 'BUY_CONFIRM_MULTI'])
            self.assertEqual([a[2] for a in task.device.actions if a[1] == 'swipe'],
                             [((1060, 392), (788, 392))] * 3 + [((788, 392), (1060, 392))])

    def test_mystic_capture_and_real_balance_start_one_image_selected_purchase(self):
        frames = ([shop_frame(1, price=200)] * 2 + [popup_frame(1, 1, multi=False)] * 4
                  + [shop_frame(0, active=False)] * 4)
        with ControlledClock() as clock:
            task = replay_task(frames, clock)
            task.device.image = fixture_image(MANIFEST, REORDERED_CAPTURES[2])
            synthetic_cards = task._cards
            # The initial card, its translated price button and the shifted
            # balance header come from real pixels. The single popup layout
            # and following zero stock are explicit simulated inputs only.
            task._cards = lambda item: (KnightsShop._cards(task, item) if task.device.index < 2
                                        else synthetic_cards(item))
            del task._balance
            self.assertTrue(task._execute_shop([(ITEMS[0], 1)]))
            self.assertEqual([a[1] for a in task.device.actions],
                             ['MYSTIC_MEDALS_result0', 'BUY_CONFIRM_SINGLE'])
            self.assertEqual(task.device.actions[0][2], (718, 344, 877, 373))

    def test_unsupported_server_never_navigates_or_builds_a_device(self):
        task = KnightsShop.__new__(KnightsShop)
        task.config = SimpleNamespace(Emulator_PackageName='com.zlongame.cn.epicseven', **{BLOOM.option: 1})
        task.ui_goto = Mock(side_effect=AssertionError('unsupported navigation'))
        self.assertTrue(task.run())
        task.ui_goto.assert_not_called()

    def test_shop_only_knights_run_still_executes_and_delays_daily(self):
        from tasks.knights.knights import Knights
        task = Knights.__new__(Knights)
        options = dict(Knights_ClaimSigninRateReward=False, Knights_WeeklyTask=False, Knights_Support=False,
                       KnightsTeamBattle_TeamBattle=False, Knights_WorldBoss=False, **{BLOOM.option: 1})
        task.config = SimpleNamespace(**options, task_delay=Mock())
        task.device = SimpleNamespace(app_is_running=lambda: True)
        task._reset_team_battle_status_runtime = Mock()
        task._enter_knights = Mock(return_value=True)
        task._settle_knights_home = Mock()
        task._get_team_battle_next_delay_target = Mock(return_value=None)
        task.run_shop = Mock(return_value=True)
        task.ui_goto = Mock()
        self.assertTrue(task.run())
        task.run_shop.assert_called_once_with(skip_first_screenshot=True)
        self.assertEqual([call.args[0].name for call in task.ui_goto.call_args_list], ['page_knights'])
        task.config.task_delay.assert_called_once_with(server_update=True)

    def test_activity_subtasks_share_one_knights_instance_and_close_once(self):
        from itertools import product
        from tasks.knights.knights import Knights
        for support, shop, weekly in product((False, True), repeat=3):
            task = Knights.__new__(Knights)
            task.config = SimpleNamespace(
                Knights_ClaimSigninRateReward=False, Knights_WeeklyTask=weekly, Knights_Support=support,
                Knights_SupportLowerLevelFairyFlower=support, Knights_SupportBeginnerPenguin=False,
                KnightsTeamBattle_TeamBattle=False, Knights_WorldBoss=False, task_delay=Mock(),
                **{BLOOM.option: int(shop)},
            )
            task.device = SimpleNamespace(app_is_running=lambda: True)
            task._reset_team_battle_status_runtime = Mock()
            task._enter_knights = Mock(return_value=True)
            task._settle_knights_home = Mock()
            task._get_team_battle_next_delay_target = Mock(return_value=None)
            order = []
            for method, label in (('run_support', 'support'), ('run_shop', 'shop'), ('run_weekly_task', 'weekly')):
                setattr(task, method, Mock(side_effect=lambda label=label, **kw: order.append(label) or True))
            task.ui_goto = Mock(side_effect=lambda destination, **kw: order.append(destination.name))
            with patch('tasks.knights.shop.KnightsShop', side_effect=AssertionError('new task instance')):
                self.assertTrue(task.run())
            expected = [label for enabled, label in ((support, 'support'), (shop, 'shop'), (weekly, 'weekly')) if enabled]
            if expected:
                expected.append('page_knights')
            self.assertEqual(order, expected)

    def test_all_activity_entry_methods_delegate_to_global_navigation(self):
        from tasks.knights.knights import Knights
        task = Knights.__new__(Knights)
        task.ui_goto = Mock()
        task._enter_support(skip_first_screenshot=False)
        task._enter_weekly_task(skip_first_screenshot=False)
        self.assertEqual([call.args[0].name for call in task.ui_goto.call_args_list],
                         ['page_knights_support', 'page_knights_weekly_task'])
        self.assertTrue(all(call.kwargs == {'skip_first_screenshot': False} for call in task.ui_goto.call_args_list))

    def test_shop_entry_leaves_tab_open_for_following_subtasks(self):
        task = KnightsShop.__new__(KnightsShop)
        task.config = SimpleNamespace(Emulator_PackageName='com.stove.epic7.google', **{BLOOM.option: 1})
        task.ui_goto = Mock()
        task._execute_shop = Mock(return_value=True)
        # The conditionally registered page is supplied as an explicit mock;
        # actual three-client imports and route execution are checked below.
        with patch('tasks.base.page.page_knights_shop', SimpleNamespace(name='shop'), create=True), \
                patch.object(server, 'lang', 'global_cn'):
            self.assertTrue(task.run_shop(skip_first_screenshot=False))
        self.assertEqual(len(task.ui_goto.call_args_list), 1)
        self.assertEqual(task.ui_goto.call_args.args[0].name, 'shop')
        self.assertEqual(task.ui_goto.call_args.kwargs, {'skip_first_screenshot': False})


class ShopRouteTests(unittest.TestCase):
    def test_three_server_imports_and_shop_routes_are_isolated(self):
        script = '''
import sys
from module.config import server
server.server, lang = sys.argv[1:]
server.set_lang(lang)
from tasks.knights.knights import Knights
from tasks.base import page
supported = lang == 'global_cn'
assert hasattr(page, 'page_knights_shop') == supported
if supported:
    shop = page.page_knights_shop
    assert shop in page.page_knights_support.links
    assert shop in page.page_knights_weekly_task.links
    assert page.page_knights in shop.links
    assert shop.check_button.name == 'SHOP_CHECK'
    page.Page.init_connection(shop)
    assert page.page_main.parent is not None
    page.Page.init_connection(page.page_knights)
    assert shop.parent == page.page_knights
    from itertools import permutations
    from types import SimpleNamespace
    from unittest.mock import Mock
    from pathlib import Path
    from tests.support.offline import ControlledClock, fixture_image
    from tasks.base.ui import UI
    tabs = (page.page_knights_support, shop, page.page_knights_weekly_task)
    for source, target in permutations(tabs, 2):
        page.Page.init_connection(target)
        assert source.parent == target, (source, target, source.parent)
        with ControlledClock() as clock:
            task = UI.__new__(UI)
            task.config = SimpleNamespace(Emulator_PackageName='com.stove.epic7.google')
            fid = '20261002-160820-406' if source == page.page_knights_support else '20261002-160823-507'
            image = fixture_image(Path('tests/fixtures/knights/manifest.json'), fid)
            states = [source, source, target]
            task.device = SimpleNamespace(image=image, index=0, click=Mock())
            def screenshot():
                task.device.index += 1
                assert task.device.index < len(states), 'navigation failed to arrive'
                clock.advance(1)
            task.device.screenshot = screenshot
            task.ui_page_appear = lambda current: current == states[task.device.index]
            task.ui_page_confirm = Mock(return_value=False)
            task.interval_clear = Mock()
            task.handle_ui_recovery = Mock(return_value=False)
            task.handle_ad_buff_x_close = Mock(side_effect=AssertionError('closed activity panel as ad'))
            task.handle_popup_confirm = Mock(return_value=False)
            task.ui_goto(target)
            task.device.click.assert_called_once()
            clicked = task.device.click.call_args.args[0]
            x1, y1, x2, y2 = clicked.button
            assert x2 < 290 and y2 < 330, (source, target, clicked)
            task.handle_ad_buff_x_close.assert_not_called()
for registered in page.Page.iter_pages():
    buttons = registered.check_button if isinstance(registered.check_button, tuple) else (registered.check_button,)
    for button in buttons:
        assert button.area
print('ROUTES_OK')
'''
        for name, lang in (('CN-Official', 'cn'), ('OVERSEA-Play', 'global_cn'), ('OVERSEA-Play', 'global_en')):
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script, name, lang], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('ROUTES_OK', result.stdout)
