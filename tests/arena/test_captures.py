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
from tasks.arena.assets.assets_arena_weekly_rewards import (
    WEEKLY_REWARDS_CLAIM,
    WEEKLY_REWARDS_MYSTIC,
    WEEKLY_REWARDS_MYSTIC_SELECTED,
    WEEKLY_REWARDS_SKYSTONE,
    WEEKLY_REWARDS_SKYSTONE_SELECTED,
)
from tasks.base.resource_bar import RESOURCE_BAR_ICONS, get_resource_bar_segment_area
from tests.support.arena import MANIFEST, ArenaReplay
from tests.support.arena_entry import WeeklyRewardsReplay
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

    def test_flag_counter_is_independent_and_rejects_missing_flag_field(self):
        for missing in (False, True):
            with self.subTest(missing=missing), ControlledClock() as clock:
                replay = ArenaReplay([
                    scene(fixture='20261021-001015-030', flags=(9, 5)),
                ], clock)
                self.device = replay
                layout = ('arena_flag', 'conquest_point')
                offsets, _ = replay.task._match_resource_bar_icons(layout)
                self.assertEqual(set(offsets), set(layout))
                area = get_resource_bar_segment_area(layout, 'arena_flag', icon_offsets=offsets)
                self.assertIsNotNone(area)
                x1, y1, x2, y2 = area
                # Other currencies cannot prevent checking the live flag field.
                currency_start = RESOURCE_BAR_ICONS['conquest_point'].area[2] + offsets['conquest_point'][0]
                replay.image[y1:y2, currency_start:] = 0
                if missing:
                    replay.image[y1:y2, x1:x2] = 0
                self.assertEqual(replay.task._ocr_arena_flag_status(), None if missing else (0, 5))
                self.assertEqual(replay.flags.value, 9 if missing else 0)

    def test_fast_battle_counter_reads_remaining_before_start(self):
        for fixture_id in FIXTURE_CASES['test_fast_battle_counter_reads_remaining_before_start']:
            with self.subTest(fixture=fixture_id), ControlledClock() as clock:
                replay = ArenaReplay([scene(fixture=fixture_id)], clock)
                self.device = replay
                self.assertTrue(replay.task._is_battle_prepare_page())
                self.assertEqual(replay.task._ocr_fast_battle_times(), (9, 1, 10))


class CapturedWeeklyRewardTests(unittest.TestCase):
    def test_mystic_selection_uses_only_outer_right_border(self):
        for fixture, hidden_area, selected in (
            ('20261019-201534-380', (625, 307, 679, 497), True),
            ('20261019-201534-380', (759, 307, 814, 497), False),
            ('20261019-201536-225', None, False),
        ):
            with self.subTest(fixture=fixture, hidden=hidden_area), ControlledClock() as clock:
                replay = WeeklyRewardsReplay([scene(fixture=fixture)], clock)
                self.device = replay
                replay.task.config.Arena_WeeklyReward = 'MysticMedals'
                if hidden_area:
                    left, top, right, bottom = hidden_area
                    replay.image[top:bottom, left:right] = 0
                self.assertEqual(replay.task.appear(WEEKLY_REWARDS_MYSTIC_SELECTED), selected)
                self.assertTrue(replay.task._handle_weekly_rewards_popup())
                action = 'WEEKLY_REWARDS_CLAIM' if selected else 'WEEKLY_REWARDS_MYSTIC'
                self.assertEqual(replay.actions, [(0, action)])

    def test_reward_captures_distinguish_selection_and_claim_availability(self):
        for fixture, popup, skystone, mystic in (
            ('20261019-201526-416', False, False, False),
            ('20261019-201529-443', False, False, False),
            ('20261019-201532-705', True, False, False),
            ('20261019-201534-380', True, False, True),
            ('20261019-201536-225', True, True, False),
        ):
            with self.subTest(fixture=fixture), ControlledClock() as clock:
                replay = WeeklyRewardsReplay([scene(fixture=fixture)], clock)
                self.device = replay
                self.assertEqual(replay.task.appear(WEEKLY_REWARDS_SKYSTONE), popup)
                self.assertEqual(replay.task.appear(WEEKLY_REWARDS_MYSTIC), popup)
                self.assertEqual(replay.task.appear(WEEKLY_REWARDS_SKYSTONE_SELECTED), skystone)
                self.assertEqual(replay.task.appear(WEEKLY_REWARDS_MYSTIC_SELECTED), mystic)
                self.assertEqual(replay.task.match_template_color(
                    WEEKLY_REWARDS_CLAIM, similarity=0.85, threshold=30,
                ), skystone or mystic)

    def test_captured_reward_switch_claims_only_after_configured_selection(self):
        for reward, wrong, ready, target in (
            ('Skystone', '20261019-201534-380', '20261019-201536-225', 'WEEKLY_REWARDS_SKYSTONE'),
            ('MysticMedals', '20261019-201536-225', '20261019-201534-380', 'WEEKLY_REWARDS_MYSTIC'),
        ):
            frames = [scene(fixture='20261019-201529-443')] * 4
            frames += [scene(fixture='20261019-201532-705')] * 4
            frames += [scene(fixture=wrong)] * 4 + [scene(fixture=ready)] * 4
            frames += [scene(fixture='20261015-165622-212')]
            with self.subTest(reward=reward), ControlledClock() as clock:
                replay = WeeklyRewardsReplay(frames, clock)
                self.device = replay
                replay.task.config.Arena_WeeklyReward = reward
                clicked_areas = []
                original_click = replay.click

                def click(button, clicked_areas=clicked_areas, original_click=original_click):
                    clicked_areas.append((button.name, tuple(button.button)))
                    original_click(button)

                with patch.object(replay, 'click', side_effect=click):
                    self.assertEqual(replay.task._enter_arena(), 'entered')
                replay.assert_retried(self, target, 1)
                replay.assert_retried(self, 'WEEKLY_REWARDS_CLAIM', 1)
                self.assertTrue(all(index < 4 for index in replay.clicks('ARENA_COMMON_ENTRY')))
                self.assertTrue(all(4 <= index < 12 for index in replay.clicks(target)))
                self.assertTrue(all(12 <= index < 16 for index in replay.clicks('WEEKLY_REWARDS_CLAIM')))
                opposite = 'WEEKLY_REWARDS_MYSTIC' if reward == 'Skystone' else 'WEEKLY_REWARDS_SKYSTONE'
                self.assertEqual(replay.clicks(opposite), [])
                self.assertEqual(replay.index, len(frames) - 1)
                expected_area = (539, 355, 577, 411) if reward == 'Skystone' else (700, 355, 738, 411)
                self.assertTrue(all(area == expected_area for name, area in clicked_areas if name == target))
