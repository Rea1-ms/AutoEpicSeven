# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

import unittest

from tasks.arena.arena import Arena
from tasks.dungeon.dungeon import Combat
from tasks.gacha.gacha import Gacha
from tasks.knights.knights import Knights
from tasks.sanctuary.sanctuary import Sanctuary


class MissionRewardCallbackRuleTest(unittest.TestCase):
    def test_sanctuary_daily_requires_claim(self):
        self.assertFalse(Sanctuary._should_schedule_mission_reward_after_daily(False))
        self.assertTrue(Sanctuary._should_schedule_mission_reward_after_daily(True))

    def test_world_boss_requires_completed_rounds(self):
        self.assertFalse(Knights._should_schedule_mission_reward_after_world_boss(0))
        self.assertTrue(Knights._should_schedule_mission_reward_after_world_boss(1))

    def test_arena_npc_requires_completed_rounds(self):
        self.assertFalse(Arena._should_schedule_mission_reward_after_npc(0))
        self.assertTrue(Arena._should_schedule_mission_reward_after_npc(2))

    def test_gacha_requires_completed_free_summon(self):
        self.assertFalse(Gacha._should_schedule_mission_reward_after_free_summon(False))
        self.assertTrue(Gacha._should_schedule_mission_reward_after_free_summon(True))

    def test_combat_requires_finished_session_and_no_background_runtime(self):
        self.assertFalse(Combat._should_schedule_mission_reward(0, runtime_active=False))
        self.assertFalse(Combat._should_schedule_mission_reward(1, runtime_active=True))
        self.assertTrue(Combat._should_schedule_mission_reward(1, runtime_active=False))


# Historical assertions retained verbatim for migration review; not executable evidence.
HISTORICAL_CONTRACTS = 'def test_combat_success_runtime_state_matches_mode(self):\n        self.assertTrue(Combat._runtime_active_after_success(event_mode=True, use_fast_combat=True))\n        self.assertTrue(Combat._runtime_active_after_success(event_mode=False, use_fast_combat=False))\n        self.assertFalse(Combat._runtime_active_after_success(event_mode=False, use_fast_combat=True))'
