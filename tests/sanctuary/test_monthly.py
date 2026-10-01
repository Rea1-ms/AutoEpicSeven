"""Monthly tier, capacity, and scheduling regressions without a real device."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from module.config import server as server_

server_.set_lang('global_cn')

from tasks.sanctuary.sanctuary import Sanctuary  # noqa: E402
from tests.support.sanctuary import MonthlyReplay, replay_timer  # noqa: E402


class SanctuaryMonthlyLevelUpTest(unittest.TestCase):
    def make_task(self, reward_tier: str) -> Sanctuary:
        task = Sanctuary.__new__(Sanctuary)
        task.config = SimpleNamespace(SanctuaryMonthly_RewardTier=reward_tier)
        return task

    def test_max_minus_1_tier_moves_up_after_level_up(self):
        task = self.make_task("MaxMinus1")

        heart_level, target_tier = task._sync_monthly_target_tier_after_level_up(
            heart_level=3,
            target_tier="A",
        )

        self.assertEqual(heart_level, 4)
        self.assertEqual(target_tier, "S")

    def test_max_minus_2_tier_moves_up_after_level_up(self):
        task = self.make_task("MaxMinus2")

        heart_level, target_tier = task._sync_monthly_target_tier_after_level_up(
            heart_level=5,
            target_tier="A",
        )

        self.assertEqual(heart_level, 6)
        self.assertEqual(target_tier, "S")

    def test_fixed_tier_stays_unchanged_after_level_up(self):
        task = self.make_task("S")

        heart_level, target_tier = task._sync_monthly_target_tier_after_level_up(
            heart_level=4,
            target_tier="S",
        )

        self.assertEqual(heart_level, 5)
        self.assertEqual(target_tier, "S")

    def test_unknown_heart_level_forces_re_read(self):
        task = self.make_task("MaxMinus1")

        heart_level, target_tier = task._sync_monthly_target_tier_after_level_up(
            heart_level=None,
            target_tier="A",
        )

        self.assertIsNone(heart_level)
        self.assertIsNone(target_tier)

    def test_heart_level_does_not_exceed_max_level(self):
        task = self.make_task("MaxMinus1")

        heart_level, target_tier = task._sync_monthly_target_tier_after_level_up(
            heart_level=11,
            target_tier="SS",
        )

        self.assertEqual(heart_level, 11)
        self.assertEqual(target_tier, "SS")

    def test_heart_level_is_max_only_at_or_above_11(self):
        task = self.make_task("MaxMinus1")

        self.assertFalse(task._heart_level_is_max(None))
        self.assertFalse(task._heart_level_is_max(10))
        self.assertTrue(task._heart_level_is_max(11))

    def test_smart_mode_dispatches_to_smart_custody(self):
        task = self.make_task("Smart")
        task._monthly_purify_smart = lambda: "smart-result"

        self.assertEqual(task._monthly_purify(), "smart-result")


class SanctuaryMonthlyCapacityTest(unittest.TestCase):
    def setUp(self):
        server_.set_lang('global_cn')

    def test_resource_parser_accepts_balance_greater_than_cost(self):
        from tasks.sanctuary.monthly import OcrPurifyTimes, OCR_PURIFY_TIMES_FULL
        ocr = OcrPurifyTimes(OCR_PURIFY_TIMES_FULL, lang='cn')
        self.assertEqual(ocr.format_result('400/10'), (400, -390, 10))
        self.assertEqual(ocr.format_result('9/10'), (9, 1, 10))

    def test_both_counter_layouts_purify_at_their_actual_button_positions(self):
        for mode in ('Smart', 'SS'):
            for layout, offset in (('not_full', 0), ('full', 126)):
                with self.subTest(mode=mode, layout=layout):
                    flow = MonthlyReplay([{'layout': layout}] * 3 + [{'claimed': True}], mode)
                    self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
                    self.assertEqual(len(flow.actions), 1 if mode == 'Smart' else 2)
                    self.assertTrue(all(action[1:] == ('PURIFY', (134+offset, 638, 168+offset, 677)) for action in flow.actions))

    def test_layout_changes_relocate_the_next_click(self):
        flow = MonthlyReplay([{'layout': 'not_full'}] * 3 + [{'layout': 'full'}] * 3 + [{'claimed': True}])
        self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
        self.assertEqual([action[2] for action in flow.actions], [(134, 638, 168, 677), (260, 638, 294, 677)])

    def test_one_plausible_counter_frame_does_not_authorize_purify(self):
        flow = MonthlyReplay([
            {'counter': (400, -390, 10, 'full')}, {'counter': (390, -380, 10, 'full')},
        ] + [{'counter': (0, 10, 10, 'full')}] * 3)
        self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_EXHAUSTED)
        self.assertEqual(flow.actions, [])

    def test_balance_below_cost_exhausts_in_both_modes(self):
        for mode in ('Smart', 'SS'):
            flow = MonthlyReplay([{'counter': (9, 1, 10, 'full')}] * 3, mode)
            self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_EXHAUSTED)
            self.assertEqual(flow.actions, [])

    def test_unknown_slot_never_becomes_full_or_schedules_next_week(self):
        from module.exception import ScriptError
        from unittest.mock import Mock
        for mode in ('Smart', 'SS'):
            with self.subTest(mode=mode):
                flow = MonthlyReplay([{'slot': False}] * 45, mode)
                flow.task.config.task_delay = Mock()
                with self.assertRaisesRegex(ScriptError, 'deposit capacity is unknown'):
                    flow.run(public=True)
                self.assertEqual(flow.actions, [])
                self.assertEqual(flow.task._monthly_status, Sanctuary.MONTHLY_STATUS_FAILED)
                flow.task.config.task_delay.assert_not_called()

    def test_slot_recovers_without_a_premature_full_result(self):
        for mode in ('Smart', 'SS'):
            flow = MonthlyReplay([{'slot': False}] * 3 + [{}] * 3 + [{'claimed': True}], mode)
            self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
            self.assertTrue(flow.actions)
            self.assertTrue(all(index > 3 for index, _, _ in flow.actions))

    def test_capacity_requires_positive_free_slot_even_with_readable_counter(self):
        flow = MonthlyReplay([{'slot': False}])
        flow.screenshot()
        self.assertIsNone(flow.task._is_monthly_deposit_box_full())
        flow = MonthlyReplay([{}])
        flow.screenshot()
        self.assertIs(flow.task._is_monthly_deposit_box_full(), False)

    def test_hidden_button_waits_then_relocates_before_clicking(self):
        flow = MonthlyReplay([{'purify': False}] * 4 + [{}] + [{'claimed': True}])
        self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
        self.assertEqual(flow.actions, [(5, 'PURIFY', (260, 638, 294, 677))])

    def test_permanently_hidden_button_errors_instead_of_clicking_or_exhausting(self):
        from module.exception import ScriptError
        for mode in ('Smart', 'SS'):
            flow = MonthlyReplay([{'purify': False}] * 40, mode)
            with self.assertRaisesRegex(ScriptError, '(purify button|PURIFY) not detected'):
                flow.run()
            self.assertEqual(flow.actions, [])

    def test_cancel_popup_and_custody_must_finish_before_purify(self):
        before = {'deposit_tiers': ('B', None, None, None, None)}
        stored = {'deposit_tiers': ('B', 'S', None, None, None), 'custody': False}
        flow = MonthlyReplay([
            {'cancel': True, 'popup': True}, {'popup': True, 'slot': False}, before,
            before, {'tier': None, **stored}, stored,
            stored, stored, stored, {'claimed': True},
        ])
        self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
        self.assertEqual([(index, name) for index, name, _ in flow.actions], [
            (1, 'POPUP_CANCEL'), (4, 'CUSTODY'), (9, 'PURIFY'),
        ])

    def test_missing_slot_before_custody_errors_without_refreshing_protected_reward(self):
        from module.exception import ScriptError
        flow = MonthlyReplay([{'cancel': True}] + [{'slot': False}] * 40)
        with self.assertRaisesRegex(ScriptError, 'deposit capacity is unknown'):
            flow.run()
        self.assertEqual([name for _, name, _ in flow.actions], ['POPUP_CANCEL'])

    def test_missing_slot_after_confirmed_custody_still_errors(self):
        from module.exception import ScriptError
        stored = {'deposit_tiers': ('S', None, None, None, None), 'custody': False}
        flow = MonthlyReplay([{'cancel': True}, {}, {}] + [stored] * 2 + [{'slot': False, **stored}] * 40)
        with self.assertRaisesRegex(ScriptError, 'deposit capacity is unknown'):
            flow.run()
        self.assertEqual([name for _, name, _ in flow.actions], ['POPUP_CANCEL', 'CUSTODY'])

    def test_missing_tier_marker_does_not_confirm_custody(self):
        flow = MonthlyReplay([{'tier': None}] * 15)
        with patch('tasks.sanctuary.monthly.Timer', replay_timer(lambda: flow.at)):
            self.assertFalse(flow.task._wait_monthly_custody_settle(object()))
        self.assertEqual(flow.actions, [])

    def test_failed_custody_keeps_smart_purify_blocked(self):
        flow = MonthlyReplay(
            [{'cancel': True}, {}, {}] + [{'tier': None, 'custody': False}] * 13 + [{'claimed': True}]
        )
        self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
        self.assertEqual([name for _, name, _ in flow.actions], ['POPUP_CANCEL', 'CUSTODY'])

    def test_fixed_tier_mode_stops_when_custody_is_unconfirmed(self):
        from module.exception import ScriptError
        flow = MonthlyReplay([{'tier': 'SS'}] * 2 + [{'tier': None}] * 20, 'SS')
        with self.assertRaisesRegex(ScriptError, 'custody was not confirmed'):
            flow.run()
        self.assertEqual([name for _, name, _ in flow.actions], ['CUSTODY'])

    def test_invalid_counter_does_not_authorize_fixed_tier_purify(self):
        flow = MonthlyReplay([{'counter': (0, 0, 0, None)}] * 3 + [{'claimed': True}], 'SS')
        self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
        self.assertEqual(flow.actions, [])

    def test_claimed_reward_precedes_unknown_capacity(self):
        for mode in ('Smart', 'SS'):
            flow = MonthlyReplay([{'claimed': True, 'slot': False, 'counter': (0, 0, 0, None)}], mode)
            self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
            self.assertEqual(flow.actions, [])


class SanctuaryMonthlySchedulingTest(unittest.TestCase):
    class DummyConfig:
        def __init__(self):
            self.Scheduler_ServerUpdate = "02:00"
            self.delayed_to = None

        def task_delay(self, *, target=None, **kwargs):
            self.delayed_to = target

    def make_task(self, monthly_status: str) -> Sanctuary:
        task = Sanctuary.__new__(Sanctuary)
        task.config = self.DummyConfig()
        task._monthly_status = monthly_status
        task._ensure_app_running = lambda: None
        task.run_monthly = lambda: monthly_status != Sanctuary.MONTHLY_STATUS_FAILED
        return task

    def test_claimed_delays_to_next_month_server_update(self):
        task = self.make_task(Sanctuary.MONTHLY_STATUS_CLAIMED)
        expected = datetime(2026, 6, 1, 2, 0, 0)

        with patch("tasks.sanctuary.sanctuary.get_server_next_month_update", return_value=expected):
            success = task.run_monthly_task()

        self.assertTrue(success)
        self.assertEqual(task.config.delayed_to, expected)

    def test_full_delays_to_next_monday_server_update(self):
        task = self.make_task(Sanctuary.MONTHLY_STATUS_FULL)
        expected = datetime(2026, 5, 18, 2, 0, 0)

        with patch("tasks.sanctuary.sanctuary.get_server_next_monday_update", return_value=expected):
            success = task.run_monthly_task()

        self.assertTrue(success)
        self.assertEqual(task.config.delayed_to, expected)

    def test_exhausted_delays_to_next_monday_server_update(self):
        task = self.make_task(Sanctuary.MONTHLY_STATUS_EXHAUSTED)
        expected = datetime(2026, 5, 18, 2, 0, 0)

        with patch("tasks.sanctuary.sanctuary.get_server_next_monday_update", return_value=expected):
            success = task.run_monthly_task()

        self.assertTrue(success)
        self.assertEqual(task.config.delayed_to, expected)

    def test_failed_delays_to_next_daily_server_update(self):
        task = self.make_task(Sanctuary.MONTHLY_STATUS_FAILED)
        expected = datetime(2026, 5, 14, 2, 0, 0)

        with patch("tasks.sanctuary.sanctuary.get_server_next_update", return_value=expected):
            success = task.run_monthly_task()

        self.assertFalse(success)
        self.assertEqual(task.config.delayed_to, expected)
