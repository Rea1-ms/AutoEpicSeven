from datetime import datetime

from module.config.utils import get_server_last_update
from module.logger import logger
from tasks.activity.calendar import DEFAULT_FREE_GACHA_20_ID, active_activities, next_activity_start


FREE_GACHA_20_CHECKED_AT = "SpecialActivity.ActivityRuntime.FreeGacha20CheckedAt"


def delay_next_activity_check(config) -> None:
    starts_at = next_activity_start(config)
    if starts_at is None:
        config.task_delay(server_update=True)
        return
    # Calendar dates are timezone-aware; the scheduler stores local naive
    # datetimes. Wake at the earlier of the next reset and campaign launch,
    # so a CN maintenance opening at 11:00 is not postponed until tomorrow.
    target = starts_at.astimezone().replace(tzinfo=None)
    config.task_delay(server_update=True, target=target)


def _free_gacha_20_checked_at(config, event_id: str) -> datetime | None:
    value = config.cross_get(_checked_path(config, event_id), default=None)
    # Only the original campaign may inherit the pre-calendar daily marker.
    # Reusing it for later campaigns would skip a new reward on transition day.
    if value is None and event_id == DEFAULT_FREE_GACHA_20_ID:
        value = config.cross_get(FREE_GACHA_20_CHECKED_AT, default=None)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _checked_path(config, event_id: str) -> str:
    from module.config.server import server_family

    family = server_family(config.Emulator_PackageName)
    return f"SpecialActivity.ActivityRuntime.CheckedEvents.{event_id}.{family}"


def is_free_gacha_20_checked_today(config, event_id=DEFAULT_FREE_GACHA_20_ID) -> bool:
    """Return whether this campaign's reward was checked this server day."""
    checked_at = _free_gacha_20_checked_at(config, event_id)
    if checked_at is None:
        return False

    server_update = config.cross_get(
        "SpecialActivity.Scheduler.ServerUpdate",
        default=config.Scheduler_ServerUpdate,
    )
    return checked_at >= get_server_last_update(server_update)


def mark_free_gacha_20_checked(config, event_id=DEFAULT_FREE_GACHA_20_ID) -> None:
    checked_at = datetime.now().replace(microsecond=0)
    config.cross_set(_checked_path(config, event_id), checked_at)
    logger.info(f"SpecialActivity: 20-free-summon reward checked at {checked_at}")


def should_schedule_after_battle(config) -> bool:
    if not any(event.mode == "legacy" for event in active_activities(config)):
        return False
    from tasks.activity.legacy.summer_2026_06_25.scheduling import (
        should_schedule_after_battle as legacy_schedule,
    )

    return legacy_schedule(config)
