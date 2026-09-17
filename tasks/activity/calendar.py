"""Resolve automated event windows and render their shared timeline."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import yaml

import module.config.server as server


EVENT_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
CALENDAR_PATH = Path(__file__).with_suffix(".yaml")
DEFAULT_FREE_GACHA_20_ID = "free_gacha_20_2026_08_27"
SUPPORTED_MODES = ("legacy", "free_gacha_20")


@dataclass(frozen=True)
class ActivityWindow:
    event_id: str
    name: str
    mode: str
    server_family: str
    start: datetime
    end: datetime

    def contains(self, now: datetime) -> bool:
        return self.start <= now < self.end


def _parse_time(value) -> datetime:
    timestamp = datetime.fromisoformat(str(value))
    if timestamp.tzinfo is None:
        raise ValueError("Activity calendar timestamps must include a timezone")
    return timestamp.astimezone(EVENT_TIMEZONE)


def load_calendar(path: Path = CALENDAR_PATH) -> tuple[ActivityWindow, ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    delay_days = data["cn_delay_days"]
    if type(delay_days) is not int or delay_days < 0:
        raise ValueError("Activity calendar cn_delay_days must be a nonnegative integer")
    delay = timedelta(days=delay_days)
    windows = []
    event_ids = set()
    for event in data["events"]:
        event_id = event["id"]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", event_id) or event_id in event_ids:
            raise ValueError(f"Invalid or duplicate activity id: {event_id}")
        event_ids.add(event_id)
        if event["mode"] not in SUPPORTED_MODES:
            raise ValueError(f"Activity flow is not implemented: {event['mode']}")
        start = _parse_time(event["oversea_start"])
        end = _parse_time(event["oversea_end"])
        for family, starts_at, ends_at in (
            (server.SERVER_FAMILY_OVERSEA, start, end),
            (
                server.SERVER_FAMILY_CN,
                _parse_time(event["cn_start"]) if "cn_start" in event else start + delay,
                _parse_time(event["cn_end"]) if "cn_end" in event else end + delay,
            ),
        ):
            if starts_at >= ends_at:
                raise ValueError(f"Activity must end after it starts: {event_id}/{family}")
            windows.append(ActivityWindow(
                event_id=event_id,
                name=event["name"],
                mode=event["mode"],
                server_family=family,
                start=starts_at,
                end=ends_at,
            ))
    # One visual flow cannot distinguish two campaigns with the same assets.
    # Reject overlapping reuse instead of marking both campaigns as claimed
    # after observing a single obtained marker. Adjacent windows are valid.
    for index, first in enumerate(windows):
        for second in windows[index + 1:]:
            if (
                first.server_family == second.server_family
                and first.mode == second.mode
                and first.start < second.end
                and second.start < first.end
            ):
                raise ValueError(f"Overlapping activity flow: {first.event_id}/{second.event_id}")
    return tuple(windows)


def _server_windows(config) -> tuple[ActivityWindow, ...]:
    family = server.server_family(config.Emulator_PackageName)
    supported = (
        family == server.SERVER_FAMILY_CN and server.lang == "cn"
    ) or (
        family == server.SERVER_FAMILY_OVERSEA and server.lang == "global_cn"
    )
    if not supported:
        return ()
    return tuple(
        window for window in load_calendar()
        if window.server_family == family
    )


def active_activities(config, now: datetime | None = None) -> tuple[ActivityWindow, ...]:
    now = now or datetime.now(EVENT_TIMEZONE)
    return tuple(window for window in _server_windows(config) if window.contains(now))


def next_activity_start(config, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(EVENT_TIMEZONE)
    return min((window.start for window in _server_windows(config) if window.start > now),
               default=None)


def render_timeline() -> str:
    """Build a Markdown Gantt chart from the same windows used by the task."""
    windows = load_calendar()
    lines = [
        "活动时间表由任务实际读取的日历生成，时间均为北京时间。",
        "",
        "```mermaid",
        "gantt",
        "    title 活动衔接时间表",
        "    dateFormat YYYY-MM-DD HH:mm",
        "    axisFormat %m/%d",
    ]
    for family, label in ((server.SERVER_FAMILY_OVERSEA, "国际服"),
                          (server.SERVER_FAMILY_CN, "国服")):
        lines.append(f"    section {label}")
        for event in windows:
            if event.server_family == family:
                lines.append(
                    f"    {event.name} :{family}_{event.event_id}, "
                    f"{event.start:%Y-%m-%d %H:%M}, {event.end:%Y-%m-%d %H:%M}"
                )
    lines.extend(["```", "", "| 活动 | 服务器 | 开始 | 结束 |", "| --- | --- | --- | --- |"])
    for event in windows:
        family = "国服" if event.server_family == server.SERVER_FAMILY_CN else "国际服"
        lines.append(
            f"| {event.name} | {family} | {event.start:%Y-%m-%d %H:%M} | "
            f"{event.end:%Y-%m-%d %H:%M} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(render_timeline(), end="")
