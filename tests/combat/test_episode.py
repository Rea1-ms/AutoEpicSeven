"""Episode plans, stage parsing, and fast-combat rules."""

import json
import unittest
from unittest.mock import patch

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
from tests.support.episode import MANIFEST, EpisodeReplay
from tests.support.interaction import scene
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


def frontier(index=3, **kwargs):
    return scene(map_ready=True, selected=index, rows=[StoryStage(index, (95, 218, 300, 246), False, False)], **kwargs)


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
                self.assertIn('攻略', critical.call_args.args[0])
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
            self.assertIn('攻略', critical.call_args.args[0])
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
        with ControlledClock() as clock:
            replay = self.replay(frames, clock)
            self.assertEqual(replay.task._push(number=4, limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('BACK'), [0])
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
