"""Fast and background batches keep separate, crash-safe daily allowances."""

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from module.config.deep import deep_get
from tasks.base.resource_scheduling import RESOURCE_STATE_PATH
from tasks.dungeon.dungeon import Combat
from tasks.mission_reward.planning import DailyBudget, acknowledge_daily_review
from tests.support.daily_planning import DailyPlanningCase


class CombatDailyBatchTests(DailyPlanningCase, unittest.TestCase):
    def test_exhausted_fast_quota_does_not_lower_repeat_resource_gate(self):
        self.snapshot(points=100)
        self.config.Combat_RepeatCombatPrioritizeStamina = True
        task = self.task(fast=0, repeat=1)

        def delay(**kwargs):
            from datetime import timedelta

            self.set("Combat.Scheduler.NextRun", self.day + timedelta(hours=2))

        self.config.task_delay.side_effect = delay
        self.assertTrue(task._run_daily_combat_batch(task._daily_combat_budget, 0))
        waits = deep_get(self.data, RESOURCE_STATE_PATH)["servers"]["CN-Official"]["waits"]
        self.assertEqual(waits["Combat"]["need"], 80)
        task._run_fast_combat.assert_not_called()
        task._run_repeat_combat.assert_not_called()

    def test_claimed_goal_and_exhausted_quotas_do_not_record_resource_wait(self):
        self.snapshot(points=100)
        task = self.task(fast=0, repeat=0)
        self.assertTrue(task._run_daily_combat_batch(task._daily_combat_budget, 0))
        self.config.task_delay.assert_called_once_with(server_update=True)
        state = deep_get(self.data, RESOURCE_STATE_PATH)["servers"]["CN-Official"]
        self.assertNotIn("Combat", state["waits"])

    def test_real_fast_prepare_uses_explicit_batch_cap_without_rewriting_total(self):
        task = self.task()
        task._handle_dungeon_additional = lambda: False
        task._is_prepare_page = lambda: True
        task._is_fast_combat_locked = lambda: False
        task._ensure_fast_combat_state = lambda **kwargs: True
        task._ocr_fast_combat_remaining_times = lambda: 10
        task._set_prepare_count = Mock(return_value=True)
        self.assertEqual(Combat._prepare_fast_combat(task, 300, count_limit=3), ("ready", 3))
        self.assertEqual(task._set_prepare_count.call_args.args[0], 3)
        self.assertEqual(self.config.Combat_FastCombatCount, 5)

    def task(self, fast=5, repeat=1):
        task = object.__new__(Combat)
        task.config = self.config
        task.device = SimpleNamespace(image=object(), app_is_running=lambda: True)
        task._combat_should_use_fast = lambda: self.config.Combat_FastCombat
        task._leave_to_main = Mock(return_value=True)
        task._run_fast_combat = Mock(return_value=True)
        task._run_repeat_combat = Mock(return_value=True)
        task._delay_running_repeat_combat = Mock()
        task._prepare_fast_combat = Mock(side_effect=lambda **kw: ("ready", kw["count_limit"]))

        def prepare_repeat(**kwargs):
            task._repeat_combat_prepared_leif_count = kwargs["leif_count"]
            return True

        task._prepare_repeat_combat = Mock(side_effect=prepare_repeat)
        task._combat_runtime_build = Mock(return_value={"active": True, "mode": "repeat_server"})
        task._daily_combat_budget = DailyBudget(self.config, "Combat", {"fast": fast, "repeat": repeat})
        return task

    def test_fast_batch_stops_at_next_stamina_milestone_and_preserves_repeat(self):
        task = self.task()
        budget = task._daily_combat_budget
        self.assertTrue(task._run_daily_combat_batch(budget, 300))
        self.assertEqual(task._prepare_fast_combat.call_args.kwargs["count_limit"], 3)
        self.assertEqual((budget.remaining("fast"), budget.remaining("repeat")), (2, 1))
        task._prepare_repeat_combat.assert_not_called()
        self.assertTrue(budget.awaiting_review)
        self.config.task_call.assert_any_call("MissionReward", force_call=False)

    def test_claimed_goal_resumes_both_original_allowances(self):
        task = self.task()
        budget = task._daily_combat_budget
        task._run_daily_combat_batch(budget, 300)
        self.snapshot(points=100, stamina=60)
        acknowledge_daily_review(self.config)
        self.assertTrue(task._run_daily_combat_batch(budget, 240))
        self.assertEqual(budget.remaining("fast"), 0)
        self.assertFalse(budget.awaiting_review)
        self.assertTrue(task._run_daily_combat_batch(budget, 200, leif=1))
        self.assertEqual(budget.remaining("repeat"), 0)
        self.assertEqual(budget.pending["kind"], "repeat")
        self.assertEqual(task._prepare_repeat_combat.call_args.kwargs["prioritize_stamina"], None)

    def test_repeat_launch_intent_is_saved_before_consumption(self):
        self.config.Combat_FastCombat = False
        task = self.task(fast=0)
        budget = task._daily_combat_budget
        observed = deepcopy(deep_get(self.data, "MissionReward.MissionReward.DailyPlan"))

        def launch(**kwargs):
            session = deep_get(self.data, "Combat.CombatRuntime.Session")
            self.assertEqual(session["daily_reservation"], budget.pending)
            self.assertTrue(session["active"])
            return True

        task._run_repeat_combat.side_effect = launch
        self.assertTrue(task._run_daily_combat_batch(budget, 160, leif=1))
        self.assertIsNotNone(budget.pending)
        # The 80-stamina unit is a reserved allowance, not earned daily
        # progress. The game credits consumption only when rewards are settled.
        self.assertEqual(deep_get(self.data, "MissionReward.MissionReward.DailyPlan"), observed)
        self.assertFalse(budget.awaiting_review)
        self.config.task_call.assert_not_called()
        session = deep_get(self.data, "Combat.CombatRuntime.Session")
        task._settle_daily_repeat_budget(session)
        task._settle_daily_repeat_budget(session)
        self.assertIsNone(budget.pending)
        self.assertEqual(budget.remaining("repeat"), 0)
        self.assertEqual(deep_get(self.data, "MissionReward.MissionReward.DailyPlan"), observed)
        self.assertTrue(budget.awaiting_review)

    def test_lost_or_manually_adopted_repeat_does_not_confirm_reserved_batch(self):
        self.config.Combat_FastCombat = False
        task = self.task(fast=0)
        budget = task._daily_combat_budget
        task._run_daily_combat_batch(budget, 160, leif=1)
        task._settle_daily_repeat_budget({"active": True, "source": "detected_existing"})
        self.assertIsNotNone(budget.pending)
        self.assertFalse(task._run_daily_combat_batch(budget, 160))
        self.assertEqual(task._run_repeat_combat.call_count, 1)

    def test_failed_fast_settlement_is_never_recharged_or_replayed(self):
        task = self.task()
        budget = task._daily_combat_budget
        task._run_fast_combat.return_value = False
        self.assertFalse(task._run_daily_combat_batch(budget, 300))
        self.assertEqual(budget.remaining("fast"), 2)
        self.assertFalse(task._run_daily_combat_batch(budget, 300))
        self.assertEqual(task._run_fast_combat.call_count, 1)

    def test_extra_repeat_uses_owned_stamina_and_no_extra_leaf_allowance(self):
        self.set("Arena.Scheduler.Enable", False)
        self.config.Combat_FastCombat = False
        task = self.task(fast=0)
        budget = task._daily_combat_budget
        task._run_daily_combat_batch(budget, 160, leif=1)
        task._settle_daily_repeat_budget(deep_get(self.data, "Combat.CombatRuntime.Session"))
        self.snapshot(points=75, stamina=80)
        acknowledge_daily_review(self.config)
        self.assertTrue(task._run_daily_combat_batch(budget, 80))
        self.assertEqual(task._prepare_repeat_combat.call_args.kwargs["leif_count"], 1)
        self.assertTrue(task._prepare_repeat_combat.call_args.kwargs["prioritize_stamina"])
        self.assertTrue(budget.pending["extra"])
        self.assertFalse(self.config.Combat_RepeatCombatPrioritizeStamina)
        self.assertEqual(self.config.Combat_RepeatCombatLeifCount, 1)

    def test_insufficient_owned_stamina_never_starts_an_extra_leaf_batch(self):
        self.set("Arena.Scheduler.Enable", False)
        self.config.Combat_FastCombat = False
        task = self.task(fast=0, repeat=0)
        self.assertTrue(task._run_daily_combat_batch(task._daily_combat_budget, 79))
        task._prepare_repeat_combat.assert_not_called()
        task._run_repeat_combat.assert_not_called()

    def test_no_complete_daily_snapshot_schedules_review_without_starting(self):
        self.plan["missions"].pop()
        task = self.task()
        self.assertTrue(task._run_daily_combat_batch(task._daily_combat_budget, 300))
        task._prepare_fast_combat.assert_not_called()
        task._prepare_repeat_combat.assert_not_called()
        self.config.task_call.assert_any_call("MissionReward", force_call=False)

    def test_outer_run_confirms_matching_repeat_only_after_finished_status(self):
        self.config.Combat_FastCombat = False
        self.set("Combat.Combat.FastCombat", False)
        task = self.task(fast=0)
        task._run_daily_combat_batch(task._daily_combat_budget, 160, leif=1)
        observed = deepcopy(deep_get(self.data, "MissionReward.MissionReward.DailyPlan"))
        pending = task._daily_combat_budget.pending
        task._rune_balance_enabled = lambda: False
        task._prepare_background_repeat_check_context = Mock()
        task._adopt_existing_background_repeat_combat = Mock()
        task._is_repeat_result_window = lambda: False
        task.is_in_main = lambda **kwargs: True
        task._watch_repeat_combat = Mock(side_effect=["running", "finished"])
        task._urgent_tasks_enabled = lambda: False
        task._rune_balance_after_settled = Mock()
        self.assertTrue(task.run())
        self.assertEqual(task._daily_combat_budget.pending, pending)
        self.assertTrue(task._combat_runtime_active())
        self.assertFalse(task._daily_combat_budget.awaiting_review)
        self.assertEqual(deep_get(self.data, "MissionReward.MissionReward.DailyPlan"), observed)
        self.config.task_call.assert_not_called()
        # Even a fresh daily read while the run is pending must not settle its
        # reservation or start a replacement because stamina progress is stale.
        acknowledge_daily_review(self.config)
        self.assertEqual(task._daily_combat_budget.pending, pending)
        self.assertEqual(task._daily_combat_budget.choose("repeat", 2, 80).reason, "uncertain")
        self.assertEqual(task._run_repeat_combat.call_count, 1)

        self.assertTrue(task.run())
        self.assertIsNone(task._daily_combat_budget.pending)
        self.assertFalse(task._combat_runtime_active())
        self.assertTrue(task._daily_combat_budget.awaiting_review)
        self.config.task_call.assert_any_call("MissionReward", force_call=False)
        # A verified settlement permits a new daily observation; it still must
        # not synthesize 80 stamina or its activity reward in the saved plan.
        self.assertEqual(deep_get(self.data, "MissionReward.MissionReward.DailyPlan"), observed)
        self.assertEqual(task._daily_combat_budget.choose("repeat", 2, 80).reason, "review")
        self.assertEqual(task._run_repeat_combat.call_count, 1)

    def test_burnout_fast_completion_reviews_daily_while_repeat_keeps_running(self):
        self.config.Combat_BurnoutMode = "Burnout"
        self.set("Combat.Combat.BurnoutMode", "Burnout")
        observed = deepcopy(deep_get(self.data, "MissionReward.MissionReward.DailyPlan"))
        for status, count in (("ready", 3), ("no_stamina", 0), ("fallback", 0)):
            with self.subTest(fast_prepare=status):
                self.set("Combat.CombatRuntime.Session", {})
                self.config.task_call.reset_mock()
                events = []
                task = self.task()
                task._prepare_background_repeat_check_context = Mock()
                task._adopt_existing_background_repeat_combat = Mock()
                task._urgent_tasks_enabled = lambda: False
                task._is_in_dungeon_context = lambda: True
                task._dungeon_navigate = Mock(return_value=True)
                task._update_prepare_resource_snapshot = Mock(return_value={
                    "stamina": SimpleNamespace(value=300),
                })
                task._prepare_fast_combat = Mock(return_value=(status, count))
                task._run_repeat_combat.side_effect = lambda events=events, **kwargs: events.append("repeat") or True
                task._run_fast_combat.side_effect = lambda events=events, **kwargs: events.append("fast") or True
                task._leave_to_main.side_effect = lambda events=events, **kwargs: events.append("main") or True

                self.assertTrue(task.run())
                self.assertFalse(task._daily_combat_budget.active)
                task._prepare_repeat_combat.assert_called_once_with(
                    leif_count=3, skip_first_screenshot=True,
                )
                task._prepare_fast_combat.assert_called_once_with(
                    stamina=60, use_max=False, skip_first_screenshot=True,
                )
                self.assertEqual(events, ["repeat", "fast", "main"] if count else ["repeat", "main"])
                self.assertTrue(task._combat_runtime_active())
                task._delay_running_repeat_combat.assert_called_once()
                if count:
                    self.config.task_call.assert_any_call("MissionReward", force_call=False)
                else:
                    self.config.task_call.assert_not_called()
                    task._run_fast_combat.assert_not_called()
                # A completed fast batch permits observation while the repeat
                # remains active. Neither action fabricates daily consumption
                # or activity in the previously observed mission snapshot.
                self.assertEqual(deep_get(self.data, "MissionReward.MissionReward.DailyPlan"), observed)

    def test_empty_repeat_resources_release_other_executor_from_waiting(self):
        self.config.Combat_FastCombat = False
        self.set("Combat.Combat.FastCombat", False)
        task = self.task(fast=0)
        budget = task._daily_combat_budget
        self.assertTrue(task._run_daily_combat_batch(budget, 0, leif=0))
        task._prepare_repeat_combat.assert_not_called()
        self.assertIsNone(budget.pending)
        self.assertEqual(budget.remaining("repeat"), 1)
        arena = DailyBudget(self.config, "Arena", {"arena": 5})
        arena.choose("arena", 5)
        arena.reserve("arena", 5)
        arena.confirm()
        self.snapshot(points=85, arena=5)
        self.assertTrue(arena.choose("arena", 5).extra)

    def test_original_repeat_can_use_owned_stamina_when_user_enabled_it(self):
        self.config.Combat_FastCombat = False
        self.config.Combat_RepeatCombatPrioritizeStamina = True
        task = self.task(fast=0, repeat=2)
        self.assertTrue(task._run_daily_combat_batch(task._daily_combat_budget, 80, leif=0))
        self.assertEqual(task._prepare_repeat_combat.call_args.kwargs["leif_count"], 1)
        self.assertEqual(task._daily_combat_budget.remaining("repeat"), 1)
