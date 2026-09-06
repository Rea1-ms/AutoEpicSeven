from datetime import datetime

import module.config.server as server_
from module.config.utils import get_server_last_update
from module.logger import logger


TASK_REWARD_CLAIMED_AT = "SpecialActivity.ActivityRuntime.TaskRewardClaimedAt"
FREE_GACHA_20_CHECKED_AT = "SpecialActivity.ActivityRuntime.FreeGacha20CheckedAt"


def _task_reward_claimed_at(config) -> datetime | None:
    value = config.cross_get(TASK_REWARD_CLAIMED_AT, default=None)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def is_task_reward_claimed_today(config) -> bool:
    """
    Return whether the task-reward completion state belongs to this server day.

    The timestamp is intentionally kept after expiry. Comparing it with the
    configured server update makes process restarts safe without needing a
    separate reset task that could run after Arena or Combat by priority.
    """
    claimed_at = _task_reward_claimed_at(config)
    if claimed_at is None:
        return False

    server_update = config.cross_get(
        "SpecialActivity.Scheduler.ServerUpdate",
        default=config.Scheduler_ServerUpdate,
    )
    return claimed_at >= get_server_last_update(server_update)


def mark_task_reward_claimed(config) -> None:
    claimed_at = datetime.now().replace(microsecond=0)
    config.cross_set(TASK_REWARD_CLAIMED_AT, claimed_at)
    logger.info(f"SpecialActivity: task reward locked for today at {claimed_at}")


def _free_gacha_20_checked_at(config) -> datetime | None:
    value = config.cross_get(FREE_GACHA_20_CHECKED_AT, default=None)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def is_free_gacha_20_checked_today(config) -> bool:
    """Return whether the overseas reward was checked this server day."""
    checked_at = _free_gacha_20_checked_at(config)
    if checked_at is None:
        return False

    server_update = config.cross_get(
        "SpecialActivity.Scheduler.ServerUpdate",
        default=config.Scheduler_ServerUpdate,
    )
    return checked_at >= get_server_last_update(server_update)


def mark_free_gacha_20_checked(config) -> None:
    checked_at = datetime.now().replace(microsecond=0)
    config.cross_set(FREE_GACHA_20_CHECKED_AT, checked_at)
    logger.info(f"SpecialActivity: 20-free-summon reward checked at {checked_at}")


def should_schedule_after_battle(config) -> bool:
    # The overseas event reward is claimed once immediately after login. Only
    # the legacy CN activity still unlocks daily task rewards through battles.
    if not server_.is_cn_server(config.Emulator_PackageName):
        return False
    if not config.SpecialActivity_GetTaskReward:
        logger.info("SpecialActivity: task reward disabled, skip task call")
        return False
    if is_task_reward_claimed_today(config):
        logger.info(
            "SpecialActivity: task reward already claimed today, skip task call"
        )
        return False
    return True
