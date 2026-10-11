# ruff: noqa: E402
"""Current inventory-counter rules; obsolete contracts stay in the inventory."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.config import server

server.set_lang('global_cn')

from tasks.item.data_update import DataUpdate, E7DigitCounter, normalize_e7_counter_text


class LegacyRuleTests(unittest.TestCase):
    def test_normalize_e7_counter_text(self):
        self.assertEqual(normalize_e7_counter_text(" 1,026 / 1,000 "), "1026/1000")
        self.assertEqual(normalize_e7_counter_text("250／500"), "250/500")
        self.assertEqual(normalize_e7_counter_text("8O6/1OOO"), "806/1000")

    def test_parse_e7_counter_text(self):
        # Keep the migration ID while exercising the actual inventory reader,
        # counter postprocessor, and parser instead of a parallel text helper.
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
            with self.subTest(raw=raw), patch.object(
                E7DigitCounter, "ocr_single_line", autospec=True,
                side_effect=lambda reader, image: reader.format_result(reader.after_process(raw)),
            ):
                self.assertEqual(updater._ocr_equipment_inventory_count(), expected)
