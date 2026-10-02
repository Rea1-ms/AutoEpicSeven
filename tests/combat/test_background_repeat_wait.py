# ruff: noqa: E402
"""Historical manual assertions adapted to the account-free offline runner."""
import unittest
from module.config import server as _test_server
_test_server.set_lang("global_cn")



from types import SimpleNamespace

from unittest.mock import Mock, call

from tasks.arena.npc_combat import ArenaNpcCombatMixin

from tasks.dungeon.runtime import (  # noqa: E402
    background_repeat_combat_requires_game_client,
    is_background_repeat_combat_active,
)

class FakeConfig:
    def __init__(self, sessions):
        self.values = {}
        for task, session in sessions.items():
            base = f"{task}.CombatRuntime.Session"
            self.values[f"{base}.active"] = session.get("active", False)
            self.values[f"{base}.mode"] = session.get("mode")

    def cross_get(self, path, default=None):
        return self.values.get(path, default)

def check_arena_fast_battle() -> None:
    sessions = (
        {},
        {"Combat": {"active": True, "mode": "repeat_server"}},
        {"CombatFarm": {"active": True, "mode": "repeat_server"}},
        {"Combat": {"active": True, "mode": "repeat_background"}},
        {"CombatFarm": {"active": True}},
        {
            "Combat": {"active": True, "mode": "repeat_server"},
            "CombatFarm": {"active": True, "mode": "repeat_background"},
        },
    )
    checks = 0
    for background in sessions:
        for setting, expected in ((True, True), (False, False), (None, True)):
            config = FakeConfig(background)
            config.Arena_NPCCombatCount = 2
            if setting is not None:
                config.Arena_NPCCombatFastBattle = setting

            # Run the real arena batch entry. Only the individual fight and
            # resource storage are replaced, so the removed background guard
            # would still receive active records and fail the enabled cases.
            arena = ArenaNpcCombatMixin()
            arena.config = config
            arena.device = SimpleNamespace(click_record_clear=Mock())
            arena._arena_burnout_enabled = Mock(return_value=False)
            arena._stored_arena_flag_status = Mock(
                side_effect=[(2, 5), (1, 5), (0, 5)]
            )
            arena._consume_stored_arena_flags = Mock()
            arena._npc_combat_once = Mock(return_value="completed")

            assert arena._run_npc_combat(), (background, setting)
            assert arena._npc_combat_once.call_args_list == [
                call(use_fast_battle=expected, skip_first_screenshot=True),
                call(use_fast_battle=expected, skip_first_screenshot=True),
            ], (background, setting, arena._npc_combat_once.call_args_list)
            assert arena._arena_npc_completed_rounds == 2
            assert arena._consume_stored_arena_flags.call_args_list == [
                call(1),
                call(1),
            ]
            checks += 1
    print(f"{checks} arena fast-battle preference checks passed")

class ManualChecks(unittest.TestCase):
    def test_background_session_ownership(self):
        server = FakeConfig({"Combat": {"active": True, "mode": "repeat_server"}})
        assert is_background_repeat_combat_active(server)
        assert not background_repeat_combat_requires_game_client(server)
        legacy = FakeConfig(
                {"Combat": {"active": True, "mode": "repeat_background"}}
            )
        assert is_background_repeat_combat_active(legacy)
        assert background_repeat_combat_requires_game_client(legacy)
        unknown = FakeConfig({"CombatFarm": {"active": True}})
        assert background_repeat_combat_requires_game_client(unknown)
        mixed = FakeConfig(
                {
                    "Combat": {"active": True, "mode": "repeat_server"},
                    "CombatFarm": {"active": True, "mode": "repeat_background"},
                }
            )
        assert background_repeat_combat_requires_game_client(mixed)
        inactive = FakeConfig(
                {"Combat": {"active": False, "mode": "repeat_background"}}
            )
        assert not is_background_repeat_combat_active(inactive)
        assert not background_repeat_combat_requires_game_client(inactive)

    def test_independent_arena_fast_battle(self):
        check_arena_fast_battle()
