"""Month-end reward reminders for the sanctuary monthly task."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from module.config.utils import server_time_offset
from module.logger import logger
from module.notify import handle_notify


@dataclass(frozen=True)
class MonthlyReminderWindow:
    start: datetime
    end: datetime
    next_daily: datetime
    day_key: str


def monthly_reminder_window(
    now: datetime, daily_trigger: str, offset: timedelta, lead_days: int,
) -> MonthlyReminderWindow:
    """Calculate local deadlines using the task's server reset, including month boundaries."""
    hour, minute = map(int, daily_trigger.split(",")[0].strip().split(":"))
    reset_offset = timedelta(hours=hour, minutes=minute)
    server_now = now - offset

    # Before the daily reset, the calendar may already say October 1 while
    # September's reward is still available. Subtract the reset time before
    # selecting the month and deduplication day; using now.month loses this
    # final claim window. offset follows the existing local-minus-server API.
    server_day = (server_now - reset_offset).replace(hour=0, minute=0, second=0, microsecond=0)
    next_month = (server_day.replace(day=1) + timedelta(days=32)).replace(day=1)
    end = next_month + reset_offset + offset
    return MonthlyReminderWindow(
        start=end - timedelta(days=lead_days),
        end=end,
        next_daily=server_day + timedelta(days=1) + reset_offset + offset,
        day_key=f"{next_month:%Y-%m}|{server_day:%Y-%m-%d}",
    )


class SanctuaryMonthlyReminderMixin:
    def _monthly_reminder_window(self, now: datetime) -> MonthlyReminderWindow:
        value = self.config.SanctuaryMonthly_ReminderLeadDays
        try:
            lead_days = max(1, min(int(value), 7))
        except (TypeError, ValueError):
            logger.warning("Invalid monthly reminder lead days, using 3")
            lead_days = 3
        return monthly_reminder_window(
            now, self.config.Scheduler_ServerUpdate, server_time_offset(), lead_days,
        )

    def _send_monthly_reward_reminder(self) -> None:
        """Check the current heart page before purification can fail or change it."""
        if not getattr(self.config, "SanctuaryMonthly_Reminder", False):
            return
        now = datetime.now()
        window = self._monthly_reminder_window(now)
        if now < window.start:
            return
        if self._is_monthly_claimed():
            logger.info("Monthly reward reminder skipped: reward already claimed")
            return
        if self.config.SanctuaryMonthly_ReminderLastSent == window.day_key:
            logger.info("Monthly reward reminder already sent for this server day")
            return

        # Absence of the claimed marker does not prove that a reward is ready.
        # Ask the player to check it, without selecting or claiming any item.
        config_name = getattr(self.config, "config_name", "alas")
        logger.warning(f"Monthly reward claim deadline approaching: {window.end:%Y-%m-%d %H:%M:%S}")
        content = (
            "圣域月常即将重置，尚未确认本月奖励已领取。\n"
            f"预计重置时间：{window.end:%Y-%m-%d %H:%M}（运行脚本的电脑本地时间）。\n"
            "请进入圣域 → 欧勒毕斯之心，检查保管箱并手动选择、领取本月奖励，避免过期。"
        )
        if handle_notify(
            self.config.Error_OnePushConfig,
            title=f"AES <{config_name}> 圣域月常领取提醒",
            content=content,
        ):
            # Persist only confirmed delivery. Restarting the script must not
            # resend today's reminder, while a failed push must remain retryable.
            self.config.SanctuaryMonthly_ReminderLastSent = window.day_key
            logger.info("Monthly reward reminder sent")
        else:
            logger.warning("Monthly reward reminder delivery failed; leaving it eligible for retry")

    def _monthly_reminder_delay(self, target: datetime) -> datetime:
        """Prevent weekly purification scheduling from skipping the claim window."""
        if not getattr(self.config, "SanctuaryMonthly_Reminder", False):
            return target
        now = datetime.now()
        window = self._monthly_reminder_window(now)
        reminder_check = window.start if now < window.start else window.next_daily
        return min(target, reminder_check)
