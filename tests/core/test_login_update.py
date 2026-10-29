"""International Chinese update screens and Google Play update replay."""

import unittest
from unittest.mock import Mock, patch

from module.config import server

server.set_lang('global_cn')

from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.login.assets.assets_login import GAME_UPGRADE_AVAILABLE, PATCH_APPLY
from tasks.login.assets.assets_login_update import (
    GOOGLE_PLAY_PLAY,
    GOOGLE_PLAY_UPDATE,
    PATCH_CHECKING,
    PATCH_DOWNLOADING,
)
from tasks.login.login import Login
from tasks.login.update import GameUpdateFailed, UpdateHandler
from tests.support.interaction import scene
from tests.support.login_update import MANIFEST, LoginUpdateReplay, capture
from tests.support.offline import ControlledClock, fixture_image

CAPTURES = ('194637-390', '194638-366', '194640-422', '194642-189', '194645-647',
            '194651-922', '194703-928', '194709-969', '194712-614', '194725-016',
            '194905-434', '194911-593', '194922-656')


class UpdateCaptureTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_version_notice_matches_new_dialog(self):
        for name in CAPTURES:
            with self.subTest(frame=name):
                image = fixture_image(MANIFEST, f'20261029-{name}')
                self.assertEqual(GAME_UPGRADE_AVAILABLE.match_template(image), name == '194637-390')
                if name == '194637-390':
                    self.assertTrue(TOUCH_TO_CLOSE.match_template(image))
                    self.assertEqual(TOUCH_TO_CLOSE.button, (628, 554, 651, 569))

    def test_patch_confirmation_targets_proceed_button(self):
        for name in CAPTURES:
            with self.subTest(frame=name):
                image = fixture_image(MANIFEST, f'20261029-{name}')
                self.assertEqual(PATCH_APPLY.match_template(image), name == '194905-434')
                if name == '194905-434':
                    left, top, right, bottom = PATCH_APPLY.button
                    self.assertTrue(656 <= left < right <= 888)
                    self.assertTrue(521 <= top < bottom <= 574)

    def test_google_play_buttons_have_positive_and_negative_captures(self):
        for name in CAPTURES:
            with self.subTest(frame=name):
                image = fixture_image(MANIFEST, f'20261029-{name}')
                self.assertEqual(GOOGLE_PLAY_UPDATE.match_template(image), name == '194642-189')
                self.assertEqual(GOOGLE_PLAY_PLAY.match_template(image), name == '194703-928')

    def test_patch_checking_and_downloading_are_read_only_distinct_states(self):
        for name in CAPTURES:
            with self.subTest(frame=name):
                image = fixture_image(MANIFEST, f'20261029-{name}')
                self.assertEqual(PATCH_CHECKING.match_template(image), name in ('194712-614', '194725-016'))
                self.assertEqual(PATCH_DOWNLOADING.match_template(image), name in ('194911-593', '194922-656'))


class UpdateReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def test_version_notice_opens_store_and_updates_until_play(self):
        replay = LoginUpdateReplay(UpdateHandler, [capture(n) for n in (
            '194637-390', '194642-189', '194645-647', '194651-922', '194703-928'
        )], self.clock)
        self.assertTrue(replay.task.handle_game_update())
        self.assertEqual(replay.actions, [(0, 'TOUCH_TO_CLOSE'), (1, 'GOOGLE_PLAY_UPDATE')])
        self.assertEqual(replay.index, 4)
        self.assertEqual(replay.click_areas, [
            (0, 'TOUCH_TO_CLOSE', (628, 554, 651, 569)),
            (1, 'GOOGLE_PLAY_UPDATE', (138, 465, 209, 487)),
        ])
        self.assertEqual(replay.sleeps, [])
        self.assertEqual(replay.lifecycle, [])

    def test_notice_and_update_retry_only_after_their_own_intervals(self):
        frames = [capture('194637-390')] * 8 + [capture('194642-189')] * 13
        frames += [capture(n) for n in ('194645-647', '194651-922', '194703-928')]
        replay = LoginUpdateReplay(UpdateHandler, frames, self.clock)
        self.assertTrue(replay.task.handle_game_update())
        self.assertEqual(replay.clicks('TOUCH_TO_CLOSE'), [0, 7])
        self.assertEqual(replay.clicks('GOOGLE_PLAY_UPDATE'), [8, 19])
        replay.assert_retried(self, 'TOUCH_TO_CLOSE', 3)
        replay.assert_retried(self, 'GOOGLE_PLAY_UPDATE', 5)
        self.assertEqual(replay.index, 23)
        self.assertEqual(len(replay.actions), 4)

    def test_pending_cancelling_and_download_never_click_cancel_or_uninstall(self):
        replay = LoginUpdateReplay(UpdateHandler, [capture(n) for n in (
            '194638-366', '194640-422', '194645-647', '194651-922', '194703-928'
        )], self.clock)
        self.assertFalse(replay.task._handle_google_play_update())
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.index, 4)

    def test_already_ready_exits_on_prepared_frame_without_clicking_play(self):
        replay = LoginUpdateReplay(UpdateHandler, [capture('194703-928')], self.clock)
        replay.task.interval_reset(GOOGLE_PLAY_UPDATE, interval=5)
        self.assertFalse(replay.task._handle_google_play_update())
        self.assertEqual(replay.index, 0)
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.sleeps, [])

    def test_fresh_first_screenshot_and_completion_bypass_click_interval(self):
        replay = LoginUpdateReplay(UpdateHandler, [scene(), capture('194642-189'),
                                                  capture('194703-928')], self.clock)
        self.assertTrue(replay.task._handle_google_play_update(skip_first_screenshot=False))
        self.assertEqual(replay.actions, [(1, 'GOOGLE_PLAY_UPDATE')])
        self.assertEqual(replay.index, 2)

    def test_completion_has_priority_over_timeout_and_retryable_buttons(self):
        replay = LoginUpdateReplay(UpdateHandler, [scene(), scene(
            'GOOGLE_PLAY_PLAY', 'GOOGLE_PLAY_UPDATE', seconds=301
        )], self.clock, guards=False)
        self.assertFalse(replay.task._handle_google_play_update())
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.lifecycle, [])

    def test_unknown_screen_times_out_without_actions(self):
        replay = LoginUpdateReplay(UpdateHandler, [scene(seconds=1)] * 303, self.clock, guards=False)
        with self.assertRaisesRegex(GameUpdateFailed, 'Google Play update timeout'):
            replay.task._handle_google_play_update()
        self.assertEqual(replay.index, 301)
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.lifecycle, [(301, 'app_stop')])

    def test_failed_update_clicks_do_not_extend_original_deadline(self):
        replay = LoginUpdateReplay(UpdateHandler, [scene('GOOGLE_PLAY_UPDATE', seconds=1)] * 303,
                                   self.clock, guards=False)
        with self.assertRaisesRegex(GameUpdateFailed, 'Google Play update timeout'):
            replay.task._handle_google_play_update()
        self.assertEqual(replay.index, 301)
        self.assertEqual(replay.clicks('GOOGLE_PLAY_UPDATE'), list(range(0, 301, 6)))
        self.assertEqual(replay.lifecycle, [(301, 'app_stop')])

    def test_public_handler_leaves_cn_and_non_update_screens_untouched(self):
        for package, frame in (('CN-Official', capture('194637-390')),
                               ('OVERSEA-Play', capture('194709-969'))):
            with self.subTest(package=package):
                replay = LoginUpdateReplay(UpdateHandler, [frame], self.clock)
                replay.task.config.Emulator_PackageName = package
                self.assertFalse(replay.task.handle_game_update())
                self.assertEqual(replay.actions, [])
                self.assertEqual(replay.index, 0)


class LoginUpdateFlowTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def login(self, frames):
        replay = LoginUpdateReplay(Login, [scene(), *frames], self.clock)
        replay.task.is_in_main = lambda **kwargs: replay.frame['page'] == 'main'
        return replay

    def test_login_restarts_once_only_after_store_reports_play(self):
        replay = self.login([capture('194637-390', seconds=6), capture('194642-189'),
                             capture('194645-647'), capture('194651-922'), capture('194703-928')])
        replay.task.app_restart = Mock(side_effect=lambda: replay.lifecycle.append((replay.index, 'restart')))
        self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.actions, [(1, 'TOUCH_TO_CLOSE'), (2, 'GOOGLE_PLAY_UPDATE')])
        self.assertEqual(replay.lifecycle, [(5, 'restart')])
        replay.task.app_restart.assert_called_once_with()
        self.assertEqual(replay.index, 5)

    def test_long_patch_scan_and_download_wait_then_enter_main(self):
        frames = [capture('194712-614', seconds=6)]
        frames += [capture('194725-016', seconds=1)] * 130
        frames += [capture('194905-434')] * 8
        frames += [capture('194911-593'), capture('194922-656', seconds=1)]
        frames += [capture('194922-656', seconds=1)] * 130
        frames += [scene('LOGIN_CONFIRM')]
        frames += [scene(page='main')] * 6
        replay = self.login(frames)
        with patch('tasks.activity.entry.SpecialActivityEntry') as reward_type:
            reward_type.return_value.run_login_daily_reward.return_value = True
            self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.clicks('PATCH_APPLY'), [132, 139])
        self.assertEqual(replay.clicks('LOGIN_CONFIRM'), [272])
        self.assertEqual(len(replay.actions), 3)
        self.assertEqual(replay.frame['page'], 'main')
        self.assertEqual(replay.lifecycle, [])
        reward_type.return_value.run_login_daily_reward.assert_called_once_with()

    def test_popup_handoff_recognizes_each_new_patch_progress_state(self):
        for name in ('194712-614', '194725-016', '194911-593', '194922-656'):
            with self.subTest(frame=name):
                replay = self.login([capture(name)])
                replay.screenshot()
                self.assertTrue(replay.task._is_login_progress_state())
                self.assertEqual(replay.actions, [])

    def test_cn_progress_does_not_access_oversea_only_assets(self):
        server.set_lang('cn')
        replay = self.login([scene('LOGIN_CONFIRM')])
        replay.task.config.Emulator_PackageName = 'CN-Official'
        replay.screenshot()
        self.assertTrue(replay.task._is_login_progress_state())
        self.assertEqual(replay.actions, [])
