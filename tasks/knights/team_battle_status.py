from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from module.logger import logger
from module.notify import handle_notify


@dataclass(frozen=True)
class TeamBattleCrestStatus:
    current: int
    remain: int
    total: int

    def is_valid(self) -> bool:
        return self.total > 0 and 0 <= self.current <= self.total

    def to_counter(self) -> str:
        return f"{self.current}/{self.total}"

    def has_unused_attack(self) -> bool:
        return self.remain > 0


class KnightsTeamBattleStatusMixin:
    TEAM_BATTLE_DASHBOARD_LOCKED_TEXT = "未开放"
    TEAM_BATTLE_DASHBOARD_INVALID_TEXT = "识别失败"
    TEAM_BATTLE_DASHBOARD_NOT_ENOUGH_PEOPLE_TEXT = "人数不足"
    TEAM_BATTLE_SCHEDULE_HOUR = 11
    TEAM_BATTLE_START_WEEKDAY_END_WEEKDAY = (
        (0, 1),  # Mon 11:00 -> Tue 11:00
        (2, 3),  # Wed 11:00 -> Thu 11:00
        (4, 5),  # Fri 11:00 -> Sat 11:00
    )

    @classmethod
    def _get_active_team_battle_end(cls, now: datetime | None = None) -> datetime | None:
        now = (now or datetime.now()).replace(microsecond=0)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        for start_weekday, end_weekday in cls.TEAM_BATTLE_START_WEEKDAY_END_WEEKDAY:
            start = week_start + timedelta(days=start_weekday)
            start = start.replace(hour=cls.TEAM_BATTLE_SCHEDULE_HOUR)
            end = week_start + timedelta(days=end_weekday)
            end = end.replace(hour=cls.TEAM_BATTLE_SCHEDULE_HOUR)
            if start <= now < end:
                return end

        return None

    def _reset_team_battle_status_runtime(self) -> None:
        self._team_battle_next_delay_target = None

    def _set_team_battle_dashboard_value(self, value: str) -> None:
        self.config.stored.E7TeamBattle.value = value

    def _update_team_battle_dashboard_locked(self) -> None:
        logger.attr("TeamBattleStatus", self.TEAM_BATTLE_DASHBOARD_LOCKED_TEXT)
        self._set_team_battle_dashboard_value(self.TEAM_BATTLE_DASHBOARD_LOCKED_TEXT)
        self._team_battle_next_delay_target = None

    def _update_team_battle_dashboard_invalid(self) -> None:
        logger.attr("TeamBattleStatus", self.TEAM_BATTLE_DASHBOARD_INVALID_TEXT)
        self._set_team_battle_dashboard_value(self.TEAM_BATTLE_DASHBOARD_INVALID_TEXT)
        self._team_battle_next_delay_target = None

    def _update_team_battle_dashboard_not_enough_people(self) -> None:
        logger.attr("TeamBattleStatus", self.TEAM_BATTLE_DASHBOARD_NOT_ENOUGH_PEOPLE_TEXT)
        self._set_team_battle_dashboard_value(self.TEAM_BATTLE_DASHBOARD_NOT_ENOUGH_PEOPLE_TEXT)
        self._team_battle_next_delay_target = None

    def _update_team_battle_dashboard_counter(self, status: TeamBattleCrestStatus) -> None:
        logger.attr("TeamBattleStatus", status.to_counter())
        self._set_team_battle_dashboard_value(status.to_counter())

    def _get_team_battle_reminder_lead_minutes(self) -> int:
        value = getattr(self.config, "KnightsExpedition_TeamBattleReminderLeadMinutes", 60)
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            logger.warning(f"Invalid TeamBattleReminderLeadMinutes: {value}, fallback to 60")
            return 60

    def _send_or_schedule_team_battle_reminder(self, status: TeamBattleCrestStatus) -> None:
        self._team_battle_next_delay_target = None

        if not getattr(self.config, "KnightsExpedition_TeamBattleReminder", False):
            return
        if not status.is_valid():
            return
        if not status.has_unused_attack():
            logger.info("Team battle reminder skipped, no unused attacks left")
            return

        now = datetime.now().replace(microsecond=0)
        end_time = self._get_active_team_battle_end(now)
        if end_time is None:
            logger.info("Team battle reminder skipped, current time is outside active war schedule")
            return

        # Guild war OCR x/y is remaining_attacks / total_attacks.
        lead = timedelta(minutes=self._get_team_battle_reminder_lead_minutes())
        reminder_time = end_time - lead
        end_text = end_time.strftime("%Y-%m-%d %H:%M:%S")
        last_end = getattr(self.config, "KnightsExpedition_TeamBattleReminderLastEnd", "") or ""

        if last_end == end_text:
            logger.info(f"Team battle reminder already sent for war ending at {end_text}")
            return

        if now < reminder_time:
            logger.info(f"Team battle reminder scheduled at {reminder_time}")
            self._team_battle_next_delay_target = reminder_time
            return

        config_name = getattr(self.config, "config_name", "alas")
        title = f"AES <{config_name}> 团战提醒"
        content = (
            f"当前团战刀数 {status.to_counter()}，仍有 {status.remain} 刀未出。\n"
            f"本轮团战将在 {end_text} 结束，请尽快处理。"
        )
        if handle_notify(self.config.Error_OnePushConfig, title=title, content=content):
            logger.info("Team battle reminder sent")
            self.config.KnightsExpedition_TeamBattleReminderLastEnd = end_text
        else:
            logger.warning("Team battle reminder notify failed")

    def _get_team_battle_next_delay_target(self) -> datetime | None:
        return getattr(self, "_team_battle_next_delay_target", None)
