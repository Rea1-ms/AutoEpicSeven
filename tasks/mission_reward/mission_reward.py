"""
Mission reward task facade.
"""

from module.logger import logger
from tasks.mission_reward.current import CurrentMissionReward
from tasks.mission_reward.scheduling import daily_priority_enabled


class MissionReward:
    def __new__(cls, config, device=None, task=None):
        if daily_priority_enabled(config):
            from tasks.mission_reward.daily_reward import DailyMissionReward

            logger.attr("MissionRewardVariant", "daily_priority")
            return DailyMissionReward(config=config, device=device, task=task)
        logger.attr("MissionRewardVariant", "current")
        return CurrentMissionReward(config=config, device=device, task=task)
