"""CN daily-list OCR boundaries and bounded, time-faithful collection."""

import importlib.util
import sys
import unittest
from contextlib import ExitStack

from module.config import server
from tests.support.daily_missions import (
    CAPTURES,
    EXPECTED,
    HOME,
    VISIBLE_KEYS,
    DailyScanReplay,
    capture_probe,
    mission_scene,
    scan_frames,
)
from tests.support.offline import ControlledClock, record_frame


class DailyCase(unittest.TestCase):
    def setUp(self):
        previous_lang, previous_server = server.lang, server.server
        self.addCleanup(server.set_lang, previous_lang)
        self.addCleanup(setattr, server, 'server', previous_server)
        server.set_lang('cn')
        server.server = 'CN-Official'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())


class DailyCaptureTests(DailyCase):
    def test_real_cn_capture_identifies_only_first_two_reward_chests_as_ready(self):
        task = capture_probe(CAPTURES[0])
        self.assertEqual(
            [button.match_color(task.device.image, threshold=task.REWARD_COLOR_THRESHOLD)
             for button in task.REWARD_BUTTONS],
            [True, True, False, False, False, False],
        )
        self.assertIs(
            task._get_ready_reward_button(task.REWARD_BUTTONS, task.REWARD_ACTION),
            task.REWARD_BUTTONS[0],
        )

    def test_real_cn_captures_read_complete_rows_and_activity(self):
        for fixture, keys in zip(CAPTURES, VISIBLE_KEYS):
            with self.subTest(fixture=fixture):
                task = capture_probe(fixture)
                self.assertTrue(task._daily_page_ready())
                self.assertEqual(task._read_daily_points(), 25)
                rows = task._read_daily_missions()
                self.assertIsNotNone(rows)
                self.assertEqual(tuple(row.key for row in rows), keys)
                self.assertEqual(
                    [(row.current, row.total, row.points) for row in rows],
                    [EXPECTED[key] for key in keys],
                )

    def test_real_cn_captures_collect_all_thirteen_daily_missions(self):
        seen = {}
        for fixture in CAPTURES:
            task = capture_probe(fixture)
            rows = task._read_daily_missions()
            self.assertIsNotNone(rows, fixture)
            for row in rows:
                if row.key in seen:
                    self.assertEqual(row, seen[row.key], fixture)
                seen[row.key] = row
        self.assertEqual(set(seen), set(EXPECTED))
        self.assertEqual(len(seen), 13)
        self.assertEqual(seen['login'].current, 1)
        self.assertEqual(sum(row.points for row in seen.values() if row.current >= row.total), 25)

    def test_partial_title_or_progress_is_ignored(self):
        # The first and last visible cards differ across these real captures.
        # A cropped decoration is allowed; a missing title or progress is not.
        excluded = (
            {'gacha'}, {'core_1'}, {'stamina_100'},
            {'core_3'}, {'arena_10'}, {'core_2'},
        )
        for fixture, expected_absent in zip(CAPTURES, excluded):
            with self.subTest(fixture=fixture):
                rows = capture_probe(fixture)._read_daily_missions()
                self.assertIsNotNone(rows)
                keys = {row.key for row in rows}
                self.assertFalse(keys & expected_absent)
                self.assertEqual(len(keys), 3)

    def test_unreadable_complete_progress_keeps_frame_unknown(self):
        task = capture_probe(CAPTURES[0])
        task.device.image[286:311, 510:590] = 0
        record_frame('synthetic:daily-first-progress-obscured')
        self.assertIsNone(task._read_daily_missions())

    def test_homepage_does_not_create_daily_state(self):
        task = capture_probe(HOME)
        self.assertFalse(task._daily_page_ready())
        self.assertIsNone(task._read_daily_missions())

    def test_dimmed_daily_page_does_not_read_missions(self):
        task = capture_probe(CAPTURES[0])
        task.device.image = (task.device.image * 0.4).astype('uint8')
        record_frame('synthetic:daily-page-darkened-from-cn-capture')
        self.assertFalse(task._daily_page_ready())
        self.assertIsNone(task._read_daily_missions())


class DailyImportTests(DailyCase):
    def test_overseas_import_can_later_use_cn_assets_in_same_process(self):
        from tasks.mission_reward import daily

        server.set_lang('global_cn')
        name = '_daily_import_contract'
        spec = importlib.util.spec_from_file_location(name, daily.__file__)
        module = importlib.util.module_from_spec(spec)
        self.addCleanup(sys.modules.pop, name, None)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        self.assertEqual(module.OCR_DAILY_MISSIONS.area, (312, 190, 980, 654))
        server.set_lang('global_en')
        task = module.DailyMissionMixin()
        self.assertIsNone(task._read_daily_missions())
        self.assertIsNone(task._scan_daily_missions())
        server.set_lang('cn')
        self.assertEqual(module.OCR_DAILY_MISSIONS.area, (312, 190, 980, 654))


class DailyScanTests(DailyCase):
    def test_complete_scan_collects_thirteen_keys_with_overlap_and_repeated_frames(self):
        frames = scan_frames()
        replay = DailyScanReplay(frames, self.clock)
        rows = replay.task._scan_daily_missions(skip_first_screenshot=True)
        self.assertIsNotNone(rows)
        self.assertEqual({row.key for row in rows}, set(EXPECTED))
        self.assertEqual(len(rows), 13)
        self.assertEqual(len(replay.swipes), 5)
        self.assertTrue(all(start[1] > end[1] for _, start, end, _ in replay.swipes))
        self.assertTrue(all(action == 'swipe' for _, action in replay.actions), replay.actions)
        replay.assert_swipe_intervals(self)

    def test_completed_login_stops_without_another_swipe_or_bottom_probe(self):
        frames = scan_frames()
        replay = DailyScanReplay(frames, self.clock)
        rows = replay.task._scan_daily_missions(skip_first_screenshot=True)
        self.assertEqual({row.key: (row.current, row.total, row.points) for row in rows}, EXPECTED)
        self.assertEqual(len(replay.swipes), 5)
        self.assertLess(replay.index, len(frames) - 2)
        self.assertTrue(all(start[1] > end[1] for _, start, end, _ in replay.swipes))
        replay.assert_swipe_intervals(self)

    def test_login_without_complete_progress_does_not_end_the_scan(self):
        expected = {**EXPECTED, 'login': (0, 1, 25)}
        frames = [mission_scene(('login',), expected=expected, points=0)] * 35
        replay = DailyScanReplay(frames, self.clock)
        self.assertIsNone(replay.task._scan_daily_missions())
        self.assertGreaterEqual(len(replay.swipes), 2)
        self.assertTrue(all(start[1] > end[1] for _, start, end, _ in replay.swipes))

    def test_conflicting_overlapping_progress_never_proves_complete_input(self):
        frames = scan_frames()
        from tasks.mission_reward.daily import DailyMission

        for frame in frames[4:9]:
            frame['missions'] = tuple(
                DailyMission('arena_5', 1, 5, 30) if row.key == 'arena_5' else row
                for row in frame['missions']
            )
        replay = DailyScanReplay(frames, self.clock)
        self.assertIsNone(replay.task._scan_daily_missions())
        self.assertEqual(len(replay.swipes), 1)

    def test_missing_mission_never_turns_a_partial_list_into_complete(self):
        panels = tuple(tuple(key for key in keys if key != 'gacha') for keys in VISIBLE_KEYS)
        frames = scan_frames(panels)
        frames.extend([mission_scene(VISIBLE_KEYS[-1])] * 45)
        replay = DailyScanReplay(frames, self.clock)
        self.assertIsNone(replay.task._scan_daily_missions(skip_first_screenshot=True))
        self.assertLess(replay.index, len(frames) - 1)
        self.assertTrue(all(action == 'swipe' for _, action in replay.actions), replay.actions)
        replay.assert_swipe_intervals(self)

    def test_overlay_is_closed_before_reading_or_scrolling_visible_daily_tab(self):
        frames = [mission_scene(VISIBLE_KEYS[0], dimmed=True)] * 6 + scan_frames()
        replay = DailyScanReplay(frames, self.clock)
        rows = replay.task._scan_daily_missions(skip_first_screenshot=True)
        self.assertIsNotNone(rows)
        self.assertEqual({row.key for row in rows}, set(EXPECTED))
        self.assertTrue(replay.clicks('TOUCH_TO_CLOSE'))
        self.assertTrue(all(index < 6 for index in replay.clicks('TOUCH_TO_CLOSE')))
        self.assertTrue(all(index >= 6 for index in replay.reads))
        self.assertTrue(all(index >= 6 for index, _, _, _ in replay.swipes))
        replay.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        replay.assert_swipe_intervals(self)

    def test_unresponsive_swipes_stop_without_claiming_a_complete_list(self):
        frames = [mission_scene(VISIBLE_KEYS[0])] * 100
        replay = DailyScanReplay(frames, self.clock)
        self.assertIsNone(replay.task._scan_daily_missions(skip_first_screenshot=True))
        self.assertLess(replay.index, 99)
        self.assertGreater(len(replay.swipes), 1)
        self.assertLessEqual(len(replay.swipes), 12)
        self.assertTrue(all(action == 'swipe' for _, action in replay.actions), replay.actions)
        replay.assert_swipe_intervals(self)

    def test_unreadable_frames_cannot_be_counted_as_stable_complete_observations(self):
        frames = [mission_scene(readable=False)] * 140
        replay = DailyScanReplay(frames, self.clock)
        self.assertIsNone(replay.task._scan_daily_missions(skip_first_screenshot=True))
        self.assertLess(replay.index, 139)
        self.assertEqual(replay.actions, [])
