"""Missed input across migrated business flows; actions never change the scene."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server

server.set_lang('global_cn')

from module.alas import AzurLaneAutoScript  # noqa: E402
from module.base.button import ClickButton  # noqa: E402
from tasks.base.ui import UI, Page  # noqa: E402
from tasks.base.popup import PopupHandler  # noqa: E402
from tasks.dungeon.dungeon import Combat  # noqa: E402
from tasks.activity.free_gacha_20 import FreeGacha20  # noqa: E402
from tasks.activity.legacy.e7wc_battle_gate_2026_09_12.e7wc_battle_gate import E7wcBattleGate  # noqa: E402
from tasks.activity.koharu_raffle import KoharuRaffle  # noqa: E402
from tasks.gacha.gacha import Gacha  # noqa: E402
from tasks.mail.mail import Mail  # noqa: E402
from tasks.store.current import (  # noqa: E402
    CurrentStore, BUY_CONFIRM_SINGLE, BUY_CONFIRM_MULTI, BUY_MIN, BUY_MAX, BUY_TIMES_MINUS, BUY_TIMES_PLUS,
)
from tasks.store.purchase import ItemPurchasePlan  # noqa: E402
from tasks.store.assets.assets_store_items import DAILY_FREE_ITEM  # noqa: E402
from tasks.sanctuary.sanctuary import Sanctuary  # noqa: E402
from tests.support.interaction import InteractionReplay, scene  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402
from tests.support.history_huche_shop import CHEAP, CHEAP_DONE, REGULAR_DONE, NOW, WINDOW  # noqa: E402
from tasks.activity.huche_shop import HucheShop  # noqa: E402
from tasks.store.purchase import plan_purchase_selection  # noqa: E402


class MissedClickTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, task_type, frames, **kwargs):
        replay = InteractionReplay(task_type, frames, self.clock, **kwargs)
        # Production loops below always request a screenshot for their first
        # iteration; retain the prepared first observation for that request.
        replay.index = -1
        return replay

    def assert_restarts(self, action):
        runner = object.__new__(AzurLaneAutoScript)
        runner.config = SimpleNamespace(task_call=Mock())
        runner.device = SimpleNamespace(screenshot=Mock(), screenshot_tracking=[], package='offline', sleep=Mock())
        runner.save_error_log = Mock()
        runner.interaction = action
        with patch('module.alas.handle_notify') as notify, patch('module.device.device.show_function_call'):
            self.assertFalse(runner.run('interaction'))
        runner.config.task_call.assert_called_once_with('Restart')
        runner.save_error_log.assert_called_once_with()
        notify.assert_not_called()


class GachaMissedClickTests(MissedClickTests):
    def gacha(self, frames, **kwargs):
        r = self.replay(Gacha, frames, **kwargs)
        r.task._draw_count, r.task._draw_free, r.task._in_standard_pool = 1, True, True
        r.task._save_result = Mock()
        r.task._read_next_free_summon_count = Mock(return_value=1)
        return r

    def test_start_click_retries_before_result_and_does_not_start_again_after_return(self):
        pool = scene('EPIC_BOOKMARK', 'SUMMON_ONE_FREE', page='page_gacha')
        r = self.gacha([pool] * 8 + [scene('SUMMON_RESULT_BACK')] * 2 + [pool])
        r.index = 0  # The initial click uses the already observed pool frame.
        self.assertTrue(r.task._start_summon())
        r.task._handle_summon_flow()
        r.assert_retried(self, 'SUMMON_ONE_FREE', 2)
        self.assertEqual(r.clicks('SUMMON_RESULT_BACK'), [8])
        self.assertEqual(r.index, 10)
        r.task._save_result.assert_called_once_with(tag='result')

    def test_start_never_takes_effect_reaches_framework_restart(self):
        r = self.gacha([scene('EPIC_BOOKMARK', 'SUMMON_ONE_FREE', page='page_gacha')] * 280)
        r.index = 0
        self.assertTrue(r.task._start_summon())
        self.assert_restarts(r.task._handle_summon_flow)
        r.task._save_result.assert_not_called()

    def test_result_controls_retry_on_fresh_frames_and_save_once(self):
        for button in ('SUMMON_NEW', 'SUMMON_SKIP', 'SUMMON_NEXT_PAGE', 'SUMMON_RESULT_BACK'):
            with self.subTest(button=button):
                frames = [scene(button)] * 8
                if button != 'SUMMON_RESULT_BACK':
                    frames += [scene('SUMMON_RESULT_BACK')] * 2
                frames += [scene(page='page_gacha')]
                r = self.gacha(frames)
                r.task._handle_summon_flow()
                r.assert_retried(self, button, 2 if button == 'SUMMON_RESULT_BACK' else 1)
                r.task._save_result.assert_called_once()
                self.assertEqual(r.intervals, [(1.0,), ()])

    def test_continue_click_retries_without_duplicate_result_or_extra_summon(self):
        r = self.gacha([scene('SUMMON_RESULT_BACK', 'SUMMON_FREE_CONTINUE')] * 8
                       + [scene('SUMMON_SKIP')] * 2 + [scene('SUMMON_RESULT_BACK')] * 2
                       + [scene(page='page_gacha')])
        r.task._handle_summon_flow()
        r.assert_retried(self, 'SUMMON_FREE_CONTINUE', 1)
        self.assertEqual(r.task._save_result.call_count, 2)
        self.assertEqual(r.task._read_next_free_summon_count.call_count, 1)
        self.assertEqual(r.clicks('SUMMON_RESULT_BACK'), [10])

    def test_result_never_closes_reaches_real_click_guard_and_restart(self):
        r = self.gacha([scene('SUMMON_RESULT_BACK')] * 160)
        self.assert_restarts(r.task._handle_summon_flow)
        r.task._save_result.assert_called_once_with(tag='result')
        self.assertEqual(r.intervals[-1], ())


class StoreMissedClickTests(MissedClickTests):
    def store(self, frames, **kwargs):
        r = self.replay(CurrentStore, frames, **kwargs)
        for name, asset in (
            ('buy_confirm_single_asset', BUY_CONFIRM_SINGLE), ('buy_confirm_multi_asset', BUY_CONFIRM_MULTI),
            ('buy_min_asset', BUY_MIN), ('buy_max_asset', BUY_MAX),
            ('buy_times_minus_asset', BUY_TIMES_MINUS), ('buy_times_plus_asset', BUY_TIMES_PLUS),
        ):
            setattr(r.task, name, asset)
        r.task._match_purchaseable_item = lambda _: 'DAILY_FREE_ITEM' in r.frame['buttons']
        r.task._is_on_any_store_page = lambda: r.frame['page'] == 'store'
        r.task._has_item = lambda *a: 'DAILY_FREE_ITEM' in r.frame['buttons']
        r.task._log_purchase_debug = Mock()
        r.task._ocr_purchase_counter = lambda _: r.frame.get('counter', (1, 2, 3))
        return r

    def test_item_and_confirm_and_reward_close_all_retry_without_extra_purchase(self):
        r = self.store([scene('DAILY_FREE_ITEM', page='store')] * 6
                       + [scene('BUY_CONFIRM_SINGLE')] * 6 + [scene('TOUCH_TO_CLOSE')] * 6
                       + [scene(page='store')])
        result = r.task._purchase_item(ItemPurchasePlan('free', DAILY_FREE_ITEM, direct_click=True,
                                                       requires_reward_popup=True))
        self.assertTrue(result.success)
        for button in ('DAILY_FREE_ITEM', 'BUY_CONFIRM_SINGLE', 'TOUCH_TO_CLOSE'):
            r.assert_retried(self, button, 0.8 if button == 'DAILY_FREE_ITEM' else 1)
        self.assertTrue(all(i < 6 for i in r.clicks('DAILY_FREE_ITEM')))
        self.assertEqual(result.quantity, 1)
        self.assertEqual(r.index, 18)

    def test_multi_quantity_retry_requires_changed_counter_before_confirm(self):
        r = self.store([scene('DAILY_FREE_ITEM', page='store')]
                       + [scene('BUY_CONFIRM_MULTI', 'BUY_MAX', counter=(1, 2, 3))] * 7
                       + [scene('BUY_CONFIRM_MULTI', 'BUY_MAX', counter=(3, 0, 3))] * 6
                       + [scene(page='store')])
        result = r.task._purchase_item(ItemPurchasePlan('multi', DAILY_FREE_ITEM, desired_quantity=3,
                                                       quantity_strategy='max', direct_click=True))
        self.assertTrue(result.success)
        self.assertEqual(result.quantity, 3)
        r.assert_retried(self, 'BUY_MAX', 1)
        r.assert_retried(self, 'BUY_CONFIRM_MULTI', 1)
        self.assertGreaterEqual(min(r.clicks('BUY_CONFIRM_MULTI')), 8)

    def test_cancel_retries_when_period_target_already_reached(self):
        r = self.store([scene('DAILY_FREE_ITEM', page='store')]
                       + [scene('BUY_CONFIRM_MULTI', 'POPUP_CANCEL')] * 8 + [scene(page='store')])
        r.task._resolve_remaining_purchase_times = lambda *args: (1, (1, 0, 1), 'counter')
        item = ItemPurchasePlan('target', DAILY_FREE_ITEM, desired_quantity=2, quantity_strategy='target',
                                purchase_limit=3, remaining_counter_preset=object())
        result = r.task._purchase_item(item)
        self.assertFalse(result.success)
        self.assertEqual((result.quantity, result.quantity_source), (0, 'target_reached'))
        r.assert_retried(self, 'POPUP_CANCEL', 1)
        self.assertEqual(r.clicks('BUY_CONFIRM_MULTI'), [])
        self.assertEqual(r.index, 9)

    def test_persistent_item_or_confirmation_cannot_report_success(self):
        for popup in (None, 'BUY_CONFIRM_SINGLE'):
            with self.subTest(popup=popup):
                frames = [scene('DAILY_FREE_ITEM', page='store')]
                frames += [scene(popup) if popup else frames[0]] * 160
                r = self.store(frames)
                self.assert_restarts(lambda: r.task._purchase_item(
                    ItemPurchasePlan('ordinary', DAILY_FREE_ITEM, direct_click=True)))

    def test_persistent_cancel_reaches_recovery_without_confirm(self):
        r = self.store([scene('DAILY_FREE_ITEM', page='store')]
                       + [scene('BUY_CONFIRM_MULTI', 'POPUP_CANCEL')] * 180)
        r.task._resolve_remaining_purchase_times = lambda *args: (1, (1, 0, 1), 'counter')
        item = ItemPurchasePlan('target', DAILY_FREE_ITEM, desired_quantity=2, quantity_strategy='target',
                                purchase_limit=3, remaining_counter_preset=object())
        self.assert_restarts(lambda: r.task._purchase_item(item))
        self.assertEqual(r.clicks('BUY_CONFIRM_MULTI'), [])
        r.assert_retried(self, 'POPUP_CANCEL', 1)


class MailMissedClickTests(MissedClickTests):
    def mail(self, frames):
        r = self.replay(Mail, frames)
        r.task._button_available = lambda _: False
        r.task._ocr_top_remaining_state = lambda: r.task._parse_remaining_text('1小时')
        r.task._ocr_remaining_mail_count_state = lambda: r.task._parse_remaining_mail_count_text(
            str(r.frame.get('count', 2)))
        return r

    def claim(self, r):
        r.index = 0
        return r.task._claim_top_mail(r.task._parse_remaining_text('1小时'))

    def test_receive_click_retries_when_mail_count_is_unchanged(self):
        r = self.mail([scene('RECEIVE', page='page_mail')] * 10
                      + [scene('RECEIVE_CONFIRM')] * 6 + [scene(page='page_mail', count=1)] * 8)
        self.assertTrue(self.claim(r))
        r.assert_retried(self, 'RECEIVE', 1)
        r.assert_retried(self, 'RECEIVE_CONFIRM', 1)
        self.assertTrue(all(i < 10 for i in r.clicks('RECEIVE')))
        self.assertGreaterEqual(r.index, 16)

    def test_mail_confirmation_stays_visible_until_second_click(self):
        r = self.mail([scene('RECEIVE', page='page_mail')]
                      + [scene('RECEIVE_CONFIRM')] * 8 + [scene(page='page_mail', count=1)] * 8)
        self.assertTrue(self.claim(r))
        r.assert_retried(self, 'RECEIVE_CONFIRM', 1)
        self.assertEqual(r.clicks('RECEIVE'), [0])

    def test_receive_never_takes_effect_reaches_restart(self):
        r = self.mail([scene('RECEIVE', page='page_mail')] * 180)
        self.assert_restarts(lambda: self.claim(r))
        r.task.config.task_delay.assert_not_called()

    def test_persistent_confirmation_reaches_restart(self):
        r = self.mail([scene('RECEIVE', page='page_mail')] + [scene('RECEIVE_CONFIRM')] * 180)
        self.assert_restarts(lambda: self.claim(r))


class ActivityMissedClickTests(MissedClickTests):
    def activity(self, cls, frames, **kwargs):
        r = self.replay(cls, frames, **kwargs)
        r.index = 0
        r.task.activity_id = 'offline_missed_click'
        r.task._activity_selected = lambda _: True
        if cls is E7wcBattleGate:
            r.task.reward_received = lambda _: r.frame.get('received', False)
        return r

    def test_free_reward_claim_and_close_retry_then_schedule_once(self):
        r = self.activity(FreeGacha20, [scene('FREE_20_GACHA')] * 8
                          + [scene('TOUCH_TO_CLOSE')] * 8 + [scene('FREE_20_GACHA_OBTAINED')])
        self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, 'FREE_20_GACHA', 2)
        r.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        self.assertEqual(len(r.values), 1)
        r.task.config.task_call.assert_called_once_with('Gacha', force_call=False)

    def test_battle_gate_claim_and_close_retry_before_recording_receipt(self):
        r = self.activity(E7wcBattleGate, [scene('E7WC_BATTLE_GATE_CHECK', 'E7WC_LEFT_REWARD_AVAILABLE')] * 8
                          + [scene('TOUCH_TO_CLOSE')] * 8
                          + [scene('E7WC_BATTLE_GATE_CHECK', received=True)])
        self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, 'E7WC_LEFT_REWARD_AVAILABLE', 2)
        r.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        self.assertEqual(r.clicks('E7WC_RIGHT_REWARD_AVAILABLE'), [])
        self.assertEqual(len(r.values), 1)

    def test_raffle_claim_and_close_retry_before_done(self):
        r = self.activity(KoharuRaffle, [scene('CHUN_GATE_CHECK', 'CHUN_TASK_REWARD_PENDING')] * 9
                          + [scene('TOUCH_TO_CLOSE')] * 8 + [scene('CHUN_GATE_CHECK', 'CHUN_ALL_TASK_DONE')])
        self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, 'CHUN_TASK_REWARD_PENDING', 2)
        r.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        self.assertEqual(len(r.values), 1)

    def test_persistent_activity_clicks_have_bounded_failure_without_daily_record(self):
        cases = ((FreeGacha20, scene('FREE_20_GACHA')),
                 (E7wcBattleGate, scene('E7WC_BATTLE_GATE_CHECK', 'E7WC_LEFT_REWARD_AVAILABLE')),
                 (KoharuRaffle, scene('CHUN_GATE_CHECK', 'CHUN_TASK_REWARD_PENDING')))
        for cls, frame in cases:
            with self.subTest(task=cls.__name__):
                # Disable device guards only here to check each local timeout
                # cannot be extended indefinitely by unsuccessful clicks.
                r = self.activity(cls, [frame] * 180, guards=False)
                self.assertFalse(r.task.run_claim(navigate=False))
                self.assertEqual(r.values, {})
                r.task.config.task_call.assert_not_called()


class SanctuaryMissedClickTests(MissedClickTests):
    def test_monthly_entry_retries_until_positive_destination(self):
        r = self.replay(Sanctuary, [scene('HEART_OF_EULERBIS')] * 8 + [scene('HEART_OF_EULERBIS_CHECK')])
        self.assertTrue(r.task._enter_monthly())
        r.assert_retried(self, 'HEART_OF_EULERBIS', 2)
        self.assertEqual(r.index, 8)

    def test_monthly_entry_never_opens_returns_failure(self):
        r = self.replay(Sanctuary, [scene('HEART_OF_EULERBIS')] * 40)
        self.assertFalse(r.task._enter_monthly())
        r.assert_retried(self, 'HEART_OF_EULERBIS', 2)

    def test_level_up_popup_close_retries_until_screen_changes(self):
        r = self.replay(Sanctuary, [scene('TOUCH_TO_CLOSE')] * 6 + [scene('PURIFY')])
        self.assertTrue(r.task._wait_monthly_level_up_settle())
        r.assert_retried(self, 'TOUCH_TO_CLOSE', 0.5)
        self.assertEqual(r.index, 6)

    def test_unknown_frame_after_level_up_click_is_not_settlement(self):
        r = self.replay(Sanctuary, [scene('TOUCH_TO_CLOSE')] + [scene()] * 35)
        self.assertFalse(r.task._wait_monthly_level_up_settle())


class HucheMissedClickTests(MissedClickTests):
    def huche(self, frames):
        r = self.replay(HucheShop, frames)
        r.index = 0
        r.task.activity_id = 'offline_huche'
        r.task.price_limits, r.task.regular_price = {160: 2, 200: 4}, 200
        r.task.find_offers = lambda: r.frame.get('offers', [])
        r.task.read_balance = lambda: 2000
        r.task._locate = lambda button, area: (0, 0) if button.name in r.frame['buttons'] else None
        r.task._match_at = lambda *a, **kw: True
        r.task.popup_selection = lambda offer, balance: (
            (plan_purchase_selection('target', r.frame.get('counter', (2, 0, 2)), 2), BUY_CONFIRM_MULTI)
            if not r.frame.get('wrong') else None)
        self.stack.enter_context(patch('tasks.activity.huche_shop.aware_time', return_value=NOW))
        return r

    def test_offer_quantity_and_confirm_retry_then_wait_for_stock(self):
        before = scene('HUCHE_SHOP_CHECK', offers=[CHEAP, REGULAR_DONE])
        popup = ('HUCHE_BUY_POPUP_CHECK', 'BUY_MAX', 'BUY_CONFIRM_MULTI')
        r = self.huche([before] * 8 + [scene(*popup, counter=(1, 1, 2))] * 8
                       + [scene(*popup)] * 8 + [before] * 8
                       + [scene('HUCHE_SHOP_CHECK', offers=[CHEAP_DONE, REGULAR_DONE])] * 2)
        self.assertTrue(r.task.run_purchase(WINDOW))
        for button in ('HucheMysticBuy', 'BUY_MAX', 'BUY_CONFIRM_MULTI'):
            r.assert_retried(self, button, 2)
        self.assertTrue(all(i < 8 for i in r.clicks('HucheMysticBuy')))
        self.assertEqual(r.index, 33)
        self.assertEqual(len(r.values), 2)

    def test_wrong_popup_cancel_retries_without_buying(self):
        before = scene('HUCHE_SHOP_CHECK', offers=[CHEAP, REGULAR_DONE])
        r = self.huche([before] * 2 + [scene('HUCHE_BUY_POPUP_CHECK', 'HUCHE_BUY_CANCEL', wrong=True)] * 14
                       + [before])
        self.assertFalse(r.task.run_purchase(WINDOW))
        r.assert_retried(self, 'HucheBuyCancel', 2)
        self.assertEqual(r.clicks('BUY_CONFIRM_MULTI'), [])
        self.assertEqual(r.index, 16)

    def test_persistent_purchase_confirmation_reaches_framework_restart(self):
        before = scene('HUCHE_SHOP_CHECK', offers=[CHEAP, REGULAR_DONE])
        r = self.huche([before] * 2 + [scene('HUCHE_BUY_POPUP_CHECK', 'BUY_CONFIRM_MULTI')] * 180)
        self.assert_restarts(lambda: r.task.run_purchase(WINDOW))
        self.assertEqual(len(r.values), 1)  # Only pre-existing sold-out regular stock.


class SharedRecoveryTests(MissedClickTests):
    def test_popup_click_does_not_advance_input_or_bypass_interval(self):
        r = self.replay(PopupHandler, [scene('POPUP_CANCEL')] * 8 + [scene()])
        for index in range(len(r.frames)):
            r.screenshot()
            before = r.index
            handled = r.task.handle_popup_cancel(interval=2)
            self.assertEqual(r.index, before)
            self.assertEqual(handled, index in (0, 5))
        self.assertEqual(r.actions, [(0, 'POPUP_CANCEL'), (5, 'POPUP_CANCEL')])

    def test_network_retry_remains_visible_then_recovers_without_reward_action(self):
        r = self.replay(PopupHandler, [scene('NETWORK_ERROR_ABNORMAL')] * 8 + [scene()])
        outcomes = []
        for _ in r.frames:
            r.screenshot()
            outcomes.append(r.task.handle_network_error())
        self.assertGreaterEqual(sum(outcomes), 2)
        self.assertFalse(outcomes[-1])
        self.assertTrue(all(name == 'TOUCH_TO_CLOSE' for _, name in r.actions))
        self.assertEqual(r.values, {})

    def test_unknown_screen_uses_real_stuck_guard_and_scheduler(self):
        r = self.replay(PopupHandler, [scene()] * 180)

        def wait_unknown():
            while True:
                r.screenshot()
                r.task.handle_popup_cancel()

        self.assert_restarts(wait_unknown)
        self.assertEqual(r.actions, [])

    def test_permanent_popup_uses_real_click_guard_and_scheduler(self):
        r = self.replay(PopupHandler, [scene('POPUP_CANCEL')] * 180)

        def close_popup():
            while True:
                r.screenshot()
                r.task.handle_popup_cancel()

        self.assert_restarts(close_popup)
        r.assert_retried(self, 'POPUP_CANCEL', 2)


class NavigationCombatMissedClickTests(MissedClickTests):
    def navigation(self, frames):
        r = self.replay(UI, frames)
        destination = type('Destination', (), {'name': 'destination', 'parent': None, 'check_button': None})()
        class Source:
            name, parent, check_button = 'source', destination, 'source'
            links_need_match = set()
        source = Source()
        # A real route loop and timer with a minimal isolated graph.
        r.task._ui_build_dynamic_links = lambda: {}
        r.task._ui_get_link_button = lambda *a: ClickButton((0, 0, 20, 20), name='route')
        r.task._ui_record_transition = lambda *a: None
        r.task.ui_button_interval_reset = lambda *a: None
        r.task.ui_page_confirm = lambda *a: False
        r.task.handle_ui_recovery = lambda: False
        for name in ('init_connection', 'clear_connection'):
            self.stack.enter_context(patch.object(Page, name))
        self.stack.enter_context(patch.object(Page, 'iter_check_buttons', return_value=()))
        self.stack.enter_context(patch.object(Page, 'iter_pages', side_effect=lambda: iter([source])))
        return r, destination

    def test_navigation_retries_source_and_exits_on_first_destination_frame(self):
        r, dest = self.navigation([scene(page='source')] * 8 + [scene(page='destination')])
        r.task.ui_goto(dest, skip_first_screenshot=False)
        r.assert_retried(self, 'route', 2)
        self.assertEqual(r.index, 8)

    def test_navigation_stuck_source_reaches_framework_restart(self):
        r, dest = self.navigation([scene(page='source')] * 180)
        self.assert_restarts(lambda: r.task.ui_goto(dest, skip_first_screenshot=False))

    def combat(self, frames):
        r = self.replay(Combat, frames)
        r.task._combat_supports_fast_combat = lambda: True
        r.task._is_fast_combat_locked = lambda: False
        r.task._detect_auto_combat_state = lambda: None
        return r

    def test_fast_toggle_retries_without_claiming_enabled_before_new_state(self):
        r = self.combat([scene('FAST_COMBAT_OFF')] * 8 + [scene('FAST_COMBAT_ON')])
        results = []
        for _ in r.frames:
            r.screenshot()
            results.append(r.task._ensure_fast_combat_state(True))
        self.assertEqual(results, [False] * 8 + [True])
        r.assert_retried(self, 'FAST_COMBAT_OFF', r.task.COMBAT_TOGGLE_INTERVAL_SECONDS)

    def test_battle_start_and_result_buttons_retry_without_early_completion(self):
        r = self.combat([scene('REPEAT_COMBAT_MENU', 'FAST_COMBAT_OFF', 'COMBAT_START')] * 30
                        + [scene('COMBAT_RESULT_CLEAR')] * 8 + [scene('COMBAT_RESULT_CONFIRM')] * 8
                        + [scene(page='done')])
        self.assertTrue(r.task._run_normal_combat(lambda: r.frame['page'] == 'done', skip_first_screenshot=False))
        r.assert_retried(self, 'COMBAT_START', r.task.COMBAT_START_INTERVAL_SECONDS)
        for button in ('COMBAT_RESULT_CLEAR', 'COMBAT_RESULT_CONFIRM'):
            r.assert_retried(self, button, r.task.COMBAT_RESULT_INTERVAL_SECONDS)
        self.assertEqual(r.index, 46)

    def test_persistent_battle_start_reaches_framework_restart(self):
        r = self.combat([scene('REPEAT_COMBAT_MENU', 'FAST_COMBAT_OFF', 'COMBAT_START')] * 380)
        self.assert_restarts(lambda: r.task._run_normal_combat(lambda: False, skip_first_screenshot=False))
