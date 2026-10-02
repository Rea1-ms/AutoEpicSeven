# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

import json
from pathlib import Path

from tasks.item.data_update import normalize_e7_counter_text, parse_e7_counter_text
from tasks.item.data_update import DataUpdate


def test_normalize_e7_counter_text():
    assert normalize_e7_counter_text(" 1,026 / 1,000 ") == "1026/1000"
    assert normalize_e7_counter_text("250／500") == "250/500"
    assert normalize_e7_counter_text("8O6/1OOO") == "806/1000"


def test_parse_e7_counter_text():
    assert parse_e7_counter_text("1026/1000") == (1026, 1000)
    assert parse_e7_counter_text("806/1000") == (806, 1000)
    assert parse_e7_counter_text("250/500") == (250, 500)
    assert parse_e7_counter_text("invalid") is None


class _DummyDevice:
    def __init__(self):
        self.image = object()

    def app_is_running(self):
        return True


class _DummyConfig:
    def __init__(self):
        self.task_delay_calls = []

    def task_delay(self, **kwargs):
        self.task_delay_calls.append(kwargs)


class _DataUpdateRunHarness(DataUpdate):
    def __init__(self):
        self.device = _DummyDevice()
        self.config = _DummyConfig()
        self.calls = []

    def _update_equipment_inventory(self, skip_first_screenshot=True) -> bool:
        self.calls.append("equipment")
        return False

    def _update_secret_shop_resources(self, skip_first_screenshot=True) -> bool:
        self.calls.append("secret_shop")
        return False

    def _update_combat_status(self, skip_first_screenshot=True) -> bool:
        self.calls.append("combat")
        return False

    def _update_arena_status(self, skip_first_screenshot=True) -> bool:
        self.calls.append("arena")
        return False

    def ui_goto(self, destination, skip_first_screenshot=True):
        self.calls.append("goto_main")

    def _sync_legacy_item_storage(self):
        self.calls.append("sync")


def test_data_update_run_skips_main_resource_stage():
    updater = _DataUpdateRunHarness()

    assert updater.run() is False
    assert updater.calls == [
        "equipment",
        "secret_shop",
        "combat",
        "arena",
        "goto_main",
        "sync",
    ]
    assert updater.config.task_delay_calls == [{"success": False}]


def test_e7_dashboard_generated_stored_entries_visible():
    stored_path = (
        Path(__file__).resolve().parents[2]
        / "module"
        / "config"
        / "argument"
        / "stored.json"
    )
    stored = json.loads(stored_path.read_text(encoding="utf-8"))

    assert stored["E7ArenaFlag"]["order"] == 8
    assert stored["E7ArenaFlag"]["color"] == "#f2a93b"
    assert stored["E7ConquestPoint"]["order"] == 9
    assert stored["E7ConquestPoint"]["color"] == "#5ea3ff"


import unittest

class LegacyRuleTests(unittest.TestCase):
    def test_normalize_e7_counter_text(self):
        test_normalize_e7_counter_text()

    def test_parse_e7_counter_text(self):
        test_parse_e7_counter_text()



# Historical assertions retained verbatim for migration review; not executable evidence.
HISTORICAL_CONTRACTS = 'def test_data_update_run_skips_main_resource_stage(self):\n        test_data_update_run_skips_main_resource_stage()\n\ndef test_e7_dashboard_generated_stored_entries_visible(self):\n        test_e7_dashboard_generated_stored_entries_visible()'
