"""Actual arena pixels, including challenge controls below a fast-battle overlay."""

import json
import unittest
from unittest.mock import patch

from module.config import server

server.set_lang('global_cn')

from tasks.arena.assets.assets_arena import (
    AUTO_BATTLE_RESULT_CONFIRM,
    BATTLE_START,
    CHALLENGE,
    FAST_BATTLE_LOCKED,
    FAST_BATTLE_OFF,
    FAST_BATTLE_ON,
    FAST_BATTLE_RESULT_CONFIRM,
    NPC_OPPONENT,
)
from tests.support.arena import MANIFEST, ArenaReplay
from tests.support.interaction import scene
from tests.support.offline import ControlledClock

FIXTURE_CASES = {
    'test_challenge_ready_requires_bright_list_button': 'all',
    'test_fast_battle_animation_keeps_challenge_visible_under_overlay': [
        '20261015-165612-968', '20261015-165613-439',
        '20261015-165601-001', '20261015-165606-908',
    ],
    'test_fast_prepare_controls_are_separate_from_list_and_result': 'all',
    'test_shifted_fast_result_confirm_matches_and_clicks_current_position': 'all',
    'test_arena_home_rejects_fast_animation_and_accepts_return_highlight': 'all',
    'test_fast_battle_counter_reads_remaining_before_start': [
        '20261015-165606-908', '20261015-165612-396',
    ],
}


class CapturedArenaTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.captures = json.loads(MANIFEST.read_text(encoding='utf-8'))['fixtures']

    def test_challenge_ready_requires_bright_list_button(self):
        for fixture_id, item in self.captures.items():
            with self.subTest(scene=item['scene']), ControlledClock() as clock:
                replay = ArenaReplay([scene(fixture=fixture_id)], clock)
                self.device = replay
                expected = item['scene'] in (
                    'npc_list_challenge_ready', 'npc_list_next_challenge_ready',
                )
                self.assertEqual(replay.task._is_challenge_ready(), expected)
                if item['scene'] == 'npc_list_unselected':
                    self.assertTrue(replay.task.appear(NPC_OPPONENT))
                    self.assertTrue(replay.task._is_arena_combat_home_ready())
                    self.assertTrue(replay.task._is_challenge_exhausted())

    def test_fast_battle_animation_keeps_challenge_visible_under_overlay(self):
        for fixture_id in ('20261015-165612-968', '20261015-165613-439'):
            with self.subTest(fixture=fixture_id), ControlledClock() as clock:
                replay = ArenaReplay([scene(fixture=fixture_id)], clock)
                self.device = replay
                # A dimmed challenge is still a template match during battle.
                # Its presence cannot authorize selection or end the round.
                self.assertTrue(replay.task.appear(CHALLENGE))
                self.assertFalse(replay.task._is_challenge_ready())
                self.assertFalse(replay.task._is_arena_combat_home_ready())
                self.assertFalse(replay.task._is_battle_prepare_page())
                self.assertFalse(replay.task.appear(NPC_OPPONENT))
                self.assertFalse(replay.task.appear(FAST_BATTLE_RESULT_CONFIRM))

        with ControlledClock() as clock:
            listed = ArenaReplay([scene(fixture='20261015-165601-001')], clock)
            self.device = listed
            self.assertTrue(listed.task.appear(CHALLENGE))
            self.assertTrue(listed.task._is_arena_combat_home_ready())
            self.assertTrue(listed.task._is_challenge_ready())
            prepared = ArenaReplay([scene(fixture='20261015-165606-908')], clock)
            self.device = prepared
            self.assertFalse(prepared.task.appear(CHALLENGE))
            self.assertTrue(prepared.task._is_battle_prepare_page())

    def test_fast_prepare_controls_are_separate_from_list_and_result(self):
        for fixture_id, item in self.captures.items():
            with self.subTest(scene=item['scene']), ControlledClock() as clock:
                replay = ArenaReplay([scene(fixture=fixture_id)], clock)
                self.device = replay
                expected = item['scene'].startswith('fast_battle_prepare')
                self.assertEqual(replay.task._is_battle_prepare_page(), expected)
                self.assertEqual(replay.task.appear(BATTLE_START), expected)
                self.assertEqual(replay.task.appear(FAST_BATTLE_ON), expected)
                self.assertFalse(replay.task.appear(FAST_BATTLE_OFF))
                self.assertFalse(replay.task.appear(FAST_BATTLE_LOCKED))

    def test_shifted_fast_result_confirm_matches_and_clicks_current_position(self):
        for fixture_id, item in self.captures.items():
            with self.subTest(scene=item['scene']), ControlledClock() as clock:
                replay = ArenaReplay([scene(fixture=fixture_id)], clock)
                self.device = replay
                expected = item['scene'].startswith('fast_battle_result')
                # Read the target at click time: later negative matches can
                # update the shared wrapper offset even when they return False.
                clicked_areas = []
                original_click = replay.click

                def click(button, clicked_areas=clicked_areas, original_click=original_click):
                    clicked_areas.append(tuple(button.button))
                    original_click(button)

                with patch.object(replay, 'click', side_effect=click):
                    matched = replay.task.appear_then_click(
                        FAST_BATTLE_RESULT_CONFIRM, interval=0.8,
                    )
                self.assertEqual(matched, expected)
                self.assertEqual(replay.actions, [(0, 'FAST_BATTLE_RESULT_CONFIRM')] if expected else [])
                self.assertEqual(clicked_areas, [(726, 627, 766, 647)] if expected else [])
                self.assertFalse(replay.task.appear(AUTO_BATTLE_RESULT_CONFIRM))

    def test_arena_home_rejects_fast_animation_and_accepts_return_highlight(self):
        for fixture_id, item in self.captures.items():
            with self.subTest(scene=item['scene']), ControlledClock() as clock:
                replay = ArenaReplay([scene(fixture=fixture_id)], clock)
                self.device = replay
                expected = item['scene'].startswith('npc_list_')
                self.assertEqual(replay.task._is_arena_combat_home_ready(), expected)
                if item['scene'] == 'npc_list_return_highlight':
                    # The return flash obscures the opponent badge while the
                    # unobstructed home marker already proves the round ended.
                    self.assertFalse(replay.task.appear(NPC_OPPONENT))
                    self.assertTrue(replay.task.appear(CHALLENGE))

    def test_fast_battle_counter_reads_remaining_before_start(self):
        for fixture_id in FIXTURE_CASES['test_fast_battle_counter_reads_remaining_before_start']:
            with self.subTest(fixture=fixture_id), ControlledClock() as clock:
                replay = ArenaReplay([scene(fixture=fixture_id)], clock)
                self.device = replay
                self.assertTrue(replay.task._is_battle_prepare_page())
                self.assertEqual(replay.task._ocr_fast_battle_times(), (9, 1, 10))
