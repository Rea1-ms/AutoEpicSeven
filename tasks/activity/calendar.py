"""Adapt shared game facts to the activity flows this task implements."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import module.config.server as server
from module.game_info.catalog import INFO_PATH, INFO_TIMEZONE, load_info
from module.game_info.timeline import render_timeline as render_game_timeline


EVENT_TIMEZONE = INFO_TIMEZONE
CALENDAR_PATH = INFO_PATH
DEFAULT_FREE_GACHA_20_ID = "free_gacha_20_2026_08_27"
SUPPORTED_MODES = ("legacy", "free_gacha_20", "e7wc_battle_gate", "koharu_raffle")


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


def load_calendar(path: Path = CALENDAR_PATH) -> tuple[ActivityWindow, ...]:
    # Catalog categories may exist before automation supports them. Only this
    # adapter owns the supported-flow list; adding facts never executes code.
    return tuple(ActivityWindow(p.event_id, p.name, p.kind, p.server_family, p.start, p.end)
                 for p in load_info(path).periods if p.kind in SUPPORTED_MODES)


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
        and (window.mode not in ("e7wc_battle_gate", "koharu_raffle") or family == server.SERVER_FAMILY_OVERSEA)
    )


def active_activities(config, now: datetime | None = None) -> tuple[ActivityWindow, ...]:
    now = now or datetime.now(EVENT_TIMEZONE)
    return tuple(window for window in _server_windows(config) if window.contains(now))


def next_activity_start(config, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(EVENT_TIMEZONE)
    return min((window.start for window in _server_windows(config) if window.start > now),
               default=None)


def render_timeline() -> str:
    """Keep the previous command available while using the shared renderer."""
    return render_game_timeline(load_info())


if __name__ == "__main__":
    print(render_timeline(), end="")
