from datetime import datetime

from module.config.utils import get_server_last_update
from module.game_info.catalog import aware_time
from module.logger import logger
from tasks.activity.calendar import (
    ACTIVITY_TASK_MODES,
    DEFAULT_FREE_GACHA_20_ID,
    active_activities,
    next_activity_start,
)

FREE_GACHA_20_CHECKED_AT = "SpecialActivity.ActivityRuntime.FreeGacha20CheckedAt"


def delay_next_activity_check(config, *, task="SpecialActivity") -> None:
    starts_at = next_activity_start(config, task=task)
    targets = [starts_at] if starts_at is not None else []
    has_uncompleted_active = False
    now = aware_time()
    for event in active_activities(config, now, task=task):
        if is_activity_completed(config, event.event_id):
            # A completed handbook must not fall back to the daily server
            # update. Keep one wake-up at the event boundary so the calendar
            # can retire this event without repeated daily checks.
            targets.append(event.end)
            continue
        has_uncompleted_active = True
        refresh = event.next_refresh(now)
        if refresh is not None:
            targets.append(refresh)
    if not targets:
        config.task_delay(server_update=True, task=task)
        return
    # Calendar dates are timezone-aware; the scheduler stores local naive
    # datetimes. Wake at the earliest reset, campaign launch or stock refresh,
    # so an 11:00 opening/refresh is not postponed until tomorrow.
    target = min(targets).astimezone().replace(tzinfo=None)
    if has_uncompleted_active:
        config.task_delay(server_update=True, target=target, task=task)
    else:
        config.task_delay(target=target, task=task)


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
    # Retain the shared historical storage path so splitting the scheduler
    # neither drops receipts nor repeats fixed-quota purchases. Task ownership
    # controls due times; event/server ownership controls completed rewards.
    return f"SpecialActivity.ActivityRuntime.CheckedEvents.{event_id}.{family}"


def _completed_path(config, event_id: str) -> str:
    from module.config.server import server_family

    family = server_family(config.Emulator_PackageName)
    # CheckedEvents is a keepvalue dictionary. Unknown ActivityRuntime
    # fields are removed on config reload, so keep the lifetime marker
    # beside this event's daily receipts without changing their timestamps.
    return f"SpecialActivity.ActivityRuntime.CheckedEvents.{event_id}.Completed.{family}"


def is_activity_completed(config, event_id: str) -> bool:
    """Return whether an event-wide completion was confirmed on this server."""
    value = config.cross_get(_completed_path(config, event_id), default=None)
    return value is not None and value is not False


def mark_activity_completed(config, event_id: str) -> None:
    completed_at = datetime.now().replace(microsecond=0)
    config.cross_set(_completed_path(config, event_id), completed_at)
    logger.info(f"SpecialActivity: {event_id} completed at {completed_at}")


def is_activity_checked_today(config, event_id: str, *, task="LimitedActivity") -> bool:
    """Return whether this campaign's reward was checked this server day."""
    if is_activity_completed(config, event_id):
        return True
    checked_at = _free_gacha_20_checked_at(config, event_id)
    if checked_at is None:
        return False

    server_update = config.cross_get(
        f"{task}.Scheduler.ServerUpdate",
        default=config.Scheduler_ServerUpdate,
    )
    return checked_at >= get_server_last_update(server_update)


def is_activity_checked_in_window(config, event, now=None) -> bool:
    """Keep twice-daily shop checks separate from server-day rewards."""
    if is_activity_completed(config, event.event_id):
        return True
    if not event.refresh_hours:
        task = next(task for task, modes in ACTIVITY_TASK_MODES.items() if event.mode in modes)
        return is_activity_checked_today(config, event.event_id, task=task)
    checked_at = _free_gacha_20_checked_at(config, event.event_id)
    if checked_at is None:
        return False
    now = aware_time(now)
    return event.refresh_start(now) <= aware_time(checked_at) <= now


def is_activity_checked_since(config, event_id: str, since, now=None) -> bool:
    """Check an event-wide quota without resetting it at daily refreshes."""
    checked_at = _free_gacha_20_checked_at(config, event_id)
    return checked_at is not None and aware_time(since) <= aware_time(checked_at) <= aware_time(now)


def mark_free_gacha_20_checked(config, event_id=DEFAULT_FREE_GACHA_20_ID) -> None:
    mark_activity_checked(config, event_id)


def mark_activity_checked(config, event_id: str) -> None:
    checked_at = datetime.now().replace(microsecond=0)
    config.cross_set(_checked_path(config, event_id), checked_at)
    logger.info(f"SpecialActivity: {event_id} reward checked at {checked_at}")


def should_schedule_after_battle(config, *, task="LimitedActivity", arena=False) -> bool:
    if not config.is_task_enabled(task):
        return False

    for event in active_activities(config, task=task):
        if event.mode in ("epic_path", "alencia_handbook"):
            # An empty reward queue is not daily completion. The claim flow
            # only writes this event/server record after ALL_TASK_DONE, so
            # later battles can request another check without waking retired
            # events or reusing a different campaign's completion timestamp.
            # Arena/Combat are bound to their own option groups. Read the
            # owning task's saved switch instead of a generated default left
            # on the currently bound config object.
            option = {
                "epic_path": "GetEpicPathDailyReward",
                "alencia_handbook": "GetAlenciaDailyReward",
            }[event.mode]
            enabled = config.cross_get(
                f"{task}.{task}.{option}",
                default=getattr(config, f"{task}_{option}"),
            )
            if enabled and not is_activity_checked_today(config, event.event_id, task=task):
                return True
        elif event.mode == "support_reward" and arena:
            enabled = config.cross_get(
                "LimitedActivity.LimitedActivity.GetSupportReward",
                default=config.LimitedActivity_GetSupportReward,
            )
            if enabled and not is_activity_checked_in_window(config, event):
                return True
        elif event.mode == "legacy":
            from tasks.activity.legacy.summer_2026_06_25.scheduling import (
                should_schedule_after_battle as legacy_schedule,
            )

            if legacy_schedule(config):
                return True
    return False


def schedule_activity_after_battle(config, *, arena=False) -> None:
    """Wake only the enabled task that owns an unfinished battle reward."""
    for task in ACTIVITY_TASK_MODES:
        if should_schedule_after_battle(config, task=task, arena=arena):
            config.task_call(task, force_call=False)
