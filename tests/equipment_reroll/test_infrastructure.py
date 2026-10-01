import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.run_offline_tests import load_tests as load_offline_suite
from tests.support.equipment_reroll import load_sample


class InfrastructureTests(unittest.TestCase):
    def test_tool_menu_entry_is_registered_and_binds_user_settings(self):
        from aes import AutoEpicSeven
        from module.webui.submodule.utils import get_available_func

        self.assertIn("EquipmentReroll", get_available_func())
        app = AutoEpicSeven.__new__(AutoEpicSeven)
        app.__dict__["config"] = SimpleNamespace()
        app.__dict__["device"] = SimpleNamespace()
        with patch("tasks.equipment_reroll.equipment_reroll.EquipmentReroll") as constructor:
            app.equipment_reroll()
        constructor.assert_called_once_with(config=app.config, device=app.device, task="EquipmentReroll")
        constructor.return_value.run.assert_called_once_with()

    def test_source_config_is_manual_only_and_translated(self):
        import yaml
        from tasks.equipment_reroll.rules import STAT_NAMES

        root = Path(__file__).resolve().parents[2]
        folder = root / "module" / "config"
        task = yaml.safe_load((folder / "argument" / "task.yaml").read_text(encoding="utf-8"))
        self.assertEqual(task["Tool"]["tasks"]["EquipmentReroll"], ["Scheduler", "EquipmentReroll"])
        override = yaml.safe_load((folder / "argument" / "override.yaml").read_text(encoding="utf-8"))
        self.assertIs(override["EquipmentReroll"]["Scheduler"]["Enable"]["value"], False)
        for file in (folder / "i18n").glob("*.json"):
            text = json.loads(file.read_text(encoding="utf-8"))
            self.assertTrue(text["Task"]["EquipmentReroll"]["name"])
            for i in range(1, 5):
                for name in STAT_NAMES:
                    self.assertTrue(text["EquipmentReroll"][f"Stat{i}"][name])

    def test_generated_menu_and_config_match_the_source(self):
        from module.config.config_generated import GeneratedConfig
        from tasks.equipment_reroll.equipment_reroll import EquipmentReroll
        from tasks.equipment_reroll.rules import Target

        root = Path(__file__).resolve().parents[2]
        folder = root / "module" / "config" / "argument"
        menu = json.loads((folder / "menu.json").read_text(encoding="utf-8"))
        args = json.loads((folder / "args.json").read_text(encoding="utf-8"))
        template = json.loads((root / "config" / "template.json").read_text(encoding="utf-8"))
        self.assertIn("EquipmentReroll", menu["Tool"]["tasks"])
        self.assertEqual(menu["Tool"]["page"], "tool")
        self.assertIs(template["EquipmentReroll"]["Scheduler"]["Enable"], False)
        self.assertEqual(args["EquipmentReroll"]["Scheduler"]["Enable"]["display"], "hide")
        task = EquipmentReroll(GeneratedConfig(), SimpleNamespace())
        policy, budget = task._policy_from_config()
        self.assertEqual(policy.targets, (Target("Speed", 5), Target("DefensePercent", 8),
                                          Target("HealthPercent", 8), Target("Resistance", 8)))
        self.assertEqual((budget.max_refresh, budget.max_points, budget.reserve_points), (0, 0, 0))

    def test_real_device_construction_is_forbidden(self):
        from module.device.device import Device

        with self.assertRaisesRegex(AssertionError, "真实设备"):
            Device(config=None)

    def test_real_account_config_is_forbidden(self):
        from module.config.config import AzurLaneConfig

        with self.assertRaisesRegex(AssertionError, "账号配置"):
            AzurLaneConfig("offline_must_not_read")

    def test_unknown_case_is_a_preparation_error(self):
        with self.assertRaises(ValueError):
            load_offline_suite("equipment_reroll", "unknown_case")

    def test_missing_sample_is_not_skipped(self):
        with self.assertRaises(FileNotFoundError), patch("pathlib.Path.read_bytes", side_effect=FileNotFoundError):
            load_sample("initial")

    def test_corrupt_sample_is_not_skipped(self):
        with self.assertRaisesRegex(ValueError, "校验失败"), patch("pathlib.Path.read_bytes", return_value=b"corrupt"):
            load_sample("initial")

    def test_missing_numeric_model_fails_before_running_tests(self):
        from dev_tools.run_offline_tests import worker
        from module.ocr.models import OCR_MODEL

        args = SimpleNamespace(suite="equipment_reroll", list=False, out=None,
                               case="tests.equipment_reroll.test_rules.RuleTests.test_no_lock_before_speed_five")
        with patch.object(OCR_MODEL, "get_by_lang", side_effect=[object(), FileNotFoundError("英文数值模型缺失")]) as get_model, patch(
            "unittest.TextTestRunner.run", side_effect=AssertionError("模型缺失时不能开始测试")
        ), self.assertRaisesRegex(FileNotFoundError, "数值模型缺失"):
            worker(args)
        self.assertEqual([call.args[0] for call in get_model.call_args_list], ["cn", "en"])
