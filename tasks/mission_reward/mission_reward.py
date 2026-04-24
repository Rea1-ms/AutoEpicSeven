"""
Mission reward task facade.
"""

from module.logger import logger
from tasks.mission_reward.current import CurrentMissionReward


class MissionReward:
    @staticmethod
    def resolve_variant(config) -> str:
        return "current"

    @classmethod
    def resolve_impl(cls, config):
        return CurrentMissionReward

    def __new__(cls, config, device=None, task=None):
        variant = cls.resolve_variant(config)
        impl = cls.resolve_impl(config)
        logger.attr("MissionRewardVariant", variant)
        return impl(config=config, device=device, task=task)
