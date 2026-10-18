from datetime import datetime

from module.config import server
from module.config.utils import get_server_last_update
from module.logger import logger
from module.ocr.language import resolve_ocr_lang

DAILY_PLAN_PATH = "MissionReward.MissionReward.DailyPlan"
STAMINA_MISSIONS = frozenset(("stamina_50", "stamina_100", "stamina_150"))
CORE_MISSIONS = frozenset(("core_1", "core_2", "core_3"))
ARENA_MISSIONS = frozenset(("arena_5", "arena_10"))


def daily_priority_enabled(config) -> bool:
    """Read supported daily-priority switches without task-bound defaults."""
    package = getattr(config, "Emulator_PackageName", None)
    supported = package and (
        server.is_cn_server(package)
        or (server.is_oversea_server(package) and resolve_ocr_lang(config) == "cn")
    )
    if not supported:
        return False
    return all(
        config.cross_get(path, default=default)
        for path, default in (
            ("MissionReward.Scheduler.Enable", False),
            ("MissionReward.MissionReward.ClaimDailyRewards", True),
            ("MissionReward.MissionReward.DailyPriority", True),
        )
    )


def daily_snapshot_context(config) -> dict[str, str]:
    """Identify the server and recognition language that produced a snapshot."""
    package = getattr(config, "Emulator_PackageName", "")
    return {
        "server": server.normalize_server(package),
        "language": resolve_ocr_lang(config),
    }


def _current_daily_plan(config) -> dict | None:
    """Return a valid snapshot from the current server day, never a cached one."""
    plan = config.cross_get(DAILY_PLAN_PATH, default=None)
    if not isinstance(plan, dict):
        return None
    # The server day alone does not identify the mission list: two packages can
    # share a configured reset time, and a language change invalidates OCR
    # evidence. Legacy snapshots lack this context and must be scanned again.
    if any(plan.get(key) != value for key, value in daily_snapshot_context(config).items()):
        return None
    day = plan.get("day")
    if not isinstance(day, str):
        return None
    try:
        day = datetime.fromisoformat(day)
    except ValueError:
        return None
    update = config.cross_get(
        "MissionReward.Scheduler.ServerUpdate", default=config.Scheduler_ServerUpdate
    )
    if day != get_server_last_update(update):
        return None
    points = plan.get("points")
    if type(points) is not int or points < 0:
        return None
    if type(plan.get("rewards_claimed")) is not bool:
        return None
    missions = plan.get("missions")
    if not isinstance(missions, list):
        return None
    for mission in missions:
        if not isinstance(mission, dict) or not isinstance(mission.get("key"), str):
            return None
        current, total, reward = (
            mission.get("current"), mission.get("total"), mission.get("points")
        )
        if any(type(value) is not int for value in (current, total, reward)):
            return None
        if total <= 0 or not 0 <= current <= total or reward < 0:
            return None
    return plan


def should_schedule_mission_reward(config) -> bool:
    """
    Return whether another task should pull MissionReward forward.

    A full counter only blocks calls when it was observed during the current
    server day. This prevents yesterday's 100-point snapshot from suppressing
    the first reward check after the daily reset.
    """
    if daily_priority_enabled(config):
        plan = _current_daily_plan(config)
        if plan is not None and plan["points"] >= 100 and plan["rewards_claimed"]:
            logger.info("MissionReward: current daily target and all rewards confirmed")
            return False
        return True

    daily = config.stored.DailyActivity
    last_update = get_server_last_update(config.Scheduler_ServerUpdate)
    if daily.time < last_update:
        return True
    if daily.value >= daily.total:
        logger.info(
            f"MissionReward: daily activity already full ({daily.value}/{daily.total}), "
            "skip task call"
        )
        return False
    return True
