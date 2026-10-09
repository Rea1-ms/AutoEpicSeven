"""
Mission reward task facade.
"""

from module.logger import logger
from tasks.mission_reward.current import CurrentMissionReward


class MissionReward:
    def __new__(cls, config, device=None, task=None):
        logger.attr("MissionRewardVariant", "current")
        return CurrentMissionReward(config=config, device=device, task=task)
