"""Daily arena batches preserve allowance across rechecks and failures."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tasks.arena.arena import Arena
from tasks.base.resource_scheduling import ResourceCoordinator
from tasks.mission_reward.planning import DailyBudget, acknowledge_daily_review
from tests.support.arena import ArenaReplay
from tests.support.daily_planning import DailyPlanningCase
from tests.support.interaction import scene
from tests.support.offline import ControlledClock
from tests.support.resources import resource_values


class ArenaDailyBatchTests(DailyPlanningCase, unittest.TestCase):
    def test_real_round_loop_reserves_once_for_retries_and_repeated_result(self):
        with ControlledClock() as clock:
            prepare = scene("BATTLE_START", "FAST_BATTLE_ON", flags=(5, 5))
            replay = ArenaReplay([
                prepare, prepare,
                scene("BATTLE_START", "FAST_BATTLE_ON", flags=(5, 5), seconds=7),
                *[scene("FAST_BATTLE_RESULT_CONFIRM", flags=(5, 5))] * 8,
                scene(home_ready=True, flags=(5, 5)),
            ], clock)
            self.config.Arena_NPCCombatCount = 1
            self.set("Arena.Arena.NPCCombatCount", 1)
            replay.task.config = self.config
            self.snapshot(arena=4)
            self.assertTrue(replay.task._run_npc_combat())
            self.assertEqual(replay.clicks("BATTLE_START"), [1, 2])
            self.assertGreaterEqual(len(replay.clicks("FAST_BATTLE_RESULT_CONFIRM")), 2)
            self.assertEqual(replay.task._arena_npc_completed_rounds, 1)
            self.assertEqual(replay.task._arena_daily_budget.remaining("arena"), 0)
            self.assertIsNone(replay.task._arena_daily_budget.pending)

    def task(self):
        task = object.__new__(Arena)
        task.config = self.config
        task.device = SimpleNamespace(
            image=object(), app_is_running=lambda: True,
            click_record_clear=Mock(), click=Mock(),
        )
        task._is_npc_combat_context = lambda: True
        task._update_arena_dashboard_snapshot = Mock()
        task._claim_weekly_battle_rewards = Mock()
        task._claim_battle_pass_rewards = Mock()
        task._arena_delay_after_run = Mock()
        task._stored_arena_flag_status = lambda: (20, 5)
        task._consume_stored_arena_flags = Mock()
        task.appear = Mock(return_value=True)

        def round_once(**kwargs):
            self.assertTrue(task._npc_click_battle_start())
            return "completed"

        task._npc_combat_once = Mock(side_effect=round_once)
        return task

    def test_ten_fixed_rounds_split_into_five_and_five_without_restarting_total(self):
        self.config.Arena_NPCCombatCount = 10
        self.set("Arena.Arena.NPCCombatCount", 10)
        task = self.task()
        self.assertTrue(task.run())
        self.assertEqual(task._npc_combat_once.call_count, 5)
        self.snapshot(points=85, arena=5)
        acknowledge_daily_review(self.config)
        # The task double does not navigate home or read its post-battle HUD.
        # Publish that observation explicitly before planning another route.
        ResourceCoordinator(self.config).observe(resource_values(arena_flag=(15, 5)), "test-home")
        other = DailyBudget(self.config, "Combat", {"fast": 0, "repeat": 0})
        self.assertEqual(other.choose("fast", 5, 20).reason, "wait")
        self.snapshot(points=100, arena=5)
        acknowledge_daily_review(self.config)
        self.assertTrue(task.run())
        self.assertEqual(task._npc_combat_once.call_count, 10)
        self.assertTrue(task.run())
        self.assertEqual(task._npc_combat_once.call_count, 10)
        self.assertEqual(self.config.Arena_NPCCombatCount, 10)
        self.assertEqual(task._consume_stored_arena_flags.call_count, 10)

    def test_start_retries_reserve_the_same_round_once(self):
        task = self.task()
        task._arena_daily_budget = DailyBudget(self.config, "Arena", {"arena": 5})
        task._arena_daily_round_reserved = False
        self.assertTrue(task._npc_click_battle_start())
        self.assertTrue(task._npc_click_battle_start())
        self.assertEqual(task.device.click.call_count, 2)
        self.assertEqual(task._arena_daily_budget.remaining("arena"), 4)
        self.assertEqual(task._arena_daily_budget.pending["count"], 1)

    def test_partial_failure_schedules_review_and_pending_round_is_not_replayed(self):
        task = self.task()
        completed = task._npc_combat_once.side_effect
        calls = 0

        def fail_third(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                task._npc_click_battle_start()
                return "failed"
            return completed(**kwargs)

        task._npc_combat_once.side_effect = fail_third
        self.assertFalse(task.run())
        self.assertEqual(task._arena_npc_completed_rounds, 2)
        self.assertEqual(task._arena_daily_budget.remaining("arena"), 2)
        self.config.task_call.assert_any_call("MissionReward", force_call=False)
        self.snapshot(points=55, arena=2)
        acknowledge_daily_review(self.config)
        self.assertTrue(task.run())
        self.assertEqual(task._npc_combat_once.call_count, 3)

    def test_wait_then_add_only_when_other_executor_is_no_longer_available(self):
        task = self.task()
        self.assertTrue(task.run())
        self.snapshot(points=85, arena=5)
        acknowledge_daily_review(self.config)
        self.assertTrue(task.run())
        self.assertEqual(task._npc_combat_once.call_count, 5)
        self.set("Combat.Scheduler.Enable", False)
        self.assertTrue(task.run())
        self.assertEqual(task._npc_combat_once.call_count, 10)
        self.assertEqual(self.config.Arena_NPCCombatCount, 5)

    def test_unknown_flags_do_not_authorize_consumption(self):
        task = self.task()
        task._stored_arena_flag_status = lambda: None
        self.assertTrue(task.run())
        task._npc_combat_once.assert_not_called()
