"""Persistent allowances and conditional additions from observed missions."""

import unittest
from datetime import timedelta
from unittest.mock import patch

from tasks.base.resource_scheduling import RESOURCE_STATE_PATH, ResourceCoordinator
from tasks.mission_reward.planning import (
    DailyBudget,
    acknowledge_daily_review,
)
from tests.support.daily_planning import DailyPlanningCase
from tests.support.resources import resource_values


class DailyPlanningTests(DailyPlanningCase, unittest.TestCase):
    def consumption(self, *, burnout=False, fast=5, repeat=1):
        budget = self.arena()
        root, _ = budget._load()
        state = root["contexts"][budget._key]["executors"].get("Combat", {})
        return budget._future_consumption("Combat", state, {"fast": fast, "repeat": repeat}, burnout)

    def observe(self, **values):
        ResourceCoordinator(self.config).observe(resource_values(**values), "test-refill")

    def test_burnout_without_resources_before_reset_cannot_complete_daily(self):
        self.set("Combat.Combat.BurnoutMode", "Burnout")
        self.observe(stamina=(0, 336))
        with patch("tasks.mission_reward.planning.get_server_next_update",
                   return_value=self.day + timedelta(hours=1, seconds=30)):
            self.assertEqual(self.consumption(burnout=True), 0)
            budget = self.arena()
            self.spend(budget, "arena", 5)
            self.snapshot(points=85, arena=5)
            self.assertTrue(budget.choose("arena", 5).extra)

    def test_fast_and_repeat_allocate_one_shared_stamina_balance(self):
        self.set("Combat.Combat.RepeatCombatPrioritizeStamina", True)
        self.observe(stamina=100, leif=0)
        self.assertEqual(self.consumption(fast=5, repeat=1), 100)
        self.assertEqual(self.consumption(burnout=True), 100)

    def test_refill_revision_invalidates_previous_zero_capacity(self):
        self.observe(stamina=0, leif=0)
        combat = DailyBudget(self.config, "Combat", {"fast": 5, "repeat": 0})
        combat.choose("fast", 0, 20)
        self.assertEqual(self.consumption(repeat=0), 0)
        self.observe(stamina=100)
        self.assertEqual(self.consumption(repeat=0), 100)

    def test_unknown_and_expired_resources_cannot_be_inferred_from_gifts_or_store(self):
        self.set("Store.Store.ArenaFlag", 5)
        self.set("PetsGift.Scheduler.Enable", True)
        self.set(RESOURCE_STATE_PATH, {})
        self.assertEqual(self.consumption(burnout=True), 0)
        self.observe(stamina=300)
        with patch("tasks.base.resource_scheduling.local_now",
                   return_value=self.day + timedelta(hours=1, seconds=301)):
            self.assertEqual(self.consumption(burnout=True), 0)

    def test_settings_edit_invalidates_capacity_without_inventing_leaf_balance(self):
        self.observe(stamina=100)
        ResourceCoordinator(self.config).invalidate("leif")
        combat = DailyBudget(self.config, "Combat", {"fast": 5, "repeat": 1})
        combat.observe_capacity("fast", 0)
        self.assertEqual(self.consumption(), 0)
        self.set("Combat.Combat.HuntGrade", "High")
        self.assertEqual(self.consumption(), 90)

    def test_natural_recovery_is_limited_by_time_and_recovery_cap(self):
        self.set("Combat.Combat.RepeatCombatPrioritizeStamina", True)
        self.observe(stamina=(0, 100), leif=0)
        with patch("tasks.mission_reward.planning.get_server_next_update",
                   return_value=self.day + timedelta(hours=1, minutes=40)):
            self.assertEqual(self.consumption(burnout=True), 0)
        self.assertEqual(self.consumption(burnout=True), 100)

    def test_cn_and_overseas_chinese_share_conditional_addition_policy(self):
        self.set("Combat.Scheduler.Enable", False)
        for package in ("com.zlongame.cn.epicseven", "com.stove.epic7.google"):
            with self.subTest(package=package):
                self.config.Emulator_PackageName = package
                self.snapshot()
                budget = self.arena()
                self.spend(budget, "arena", 5)
                self.snapshot(points=85, arena=5)
                decision = budget.choose("arena", 5)
                self.assertEqual((decision.count, decision.extra), (5, True))

    def arena(self, limit=5):
        return DailyBudget(self.config, "Arena", {"arena": limit})

    def spend(self, budget, kind, count, cost=0):
        decision = budget.choose(kind, count, cost)
        self.assertEqual(decision.reason, "run")
        budget.reserve(kind, count, extra=decision.extra)
        budget.confirm()

    def test_arena_uses_only_two_rounds_to_reach_next_milestone(self):
        self.snapshot(arena=3)
        budget = self.arena(10)
        self.assertEqual(budget.choose("arena", 10).count, 2)
        self.spend(budget, "arena", 2)
        self.assertEqual(self.arena(10).remaining("arena"), 8)

    def test_exhausted_original_waits_for_other_scheduled_allowances(self):
        budget = self.arena()
        self.spend(budget, "arena", 5)
        self.snapshot(points=85, arena=5)
        self.assertEqual(budget.choose("arena", 5).reason, "wait")

    def test_adds_only_next_milestone_if_other_tasks_cannot_finish(self):
        self.set("Combat.Scheduler.Enable", False)
        budget = self.arena()
        self.spend(budget, "arena", 5)
        self.snapshot(points=85, arena=5)
        decision = budget.choose("arena", 8)
        self.assertEqual((decision.count, decision.extra), (5, True))
        budget.reserve("arena", 5, extra=True)
        budget.confirm()
        self.assertEqual(budget.remaining("arena"), 0)

    def test_free_task_before_reset_can_replace_an_additional_batch(self):
        self.set("Combat.Scheduler.Enable", False)
        self.set("Gacha.Scheduler.Enable", True)
        self.set("Gacha.Scheduler.NextRun", self.day + timedelta(hours=5))
        budget = self.arena()
        self.spend(budget, "arena", 5)
        self.snapshot(points=85, arena=5)
        # Select by key rather than relying on recognition order.
        for row in self.plan["missions"]:
            if row["key"] == "gacha":
                row["current"] = 0
        self.assertEqual(budget.choose("arena", 5).reason, "wait")
        self.set("Gacha.Scheduler.NextRun", self.day + timedelta(days=1))
        self.assertTrue(budget.choose("arena", 5).extra)

    def test_observed_resource_exhaustion_reconsiders_waiting(self):
        arena = self.arena()
        self.spend(arena, "arena", 5)
        self.snapshot(points=85, arena=5)
        self.assertEqual(arena.choose("arena", 5).reason, "wait")
        combat = DailyBudget(self.config, "Combat", {"fast": 5, "repeat": 1})
        combat.choose("fast", 0, 20)
        combat.choose("repeat", 0, 80)
        self.assertTrue(arena.choose("arena", 5).extra)

    def test_pending_is_not_refunded_or_repeated_after_restart(self):
        budget = self.arena()
        budget.choose("arena", 5)
        budget.reserve("arena", 1)
        restored = self.arena()
        self.assertEqual(restored.remaining("arena"), 4)
        self.assertEqual(restored.choose("arena", 5).reason, "uncertain")
        with self.assertRaises(ValueError):
            restored.reserve("arena", 1)
        restored.confirm()
        restored.confirm()
        self.assertEqual(restored.remaining("arena"), 4)

    def test_scan_acknowledgement_never_confirms_a_pending_payment(self):
        budget = self.arena()
        budget.choose("arena", 5)
        budget.reserve("arena", 1)
        budget.request_review()
        acknowledge_daily_review(self.config)
        self.assertFalse(budget.awaiting_review)
        self.assertIsNotNone(budget.pending)

    def test_partial_scan_does_not_release_review_wait(self):
        budget = self.arena()
        budget.request_review()
        self.plan["missions"].pop()
        acknowledge_daily_review(self.config)
        self.assertTrue(budget.awaiting_review)
        self.assertEqual(budget.choose("arena", 5).reason, "review")

    def test_completed_day_resumes_original_remaining_without_additions(self):
        budget = self.arena(10)
        self.spend(budget, "arena", 5)
        budget.request_review()
        self.snapshot(points=100)
        self.plan["missions"] = []
        acknowledge_daily_review(self.config)
        self.assertEqual(budget.choose("arena", 10).count, 5)
        self.spend(budget, "arena", 5)
        self.assertEqual(budget.choose("arena", 10).reason, "complete")

    def test_full_points_without_claim_confirmation_requires_review(self):
        self.snapshot(points=100, claimed=False)
        self.assertEqual(self.arena().choose("arena", 5).reason, "review")

    def test_language_change_preserves_used_allowance_but_requires_new_scan(self):
        self.config.Emulator_PackageName = "com.stove.epic7.google"
        self.snapshot()
        self.spend(self.arena(), "arena", 2)
        self.config.Emulator_GameLanguage = "global_cn"
        self.assertEqual(self.arena().remaining("arena"), 3)
        self.config.Emulator_GameLanguage = "en"
        self.assertFalse(self.arena().active)

    def test_pending_survives_reset_and_other_server_has_its_own_allowance(self):
        budget = self.arena()
        budget.choose("arena", 5)
        budget.reserve("arena", 1)
        self.day += timedelta(days=1)
        self.snapshot()
        self.assertEqual(self.arena().choose("arena", 5).reason, "uncertain")
        self.config.Emulator_PackageName = "com.stove.epic7.google"
        self.snapshot()
        self.assertEqual(self.arena().choose("arena", 5).count, 5)

    def test_editing_limits_does_not_restore_consumed_allowance(self):
        self.spend(self.arena(), "arena", 3)
        self.assertEqual(self.arena(4).remaining("arena"), 1)
        self.assertEqual(self.arena(2).remaining("arena"), 0)

    def test_zero_affordability_never_authorizes_addition(self):
        self.set("Combat.Scheduler.Enable", False)
        budget = self.arena()
        self.spend(budget, "arena", 5)
        self.snapshot(points=85, arena=5)
        self.assertEqual(budget.choose("arena", 0).count, 0)
        with self.assertRaises(ValueError):
            budget.reserve("arena", 1, extra=True)

    def test_core_missions_never_create_a_dedicated_consumption_route(self):
        budget = DailyBudget(self.config, "Combat", {"fast": 0, "repeat": 0})
        self.snapshot(points=85, stamina=150)
        self.assertEqual(budget.choose("fast", 5, 20).count, 0)
        self.assertEqual(budget.choose("repeat", 5, 80).count, 0)

    def test_burnout_disabled_priority_and_dimensional_keep_original_flow(self):
        for path, value in (
            ("Combat.Combat.BurnoutMode", "Burnout"),
            ("MissionReward.MissionReward.DailyPriority", False),
            ("Combat.Combat.HuntBoss", "Ogre"),
        ):
            with self.subTest(path=path):
                self.set(path, value)
                budget = DailyBudget(self.config, "Combat", {"fast": 5, "repeat": 1})
                self.assertFalse(budget.active)
                self.set(path, "Daily" if path.endswith("BurnoutMode") else
                         "Wyvern" if path.endswith("HuntBoss") else True)
