"""NPC round regressions: stale controls do not undo a pending battle."""

import unittest

from module.config import server
from tests.support.arena import ArenaReplay
from tests.support.interaction import scene
from tests.support.offline import ControlledClock


class NpcCombatReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def test_captured_fast_round_survives_start_timeout_and_repeated_result(self):
        # The user captures supply all recognition pixels. Repeated observations
        # and the seven-second start delay deliberately reproduce the log's race;
        # they do not claim the manual screenshot timestamps are an input trace.
        r = ArenaReplay([
            scene(fixture='20261015-165556-841'),
            scene(fixture='20261015-165556-841'),
            scene(fixture='20261015-165601-001'),
            scene(fixture='20261015-165601-001'),
            scene(fixture='20261015-165606-908'),
            scene(fixture='20261015-165606-908'),
            scene(fixture='20261015-165612-396', seconds=7),
            scene(fixture='20261015-165612-968'),
            scene(fixture='20261015-165613-439'),
            *[scene(fixture='20261015-165615-527')] * 4,
            *[scene(fixture='20261015-165616-798')] * 2,
            scene(fixture='20261015-165618-643'),
        ], self.clock)
        r.task.config.Arena_NPCCombatCount = 1
        self.assertTrue(r.task._run_npc_combat())
        self.assertEqual(r.clicks('NPC_OPPONENT'), [1])
        self.assertEqual(r.clicks('CHALLENGE'), [2])
        self.assertEqual(r.clicks('BATTLE_START'), [5, 6])
        self.assertEqual(r.clicks('FAST_BATTLE_RESULT_CONFIRM'), [9, 11, 13])
        self.assertEqual(len(r.actions), 7)
        self.assertEqual(r.task._arena_npc_completed_rounds, 1)
        r.task._consume_stored_arena_flags.assert_called_once_with(1)
        self.assertEqual(r.index, 15)

    def test_start_retry_keeps_result_handling_when_challenge_appears_next(self):
        prepare = scene('BATTLE_START', 'FAST_BATTLE_ON')
        r = ArenaReplay([
            prepare, prepare,
            scene('BATTLE_START', 'FAST_BATTLE_ON', seconds=7),
            scene('CHALLENGE'), scene('CHALLENGE'),
            scene('FAST_BATTLE_RESULT_CONFIRM'),
            scene(home_ready=True),
        ], self.clock)
        self.assertEqual(r.task._npc_combat_once(True), 'completed')
        self.assertEqual(r.clicks('BATTLE_START'), [1, 2])
        self.assertEqual(r.clicks('FAST_BATTLE_RESULT_CONFIRM'), [5])
        self.assertEqual(r.clicks('CHALLENGE'), [])
        self.assertEqual(r.index, 6)

    def test_background_npc_and_challenge_never_select_another_opponent(self):
        prepare = scene('BATTLE_START', 'FAST_BATTLE_ON')
        background = scene('NPC_OPPONENT', 'CHALLENGE', challenge_ready=True)
        r = ArenaReplay([prepare, prepare] + [background] * 14 + [
            scene('FAST_BATTLE_RESULT_CONFIRM'), scene(home_ready=True),
        ], self.clock)
        self.assertEqual(r.task._npc_combat_once(True), 'completed')
        self.assertEqual(r.actions, [(1, 'BATTLE_START'), (16, 'FAST_BATTLE_RESULT_CONFIRM')])
        self.assertEqual(r.index, 17)

    def test_start_click_retries_only_after_pending_interval(self):
        prepare = scene('BATTLE_START', 'FAST_BATTLE_ON')
        r = ArenaReplay([prepare] * 20 + [scene('FAST_BATTLE_RESULT_CONFIRM'),
                                       scene(home_ready=True)], self.clock)
        self.assertEqual(r.task._npc_combat_once(True), 'completed')
        r.assert_retried(self, 'BATTLE_START', 6)
        self.assertEqual(len(r.clicks('BATTLE_START')), 2)
        self.assertEqual(r.clicks('FAST_BATTLE_RESULT_CONFIRM'), [20])

    def test_result_retries_until_home_without_starting_again(self):
        prepare = scene('BATTLE_START', 'FAST_BATTLE_ON')
        result = scene('FAST_BATTLE_RESULT_CONFIRM')
        r = ArenaReplay([prepare, prepare] + [result] * 10 + [
            scene('BATTLE_START', 'FAST_BATTLE_ON', seconds=8),
            scene('CHALLENGE'), scene(home_ready=True),
        ], self.clock)
        self.assertEqual(r.task._npc_combat_once(True), 'completed')
        r.assert_retried(self, 'FAST_BATTLE_RESULT_CONFIRM', 0.8)
        self.assertEqual(r.clicks('BATTLE_START'), [1])
        self.assertEqual(r.clicks('CHALLENGE'), [])
        self.assertEqual(r.index, 14)

    def test_return_home_can_complete_when_result_frame_was_missed(self):
        prepare = scene('BATTLE_START', 'FAST_BATTLE_ON')
        r = ArenaReplay([prepare, prepare, scene('CHALLENGE'), scene(home_ready=True)], self.clock)
        self.assertEqual(r.task._npc_combat_once(True), 'completed')
        self.assertEqual(r.actions, [(1, 'BATTLE_START')])
        self.assertEqual(r.index, 3)

    def test_normal_battle_auto_toggle_and_result_keep_round_state(self):
        prepare = scene('BATTLE_START', 'FAST_BATTLE_OFF')
        fighting = scene('OPPONENT', 'AUTO_FIGHT_EXIST')
        r = ArenaReplay([prepare, prepare] + [fighting] * 12 + [
            scene('AUTO_FIGHT_EXIST'), scene('AUTO_BATTLE_RESULT_CONFIRM'),
            scene(home_ready=True),
        ], self.clock)
        self.assertEqual(r.task._npc_combat_once(False), 'completed')
        r.assert_retried(self, 'AUTO_FIGHT', 2)
        self.assertEqual(r.clicks('BATTLE_START'), [1])
        self.assertEqual(r.clicks('AUTO_BATTLE_RESULT_CONFIRM'), [15])
        self.assertEqual(r.clicks('FAST_BATTLE_ON'), [])

    def test_fast_lock_and_zero_remaining_fall_back_to_normal(self):
        for button, counter in [('FAST_BATTLE_LOCKED', (0, 10, 10)),
                                ('FAST_BATTLE_OFF', (0, 10, 10))]:
            with self.subTest(button=button):
                prepare = scene('BATTLE_START', button, fast_times=counter)
                r = ArenaReplay([prepare, prepare, scene('AUTO_BATTLE_RESULT_CONFIRM'),
                                 scene(home_ready=True)], self.clock)
                self.assertEqual(r.task._npc_combat_once(True), 'completed')
                self.assertEqual(r.actions, [(1, 'BATTLE_START'), (2, 'AUTO_BATTLE_RESULT_CONFIRM')])

    def test_repeated_result_counts_and_consumes_one_round(self):
        prepare = scene('BATTLE_START', 'FAST_BATTLE_ON')
        r = ArenaReplay([prepare, prepare] + [scene('FAST_BATTLE_RESULT_CONFIRM')] * 8
                        + [scene(home_ready=True)], self.clock)
        r.task.config.Arena_NPCCombatCount = 1
        self.assertTrue(r.task._run_npc_combat())
        self.assertEqual(r.task._arena_npc_completed_rounds, 1)
        r.task._consume_stored_arena_flags.assert_called_once_with(1)
        self.assertEqual(r.clicks('BATTLE_START'), [1])
        self.assertEqual(r.index, 10)
