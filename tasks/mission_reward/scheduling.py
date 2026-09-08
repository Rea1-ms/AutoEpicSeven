from module.config.utils import get_server_last_update
from module.logger import logger


def should_schedule_mission_reward(config) -> bool:
    """
    Return whether another task should pull MissionReward forward.

    A full counter only blocks calls when it was observed during the current
    server day. This prevents yesterday's 100-point snapshot from suppressing
    the first reward check after the daily reset.
    """
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
