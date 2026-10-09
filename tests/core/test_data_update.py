# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

import json
from pathlib import Path

from tasks.item.data_update import E7DigitCounter, normalize_e7_counter_text
from tasks.item.data_update import DataUpdate
from types import SimpleNamespace
from unittest.mock import patch


def test_normalize_e7_counter_text():
    assert normalize_e7_counter_text(" 1,026 / 1,000 ") == "1026/1000"
    assert normalize_e7_counter_text("250／500") == "250/500"
    assert normalize_e7_counter_text("8O6/1OOO") == "806/1000"


def test_parse_e7_counter_text():
    # Preserve the migration ID, but exercise the inventory reader and the
    # actual counter postprocessor/parser rather than a parallel text helper.
    updater = DataUpdate.__new__(DataUpdate)
    updater.config = SimpleNamespace(Emulator_GameLanguage="auto")
    updater.device = SimpleNamespace(image=object())
    for raw, expected in (
        ("1026/1000", (1026, -26, 1000)),
        ("806/1000", (806, 194, 1000)),
        ("250/500", (250, 250, 500)),
        (" 1,026 ／ 1,000 ", (1026, -26, 1000)),
        ("8O6/1OOO", (806, 194, 1000)),
        ("invalid", (0, 0, 0)),
    ):
        with patch.object(E7DigitCounter, "ocr_single_line", autospec=True,
                          side_effect=lambda reader, image: reader.format_result(reader.after_process(raw))):
            assert updater._ocr_equipment_inventory_count() == expected


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


class SharedEntryTests(unittest.TestCase):
    def test_recognition_languages_share_normalization_without_changing_assets(self):
        from module.ocr.language import resolve_ocr_lang
        from tasks.base.resource_bar import ResourceBarMixin
        from tasks.gacha.result import SummonResultRecorder
        from tasks.mail.mail import Mail
        from tasks.mission_reward.current import CurrentMissionReward
        from tasks.store.purchase import resolve_ocr_lang as store_ocr_lang

        consumers = (
            ResourceBarMixin._ocr_lang, SummonResultRecorder._ocr_lang,
            Mail._ocr_lang, CurrentMissionReward._mission_ocr_lang,
        )
        before = _test_server.lang
        groups = (
            ("cn", ("auto", "", None, "cn", "global_cn", "zh", "zh_cn", "invalid")),
            ("en", ("en", "global_en", "en_us")),
            ("jp", ("jp", "ja", "ja_jp")),
            ("tw", ("tw", "zh_tw")),
        )
        for expected, languages in groups:
            for language in languages:
                with self.subTest(language=language):
                    config = SimpleNamespace(Emulator_GameLanguage=language)
                    self.assertEqual(resolve_ocr_lang(config), expected)
                    self.assertEqual(store_ocr_lang(config), expected)
                    for method in consumers:
                        self.assertEqual(method(SimpleNamespace(config=config)), expected)
        self.assertEqual(resolve_ocr_lang(SimpleNamespace()), "cn")
        self.assertEqual(_test_server.lang, before)

    def test_fixed_task_entries_preserve_constructor_arguments(self):
        from tasks.store.store import Store
        from tasks.mission_reward.mission_reward import MissionReward

        config, device, task = object(), object(), object()
        for entry, target in (
            (Store, "tasks.store.current.CurrentStore"),
            (MissionReward, "tasks.mission_reward.mission_reward.CurrentMissionReward"),
        ):
            with self.subTest(entry=entry.__name__), patch(target) as implementation:
                self.assertIs(entry(config, device=device, task=task), implementation.return_value)
                implementation.assert_called_once_with(config=config, device=device, task=task)
