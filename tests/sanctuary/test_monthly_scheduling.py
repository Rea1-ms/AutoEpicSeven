"""Check monthly reset boundaries without a real account or device."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.config import server
from module.config.utils import get_server_next_month_update

# Page and asset registrations must use the same server before task imports.
server.set_lang('global_cn')

from tasks.sanctuary.sanctuary import Sanctuary  # noqa: E402


class MonthlySchedulingTests(unittest.TestCase):
    @staticmethod
    @contextmanager
    def frozen_time(now, offset=timedelta()):
        # Keep real datetime arithmetic; only the current local time and the
        # local-minus-server offset are controlled. The date calculation itself
        # must remain production code so this regression catches month skipping.
        with patch('module.config.utils.datetime', wraps=datetime) as clock, \
                patch('module.config.utils.server_time_offset', return_value=offset):
            clock.now.return_value = now
            yield

    def assert_next_update(self, now, expected, trigger='02:00', offset=timedelta()):
        with self.frozen_time(now, offset):
            target = get_server_next_month_update(trigger)
        self.assertEqual(target, expected)
        self.assertGreater(target, now)
        self.assertEqual(target.microsecond, 0)

    def test_month_start_before_reset_retries_current_month(self):
        self.assert_next_update(
            datetime(2026, 10, 1, 1, 2, 22),
            datetime(2026, 10, 1, 2),
        )

    def test_month_start_reset_boundary(self):
        cases = (
            (datetime(2026, 10, 1, 1, 59, 59, 999999), datetime(2026, 10, 1, 2)),
            (datetime(2026, 10, 1, 2), datetime(2026, 11, 1, 2)),
            (datetime(2026, 10, 1, 2, 0, 0, 1), datetime(2026, 11, 1, 2)),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                self.assert_next_update(now, expected)

    def test_month_end_and_midmonth_use_next_month(self):
        cases = (
            (datetime(2026, 9, 30, 23, 59, 59), datetime(2026, 10, 1, 2)),
            (datetime(2026, 10, 15, 1), datetime(2026, 11, 1, 2)),
            (datetime(2026, 4, 30, 23, 59, 59), datetime(2026, 5, 1, 2)),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                self.assert_next_update(now, expected)

    def test_year_rollover_follows_server_month(self):
        cases = (
            (datetime(2026, 12, 31, 23, 59, 59), datetime(2027, 1, 1, 2)),
            (datetime(2027, 1, 1, 1, 59, 59), datetime(2027, 1, 1, 2)),
            (datetime(2027, 1, 1, 2), datetime(2027, 2, 1, 2)),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                self.assert_next_update(now, expected)

    def test_leap_february_uses_calendar_month(self):
        cases = (
            (datetime(2024, 1, 31, 23, 59, 59), datetime(2024, 2, 1, 2)),
            (datetime(2024, 2, 1, 1, 59, 59), datetime(2024, 2, 1, 2)),
            (datetime(2024, 2, 29, 23, 59, 59), datetime(2024, 3, 1, 2)),
            (datetime(2026, 2, 28, 23, 59, 59), datetime(2026, 3, 1, 2)),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                self.assert_next_update(now, expected)

    def test_reset_minutes_and_midnight(self):
        cases = (
            ('02:30', datetime(2026, 10, 1, 2, 29, 59, 999999), datetime(2026, 10, 1, 2, 30)),
            ('02:30', datetime(2026, 10, 1, 2, 30), datetime(2026, 11, 1, 2, 30)),
            ('00:00', datetime(2026, 9, 30, 23, 59, 59, 999999), datetime(2026, 10, 1)),
            ('00:00', datetime(2026, 10, 1), datetime(2026, 11, 1)),
            ('23:59', datetime(2026, 10, 1, 23, 58, 59), datetime(2026, 10, 1, 23, 59)),
        )
        for trigger, now, expected in cases:
            with self.subTest(trigger=trigger, now=now):
                self.assert_next_update(now, expected, trigger)

    def test_timezone_offsets_use_server_month_in_local_result(self):
        # The offset is local time minus server time. In these cases the local
        # calendar month differs from the server month; both choosing the reset
        # month and returning its local timestamp must honor that distinction.
        cases = (
            (13, datetime(2026, 10, 1, 8), datetime(2026, 10, 1, 15)),
            (13, datetime(2026, 10, 1, 14), datetime(2026, 10, 1, 15)),
            (-7, datetime(2026, 9, 30, 18), datetime(2026, 9, 30, 19)),
            (-7, datetime(2026, 9, 30, 19), datetime(2026, 10, 31, 19)),
        )
        for hours, now, expected in cases:
            with self.subTest(offset_hours=hours, now=now):
                self.assert_next_update(now, expected, offset=timedelta(hours=hours))

    def test_multiple_triggers_keep_first_configured_reset(self):
        for trigger in (' 02:30 , 00:00 , 18:00 ', ['02:30', '00:00', '18:00']):
            with self.subTest(trigger=trigger):
                self.assert_next_update(
                    datetime(2026, 10, 1, 2, 20), datetime(2026, 10, 1, 2, 30), trigger,
                )
                self.assert_next_update(
                    datetime(2026, 10, 1, 2, 30), datetime(2026, 11, 1, 2, 30), trigger,
                )

    def assert_claimed_task_target(self, now, expected):
        delay = Mock()
        task = SimpleNamespace(
            config=SimpleNamespace(Scheduler_ServerUpdate='02:00', task_delay=delay),
            _ensure_app_running=Mock(),
            run_monthly=Mock(return_value=True),
            _monthly_status=Sanctuary.MONTHLY_STATUS_CLAIMED,
            MONTHLY_STATUS_CLAIMED=Sanctuary.MONTHLY_STATUS_CLAIMED,
            MONTHLY_STATUS_FAILED=Sanctuary.MONTHLY_STATUS_FAILED,
        )
        # A claimed marker before the reset belongs to the previous reward
        # period. Call the real task scheduler with that marker, without mocking
        # its reset helper or constructing the UI/device object.
        with self.frozen_time(now):
            success = Sanctuary.run_monthly_task(task)
        self.assertTrue(success)
        task._ensure_app_running.assert_called_once_with()
        task.run_monthly.assert_called_once_with()
        delay.assert_called_once_with(target=expected)

    def test_claimed_task_before_reset_retries_pending_refresh(self):
        self.assert_claimed_task_target(
            datetime(2026, 10, 1, 1, 2, 22),
            datetime(2026, 10, 1, 2),
        )

    def test_claimed_task_after_reset_delays_to_next_month(self):
        for now in (datetime(2026, 10, 1, 2), datetime(2026, 10, 1, 2, 0, 0, 1)):
            with self.subTest(now=now):
                self.assert_claimed_task_target(now, datetime(2026, 11, 1, 2))


if __name__ == '__main__':
    unittest.main()
