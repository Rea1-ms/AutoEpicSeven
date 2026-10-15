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
            'ARENA_CHECK', 'WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_CLAIM',
            disabled=('ARENA_CHECK', 'WEEKLY_REWARDS_CLAIM'),
        )
        selected_disabled = scene(
            'ARENA_CHECK', 'WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_SELECTED', 'WEEKLY_REWARDS_CLAIM',
            disabled=('ARENA_CHECK', 'WEEKLY_REWARDS_CLAIM'),
        )
        selected_ready = scene(
            'ARENA_CHECK', 'WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_SELECTED', 'WEEKLY_REWARDS_CLAIM',
            disabled=('ARENA_CHECK',),
        )
        frames = [hub] * 5 + [unselected] * 5 + [selected_disabled] * 2
        frames += [selected_ready] * 4 + [scene()] * 2 + [scene('ARENA_CHECK')]
        with entry_replay(frames, self.clock) as replay:
            self.assertEqual(replay.task._enter_arena(), 'entered')
            for name in ('ARENA_COMMON_ENTRY', 'WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_CLAIM'):
                replay.assert_retried(self, name, 1)
            self.assertTrue(all(index < 5 for index in replay.clicks('ARENA_COMMON_ENTRY')))
            self.assertTrue(all(5 <= index < 10 for index in replay.clicks('WEEKLY_REWARDS_CHECK')))
            self.assertTrue(all(12 <= index < 16 for index in replay.clicks('WEEKLY_REWARDS_CLAIM')))
            self.assertEqual(replay.clicks('WEEKLY_REWARDS_SELECTED'), [])
            self.assertEqual(replay.index, len(frames) - 1)
            replay.task.ui_goto.assert_not_called()

    def test_selected_weekly_reward_ignores_disabled_claim(self):
        frame = scene(
            'WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_SELECTED', 'WEEKLY_REWARDS_CLAIM',
            disabled=('WEEKLY_REWARDS_CLAIM',),
        )
        with entry_replay([frame], self.clock) as replay:
            self.assertFalse(replay.task._handle_weekly_rewards_popup())
            self.assertEqual(replay.actions, [])

    def test_weekly_popup_handler_reports_only_actions_and_respects_interval(self):
        frame = scene('WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_SELECTED', 'WEEKLY_REWARDS_CLAIM')
        with entry_replay([frame], self.clock) as replay:
            self.assertTrue(replay.task._handle_weekly_rewards_popup())
            self.assertFalse(replay.task._handle_weekly_rewards_popup())
            self.assertEqual(replay.actions, [(0, 'WEEKLY_REWARDS_CLAIM')])
            self.clock.advance(1.1)
            self.assertTrue(replay.task._handle_weekly_rewards_popup())
            self.assertEqual(replay.actions, [(0, 'WEEKLY_REWARDS_CLAIM')] * 2)
            self.assertEqual(replay.index, 0)

    def test_settlement_entry_waits_for_enabled_icon_after_weekly_claim(self):
        hub = scene('ARENA_COMMON_ENTRY', page='page_arena_hub')
        unselected = scene(
            'SETTLING_INFO_ICON', 'WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_CLAIM',
            disabled=('SETTLING_INFO_ICON', 'WEEKLY_REWARDS_CLAIM'),
        )
        selected = scene(
            'SETTLING_INFO_ICON', 'WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_SELECTED', 'WEEKLY_REWARDS_CLAIM',
            disabled=('SETTLING_INFO_ICON',),
        )
        frames = [hub] * 4 + [unselected] * 4 + [selected] * 4 + [scene()] + [scene('SETTLING_INFO_ICON')]
        with entry_replay(frames, self.clock) as replay:
            self.assertEqual(replay.task._enter_settling_arena(), 'settling_npc')
            replay.task.ui_goto.assert_called_once_with(page_arena_hub, skip_first_screenshot=True)
            for name in ('ARENA_COMMON_ENTRY', 'WEEKLY_REWARDS_CHECK', 'WEEKLY_REWARDS_CLAIM'):
                replay.assert_retried(self, name, 1)
            self.assertTrue(all(index < 4 for index in replay.clicks('ARENA_COMMON_ENTRY')))
            self.assertTrue(all(4 <= index < 8 for index in replay.clicks('WEEKLY_REWARDS_CHECK')))
            self.assertTrue(all(8 <= index < 12 for index in replay.clicks('WEEKLY_REWARDS_CLAIM')))
            self.assertEqual(replay.clicks('WEEKLY_REWARDS_SELECTED'), [])
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
