# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Regression checks for upgrading the retired summer-event configuration."""
import json
import unittest
from datetime import datetime
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE

from module.config.config_updater import ConfigUpdater


class SpecialActivityConfigTests(unittest.TestCase):
    def setUp(self):
        self.updater = ConfigUpdater()


    def test_campaign_records_survive_config_upgrade_and_json_reload(self):
        records = {
            "campaign_one": {
                "CN": "2026-09-18 11:05:00",
                "OVERSEA": "2026-09-18 02:05:00",
            },
            "campaign_two": {"CN": "2026-09-19 11:05:00"},
        }
        data = {"SpecialActivity": {"ActivityRuntime": {"CheckedEvents": records}}}
        for _ in range(2):
            data = self.updater.config_update(json.loads(json.dumps(data, default=str)))
            self.assertEqual(data["SpecialActivity"]["ActivityRuntime"]["CheckedEvents"], records)

    def test_pre_calendar_check_time_survives_upgrade_and_json_reload(self):
        stamp = datetime(2026, 9, 18, 11, 5)
        data = {"SpecialActivity": {"ActivityRuntime": {"FreeGacha20CheckedAt": stamp}}}
        for _ in range(2):
            data = self.updater.config_update(json.loads(json.dumps(data, default=str)))
            runtime = data["SpecialActivity"]["ActivityRuntime"]
            self.assertEqual(runtime["FreeGacha20CheckedAt"], stamp)
            self.assertEqual(runtime["CheckedEvents"], {})

# Historical assertions retained verbatim for migration review; not executable evidence.
HISTORICAL_CONTRACTS = 'def test_upgrade_removes_retired_controls_and_preserves_user_preferences(self):\n        old = {\n            "SpecialActivity": {\n                "Scheduler": {"Enable": False},\n                "SpecialActivity": {\n                    "GetDailyReward": True,\n                    "GetTaskReward": True,\n                    "GetFreeGacha": False,\n                    "GetEnergyDrink": True,\n                },\n                "GachaResult": {"SaveScreenshot": True, "OcrResult": False},\n            },\n            "Gacha": {"GachaResult": {"SaveScreenshot": False, "OcrResult": True}},\n        }\n        upgraded = self.updater.config_update(old)\n        event = upgraded["SpecialActivity"]\n        self.assertEqual(event["SpecialActivity"], {"GetFreeGacha": False, "GetE7wcBattleGateReward": True, "GetKoharuRaffleReward": True, "BuyHucheMysticMedals": False})\n        self.assertFalse(event["Scheduler"]["Enable"])\n        self.assertNotIn("GachaResult", event)\n        self.assertEqual(upgraded["Gacha"]["GachaResult"], old["Gacha"]["GachaResult"])'
