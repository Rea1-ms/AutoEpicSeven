import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from module.exception import RequestHumanTakeover
from tasks.equipment_reroll.equipment_reroll import EquipmentReroll
from tasks.equipment_reroll.rules import RefreshBudget
from tests.equipment_reroll.test_rules import policy, snapshot, stats
from tests.support.equipment_reroll import ReplayClock, ReplayDevice


class ReplayTests(unittest.TestCase):
    def replay(self, frames, budget=None):
        self.clock = ReplayClock()
        self.device = ReplayDevice(frames, self.clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task._is_ready = lambda: isinstance(self.device.image, type(snapshot()))
        task.read_snapshot = lambda: self.device.image
        task.appear = lambda button, **kwargs: isinstance(self.device.image, dict) and button.name in (
            "EQUIPMENT_REROLL_REPLACE_CHECK", "EQUIPMENT_REROLL_REPLACE_CONFIRM",
        )

        def read_replacement():
            frame = self.device.image
            if frame.get("unreadable"):
                raise ValueError("替换弹窗识别置信度不足")
            return frame["stats"]

        task.read_replacement = read_replacement
        task.handle_network_error = lambda: False
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", self.clock):
            return task.execute(policy(), budget or RefreshBudget(0, 0, 0))

    def test_speed_five_on_last_roll_is_replaced_before_stopping(self):
        before = snapshot(points=20)
        result = replace(before, candidate=stats(5), points=0)
        accepted = replace(result, current=result.candidate)
        b = RefreshBudget(1, 20, 0)
        reason = self.replay([before, before, before, before, result, result, accepted, accepted], b)
        self.assertEqual(reason, "达到最大刷新次数")
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH", "EQUIPMENT_REROLL_REPLACE"])
        self.assertEqual((b.refreshes, b.spent), (1, 20))

    def test_changed_candidate_waits_for_delayed_payment(self):
        before = snapshot(points=20)
        changed = replace(before, candidate=stats(2))
        paid = replace(changed, points=0)
        b = RefreshBudget(1, 0, 0)
        self.replay([before, before, changed, changed, changed, changed, paid, paid], b)
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual(b.refreshes, 1)

    def test_identical_result_still_counts_exact_payment(self):
        before = snapshot(points=20)
        paid = replace(before, points=0)
        b = RefreshBudget(1, 0, 0)
        self.replay([before, before, before, before, paid, paid], b)
        self.assertEqual((b.refreshes, b.spent), (1, 20))
        self.assertEqual(len(self.device.actions), 1)

    def test_unconfirmed_paid_click_is_never_repeated(self):
        before = snapshot()
        with self.assertRaises(RequestHumanTakeover):
            self.replay([before] * 24)
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual(self.device.click_history_clears, 0)
        self.assertEqual(self.device.stuck_history_clears, 0)

    def test_many_confirmed_rolls_keep_framework_protection_without_false_stuck(self):
        before = snapshot(points=1000)
        frames = [before, before]
        for index in range(1, 26):
            paid = replace(before, points=1000 - index * 20)
            frames.extend([paid, paid])
        budget = RefreshBudget(25, 0, 0)
        self.replay(frames, budget)
        self.assertEqual(budget.refreshes, 25)
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"] * 25)
        self.assertEqual(self.device.click_history_clears, 25)
        self.assertEqual(self.device.stuck_history_clears, 25)

    def test_failed_replace_retries_and_never_refreshes_old_result(self):
        before = snapshot(candidate=stats(5), points=0)
        accepted = replace(before, current=before.candidate)
        self.replay([before] * 5 + [accepted, accepted])
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REPLACE"] * 2)

    def test_stale_locks_are_removed_before_speed_search(self):
        before = snapshot(locked=(False, True, False, False), points=60)
        unlocked = replace(before, locked=(False,) * 4, cost=20)
        paid = replace(unlocked, points=40)
        self.replay([before, before, before, unlocked, unlocked, paid, paid],
                    RefreshBudget(1, 0, 0))
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_LOCK_1", "EQUIPMENT_REROLL_REFRESH"])

    def test_unlocking_can_make_an_initially_expensive_roll_affordable(self):
        before = snapshot(locked=(False, True, False, False), points=20)
        unlocked = replace(before, locked=(False,) * 4, cost=20)
        paid = replace(unlocked, points=0)
        self.replay([before, before, unlocked, unlocked, paid, paid], RefreshBudget(1, 20, 0))
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_LOCK_1", "EQUIPMENT_REROLL_REFRESH"])

    def test_speed_and_achieved_defense_are_locked_before_refresh(self):
        before = snapshot(current=stats(5, 8), candidate=stats(5, 8), points=150)
        first = replace(before, locked=(True, False, False, False), cost=60)
        second = replace(first, locked=(True, True, False, False), cost=150)
        paid = replace(second, points=0)
        self.replay([before, before, first, first, second, second, paid, paid], RefreshBudget(1, 0, 0))
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_LOCK_0", "EQUIPMENT_REROLL_LOCK_1",
                                              "EQUIPMENT_REROLL_REFRESH"])

    def test_complete_current_roll_has_no_actions(self):
        complete = snapshot(current=stats(5, 8, 8, 8))
        self.assertEqual(self.replay([complete, complete]), "四条副属性均已达标")
        self.assertEqual(self.device.actions, [])

    def test_budget_blocks_refresh_and_accepts_existing_improvement(self):
        before = snapshot(points=1000)
        self.replay([before, before], RefreshBudget(0, 19, 0))
        self.assertEqual(self.device.actions, [])

    def test_impossible_main_stat_target_has_no_actions(self):
        before = replace(snapshot(), main=stats()[1])
        with self.assertRaises(RequestHumanTakeover):
            self.replay([before, before])
        self.assertEqual(self.device.actions, [])

    def test_wrong_payment_stops_without_second_refresh(self):
        before = snapshot()
        wrong = replace(before, points=950)
        with self.assertRaises(RequestHumanTakeover):
            self.replay([before, before, wrong, wrong])
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])

    def test_apply_waits_for_actual_single_column_result(self):
        before = snapshot(candidate=stats(5), points=0)
        popup = {"main": before.main, "stats": before.candidate}
        accepted = replace(before, current=before.candidate, candidate=())
        self.replay([before, before, popup, popup, None, None, accepted, accepted])
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REPLACE", "EQUIPMENT_REROLL_REPLACE_CONFIRM"])
        self.assertEqual(self.device.action_times, [1, 3])

    def test_mismatched_popup_is_not_applied(self):
        before = snapshot(candidate=stats(5), points=0)
        popup = {"main": before.main, "stats": stats(4)}
        with self.assertRaisesRegex(RequestHumanTakeover, "不一致"):
            self.replay([before, before, popup])
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REPLACE"])

    def test_unexpected_popup_is_not_applied(self):
        before = snapshot()
        popup = {"main": before.main, "stats": before.candidate}
        for frames in ([popup], [before, before, popup]):
            with self.subTest(frames=frames), self.assertRaisesRegex(RequestHumanTakeover, "未由本次工具"):
                self.replay(frames)
            self.assertNotIn("EQUIPMENT_REROLL_REPLACE_CONFIRM", self.device.actions)

    def test_unreadable_popup_never_clicks_apply(self):
        before = snapshot(candidate=stats(5), points=0)
        with self.assertRaises(RequestHumanTakeover):
            self.replay([before, before] + [{"unreadable": True}] * 21)
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REPLACE"])

    def test_apply_retries_are_bounded_and_never_refresh(self):
        before = snapshot(candidate=stats(5), points=0)
        popup = {"main": before.main, "stats": before.candidate}
        with self.assertRaises(RequestHumanTakeover):
            self.replay([before, before] + [popup] * 21)
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REPLACE"]
                         + ["EQUIPMENT_REROLL_REPLACE_CONFIRM"] * 3)
        self.assertEqual(self.device.action_times, [1, 3, 5, 7])

    def test_centered_view_locks_at_new_position_before_refresh(self):
        before = snapshot(current=stats(5, 8), candidate=(), points=150)
        first = replace(before, locked=(True, False, False, False), cost=60)
        second = replace(first, locked=(True, True, False, False), cost=150)
        paid = replace(second, candidate=second.current, points=0)
        self.replay([before, before, first, first, second, second, paid, paid], RefreshBudget(1, 0, 0))
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_LOCK_0", "EQUIPMENT_REROLL_LOCK_1",
                                              "EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual(self.device.click_areas[:2], [(342, 228, 371, 256), (342, 275, 371, 303)])

    def test_centered_paid_view_waits_for_candidate_to_return(self):
        before = snapshot(candidate=(), points=20)
        deducted = replace(before, points=0)
        result = replace(deducted, candidate=before.current)
        budget = RefreshBudget(1, 0, 0)
        self.replay([before, before, deducted, deducted, result, result], budget)
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual((budget.refreshes, budget.spent), (1, 20))
