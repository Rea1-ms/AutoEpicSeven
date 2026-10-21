# ruff: noqa: E402
"""Shared task-entry construction and OCR language dispatch."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.config import server as _test_server

_test_server.set_lang('global_cn')

from module.ocr.language import resolve_ocr_lang
from tasks.base.resource_bar import ResourceBarMixin
from tasks.gacha.result import SummonResultRecorder
from tasks.mail.mail import Mail
from tasks.mission_reward.current import CurrentMissionReward
from tasks.mission_reward.mission_reward import MissionReward
from tasks.store.purchase import resolve_ocr_lang as store_ocr_lang
from tasks.store.store import Store


class SharedEntryTests(unittest.TestCase):
    def test_recognition_languages_share_normalization_without_changing_assets(self):
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
        config, device, task = object(), object(), object()
        for entry, target in (
            (Store, "tasks.store.current.CurrentStore"),
            (MissionReward, "tasks.mission_reward.mission_reward.CurrentMissionReward"),
        ):
            with self.subTest(entry=entry.__name__), patch(target) as implementation:
                self.assertIs(entry(config, device=device, task=task), implementation.return_value)
                implementation.assert_called_once_with(config=config, device=device, task=task)
