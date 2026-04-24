"""
Store task facade.
"""

from module.logger import logger


class Store:
    @staticmethod
    def resolve_variant(config) -> str:
        return "current"

    @classmethod
    def resolve_impl(cls, config):
        from tasks.store.current import CurrentStore
        return CurrentStore

    def __new__(cls, config, device=None, task=None):
        variant = cls.resolve_variant(config)
        impl = cls.resolve_impl(config)
        logger.attr("StoreVariant", variant)
        return impl(config=config, device=device, task=task)
