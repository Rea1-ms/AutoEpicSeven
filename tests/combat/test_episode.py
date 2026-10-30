"""Episode plans, stage parsing, and fast-combat rules."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server

server.set_lang('global_cn')

from module.base.button import ClickButton
from module.exception import RequestHumanTakeover
from tasks.base.assets.assets_base_popup import NETWORK_ERROR_DISCONNECT
from tasks.dungeon.assets.assets_dungeon_action import (
    CHOOSE_TEAM,
    COMBAT_RESULT_CONFIRM,
    COMBAT_START,
)
from tasks.dungeon.assets.assets_dungeon_configs_episode_entry import (
    EPISODE_STAGE_SEARCH,
)
from tasks.dungeon.assets.assets_dungeon_episode_battle import (
    EPISODE_AUTO_COMBAT_UNAVAILABLE,
    EPISODE_BATTLE_CONTROLS_CHECK,
    EPISODE_STORY_TEAM_CHECK,
    OCR_EPISODE_LAST_STAR_CONDITION,
)
from tasks.dungeon.assets.assets_dungeon_episode_map import (
    EPISODE_EXPLORATION_CHECK,
    OCR_EPISODE_STAGE_LIST,
)
from tasks.dungeon.assets.assets_dungeon_episode_reward import (
    EPISODE_CHAPTER_REWARD,
    EPISODE_STORY_COMPLETE,
)
from tasks.dungeon.assets.assets_dungeon_episode_story import EPISODE_STORY_SKIP_CHECK
from tasks.dungeon.assets.assets_dungeon_popup import PACKAGE_FULL
from tasks.dungeon.assets.assets_dungeon_repeat_common import REPEAT_COMBAT_MENU
from tasks.dungeon.assets.assets_dungeon_state import (
    AUTO_COMBAT_SKILL_OPENED,
    COMBAT_RESULT_CLEAR,
    COMBAT_RESULT_FAILED,
    ENEMY_NUM_EXIST,
)
from tasks.dungeon.dungeon import Combat
from tasks.dungeon.episode import EPISODE4_MATERIAL_PLANS, EpisodeNavigateMixin
from tasks.dungeon.episode_push import (
    Episode,
    StoryStage,
    parse_stage_index,
    parse_story_stamina,
)
from tests.support.episode import MANIFEST, Episode4Replay, EpisodeReplay
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ControlledClock, fixture_image


class _DummyEpisode(EpisodeNavigateMixin):
    pass


class LegacyRuleTests(unittest.TestCase):
    def test_episode4_material_plan_mapping(self):
        self.assertEqual(EPISODE4_MATERIAL_PLANS["CATALYST_RARE_BENEVOLENT"].stage_label, "1-9")
        self.assertEqual(EPISODE4_MATERIAL_PLANS["BREATH_OF_KARMA"].stage_label, "6-10")
        self.assertEqual(EPISODE4_MATERIAL_PLANS["TRACES_OF_BRILLIANCE"].stage_label, "10-9")
        self.assertEqual(EPISODE4_MATERIAL_PLANS["CATALYST_RARE_SECRET"].stage_label, "2-10")

    def test_extract_episode_number(self):
        dummy = _DummyEpisode()
        self.assertEqual(dummy._extract_episode_number("1. 不愉快的第一印象"), 1)
        self.assertEqual(dummy._extract_episode_number("10. 终末之地"), 10)
        self.assertEqual(dummy._extract_episode_number("第4章"), 4)
        self.assertEqual(dummy._extract_episode_number("无有效数字"), 0)

    def test_episode4_supports_fast_combat(self):
        episode4 = Combat.__new__(Combat)
        episode4._dungeon_domain = lambda: "Episode4"
        episode4._combat_grade = lambda: "5-5"
        self.assertIs(episode4._combat_supports_fast_combat(), True)

        saint37 = Combat.__new__(Combat)
        saint37._dungeon_domain = lambda: "Saint37"
        saint37._combat_grade = lambda: "3-7"
        self.assertIs(saint37._combat_supports_fast_combat(), False)

    def test_episode_target_above_current_should_reset_to_top(self):
        target = EPISODE4_MATERIAL_PLANS["CATALYST_RARE_OATH"]
        current_stage = 7
        self.assertIs(current_stage > target.stage, True)

    def test_episode_target_equal_current_should_not_reset_to_top(self):
        target = EPISODE4_MATERIAL_PLANS["CATALYST_RARE_OATH"]
        current_stage = 5
        self.assertIs(current_stage > target.stage, False)


class EpisodeMaterialRuleTests(unittest.TestCase):
    def test_all_catalyst_targets_match_user_table(self):
        from tasks.dungeon.episode import EPISODE_MATERIAL_PLANS
        from tasks.dungeon.episode_materials import EPISODE_MATERIAL_STAGES

        # An independent transcription of the supplied 54 destinations catches
        # swapped material names, missing episodes and lost special suffixes.
        rows = {
            'EPIC_FIGHTING_SPIRIT': (None, '6-7', '10-7', '8-7', '7-6'),
            'EPIC_OATH': (None, '5-12', '8-6', '6-8', '8S-7'),
            'EPIC_SECRET': (None, '9-2', '7-2', '10-6', '10S-4'),
            'EPIC_SNIPER': (None, '10-3', '8-7', '7-10', '10-5'),
            'EPIC_MYSTERIOUS': (None, '7-10', '7-10', '9-10', '9-6'),
            'EPIC_BENEVOLENT': (None, '8-10', '9-10', '9-7', '8-7'),
            'RARE_FIGHTING_SPIRIT': ('1-5', '3-10', '6-9', '3-10', '6-7'),
            'RARE_OATH': ('2-5', '2-9', '2-8', '5-5', '1-7'),
            'RARE_SECRET': ('3-5', '5-6', '4-7', '2-10', '2-8'),
            'RARE_SNIPER': ('4-4', '1-10', '3-10', '4-8', '5-7'),
            'RARE_MYSTERIOUS': ('5-9', '4-5', '1-8', '5-10', '3-6'),
            'RARE_BENEVOLENT': ('6-6', '4-10', '5-10', '1-9', '4-7'),
        }
        expected = {
            episode: {f'CATALYST_{key}': targets[episode - 1] for key, targets in rows.items()
                      if targets[episode - 1] is not None}
            for episode in range(1, 6)
        }
        self.assertEqual(sum(map(len, expected.values())), 54)
        self.assertEqual(set(EPISODE_MATERIAL_PLANS), set(expected))
        for episode, targets in expected.items():
            with self.subTest(episode=episode):
                actual = {key: value.stage_label for key, value in EPISODE_MATERIAL_PLANS[episode].items()
                          if key.startswith('CATALYST_')}
                self.assertEqual(actual, targets)
                data = {key: f'{chapter}-{stage}' for key, (chapter, stage)
                        in EPISODE_MATERIAL_STAGES[episode].items() if key.startswith('CATALYST_')}
                self.assertEqual(data, targets)

    def test_legacy_default_and_growth_targets_remain_episode_four(self):
        from tasks.dungeon.episode import EPISODE_MATERIAL_PLANS

        dummy = _DummyEpisode()
        dummy.config = SimpleNamespace()
        self.assertIs(dummy._combat_episode_target(), EPISODE4_MATERIAL_PLANS['BREATH_OF_KARMA'])
        self.assertIs(EPISODE4_MATERIAL_PLANS, EPISODE_MATERIAL_PLANS[4])
        growth = {'HEART_OF_THE_WOODS': '4-10', 'BREATH_OF_KARMA': '6-10',
                  'FROZEN_SEED': '8-9', 'TRACES_OF_BRILLIANCE': '10-9'}
        for key, label in growth.items():
            dummy.config.Combat_Episode4Material = key
            self.assertEqual(dummy._combat_episode_target().stage_label, label)
            for episode in (1, 2, 3, 5):
                self.assertNotIn(key, EPISODE_MATERIAL_PLANS[episode])

    def test_unsupported_material_combination_stops_before_actions(self):
        for episode, material in ((1, 'CATALYST_EPIC_OATH'), (2, 'BREATH_OF_KARMA'),
                                  (3, 'FROZEN_SEED'), (5, 'HEART_OF_THE_WOODS'),
                                  (6, 'CATALYST_RARE_OATH'), (4, 'UNKNOWN_MATERIAL')):
            with self.subTest(episode=episode, material=material), ControlledClock() as clock:
                replay = Episode4Replay([scene('READY_TO_FIGHT', prepare=True)], clock,
                                        episode=episode, material=material)
                self.device = replay
                with self.assertRaises(RequestHumanTakeover):
                    replay.task._navigate_episode4()
                self.assertEqual(replay.actions, [])

    def test_chapter_ocr_preserves_special_suffix(self):
        dummy = _DummyEpisode()
        for text, expected in (('第8章 相会', 8), ('第8S章 相会', '8S'),
                               ('第8 S章 相会', '8S'), ('第10章 不完整的意义', 10),
                               ('第10S章 连接', '10S'), ('第10s章 连接', '10S'),
                               ('第105章', 0), ('第110章', 0), ('第10SS章', 0),
                               ('', 0), ('没有章节编号', 0)):
            with self.subTest(text=text), patch.object(dummy, '_ocr_episode_chapter_text', return_value=text):
                self.assertEqual(dummy._ocr_episode_chapter_index(), expected)

    def test_episode_stamina_costs_limit_fast_combat_counts(self):
        for episode, cost, count in ((1, 8, 4), (2, 12, 3), (3, 16, 2), (4, 20, 1), (5, 20, 1)):
            with self.subTest(episode=episode), ControlledClock():
                combat = Combat.__new__(Combat)
                combat.config = SimpleNamespace(Combat_EpisodeNumber=episode, Combat_FastCombatCount=10)
                combat._dungeon_domain = lambda: 'Episode4'
                combat._handle_dungeon_additional = lambda: False
                combat._is_prepare_page = lambda: True
                combat._is_fast_combat_locked = lambda: False
                combat._ensure_fast_combat_state = lambda **kwargs: True
                combat._ocr_fast_combat_remaining_times = lambda: 10
                combat._set_prepare_count = Mock(return_value=True)
                self.assertEqual(combat._combat_stage_stamina_cost(), cost)
                self.assertTrue(combat._combat_supports_fast_combat())
                self.assertEqual(combat._prepare_fast_combat(stamina=39), ('ready', count))
                self.assertEqual(combat._set_prepare_count.call_args.args[0], count)
                combat._set_prepare_count.reset_mock()
                self.assertEqual(combat._prepare_fast_combat(stamina=cost - 1), ('no_stamina', 0))
                combat._set_prepare_count.assert_not_called()


class EpisodeMaterialNavigationTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_map_identity_is_read_once_until_a_chapter_arrow_changes_it(self):
        frames = [scene('NEXT_CHAPTER', map_ready=True, episode=2, chapter=6),
                  scene('CATALYST_EPIC_MYSTERIOUS_INDEX', map_ready=True,
                        episode=2, chapter=7, stage=7, seconds=1.3),
                  scene(map_ready=True, episode=2, chapter=7, stage=7, seconds=.2),
                  scene('READY_TO_FIGHT', map_ready=True, episode=2, chapter=7,
                        stage=10, material=True, seconds=1.1),
                  scene('READY_TO_FIGHT', map_ready=True, episode=2, chapter=7,
                        stage=10, material=True, seconds=.2), scene(prepare=True)]
        with ControlledClock() as clock:
            replay = Episode4Replay(frames, clock, episode=2, material='CATALYST_EPIC_MYSTERIOUS')
            self.device = replay
            with patch.object(replay.task, '_ocr_episode_number', wraps=replay.episode_number) as episode, \
                    patch.object(replay.task, '_ocr_episode_chapter_index', wraps=replay.chapter_index) as chapter:
                self.assertTrue(replay.task._navigate_episode4())
            self.assertEqual(episode.call_count, 1)
            self.assertEqual(chapter.call_count, 2)
            self.assertEqual(replay.actions, [(0, 'NEXT_CHAPTER'),
                                             (1, 'CATALYST_EPIC_MYSTERIOUS_INDEX'),
                                             (3, 'READY_TO_FIGHT')])
            self.assertEqual(replay.index, 5)

    def test_failed_selector_navigation_can_return_to_main(self):
        from tasks.dungeon.runtime import CombatRuntimeMixin

        frames = [scene('BACK', selector=5), scene('BACK', selector=5, seconds=.4),
                  scene('BACK', selector=5, seconds=.7),
                  scene('BACK', map_ready=True, episode=5, seconds=1.1), scene(main=True)]
        with ControlledClock() as clock:
            replay = Episode4Replay(frames, clock)
            self.device = replay
            replay.task.COMBAT_EXIT_TIMEOUT_SECONDS = Combat.COMBAT_EXIT_TIMEOUT_SECONDS
            # Episode's shared farming replay does not inherit unrelated
            # dungeon-page recognizers. These explicit absent observations
            # leave the selector and map recognizers as the only exit path.
            for name in ('_is_stage_page', '_is_combat_general_board', '_is_combat_season_board',
                         '_is_combat_urgent_board', '_is_side_story_page', '_is_time_book_page',
                         '_is_episode_preview_page', '_is_side_story_map_page', '_is_supporter_page',
                         '_is_urgent_tasks_detail_page'):
                setattr(replay.task, name, lambda: False)
            self.assertTrue(CombatRuntimeMixin._leave_to_main(replay.task))
            self.assertEqual(replay.actions, [(0, 'BACK'), (2, 'BACK'), (3, 'BACK')])
            replay.assert_retried(self, 'BACK', 1)
            self.assertEqual(replay.index, 4)

    def test_captured_chapter_ocr_distinguishes_ten_and_ten_special(self):
        for stamp, chapter in (('20261028-161745-685', 10), ('20261028-161748-124', '10S')):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = Episode4Replay([scene(fixture=stamp)], clock, episode=5)
                self.device = replay
                self.assertEqual(replay.task._ocr_episode_number(), 5)
                self.assertEqual(replay.task._ocr_episode_chapter_index(), chapter)
                self.assertEqual(replay.actions, [])

    def test_each_episode_uses_configured_entry_before_material_selection(self):
        selectors = {1: '20261021-220739-548', 2: '20261021-220738-490',
                     3: '20261021-220737-336', 4: '20261021-220735-802',
                     5: '20261021-220734-052'}
        # These map/prepare observations are synthetic. Only the episode tabs,
        # selected titles and enterable stars use captured pixels.
        chapters = {1: 2, 2: 2, 3: 2, 4: 5, 5: 1}
        for episode, stamp in selectors.items():
            frames = [scene(fixture='20261021-220728-697'), scene(fixture=stamp),
                      scene('READY_TO_FIGHT', map_ready=True, episode=episode,
                            chapter=chapters[episode], material=True), scene(prepare=True)]
            with self.subTest(episode=episode), ControlledClock() as clock:
                replay = Episode4Replay(frames, clock, episode=episode, material='CATALYST_RARE_OATH')
                self.device = replay
                self.assertTrue(replay.task._navigate_episode4())
                self.assertEqual(replay.actions, [(0, f'GOTO_EPISODE_{episode}'),
                                                 (1, 'EPISODE_CHAPTER_STAR_result0'),
                                                 (2, 'READY_TO_FIGHT')])

    def test_special_chapters_use_confirmed_order_in_both_directions(self):
        for material, current, target, direction in (
            ('CATALYST_EPIC_OATH', 8, '8S', 'NEXT_CHAPTER'),
            ('CATALYST_EPIC_OATH', 9, '8S', 'LAST_CHAPTER'),
            ('CATALYST_EPIC_SECRET', 10, '10S', 'NEXT_CHAPTER'),
            ('CATALYST_EPIC_SNIPER', '10S', 10, 'LAST_CHAPTER'),
            ('CATALYST_EPIC_MYSTERIOUS', '8S', 9, 'NEXT_CHAPTER'),
            ('CATALYST_EPIC_BENEVOLENT', '8S', 8, 'LAST_CHAPTER'),
        ):
            frames = [scene(direction, 'READY_TO_FIGHT', map_ready=True, episode=5,
                            chapter=current, material=True),
                      scene('READY_TO_FIGHT', map_ready=True, episode=5,
                            chapter=target, material=True, seconds=1.3), scene(prepare=True)]
            with self.subTest(current=current, target=target), ControlledClock() as clock:
                replay = Episode4Replay(frames, clock, episode=5, material=material)
                self.device = replay
                self.assertTrue(replay.task._navigate_episode4())
                self.assertEqual(replay.actions, [(0, direction), (1, 'READY_TO_FIGHT')])

    def test_repeated_chapter_frames_never_start_wrong_material(self):
        frames = [scene('NEXT_CHAPTER', 'READY_TO_FIGHT', 'CATALYST_EPIC_SECRET_INDEX',
                        map_ready=True, episode=5, chapter=10, material=True, seconds=seconds)
                  for seconds in (.5, .5, .8, .5)]
        frames += [scene('READY_TO_FIGHT', map_ready=True, episode=5, chapter='10S',
                         material=True, seconds=1.3), scene(prepare=True)]
        with ControlledClock() as clock:
            replay = Episode4Replay(frames, clock, episode=5, material='CATALYST_EPIC_SECRET')
            self.device = replay
            self.assertTrue(replay.task._navigate_episode4())
            self.assertEqual(replay.actions, [(0, 'NEXT_CHAPTER'), (2, 'NEXT_CHAPTER'),
                                             (4, 'READY_TO_FIGHT')])
            replay.assert_retried(self, 'NEXT_CHAPTER', 1)

    def test_ignored_special_chapter_switch_times_out_without_material_actions(self):
        frames = [scene('NEXT_CHAPTER', 'READY_TO_FIGHT', 'CATALYST_EPIC_SECRET_INDEX',
                        map_ready=True, episode=5, chapter=10, material=True, seconds=15.1)
                  for _ in range(4)]
        with ControlledClock() as clock:
            replay = Episode4Replay(frames, clock, episode=5, material='CATALYST_EPIC_SECRET')
            self.device = replay
            self.assertFalse(replay.task._navigate_episode4())
            self.assertEqual(replay.actions, [(0, 'NEXT_CHAPTER'), (1, 'NEXT_CHAPTER'), (2, 'NEXT_CHAPTER')])

    def test_material_stage_selection_keeps_four_branch_priority(self):
        index = 'CATALYST_RARE_FIGHTING_SPIRIT_INDEX'
        common = {'map_ready': True, 'episode': 5, 'chapter': 6}
        ready = scene('READY_TO_FIGHT', index, stage=7, material=True, seconds=1.1, **common)
        scenarios = (
            ([scene('READY_TO_FIGHT', index, stage=8, material=True, **common)],
             [(0, 'READY_TO_FIGHT')]),
            ([scene(index, stage=8, **common), scene(index, stage=1, seconds=1.1, **common), ready],
             [(0, 'swipe'), (1, index), (2, 'READY_TO_FIGHT')]),
            ([scene(index, stage=6, **common), ready], [(0, index), (1, 'READY_TO_FIGHT')]),
            ([scene(stage=6, **common), scene(index, stage=6, seconds=1.1, **common), ready],
             [(0, 'EPISODE_STAGE_SEARCH_LOWER_THIRD'), (1, index), (2, 'READY_TO_FIGHT')]),
        )
        for frames, expected in scenarios:
            with self.subTest(expected=expected), ControlledClock() as clock:
                replay = Episode4Replay([*frames, scene(prepare=True)], clock,
                                        episode=5, material='CATALYST_RARE_FIGHTING_SPIRIT')
                self.device = replay
                self.assertTrue(replay.task._navigate_episode4())
                self.assertEqual(replay.actions, expected)


class EpisodeFastOnlyTests(unittest.TestCase):
    def test_captured_fast_result_is_not_locked_prepare_and_exhausted_prepare_is(self):
        server.set_lang('global_cn')
        task = Combat.__new__(Combat)
        task.config = SimpleNamespace(Emulator_GameLanguage='cn')
        task.interval_timer = {}
        for stamp, prepare in (('20261028-174355-065', False), ('20261028-174449-194', True)):
            with self.subTest(stamp=stamp):
                task.device = SimpleNamespace(image=fixture_image(MANIFEST, stamp),
                                              stuck_record_add=lambda *args: None)
                self.assertEqual(task._is_prepare_page(), prepare)
                self.assertEqual(task._is_repeat_combat_unavailable(), prepare)
                if prepare:
                    task.appear_then_click = Mock(side_effect=AssertionError('No repeat click'))
                    resources = task._update_standard_prepare_resource_snapshot()
                    self.assertEqual(resources['stamina'].value, 701)
                    task._update_prepare_resource_snapshot()
                    task.appear_then_click.assert_not_called()
                    self.assertTrue(task._is_fast_combat_locked())

    def prepare_run(self, clock):
        server.set_lang('global_cn')
        replay = InteractionReplay(Combat, [scene('REPEAT_COMBAT_UNAVAILABLE')], clock)
        self.device = replay
        task = replay.task
        task.config.Combat_Domain = 'Episode4'
        task.config.Combat_EpisodeNumber = 2
        task.config.Combat_Episode4Material = 'CATALYST_EPIC_MYSTERIOUS'
        task.config.UrgentTasks_Enable = False
        task.match_color = replay.appear
        task._prepare_background_repeat_check_context = Mock()
        task._adopt_existing_background_repeat_combat = Mock()
        task._combat_runtime_active = Mock(return_value=False)
        task._prepare_rune_balance_target = Mock()
        task._dungeon_navigate = Mock(return_value=True)
        task.ocr_resource_bar_status = Mock(return_value={'stamina': SimpleNamespace(value=60)})
        task._prepare_fast_combat = Mock(return_value=('ready', 5))
        task._run_fast_combat = Mock(return_value=True)
        task._prepare_repeat_combat = Mock(side_effect=AssertionError('Locked repeat must not open'))
        task._run_repeat_combat = Mock(side_effect=AssertionError('Locked repeat must not start'))
        task._leave_to_main = Mock(return_value=True)
        task._combat_should_call_mission_reward = Mock(return_value=False)
        task._combat_runtime_clear = Mock()
        task._rune_balance_after_settled = Mock()
        task._combat_delay_after_settled = Mock()
        return task

    def test_locked_repeat_uses_top_resources_and_finishes_available_fast_combat(self):
        with ControlledClock() as clock:
            task = self.prepare_run(clock)
            self.assertTrue(task.run())
            task.ocr_resource_bar_status.assert_called_once()
            self.assertEqual(task.ocr_resource_bar_status.call_args.kwargs['layout'],
                             ('stamina', 'gold', 'skystone'))
            task._prepare_fast_combat.assert_called_once_with(
                stamina=60, use_max=False, skip_first_screenshot=True)
            task._run_fast_combat.assert_called_once_with(skip_first_screenshot=True)
            task._prepare_repeat_combat.assert_not_called()
            task._run_repeat_combat.assert_not_called()
            task._leave_to_main.assert_called_once_with(skip_first_screenshot=True)
            task._combat_delay_after_settled.assert_called_once()
            self.assertEqual(self.device.actions, [])

    def test_locked_repeat_and_unusable_fast_combat_do_not_claim_completion(self):
        for state in ('exhausted', 'disabled', 'locked'):
            with self.subTest(state=state), ControlledClock() as clock:
                task = self.prepare_run(clock)
                if state == 'exhausted':
                    task._prepare_fast_combat.return_value = ('fallback', 0)
                elif state == 'disabled':
                    task.config.Combat_FastCombat = False
                else:
                    task._is_fast_combat_locked = Mock(return_value=True)
                with self.assertRaises(RequestHumanTakeover):
                    task.run()
                task._run_fast_combat.assert_not_called()
                task._prepare_repeat_combat.assert_not_called()
                task._run_repeat_combat.assert_not_called()
                task._combat_delay_after_settled.assert_not_called()
                self.assertEqual(self.device.actions, [])


def frontier(index=3, **kwargs):
    return scene(map_ready=True, selected=index, rows=[StoryStage(index, (95, 218, 300, 246), False, False)], **kwargs)


class EpisodeNavigationReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_daily_captured_entry_then_ocr_chapter_selection_reaches_prepare(self):
        # The captured entry pages are real; Episode 4's map, chapter changes
        # and destination prepare are explicit simulated states, not evidence
        # of a continuous emulator run or of the material matcher itself.
        frames = [scene(fixture='20261021-220726-466'),
                  scene(fixture='20261021-220726-466', seconds=1),
                  scene(fixture='20261021-220726-466', seconds=1.1),
                  scene(fixture='20261021-220728-697'),
                  scene(fixture='20261021-220728-697', seconds=1),
                  scene(fixture='20261021-220728-697', seconds=1.1),
                  scene(fixture='20261021-220735-802'),
                  scene(fixture='20261021-220735-802', seconds=1),
                  scene(fixture='20261021-220735-802', seconds=1.1),
                  scene('LAST_CHAPTER', map_ready=True, episode=4, chapter=7),
                  scene('LAST_CHAPTER', map_ready=True, episode=4, chapter=7, seconds=.5),
                  scene('BREATH_OF_KARMA_INDEX', map_ready=True, episode=4, chapter=6, stage=9, seconds=1),
                  scene('BREATH_OF_KARMA_INDEX', map_ready=True, episode=4, chapter=6, stage=9),
                  scene('READY_TO_FIGHT', map_ready=True, episode=4, chapter=6, stage=10,
                        material=True, seconds=1.1),
                  scene('SUPPORTER_CHECK', 'CHOOSE_TEAM'), scene(prepare=True)]
        with ControlledClock() as clock:
            replay = Episode4Replay(frames, clock)
            self.device = replay
            self.assertTrue(replay.task._navigate_episode4())
            self.assertEqual(replay.actions, [
                (0, 'EPISODE_SELECTOR_ENTRY'), (2, 'EPISODE_SELECTOR_ENTRY'),
                (3, 'GOTO_EPISODE_4'), (5, 'GOTO_EPISODE_4'),
                (6, 'EPISODE_CHAPTER_STAR_result0'), (8, 'EPISODE_CHAPTER_STAR_result0'),
                (9, 'LAST_CHAPTER'), (11, 'BREATH_OF_KARMA_INDEX'),
                (13, 'READY_TO_FIGHT'), (14, 'CHOOSE_TEAM'),
            ])
            for name in ('EPISODE_SELECTOR_ENTRY', 'GOTO_EPISODE_4', 'EPISODE_CHAPTER_STAR_result0'):
                replay.assert_retried(self, name, 2)
            for index, name, area in replay.positions:
                if name == 'EPISODE_CHAPTER_STAR_result0':
                    self.assertTrue(414 <= area[0] < area[2] <= 440, (index, area))
                    self.assertTrue(289 <= area[1] < area[3] <= 315, (index, area))

    def test_daily_correct_episode_changes_only_chapter_before_material_actions(self):
        frames = [scene('NEXT_CHAPTER', map_ready=True, episode=4, chapter=5, material=True),
                  scene('READY_TO_FIGHT', map_ready=True, episode=4, chapter=6, material=True, seconds=1.3),
                  scene(prepare=True)]
        with ControlledClock() as clock:
            replay = Episode4Replay(frames, clock)
            self.device = replay
            self.assertTrue(replay.task._navigate_episode4())
            self.assertEqual(replay.actions, [(0, 'NEXT_CHAPTER'), (1, 'READY_TO_FIGHT')])

    def test_daily_unreadable_episode_or_chapter_never_selects_material(self):
        for episode, chapter in ((None, 6), (4, 0)):
            frames = [scene('READY_TO_FIGHT', 'BREATH_OF_KARMA_INDEX', map_ready=True,
                            episode=episode, chapter=chapter, material=True, seconds=15.1)
                      for _ in range(4)]
            with self.subTest(episode=episode, chapter=chapter), ControlledClock() as clock:
                replay = Episode4Replay(frames, clock)
                self.device = replay
                self.assertFalse(replay.task._navigate_episode4())
                self.assertEqual(replay.actions, [])

    def test_daily_missing_or_ignored_selector_controls_have_bounded_retries(self):
        for buttons in ((), ('EPISODE_SELECTOR_ENTRY',)):
            frames = [scene(*buttons, map_ready=True, episode=6, chapter=6, seconds=15.1) for _ in range(4)]
            with self.subTest(buttons=buttons), ControlledClock() as clock:
                replay = Episode4Replay(frames, clock)
                self.device = replay
                self.assertFalse(replay.task._navigate_episode4())
                expected = [(index, 'EPISODE_SELECTOR_ENTRY') for index in range(3)] if buttons else []
                self.assertEqual(replay.actions, expected)

    def test_daily_missing_star_never_reuses_old_coordinates(self):
        first = ClickButton((418, 292, 432, 312), name='STAR_FIRST')
        second = ClickButton((778, 292, 792, 312), name='STAR_SECOND')
        frames = [scene(selector=4, chapter_entries=[first, second])]
        frames += [scene(selector=4, chapter_entries=[second], seconds=15.1) for _ in range(3)]
        with ControlledClock() as clock:
            replay = Episode4Replay(frames, clock)
            self.device = replay
            self.assertFalse(replay.task._navigate_episode4())
            self.assertEqual(replay.actions, [(0, 'STAR_FIRST')])

    def test_story_captured_blue_entry_and_star_switch_to_target_episode(self):
        frames = [scene(fixture='20261021-220726-466'),
                  scene(fixture='20261021-220728-697'),
                  scene(fixture='20261021-220735-802'),
                  frontier(identity=(4, 1)), scene('EPISODE_STORY_COMPLETE'),
                  frontier(4, identity=(4, 1))]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            self.device = replay
            self.assertEqual(replay.task._push(number=4, limit=1), 'stage_limit')
            self.assertEqual(replay.actions, [
                (0, 'EPISODE_SELECTOR_ENTRY'), (1, 'GOTO_EPISODE_4'),
                (2, 'EPISODE_CHAPTER_STAR_result0'), (3, 'READY_TO_FIGHT'),
                (4, 'EPISODE_STORY_COMPLETE'),
            ])
            self.assertEqual(replay.task.completed_stages, 1)

    def test_prelude_and_side_story_panels_can_switch_to_configured_episode(self):
        for stamp in ('20261021-220732-535', '20261021-220740-914'):
            frames = [scene(fixture=stamp), scene(fixture='20261021-220735-802'),
                      frontier(identity=(4, 1)), scene('EPISODE_STORY_COMPLETE'), frontier(4, identity=(4, 1))]
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = EpisodeReplay(frames, clock)
                self.device = replay
                self.assertEqual(replay.task._push(number=4, limit=1), 'stage_limit')
                self.assertEqual(replay.actions[:2], [(0, 'GOTO_EPISODE_4'), (1, 'EPISODE_CHAPTER_STAR_result0')])
            with self.subTest(no_target=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([scene(fixture=stamp, seconds=15.1) for _ in range(4)], clock)
                self.device = replay
                self.assertEqual(replay.task._push(), 'navigation_timeout')
                self.assertEqual(replay.actions, [])

        first = ClickButton((418, 292, 433, 311), name='CHAPTER_FIRST')
        frames = [scene(selector=None), scene(selector=6, chapter_entries=[first]),
                  frontier(), scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            self.device = replay
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.actions, [(1, 'CHAPTER_FIRST'), (2, 'READY_TO_FIGHT'),
                                             (3, 'EPISODE_STORY_COMPLETE')])


class EpisodePushTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def replay(self, frames, clock):
        replay = EpisodeReplay(frames, clock)
        self.device = replay
        return replay

    def test_stage_prefix_and_stamina_semantics(self):
        for text in ('3. 悖逆者', '3．悖逆者', '3、悖逆者'):
            self.assertEqual(parse_stage_index(text), 3)
        for text in ('第3章', '悖逆者3', '0. 空', '3.', '3/15'):
            self.assertIsNone(parse_stage_index(text))
        self.assertEqual(parse_story_stamina('20/360'), (20, 360))
        self.assertEqual(parse_story_stamina('20/0'), (20, 0))
        self.assertEqual(parse_story_stamina('20/5000'), (20, 5000))
        self.assertEqual(parse_story_stamina('-20/374'), (20, 374))
        for text in ('0/360', '-0/360', '20/-360', '--20/360', '20/', '/360', '20/360/360', ''):
            self.assertIsNone(parse_story_stamina(text))

    def test_captured_map_reads_frontier_and_excludes_locked_rows(self):
        for stamp, selected, indexes in (
            ('20261019-201919-177', 3, [3, 4, 5, 6]),
            ('20261021-193325-307', 6, [6, 7, 8]),
        ):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                self.assertTrue(replay.task._map_ready())
                self.assertEqual(replay.task._map_identity(), ((6, 1), selected))
                rows = replay.task._read_stages()
                self.assertEqual([s.index for s in rows], indexes)
                self.assertEqual([s.index for s in rows if not s.completed and not s.locked], [selected])
                self.assertEqual([s.index for s in rows if s.locked], indexes[1:])
                self.assertFalse(replay.actions)

    def test_captured_prepare_reads_actual_cost_and_available(self):
        for stamp, stamina in (('20261019-201925-028', (20, 360)),
                               ('20261019-222835-132', (20, 597)),
                               ('20261021-203153-292', (20, 374))):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                self.assertTrue(replay.color(REPEAT_COMBAT_MENU, similarity=.8, threshold=25))
                self.assertTrue(replay.task.appear(COMBAT_START))
                self.assertEqual(replay.task._prepare_stamina(), stamina)

    def test_captured_prepare_uses_shared_start_and_retries_until_battle(self):
        frames = [frontier(5)]
        frames += [scene(fixture='20261021-200850-486', seconds=seconds)
                   for seconds in (.5, 1, 1, .1)]
        # This single-hero story team is permitted by its last star condition.
        # Only prepare pixels are captured; navigation, the long battle and
        # final result are explicitly synthetic observations, not a real clear.
        frames += [scene(ENEMY_NUM_EXIST.name, seconds=50),
                   scene('COMBAT_RESULT_CONFIRM'), frontier(6)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('READY_TO_FIGHT'), [0])
            self.assertEqual(replay.clicks(COMBAT_START.name), [1, 4])
            self.assertEqual(replay.clicks('AUTO_COMBAT'), [5])
            self.assertEqual(replay.clicks('COMBAT_RESULT_CONFIRM'), [6])
            self.assertEqual(replay.task.completed_stages, 1)
            self.assertEqual(replay.clicks('POPUP_CONFIRM'), [])
            for _, name, (x1, y1, x2, y2) in replay.positions:
                if name == COMBAT_START.name:
                    self.assertTrue(928 <= x1 < x2 <= 1256)
                    self.assertTrue(630 <= y1 < y2 <= 696)

    def test_dimmed_captured_prepare_never_reads_stamina_or_starts(self):
        with ControlledClock() as clock:
            replay = self.replay([scene(fixture='20261019-222835-132'), scene(seconds=50)], clock)
            # Simulate an overlay on the captured prepare page without
            # changing its geometry. Luminance alone still finds the marker;
            # the color guard must reject the obscured background.
            replay.image = (replay.image * .4).astype('uint8')
            self.assertTrue(replay.task._is_prepare_page())
            with patch.object(replay.task, '_prepare_stamina', side_effect=AssertionError('Obscured prepare read')), \
                    patch.object(replay.task, '_last_star_condition', side_effect=AssertionError('Obscured mission read')):
                self.assertEqual(replay.task._push(), 'navigation_timeout')
            self.assertEqual(replay.actions, [])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_captured_clear_and_confirmation_retry_visible_controls(self):
        # The clear and confirmation were captured from separate runs; the
        # transition and final map are explicit synthetic observations.
        frames = [frontier(5)]
        frames += [scene(fixture='20261019-224016-279', seconds=seconds)
                   for seconds in (.5, 1, 1.1, .1)]
        frames += [scene(fixture='20261019-202159-255', seconds=seconds)
                   for seconds in (.5, 1, 1.1, .1)]
        frames.append(frontier(6))
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            counts_at_click = []
            click = replay.task.device.click

            def record_click(button):
                counts_at_click.append(replay.task.completed_stages)
                click(button)

            with patch.object(replay.task.device, 'click', side_effect=record_click):
                self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.actions, [(0, 'READY_TO_FIGHT'),
                                             (1, COMBAT_RESULT_CLEAR.name), (3, COMBAT_RESULT_CLEAR.name),
                                             (5, COMBAT_RESULT_CONFIRM.name), (7, COMBAT_RESULT_CONFIRM.name)])
            self.assertEqual(counts_at_click, [0] * 5)
            self.assertEqual(replay.task.completed_stages, 1)
            replay.assert_retried(self, COMBAT_RESULT_CLEAR.name, 2)
            replay.assert_retried(self, COMBAT_RESULT_CONFIRM.name, 2)
            for _, name, (x1, y1, x2, y2) in replay.positions:
                if name == COMBAT_RESULT_CLEAR.name:
                    self.assertTrue(1224 <= x1 < x2 <= 1259)
                    self.assertTrue(657 <= y1 < y2 <= 697)
                elif name == COMBAT_RESULT_CONFIRM.name:
                    self.assertTrue(1084 <= x1 < x2 <= 1254)
                    self.assertTrue(630 <= y1 < y2 <= 696)

    def test_captured_clear_resumes_when_confirmation_frame_was_not_sampled(self):
        # A real clear is result evidence even when an intervening confirmation
        # is not sampled. Only the final synthetic unobscured map commits it.
        for resumed in (False, True):
            with self.subTest(resumed=resumed), ControlledClock() as clock:
                frames = ([] if resumed else [frontier(5)])
                frames += [scene(fixture='20261019-224016-279'), frontier(6)]
                replay = self.replay(frames, clock)
                self.assertEqual(replay.task._push(limit=1), 'stage_limit')
                self.assertEqual(replay.clicks(COMBAT_RESULT_CLEAR.name), [0 if resumed else 1])
                self.assertEqual(replay.clicks(COMBAT_RESULT_CONFIRM.name), [])
                self.assertEqual(replay.task.completed_stages, 1)

    def test_captured_pages_reject_dimmed_map_and_unrelated_rewards(self):
        stamps = ('201915-057', '201919-177', '201922-561', '201925-028', '201930-080',
                  '201935-800', '201941-878', '201943-776', '201947-654', '202147-227',
                  '202152-519', '202154-364', '202155-839', '202159-255', '202201-945', '202204-254',
                  '221856-229', '222835-132', '224016-279')
        for stamp in stamps:
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=f'20261019-{stamp}')], clock)
                self.assertEqual(replay.task._map_ready(), stamp == '201919-177')
                self.assertEqual(replay.task.appear(EPISODE_STORY_COMPLETE), stamp == '202201-945')
                self.assertEqual(replay.task.appear(EPISODE_CHAPTER_REWARD), stamp == '202204-254')
                self.assertEqual(replay.task.appear(EPISODE_STORY_SKIP_CHECK), stamp in ('201943-776', '202152-519'))
                self.assertEqual(replay.task.appear(COMBAT_RESULT_CLEAR), stamp in ('202155-839', '224016-279'))
                self.assertEqual(replay.task.appear(COMBAT_RESULT_CONFIRM), stamp == '202159-255')
                self.assertEqual(replay.color(REPEAT_COMBAT_MENU, similarity=.8, threshold=25),
                                 stamp in ('201925-028', '222835-132'))

    def test_captured_round_retries_without_double_counting_and_settles_both_rewards(self):
        frames = [scene(fixture=f'20261019-{stamp}', seconds=seconds) for stamp, seconds in (
            ('201919-177', .5), ('201919-177', 1), ('201922-561', .5),
            ('201925-028', .5), ('201925-028', 1), ('201930-080', .5),
            ('201935-800', .5), ('201941-878', 3), ('201943-776', .5),
            ('201943-776', .5), ('201947-654', .5), ('202147-227', 3),
            ('202152-519', .5), ('202154-364', .5), ('202155-839', .5),
            ('202159-255', .5), ('202159-255', 1), ('202201-945', .5),
            ('202201-945', 1), ('202204-254', .5), ('202204-254', 1),
        )]
        # Include the independently captured allowed single-hero story team;
        # this mixed-source replay is not a continuous captured battle.
        frames[3:5] = [scene(fixture='20261021-200850-486', seconds=seconds) for seconds in (.5, 1)]
        # The series ends under a reward overlay. This final map is synthetic.
        frames.append(frontier(4))
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.task.completed_stages, 1)
            self.assertEqual(replay.clicks('READY_TO_FIGHT'), [0])
            self.assertEqual(replay.clicks('CHOOSE_TEAM'), [2])
            self.assertEqual(replay.clicks(COMBAT_START.name), [3])
            self.assertEqual(replay.clicks('EPISODE_STORY_SKIP'), [7, 11])
            self.assertEqual(replay.clicks('EPISODE_STORY_SKIP_CONFIRM'), [8, 12])
            self.assertEqual(replay.clicks(COMBAT_RESULT_CLEAR.name), [14])
            self.assertEqual(replay.clicks('COMBAT_RESULT_CONFIRM'), [15])
            self.assertEqual(replay.clicks('EPISODE_STORY_COMPLETE'), [17])
            self.assertEqual(replay.clicks('EPISODE_CHAPTER_REWARD'), [19])

    def test_captured_supporter_recognition_rejects_unrelated_pages(self):
        stamps = ('20261019-201919-177', '20261019-201922-561', '20261019-201925-028',
                  '20261019-201943-776', '20261019-202155-839', '20261019-202201-945',
                  '20261019-202204-254', '20261019-221856-229', '20261021-180926-061',
                  '20261021-193325-307', '20261021-193327-115')
        for stamp in stamps:
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                supporter = stamp in ('20261019-201922-561', '20261019-221856-229',
                                      '20261021-180926-061', '20261021-193327-115')
                self.assertEqual(replay.task._is_episode_supporter_page(), supporter)
                self.assertEqual(replay.task.appear(CHOOSE_TEAM), supporter)
                if supporter:
                    # The match must still identify the visible green button,
                    # rather than retaining a coordinate from an earlier frame.
                    x1, y1, x2, y2 = CHOOSE_TEAM.button
                    self.assertTrue(928 <= x1 < x2 <= 1256)
                    self.assertTrue(630 <= y1 < y2 <= 696)
                    self.assertFalse(replay.task._map_ready())
                    self.assertFalse(replay.color(REPEAT_COMBAT_MENU, similarity=.8, threshold=25))
                self.assertFalse(replay.actions)

    def test_captured_stage_six_supporter_retries_choose_team_after_map(self):
        frames = [scene(fixture='20261021-193325-307', seconds=seconds) for seconds in (.5, 1)]
        frames += [scene(fixture='20261021-193327-115', seconds=seconds)
                   for seconds in (.5, 1, 1, .1)]
        # Only the map and supporter are captured. The next prepare page is
        # explicitly synthetic and unaffordable; it verifies the navigation
        # boundary without inventing a captured battle or a completed node.
        frames.append(scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name,
                            'POPUP_CONFIRM', stamina=(20, 0)))
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(), 'no_stamina')
            self.assertEqual(replay.actions, [(0, 'READY_TO_FIGHT'), (2, 'CHOOSE_TEAM'), (5, 'CHOOSE_TEAM')])
            replay.assert_retried(self, CHOOSE_TEAM.name, 2)
            self.assertEqual(replay.index, 6)
            self.assertEqual(replay.task.completed_stages, 0)
            self.assertEqual(replay.clicks(COMBAT_START.name), [])
            self.assertEqual(replay.clicks('POPUP_CONFIRM'), [])
            for _, name, (x1, y1, x2, y2) in replay.positions:
                if name == CHOOSE_TEAM.name:
                    self.assertTrue(928 <= x1 < x2 <= 1256)
                    self.assertTrue(630 <= y1 < y2 <= 696)

    def test_latest_supporter_retries_then_stops_at_story_team(self):
        # These independently supplied pages do not establish a continuous
        # capture. Both orange and green 攻略 conditions must block at prepare.
        for supporter, prepare in (
            ('20261021-180926-061', '20261019-225011-385'),
            ('20261021-200843-005', '20261021-200846-129'),
        ):
            frames = [scene(fixture=supporter, seconds=seconds) for seconds in (.5, 1, 1, .1)]
            frames.append(scene(fixture=prepare))
            with self.subTest(prepare=prepare), ControlledClock() as clock:
                replay = self.replay(frames, clock)
                with self.assertRaises(RequestHumanTakeover):
                    replay.task._push()
                self.assertEqual(replay.actions, [(0, 'CHOOSE_TEAM'), (3, 'CHOOSE_TEAM')])
                self.assertEqual(replay.task.completed_stages, 0)
                self.assertEqual(replay.index, 4)

    def test_navigation_timeout_keeps_last_frame_and_records_loaded_templates(self):
        frames = [scene()] + [scene(seconds=15) for _ in range(5)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            replay.task.config.SCREEN_SHOT_SAVE_FOLDER = './screenshots'
            with patch.object(replay, 'save_screenshot', create=True) as save, \
                    patch('tasks.dungeon.episode_push.logger') as log:
                self.assertFalse(replay.task.run())
            self.assertEqual(replay.task.stop_reason, 'navigation_timeout')
            save.assert_called_once_with(genre='episode_timeout', interval=0)
            self.assertEqual(replay.index, 5)
            self.assertIs(replay.image, frames[5])
            self.assertEqual(replay.actions, [])
            self.assertEqual(replay.task.completed_stages, 0)
            observations = dict(call.args for call in log.attr.call_args_list)
            self.assertEqual(observations['EpisodeAssetLanguage'], 'global_cn')
            self.assertEqual(observations['EpisodeSupporterPage'], False)
            self.assertEqual(observations['EpisodeChooseTeamVisible'], False)
            for name in ('EpisodeSupporterTemplates', 'EpisodeChooseTeamTemplates'):
                self.assertTrue(all(path.startswith('./assets/global_cn/') for path in observations[name]))

    def test_captured_supporter_retries_before_insufficient_stamina_stop(self):
        frames = [scene(fixture='20261019-221856-229', seconds=seconds)
                  for seconds in (.5, 1, 1, .1)]
        # The user supplied no next prepare screenshot. Its insufficient
        # balance is an explicit synthetic observation, not a captured read.
        frames.append(scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name,
                            'POPUP_CONFIRM', stamina=(20, 15)))
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(), 'no_stamina')
            self.assertEqual(replay.actions, [(0, 'CHOOSE_TEAM'), (3, 'CHOOSE_TEAM')])
            self.assertEqual(replay.task.completed_stages, 0)
            self.assertEqual(replay.clicks(COMBAT_START.name), [])
            self.assertEqual(replay.clicks('POPUP_CONFIRM'), [])

    def test_story_only_node_continues_through_captured_supporter(self):
        frames = [frontier(4), scene('EPISODE_STORY_SKIP'),
                  scene('EPISODE_STORY_SKIP_CHECK', 'EPISODE_STORY_SKIP_CONFIRM'),
                  scene('EPISODE_STORY_COMPLETE'), frontier(5, seconds=1),
                  scene(fixture='20261019-221856-229'),
                  scene(fixture='20261019-221856-229', seconds=1),
                  scene(fixture='20261021-200850-486'),
                  scene('COMBAT_RESULT_CONFIRM'), frontier(6)]
        # Navigation, story and the final battle result are simulated. The
        # supporter and allowed prepare pages are from different runs and
        # exercise real matchers/OCR, not a captured multi-stage battle sequence.
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=2), 'stage_limit')
            self.assertEqual(replay.clicks('READY_TO_FIGHT'), [0, 4])
            self.assertEqual(replay.clicks('CHOOSE_TEAM'), [5])
            self.assertEqual(replay.clicks(COMBAT_START.name), [7])
            self.assertEqual(replay.clicks('COMBAT_RESULT_CONFIRM'), [8])
            self.assertEqual(replay.task.completed_stages, 2)

    def test_story_only_node_continues_to_next_frontier(self):
        frames = [frontier(), scene('EPISODE_STORY_SKIP'), scene('EPISODE_STORY_SKIP_CHECK', 'EPISODE_STORY_SKIP_CONFIRM'),
                  scene('EPISODE_STORY_COMPLETE'), scene('EPISODE_CHAPTER_REWARD'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.task.completed_stages, 1)
            self.assertEqual(replay.actions[-1][1], 'EPISODE_CHAPTER_REWARD')

    def test_resumed_story_counts_only_after_unobscured_map(self):
        frames = [scene('EPISODE_STORY_COMPLETE'), scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.task.completed_stages, 1)
            self.assertEqual(len(replay.clicks('EPISODE_STORY_COMPLETE')), 1)

    def test_insufficient_stamina_never_starts_or_confirms_purchase(self):
        with ControlledClock() as clock:
            replay = self.replay([scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name, stamina=(20, 19))], clock)
            self.assertEqual(replay.task._push(), 'no_stamina')
            self.assertEqual(replay.actions, [])

    def test_unknown_stamina_times_out_without_starting(self):
        frames = [scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name, stamina=None, seconds=4) for _ in range(5)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(), 'unreadable_stamina')
            self.assertEqual(replay.actions, [])

    def test_stamina_confirmation_restarts_after_changed_or_unreadable_values(self):
        values = [(20, 374), (20, 374), None, (20, 374),
                  (20, 375), (20, 375), (20, 375), None]
        frames = [scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name, 'POPUP_CONFIRM',
                        stamina=value, seconds=.1) for value in values]
        frames += [scene(COMBAT_RESULT_CONFIRM.name), frontier(6)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            reads = []

            def read():
                reads.append(replay.index)
                return replay.stamina()

            with patch.object(replay.task, '_prepare_stamina', side_effect=read):
                self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(reads, list(range(7)))
            self.assertEqual(replay.actions, [(0, COMBAT_START.name), (8, COMBAT_RESULT_CONFIRM.name)])
            self.assertEqual(replay.task.completed_stages, 1)

    def test_stamina_cache_is_cleared_before_a_new_prepare_page(self):
        frames = [scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name, stamina=(20, 374), seconds=.1)
                  for _ in range(3)]
        # A repeated prepare frame uses the confirmed reading. Leaving that
        # page invalidates it, so a fresh insufficient balance must stop.
        frames += [scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name, stamina=None, seconds=.1),
                   scene(seconds=.1),
                   scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name, 'POPUP_CONFIRM',
                         stamina=(20, 19), seconds=.1)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            reads = []

            def read():
                reads.append(replay.index)
                return replay.stamina()

            with patch.object(replay.task, '_prepare_stamina', side_effect=read):
                self.assertEqual(replay.task._push(), 'no_stamina')
            self.assertEqual(reads, [0, 1, 2, 5])
            self.assertEqual(replay.actions, [(0, COMBAT_START.name)])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_failed_battle_and_full_inventory_require_attention(self):
        for button in (COMBAT_RESULT_FAILED, PACKAGE_FULL):
            with self.subTest(button=button.name), ControlledClock() as clock:
                replay = self.replay([scene(button.name)], clock)
                with self.assertRaises(RequestHumanTakeover):
                    replay.task._push()
                self.assertFalse(replay.actions)

    def test_captured_exploration_stage_stops_before_navigation_or_starting(self):
        with ControlledClock() as clock:
            replay = self.replay([scene(fixture='20261021-211204-367')], clock)
            with patch.object(replay.task, '_map_identity', side_effect=AssertionError('Unexpected map read')), \
                    patch.object(replay.task, '_prepare_stamina', side_effect=AssertionError('Unexpected cost read')), \
                    patch('tasks.dungeon.episode_push.logger.critical') as critical, \
                    self.assertRaises(RequestHumanTakeover) as error:
                replay.task._push()
            self.assertIn('探险', str(error.exception))
            critical.assert_called_once()
            self.assertIn('manually', critical.call_args.args[0])
            self.assertEqual(replay.index, 0)
            self.assertEqual(replay.actions, [])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_captured_exploration_marker_rejects_normal_and_obscured_pages(self):
        fixtures = json.loads(MANIFEST.read_text(encoding='utf-8'))['fixtures']
        stamps = [key for key, item in fixtures.items() if '/input/episode/' in item['path']]
        for stamp in stamps:
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                self.assertEqual(replay.task.match_template_color(EPISODE_EXPLORATION_CHECK, threshold=25),
                                 stamp == '20261021-211204-367')
                self.assertEqual(replay.actions, [])
        with ControlledClock() as clock:
            replay = self.replay([scene(fixture='20261021-211204-367')], clock)
            # Template correlation alone still recognizes a dimmed page.
            # Require its normal button color before treating it as actionable.
            replay.image = (replay.image * .4).astype('uint8')
            self.assertTrue(replay.task.appear(EPISODE_EXPLORATION_CHECK))
            self.assertFalse(replay.task.match_template_color(EPISODE_EXPLORATION_CHECK, threshold=25))
        with ControlledClock() as clock:
            replay = self.replay([scene(fixture='20261021-211204-367')], clock)
            replay.image[648:674, 1087:1137] = 0
            self.assertFalse(replay.task.match_template_color(EPISODE_EXPLORATION_CHECK, threshold=25))

    def test_exploration_stop_precedes_stale_map_result_and_prepare_controls(self):
        frames = [frontier(5), scene('EPISODE_STORY_COMPLETE'), frontier(6)]
        frames[-1]['buttons'] = frozenset((EPISODE_EXPLORATION_CHECK.name, REPEAT_COMBAT_MENU.name,
                                          COMBAT_START.name, 'POPUP_CONFIRM'))
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            with patch.object(replay.task, '_prepare_stamina', side_effect=AssertionError('Unexpected cost read')), \
                    patch('tasks.dungeon.episode_push.logger.critical') as critical, \
                    self.assertRaises(RequestHumanTakeover):
                replay.task._push(limit=1)
            critical.assert_called_once()
            self.assertEqual(replay.actions, [(0, 'READY_TO_FIGHT'), (1, 'EPISODE_STORY_COMPLETE')])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_captured_story_team_marker_rejects_other_pages(self):
        stamps = tuple(f'20261019-{stamp}' for stamp in (
            '201915-057', '201919-177', '201922-561', '201925-028', '201930-080',
            '201935-800', '201941-878', '201943-776', '201947-654', '202147-227',
            '202152-519', '202154-364', '202155-839', '202159-255', '202201-945',
            '202204-254', '221856-229', '222835-132', '224016-279',
            '225007-086', '225009-755', '225011-385', '225014-473',
        )) + ('20261021-180926-061', '20261021-193325-307', '20261021-193327-115', '20261021-194836-087',
              '20261021-200843-005', '20261021-200846-129', '20261021-200850-486', '20261021-203153-292')
        for stamp in stamps:
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                self.assertEqual(replay.task.appear(EPISODE_STORY_TEAM_CHECK),
                                 stamp in ('20261019-225011-385', '20261019-225014-473', '20261021-194836-087',
                                           '20261021-200846-129', '20261021-200850-486'))
                self.assertEqual(replay.actions, [])

    def test_captured_normal_team_with_strategy_condition_starts(self):
        # Prepare captures are independent; settlement and the final map are
        # synthetic. A keyword alone must not block a user-configured team.
        for stamp in ('20261019-201925-028', '20261019-222835-132', '20261021-203153-292'):
            frames = [scene(fixture=stamp, seconds=seconds) for seconds in (.5, 1, 1, .1) + (.1,) * 8]
            frames += [scene(COMBAT_RESULT_CONFIRM.name), frontier(6)]
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay(frames, clock)
                self.assertFalse(replay.task.appear(EPISODE_STORY_TEAM_CHECK))
                self.assertIn('攻略', replay.task._last_star_condition())
                with patch.object(replay.task, '_prepare_stamina', wraps=replay.task._prepare_stamina) as cost:
                    self.assertEqual(replay.task._push(limit=1), 'stage_limit')
                self.assertEqual(cost.call_count, 3)
                self.assertEqual(replay.actions, [(0, COMBAT_START.name), (3, COMBAT_START.name),
                                                  (12, COMBAT_RESULT_CONFIRM.name)])
                self.assertEqual(replay.task.completed_stages, 1)

    def test_normal_team_skips_empty_condition_and_resets_story_timeout(self):
        frames = [scene(REPEAT_COMBAT_MENU.name, EPISODE_STORY_TEAM_CHECK.name, star_condition=''),
                  scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name, stamina=(20, 100),
                        star_condition='', seconds=13),
                  scene(REPEAT_COMBAT_MENU.name, EPISODE_STORY_TEAM_CHECK.name, star_condition=''),
                  scene(REPEAT_COMBAT_MENU.name, EPISODE_STORY_TEAM_CHECK.name, COMBAT_START.name,
                        stamina=(20, 19))]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            with patch.object(replay.task, '_last_star_condition', wraps=replay.task._last_star_condition) as read:
                self.assertEqual(replay.task._push(), 'no_stamina')
            self.assertEqual(read.call_count, 3)
            self.assertEqual(replay.actions, [(1, COMBAT_START.name)])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_captured_story_team_stops_before_reading_cost_or_starting(self):
        # Both the story-team marker and the last-row keyword must be present.
        for stamp in ('20261019-225011-385', '20261019-225014-473',
                      '20261021-194836-087', '20261021-200846-129'):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                with patch.object(replay.task, '_prepare_stamina', side_effect=AssertionError('Unexpected cost read')), \
                        patch('tasks.dungeon.episode_push.logger.critical') as critical, \
                        self.assertRaises(RequestHumanTakeover) as error:
                    replay.task._push()
                self.assertEqual(error.exception.args, ())
                critical.assert_called_once()
                self.assertIn('strategy-hero requirement', critical.call_args.args[0])
                self.assertIn('manually', critical.call_args.args[0])
                self.assertIn('three-star', critical.call_args.args[0])
                self.assertEqual(replay.actions, [])
                self.assertEqual(replay.task.completed_stages, 0)

    def test_captured_stage_six_story_team_stops_after_supporter(self):
        # The map/supporter and story-team prepare were supplied separately.
        # Their transition is an explicit replay, not a continuous capture.
        frames = [scene(fixture='20261021-193325-307'),
                  scene(fixture='20261021-193327-115'),
                  scene(fixture='20261021-193327-115', seconds=1),
                  scene(fixture='20261021-194836-087')]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            with patch.object(replay.task, '_prepare_stamina', side_effect=AssertionError('Unexpected cost read')), \
                    patch('tasks.dungeon.episode_push.logger.critical') as critical, \
                    self.assertRaises(RequestHumanTakeover):
                replay.task._push()
            critical.assert_called_once()
            self.assertIn('strategy-hero requirement', critical.call_args.args[0])
            self.assertEqual(replay.actions, [(0, 'READY_TO_FIGHT'), (1, 'CHOOSE_TEAM')])
            self.assertEqual(replay.index, 3)
            self.assertEqual(replay.task.completed_stages, 0)
            self.assertEqual(replay.clicks(COMBAT_START.name), [])
            self.assertEqual(replay.clicks('POPUP_CONFIRM'), [])

    def test_story_team_stop_precedes_stale_controls_and_pending_result(self):
        # These deliberately conflicting synthetic controls verify priority:
        # a story team with a blocked last condition stops before stale map/result
        # signals can count a node or prepare/purchase controls can click.
        frames = [frontier(5), scene('EPISODE_STORY_COMPLETE'), frontier(6)]
        frames[-1]['star_condition'] = '未使用攻略英雄，继续战斗'
        frames[-1]['buttons'] = frozenset((REPEAT_COMBAT_MENU.name, EPISODE_STORY_TEAM_CHECK.name,
                                          COMBAT_START.name, 'POPUP_CONFIRM'))
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            with patch('tasks.dungeon.episode_push.logger.critical') as critical, \
                    self.assertRaises(RequestHumanTakeover) as error:
                replay.task._push(limit=1)
            self.assertEqual(error.exception.args, ())
            critical.assert_called_once()
            self.assertEqual(replay.actions, [(0, 'READY_TO_FIGHT'), (1, 'EPISODE_STORY_COMPLETE')])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_captured_last_star_condition_reads_keyword_and_single_hero(self):
        self.assertEqual(OCR_EPISODE_LAST_STAR_CONDITION.area, (958, 231, 1262, 269))
        for stamp in ('20261019-201925-028', '20261019-222835-132',
                      '20261019-225011-385', '20261019-225014-473',
                      '20261021-194836-087', '20261021-200843-005',
                      '20261021-200846-129', '20261021-200850-486', '20261021-203153-292'):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                text = replay.task._last_star_condition()
                if stamp == '20261021-200850-486':
                    self.assertEqual(text, '配置1名战士职业英雄到队伍中')
                    self.assertTrue(replay.task.appear(EPISODE_STORY_TEAM_CHECK))
                else:
                    self.assertEqual(text.rstrip('。'), '未使用攻略英雄、继续战斗')
                self.assertEqual(replay.actions, [])

    def test_last_star_condition_ignores_keyword_in_earlier_rows(self):
        # Explicitly compose keyword text from a real last row into the first
        # two rows of an allowed page. Only the unchanged last row is relevant.
        frames = [scene(fixture='20261021-200850-486'),
                  scene('COMBAT_RESULT_CONFIRM'), frontier(6)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            donor = fixture_image(MANIFEST, '20261021-200846-129')
            keyword = donor[231:269, 958:1262]
            replay.image[153:191, 958:1262] = keyword
            replay.image[192:230, 958:1262] = keyword
            self.assertEqual(replay.task._last_star_condition(), '配置1名战士职业英雄到队伍中')
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.actions, [(0, COMBAT_START.name), (1, 'COMBAT_RESULT_CONFIRM')])
            self.assertEqual(replay.task.completed_stages, 1)

    def test_unknown_last_star_condition_times_out_without_cost_or_clicks(self):
        frames = [scene(REPEAT_COMBAT_MENU.name, EPISODE_STORY_TEAM_CHECK.name, COMBAT_START.name, 'POPUP_CONFIRM',
                        star_condition='', seconds=4) for _ in range(5)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            with patch.object(replay.task, '_prepare_stamina', side_effect=AssertionError('Unexpected cost read')):
                self.assertEqual(replay.task._push(), 'unreadable_star_condition')
            self.assertEqual(replay.actions, [])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_last_star_condition_retries_and_resets_after_leaving_prepare(self):
        for leave_prepare in (False, True):
            frames = [scene(REPEAT_COMBAT_MENU.name, EPISODE_STORY_TEAM_CHECK.name,
                            COMBAT_START.name, star_condition='')]
            if leave_prepare:
                frames.append(scene(seconds=13))
                frames.append(scene(REPEAT_COMBAT_MENU.name, EPISODE_STORY_TEAM_CHECK.name,
                                    COMBAT_START.name, star_condition=''))
            frames.append(scene(REPEAT_COMBAT_MENU.name, EPISODE_STORY_TEAM_CHECK.name, COMBAT_START.name, 'POPUP_CONFIRM',
                                stamina=(20, 19), star_condition='配置1名战士职业英雄到队伍中'))
            with self.subTest(leave_prepare=leave_prepare), ControlledClock() as clock:
                replay = self.replay(frames, clock)
                with patch.object(replay.task, '_prepare_stamina', wraps=replay.task._prepare_stamina) as cost:
                    self.assertEqual(replay.task._push(), 'no_stamina')
                cost.assert_called_once()
                self.assertEqual(replay.actions, [])
                self.assertEqual(replay.task.completed_stages, 0)

    def test_auto_combat_only_toggles_with_positive_manual_hud(self):
        frames = [scene(ENEMY_NUM_EXIST.name), scene(), scene(AUTO_COMBAT_SKILL_OPENED.name),
                  scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('AUTO_COMBAT'), [0])

    def test_captured_unavailable_auto_combat_stops_before_any_battle_action(self):
        for from_prepare in (False, True):
            frames = [scene(fixture='20261021-202808-776')]
            if from_prepare:
                # Independently captured allowed prepare and blocked battle
                # pages; their transition is simulated, not a continuous run.
                frames.insert(0, scene(fixture='20261021-200850-486'))
            with self.subTest(from_prepare=from_prepare), ControlledClock() as clock:
                replay = self.replay(frames, clock)
                with patch('tasks.dungeon.episode_push.logger.critical') as critical, \
                        self.assertRaises(RequestHumanTakeover) as error:
                    replay.task._push()
                self.assertIn('automatic combat is unavailable', str(error.exception))
                critical.assert_called_once()
                self.assertEqual(replay.actions, [(0, COMBAT_START.name)] if from_prepare else [])
                self.assertEqual(replay.index, int(from_prepare))
                self.assertEqual(replay.task.completed_stages, 0)

    def test_captured_unavailable_auto_combat_rejects_enabled_and_obscured_pages(self):
        fixtures = json.loads(MANIFEST.read_text(encoding='utf-8'))['fixtures']
        stamps = [key for key, item in fixtures.items() if '/input/episode/' in item['path']]
        stamps += [f'legacy-urgent-tasks-battle-{suffix}'
                   for suffix in ('auto-on', 'manual-target', 'clear', 'result', 'leave')]
        for stamp in stamps:
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                self.assertEqual(replay.task._auto_combat_unavailable(), stamp == '20261021-202808-776')
                self.assertEqual(replay.actions, [])
        for stamp in ('20261021-202808-776', 'legacy-urgent-tasks-battle-manual-target'):
            with self.subTest(obscured=stamp), ControlledClock() as clock:
                replay = self.replay([scene(fixture=stamp)], clock)
                # An overlay dims the whole toolbar. It is not the specific
                # gray auto icon beside otherwise bright battle controls.
                replay.image = (replay.image * .5).astype('uint8')
                self.assertFalse(replay.task._auto_combat_unavailable())
        with ControlledClock() as clock:
            replay = self.replay([scene(fixture='20261021-202808-776')], clock)
            replay.image[15:50, 1107:1147] = 0
            self.assertFalse(replay.task._auto_combat_unavailable())

    def test_unavailable_auto_combat_precedes_stale_result_and_manual_marker(self):
        frames = [frontier(5), scene('EPISODE_STORY_COMPLETE'), frontier(6)]
        frames[-1]['buttons'] = frozenset((EPISODE_AUTO_COMBAT_UNAVAILABLE.name,
                                          EPISODE_BATTLE_CONTROLS_CHECK.name, ENEMY_NUM_EXIST.name,
                                          COMBAT_START.name, 'POPUP_CONFIRM'))
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            with patch.object(replay.task, '_prepare_stamina', side_effect=AssertionError('Unexpected cost read')), \
                    self.assertRaises(RequestHumanTakeover):
                replay.task._push(limit=1)
            self.assertEqual(replay.actions, [(0, 'READY_TO_FIGHT'), (1, 'EPISODE_STORY_COMPLETE')])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_captured_available_manual_battle_still_retries_auto_toggle(self):
        frames = [scene(fixture='legacy-urgent-tasks-battle-manual-target', seconds=seconds)
                  for seconds in (.5, 1, 1, .1)]
        # These battle screenshots come from different runs. The result and
        # final map are synthetic; only recognition/retry behavior is proven.
        frames += [scene(fixture='legacy-urgent-tasks-battle-auto-on'),
                   scene('COMBAT_RESULT_CONFIRM'), frontier(6)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.actions, [(0, 'AUTO_COMBAT'), (3, 'AUTO_COMBAT'), (5, 'COMBAT_RESULT_CONFIRM')])
            replay.assert_retried(self, 'AUTO_COMBAT', 2)
            self.assertEqual(replay.task.completed_stages, 1)

    def test_ignored_map_click_retries_after_production_interval(self):
        frames = [frontier(), frontier(seconds=1), frontier(seconds=1), frontier(seconds=.1),
                  scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('READY_TO_FIGHT'), [0, 3])
            self.assertEqual(replay.task.completed_stages, 1)

    def test_selecting_frontier_does_not_start_previous_completed_stage(self):
        frames = [scene(map_ready=True, selected=2, rows=[StoryStage(2, (95, 130, 280, 155), True, False),
                        StoryStage(3, (95, 218, 300, 246), False, False)]),
                  frontier(), scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.actions[:2], [(0, 'EPISODE_STAGE_3'), (1, 'READY_TO_FIGHT')])

    def test_unknown_frames_timeout_without_clicking(self):
        with ControlledClock() as clock:
            replay = self.replay([scene(seconds=15) for _ in range(5)], clock)
            self.assertEqual(replay.task._push(), 'navigation_timeout')
            self.assertEqual(replay.actions, [])

    def test_locked_rows_do_not_end_on_first_delayed_scroll_frame(self):
        rows = [StoryStage(4, (95, 220, 300, 245), False, True)]
        frames = [scene(map_ready=True, rows=rows), scene(map_ready=True, rows=rows, seconds=1),
                  frontier(), scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            with patch.object(replay, 'swipe', wraps=replay.swipe) as swipe:
                self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            left, top, right, bottom = OCR_EPISODE_STAGE_LIST.area
            center = (left + right) // 2
            swipe.assert_called_once_with((center, bottom - 20), (center, top + 20), duration=(.2, .3))
            self.assertEqual(replay.actions[0], (0, 'swipe'))
            self.assertEqual(replay.clicks('READY_TO_FIGHT'), [2])

    def test_chapter_entry_click_does_not_skip_to_another_entry_on_ignored_click(self):
        first = ClickButton((400, 290, 430, 315), name='CHAPTER_FIRST')
        second = ClickButton((700, 290, 730, 315), name='CHAPTER_SECOND')
        frames = [scene(selector=6, chapter_entries=[first, second]),
                  scene(selector=6, chapter_entries=[first, second], seconds=1),
                  scene(selector=6, chapter_entries=[first, second], seconds=1.1),
                  frontier(), scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('CHAPTER_FIRST'), [0, 2])
            self.assertEqual(replay.clicks('CHAPTER_SECOND'), [])

    def test_main_uses_shared_episode_route(self):
        frames = [scene(main=True), frontier(), scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.actions[:2], [(0, 'route:page_episode'), (1, 'READY_TO_FIGHT')])

    def test_delayed_next_chapter_entry_is_not_reported_as_completion(self):
        first = ClickButton((400, 290, 430, 315), name='CHAPTER_FIRST')
        second = ClickButton((700, 290, 730, 315), name='CHAPTER_SECOND')
        cleared = [StoryStage(1, (95, 218, 300, 246), True, False)]
        frames = [scene(selector=6, chapter_entries=[first]),
                  scene(map_ready=True, rows=cleared),
                  scene('NEXT_CHAPTER', map_ready=True, rows=cleared, seconds=2.1),
                  scene(selector=6, chapter_entries=[], seconds=1),
                  scene(selector=6, chapter_entries=[second], seconds=1),
                  frontier(identity=(6, 2)), scene('EPISODE_STORY_COMPLETE'),
                  frontier(4, identity=(6, 2))]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.task.completed_stages, 1)
            self.assertEqual(replay.clicks('CHAPTER_SECOND'), [4])

    def test_unavailable_next_chapter_stops_without_claiming_completion(self):
        first = ClickButton((400, 290, 430, 315), name='CHAPTER_FIRST')
        cleared = [StoryStage(1, (95, 218, 300, 246), True, False)]
        frames = [scene(selector=6, chapter_entries=[first]),
                  scene(map_ready=True, rows=cleared),
                  scene('NEXT_CHAPTER', map_ready=True, rows=cleared, seconds=2.1)]
        frames += [scene(selector=6, chapter_entries=[], seconds=4) for _ in range(5)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(), 'chapter_unavailable')
            self.assertEqual(replay.task.completed_stages, 0)
            self.assertEqual(replay.actions[-1][1], 'NEXT_CHAPTER')

    def test_exhausted_chapter_advances_with_existing_next_arrow(self):
        cleared = [StoryStage(1, (95, 218, 300, 246), True, False)]
        frames = [scene('NEXT_CHAPTER', map_ready=True, rows=cleared),
                  scene('NEXT_CHAPTER', map_ready=True, rows=cleared, seconds=2.1),
                  scene('NEXT_CHAPTER', map_ready=True, rows=cleared, seconds=1),
                  scene('NEXT_CHAPTER', map_ready=True, rows=cleared, identity=(6, 2)),
                  frontier(identity=(6, 2)), scene('EPISODE_STORY_COMPLETE'),
                  frontier(4, identity=(6, 2))]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('NEXT_CHAPTER'), [1])
            self.assertEqual(replay.clicks('READY_TO_FIGHT'), [4])
            self.assertEqual(replay.clicks('swipe'), [0, 3])
            self.assertEqual(replay.clicks('BACK'), [])

    def test_episode_switch_backs_out_instead_of_changing_current_chapter(self):
        entry = ClickButton((400, 290, 430, 315), name='CHAPTER_FIRST')
        frames = [frontier(identity=(6, 1)), scene('GOTO_EPISODE_4', selector=6),
                  scene(selector=4, chapter_entries=[entry]), frontier(identity=(4, 1)),
                  scene('EPISODE_STORY_COMPLETE'), frontier(4, identity=(4, 1))]
        frames[0]['buttons'] = frozenset(('EPISODE_SELECTOR_ENTRY',))
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(number=4, limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('EPISODE_SELECTOR_ENTRY'), [0])
            self.assertEqual(replay.clicks('BACK'), [])
            self.assertEqual(replay.clicks('GOTO_EPISODE_4'), [1])
            self.assertEqual(replay.clicks('NEXT_CHAPTER'), [])

    def test_shared_swipe_preserves_farming_bounds_and_uses_story_viewport(self):
        with ControlledClock() as clock:
            replay = self.replay([scene()], clock)
            with patch.object(replay, 'swipe', wraps=replay.swipe) as swipe:
                replay.task._swipe_episode_stage_list()
                left, top, right, bottom = EPISODE_STAGE_SEARCH.area
                center = (left + right) // 2
                swipe.assert_called_with((center, bottom - 20), (center, top + 20), duration=(.2, .3))
                replay.task._swipe_episode_stage_list(toward_top=True, strong=True)
                swipe.assert_called_with((center, top + 5), (center, bottom - 5), duration=(.2, .3))
                replay.task._swipe_episode_stage_list(area=(100, 160, 380, 590))
                swipe.assert_called_with((240, 570), (240, 180), duration=(.2, .3))

    def test_shared_network_retry_keeps_interval_and_does_not_confirm_purchase(self):
        frames = [scene(NETWORK_ERROR_DISCONNECT.name), scene(NETWORK_ERROR_DISCONNECT.name, seconds=1),
                  scene(NETWORK_ERROR_DISCONNECT.name, seconds=1.1), frontier(),
                  scene('EPISODE_STORY_COMPLETE'), frontier(4)]
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('TOUCH_TO_CLOSE'), [0, 2])
            self.assertEqual(replay.clicks('POPUP_CONFIRM'), [])

    def test_unsupported_language_exits_without_device_access(self):
        task = Episode.__new__(Episode)
        with patch('tasks.dungeon.episode_push.server.lang', 'global_en'):
            self.assertFalse(task.run())

    def test_tool_entry_is_manual_and_preserves_constructor_arguments(self):
        import yaml

        from aes import AutoEpicSeven
        from module.webui.submodule.utils import get_available_func
        from tests.support.offline import ROOT

        tasks = yaml.safe_load((ROOT / 'module/config/argument/task.yaml').read_text(encoding='utf-8'))
        overrides = yaml.safe_load((ROOT / 'module/config/argument/override.yaml').read_text(encoding='utf-8'))
        self.assertEqual(tasks['Tool']['tasks']['Episode'], ['Scheduler', 'Episode'])
        self.assertFalse(overrides['Episode']['Scheduler']['Enable']['value'])
        self.assertIn('Episode', get_available_func())
        arguments = json.loads((ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        menu = json.loads((ROOT / 'module/config/argument/menu.json').read_text(encoding='utf-8'))
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        self.assertIn('Episode', menu['Tool']['tasks'])
        self.assertFalse(arguments['Episode']['Scheduler']['Enable']['value'])
        self.assertEqual(arguments['Episode']['Episode']['Number']['option'], list(range(7)))
        self.assertEqual(template['Episode']['Episode'], {'Number': 0, 'MaxStages': 0})
        self.assertFalse(template['Episode']['Scheduler']['Enable'])
        for lang in ('zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'es-ES'):
            text = json.loads((ROOT / f'module/config/i18n/{lang}.json').read_text(encoding='utf-8'))
            self.assertTrue(text['Task']['Episode']['name'])
            self.assertNotEqual(text['Task']['Episode']['name'], 'Task.Episode.name')
            self.assertTrue(text['Episode']['MaxStages']['help'])
        app = AutoEpicSeven.__new__(AutoEpicSeven)
        app.config, app.device = object(), object()
        with patch('tasks.dungeon.episode_push.Episode') as tool:
            app.episode()
            tool.assert_called_once_with(config=app.config, device=app.device, task='Episode')
            tool.return_value.run.assert_called_once_with()


class EpisodeSelectionRecognitionTests(unittest.TestCase):
    MAP = '20261021-220726-466'
    SELECTORS = (
        # Both 0/15 cards are enterable; the locked cards have no star.
        ('20261021-220728-697', 6, ((559, 292), (379, 536), (739, 536))),
        ('20261021-220732-535', None, ((418, 292), (778, 292), (598, 536), (958, 536))),
        ('20261021-220734-052', 5, ((418, 292), (778, 292), (1138, 292), (598, 536), (958, 536))),
        ('20261021-220735-802', 4, ((418, 292), (778, 292), (1138, 292), (598, 536), (958, 536))),
        ('20261021-220737-336', 3, ((418, 292), (778, 292), (1138, 292), (598, 536), (958, 536))),
        ('20261021-220738-490', 2, ((418, 292), (778, 292), (1138, 292), (598, 536), (958, 536))),
        ('20261021-220739-548', 1, ((418, 292), (778, 292), (1138, 292), (598, 536), (958, 536))),
        ('20261021-220740-914', None, ((418, 292), (778, 292), (598, 536), (958, 536))),
    )

    def setUp(self):
        server.set_lang('global_cn')

    def test_captured_selector_titles_stars_and_map_entry(self):
        from tasks.dungeon.assets.assets_dungeon_episode_navigation import (
            EPISODE_SELECTOR_ENTRY,
        )

        for stamp, number, positions in ((self.MAP, None, ()), *self.SELECTORS):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([scene(fixture=stamp)], clock)
                self.device = replay
                self.assertEqual(replay.task._is_episode_selector(), stamp != self.MAP)
                self.assertEqual(replay.task._selector_episode(), number)
                entries = replay.task._chapter_entries()
                self.assertEqual([tuple(entry.area[:2]) for entry in entries], list(positions))
                self.assertEqual([tuple(entry.button) for entry in entries],
                                 [(x, y, x + 15, y + 19) for x, y in positions])
                self.assertEqual(replay.task.match_template_color(EPISODE_SELECTOR_ENTRY, threshold=25),
                                 stamp == self.MAP)
                self.assertEqual(replay.actions, [])

    def test_captured_episode_tabs_match_current_rows(self):
        from tasks.dungeon.episode_navigation import EPISODE_ENTRIES

        rows = {1: 203, 2: 273, 3: 343, 4: 413, 5: 483, 6: 623}
        for stamp in (self.MAP, *(stamp for stamp, _, _ in self.SELECTORS)):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([scene(fixture=stamp)], clock)
                self.device = replay
                for number, (button, _) in EPISODE_ENTRIES.items():
                    self.assertEqual(replay.task.appear(button), stamp != self.MAP)
                    if stamp != self.MAP:
                        self.assertEqual(tuple(button.button), (76, rows[number], 151, rows[number] + 19))
                self.assertEqual(replay.actions, [])

    def test_selection_rejects_old_maps_prepare_rewards_and_dimmed_overlay(self):
        from tasks.dungeon.assets.assets_dungeon_episode_navigation import (
            EPISODE_SELECTOR_ENTRY,
        )

        for stamp in ('20261019-201919-177', '20261019-201925-028',
                      '20261019-202201-945', '20261019-201943-776'):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([scene(fixture=stamp)], clock)
                self.device = replay
                self.assertFalse(replay.task._is_episode_selector())
                self.assertIsNone(replay.task._selector_episode())
                self.assertEqual(replay.task._chapter_entries(), [])
                if stamp != '20261019-201919-177':
                    self.assertFalse(replay.task.match_template_color(EPISODE_SELECTOR_ENTRY, threshold=25))
                self.assertEqual(replay.actions, [])
        for stamp in (self.MAP, '20261021-220735-802'):
            with self.subTest(obscured=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([scene(fixture=stamp)], clock)
                self.device = replay
                replay.image = (fixture_image(MANIFEST, stamp) * .5).astype('uint8')
                # Correlation alone can still find stars through a dimmed
                # overlay. The selector's color guard owns permission to act.
                self.assertFalse(replay.task._is_episode_selector())
                self.assertIsNone(replay.task._selector_episode())
                self.assertFalse(replay.task._handle_episode_switch())
                self.assertEqual(replay.actions, [])
