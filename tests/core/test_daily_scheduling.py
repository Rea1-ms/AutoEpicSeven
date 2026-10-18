import copy
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from module.config.deep import deep_get, deep_set
from tasks.mission_reward.scheduling import (
    DAILY_PLAN_PATH,
    _current_daily_plan,
    daily_priority_enabled,
    daily_snapshot_context,
    should_schedule_mission_reward,
)


class DailySchedulingTest(unittest.TestCase):
    DAY = datetime.fromisoformat("2026-10-15T03:00:00")

    def setUp(self):
        self.data = {}
        for name in ("MissionReward", "Gacha", "SanctuaryDaily", "Arena", "Combat"):
            deep_set(self.data, f"{name}.Scheduler.Enable", True)
        self.config = SimpleNamespace(
            Emulator_PackageName="com.zlongame.cn.epicseven",
            Emulator_GameLanguage="cn",
            Scheduler_ServerUpdate="03:00",
            stored=SimpleNamespace(DailyActivity=SimpleNamespace(
                value=100, total=100, time=self.DAY + timedelta(hours=1),
            )),
            cross_get=lambda keys, default=None: deep_get(self.data, keys, default),
        )
        self.patch = patch(
            "tasks.mission_reward.scheduling.get_server_last_update", return_value=self.DAY
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def plan(self, *keys, points=0, claimed=False, day=None):
        plan = {
            **daily_snapshot_context(self.config),
            "day": (day or self.DAY).isoformat(),
            "points": points,
            "missions": [
                {"key": key, "current": 0, "total": 1, "points": 10}
                for key in keys
            ],
            "rewards_claimed": claimed,
        }
        deep_set(self.data, DAILY_PLAN_PATH, plan)
        return plan

    def test_supported_servers_use_the_same_daily_executor(self):
        from tasks.mission_reward.daily_reward import DailyMissionReward
        from tasks.mission_reward.mission_reward import MissionReward
        from tests.support.daily_missions import task_type as cn_task_type
        from tests.support.global_daily_missions import task_type as global_task_type

        self.assertIs(cn_task_type(), DailyMissionReward)
        self.assertIs(global_task_type(), DailyMissionReward)
        device = object()
        for package in ("com.zlongame.cn.epicseven", "com.stove.epic7.google"):
            with self.subTest(package=package), patch(
                "tasks.mission_reward.daily_reward.DailyMissionReward"
            ) as daily, patch(
                "tasks.mission_reward.mission_reward.CurrentMissionReward"
            ) as current:
                self.config.Emulator_PackageName = package
                self.assertIs(
                    MissionReward(self.config, device=device, task="MissionReward"),
                    daily.return_value,
                )
                daily.assert_called_once_with(config=self.config, device=device, task="MissionReward")
                current.assert_not_called()

    def test_facade_keeps_claim_only_when_priority_is_disabled_or_language_unsupported(self):
        from tasks.mission_reward.mission_reward import MissionReward

        cases = (
            ("com.zlongame.cn.epicseven", "cn", False),
            ("com.stove.epic7.google", "cn", False),
            ("com.stove.epic7.google", "en", True),
        )
        device = object()
        for package, language, enabled in cases:
            with self.subTest(package=package, language=language, enabled=enabled), patch(
                "tasks.mission_reward.daily_reward.DailyMissionReward"
            ) as daily, patch(
                "tasks.mission_reward.mission_reward.CurrentMissionReward"
            ) as current:
                self.config.Emulator_PackageName = package
                self.config.Emulator_GameLanguage = language
                deep_set(self.data, "MissionReward.MissionReward.DailyPriority", enabled)
                self.assertIs(
                    MissionReward(self.config, device=device, task="MissionReward"),
                    current.return_value,
                )
                current.assert_called_once_with(config=self.config, device=device, task="MissionReward")
                daily.assert_not_called()

    def test_overseas_chinese_uses_daily_priority_and_verified_reward_callback(self):
        self.config.Emulator_PackageName = "com.stove.epic7.google"
        for language in ("cn", "global_cn", "auto"):
            with self.subTest(language=language):
                self.config.Emulator_GameLanguage = language
                self.plan("gacha", "flower", "arena_5", "stamina_50", points=25)
                self.assertTrue(daily_priority_enabled(self.config))
                self.plan(points=100, claimed=False)
                self.assertTrue(should_schedule_mission_reward(self.config))
                self.plan(points=100, claimed=True)
                self.assertFalse(should_schedule_mission_reward(self.config))

    def test_package_change_invalidates_same_day_snapshot(self):
        self.plan("gacha", points=100, claimed=True)
        self.config.Emulator_PackageName = "com.stove.epic7.google"
        self.assertTrue(should_schedule_mission_reward(self.config))
        self.plan("gacha")
        self.config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        self.assertIsNone(_current_daily_plan(self.config))

    def test_server_alias_keeps_context_but_language_change_invalidates_snapshot(self):
        self.config.Emulator_PackageName = "com.stove.epic7.google"
        snapshot = self.plan("gacha")
        self.assertEqual(snapshot["server"], "OVERSEA-Play")
        self.assertEqual(snapshot["language"], "cn")
        self.config.Emulator_PackageName = "OVERSEA-Play"
        self.config.Emulator_GameLanguage = "global_cn"
        self.assertIs(_current_daily_plan(self.config), snapshot)
        self.config.Emulator_GameLanguage = "en"
        self.assertIsNone(_current_daily_plan(self.config))
        self.plan("gacha", points=100, claimed=True)
        self.config.Emulator_GameLanguage = "cn"
        self.assertTrue(should_schedule_mission_reward(self.config))

    def test_legacy_and_malformed_snapshot_context_requires_new_scan(self):
        for field in ("server", "language"):
            for value in (None, "", 1, [], {}, "unrecognized"):
                with self.subTest(field=field, value=value):
                    snapshot = self.plan("gacha", points=100, claimed=True)
                    if value is None:
                        snapshot.pop(field)
                    else:
                        snapshot[field] = value
                    self.assertTrue(should_schedule_mission_reward(self.config))

    def test_full_points_without_confirmed_rewards_keeps_callback(self):
        self.plan(points=100, claimed=False)
        self.assertTrue(should_schedule_mission_reward(self.config))

    def test_full_confirmed_rewards_stop_callback_only_for_current_day(self):
        self.plan(points=100, claimed=True)
        self.assertFalse(should_schedule_mission_reward(self.config))
        self.plan(points=100, claimed=True, day=self.DAY - timedelta(days=1))
        self.assertTrue(should_schedule_mission_reward(self.config))

    def test_generated_config_update_preserves_plan_and_switch(self):
        from module.config.config_updater import ConfigUpdater

        plan = copy.deepcopy(self.plan("gacha", points=25, claimed=True))
        budget = {"version": 1, "contexts": {"server-day": {"executors": {"Arena": {"used": {"arena": 3}}}}}}
        deep_set(self.data, "MissionReward.MissionReward.DailyBudget", budget)
        deep_set(self.data, "MissionReward.MissionReward.DailyPriority", False)
        updated = ConfigUpdater().config_update(copy.deepcopy(self.data))
        self.assertEqual(deep_get(updated, DAILY_PLAN_PATH), plan)
        self.assertEqual(deep_get(updated, "MissionReward.MissionReward.DailyBudget"), budget)
        self.assertIs(deep_get(updated, "MissionReward.MissionReward.DailyPriority"), False)

    def test_missing_plan_does_not_trust_full_dashboard_counter(self):
        self.assertTrue(should_schedule_mission_reward(self.config))

    def test_malformed_mission_counter_invalidates_snapshot(self):
        plan = self.plan("gacha", points=100, claimed=True)
        plan["missions"][0]["current"] = 2
        self.assertTrue(should_schedule_mission_reward(self.config))

    def test_daily_switches_disable_strategy_without_invalidating_snapshot(self):
        snapshot = self.plan("gacha")
        for path in (
            "MissionReward.Scheduler.Enable",
            "MissionReward.MissionReward.ClaimDailyRewards",
            "MissionReward.MissionReward.DailyPriority",
        ):
            with self.subTest(path=path):
                deep_set(self.data, path, False)
                self.assertFalse(daily_priority_enabled(self.config))
                self.assertIs(_current_daily_plan(self.config), snapshot)
                deep_set(self.data, path, True)
                self.assertTrue(daily_priority_enabled(self.config))

    def test_unsupported_context_uses_dashboard_for_reward_callback(self):
        for package, language in (
            ("unknown.package", "cn"),
            (None, "cn"),
            ("com.stove.epic7.google", "en"),
            ("com.stove.epic7.google", "tw"),
            ("com.stove.epic7.google", "jp"),
        ):
            with self.subTest(package=package, language=language):
                self.config.Emulator_PackageName = package
                self.config.Emulator_GameLanguage = language
                self.assertFalse(daily_priority_enabled(self.config))
                self.config.stored.DailyActivity.time = self.DAY + timedelta(hours=1)
                self.assertFalse(should_schedule_mission_reward(self.config))
                self.config.stored.DailyActivity.time = self.DAY - timedelta(seconds=1)
                self.assertTrue(should_schedule_mission_reward(self.config))

    def test_malformed_snapshot_requires_review(self):
        for invalid in (None, {}, {"day": "bad"}, {"day": self.DAY.isoformat(), "points": True}):
            with self.subTest(plan=invalid):
                deep_set(self.data, DAILY_PLAN_PATH, invalid)
                self.assertIsNone(_current_daily_plan(self.config))
                self.assertTrue(should_schedule_mission_reward(self.config))

    def test_review_callback_reads_latest_snapshot_and_switch(self):
        self.plan("gacha", points=25)
        self.assertTrue(should_schedule_mission_reward(self.config))
        self.plan(points=100, claimed=False)
        self.assertTrue(should_schedule_mission_reward(self.config))
        self.plan(points=100, claimed=True)
        self.assertFalse(should_schedule_mission_reward(self.config))
        self.plan("gacha", points=25)
        self.assertTrue(should_schedule_mission_reward(self.config))
        deep_set(self.data, "MissionReward.MissionReward.DailyPriority", False)
        self.assertFalse(should_schedule_mission_reward(self.config))


class SchedulerPriorityTest(unittest.TestCase):
    def config(self, *names):
        from module.config.config import AzurLaneConfig

        # Template mode never reads an account or writes a configuration file.
        # Exercise the real due/waiting partition and priority filter, rather
        # than reproducing the scheduler's sorting in a test-only helper.
        config = AzurLaneConfig("template-priority-offline")
        config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        config.Emulator_GameLanguage = "cn"
        config.Scheduler_ServerUpdate = "03:00"
        for name in names:
            deep_set(config.data, f"{name}.Scheduler", {
                "Command": name, "Enable": True,
                "NextRun": datetime.fromisoformat("2000-01-01T00:00:00"),
            })
        return config

    def order(self, config):
        from module.config.config import AzurLaneConfig

        original = copy.deepcopy(config.data)
        with patch.object(AzurLaneConfig, "is_hoarding_task", False):
            config.get_next_task()
        self.assertEqual(config.data, original)
        self.assertEqual(config.modified, {})
        return [task.command for task in config.pending_task]

    def test_original_table_places_daily_tasks_first_and_keeps_other_relative_order(self):
        expected = [
            "Restart", "MissionReward", "Gacha", "SanctuaryDaily", "Arena", "Combat",
            "Mail", "SanctuaryMonthly", "Knights", "Store", "PetsGift", "SecretShop",
            "Pets", "DataUpdate", "CommunityAio", "LimitedActivity", "SpecialActivity",
        ]
        # Even stale enabled entries must not put manual tools in the queue.
        config = self.config("BuyPenguins", *reversed(expected), "EquipmentReroll")
        self.assertEqual(self.order(config), expected)

    def test_editing_original_table_changes_order_without_daily_override(self):
        config = self.config("Restart", "MissionReward", "Gacha", "SanctuaryDaily", "Arena", "Combat", "Mail")
        day = datetime.fromisoformat("2026-10-15T03:00:00")
        deep_set(config.data, DAILY_PLAN_PATH, {
            **daily_snapshot_context(config), "day": day.isoformat(), "points": 25,
            "missions": [
                {"key": key, "current": 0, "total": 1, "points": 10}
                for key in ("gacha", "flower", "arena_5", "stamina_50")
            ],
            "rewards_claimed": False,
        })
        deep_set(config.data, "Combat.CombatRuntime.Session.active", True)
        orders = (
            ["Restart", "Mail", "Arena", "Gacha", "SanctuaryDaily", "Combat", "MissionReward"],
            ["Restart", "MissionReward", "Combat", "SanctuaryDaily", "Gacha", "Arena", "Mail"],
        )
        with patch("tasks.mission_reward.scheduling.get_server_last_update", return_value=day):
            for expected in orders:
                with self.subTest(order=expected):
                    config.SCHEDULER_PRIORITY = " > ".join(expected)
                    self.assertEqual(self.order(config), expected)

    def test_snapshot_and_reward_state_do_not_change_original_table_order(self):
        expected = ["Restart", "MissionReward", "Gacha", "SanctuaryDaily", "Arena", "Combat", "Mail"]
        config = self.config(*reversed(expected))
        day = datetime.fromisoformat("2026-10-15T03:00:00")
        for points, claimed, offset in ((25, False, 0), (100, False, 0), (100, True, 0), (100, True, -1)):
            with self.subTest(points=points, claimed=claimed, day_offset=offset):
                deep_set(config.data, DAILY_PLAN_PATH, {
                    **daily_snapshot_context(config),
                    "day": (day + timedelta(days=offset)).isoformat(),
                    "points": points, "missions": [], "rewards_claimed": claimed,
                })
                with patch("tasks.mission_reward.scheduling.get_server_last_update", return_value=day):
                    self.assertEqual(self.order(config), expected)
        for invalid in (None, {}, {"day": "bad"}):
            with self.subTest(plan=invalid):
                deep_set(config.data, DAILY_PLAN_PATH, invalid)
                self.assertEqual(self.order(config), expected)

    def test_disabled_and_future_tasks_keep_enable_and_wait_times(self):
        config = self.config("Mail", "Arena", "Combat", "Gacha", "SanctuaryDaily", "MissionReward", "Restart")
        deep_set(config.data, "Combat.Scheduler.Enable", False)
        future = datetime.fromisoformat("2099-01-01T00:00:00")
        deep_set(config.data, "Arena.Scheduler.NextRun", future)
        self.assertEqual(self.order(config), ["Restart", "MissionReward", "Gacha", "SanctuaryDaily", "Mail"])
        self.assertEqual([(task.command, task.next_run) for task in config.waiting_task], [("Arena", future)])
        deep_set(config.data, "MissionReward.Scheduler.NextRun", future + timedelta(minutes=5))
        self.assertEqual(self.order(config), ["Restart", "Gacha", "SanctuaryDaily", "Mail"])
        self.assertEqual(
            [(task.command, task.next_run) for task in config.waiting_task],
            [("Arena", future), ("MissionReward", future + timedelta(minutes=5))],
        )

    def test_waiting_queue_uses_next_run_and_table_for_ties(self):
        config = self.config("Arena", "MissionReward", "Gacha", "Mail", "BuyPenguins", "EquipmentReroll")
        future = datetime.fromisoformat("2099-01-01T00:00:00")
        for name in config.data:
            deep_set(config.data, f"{name}.Scheduler.NextRun", future)
        deep_set(config.data, "Mail.Scheduler.NextRun", future - timedelta(minutes=5))
        self.assertEqual(self.order(config), [])
        self.assertEqual([task.command for task in config.waiting_task], ["Mail", "MissionReward", "Gacha", "Arena"])
        config.SCHEDULER_PRIORITY = "Mail > Arena > Gacha > MissionReward"
        self.assertEqual(self.order(config), [])
        self.assertEqual([task.command for task in config.waiting_task], ["Mail", "Arena", "Gacha", "MissionReward"])

    def test_daily_switch_and_language_do_not_select_another_priority_order(self):
        expected = ["Restart", "MissionReward", "Gacha", "SanctuaryDaily", "Arena", "Combat", "Mail"]
        config = self.config(*reversed(expected))
        for package, language in (
            ("com.zlongame.cn.epicseven", "cn"),
            ("com.stove.epic7.google", "cn"),
            ("com.stove.epic7.google", "en"),
        ):
            for enabled in (True, False):
                with self.subTest(package=package, language=language, daily_priority=enabled):
                    config.Emulator_PackageName = package
                    config.Emulator_GameLanguage = language
                    deep_set(config.data, "MissionReward.MissionReward.DailyPriority", enabled)
                    self.assertEqual(self.order(config), expected)
