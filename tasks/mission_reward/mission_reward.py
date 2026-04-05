"""
Mission reward task facade.

Route to legacy/current implementation according to server family.
"""

import module.config.server as server
from module.logger import logger
from tasks.mission_reward.current import CurrentMissionReward
from tasks.mission_reward.legacy import LegacyMissionReward


class MissionReward:
    @staticmethod
    def resolve_variant(config) -> str:
        package_name = getattr(config, "Emulator_PackageName", "")
        if server.is_oversea_server(package_name):
            return "current"
        return "legacy"

    @classmethod
    def resolve_impl(cls, config):
        variant = cls.resolve_variant(config)
        if variant == "current":
            return CurrentMissionReward
        return LegacyMissionReward

    def __new__(cls, config, device=None, task=None):
        variant = cls.resolve_variant(config)
        impl = cls.resolve_impl(config)
        logger.attr("MissionRewardVariant", variant)
        return impl(config=config, device=device, task=task)
