"""Automated replacements for the historical manual arena entry observations."""

import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from module.config import server

server.set_lang('global_cn')

from tasks.base.page import page_arena_hub
from tests.support.arena_entry import entry_replay
from tests.support.interaction import scene
from tests.support.offline import ControlledClock


class ArenaEntryTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.clock = stack.enter_context(ControlledClock())

    def test_entry_surface_preserves_open_arena_and_hub(self):
        for frame, expected in (
            (scene('ARENA_CHECK'), 'arena'),
            (scene(page='page_arena_hub'), 'surface'),
        ):
            with self.subTest(surface=expected), entry_replay([frame], self.clock) as replay:
                self.assertEqual(replay.task._ensure_arena_entry_surface(), expected)
                replay.task.ui_goto.assert_not_called()
                self.assertEqual(replay.actions, [])
                self.assertEqual(replay.index, 0)

    def test_unknown_entry_surface_routes_to_hub_with_existing_frame(self):
        with entry_replay([scene(page='page_main')], self.clock) as replay:
            self.assertEqual(replay.task._ensure_arena_entry_surface(), 'surface')
            replay.task.ui_goto.assert_called_once_with(page_arena_hub, skip_first_screenshot=True)
            self.assertEqual(replay.actions, [])

    def test_common_entry_retries_then_handles_weekly_reward_before_exit(self):
        hub = scene('ARENA_COMMON_ENTRY', page='page_arena_hub')
        unselected = scene(
            'ARENA_CHECK', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_CLAIM',
            disabled=('ARENA_CHECK', 'WEEKLY_REWARDS_CLAIM'),
        )
        selected_disabled = scene(
            'ARENA_CHECK', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_SKYSTONE_SELECTED', 'WEEKLY_REWARDS_CLAIM',
            disabled=('ARENA_CHECK', 'WEEKLY_REWARDS_CLAIM'),
        )
        selected_ready = scene(
            'ARENA_CHECK', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_SKYSTONE_SELECTED', 'WEEKLY_REWARDS_CLAIM',
            disabled=('ARENA_CHECK',),
        )
        frames = [hub] * 5 + [unselected] * 5 + [selected_disabled] * 2
        frames += [selected_ready] * 4 + [scene()] * 2 + [scene('ARENA_CHECK')]
        with entry_replay(frames, self.clock) as replay:
            self.assertEqual(replay.task._enter_arena(), 'entered')
            for name in ('ARENA_COMMON_ENTRY', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_CLAIM'):
                replay.assert_retried(self, name, 1)
            self.assertTrue(all(index < 5 for index in replay.clicks('ARENA_COMMON_ENTRY')))
            self.assertTrue(all(5 <= index < 10 for index in replay.clicks('WEEKLY_REWARDS_SKYSTONE')))
            self.assertTrue(all(12 <= index < 16 for index in replay.clicks('WEEKLY_REWARDS_CLAIM')))
            self.assertEqual(replay.clicks('WEEKLY_REWARDS_SKYSTONE_SELECTED'), [])
            self.assertEqual(replay.index, len(frames) - 1)
            replay.task.ui_goto.assert_not_called()

    def test_selected_weekly_reward_ignores_disabled_claim(self):
        frame = scene(
            'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_SKYSTONE_SELECTED', 'WEEKLY_REWARDS_CLAIM',
            disabled=('WEEKLY_REWARDS_CLAIM',),
        )
        with entry_replay([frame], self.clock) as replay:
            self.assertFalse(replay.task._handle_weekly_rewards_popup())
            self.assertEqual(replay.actions, [])

    def test_weekly_popup_handler_reports_only_actions_and_respects_interval(self):
        frame = scene('WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_SKYSTONE_SELECTED', 'WEEKLY_REWARDS_CLAIM')
        with entry_replay([frame], self.clock) as replay:
            self.assertTrue(replay.task._handle_weekly_rewards_popup())
            self.assertFalse(replay.task._handle_weekly_rewards_popup())
            self.assertEqual(replay.actions, [(0, 'WEEKLY_REWARDS_CLAIM')])
            self.clock.advance(1.1)
            self.assertTrue(replay.task._handle_weekly_rewards_popup())
            self.assertEqual(replay.actions, [(0, 'WEEKLY_REWARDS_CLAIM')] * 2)
            self.assertEqual(replay.index, 0)

    def test_weekly_reward_defaults_to_skystone_for_existing_configs(self):
        with entry_replay([scene('WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_MYSTIC')], self.clock) as replay:
            self.assertFalse(hasattr(replay.task.config, 'Arena_WeeklyReward'))
            self.assertTrue(replay.task._handle_weekly_rewards_popup())
            self.assertEqual(replay.actions, [(0, 'WEEKLY_REWARDS_SKYSTONE')])

    def test_weekly_reward_switch_waits_for_target_border_and_retries(self):
        for reward, target, selected, other in (
            ('Skystone', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_SKYSTONE_SELECTED', 'WEEKLY_REWARDS_MYSTIC_SELECTED'),
            ('MysticMedals', 'WEEKLY_REWARDS_MYSTIC', 'WEEKLY_REWARDS_MYSTIC_SELECTED', 'WEEKLY_REWARDS_SKYSTONE_SELECTED'),
        ):
            wrong = scene('ARENA_CHECK', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_MYSTIC',
                          'WEEKLY_REWARDS_CLAIM', other, disabled=('ARENA_CHECK',))
            pending = scene('ARENA_CHECK', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_MYSTIC',
                            'WEEKLY_REWARDS_CLAIM', disabled=('ARENA_CHECK',))
            ready = scene('ARENA_CHECK', 'WEEKLY_REWARDS_SKYSTONE', selected,
                          'WEEKLY_REWARDS_CLAIM', disabled=('ARENA_CHECK',))
            frames = [wrong] * 5 + [scene()] * 2 + [pending] * 4
            frames += [ready] * 4 + [scene()] * 2 + [scene('ARENA_CHECK')]
            with self.subTest(reward=reward), entry_replay(frames, self.clock) as replay:
                replay.task.config.Arena_WeeklyReward = reward
                self.assertEqual(replay.task._enter_arena(), 'entered')
                replay.assert_retried(self, target, 1)
                replay.assert_retried(self, 'WEEKLY_REWARDS_CLAIM', 1)
                self.assertTrue(all(index < 11 for index in replay.clicks(target)))
                self.assertTrue(all(11 <= index < 15 for index in replay.clicks('WEEKLY_REWARDS_CLAIM')))
                self.assertEqual(replay.clicks(selected), [])
                self.assertEqual(replay.clicks(other), [])
                if reward == 'MysticMedals':
                    self.assertEqual(replay.clicks('WEEKLY_REWARDS_SKYSTONE'), [])
                else:
                    self.assertEqual(replay.clicks('WEEKLY_REWARDS_MYSTIC'), [])
                self.assertEqual(replay.index, len(frames) - 1)

    def test_weekly_reward_never_claims_from_bright_button_without_selected_border(self):
        for reward in ('Skystone', 'MysticMedals'):
            frame = scene('WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_MYSTIC', 'WEEKLY_REWARDS_CLAIM')
            with self.subTest(reward=reward), entry_replay([frame], self.clock) as replay:
                replay.task.config.Arena_WeeklyReward = reward
                self.assertTrue(replay.task._handle_weekly_rewards_popup())
                self.assertFalse(replay.task._handle_weekly_rewards_popup())
                self.assertEqual(replay.clicks('WEEKLY_REWARDS_CLAIM'), [])

    def test_weekly_reward_ignores_unrelated_frame_and_disabled_mystic_claim(self):
        for frame in (
            scene('WEEKLY_REWARDS_MYSTIC_SELECTED', 'WEEKLY_REWARDS_CLAIM'),
            scene('WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_MYSTIC_SELECTED',
                  'WEEKLY_REWARDS_CLAIM', disabled=('WEEKLY_REWARDS_CLAIM',)),
        ):
            with self.subTest(frame=frame), entry_replay([frame], self.clock) as replay:
                replay.task.config.Arena_WeeklyReward = 'MysticMedals'
                self.assertFalse(replay.task._handle_weekly_rewards_popup())
                self.assertEqual(replay.actions, [])

    def test_settlement_entry_claims_configured_mystic_reward(self):
        wrong = scene('SETTLING_INFO_ICON', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_MYSTIC',
                      'WEEKLY_REWARDS_SKYSTONE_SELECTED', 'WEEKLY_REWARDS_CLAIM', disabled=('SETTLING_INFO_ICON',))
        ready = scene('SETTLING_INFO_ICON', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_MYSTIC_SELECTED',
                      'WEEKLY_REWARDS_CLAIM', disabled=('SETTLING_INFO_ICON',))
        frames = [wrong] * 4 + [ready] * 4 + [scene('SETTLING_INFO_ICON')]
        with entry_replay(frames, self.clock) as replay:
            replay.task.config.Arena_WeeklyReward = 'MysticMedals'
            self.assertEqual(replay.task._enter_settling_arena(), 'settling_npc')
            replay.assert_retried(self, 'WEEKLY_REWARDS_MYSTIC', 1)
            self.assertTrue(all(index < 4 for index in replay.clicks('WEEKLY_REWARDS_MYSTIC')))
            self.assertTrue(all(4 <= index < 8 for index in replay.clicks('WEEKLY_REWARDS_CLAIM')))
            self.assertEqual(replay.clicks('WEEKLY_REWARDS_SKYSTONE'), [])
            self.assertEqual(replay.index, len(frames) - 1)

    def test_settlement_entry_waits_for_enabled_icon_after_weekly_claim(self):
        hub = scene('ARENA_COMMON_ENTRY', page='page_arena_hub')
        unselected = scene(
            'SETTLING_INFO_ICON', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_CLAIM',
            disabled=('SETTLING_INFO_ICON', 'WEEKLY_REWARDS_CLAIM'),
        )
        selected = scene(
            'SETTLING_INFO_ICON', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_SKYSTONE_SELECTED', 'WEEKLY_REWARDS_CLAIM',
            disabled=('SETTLING_INFO_ICON',),
        )
        frames = [hub] * 4 + [unselected] * 4 + [selected] * 4 + [scene()] + [scene('SETTLING_INFO_ICON')]
        with entry_replay(frames, self.clock) as replay:
            self.assertEqual(replay.task._enter_settling_arena(), 'settling_npc')
            replay.task.ui_goto.assert_called_once_with(page_arena_hub, skip_first_screenshot=True)
            for name in ('ARENA_COMMON_ENTRY', 'WEEKLY_REWARDS_SKYSTONE', 'WEEKLY_REWARDS_CLAIM'):
                replay.assert_retried(self, name, 1)
            self.assertTrue(all(index < 4 for index in replay.clicks('ARENA_COMMON_ENTRY')))
            self.assertTrue(all(4 <= index < 8 for index in replay.clicks('WEEKLY_REWARDS_SKYSTONE')))
            self.assertTrue(all(8 <= index < 12 for index in replay.clicks('WEEKLY_REWARDS_CLAIM')))
            self.assertEqual(replay.clicks('WEEKLY_REWARDS_SKYSTONE_SELECTED'), [])
            self.assertEqual(replay.index, len(frames) - 1)

    def test_settlement_entry_preserves_ready_npc_page(self):
        with entry_replay([scene('SETTLING_INFO_ICON')], self.clock) as replay:
            self.assertEqual(replay.task._enter_settling_arena(), 'settling_npc')
            replay.task.ui_goto.assert_not_called()
            self.assertEqual(replay.actions, [])

    def test_arena_goto_selects_current_settlement_route(self):
        for settling, expected in ((False, 'entered'), (True, 'settling_npc')):
            with self.subTest(settling=settling), entry_replay([scene()], self.clock) as replay:
                replay.task._enter_arena = Mock(return_value='entered')
                replay.task._enter_settling_arena = Mock(return_value='settling_npc')
                with patch('tasks.arena.entry.is_arena_settling_period', return_value=settling):
                    self.assertEqual(replay.task.arena_goto(skip_first_screenshot=False), expected)
                selected = replay.task._enter_settling_arena if settling else replay.task._enter_arena
                unselected = replay.task._enter_arena if settling else replay.task._enter_settling_arena
                selected.assert_called_once_with(skip_first_screenshot=False)
                unselected.assert_not_called()
