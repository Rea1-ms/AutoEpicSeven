# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

import subprocess
import sys
import unittest
from datetime import datetime
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
        self.config.LimitedActivity_GetDetectiveSearch = True
        self.config.LimitedActivity_GetEpicPathDailyReward = True
        self.now = datetime.fromisoformat("2026-10-29T21:00:00+08:00")

    def test_new_events_are_international_only_and_have_end_boundaries(self):
        events = calendar.active_activities(self.config, self.now, task="LimitedActivity")
        self.assertEqual({e.mode for e in events}, {"support_reward", "completion_record", "detective_search", "epic_path"})
        for event in events:
            self.assertFalse(event.contains(event.end))
        self.config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        server.set_lang("cn")
        self.assertFalse(any(e.mode in ("completion_record", "detective_search", "epic_path")
                             for e in calendar.active_activities(self.config, self.now)))

    def test_battle_and_arena_wake_each_unfinished_event_and_stop_after_receipt(self):
        events = calendar.active_activities(self.config, self.now, task="LimitedActivity")
        for mode, option in (("detective_search", "GetDetectiveSearch"), ("epic_path", "GetEpicPathDailyReward")):
            event = next(e for e in events if e.mode == mode)
            with patch.object(scheduling, "active_activities", side_effect=lambda config, *, task, event=event: (event,) if task == "LimitedActivity" else ()):
                for arena in (False, True):
                    scheduling.schedule_activity_after_battle(self.config, arena=arena)
                self.assertEqual(self.config.calls, [("LimitedActivity", {"force_call": False})] * 2)
                self.config.calls.clear()
                self.config.values[f"LimitedActivity.LimitedActivity.{option}"] = False
                self.assertFalse(scheduling.should_schedule_after_battle(self.config))
                self.config.values[f"LimitedActivity.LimitedActivity.{option}"] = True
                self.config.values[scheduling._checked_path(self.config, event.event_id)] = datetime(2026, 10, 29, 21)
                with patch.object(scheduling, "get_server_last_update", return_value=datetime(2026, 10, 29, 2)):
                    self.assertFalse(scheduling.should_schedule_after_battle(self.config))
                with patch.object(scheduling, "get_server_last_update", return_value=datetime(2026, 10, 30, 2)):
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
