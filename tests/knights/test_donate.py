"""Real donation labels and deterministic inventory/confirmation regressions."""

import json
import unittest
from unittest.mock import Mock

from module.config import server

server.set_lang('global_cn')

from module.base.button import ClickButton
from tasks.knights.donate import (
    donation_views_match,
    parse_donation_quantity,
    parse_donation_stock,
)
from tests.support.interaction import scene
from tests.support.knights_donate import (
    FLOWER,
    PENGUIN,
    DonationReplay,
    listing,
    pixel_task,
)
from tests.support.offline import ROOT, ControlledClock

LOW = '20261018-141840-024'
BEFORE = '20261018-142106-068'
AFTER_ONE = '20261018-142110-919'
AFTER_TWO = '20261018-142114-942'
AFTER_THREE = '20261018-142118-971'
REWARD = '20261018-142109-419'
FLOWER_BEFORE = '20261018-184049-280'
FLOWER_REWARD = '20261018-184103-792'
FLOWER_AFTER = '20261018-184105-118'
FLOWER_COMPLETE = '20261018-184107-923'
STOCK_ONE = (PENGUIN, 1, 2, 'penguin_a', 328)
STOCK_FIVE = (PENGUIN, 5, 2, 'penguin_a', 328)


class DonationCaptureTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_all_visible_rows_read_stock_and_exact_button_quantity(self):
        for capture, stock, count in [(LOW, 1, 3), (BEFORE, 707, 3),
                                      (AFTER_ONE, 705, 2), (AFTER_TWO, 703, 1)]:
            with self.subTest(capture=capture):
                task = pixel_task(capture)
                self.assertTrue(task._donation_page_ready())
                pairs = task._find_donate_action_pairs(task._build_donate_plans())
                self.assertEqual(len(pairs), count)
                for name, button in pairs:
                    quote = task._donation_quote(name, button)
                    self.assertIsNotNone(quote)
                    self.assertEqual((quote.item, quote.stock, quote.quantity), (PENGUIN, stock, 2))

    def test_own_flower_inventory_is_read_but_not_paired_with_other_items(self):
        task = pixel_task(BEFORE)
        self.assertEqual(task._donation_inventory(task._build_donate_plans(), FLOWER), 8438)
        self.assertFalse(any(name == FLOWER for name, _ in
                             task._find_donate_action_pairs(task._build_donate_plans())))
        task = pixel_task(AFTER_THREE)
        self.assertEqual(task._find_donate_action_pairs(task._build_donate_plans()), [])
        self.assertEqual(task._donation_inventory(task._build_donate_plans(), PENGUIN), 701)

    def test_reward_overlay_cannot_be_used_as_a_ready_list(self):
        for capture in (REWARD, FLOWER_REWARD):
            with self.subTest(capture=capture):
                task = pixel_task(capture)
                self.assertFalse(task._donation_page_ready())
                self.assertTrue(task._donation_text((505, 218, 785, 242), 'result').startswith('成功支援'))

    def test_captured_flower_quantity_debit_and_completed_request(self):
        task = pixel_task(FLOWER_BEFORE)
        self.assertTrue(task._donation_page_ready())
        plans = task._build_donate_plans()
        pairs = task._find_donate_action_pairs(plans)
        quotes = [task._donation_quote(item, button) for item, button in pairs]
        self.assertEqual([(row.item, row.stock, row.quantity) for row in quotes],
                         [(PENGUIN, 701, 2), (FLOWER, 8474, 1), (PENGUIN, 701, 2)])
        for capture in (FLOWER_AFTER, FLOWER_COMPLETE):
            with self.subTest(capture=capture):
                task = pixel_task(capture)
                self.assertTrue(task._donation_page_ready())
                self.assertEqual(task._donation_inventory(plans, FLOWER), 8473)
                self.assertFalse(any(item == FLOWER for item, _ in task._find_donate_action_pairs(plans)))

    def test_cut_stock_field_is_unknown_without_ocr(self):
        task = pixel_task(BEFORE)
        task._donation_text = Mock(side_effect=AssertionError('clipped field sent to OCR'))
        self.assertIsNone(task._donation_stock_at(170))
        self.assertIsNone(task._donation_quote(PENGUIN, ClickButton((1082, 160, 1123, 180))))
        self.assertIsNone(task._donation_quote(PENGUIN, ClickButton((1082, 620, 1123, 650))))

    def test_recipient_change_is_list_movement_but_portrait_glow_is_not(self):
        task = pixel_task(BEFORE)
        before = task._donation_view()
        # Explicit synthetic pixels over the redacted name field, not evidence
        # from another real scroll position. The item columns remain identical.
        task.device.image[150:160, 850:855] = 255
        self.assertFalse(donation_views_match(before, task._donation_view()))
        before = task._donation_view()
        task.device.image[135:645, 775:840] = 255
        self.assertTrue(donation_views_match(before, task._donation_view()))


class DonationRuleTests(unittest.TestCase):
    def test_strict_text_parsing_distinguishes_zero_from_unknown(self):
        for text, expected in [('持有：１　', 1), ('持有:8,438', 8438), ('持有：0', 0),
                               ('持有：1,23', None), ('持有：O', None), ('8438', None),
                               ('支援707个', None), ('', None)]:
            self.assertEqual(parse_donation_stock(text), expected, text)
        for text, expected in [('支援2个', 2), ('支援１个', 1), ('支援0个', None),
                               ('支援', None), ('【支援2个', None), ('持有2', None)]:
            self.assertEqual(parse_donation_quantity(text), expected, text)

    def test_disabled_switches_never_read_inventory_or_click(self):
        with ControlledClock() as clock:
            replay = DonationReplay([listing(STOCK_FIVE)], clock, penguin=False, flower=False)
            replay.task._donation_quote = Mock(side_effect=AssertionError('disabled donation read'))
            self.assertTrue(replay.task._run_support_donate())
            self.assertEqual(replay.actions, [])

    def test_translations_describe_inventory_bounded_switches(self):
        for lang in ('zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'es-ES'):
            data = json.loads((ROOT / f'module/config/i18n/{lang}.json').read_text(encoding='utf-8'))
            for key in ('SupportLowerLevelFairyFlower', 'SupportBeginnerPenguin'):
                self.assertIn('Knights', data)
                self.assertTrue(data['Knights'][key]['name'])
                self.assertTrue(data['Knights'][key]['help'])
        self.assertIn('尽力捐赠', json.loads((ROOT / 'module/config/i18n/zh-CN.json')
                                         .read_text(encoding='utf-8'))['Knights']['SupportBeginnerPenguin']['name'])


class DonationReplayTests(unittest.TestCase):
    def run_replay(self, frames, **options):
        with ControlledClock() as clock:
            replay = DonationReplay(frames, clock, **options)
            result = replay.task._run_support_donate()
        return replay, result

    def test_insufficient_inventory_scans_without_donating(self):
        replay, result = self.run_replay([listing(STOCK_ONE)] * 32)
        self.assertTrue(result)
        self.assertFalse(any(name.startswith('donate_') for _, name in replay.actions))
        self.assertEqual(len(replay.clicks('swipe')), 4)

    def test_one_unaffordable_request_does_not_hide_a_smaller_request(self):
        smaller = (PENGUIN, 1, 1, 'penguin_b', 458)
        frames = [listing(STOCK_ONE, smaller)] * 10 + [listing(stocks={PENGUIN: 0}, view=1)] * 30
        replay, result = self.run_replay(frames)
        self.assertTrue(result)
        self.assertEqual(len(replay.clicks('donate_penguin_b')), 1)
        self.assertEqual(replay.clicks('donate_penguin_a'), [])

    def test_flower_and_penguin_switches_are_independent(self):
        rows = ((FLOWER, 1, 1, 'flower', 198), STOCK_FIVE)
        for penguin, flower, expected in [(False, True, 'donate_flower'), (True, False, 'donate_penguin_a')]:
            with self.subTest(penguin=penguin, flower=flower):
                frames = [listing(*rows)] * 10 + [listing(view=1, stocks={FLOWER: 0, PENGUIN: 3})] * 30
                replay, result = self.run_replay(frames, penguin=penguin, flower=flower)
                self.assertTrue(result)
                self.assertEqual([name for _, name in replay.actions if name.startswith('donate_')], [expected])

    def test_no_effect_click_retries_only_same_recipient_with_production_interval(self):
        frames = [listing(STOCK_FIVE)] * 16 + [listing(view=1, stocks={PENGUIN: 3})] * 30
        replay, result = self.run_replay(frames)
        self.assertTrue(result)
        replay.assert_retried(self, 'donate_penguin_a', 2)
        self.assertFalse(any(name.startswith('donate_') and name != 'donate_penguin_a'
                             for _, name in replay.actions))

    def test_reward_and_old_list_do_not_release_another_donation(self):
        reward = scene('SUPPORT_CHECK', 'TOUCH_TO_CLOSE', overlay=True, result='成功支援，已获得了下列的奖励。')
        frames = ([listing(STOCK_FIVE)] * 10 + [reward] * 3 + [listing(STOCK_FIVE)] * 8
                  + [listing(view=1, stocks={PENGUIN: 3})] * 30)
        replay, result = self.run_replay(frames)
        self.assertTrue(result)
        self.assertEqual(len(replay.clicks('donate_penguin_a')), 1)
        self.assertGreaterEqual(len(replay.clicks('TOUCH_TO_CLOSE')), 1)

    def test_changed_recipient_or_quantity_never_retries_pending_click(self):
        for row in [(PENGUIN, 5, 2, 'penguin_b', 328), (PENGUIN, 5, 1, 'penguin_a', 328)]:
            with self.subTest(row=row):
                frames = [listing(STOCK_FIVE)] * 10 + [listing(row)] * 60
                replay, result = self.run_replay(frames)
                self.assertFalse(result)
                self.assertEqual(len(replay.clicks('donate_penguin_a')), 1)
                self.assertEqual(replay.clicks('donate_penguin_b'), [])

    def test_network_and_wrong_debit_do_not_count_as_success(self):
        network = scene(network=True)
        frames = [listing(STOCK_FIVE)] * 10 + [network] * 3 + [listing(stocks={PENGUIN: 2})] * 60
        replay, result = self.run_replay(frames)
        self.assertFalse(result)
        self.assertEqual(len(replay.clicks('donate_penguin_a')), 1)
        self.assertEqual(len(replay.clicks('NETWORK_RETRY')), 3)

    def test_confirmed_debit_does_not_repay_a_late_disabled_request(self):
        old_request = (PENGUIN, 3, 2, 'penguin_a', 328)
        new_request = (PENGUIN, 3, 2, 'penguin_b', 328)
        frames = ([listing(STOCK_FIVE)] * 10 + [listing(old_request)] * 3
                  + [listing(new_request, view=1)] * 6
                  + [listing(view=2, stocks={PENGUIN: 1})] * 30)
        replay, result = self.run_replay(frames)
        self.assertTrue(result)
        self.assertEqual(len(replay.clicks('donate_penguin_a')), 1)
        self.assertEqual(len(replay.clicks('donate_penguin_b')), 1)

    def test_unreadable_old_request_after_reward_still_blocks_scroll_and_payment(self):
        reward = scene('SUPPORT_CHECK', 'TOUCH_TO_CLOSE', overlay=True, result='成功支援')
        unknown = (PENGUIN, None, 2, 'penguin_a', 328)
        frames = [listing(STOCK_FIVE)] * 10 + [reward] + [listing(unknown)] * 60
        replay, result = self.run_replay(frames)
        self.assertFalse(result)
        self.assertEqual(len(replay.clicks('swipe')), 2)
        self.assertEqual(len(replay.clicks('donate_penguin_a')), 1)

    def test_success_popup_and_changed_list_allow_inventory_hidden_by_reordering(self):
        reward = scene('SUPPORT_CHECK', 'TOUCH_TO_CLOSE', overlay=True, result='成功支援')
        frames = ([listing(STOCK_FIVE)] * 10 + [reward]
                  + [listing(view=1)] * 30)
        replay, result = self.run_replay(frames)
        self.assertTrue(result)
        self.assertEqual(len(replay.clicks('donate_penguin_a')), 1)

    def test_completed_rows_do_not_stop_search_before_later_affordable_items(self):
        flower = (FLOWER, 2, 1, 'flower', 328)
        frames = ([listing()] * 10 + [listing(flower, view=1)] * 6
                  + [listing(view=2, stocks={FLOWER: 1})] * 30)
        replay, result = self.run_replay(frames)
        self.assertTrue(result)
        self.assertEqual(len(replay.clicks('donate_flower')), 1)

    def test_unknown_quote_skips_payment_and_reports_unverified_scan(self):
        replay, result = self.run_replay([listing((PENGUIN, None, 2, 'bad', 328))] * 32)
        self.assertFalse(result)
        self.assertEqual(replay.clicks('donate_bad'), [])

    def test_clipped_request_is_revisited_when_its_fields_become_visible(self):
        clipped = (PENGUIN, 5, 2, 'penguin_a', 170)
        frames = ([listing(clipped)] * 10 + [listing(STOCK_FIVE, view=1)] * 6
                  + [listing(view=2, stocks={PENGUIN: 3})] * 30)
        replay, result = self.run_replay(frames)
        self.assertTrue(result)
        self.assertEqual(len(replay.clicks('donate_penguin_a')), 1)
        self.assertGreaterEqual(replay.clicks('donate_penguin_a')[0], 10)

    def test_captured_three_donations_confirm_inventory_even_without_reward_frames(self):
        def capture(key):
            return scene(capture=key)
        frames = ([capture(BEFORE)] * 10 + [capture(AFTER_ONE)] * 5
                  + [capture(AFTER_TWO)] * 5 + [capture(AFTER_THREE)] * 30)
        replay, result = self.run_replay(frames)
        self.assertTrue(result)
        donations = [(index, name) for index, name in replay.actions if name.startswith('SUPPORT_ACTION')]
        self.assertEqual(len(donations), 3)
        # Two stable frames and the production two-second gate place each
        # payment in its corresponding 707/705/703-stock observation segment.
        self.assertEqual([index for index, _ in donations], [8, 13, 18])

    def test_captured_flower_donation_waits_through_reward_and_delayed_list(self):
        # The repeated old list explicitly models delayed UI refresh after the
        # real reward. It cannot release a retry or a payment for another item.
        # Six half-second reward frames cross Timer's strict > 2-second gate,
        # proving that an ineffective close is retried with production timing.
        frames = ([scene(capture=FLOWER_BEFORE)] * 10 + [scene(capture=FLOWER_REWARD)] * 6
                  + [scene(capture=FLOWER_BEFORE)] * 8 + [scene(capture=FLOWER_AFTER)] * 5
                  + [scene(capture=FLOWER_COMPLETE)] * 30)
        replay, result = self.run_replay(frames, penguin=False)
        self.assertTrue(result)
        donations = [(index, name) for index, name in replay.actions if name.startswith('SUPPORT_ACTION')]
        task = pixel_task(FLOWER_BEFORE)
        flower_button = next(button for item, button in task._find_donate_action_pairs(
            task._build_donate_plans()) if item == FLOWER)
        self.assertEqual(donations, [(8, flower_button.name)])
        self.assertEqual(replay.clicks('TOUCH_TO_CLOSE'), [10, 15])
        self.assertFalse(any(10 <= index < 24 for index in replay.clicks('swipe')))
        self.assertGreaterEqual(replay.index, 29)
        self.assertTrue(replay.task._donation_page_ready())

    def test_captured_flower_debit_confirms_when_reward_frame_is_missed(self):
        frames = ([scene(capture=FLOWER_BEFORE)] * 10 + [scene(capture=FLOWER_AFTER)] * 5
                  + [scene(capture=FLOWER_COMPLETE)] * 30)
        replay, result = self.run_replay(frames, penguin=False)
        self.assertTrue(result)
        self.assertEqual([index for index, name in replay.actions if name.startswith('SUPPORT_ACTION')], [8])
        self.assertEqual(replay.clicks('TOUCH_TO_CLOSE'), [])
        self.assertGreaterEqual(replay.index, 15)
        self.assertTrue(replay.task._donation_page_ready())
