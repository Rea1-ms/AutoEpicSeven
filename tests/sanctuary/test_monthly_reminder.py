"""Check month-end reminder rules without real notifications, accounts, or devices."""

import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server

# Page and asset registrations bind to the active server during task import.
server.set_lang("global_cn")

from module.exception import ScriptError  # noqa: E402
from tasks.sanctuary import monthly_reminder as reminder  # noqa: E402
from tasks.sanctuary.sanctuary import Sanctuary  # noqa: E402


class ReminderWindowTests(unittest.TestCase):
    def window(self, now, reset="03:00", offset=0, days=3):
        return reminder.monthly_reminder_window(now, reset, timedelta(hours=offset), days)

    def test_cn_month_end(self):
        window = self.window(datetime(2026, 9, 21, 18))
        self.assertEqual(window.end, datetime(2026, 10, 1, 3))
        self.assertEqual(window.start, datetime(2026, 9, 28, 3))
        self.assertEqual(window.next_daily, datetime(2026, 9, 22, 3))

    def test_global_reset(self):
        window = self.window(datetime(2026, 9, 28, 2), reset="02:00")
        self.assertEqual(window.start, datetime(2026, 9, 28, 2))
        self.assertEqual(window.next_daily, datetime(2026, 9, 29, 2))

    def test_first_day_before_reset_still_previous_month(self):
        window = self.window(datetime(2026, 10, 1, 2, 59, 59))
        self.assertEqual(window.end, datetime(2026, 10, 1, 3))
        self.assertEqual(window.day_key, "2026-10|2026-09-30")
        self.assertEqual(window.next_daily, window.end)

    def test_exact_reset_starts_new_month(self):
        window = self.window(datetime(2026, 10, 1, 3))
        self.assertEqual(window.end, datetime(2026, 11, 1, 3))
        self.assertEqual(window.start, datetime(2026, 10, 29, 3))
        self.assertEqual(window.next_daily, datetime(2026, 10, 2, 3))

    def test_leap_february(self):
        self.assertEqual(self.window(datetime(2028, 2, 1, 3)).start, datetime(2028, 2, 27, 3))

    def test_non_leap_february(self):
        self.assertEqual(self.window(datetime(2027, 2, 1, 3)).start, datetime(2027, 2, 26, 3))

    def test_year_boundary(self):
        window = self.window(datetime(2026, 12, 31, 18))
        self.assertEqual(window.end, datetime(2027, 1, 1, 3))

    def test_local_timezone_conversion(self):
        # UTC computer, CN server: local minus server is -8 hours.
        window = self.window(datetime(2026, 9, 27, 20), offset=-8)
        self.assertEqual(window.start, datetime(2026, 9, 27, 19))
        self.assertEqual(window.end, datetime(2026, 9, 30, 19))
        self.assertEqual(window.day_key, "2026-10|2026-09-28")

    def test_day_does_not_change_at_midnight(self):
        before = self.window(datetime(2026, 9, 28, 23))
        after = self.window(datetime(2026, 9, 29, 1))
        reset = self.window(datetime(2026, 9, 29, 3))
        self.assertEqual(before.day_key, after.day_key)
        self.assertNotEqual(before.day_key, reset.day_key)

    def test_configured_lead_days(self):
        for days in (1, 3, 5, 7):
            with self.subTest(days=days):
                window = self.window(datetime(2026, 9, 21), days=days)
                self.assertEqual(window.end - window.start, timedelta(days=days))


class ReminderWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.config = SimpleNamespace(
            SanctuaryMonthly_Reminder=True,
            SanctuaryMonthly_ReminderLeadDays=3,
            SanctuaryMonthly_ReminderLastSent="",
            Scheduler_ServerUpdate="03:00",
            Error_OnePushConfig="provider: mock",
            config_name="test-account",
            task_delay=Mock(),
        )
        self.clock = self.stack.enter_context(patch.object(reminder, "datetime", wraps=datetime))
        self.clock.now.return_value = datetime(2026, 9, 28, 18)
        self.stack.enter_context(patch.object(reminder, "server_time_offset", return_value=timedelta()))
        self.notify = self.stack.enter_context(patch.object(reminder, "handle_notify", return_value=True))
        self.task = self.make_task()

    def make_task(self):
        # Bypass UI initialization: this suite exercises reminder and scheduler
        # behavior using an in-memory config and explicit navigation outcomes.
        task = Sanctuary.__new__(Sanctuary)
        task.config = self.config
        task._is_monthly_claimed = Mock(return_value=False)
        task._ensure_app_running = Mock()
        task._enter_sanctuary = Mock(return_value=True)
        task._back_to_sanctuary = Mock(return_value=True)
        task._enter_monthly = Mock(return_value=True)
        task._monthly_purify = Mock(return_value=task.MONTHLY_STATUS_EXHAUSTED)
        return task

    def test_disabled(self):
        self.config.SanctuaryMonthly_Reminder = False
        self.task._send_monthly_reward_reminder()
        self.notify.assert_not_called()
        self.task._is_monthly_claimed.assert_not_called()

    def test_before_window(self):
        self.clock.now.return_value = datetime(2026, 9, 28, 2, 59, 59)
        self.task._send_monthly_reward_reminder()
        self.notify.assert_not_called()

    def test_claimed_skips_notification(self):
        self.task._is_monthly_claimed.return_value = True
        self.task._send_monthly_reward_reminder()
        self.notify.assert_not_called()

    def test_sent_with_deadline_and_account(self):
        self.task._send_monthly_reward_reminder()
        kwargs = self.notify.call_args.kwargs
        self.assertIn("2026-10-01 03:00", kwargs["content"])
        self.assertIn("test-account", kwargs["title"])
        self.assertEqual(self.config.SanctuaryMonthly_ReminderLastSent, "2026-10|2026-09-28")

    def test_restart_does_not_resend_success(self):
        self.task._send_monthly_reward_reminder()
        self.make_task()._send_monthly_reward_reminder()
        self.notify.assert_called_once()

    def test_next_server_day_sends_again(self):
        self.task._send_monthly_reward_reminder()
        self.clock.now.return_value = datetime(2026, 9, 29, 3)
        self.make_task()._send_monthly_reward_reminder()
        self.assertEqual(self.notify.call_count, 2)

    def test_failure_remains_retryable(self):
        self.notify.return_value = False
        self.task._send_monthly_reward_reminder()
        self.assertEqual(self.config.SanctuaryMonthly_ReminderLastSent, "")
        self.notify.return_value = True
        self.make_task()._send_monthly_reward_reminder()
        self.assertEqual(self.notify.call_count, 2)
        self.assertTrue(self.config.SanctuaryMonthly_ReminderLastSent)

    def test_new_month_does_not_reuse_old_dedup(self):
        self.task._send_monthly_reward_reminder()
        self.clock.now.return_value = datetime(2026, 10, 29, 18)
        self.make_task()._send_monthly_reward_reminder()
        self.assertEqual(self.notify.call_count, 2)

    def test_no_reminder_for_new_period_after_reset(self):
        self.clock.now.return_value = datetime(2026, 10, 1, 3)
        self.task._send_monthly_reward_reminder()
        self.notify.assert_not_called()

    def test_reminder_before_purification_exception(self):
        self.task._monthly_purify.side_effect = ScriptError("Capacity unknown")
        with self.assertRaises(ScriptError):
            self.task.run_monthly_task()
        self.notify.assert_called_once()
        self.config.task_delay.assert_not_called()

    def test_failed_entry_does_not_send(self):
        self.task._enter_monthly.return_value = False
        self.assertFalse(self.task.run_monthly())
        self.notify.assert_not_called()

    def test_weekly_target_does_not_skip_reminder_start(self):
        self.clock.now.return_value = datetime(2026, 9, 27, 18)
        with patch("tasks.sanctuary.sanctuary.get_server_next_monday_update",
                   return_value=datetime(2026, 10, 5, 3)):
            self.assertTrue(self.task.run_monthly_task())
        self.config.task_delay.assert_called_once_with(target=datetime(2026, 9, 28, 3))
        self.notify.assert_not_called()

    def test_daily_check_during_reminder_window(self):
        with patch("tasks.sanctuary.sanctuary.get_server_next_monday_update",
                   return_value=datetime(2026, 10, 5, 3)):
            self.task.run_monthly_task()
        self.config.task_delay.assert_called_once_with(target=datetime(2026, 9, 29, 3))

    def test_earlier_normal_schedule_kept(self):
        self.clock.now.return_value = datetime(2026, 9, 20, 18)
        target = datetime(2026, 9, 21, 3)
        self.assertEqual(self.task._monthly_reminder_delay(target), target)

    def test_disabled_schedule_unchanged(self):
        self.config.SanctuaryMonthly_Reminder = False
        target = datetime(2026, 10, 5, 3)
        self.assertEqual(self.task._monthly_reminder_delay(target), target)

    def test_claimed_schedule_stays_monthly(self):
        self.task._is_monthly_claimed.return_value = True
        self.task._monthly_purify.return_value = self.task.MONTHLY_STATUS_CLAIMED
        target = datetime(2026, 10, 1, 3)
        with patch("tasks.sanctuary.sanctuary.get_server_next_month_update", return_value=target):
            self.task.run_monthly_task()
        self.config.task_delay.assert_called_once_with(target=target)
        self.notify.assert_not_called()

    def test_invalid_lead_defaults_to_three_days(self):
        self.config.SanctuaryMonthly_ReminderLeadDays = "bad"
        window = self.task._monthly_reminder_window(self.clock.now())
        self.assertEqual(window.end - window.start, timedelta(days=3))


if __name__ == "__main__":
    unittest.main()
