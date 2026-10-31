"""Captured story lists, interruption recovery, and paid-watch boundaries."""

import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from module.config import server

server.set_lang('global_cn')

from module.exception import RequestHumanTakeover
from tasks.dungeon.assets.assets_dungeon_action import AUTO_COMBAT, COMBAT_START
from tasks.dungeon.assets.assets_dungeon_episode_story import EPISODE_STORY_SKIP
from tasks.dungeon.assets.assets_dungeon_repeat_common import REPEAT_COMBAT_MENU
from tasks.dungeon.assets.assets_dungeon_story_push_side_story import (
    SIDE_STORY_READY,
    SIDE_STORY_WATCH,
)
from tasks.dungeon.episode_push import Episode
from tests.support.episode import EpisodeReplay
from tests.support.interaction import scene
from tests.support.offline import ROOT, ControlledClock

FIRST = ['210121-471', '210127-834', '210138-020', '210141-033', '210144-947', '210255-424', '210300-938', '210303-621', '210341-110', '210344-997', '210348-137', '210350-311', '210354-862', '210400-283', '210401-363', '210403-270', '210410-023', '210411-778', '210412-934', '210414-192', '210421-767', '210423-105', '210424-139', '210426-104']
SECOND = ['210426-104', '212346-548', '212443-661', '212445-854', '212449-450', '212452-590', '212454-720', '212457-623', '212459-418', '212500-658', '212502-109', '212604-348', '212606-278', '212607-478', '212612-942', '212614-702', '212711-918', '212713-459', '212714-667', '212717-178', '212720-853']
LISTS = {
    '210121-471': ('第1章', '钢管舞舞者的秘密', (20, 16), 'ready'),
    '210127-834': ('第1章', '钢管舞舞者的秘密', (20, 96), 'ready'),
    '210350-311': ('第1章', '被掩盖的历史', (20, 77), 'watch'),
    '210403-270': ('第2章', '派对上的可疑卡牌', (20, 57), 'watch'),
    '210426-104': ('第2章', '人生就是一场豪赌', (20, 37), 'watch'),
    '212443-661': ('第2章', '不可避的一战', (20, 62), 'ready'),
    '212720-853': ('第2章', '黑手党的秘密', (20, 43), 'watch'),
}


def captured(stamp, **values):
    return scene(fixture='20261029-' + stamp, **values)


def ordered(stamps):
    times = [datetime.strptime(stamp, '%H%M%S-%f') for stamp in stamps]
    return [captured(stamp, seconds=.5 if i == 0 else (times[i] - times[i - 1]).total_seconds())
            for i, stamp in enumerate(stamps)]


def story_list(title, *buttons, **values):
    return scene(*buttons, story_list=True, story_identity=('第2章', title), **values)


class StoryPushRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_captured_lists_identify_current_row_buttons_and_cost(self):
        for stamp, (chapter, title, cost, action) in LISTS.items():
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([captured(stamp)], clock)
                task = replay.task
                self.assertTrue(task._side_story_list_ready())
                self.assertEqual(task._side_story_identity(), (chapter, title))
                self.assertEqual(task._side_story_cost(), cost)
                self.assertEqual(task.match_template_color(SIDE_STORY_WATCH, threshold=25), action == 'watch')
                self.assertEqual(task.match_template_color(SIDE_STORY_READY, threshold=25), action == 'ready')
                self.assertEqual(replay.actions, [])

    def test_all_supplied_frames_reject_non_list_states(self):
        for stamp in dict.fromkeys(FIRST + SECOND):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([captured(stamp)], clock)
                self.assertEqual(replay.task._side_story_list_ready(), stamp in LISTS)
                self.assertEqual(replay.actions, [])

    def test_captured_manual_elites_and_auto_animations_are_distinct(self):
        for stamp, expected in (('212607-478', False), ('212612-942', False),
                                ('212502-109', True), ('212614-702', True),
                                ('212714-667', True), ('212449-450', None),
                                ('212604-348', None), ('212606-278', None),
                                ('210424-139', None)):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([captured(stamp)], clock)
                self.assertIs(replay.task._story_auto_state(), expected)
                self.assertEqual(replay.actions, [])

    def test_dimmed_list_and_manual_battle_never_authorize_clicks(self):
        for stamp in ('210426-104', '212443-661', '212607-478', '212612-942'):
            with self.subTest(stamp=stamp), ControlledClock() as clock:
                replay = EpisodeReplay([captured(stamp), scene(seconds=50)], clock)
                replay.image = (replay.image * .4).astype('uint8')
                self.assertFalse(replay.task._side_story_list_ready())
                self.assertIsNone(replay.task._story_auto_state())
                with patch.object(replay.task, '_side_story_cost', side_effect=AssertionError('Obscured cost')):
                    self.assertEqual(replay.task._push_side_story(), 'navigation_timeout')
                self.assertEqual(replay.actions, [])


class StoryPushReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_first_supplied_sequence_advances_three_nodes(self):
        with ControlledClock() as clock:
            replay = EpisodeReplay(ordered(FIRST), clock)
            self.assertEqual(replay.task._push_side_story(limit=3), 'stage_limit')
            self.assertEqual(replay.task.completed_stages, 3)
            self.assertEqual(replay.clicks(SIDE_STORY_READY.name), [0, 1])
            self.assertEqual(replay.clicks(SIDE_STORY_WATCH.name), [11, 15])
            self.assertEqual(replay.clicks('EPISODE_STORY_SKIP_CONFIRM'), [3, 5, 9, 14, 18, 21])
            self.assertEqual(replay.clicks('AUTO_COMBAT'), [6])
            self.assertEqual(replay.index, 23)
            self.assertEqual(replay.clicks('POPUP_CONFIRM'), [])

    def test_second_supplied_sequence_restores_auto_after_story_and_returns(self):
        with ControlledClock() as clock:
            replay = EpisodeReplay(ordered(SECOND), clock)
            with patch.object(replay.task, '_last_star_condition', side_effect=AssertionError('Main-only condition')):
                self.assertEqual(replay.task._push_side_story(limit=2), 'stage_limit')
            self.assertEqual(replay.task.completed_stages, 2)
            self.assertEqual(replay.clicks(SIDE_STORY_WATCH.name), [0])
            self.assertEqual(replay.clicks(SIDE_STORY_READY.name), [2])
            self.assertEqual(replay.clicks(COMBAT_START.name), [3])
            self.assertEqual(replay.clicks(AUTO_COMBAT.name), [13, 14])
            self.assertEqual(replay.clicks('EPISODE_STORY_SKIP_CONFIRM'), [6, 8, 12, 17])
            self.assertEqual(replay.clicks('COMBAT_RESULT_CLEAR'), [19])
            self.assertEqual(replay.index, 20)
            for _, name, (x1, y1, x2, y2) in replay.positions:
                if name in (SIDE_STORY_WATCH.name, SIDE_STORY_READY.name):
                    self.assertTrue(960 <= x1 < x2 <= 1250 and 630 <= y1 < y2 <= 700)

    def test_main_loop_also_restores_manual_elite_after_story(self):
        frames = [captured('212502-109'), captured('212604-348'), captured('212606-278'),
                  captured('212607-478'), captured('212612-942', seconds=1),
                  captured('212614-702'), scene('COMBAT_RESULT_CLEAR'),
                  scene(map_ready=True, identity=(6, 1), selected=4)]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            self.assertEqual(replay.task._push(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks(AUTO_COMBAT.name), [3])
            self.assertEqual(replay.task.completed_stages, 1)

    def test_paid_watch_is_not_repeated_on_old_frames_or_delayed_debit(self):
        frames = [captured('210426-104', seconds=seconds) for seconds in (.5, 1, 1.1, 5)]
        frames += [captured('212346-548'), captured('212443-661')]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            self.assertEqual(replay.task._push_side_story(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks(SIDE_STORY_WATCH.name), [0])
            self.assertEqual(replay.clicks(SIDE_STORY_READY.name), [])
            self.assertEqual(replay.task.completed_stages, 1)

    def test_ready_retries_but_new_prepare_starts_without_old_cooldown(self):
        frames = [captured('212443-661', seconds=seconds) for seconds in (.5, 1, 1.1)]
        frames += [captured('212445-854', seconds=.1), captured('212502-109'),
                   captured('212717-178'), captured('212720-853')]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            self.assertEqual(replay.task._push_side_story(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks(SIDE_STORY_READY.name), [0, 2])
            self.assertEqual(replay.clicks(COMBAT_START.name), [3])
            self.assertEqual(replay.task.completed_stages, 1)

    def test_paid_watch_waits_through_debit_and_requires_entry_evidence(self):
        for late_cost in ((20, 0), None):
            frames = [story_list('人生就是一场豪赌', SIDE_STORY_WATCH.name, story_cost=(20, 20)),
                      story_list('人生就是一场豪赌', SIDE_STORY_WATCH.name, story_cost=late_cost, seconds=3),
                      captured('212346-548'), captured('212443-661')]
            with self.subTest(late_cost=late_cost), ControlledClock() as clock:
                replay = EpisodeReplay(frames, clock)
                with patch.object(replay.task, '_side_story_cost', wraps=replay.task._side_story_cost) as cost:
                    self.assertEqual(replay.task._push_side_story(limit=1), 'stage_limit')
                self.assertEqual(cost.call_count, 1)
                self.assertEqual(replay.clicks(SIDE_STORY_WATCH.name), [0])
                self.assertEqual(replay.task.completed_stages, 1)
        with ControlledClock() as clock:
            replay = EpisodeReplay([captured('210426-104'), captured('212443-661', seconds=50)], clock)
            self.assertEqual(replay.task._push_side_story(), 'result_unconfirmed')
            self.assertEqual(replay.clicks(SIDE_STORY_WATCH.name), [0])
            self.assertEqual(replay.clicks(SIDE_STORY_READY.name), [])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_skip_retry_does_not_delay_confirmation_or_limit_exit(self):
        frames = [captured('212604-348', seconds=seconds) for seconds in (.5, 1, 1.1)]
        frames += [captured('212606-278', seconds=.1), captured('212720-853', seconds=.1)]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            self.assertEqual(replay.task._push_side_story(limit=1), 'stage_limit')
            self.assertEqual(replay.actions, [(0, EPISODE_STORY_SKIP.name), (2, EPISODE_STORY_SKIP.name),
                                             (3, 'EPISODE_STORY_SKIP_CONFIRM')])
            self.assertEqual(replay.index, 4)

    def test_same_returned_list_cannot_replay_a_paid_story(self):
        frames = [captured('210426-104'), captured('212346-548')]
        frames += [captured('210426-104', seconds=10) for _ in range(5)]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            self.assertEqual(replay.task._push_side_story(), 'result_unconfirmed')
            self.assertEqual(replay.clicks(SIDE_STORY_WATCH.name), [0])
            self.assertEqual(replay.task.completed_stages, 0)

    def test_clear_repeated_frames_count_once_and_never_toggle_auto(self):
        frames = [captured('212443-661'), captured('212445-854')]
        frames += [captured('212717-178', seconds=seconds) for seconds in (.5, 1, 1.1)]
        frames += [captured('212720-853')]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            self.assertEqual(replay.task._push_side_story(limit=1), 'stage_limit')
            self.assertEqual(replay.clicks('COMBAT_RESULT_CLEAR'), [2, 4])
            self.assertEqual(replay.clicks(AUTO_COMBAT.name), [])
            self.assertEqual(replay.task.completed_stages, 1)

    def test_empty_balance_and_invalid_cost_stop_without_watch(self):
        for cost in ((20, 0), (20, 19), None):
            with self.subTest(cost=cost), ControlledClock() as clock:
                frames = [story_list('剧情', SIDE_STORY_WATCH.name, story_cost=cost, seconds=seconds)
                          for seconds in (.5, 13)]
                replay = EpisodeReplay(frames, clock)
                expected = 'unreadable_stamina' if cost is None else 'no_stamina'
                self.assertEqual(replay.task._push_side_story(), expected)
                self.assertEqual(replay.actions, [])

    def test_prepare_affordability_has_no_main_star_read(self):
        with ControlledClock() as clock:
            replay = EpisodeReplay([scene(REPEAT_COMBAT_MENU.name, COMBAT_START.name, stamina=(20, 0))], clock)
            with patch.object(replay.task, '_last_star_condition', side_effect=AssertionError('Main-only condition')):
                self.assertEqual(replay.task._push_side_story(), 'no_stamina')
            self.assertEqual(replay.actions, [])
        with ControlledClock() as clock:
            frames = [scene(REPEAT_COMBAT_MENU.name, stamina=cost)
                      for cost in ((20, 40), None, (20, 40), (20, 40), (20, 40), (20, 40))]
            replay = EpisodeReplay([*frames, scene(map_ready=True)], clock)
            with (
                patch.object(replay.task, '_prepare_stamina', wraps=replay.task._prepare_stamina) as read,
                patch.object(replay.task, '_last_star_condition', side_effect=AssertionError('Main-only condition')),
            ):
                self.assertEqual(replay.task._push_side_story(), 'wrong_start_page')
            self.assertEqual(read.call_count, 5)
            self.assertEqual(replay.actions, [])

    def test_wrong_mode_and_unavailable_auto_stop_without_clicks(self):
        with ControlledClock() as clock:
            replay = EpisodeReplay([scene(map_ready=True)], clock)
            self.assertEqual(replay.task._push_side_story(), 'wrong_start_page')
            self.assertEqual(replay.actions, [])
        with ControlledClock() as clock:
            replay = EpisodeReplay([scene(fixture='20261021-202808-776')], clock)
            with self.assertRaises(RequestHumanTakeover):
                replay.task._push_side_story()
            self.assertEqual(replay.actions, [])

    def test_run_dispatches_mode_and_ignores_legacy_episode_choice(self):
        for mode, method in (('Main', '_push'), ('Side', '_push_side_story')):
            with self.subTest(mode=mode), ControlledClock() as clock:
                replay = EpisodeReplay([scene(), scene()], clock)
                replay.task.config.Episode_Mode = mode
                replay.task.config.Episode_Number = 999
                with patch.object(replay.task, method, return_value='stage_limit') as run:
                    self.assertTrue(replay.task.run())
                self.assertEqual(run.call_args.args if mode == 'Side' else run.call_args.kwargs,
                                 (1,) if mode == 'Side' else {'limit': 1})

    def test_start_on_home_does_not_navigate(self):
        with ControlledClock() as clock:
            replay = EpisodeReplay([scene(), scene(main=True)], clock)
            self.assertFalse(replay.task.run())
            self.assertEqual(replay.task.stop_reason, 'wrong_start_page')
            self.assertEqual(replay.actions, [])

    def test_unsupported_side_language_does_not_capture(self):
        task = Episode.__new__(Episode)
        task.config = SimpleNamespace(Episode_Mode='Side', Episode_MaxStages=0)
        with patch('tasks.dungeon.episode_push.server.lang', 'cn'):
            self.assertFalse(task.run())

    def test_source_config_exposes_mode_and_hides_legacy_number(self):
        import yaml

        from module.config.config_generated import GeneratedConfig
        from module.config.config_updater import ConfigUpdater

        source = yaml.safe_load((ROOT / 'module/config/argument/argument.yaml').read_text(encoding='utf-8'))
        self.assertEqual(source['Episode']['Mode'], {'value': 'Main', 'option': ['Main', 'Side']})
        self.assertEqual(source['Episode']['Number']['display'], 'hide')
        self.assertEqual(GeneratedConfig.Episode_Mode, 'Main')
        updater = ConfigUpdater()
        for old, expected in (({'Number': 6, 'MaxStages': 5}, 'Main'),
                              ({'Mode': 'Side', 'Number': 6, 'MaxStages': 5}, 'Side')):
            with self.subTest(saved_mode=old.get('Mode')):
                updated = updater.config_update({'Episode': {'Episode': old}})
                # The existing updater resets hidden fields to their default.
                # Legacy episode choices no longer select a navigation target;
                # the visible mode and node limit must still survive migration.
                self.assertEqual(updated['Episode']['Episode'], {
                    'Mode': expected, 'Number': 0, 'MaxStages': 5,
                })
                self.assertFalse(updated['Episode']['Scheduler']['Enable'])
        for language in ('zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'es-ES'):
            text = json.loads((ROOT / f'module/config/i18n/{language}.json').read_text(encoding='utf-8'))
            self.assertTrue(text['Episode']['Mode']['Main'])
            self.assertTrue(text['Episode']['Mode']['Side'])
            self.assertTrue(text['Episode']['Mode']['help'])


class StoryIntroTests(unittest.TestCase):
    """Prepared templates and demonstration frames are separate evidence."""

    def setUp(self):
        server.set_lang('global_cn')

    def test_prepared_assets_and_demonstrations_match_without_title_text(self):
        from tasks.dungeon.assets.assets_dungeon_story_push_common import (
            STORY_HISTORY_CHECK,
        )

        manifest = json.loads((ROOT / 'tests/fixtures/historical/manifest.json').read_text(encoding='utf-8'))
        cases = [('title-asset', False, False), ('history-asset', True, False),
                 ('intro-before', True, False), ('intro-skip', True, True)]
        for name, history, skip in cases:
            with self.subTest(name=name), ControlledClock() as clock:
                key = '20261029-story-' + name
                replay = EpisodeReplay([scene(fixture=key)], clock)
                self.assertEqual(replay.task._story_intro_visible(), history)
                self.assertEqual(replay.task.match_template_color(STORY_HISTORY_CHECK, threshold=25), history)
                self.assertEqual(replay.task.appear(EPISODE_STORY_SKIP), skip)
                self.assertEqual(manifest['fixtures'][key]['capture_type'],
                                 'prepared_asset' if name.endswith('asset') else 'emulator_screenshot')
                self.assertEqual(replay.actions, [])

    def test_blank_reveal_latches_through_repeats_blackout_and_skip(self):
        before = scene(fixture='20261029-story-intro-before', seconds=.1)
        frames = [before, before, scene(seconds=5), before,
                  scene(fixture='20261029-story-intro-skip', seconds=.1),
                  scene('EPISODE_STORY_SKIP_CHECK', 'EPISODE_STORY_SKIP_CONFIRM',
                        EPISODE_STORY_SKIP.name, 'STORY_HISTORY_CHECK', seconds=.1),
                  scene('STORY_HISTORY_CHECK', seconds=5)]
        with ControlledClock() as clock:
            replay = EpisodeReplay(frames, clock)
            changed = []
            for index in range(len(frames)):
                if index:
                    replay.screenshot()
                changed.append(replay.task._handle_story_skip())
            self.assertEqual(changed, [True, False, False, False, True, True, False])
            self.assertEqual(replay.actions, [(0, 'STORY_INTRO_REVEAL'), (4, EPISODE_STORY_SKIP.name),
                                             (5, 'EPISODE_STORY_SKIP_CONFIRM')])
            self.assertEqual(replay.positions[0][2], (920, 150, 1000, 210))

    def test_visible_skip_or_confirmation_prevents_later_blank_click(self):
        for first, expected in ((scene(EPISODE_STORY_SKIP.name, 'STORY_HISTORY_CHECK'), EPISODE_STORY_SKIP.name),
                                (scene('EPISODE_STORY_SKIP_CHECK', 'EPISODE_STORY_SKIP_CONFIRM',
                                       EPISODE_STORY_SKIP.name), 'EPISODE_STORY_SKIP_CONFIRM')):
            with self.subTest(first=expected), ControlledClock() as clock:
                frames = [first, scene(seconds=3), scene('STORY_HISTORY_CHECK'),
                          scene(fixture='20261029-story-intro-before')]
                replay = EpisodeReplay(frames, clock)
                self.assertTrue(replay.task._handle_story_skip())
                for _ in frames[1:]:
                    replay.screenshot()
                    self.assertFalse(replay.task._handle_story_skip())
                self.assertEqual(replay.actions, [(0, expected)])

    def test_unrecognized_or_dimmed_frames_never_reveal(self):
        stamps = ['210426-104', '212443-661', '212445-854', '212502-109',
                  '212607-478', '212612-942', '212717-178']
        for frame in [scene(), *(captured(stamp) for stamp in stamps)]:
            with self.subTest(frame=frame), ControlledClock() as clock:
                replay = EpisodeReplay([frame], clock)
                self.assertFalse(replay.task._story_visible())
                self.assertFalse(replay.task._handle_story_skip())
                self.assertEqual(replay.actions, [])
        with ControlledClock() as clock:
            replay = EpisodeReplay([scene(fixture='20261029-story-intro-before')], clock)
            replay.image = (replay.image * .4).astype('uint8')
            self.assertFalse(replay.task._story_visible())
            self.assertFalse(replay.task._handle_story_skip())
            self.assertEqual(replay.actions, [])

    def test_both_loops_reveal_and_reset_on_returned_map_or_list(self):
        from tasks.dungeon.episode_push import StoryStage

        before = scene(fixture='20261029-story-intro-before')
        skip = scene(fixture='20261029-story-intro-skip', seconds=.1)
        confirm = scene('EPISODE_STORY_SKIP_CHECK', 'EPISODE_STORY_SKIP_CONFIRM', seconds=.1)
        for mode in ('Main', 'Side'):
            with self.subTest(mode=mode), ControlledClock() as clock:
                if mode == 'Main':
                    frames = [scene(map_ready=True, rows=[StoryStage(3, (900, 300, 1200, 350), False, False)]),
                              before, before, scene(seconds=5), skip, confirm, scene('EPISODE_STORY_COMPLETE'),
                              scene(map_ready=True, selected=4, rows=[StoryStage(4, (900, 300, 1200, 350), False, False)], seconds=3),
                              before, skip, confirm, scene('EPISODE_STORY_COMPLETE'), scene(map_ready=True)]
                    expected_reveals, expected_skips, expected_confirm = [1, 8], [4, 9], [5, 10]
                else:
                    frames = [story_list('第一段', SIDE_STORY_WATCH.name, story_cost=(20, 100)),
                              before, before, scene(seconds=5), skip, confirm,
                              story_list('第二段', SIDE_STORY_WATCH.name, story_cost=(20, 80), seconds=3),
                              before, skip, confirm, story_list('第三段', SIDE_STORY_WATCH.name)]
                    expected_reveals, expected_skips, expected_confirm = [1, 7], [4, 8], [5, 9]
                replay = EpisodeReplay(frames, clock)
                run = replay.task._push if mode == 'Main' else replay.task._push_side_story
                self.assertEqual(run(limit=2), 'stage_limit')
                self.assertEqual(replay.task.completed_stages, 2)
                self.assertEqual(replay.clicks('STORY_INTRO_REVEAL'), expected_reveals)
                self.assertEqual(replay.clicks(EPISODE_STORY_SKIP.name), expected_skips)
                self.assertEqual(replay.clicks('EPISODE_STORY_SKIP_CONFIRM'), expected_confirm)
                self.assertEqual(replay.index, len(frames) - 1)

    def test_positive_battle_prepare_supporter_and_result_rearm_reveal(self):
        from tasks.dungeon.assets.assets_dungeon_configs_side_story_entry import (
            SUPPORTER_CHECK,
        )

        resets = [captured('212502-109'), scene(REPEAT_COMBAT_MENU.name, stamina=(20, 40)),
                  scene(SUPPORTER_CHECK.name), scene('COMBAT_RESULT_CLEAR')]
        for mode in ('Main', 'Side'):
            for reset in resets:
                with self.subTest(mode=mode, reset=reset), ControlledClock() as clock:
                    terminal = scene(map_ready=True) if mode == 'Main' else story_list('下一段', SIDE_STORY_WATCH.name)
                    frames = [scene(fixture='20261029-story-intro-before'), reset,
                              scene(fixture='20261029-story-intro-before', seconds=.1),
                              scene('COMBAT_RESULT_CLEAR'), terminal]
                    replay = EpisodeReplay(frames, clock)
                    run = replay.task._push if mode == 'Main' else replay.task._push_side_story
                    self.assertEqual(run(limit=1), 'stage_limit')
                    self.assertEqual(replay.clicks('STORY_INTRO_REVEAL'), [0, 2])
                    self.assertEqual(replay.clicks(AUTO_COMBAT.name), [])
                    self.assertEqual(replay.clicks(COMBAT_START.name), [])

    def test_starting_another_loop_rearms_the_same_task(self):
        for mode in ('Main', 'Side'):
            with self.subTest(mode=mode), ControlledClock() as clock:
                terminal = scene(map_ready=True) if mode == 'Main' else story_list('下一段', SIDE_STORY_WATCH.name)
                frames = [scene(fixture='20261029-story-intro-before'), scene('COMBAT_RESULT_CLEAR'), terminal] * 2
                replay = EpisodeReplay(frames, clock)
                run = replay.task._push if mode == 'Main' else replay.task._push_side_story
                self.assertEqual(run(limit=1), 'stage_limit')
                replay.screenshot()
                replay.task.completed_stages = 0
                self.assertEqual(run(limit=1), 'stage_limit')
                self.assertEqual(replay.clicks('STORY_INTRO_REVEAL'), [0, 3])


    def test_history_alone_reveals_with_unrelated_backgrounds(self):
        from tasks.dungeon.assets.assets_dungeon_story_push_common import (
            STORY_HISTORY_CHECK,
        )

        for color in ((0, 0, 0), (45, 76, 130), (180, 150, 110)):
            with self.subTest(color=color), ControlledClock() as clock:
                replay = EpisodeReplay([scene(fixture='20261029-story-intro-before')], clock)
                left, top, right, bottom = STORY_HISTORY_CHECK.area
                icon = replay.image[top:bottom, left:right].copy()
                # Synthetic backdrops remove every trace of this event's card.
                # These are matcher-isolation inputs, not captures of other stories.
                replay.image[:] = color
                replay.image[top:bottom, left:right] = icon
                self.assertTrue(replay.task._story_visible())
                self.assertTrue(replay.task._handle_story_skip())
                self.assertFalse(replay.task._handle_story_skip())
                self.assertEqual(replay.actions, [(0, 'STORY_INTRO_REVEAL')])

    def test_missing_history_waits_for_skip_even_with_title_artwork(self):
        with ControlledClock() as clock:
            frames = [scene(fixture='20261029-story-intro-before'),
                      scene(fixture='20261029-story-intro-skip', seconds=.1)]
            replay = EpisodeReplay(frames, clock)
            # Remove only the history control; keep the event artwork intact.
            # Its presence must not authorize a blank tap without story controls.
            replay.image[0:80, 1060:1140] = 0
            self.assertFalse(replay.task._story_visible())
            self.assertFalse(replay.task._handle_story_skip())
            self.assertEqual(replay.actions, [])
            replay.screenshot()
            replay.image[0:80, 1060:1140] = 0
            self.assertTrue(replay.task._story_visible())
            self.assertTrue(replay.task._handle_story_skip())
            self.assertEqual(replay.actions, [(1, EPISODE_STORY_SKIP.name)])
