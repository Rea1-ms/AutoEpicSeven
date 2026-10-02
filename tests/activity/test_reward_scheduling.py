# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tasks.activity.legacy.summer_2026_06_25.scheduling import (
    TASK_REWARD_CLAIMED_AT,
    should_schedule_after_battle,
)
from tasks.mission_reward.scheduling import should_schedule_mission_reward


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
