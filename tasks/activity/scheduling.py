from datetime import datetime

from module.config.utils import get_server_last_update
from module.game_info.catalog import aware_time
from module.logger import logger
from tasks.activity.calendar import ACTIVITY_TASK_MODES, DEFAULT_FREE_GACHA_20_ID, active_activities, next_activity_start


FREE_GACHA_20_CHECKED_AT = "SpecialActivity.ActivityRuntime.FreeGacha20CheckedAt"


def delay_next_activity_check(config, *, task="SpecialActivity") -> None:
    starts_at = next_activity_start(config, task=task)
    targets = [starts_at] if starts_at is not None else []
    now = aware_time()
    for event in active_activities(config, now, task=task):
        if event.mode == "huche_shop" and not config.SpecialActivity_BuyHucheMysticMedals:
            continue
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
    config.task_delay(server_update=True, target=target, task=task)


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


def is_free_gacha_20_checked_today(config, event_id=DEFAULT_FREE_GACHA_20_ID) -> bool:
    return is_activity_checked_today(config, event_id)


def is_activity_checked_today(config, event_id: str, *, task="LimitedActivity") -> bool:
    """Return whether this campaign's reward was checked this server day."""
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


def should_schedule_after_battle(config, *, task="LimitedActivity") -> bool:
    if not config.is_task_enabled(task):
        return False

    for event in active_activities(config, task=task):
        if event.mode == "koharu_raffle":
            # An empty reward queue is not daily completion. The claim flow
            # only writes this event/server record after ALL_TASK_DONE, so
            # later battles can request another check without waking retired
            # events or reusing a different campaign's completion timestamp.
            # Arena/Combat are bound to their own option groups. Read the
            # owning task's saved switch instead of a generated default left
            # on the currently bound config object.
            enabled = config.cross_get(
                "LimitedActivity.LimitedActivity.GetKoharuRaffleReward",
                default=config.LimitedActivity_GetKoharuRaffleReward,
            )
            if enabled and not is_activity_checked_today(config, event.event_id, task=task):
                return True
        elif event.mode == "legacy":
            from tasks.activity.legacy.summer_2026_06_25.scheduling import (
                should_schedule_after_battle as legacy_schedule,
            )

            if legacy_schedule(config):
                return True
    return False


def schedule_activity_after_battle(config) -> None:
    """Wake only the enabled task that owns an unfinished battle reward."""
    for task in ACTIVITY_TASK_MODES:
        if should_schedule_after_battle(config, task=task):
            config.task_call(task, force_call=False)
