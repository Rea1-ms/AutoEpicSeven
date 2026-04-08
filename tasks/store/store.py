"""
Store task facade.

Route to legacy/current implementation according to server family.
"""

import module.config.server as server
from module.logger import logger


class Store:
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
            from tasks.store.current import CurrentStore
            return CurrentStore
        from tasks.store.legacy import LegacyStore
        return LegacyStore

    def __new__(cls, config, device=None, task=None):
        variant = cls.resolve_variant(config)
        impl = cls.resolve_impl(config)
        logger.attr("StoreVariant", variant)
        return impl(config=config, device=device, task=task)
