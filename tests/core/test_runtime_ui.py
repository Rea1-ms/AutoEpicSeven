"""Session delivery regressions using mocked browser and runtime event boundaries."""

import unittest
from contextlib import nullcontext
from copy import deepcopy
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pywebio.exceptions import SessionClosedException

from module.webui.runtime_ui import RuntimeUI, put_system_notification_settings


class RuntimeUITests(unittest.TestCase):
    def setUp(self):
        self.start_patch('lang.LANG', new='zh-CN')
        self.gui = SimpleNamespace(alas_name="test-aes", alive=True)
        self.ui = RuntimeUI(self.gui)
        self.events = self.start_patch("runtime_events")
        self.events.register_client.return_value = True
        self.events.get_progress.return_value = {
            "config_name": "test-aes", "state": "running", "completed": 1,
            "total": 3, "task": "Mail", "updated_at": 1,
        }
        self.events.poll_notifications.return_value = [{"id": "1", "title": "Test", "content": "Body"}]
        self.evaluate = self.start_patch("eval_js", return_value="browser")
        self.run_script = self.start_patch("run_js")
        self.toast = self.start_patch("toast")

    def start_patch(self, name, **kwargs):
        patcher = patch(f"module.webui.runtime_ui.{name}", **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_poll_registers_live_receiver_and_delivers_progress_and_notifications(self):
        self.ui.poll()
        self.events.register_client.assert_called_once_with(self.ui.client_id, kind="browser")
        self.events.poll_notifications.assert_called_once_with(self.ui.client_id)
        payload = self.run_script.call_args.kwargs
        self.assertEqual(payload["progress"]["completed"], 1)
        self.assertEqual(payload["progress"]["total"], 3)
        self.assertEqual(payload["notifications"][0]["id"], "1")

    def test_revoked_permission_unregisters_and_does_not_consume_notifications(self):
        self.ui.poll()
        self.events.reset_mock()
        self.evaluate.return_value = None
        self.ui.poll()
        self.events.unregister_client.assert_called_once_with(self.ui.client_id)
        self.events.poll_notifications.assert_not_called()
        self.assertFalse(self.ui.registered)
        self.assertEqual(self.run_script.call_args.kwargs["notifications"], [])

    def test_home_page_preserves_selected_config_but_explicit_instance_switch_updates_it(self):
        self.ui.poll()
        self.gui.alas_name = ""
        self.ui.poll()
        self.events.get_progress.assert_called_with("test-aes")
        self.gui.alas_name = "second-test-aes"
        self.ui.poll()
        self.events.get_progress.assert_called_with("second-test-aes")

    def test_disconnect_releases_receiver_and_subsequent_polls_are_inactive(self):
        self.ui.poll()
        self.events.reset_mock()
        self.evaluate.side_effect = SessionClosedException()
        self.ui.poll()
        self.events.unregister_client.assert_called_once_with(self.ui.client_id)
        self.assertTrue(self.ui.closed)
        self.events.reset_mock()
        self.ui.poll()
        self.events.register_client.assert_not_called()
        self.events.get_progress.assert_not_called()

    def test_runtime_task_is_not_removed_by_page_navigation(self):
        self.gui.task_handler = Mock()
        with patch("module.webui.runtime_ui.defer_call") as defer:
            self.ui.start()
        self.gui.task_handler.add.assert_called_once_with(self.ui.poll, 2, pending_delete=False)
        defer.assert_called_once_with(self.ui.stop)
        self.run_script.assert_called_once()

    def test_test_notification_refreshes_permission_and_only_reports_queueing(self):
        self.events.notify.return_value = True
        self.assertTrue(self.ui.send_test_notification())
        self.evaluate.assert_called_once()
        self.events.register_client.assert_called_once_with(self.ui.client_id, kind="browser")
        self.events.notify.assert_called_once()
        self.assertIn("已排队", self.toast.call_args.args[0])
        self.assertNotIn("已显示", self.toast.call_args.args[0])

    def test_test_notification_without_permission_does_not_queue_or_request_permission(self):
        self.evaluate.return_value = None
        self.assertFalse(self.ui.send_test_notification())
        self.events.register_client.assert_not_called()
        self.events.notify.assert_not_called()
        self.assertIn("尚未启用", self.toast.call_args.args[0])

    def test_test_notification_queue_failure_does_not_claim_success(self):
        self.events.notify.return_value = False
        self.assertFalse(self.ui.send_test_notification())
        self.assertEqual(self.toast.call_args.kwargs["color"], "warning")
        self.assertNotIn("已排队", self.toast.call_args.args[0])

    def test_missing_progress_clears_previous_runtime_display(self):
        self.events.get_progress.return_value = None
        self.ui.poll()
        progress = self.run_script.call_args.kwargs["progress"]
        self.assertEqual(progress["state"], "stopped")
        self.assertIsNone(progress["total"])

    def test_system_settings_never_overwrite_remote_config_or_select_a_provider(self):
        self.gui._save_config = Mock()
        with patch('module.webui.runtime_ui.put_text') as text, \
                patch('module.webui.runtime_ui.put_html'), \
                patch('module.webui.runtime_ui.put_button') as button:
            put_system_notification_settings(self.gui, 'Alas')
        self.gui._save_config.assert_not_called()
        self.assertIn('同时启用', text.call_args.args[0])
        self.assertEqual(button.call_count, 1)
        self.assertEqual(button.call_args.args[0], '发送测试通知')


class MaterialSelectUITests(unittest.TestCase):
    MATERIAL_PATH = 'Combat.Combat.Episode4Material'
    MATERIAL_PIN = 'Combat_Combat_Episode4Material'
    GROWTH_MATERIALS = frozenset({'HEART_OF_THE_WOODS', 'BREATH_OF_KARMA', 'FROZEN_SEED', 'TRACES_OF_BRILLIANCE'})

    def setUp(self):
        from module.config.config_updater import ConfigUpdater

        # Import the production GUI without loading deployment settings or
        # installing browser executors. Profiles and sessions stay synthetic.
        with (patch('module.webui.config.DeployConfig.read'),
              patch('module.webui.patch.patch_executor'),
              patch('module.webui.patch.patch_mimetype')):
            from module.webui.app import AlasGUI

        self.gui_type = AlasGUI
        self.gui = AlasGUI.__new__(AlasGUI)
        self.updater = ConfigUpdater()
        self.gui.ALAS_ARGS = deepcopy(self.updater.args)
        self.gui.alas_config = self.updater
        self.config = {'Combat': {'Combat': {
            'Domain': 'Episode4', 'EpisodeNumber': 4,
            'Episode4Material': 'BREATH_OF_KARMA', 'FastCombatCount': 10,
        }}}
        self.gui.alas_config_hidden = self.updater.get_hidden_args(self.config)
        for name in ('pin_remove_invalid_mark', 'pin_set_invalid_mark',
                     'pin_set_hidden_arg', 'pin_remove_hidden_arg'):
            setattr(self.gui, name, Mock())
        self.updater.read_file = Mock(side_effect=lambda name: deepcopy(self.config))
        self.updater.write_file = Mock(side_effect=self.store_config)
        self.start_patch('module.webui.app.t', side_effect=self.translate)
        self.start_patch('module.webui.app.pin', new={})
        self.start_patch('module.webui.app.toast')
        self.start_patch('module.webui.app.filepath_config', return_value='synthetic-profile.json')
        self.pin_update = self.start_patch('module.webui.app.pin_update')
        self.errors = self.start_patch('module.webui.app.logger.exception')

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def translate(key, *args):
        if key == 'Gui.Text.SelectUnavailable':
            return '当前篇章不支持，请重新选择'
        return key

    def store_config(self, name, config):
        self.config = deepcopy(config)

    def render_material(self):
        output = Mock(spec=['spec', 'style', 'show'])
        output.spec = {}
        output.style.return_value = output
        group = {name: deepcopy(self.gui.ALAS_ARGS['Combat']['Combat'][name])
                 for name in ('Domain', 'EpisodeNumber', 'Episode4Material')}
        # set_group is shared by ordinary settings and daemon settings. Call
        # its real body and widget builder; only the browser boundary is mocked.
        with (patch('module.webui.app.use_scope', side_effect=lambda *args, **kwargs: nullcontext()),
              patch('module.webui.app.put_text'), patch('module.webui.app.put_html'),
              patch('module.webui.widgets.get_title_help'),
              patch('module.webui.widgets.put_scope', return_value=output),
              patch('module.webui.widgets.put_select', return_value=output) as select):
            self.assertEqual(unwrap(self.gui_type.set_group)(self.gui, ['Combat'], group, self.config, 'Combat'), 3)
        calls = {call.kwargs['name']: call.kwargs for call in select.call_args_list}
        return calls[self.MATERIAL_PIN]

    def save(self, changes):
        self.gui._save_config(changes, 'synthetic', self.updater)
        self.errors.assert_not_called()
        self.updater.write_file.assert_called()

    @staticmethod
    def selected(options):
        return [option for option in options if option.get('selected')]

    def test_first_render_filters_material_candidates_for_selected_episode(self):
        all_options = deepcopy(self.gui.ALAS_ARGS['Combat']['Combat']['Episode4Material']['option'])
        for episode, expected_count in ((1, 6), (2, 12), (3, 12), (4, 16), (5, 12)):
            with self.subTest(episode=episode):
                self.config['Combat']['Combat'].update(EpisodeNumber=episode, Episode4Material='CATALYST_RARE_OATH')
                rendered = self.render_material()
                choices = rendered['options']
                values = [item['value'] for item in choices]
                self.assertEqual(len(values), expected_count)
                self.assertFalse(any(item.get('disabled') for item in choices))
                self.assertEqual([item['value'] for item in self.selected(choices)], ['CATALYST_RARE_OATH'])
                if episode == 1:
                    self.assertTrue(all(value.startswith('CATALYST_RARE_') for value in values))
                elif episode != 4:
                    self.assertFalse(self.GROWTH_MATERIALS.intersection(values))
                else:
                    self.assertEqual(set(values[-4:]), self.GROWTH_MATERIALS)
                self.assertEqual(self.gui.ALAS_ARGS['Combat']['Combat']['Episode4Material']['option'], all_options)
        self.updater.write_file.assert_not_called()

    def test_first_render_preserves_unavailable_material_as_disabled_selection(self):
        self.config['Combat']['Combat']['EpisodeNumber'] = 2
        before = deepcopy(self.config)
        rendered = self.render_material()
        selected = self.selected(rendered['options'])
        self.assertEqual(selected, [{
            'label': '当前篇章不支持，请重新选择', 'value': 'BREATH_OF_KARMA',
            'selected': True, 'disabled': True,
        }])
        self.assertEqual(rendered['value'], 'BREATH_OF_KARMA')
        enabled = [item['value'] for item in rendered['options'] if not item.get('disabled')]
        self.assertEqual(len(enabled), 12)
        self.assertFalse(self.GROWTH_MATERIALS.intersection(enabled))
        self.assertEqual(self.config, before)
        self.updater.write_file.assert_not_called()

    def test_saving_episode_refreshes_candidates_and_restores_fourth_episode(self):
        self.save({'Combat.Combat.EpisodeNumber': 2})
        self.pin_update.assert_called_once()
        self.assertEqual(self.pin_update.call_args.args, (self.MATERIAL_PIN,))
        update = self.pin_update.call_args.kwargs
        self.assertEqual(update['value'], 'BREATH_OF_KARMA')
        self.assertEqual([item['value'] for item in self.selected(update['options'])], ['BREATH_OF_KARMA'])
        self.assertTrue(self.selected(update['options'])[0]['disabled'])
        enabled = [item for item in update['options'] if not item.get('disabled')]
        self.assertEqual(len(enabled), 12)
        self.assertEqual(self.config['Combat']['Combat']['Episode4Material'], 'BREATH_OF_KARMA')

        self.pin_update.reset_mock()
        self.save({'Combat.Combat.EpisodeNumber': 4})
        self.pin_update.assert_called_once()
        update = self.pin_update.call_args.kwargs
        self.assertEqual(len(update['options']), 16)
        self.assertFalse(any(item.get('disabled') for item in update['options']))
        self.assertEqual([item['value'] for item in self.selected(update['options'])], ['BREATH_OF_KARMA'])
        self.assertEqual(update['value'], 'BREATH_OF_KARMA')
        self.assertEqual(self.config['Combat']['Combat']['Episode4Material'], 'BREATH_OF_KARMA')

    def test_saving_material_clears_placeholder_and_unrelated_save_does_not_refresh(self):
        self.config['Combat']['Combat']['EpisodeNumber'] = 2
        self.save({self.MATERIAL_PATH: 'CATALYST_RARE_OATH'})
        self.pin_update.assert_called_once()
        update = self.pin_update.call_args.kwargs
        self.assertEqual(update['value'], 'CATALYST_RARE_OATH')
        self.assertEqual(len(update['options']), 12)
        self.assertFalse(any(item.get('disabled') for item in update['options']))
        self.assertEqual([item['value'] for item in self.selected(update['options'])], ['CATALYST_RARE_OATH'])

        self.pin_update.reset_mock()
        self.save({'Combat.Combat.FastCombatCount': 3})
        self.pin_update.assert_not_called()
        self.assertEqual(self.config['Combat']['Combat']['Episode4Material'], 'CATALYST_RARE_OATH')
        self.assertEqual(self.config['Combat']['Combat']['FastCombatCount'], 3)

    def test_normal_select_options_and_disabled_values_remain_unchanged(self):
        from module.webui.widgets import build_select_options, put_arg_select

        choices = build_select_options(['Hunt', 'SpiritAltar'], ['讨伐', '精灵祭坛'], 'SpiritAltar')
        self.assertEqual(choices, [
            {'label': '讨伐', 'value': 'Hunt', 'selected': False},
            {'label': '精灵祭坛', 'value': 'SpiritAltar', 'selected': True},
        ])
        self.assertEqual(len(build_select_options(['Hunt'], ['讨伐'], 'UNKNOWN')), 1)
        with (patch('module.webui.widgets.get_title_help'), patch('module.webui.widgets.put_scope'),
              patch('module.webui.widgets.put_select') as select):
            put_arg_select({'name': 'ordinary', 'value': 'SpiritAltar', 'disabled': True,
                            'options': ['Hunt', 'SpiritAltar'], 'options_label': ['讨伐', '精灵祭坛']})
        self.assertEqual(select.call_args.kwargs['options'], [
            {'label': '精灵祭坛', 'value': 'SpiritAltar', 'selected': True},
        ])


if __name__ == "__main__":
    unittest.main()
