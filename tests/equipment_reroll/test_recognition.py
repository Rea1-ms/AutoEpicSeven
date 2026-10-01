import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tasks.equipment_reroll.equipment_reroll import EquipmentReroll, PendingAction
from module.exception import RequestHumanTakeover
from tasks.equipment_reroll.assets.assets_equipment_reroll import EQUIPMENT_REROLL_REPLACE_CHECK, OCR_EQUIPMENT_REROLL_APPLIED
from tasks.equipment_reroll.rules import RefreshBudget, Substat
from tests.equipment_reroll.test_rules import policy, snapshot
from tests.support.equipment_reroll import FIXTURES, ReplayClock, ReplayDevice, load_sample


APPLIED_STATS = (Substat("FlatAttack", 38), Substat("Speed", 4),
                 Substat("AttackPercent", 6), Substat("FlatDefense", 33))


class RecognitionTests(unittest.TestCase):
    def test_screenshots(self):
        expected = {
            "initial": (90373, (False, False, False, False),
                        (("HealthPercent", 6), ("AttackPercent", 8), ("DefensePercent", 6), ("FlatDefense", 33))),
            "lock_speed": (90353, (False, False, False, True),
                           (("CriticalChance", 3), ("AttackPercent", 6), ("FlatAttack", 37), ("HealthPercent", 7))),
            "one_lock": (90293, (False, False, False, True),
                         (("DefensePercent", 4), ("FlatAttack", 36), ("CriticalChance", 3), ("Speed", 3))),
            "two_locks": (90293, (True, False, False, True),
                          (("DefensePercent", 4), ("FlatAttack", 36), ("CriticalChance", 3), ("Speed", 3))),
            "two_lock_roll": (90143, (True, False, False, True),
                              (("Resistance", 7), ("CriticalChance", 3), ("HealthPercent", 8), ("Speed", 3))),
            "unlocked": (90143, (False, False, False, False),
                         (("Resistance", 7), ("CriticalChance", 3), ("HealthPercent", 8), ("Speed", 3))),
            "speed_four": (90123, (False, False, False, False),
                           (("FlatAttack", 38), ("Speed", 4), ("AttackPercent", 6), ("FlatDefense", 33))),
        }
        for sample_id, (points, locks, candidate) in expected.items():
            with self.subTest(sample_id=sample_id):
                self.sample_id = sample_id
                self.device = ReplayDevice([load_sample(sample_id)])
                task = EquipmentReroll(SimpleNamespace(), self.device)
                self.assertTrue(task._is_ready())
                observed = task.read_snapshot()
                self.assertEqual(observed.points, points)
                self.assertEqual(observed.locked, locks)
                self.assertEqual(observed.current, (Substat("Resistance", 7), Substat("FlatAttack", 39),
                                                    Substat("CriticalChance", 3), Substat("Speed", 3)))
                self.assertEqual(observed.candidate, tuple(Substat(*s) for s in candidate))

    def test_overlay_and_blank_screen_do_not_enable_actions(self):
        for sample_id in ("initial", "replace_applied"):
            with self.subTest(sample_id=sample_id):
                self.sample_id = sample_id
                self.device = ReplayDevice([load_sample(sample_id)])
                task = EquipmentReroll(SimpleNamespace(), self.device)
                self.device.image = (self.device.image * 0.5).astype("uint8")
                self.assertFalse(task._is_ready())
                self.device.image[:] = 0
                self.assertFalse(task._is_ready())

    def test_replacement_dialog_reads_candidate_and_blocks_refresh(self):
        self.sample_id = "replace_confirm"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        self.assertFalse(task._is_ready())
        self.assertTrue(task.appear(EQUIPMENT_REROLL_REPLACE_CHECK))
        self.assertEqual(task.read_replacement(), APPLIED_STATS)

    def test_applied_single_column_reads_stats_points_and_locks(self):
        self.sample_id = "replace_applied"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        self.assertTrue(task._is_ready())
        observed = task.read_snapshot()
        self.assertEqual(observed.current, APPLIED_STATS)
        self.assertEqual(observed.candidate, ())
        self.assertEqual((observed.points, observed.cost), (91483, 20))
        self.assertEqual(observed.locked, (False,) * 4)
        self.assertEqual(observed.main, Substat("Effectiveness", 12))

    def test_speed_four_current_no_longer_stalls(self):
        self.sample_id = "speed_four_current"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        self.assertTrue(task._is_ready())
        observed = task.read_snapshot()
        self.assertEqual(observed.current, APPLIED_STATS)
        self.assertEqual(observed.candidate, (Substat("AttackPercent", 7), Substat("DefensePercent", 8),
                                              Substat("CriticalChance", 5), Substat("HealthPercent", 5)))
        self.assertEqual((observed.points, observed.cost, observed.locked), (91463, 20, (False,) * 4))

    def test_health_percent_four_dialog_applies_only_matching_candidate(self):
        expected = (Substat("HealthPercent", 4), Substat("Speed", 4),
                    Substat("FlatHealth", 185), Substat("Resistance", 4))
        self.sample_id = "replace_health_percent_four"
        for matching in (True, False):
            with self.subTest(matching=matching):
                self.device = ReplayDevice([load_sample(self.sample_id)])
                task = EquipmentReroll(SimpleNamespace(), self.device)
                self.assertFalse(task._is_ready())
                self.assertEqual(task.read_replacement(), expected)
                before = snapshot(candidate=expected if matching else APPLIED_STATS)
                pending = PendingAction("replace", before, started=0, last_click=0)
                with patch("tasks.equipment_reroll.equipment_reroll.monotonic", return_value=2):
                    if matching:
                        self.assertTrue(task.handle_replace_confirm(pending))
                        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REPLACE_CONFIRM"])
                    else:
                        with self.assertRaisesRegex(RequestHumanTakeover, "不一致"):
                            task.handle_replace_confirm(pending)
                        self.assertEqual(self.device.actions, [])

    def test_dialog_does_not_reread_fixed_main(self):
        self.sample_id = "replace_confirm"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        with patch("tasks.equipment_reroll.equipment_reroll.Ocr",
                   side_effect=AssertionError("弹窗不应重复识别固定主属性")):
            self.assertEqual(task.read_replacement(), APPLIED_STATS)

    def test_contrast_verification_requires_agreement_and_original_threshold(self):
        self.sample_id = "replace_applied"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        fields = list(task._row_fields(OCR_EQUIPMENT_REROLL_APPLIED))
        names = [(s, 0.99) for s in ("攻击力", "速度", "攻击力", "防御力")]
        numbers = [(s, 0.99) for s in ("38", "6%", "33")]
        for text, confidence in (("5", 0.99), ("4", 0.79)):
            with self.subTest(text=text, confidence=confidence), patch(
                "tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                side_effect=[names, numbers, [("4", 0.61)], [(text, confidence)]],
            ), self.assertRaises(ValueError):
                task._read_substats(fields)
        with patch("tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                   side_effect=[names, numbers, [("4", 0.61)], [("4", 0.91)]]):
            self.assertEqual(task._read_substats(fields), APPLIED_STATS)

    def test_real_applied_view_refreshes_into_previous_failure_screen(self):
        self.sample_id = "speed_four_current"
        before, after = load_sample("replace_applied"), load_sample(self.sample_id)
        clock = ReplayClock()
        self.device = ReplayDevice([before, before, after, after], clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        budget = RefreshBudget(1, 0, 0)
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock):
            reason = task.execute(policy(), budget)
        self.assertEqual(reason, "达到最大刷新次数")
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual((budget.refreshes, budget.spent), (1, 20))

    def test_screenshot_replacement_flow_confirms_applied_stats(self):
        # The initiating frame is synthetic; the modal and applied view are the
        # user's real images. No screenshot of the initiating click was supplied.
        before = snapshot(candidate=APPLIED_STATS, points=91483)
        popup = load_sample("replace_confirm")
        applied = load_sample("replace_applied")
        clock = ReplayClock()
        self.device = ReplayDevice([before, before, popup, popup, applied, applied], clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        original_ready, original_read, original_appear = task._is_ready, task.read_snapshot, task.appear
        task._is_ready = lambda: isinstance(self.device.image, type(before)) or original_ready()
        task.read_snapshot = lambda: (self.device.image if isinstance(self.device.image, type(before))
                                      else original_read())
        task.appear = lambda button, **kwargs: (False if isinstance(self.device.image, type(before))
                                               else original_appear(button, **kwargs))
        task.handle_network_error = lambda: False
        budget = RefreshBudget(0, 19, 0)
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock):
            reason = task.execute(policy(), budget)
        self.assertEqual(reason, "达到点数预算")
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REPLACE", "EQUIPMENT_REROLL_REPLACE_CONFIRM"])
        self.assertEqual((budget.refreshes, budget.spent), (0, 0))

    def test_manifest_covers_the_selected_samples(self):
        data = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["samples"]), 11)
        self.assertEqual(len({s["id"] for s in data["samples"]}), 11)
        for sample in data["samples"]:
            self.assertEqual(load_sample(sample["id"]).shape, (720, 1280, 3))
