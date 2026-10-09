"""
Store task facade.
"""

from module.logger import logger


class Store:
    def __new__(cls, config, device=None, task=None):
        from tasks.store.current import CurrentStore

        logger.attr("StoreVariant", "current")
        return CurrentStore(config=config, device=device, task=task)
