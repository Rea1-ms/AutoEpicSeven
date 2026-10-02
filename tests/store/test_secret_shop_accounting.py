# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

from contextlib import contextmanager

from tasks.secret_shop.secret_shop import SecretShop


class _DummyStoredValue:
    def __init__(self, value):
        self.value = value


class _DummyStored:
    def __init__(self, gold, skystone):
        self.E7Gold = _DummyStoredValue(gold)
        self.E7Skystone = _DummyStoredValue(skystone)


class _DummyConfig:
    def __init__(self, gold, skystone):
        self.stored = _DummyStored(gold, skystone)

    @contextmanager
    def multi_set(self):
        yield


def _build_shop(gold=0, skystone=0, buy_covenant=True, buy_mystic=True, only_free=False):
    shop = SecretShop.__new__(SecretShop)
    shop.config = _DummyConfig(gold, skystone)
    shop.buy_covenant = buy_covenant
    shop.buy_mystic = buy_mystic
    shop.only_free = only_free
    shop._resource_dirty = False
    shop._refreshes_since_resource_sync = 0
    return shop


def test_only_free_entry_gold_requirement():
    shop = _build_shop(gold=200000, only_free=True)

    assert shop._only_free_entry_gold_requirement() == 280000
    assert shop._should_stop_only_free_by_gold(resource_synced=True) is True


def test_minimum_affordable_gold_requirement():
    shop = _build_shop(gold=200000)

    assert shop._minimum_affordable_gold_requirement() == 184000
    assert shop._can_afford_any_enabled_item() is True

    shop.config.stored.E7Gold.value = 180000
    assert shop._can_afford_any_enabled_item() is False


def test_apply_local_resource_spend():
    shop = _build_shop(gold=1000000, skystone=30)

    shop._apply_local_resource_spend(gold=280000, skystone=3, reason='test')

    assert shop.config.stored.E7Gold.value == 720000
    assert shop.config.stored.E7Skystone.value == 27
    assert shop._resource_dirty is True
    assert shop._refreshes_since_resource_sync == 1


def test_should_mid_run_resource_sync():
    shop = _build_shop()

    shop._resource_dirty = True
    shop._refreshes_since_resource_sync = 9
    assert shop._should_mid_run_resource_sync() is False

    shop._refreshes_since_resource_sync = 10
    assert shop._should_mid_run_resource_sync() is True




# Historical assertions retained verbatim for migration review; not executable evidence.
HISTORICAL_CONTRACTS = 'def test_only_free_entry_gold_requirement(self):\n        test_only_free_entry_gold_requirement()\n\ndef test_minimum_affordable_gold_requirement(self):\n        test_minimum_affordable_gold_requirement()\n\ndef test_apply_local_resource_spend(self):\n        test_apply_local_resource_spend()\n\ndef test_should_mid_run_resource_sync(self):\n        test_should_mid_run_resource_sync()\n\nclass LegacyRuleTests(unittest.TestCase):\n    def test_only_free_entry_gold_requirement(self):\n        test_only_free_entry_gold_requirement()\n\n    def test_minimum_affordable_gold_requirement(self):\n        test_minimum_affordable_gold_requirement()\n\n    def test_apply_local_resource_spend(self):\n        test_apply_local_resource_spend()\n\n    def test_should_mid_run_resource_sync(self):\n        test_should_mid_run_resource_sync()'
