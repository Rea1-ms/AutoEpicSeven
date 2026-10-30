import unittest
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.run_offline_tests import run as run_offline_suite
from module.exception import RequestHumanTakeover
from tests.support.equipment_reroll import ReplayClock, ReplayDevice
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

    def test_speed_four_startup_reports_config_reason_without_recognition_or_clicks(self):
        from module.config.config_generated import GeneratedConfig
        from tasks.equipment_reroll.equipment_reroll import EquipmentReroll

        config = GeneratedConfig()
        config.EquipmentReroll_Value1 = 4
        self.device = ReplayDevice([None])
        task = EquipmentReroll(config, self.device)
        with patch.object(task, "execute", side_effect=AssertionError("配置无效时不能进入识别或点击")) as execute, patch(
            "tasks.equipment_reroll.equipment_reroll.logger.critical",
        ) as error, self.assertRaises(RequestHumanTakeover) as raised:
            task.run()
        execute.assert_not_called()
        error.assert_called_once()
        self.assertIn("Invalid target 1 (Speed 4)", error.call_args.args[0])
        self.assertIn("Speed target must be 5", error.call_args.args[0])
        self.assertEqual(raised.exception.args, ())
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertEqual(config.EquipmentReroll_Value1, 4)
        self.assertEqual(self.device.actions, [])

    def test_startup_reports_invalid_goal_duplicate_stat_and_budget_reasons(self):
        from module.config.config_generated import GeneratedConfig
        from tasks.equipment_reroll.equipment_reroll import EquipmentReroll

        cases = (
            ("EquipmentReroll_Value3", 9, "Invalid target 3.*HealthPercent 9.*maximum of 8"),
            ("EquipmentReroll_Stat3", "DefensePercent", "four distinct target substat types"),
            ("EquipmentReroll_MaxPoints", -1, "nonnegative integers"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                config = GeneratedConfig()
                setattr(config, field, value)
                self.device = ReplayDevice([None])
                task = EquipmentReroll(config, self.device)
                with patch.object(task, "execute") as execute, patch(
                    "tasks.equipment_reroll.equipment_reroll.logger.critical",
                ) as error, self.assertRaises(RequestHumanTakeover) as raised:
                    task.run()
                execute.assert_not_called()
                error.assert_called_once()
                self.assertRegex(error.call_args.args[0], reason)
                self.assertEqual(raised.exception.args, ())
                self.assertIsInstance(raised.exception.__cause__, ValueError)
                self.assertEqual(self.device.actions, [])

    def test_unsupported_language_logs_reason_before_starting_recognition(self):
        from module.config.config_generated import GeneratedConfig
        from tasks.equipment_reroll.equipment_reroll import EquipmentReroll

        config = GeneratedConfig()
        config.Emulator_GameLanguage = "en"
        self.device = ReplayDevice([None])
        task = EquipmentReroll(config, self.device)
        with patch.object(task, "execute") as execute, patch(
            "tasks.equipment_reroll.equipment_reroll.logger.critical",
        ) as error, self.assertRaises(RequestHumanTakeover) as raised:
            task.run()
        execute.assert_not_called()
        error.assert_called_once()
        self.assertIn("only Simplified Chinese is supported, configured language: en", error.call_args.args[0])
        self.assertEqual(raised.exception.args, ())
        self.assertEqual(self.device.actions, [])

    def test_runtime_takeover_logs_stop_reason_and_preserves_exception(self):
        from module.config.config_generated import GeneratedConfig
        from tasks.equipment_reroll.equipment_reroll import EquipmentReroll
        from tests.equipment_reroll.test_rules import snapshot

        before = snapshot()
        wrong_payment = replace(before, points=950)
        clock = ReplayClock()
        self.device = ReplayDevice([before, before, wrong_payment, wrong_payment], clock)
        task = EquipmentReroll(GeneratedConfig(), self.device)
        task._is_ready = lambda: True
        task.read_snapshot = lambda: self.device.image
        task.appear = lambda button, **kwargs: False
        task.handle_network_error = lambda: False
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch(
            "tasks.equipment_reroll.equipment_reroll.logger.critical",
        ) as error, self.assertRaises(RequestHumanTakeover) as raised:
            task.run()
        error.assert_called_once()
        self.assertIn("Refresh payment does not match the expected cost", error.call_args.args[0])
        self.assertEqual(raised.exception.args, ())
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])

    def test_valid_speed_five_startup_runs_real_no_gold_frames_to_budget_stop(self):
        from module.config.config_generated import GeneratedConfig
        from tasks.equipment_reroll.equipment_reroll import EquipmentReroll

        config = GeneratedConfig()
        config.EquipmentReroll_MaxPoints = 19
        self.sample_id = "critical_damage"
        image = load_sample(self.sample_id)
        clock = ReplayClock()
        self.device = ReplayDevice([image, image], clock)
        task = EquipmentReroll(config, self.device)
        task.handle_network_error = lambda: False
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch(
            "tasks.equipment_reroll.equipment_reroll.logger.critical",
        ) as error:
            self.assertTrue(task.run())
        error.assert_not_called()
        self.assertIsNotNone(task._recorded_current)
        self.assertEqual(self.device.actions, [])

    def test_real_device_construction_is_forbidden(self):
        from module.device.device import Device

        with self.assertRaisesRegex(AssertionError, "真实设备"):
            Device(config=None)

    def test_real_account_config_is_forbidden(self):
        from module.config.config import AzurLaneConfig

        with self.assertRaisesRegex(AssertionError, "账号配置"):
            AzurLaneConfig("offline_must_not_read")

    def test_unknown_case_is_a_preparation_error(self):
        args = SimpleNamespace(suite="equipment_reroll", list=True, case="unknown_case")
        self.assertEqual(run_offline_suite(args), 2)

    def test_missing_sample_is_not_skipped(self):
        with self.assertRaises(FileNotFoundError), patch("pathlib.Path.read_bytes", side_effect=FileNotFoundError):
            load_sample("initial")

    def test_corrupt_sample_is_not_skipped(self):
        with self.assertRaisesRegex(ValueError, "校验失败"), patch("pathlib.Path.read_bytes", return_value=b"corrupt"):
            load_sample("initial")

    def test_missing_numeric_model_fails_before_running_tests(self):
        from dev_tools.run_offline_tests import run
        from module.ocr.models import OCR_MODEL

        args = SimpleNamespace(suite="equipment_reroll", list=False, out=None,
                               case="tests.equipment_reroll.test_rules.RuleTests.test_no_lock_before_speed_five")
        with patch.object(OCR_MODEL, "get_by_lang", side_effect=[object(), FileNotFoundError("英文数值模型缺失")]) as get_model, patch(
            "unittest.TextTestRunner.run", side_effect=AssertionError("模型缺失时不能开始测试")
        ), patch("dev_tools.run_offline_tests.equipment_reroll_fixtures") as fixtures:
            self.assertEqual(run(args), 2)
        fixtures.assert_not_called()
        self.assertEqual([call.args[0] for call in get_model.call_args_list], ["cn", "en"])
