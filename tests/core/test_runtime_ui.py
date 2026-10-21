"""Session delivery regressions using mocked browser and runtime event boundaries."""

import unittest
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


if __name__ == "__main__":
    unittest.main()
