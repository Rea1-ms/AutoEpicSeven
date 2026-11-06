"""Login readiness, recovery, and client/patch update regressions."""

import unittest
from unittest.mock import Mock, patch

from module.config import server

server.set_lang('global_cn')

from module.config.config import TaskEnd
from module.exception import GameNotRunningError, RequestHumanTakeover
from module.ocr.ocr import OcrWhiteLetterOnComplexBackground
from tasks.base.assets.assets_base_popup import (
    NETWORK_ERROR_ABNORMAL,
    NETWORK_ERROR_DISCONNECT,
    TOUCH_TO_CLOSE,
)
from tasks.base.ui import UI
from tasks.login.assets.assets_login import (
    GAME_UPGRADE_AVAILABLE,
    LOGGED_OUT,
    LOGIN_CONFIRM,
    LOGIN_ERROR,
    OCR_LOGIN_START,
    PATCH_APPLY,
)
from tasks.login.assets.assets_login_maintenance import ANNOUNCEMENT_CLOSE
from tasks.login.assets.assets_login_update import (
    CLIENT_DOWNLOADING,
    CLIENT_INSTALL_CONFIRM,
    CLIENT_UPDATE_CONFIRM,
    GOOGLE_PLAY_PLAY,
    GOOGLE_PLAY_UPDATE,
    PACKAGE_INSTALL_COMPLETE,
    PACKAGE_INSTALL_CONFIRM,
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
CN_CAPTURES = ('220435-623', '220437-897', '220443-735', '220447-999', '220451-467',
               '221739-381', '221833-219', '221842-942', '222010-024', '222013-948', '222031-002')
START_CAPTURES = ('login-start-default-20261105', 'login-start-event-20261105')


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
        frames += [scene(fixture=START_CAPTURES[0])]
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


class CnUpdateTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('cn')
        self.addCleanup(server.set_lang, 'global_cn')
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames, task_type=UpdateHandler, **kwargs):
        replay = LoginUpdateReplay(task_type, frames, self.clock, **kwargs)
        replay.task.config.Emulator_PackageName = 'CN-Official'
        return replay

    def login(self, frames):
        replay = self.replay([scene(), *frames], Login)
        replay.task.is_in_main = lambda **kwargs: replay.frame['page'] == 'main'
        return replay

    def test_patch_confirm_and_progress_match_only_their_own_captures(self):
        for name in CN_CAPTURES:
            with self.subTest(frame=name):
                image = fixture_image(MANIFEST, f'20261029-{name}')
                self.assertEqual(PATCH_APPLY.match_template(image), name == '222010-024')
                self.assertEqual(PATCH_CHECKING.match_template(image), name in ('221739-381', '221842-942'))
                self.assertEqual(PATCH_DOWNLOADING.match_template(image), name in ('222013-948', '222031-002'))
                if name == '222010-024':
                    self.assertEqual(PATCH_APPLY.button, (689, 529, 854, 565))

    def test_long_patch_scan_and_download_continue_without_restart(self):
        frames = [capture('221739-381', seconds=6)]
        frames += [capture('221842-942', seconds=1)] * 130
        frames += [capture('222010-024')] * 8
        frames += [capture('222013-948'), capture('222031-002', seconds=1)]
        frames += [capture('222031-002', seconds=1)] * 130
        frames += [scene('LOGIN_CONFIRM')] + [scene(page='main')] * 6
        replay = self.login(frames)
        with patch('tasks.activity.entry.SpecialActivityEntry') as reward_type:
            reward_type.return_value.run_login_daily_reward.return_value = True
            self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.clicks('PATCH_APPLY'), [132, 139])
        self.assertEqual(replay.clicks('LOGIN_CONFIRM'), [272])
        self.assertEqual(len(replay.actions), 3)
        self.assertEqual(replay.frame['page'], 'main')
        self.assertEqual(replay.lifecycle, [])
        self.assertEqual(replay.sleeps, [])

    def test_popup_handoff_accepts_new_cn_patch_and_client_states(self):
        for name in ('220435-623', '220437-897', '220443-735', '220447-999', '220451-467',
                     '221739-381', '221842-942', '222010-024', '222013-948', '222031-002'):
            with self.subTest(frame=name):
                replay = self.replay([capture(name)], Login)
                self.assertTrue(replay.task._is_login_progress_state())
                self.assertEqual(replay.actions, [])

    def test_client_states_match_only_their_own_captures(self):
        states = ((CLIENT_UPDATE_CONFIRM, '220435-623'), (CLIENT_DOWNLOADING, '220437-897'),
                  (CLIENT_INSTALL_CONFIRM, '220443-735'), (PACKAGE_INSTALL_CONFIRM, '220447-999'),
                  (PACKAGE_INSTALL_COMPLETE, '220451-467'))
        for name in CN_CAPTURES:
            image = fixture_image(MANIFEST, f'20261029-{name}')
            for button, expected in states:
                with self.subTest(frame=name, state=button.name):
                    self.assertEqual(button.match_template(image), name == expected)

    def test_client_download_install_and_portrait_confirmation_finish_before_restart(self):
        replay = self.replay([capture(name) for name in CN_CAPTURES[:5]])
        self.assertTrue(replay.task.handle_game_update())
        self.assertEqual(replay.actions, [(0, 'CLIENT_UPDATE_CONFIRM'), (2, 'CLIENT_INSTALL_CONFIRM'),
                                          (3, 'PACKAGE_INSTALL_CONFIRM')])
        self.assertEqual(replay.click_areas[:2], [
            (0, 'CLIENT_UPDATE_CONFIRM', (518, 490, 749, 548)),
            (2, 'CLIENT_INSTALL_CONFIRM', (518, 490, 749, 548)),
        ])
        self.assertEqual(replay.native_taps, [(3, 590, 736)])
        self.assertEqual(replay.index, 4)
        self.assertEqual(replay.lifecycle, [])
        self.assertEqual(replay.sleeps, [])

    def test_failed_clicks_retry_independently_and_never_cancel_installation(self):
        frames = [capture('220435-623')] * 8 + [capture('220437-897')]
        frames += [capture('220443-735')] * 8 + [capture('220447-999')] * 8
        frames += [capture('220451-467')]
        replay = self.replay(frames)
        self.assertTrue(replay.task.handle_game_update())
        self.assertEqual(replay.clicks('CLIENT_UPDATE_CONFIRM'), [0, 7])
        self.assertEqual(replay.clicks('CLIENT_INSTALL_CONFIRM'), [9, 16])
        self.assertEqual(replay.clicks('PACKAGE_INSTALL_CONFIRM'), [17, 24])
        for name in ('CLIENT_UPDATE_CONFIRM', 'CLIENT_INSTALL_CONFIRM', 'PACKAGE_INSTALL_CONFIRM'):
            replay.assert_retried(self, name, 3)
        self.assertEqual(len(replay.actions), 6)
        self.assertEqual(replay.index, 25)

    def test_long_client_download_is_read_only(self):
        replay = self.replay([capture('220437-897', seconds=1)] * 130 + [capture('220451-467')])
        self.assertFalse(replay.task.handle_game_update())
        self.assertEqual(replay.index, 130)
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.lifecycle, [])

    def test_installed_frame_exits_immediately_without_open_or_done_click(self):
        replay = self.replay([capture('220451-467')])
        replay.task.interval_reset(CLIENT_INSTALL_CONFIRM, interval=3)
        self.assertFalse(replay.task._handle_cn_app_update())
        self.assertEqual(replay.index, 0)
        self.assertEqual(replay.actions, [])

    def test_new_screenshot_and_completion_take_priority_over_timeout(self):
        replay = self.replay([scene(), capture('220451-467', seconds=301)], guards=False)
        self.assertFalse(replay.task._handle_cn_app_update(skip_first_screenshot=False))
        self.assertEqual(replay.index, 1)
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.lifecycle, [])

    def test_unknown_screen_and_failed_clicks_have_a_fixed_update_deadline(self):
        for buttons in ((), ('CLIENT_UPDATE_CONFIRM',)):
            with self.subTest(buttons=buttons):
                replay = self.replay([scene(*buttons, seconds=1)] * 303, guards=False)
                with self.assertRaisesRegex(GameUpdateFailed, 'CN client update timeout'):
                    replay.task._handle_cn_app_update()
                self.assertEqual(replay.index, 301)
                self.assertEqual(replay.lifecycle, [(301, 'app_stop')])
                expected = list(range(0, 301, 4)) if buttons else []
                self.assertEqual(replay.clicks('CLIENT_UPDATE_CONFIRM'), expected)
                self.assertEqual(len(replay.actions), len(expected))

    def test_other_app_portrait_confirmation_is_not_clicked(self):
        replay = self.replay([capture('220447-999')])
        self.assertIsNotNone(UpdateHandler._cn_package_install_button(replay.task))
        replay.image[558:610, 89:434] = 0
        self.assertIsNone(UpdateHandler._cn_package_install_button(replay.task))
        self.assertEqual(replay.actions, [])

    def test_login_allows_updater_to_leave_game_and_restarts_after_installation(self):
        frames = [scene(seconds=6), capture('220435-623'), capture('220437-897', seconds=6),
                  capture('220443-735', seconds=6), capture('220447-999'), capture('220451-467')]
        replay = self.login(frames)
        replay.app_is_running = Mock(side_effect=[True, False])
        replay.task.app_restart = Mock(side_effect=lambda: replay.lifecycle.append((replay.index, 'restart')))
        self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.app_is_running.call_count, 1)
        self.assertEqual(replay.lifecycle, [(6, 'restart')])
        self.assertEqual(replay.actions, [(2, 'CLIENT_UPDATE_CONFIRM'), (4, 'CLIENT_INSTALL_CONFIRM'),
                                          (5, 'PACKAGE_INSTALL_CONFIRM')])
        replay.task.app_restart.assert_called_once_with()

    def test_patch_confirmation_cooldown_does_not_delay_login_action(self):
        replay = self.login([capture('222010-024', seconds=6), scene('LOGIN_CONFIRM'),
                             *[scene(page='main')] * 6])
        with patch('tasks.activity.entry.SpecialActivityEntry') as reward_type:
            reward_type.return_value.run_login_daily_reward.return_value = True
            self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.actions, [(1, 'PATCH_APPLY'), (2, 'LOGIN_CONFIRM')])
        self.assertEqual(replay.frame['page'], 'main')

    def test_public_handler_leaves_patch_and_health_notice_untouched(self):
        for name in CN_CAPTURES[5:]:
            with self.subTest(frame=name):
                replay = self.replay([capture(name)])
                self.assertFalse(replay.task.handle_game_update())
                self.assertEqual(replay.actions, [])
                self.assertEqual(replay.index, 0)


class LoginStartTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.addCleanup(server.set_lang, 'global_cn')
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def test_both_backgrounds_are_ready_without_clicking(self):
        for name in START_CAPTURES:
            with self.subTest(frame=name):
                replay = LoginUpdateReplay(Login, [scene(fixture=name)], self.clock)
                self.assertTrue(replay.task.is_in_login_confirm())
                self.assertTrue(replay.task._is_login_progress_state())
                self.assertEqual(replay.actions, [])
                self.assertEqual(replay.index, 0)

    def test_hidden_and_dimmed_text_does_not_fall_back_to_transparent_icon(self):
        for name in START_CAPTURES:
            for change in ({'hide_start_text': True}, {'brightness': .6}, {'brightness': .3}):
                with self.subTest(frame=name, change=change):
                    replay = LoginUpdateReplay(Login, [scene(fixture=name, **change)], self.clock)
                    if name == START_CAPTURES[1] and change.get('hide_start_text'):
                        self.assertTrue(LOGIN_CONFIRM.match_template_luma(replay.image))
                    self.assertFalse(replay.task.is_in_login_confirm())
                    self.assertEqual(replay.actions, [])

    def test_update_and_store_captures_are_not_ready_to_enter(self):
        for name in CAPTURES:
            with self.subTest(frame=name):
                replay = LoginUpdateReplay(Login, [capture(name)], self.clock)
                self.assertFalse(replay.task.is_in_login_confirm())
                self.assertEqual(replay.actions, [])

    def test_full_start_phrase_and_normalized_model_language_are_required(self):
        for lang in ('cn', 'auto', '', 'global_cn'):
            replay = LoginUpdateReplay(Login, [scene(fixture=START_CAPTURES[0])], self.clock)
            replay.task.config.Emulator_GameLanguage = lang
            with patch('tasks.base.ui.OcrWhiteLetterOnComplexBackground') as ocr:
                for text, expected in (('轻触画面开始', True), ('轻触', False), ('画面开始', False),
                                       ('轻触画面关闭', False), ('', False)):
                    ocr.return_value.ocr_single_line.return_value = text
                    self.assertEqual(replay.task.is_in_login_confirm(), expected, (lang, text))
                self.assertEqual(ocr.call_args.kwargs['lang'], 'cn')
                self.assertEqual(replay.actions, [])

    def test_read_only_detection_bypasses_action_cooldown(self):
        replay = LoginUpdateReplay(Login, [scene(fixture=START_CAPTURES[0])], self.clock)
        self.assertTrue(replay.task.is_in_login_confirm(interval=2))
        self.assertFalse(replay.task.is_in_login_confirm(interval=2))
        self.assertTrue(replay.task.is_in_login_confirm(interval=0))
        self.clock.advance(2.5)
        self.assertTrue(replay.task.is_in_login_confirm(interval=2))
        self.assertEqual(replay.actions, [])

    def test_known_popups_block_readable_background_even_during_cooldown(self):
        for popup in (ANNOUNCEMENT_CLOSE, NETWORK_ERROR_DISCONNECT, NETWORK_ERROR_ABNORMAL, TOUCH_TO_CLOSE):
            with self.subTest(popup=popup.name):
                replay = LoginUpdateReplay(Login, [scene(fixture=START_CAPTURES[0], overlay=(popup,))], self.clock)
                ocr = OcrWhiteLetterOnComplexBackground(OCR_LOGIN_START, lang='cn')
                self.assertEqual(ocr.ocr_single_line(replay.image), '轻触画面开始')
                self.assertTrue(replay.task.appear(popup))
                replay.task.interval_reset(popup, interval=5)
                self.assertFalse(replay.task.is_in_login_confirm())
                self.assertFalse(replay.task._is_login_progress_state())
                self.assertEqual(replay.actions, [])

    def test_normal_task_recovery_hands_both_titles_to_login(self):
        for name in START_CAPTURES:
            replay = LoginUpdateReplay(UI, [scene(fixture=name)], self.clock)
            replay.task._ui_handoff_to_login = Mock(return_value=True)
            replay.task.interval_reset(LOGIN_CONFIRM, interval=2)
            self.assertTrue(replay.task.handle_ui_recovery())
            replay.task._ui_handoff_to_login.assert_called_once_with('login startup state')
            self.assertEqual(replay.actions, [])

    def test_normal_task_does_not_handoff_hidden_or_obstructed_title(self):
        for change in ({'hide_start_text': True}, {'brightness': .3}, {'overlay': (ANNOUNCEMENT_CLOSE,)}):
            replay = LoginUpdateReplay(UI, [scene(fixture=START_CAPTURES[0], **change)], self.clock)
            replay.task._ui_handoff_to_login = Mock()
            self.assertFalse(replay.task.handle_ui_recovery())
            replay.task._ui_handoff_to_login.assert_not_called()
            self.assertEqual(replay.actions, [])

    def test_other_asset_languages_keep_template_recognition(self):
        for lang, package in (('cn', 'CN-Official'), ('global_en', 'OVERSEA-Play')):
            server.set_lang(lang)
            replay = LoginUpdateReplay(Login, [scene('LOGIN_CONFIRM')], self.clock)
            replay.task.config.Emulator_PackageName = package
            with patch('tasks.base.ui.OcrWhiteLetterOnComplexBackground') as ocr, \
                    patch.object(LOGIN_CONFIRM, 'match_template_luma', return_value=True):
                self.assertTrue(replay.task.is_in_login_confirm())
                ocr.assert_not_called()
            self.assertEqual(replay.actions, [])


class LoginStartFlowTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.addCleanup(server.set_lang, 'global_cn')
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        reward = patch('tasks.activity.entry.SpecialActivityEntry')
        self.reward_type = reward.start()
        self.addCleanup(reward.stop)
        self.reward_type.return_value.run_login_daily_reward.return_value = True

    def login(self, frames, **kwargs):
        replay = LoginUpdateReplay(Login, [scene(), *frames], self.clock, **kwargs)
        replay.task.is_in_main = lambda **kwargs: replay.frame['page'] == 'main'
        return replay

    def test_missed_start_click_retries_safely_then_waits_for_confirmed_main(self):
        for name in START_CAPTURES:
            with self.subTest(frame=name):
                replay = self.login([scene(fixture=name, seconds=6)]
                                    + [scene(fixture=name)] * 6 + [scene(page='main')] * 6)
                LOGIN_CONFIRM.load_offset((18, -12))
                self.assertTrue(replay.task._handle_app_login())
                self.assertEqual(replay.actions, [(1, 'LOGIN_CONFIRM'), (6, 'LOGIN_CONFIRM')])
                self.assertEqual([area for _, _, area in replay.click_areas], [(465, 94, 815, 294)] * 2)
                replay.assert_retried(self, 'LOGIN_CONFIRM', 2)
                self.assertEqual(replay.frame['page'], 'main')
                self.assertGreaterEqual(replay.index, 12)
                self.assertEqual(replay.lifecycle, [])
                self.assertEqual(replay.sleeps, [])

    def test_absent_text_waits_and_first_ready_frame_enters_immediately(self):
        replay = self.login([scene(fixture=START_CAPTURES[1], hide_start_text=True, seconds=6)]
                            + [scene(fixture=START_CAPTURES[1], hide_start_text=True)] * 4
                            + [scene(fixture=START_CAPTURES[1])] + [scene(page='main')] * 6)
        self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.actions, [(6, 'LOGIN_CONFIRM')])
        self.assertEqual(replay.frame['page'], 'main')

    def test_network_popup_retries_without_start_clicks_underneath(self):
        frame = scene(fixture=START_CAPTURES[0], overlay=(NETWORK_ERROR_DISCONNECT, TOUCH_TO_CLOSE))
        replay = self.login([dict(frame, seconds=6)] + [frame] * 11
                            + [scene(fixture=START_CAPTURES[0])] + [scene(page='main')] * 6)
        self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.actions, [(1, 'TOUCH_TO_CLOSE'), (12, 'TOUCH_TO_CLOSE'), (13, 'LOGIN_CONFIRM')])
        replay.assert_retried(self, 'TOUCH_TO_CLOSE', 5)
        self.assertEqual(replay.lifecycle, [])
        self.assertEqual(replay.frame['page'], 'main')

    def test_announcement_flow_waits_through_close_cooldown_before_title(self):
        frame = scene(fixture=START_CAPTURES[0], overlay=(ANNOUNCEMENT_CLOSE,))
        replay = self.login([dict(frame, seconds=6)] + [frame] * 3
                            + [scene(fixture=START_CAPTURES[0])] * 2 + [scene(page='main')] * 6)
        self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.actions, [(1, 'ANNOUNCEMENT_CLOSE'), (4, 'ANNOUNCEMENT_CLOSE'), (6, 'LOGIN_CONFIRM')])
        replay.assert_retried(self, 'ANNOUNCEMENT_CLOSE', 1)
        self.assertEqual(replay.lifecycle, [])
        self.assertEqual(replay.frame['page'], 'main')

    def test_cn_agreement_clicks_retry_but_only_checked_state_enters(self):
        server.set_lang('cn')
        replay = self.login([scene('LOGIN_AGREEMENT_UNCHECKED', seconds=6)]
                            + [scene('LOGIN_AGREEMENT_UNCHECKED')] * 6
                            + [scene('LOGIN_CONFIRM')] + [scene(page='main')] * 6)
        replay.task.config.Emulator_PackageName = 'CN-Official'
        self.assertTrue(replay.task._handle_app_login())
        self.assertEqual(replay.actions, [(1, 'LOGIN_AGREEMENT_UNCHECKED'), (6, 'LOGIN_AGREEMENT_UNCHECKED'),
                                          (8, 'LOGIN_CONFIRM')])
        replay.assert_retried(self, 'LOGIN_AGREEMENT_UNCHECKED', 2)
        self.assertEqual(replay.click_areas[-1][2], (465, 94, 815, 294))
        self.assertEqual(replay.lifecycle, [])

    def test_logged_out_stops_without_clicking_readable_start_text(self):
        replay = self.login([scene(fixture=START_CAPTURES[0], overlay=(LOGGED_OUT,), seconds=6)])
        with self.assertRaises(RequestHumanTakeover):
            replay.task._handle_app_login()
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.lifecycle, [])
        self.reward_type.assert_not_called()

    def test_second_login_error_stops_after_one_restart(self):
        replay = self.login([scene(fixture=START_CAPTURES[0], overlay=(LOGIN_ERROR,), seconds=6)] * 2)
        with self.assertRaisesRegex(GameNotRunningError, 'Login error'):
            replay.task._handle_app_login()
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.lifecycle, [(1, 'app_stop'), (1, 'app_start'), (2, 'app_stop')])
        self.reward_type.assert_not_called()

    def test_unknown_login_screen_keeps_bounded_timeout(self):
        replay = self.login([scene(seconds=1)] * 122, guards=False)
        with self.assertRaisesRegex(GameNotRunningError, 'Login timeout'):
            replay.task._handle_app_login()
        self.assertEqual(replay.index, 121)
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.lifecycle, [])
        self.reward_type.assert_not_called()

    def test_network_flood_delays_tasks_and_stops_after_twelve_retries(self):
        replay = self.login([scene('NETWORK_ERROR_DISCONNECT', seconds=6)] * 12, guards=False)
        replay.task._ocr_maintenance_minutes = Mock(return_value=10)
        replay.task._delay_tasks_during_maintenance = Mock()
        with self.assertRaisesRegex(TaskEnd, 'waiting 30 minutes'):
            replay.task._handle_app_login()
        self.assertEqual(replay.clicks('TOUCH_TO_CLOSE'), list(range(1, 13)))
        self.assertEqual(len(replay.actions), 12)
        self.assertEqual(replay.lifecycle, [(12, 'app_stop')])
        replay.task._delay_tasks_during_maintenance.assert_called_once_with(30)
        self.reward_type.assert_not_called()

    def test_public_login_restores_device_timing_after_failure(self):
        replay = self.login([])
        replay.task._handle_app_login = Mock(side_effect=GameNotRunningError('test failure'))
        with self.assertRaisesRegex(GameNotRunningError, 'test failure'):
            replay.task.handle_app_login()
        self.assertEqual(replay.intervals, [(0.5,), ()])
        self.assertEqual(replay.stuck_timer.limit, 60)
        self.assertEqual(replay.actions, [])
