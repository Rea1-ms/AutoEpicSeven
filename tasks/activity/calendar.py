"""Adapt shared game facts to the activity flows this task implements."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import module.config.server as server
from module.game_info.catalog import INFO_PATH, INFO_TIMEZONE, load_info
from module.game_info.timeline import render_timeline as render_game_timeline


EVENT_TIMEZONE = INFO_TIMEZONE
CALENDAR_PATH = INFO_PATH
DEFAULT_FREE_GACHA_20_ID = "free_gacha_20_2026_08_27"
SUPPORTED_MODES = ("legacy", "free_gacha_20", "support_reward",
                   "completion_record", "detective_search", "epic_path")
ACTIVITY_TASK_MODES = {
    "LimitedActivity": ("free_gacha_20", "support_reward",
                        "completion_record", "detective_search", "epic_path"),
    "SpecialActivity": ("legacy",),
}
ACTIVITY_OPTIONS = {
    "LimitedActivity": {
        "free_gacha_20": "LimitedActivity_GetFreeGacha",
        "support_reward": "LimitedActivity_GetSupportReward",
        "completion_record": "LimitedActivity_GetCompletionRecord",
        "detective_search": "LimitedActivity_GetDetectiveSearch",
        "epic_path": "LimitedActivity_GetEpicPathDailyReward",
    },
    "SpecialActivity": {},
}


@dataclass(frozen=True)
class ActivityWindow:
    event_id: str
    name: str
    mode: str
    server_family: str
    start: datetime
    end: datetime
    refresh_hours: int = 0
    refresh_hour: int | None = None

    def contains(self, now: datetime) -> bool:
        return self.start <= now < self.end

    def refresh_start(self, now: datetime) -> datetime:
        """Anchor each refresh to this server's opening time, not a last run."""
        if not self.refresh_hours:
            return self.start
        interval = timedelta(hours=self.refresh_hours)
        anchor = self.start if self.refresh_hour is None else self.start.replace(
            hour=self.refresh_hour, minute=0, second=0, microsecond=0,
        )
        return anchor + max(0, (now - anchor) // interval) * interval

    def next_refresh(self, now: datetime) -> datetime | None:
        if not self.refresh_hours or not self.contains(now):
            return None
        target = self.refresh_start(now) + timedelta(hours=self.refresh_hours)
        return target if target < self.end else None


def load_calendar(path: Path = CALENDAR_PATH) -> tuple[ActivityWindow, ...]:
    # Catalog categories may exist before automation supports them. Only this
    # adapter owns the supported-flow list; adding facts never executes code.
    return tuple(ActivityWindow(p.event_id, p.name, p.kind, p.server_family, p.start, p.end,
                                p.values.get("refresh_hours", 0), p.values.get("refresh_hour"))
                 for p in load_info(path).periods if p.kind in SUPPORTED_MODES)


def _server_windows(config, *, assets_lang: str | None = None) -> tuple[ActivityWindow, ...]:
    family = server.server_family(config.Emulator_PackageName)
    assets_lang = server.lang if assets_lang is None else assets_lang
    supported = (
        family == server.SERVER_FAMILY_CN and assets_lang == "cn"
    ) or (
        family == server.SERVER_FAMILY_OVERSEA and assets_lang == "global_cn"
    )
    if not supported:
        return ()
    return tuple(
        window for window in load_calendar()
        if window.server_family == family
        and (window.mode not in ("support_reward",
                                "completion_record", "detective_search", "epic_path")
             or family == server.SERVER_FAMILY_OVERSEA)
    )


def active_activities(config, now: datetime | None = None, *, task=None,
                      assets_lang: str | None = None) -> tuple[ActivityWindow, ...]:
    now = now or datetime.now(EVENT_TIMEZONE)
    modes = SUPPORTED_MODES if task is None else ACTIVITY_TASK_MODES[task]
    return tuple(window for window in _server_windows(config, assets_lang=assets_lang)
                 if window.mode in modes and window.contains(now))


def next_activity_start(config, now: datetime | None = None, *, task=None) -> datetime | None:
    now = now or datetime.now(EVENT_TIMEZONE)
    modes = SUPPORTED_MODES if task is None else ACTIVITY_TASK_MODES[task]
    return min((window.start for window in _server_windows(config) if window.mode in modes and window.start > now),
               default=None)


def render_timeline() -> str:
    """Keep the previous command available while using the shared renderer."""
    return render_game_timeline(load_info())


if __name__ == "__main__":
    print(render_timeline(), end="")
