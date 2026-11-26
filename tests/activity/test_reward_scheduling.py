# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

import json
import subprocess
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from module.config import server
from tasks.activity import calendar, scheduling
from tasks.activity.common_activity import CommonActivityBatch
from tasks.activity.completion_record import CompletionRecord
from tasks.activity.legacy.summer_2026_06_25.scheduling import (
    TASK_REWARD_CLAIMED_AT,
    should_schedule_after_battle,
)
from tasks.mission_reward.scheduling import should_schedule_mission_reward
from tests.support.offline import ROOT


class ActivityConfigReloadTests(unittest.TestCase):
    @contextmanager
    def memory_config(self):
        from module.config.config import AzurLaneConfig
        from module.config.config_updater import ConfigUpdater

        now = datetime.now().replace(microsecond=0)
        data = {
            "Alas": {"Emulator": {"PackageName": "OVERSEA-Play", "GameLanguage": "cn"}},
            "SecretShop": {"Scheduler": {
                "Enable": True, "Command": "SecretShop", "NextRun": now - timedelta(minutes=1),
            }},
        }
        for task in calendar.ACTIVITY_TASK_MODES:
            data[task] = {"Scheduler": {
                "Enable": True, "Command": task, "NextRun": now - timedelta(minutes=1),
            }}
        saved = [json.dumps(data, default=str)]
        config = AzurLaneConfig("template-activity-reload-offline")

        def read_config(*args, **kwargs):
            return ConfigUpdater().config_update(json.loads(saved[0]))

        def write_config(name, data):
            saved[0] = json.dumps(data, default=str)

        # Replace only account I/O. Real delay/update/load/override/binding and
        # queue partitioning must run together: checking task_delay's arguments
        # alone cannot catch a future target being reset during config reload.
        with patch.object(config, "read_file", side_effect=read_config), \
                patch.object(config, "write_file", side_effect=write_config), \
                patch.object(AzurLaneConfig, "is_hoarding_task", False):
            config.load()
            config.bind("SpecialActivity")
            config.auto_update = True
            yield config

    def test_completed_handbook_stays_waiting_after_delay_and_reloads(self):
        with self.memory_config() as config:
            now = datetime.now(calendar.EVENT_TIMEZONE).replace(microsecond=0)
            event = calendar.ActivityWindow(
                "completed_handbook", "Completed handbook", "alencia_handbook", "OVERSEA",
                now - timedelta(days=1), now + timedelta(days=24),
            )
            scheduling.mark_activity_completed(config, event.event_id)
            target = event.end.astimezone().replace(tzinfo=None)
            with patch.object(scheduling, "active_activities", return_value=(event,)), \
                    patch.object(scheduling, "next_activity_start", return_value=None):
                scheduling.delay_next_activity_check(config)
            for _ in range(3):
                config.load()
                self.assertEqual(config.cross_get("SpecialActivity.Scheduler.NextRun"), target)
                self.assertTrue(scheduling.is_activity_completed(config, event.event_id))
                config.get_next_task()
                self.assertNotIn("SpecialActivity", [task.command for task in config.pending_task])
                self.assertIn("SecretShop", [task.command for task in config.pending_task])
                self.assertIn("SpecialActivity", [task.command for task in config.waiting_task])

    def test_future_activity_launch_survives_reload_for_both_tasks(self):
        with self.memory_config() as config:
            target = datetime.now(calendar.EVENT_TIMEZONE).replace(microsecond=0) + timedelta(days=10)
            for task in calendar.ACTIVITY_TASK_MODES:
                with self.subTest(task=task), \
                        patch.object(scheduling, "active_activities", return_value=()), \
                        patch.object(scheduling, "next_activity_start", return_value=target):
                    scheduling.delay_next_activity_check(config, task=task)
                    config.load()
                    self.assertEqual(
                        config.cross_get(f"{task}.Scheduler.NextRun"),
                        target.astimezone().replace(tzinfo=None),
                    )
                    config.get_next_task()
                    self.assertNotIn(task, [item.command for item in config.pending_task])
                    self.assertIn(task, [item.command for item in config.waiting_task])

    def test_daily_and_monthly_stale_timestamp_limits_are_preserved(self):
        from module.config.deep import deep_set

        with self.memory_config() as config:
            now = datetime.now().replace(microsecond=0)
            for task, valid_days, stale_days in (("SecretShop", 0.5, 2), ("SanctuaryMonthly", 35, 41)):
                with self.subTest(task=task):
                    valid = now + timedelta(days=valid_days)
                    deep_set(config.data, f"{task}.Scheduler.NextRun", valid)
                    config.config_override()
                    self.assertEqual(config.cross_get(f"{task}.Scheduler.NextRun"), valid)
                    deep_set(config.data, f"{task}.Scheduler.NextRun", now + timedelta(days=stale_days))
                    config.config_override()
                    self.assertLessEqual(
                        config.cross_get(f"{task}.Scheduler.NextRun"),
                        datetime.now(),
                    )


class RewardSchedulingTest(unittest.TestCase):
    def test_full_daily_activity_only_blocks_current_server_day(self):
        daily = SimpleNamespace(
            time=datetime(2026, 8, 10, 3),
            value=100,
            total=100,
        )
        config = SimpleNamespace(
            stored=SimpleNamespace(DailyActivity=daily),
            Scheduler_ServerUpdate="02:00",
        )

        with patch(
            "tasks.mission_reward.scheduling.get_server_last_update",
            return_value=datetime(2026, 8, 10, 2),
        ):
            self.assertFalse(should_schedule_mission_reward(config))
            daily.time = datetime(2026, 8, 9, 3)
            self.assertTrue(should_schedule_mission_reward(config))

    def test_special_activity_lock_expires_at_server_update(self):
        config = MagicMock()
        config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        config.SpecialActivity_GetTaskReward = True
        config.Scheduler_ServerUpdate = "02:00"
        claimed_at = datetime(2026, 8, 10, 3)
        config.cross_get.side_effect = lambda key, default=None: (
            claimed_at if key == TASK_REWARD_CLAIMED_AT else default
        )

        with patch(
            "tasks.activity.legacy.summer_2026_06_25.scheduling.get_server_last_update",
            return_value=datetime(2026, 8, 10, 2),
        ):
            self.assertFalse(should_schedule_after_battle(config))
            claimed_at = datetime(2026, 8, 9, 3)
            self.assertTrue(should_schedule_after_battle(config))


class LimitedActivitySchedulingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        from tests.support.meirin_reward import Config
        self.config = Config()
        self.config.LimitedActivity_GetEpicPathDailyReward = True
        self.now = datetime.fromisoformat("2026-11-26T16:00:00+08:00")

    def test_new_events_are_international_only_and_have_end_boundaries(self):
        events = calendar.active_activities(self.config, self.now, task="LimitedActivity")
        self.assertEqual({e.mode for e in events}, {"support_reward", "completion_record", "epic_path"})
        for event in events:
            self.assertFalse(event.contains(event.end))
        self.config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        server.set_lang("cn")
        self.assertFalse(any(e.mode in ("completion_record", "epic_path")
                             for e in calendar.active_activities(self.config, self.now)))

    def test_battle_and_arena_wake_each_unfinished_event_and_stop_after_receipt(self):
        events = calendar.active_activities(self.config, self.now, task="LimitedActivity")
        event = next(e for e in events if e.mode == "epic_path")
        with patch.object(scheduling, "active_activities", side_effect=lambda config, *, task, event=event: (event,) if task == "LimitedActivity" else ()):
            for arena in (False, True):
                scheduling.schedule_activity_after_battle(self.config, arena=arena)
            self.assertEqual(self.config.calls, [("LimitedActivity", {"force_call": False})] * 2)
            self.config.calls.clear()
            self.config.values["LimitedActivity.LimitedActivity.GetEpicPathDailyReward"] = False
            self.assertFalse(scheduling.should_schedule_after_battle(self.config))
            self.config.values["LimitedActivity.LimitedActivity.GetEpicPathDailyReward"] = True
            self.config.values[scheduling._checked_path(self.config, event.event_id)] = datetime(2026, 11, 26, 16)
            with patch.object(scheduling, "get_server_last_update", return_value=datetime(2026, 11, 26, 2)):
                self.assertFalse(scheduling.should_schedule_after_battle(self.config))
            with patch.object(scheduling, "get_server_last_update", return_value=datetime(2026, 11, 27, 2)):
                self.assertTrue(scheduling.should_schedule_after_battle(self.config))

    def test_attendance_does_not_wake_after_battle_and_flags_are_registered(self):
        event = next(e for e in calendar.active_activities(self.config, self.now) if e.mode == "completion_record")
        with patch.object(scheduling, "active_activities", return_value=(event,)):
            self.assertFalse(scheduling.should_schedule_after_battle(self.config))
        self.assertIs(CommonActivityBatch.ACTIVITIES["completion_record"][2], CompletionRecord)

    def test_disabled_task_does_not_wake_battle_rewards(self):
        self.config.enabled = False
        with patch.object(scheduling, "active_activities") as active:
            self.assertFalse(scheduling.should_schedule_after_battle(self.config))
            active.assert_not_called()

    def test_activity_route_and_modal_registration_are_isolated_by_server(self):
        code = '''
from types import SimpleNamespace
from unittest.mock import Mock
from module.config import server
server.server = {package!r}
server.set_lang({lang!r})
from tasks.base import page
from tasks.base.ui import UI
from tasks.activity.calendar import active_activities
assert ('page_epic_path_daily' in page.Page.all_pages) == ({lang!r} == 'global_cn')
assert page.page_main.links[page.page_common_activity].name == 'MAIN_GOTO_COMMON_ACTIVITY'
page.Page.init_connection(page.page_common_activity)
assert page.page_main.parent is page.page_common_activity
if {lang!r} == 'global_cn':
    modal = page.page_epic_path_daily
    assert modal.parent is page.page_common_activity
    assert modal.links[page.page_common_activity].name == 'EPIC_PATH_TASK_CLOSE'
    ui = object.__new__(UI)
    ui.device = SimpleNamespace(image=object(), screenshot=Mock(side_effect=AssertionError('real device call')))
    ui.is_in_main = lambda interval=0: False
    ui.appear = lambda button, **kwargs: button is modal.check_button
    assert ui.ui_get_current_page() is modal
    ui.device.screenshot.assert_not_called()
page.Page.clear_connection()
'''
        for package, lang in (("CN-Official", "cn"), ("OVERSEA-Play", "global_cn"), ("OVERSEA-Play", "global_en")):
            with self.subTest(lang=lang):
                result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code.format(package=package, lang=lang)],
                                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=45, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
