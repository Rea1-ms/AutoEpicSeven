import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.base.utils import crop
from tasks.equipment_reroll.equipment_reroll import EquipmentReroll, PendingAction
from module.exception import RequestHumanTakeover
from tasks.equipment_reroll.assets.assets_equipment_reroll import (
    EQUIPMENT_REROLL_REPLACE_CHECK, OCR_EQUIPMENT_REROLL_APPLIED,
    OCR_EQUIPMENT_REROLL_CANDIDATE, OCR_EQUIPMENT_REROLL_CURRENT,
    OCR_EQUIPMENT_REROLL_REPLACE_STATS,
    EQUIPMENT_REROLL_ROLL_NORMAL,
)
from tasks.equipment_reroll.recognition import (
    GoldMarkerDetector, PointBalanceEstimate, RecognitionCache, RefreshCostTemplates, StatNameTemplates, point_signature,
)
from tasks.equipment_reroll.rules import RefreshBudget, RejectedCandidate, RerollPolicy, Substat, Target
from tests.equipment_reroll.test_rules import policy, snapshot
from tests.support.equipment_reroll import FIXTURES, ReplayClock, ReplayDevice, load_sample, with_point_balance


APPLIED_STATS = (Substat("FlatAttack", 38), Substat("Speed", 4),
                 Substat("AttackPercent", 6), Substat("FlatDefense", 33))
LOCKED_SPEED_FIVE_STATS = (Substat("HealthPercent", 8), Substat("AttackPercent", 5),
                           Substat("Speed", 5), Substat("DefensePercent", 8))


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

    def test_critical_damage_screenshot_reads_full_result_and_priority(self):
        self.sample_id = "critical_damage"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        self.assertTrue(task._is_ready())
        full = task.read_snapshot()
        self.assertEqual(full.current, (Substat("FlatDefense", 32), Substat("Speed", 4),
                                        Substat("FlatAttack", 40), Substat("DefensePercent", 8)))
        self.assertEqual(full.candidate, (Substat("HealthPercent", 4), Substat("Resistance", 4),
                                          Substat("CriticalChance", 3), Substat("CriticalDamage", 4)))
        self.assertEqual((full.points, full.cost, full.locked), (87953, 20, (False,) * 4))
        selected = RerollPolicy((Target("CriticalDamage", 7), Target("Resistance", 8),
                                 Target("HealthPercent", 8), Target("DefensePercent", 8)))
        task._recognition_policy = selected
        staged = task.read_snapshot()
        self.assertIsInstance(staged.candidate, RejectedCandidate)
        self.assertFalse(selected.should_replace(staged))
        self.assertTrue(all(row.name is None for row in staged.candidate.rows))

    def test_critical_damage_source_template_avoids_ocr_and_rejects_other_labels(self):
        self.sample_id = "critical_damage"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        field = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))[6]
        with patch("tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                   side_effect=AssertionError("爆伤名称已有图片素材，不应调用文字识别")):
            names = task._read_substat_fields([field], threshold=128, scale=2,
                                             name="CriticalDamageTemplate", labels=True)
        self.assertEqual(names[0][0], "暴击伤害")
        template = StatNameTemplates()
        fields = [*task._row_fields(OCR_EQUIPMENT_REROLL_CURRENT), *task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE)]
        for other in fields[::2]:
            if other.name == field.name:
                continue
            with self.subTest(name=other.name):
                observed = template.match(crop(self.device.image, other.area), 2)
                if other.name == "OCR_EQUIPMENT_REROLL_CURRENT_NAME_1":
                    self.assertIsNotNone(observed)
                    self.assertEqual(observed[0], "速度")
                    self.assertGreaterEqual(observed[1], 0.98)
                else:
                    self.assertIsNone(observed)
        glyph = crop(self.device.image, field.area)
        self.assertIsNone(template.match(glyph, 3))
        self.assertIsNone(template.match(np.zeros_like(glyph), 2))

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
                        with patch("tasks.equipment_reroll.equipment_reroll.logger.critical") as critical, \
                                self.assertRaises(RequestHumanTakeover) as raised:
                            task.handle_replace_confirm(pending)
                        critical.assert_called_once()
                        self.assertIn("dialog substats do not match the selected candidate", critical.call_args.args[0])
                        self.assertEqual(raised.exception.args, ())
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
            task = EquipmentReroll(SimpleNamespace(), self.device)
            with self.subTest(text=text, confidence=confidence), patch(
                "tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                side_effect=[names, numbers, [("4", 0.61)], [(text, confidence)]],
            ), patch.object(StatNameTemplates, "match", return_value=None), self.assertRaises(ValueError):
                task._read_substats(fields)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        with patch("tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                   side_effect=[names, numbers, [("4", 0.61)], [("4", 0.91)]]), patch.object(
            StatNameTemplates, "match", return_value=None,
        ):
            self.assertEqual(task._read_substats(fields), APPLIED_STATS)

    def test_selection_ignores_unreadable_fixed_main_stat(self):
        for sample_id, area in (("speed_four_current", (206, 134, 466, 169)),
                                ("replace_applied", (386, 167, 646, 203))):
            with self.subTest(sample_id=sample_id):
                self.sample_id = sample_id
                image = load_sample(sample_id)
                image[area[1]:area[3], area[0]:area[2]] = 0
                self.device = ReplayDevice([image])
                observed = EquipmentReroll(SimpleNamespace(), self.device).read_snapshot()
                self.assertEqual(observed.current, APPLIED_STATS)

    def test_verified_name_templates_recognize_full_labels_across_real_rows(self):
        templates = StatNameTemplates()
        examples = []
        for sample_id, asset, step, scale in (
            ("initial", OCR_EQUIPMENT_REROLL_CURRENT, 47, 2),
            ("initial", OCR_EQUIPMENT_REROLL_CANDIDATE, 47, 2),
            ("speed_four_current", OCR_EQUIPMENT_REROLL_CURRENT, 47, 2),
            ("speed_four_current", OCR_EQUIPMENT_REROLL_CANDIDATE, 47, 2),
            ("replace_applied", OCR_EQUIPMENT_REROLL_APPLIED, 47, 2),
            ("replace_confirm", OCR_EQUIPMENT_REROLL_REPLACE_STATS, 42, 3),
            ("replace_health_percent_four", OCR_EQUIPMENT_REROLL_REPLACE_STATS, 42, 3),
        ):
            self.sample_id = sample_id
            self.device = ReplayDevice([load_sample(sample_id)])
            task = EquipmentReroll(SimpleNamespace(), self.device)
            fields = list(task._row_fields(asset, row_step=step))[::2]
            names = task._read_substat_fields(fields, threshold=255 if sample_id == "replace_applied" else 128,
                                             scale=scale, name="TemplateVerification", labels=True)
            for field, (name, _) in zip(fields, names):
                image = crop(self.device.image, field.area)
                observed = templates.match(image, scale)
                if observed is not None:
                    self.assertEqual(observed[0], name)
                templates.remember(image, scale, name)
                examples.append((image, scale, name))
        for image, scale, name in examples:
            with self.subTest(name=name, scale=scale):
                observed = templates.match(image, scale)
                self.assertIsNotNone(observed)
                self.assertEqual(observed[0], name)
        self.assertIsNone(templates.match(np.zeros_like(examples[0][0]), 2))

    def test_no_speed_in_candidate_skips_all_candidate_numbers(self):
        self.sample_id = "speed_four_current"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task._recognition_policy = policy()
        original = task._read_substat_fields
        numeric_fields = []

        def read(fields, **kwargs):
            if not kwargs.get("labels"):
                numeric_fields.extend(field.name for field in fields)
            return original(fields, **kwargs)

        with patch.object(task, "_read_substat_fields", side_effect=read):
            observed = task.read_snapshot()
        self.assertIsInstance(observed.candidate, RejectedCandidate)
        self.assertFalse(policy().should_replace(observed))
        self.assertEqual(len(numeric_fields), 4)
        self.assertTrue(all("CURRENT" in name for name in numeric_fields))

    def test_achieved_speed_skips_its_gold_number_but_reads_locked_rows(self):
        self.sample_id = "speed_four"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        fields = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))
        current = (Substat("FlatAttack", 38), Substat("Speed", 5),
                   Substat("AttackPercent", 8), Substat("FlatDefense", 33))
        original = task._read_substat_fields
        numeric_rows = []

        def read(selected, **kwargs):
            if not kwargs.get("labels"):
                numeric_rows.extend(field.name.rsplit("_", 1)[1] for field in selected)
            return original(selected, **kwargs)

        with patch.object(task, "_read_substat_fields", side_effect=read):
            candidate = task._read_candidate(fields, current, (False,) * 4, policy())
        self.assertIsInstance(candidate, RejectedCandidate)
        self.assertEqual(numeric_rows, [])
        numeric_rows.clear()
        with patch.object(task, "_read_substat_fields", side_effect=read):
            protected = task._read_candidate(fields, current, (False, False, False, True), policy())
        self.assertEqual(numeric_rows, ["3"])
        self.assertEqual(protected[3], Substat("FlatDefense", 33))

    def test_better_speed_reads_every_row_before_replacement(self):
        self.sample_id = "speed_four"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task._recognition_policy = policy()
        observed = task.read_snapshot()
        self.assertEqual(observed.candidate, APPLIED_STATS)
        self.assertTrue(policy().should_replace(observed))
        self.assertEqual(policy().desired_locks(observed.candidate), (False,) * 4)

    def test_skipped_numbers_still_participate_in_frame_stability(self):
        self.sample_id = "speed_four_current"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task._recognition_policy = policy()
        before = task.read_snapshot()
        field = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))[1]
        left, top, _, _ = field.area
        self.device.image[top + 5, left + 5] ^= 255
        after = task.read_snapshot()
        self.assertEqual(before.candidate.rows, after.candidate.rows)
        self.assertNotEqual(before, after)

    def test_numeric_and_resource_caches_require_identical_images(self):
        self.sample_id = "speed_four_current"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        first = task.read_snapshot()
        with patch("tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                   side_effect=AssertionError("相同图片不应重复识别副属性")), patch(
            "tasks.equipment_reroll.equipment_reroll.Ocr.ocr_multi_lines",
            side_effect=AssertionError("相同图片不应重复识别点数"),
        ):
            self.assertEqual(task.read_snapshot(), first)
        task._resource_cache = RecognitionCache(limit=8)
        field = list(task._row_fields(OCR_EQUIPMENT_REROLL_CURRENT))[3]
        left, top, _, _ = field.area
        self.device.image[top + 5, left + 5] ^= 255
        with patch("tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                   side_effect=ValueError("变化的数字必须重读")), self.assertRaisesRegex(ValueError, "重读"):
            task.read_snapshot()

    def test_unseen_name_requires_ocr_before_rejecting_a_candidate(self):
        self.sample_id = "speed_four_current"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        fields = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))
        selected = RerollPolicy((Target("DefensePercent", 8), Target("HealthPercent", 8),
                                 Target("CriticalChance", 5), Target("Resistance", 8)))
        with patch("tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                   side_effect=ValueError("陌生名称尚未确认")), self.assertRaisesRegex(ValueError, "名称尚未确认"):
            task._read_candidate(fields, APPLIED_STATS, (False,) * 4, selected)

    def test_gold_filter_respects_phase_order_and_complete_replacement_reading(self):
        policies = (
            policy(),
            RerollPolicy((Target("AttackPercent", 8), Target("DefensePercent", 8),
                          Target("HealthPercent", 8), Target("CriticalChance", 5))),
            RerollPolicy((Target("FlatDefense", 34), Target("FlatAttack", 44),
                          Target("HealthPercent", 8), Target("Resistance", 8))),
            RerollPolicy((Target("HealthPercent", 8), Target("FlatHealth", 201),
                          Target("Effectiveness", 8), Target("CriticalDamage", 7))),
        )
        for sample_id in ("initial", "lock_speed", "one_lock", "two_locks", "two_lock_roll",
                          "unlocked", "speed_four", "speed_four_current"):
            self.sample_id = sample_id
            self.device = ReplayDevice([load_sample(sample_id)])
            task = EquipmentReroll(SimpleNamespace(), self.device)
            full = task.read_snapshot()
            fields = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))
            gold = GoldMarkerDetector().rows(self.device.image, task.ROW_STEP)
            for selected_policy in policies:
                with self.subTest(sample_id=sample_id, targets=selected_policy.targets):
                    staged = task._read_candidate(fields, full.current, full.locked, selected_policy)
                    current_values = {stat.kind: stat.value for stat in full.current}
                    target = next((t for t in selected_policy.targets
                                   if current_values.get(t.kind, 0) < t.value), None)
                    improves = target is not None and any(
                        marked and stat.kind == target.kind
                        and stat.value > current_values.get(target.kind, 0)
                        for marked, stat in zip(gold, full.candidate)
                    )
                    if improves:
                        self.assertEqual(staged, full.candidate)
                        self.assertEqual(selected_policy.should_replace(full),
                                         selected_policy.should_replace(replace(full, candidate=staged)))
                    else:
                        self.assertIsInstance(staged, RejectedCandidate)
                        self.assertFalse(selected_policy.should_replace(replace(full, candidate=staged)))
                    if isinstance(staged, RejectedCandidate):
                        for row, locked in enumerate(full.locked):
                            if locked:
                                self.assertEqual(staged[row], full.candidate[row])

    def test_gold_markers_match_all_real_comparison_rows(self):
        expected = {
            "initial": (False, True, False, False),
            "lock_speed": (False,) * 4, "one_lock": (False,) * 4, "two_locks": (False,) * 4,
            "two_lock_roll": (False, False, True, False), "unlocked": (False, False, True, False),
            "speed_four": (False, True, False, False),
            "speed_four_current": (False, True, True, False), "critical_damage": (False,) * 4,
        }
        detector = GoldMarkerDetector()
        for sample_id, gold in expected.items():
            with self.subTest(sample_id=sample_id):
                self.sample_id = sample_id
                self.device = ReplayDevice([load_sample(sample_id)])
                self.assertEqual(detector.rows(self.device.image, 47), gold)

    def test_no_gold_skips_candidate_ocr_and_keeps_budget_confirmation(self):
        self.sample_id = "critical_damage"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        full = task.read_snapshot()
        task._recorded_current = full.current
        task._recognition_policy = policy()
        with patch("tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                   side_effect=AssertionError("无金色且已记录当前属性，不应调用副属性识别")):
            observed = task.read_snapshot()
            self.assertEqual(task.read_snapshot(), observed)
        self.assertIsInstance(observed.candidate, RejectedCandidate)
        self.assertTrue(all(row.name is None for row in observed.candidate.rows))
        budget = RefreshBudget(0, 0, 0)
        self.assertFalse(budget.confirm_refresh(observed, observed))
        self.assertTrue(budget.confirm_refresh(observed, replace(observed, points=observed.points - 20)))
        self.assertEqual((budget.refreshes, budget.spent), (1, 20))

    def test_no_gold_reads_only_protected_rows(self):
        self.sample_id = "one_lock"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task._recognition_policy = policy()
        full = EquipmentReroll(SimpleNamespace(), self.device).read_snapshot()
        task._recorded_current = full.current
        original = task._read_substat_fields
        read_fields = []

        def read(fields, **kwargs):
            read_fields.extend(field.name for field in fields)
            return original(fields, **kwargs)

        with patch.object(task, "_read_substat_fields", side_effect=read):
            observed = task.read_snapshot()
        self.assertEqual(read_fields, [])
        self.assertEqual(observed.candidate[3], full.current[3])
        self.assertTrue(RefreshBudget(0, 0, 0).confirm_refresh(
            observed, replace(observed, points=observed.points - observed.cost),
        ))

    def test_unrelated_gold_reads_only_gold_names(self):
        self.sample_id = "speed_four_current"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        fields = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))
        original = task._read_substat_fields
        read_fields = []

        def read(selected, **kwargs):
            read_fields.extend(field.name for field in selected)
            return original(selected, **kwargs)

        with patch.object(task, "_read_substat_fields", side_effect=read):
            candidate = task._read_candidate(fields, APPLIED_STATS, (False,) * 4, policy())
        self.assertIsInstance(candidate, RejectedCandidate)
        self.assertEqual(read_fields, ["OCR_EQUIPMENT_REROLL_CANDIDATE_NAME_1",
                                       "OCR_EQUIPMENT_REROLL_CANDIDATE_NAME_2"])

    def test_unrecognized_icon_and_incomplete_text_do_not_count_as_no_gold(self):
        self.sample_id = "critical_damage"
        original = load_sample(self.sample_id)
        for area in (EQUIPMENT_REROLL_ROLL_NORMAL.area, (774, 197, 816, 220), (571, 197, 661, 221)):
            with self.subTest(area=area):
                image = original.copy()
                left, top, right, bottom = area
                image[top:bottom, left:right] = 0
                self.device = ReplayDevice([image])
                task = EquipmentReroll(SimpleNamespace(), self.device)
                fields = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))
                with self.assertRaises(ValueError):
                    task._read_candidate(fields, APPLIED_STATS, (False,) * 4, policy())
                self.assertEqual(self.device.actions, [])

    def test_dark_background_changes_do_not_restart_no_gold_stability(self):
        self.sample_id = "critical_damage"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task._recognition_policy = policy()
        before = task.read_snapshot()
        task._recorded_current = before.current
        field = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))[1]
        left, top, right, bottom = field.area
        region = self.device.image[top:bottom, left:right]
        region[(region.max(axis=2) < 70)] = (35, 30, 40)
        after = task.read_snapshot()
        self.assertEqual(before, after)

    def test_no_gold_real_frames_refresh_once_and_confirm_delayed_payment(self):
        self.sample_id = "critical_damage"
        before = load_sample(self.sample_id)
        after = before.copy()
        # Only the balance text is supplied synthetically; all candidate glyphs
        # and icons remain the user's real no-gold comparison screen.
        clock = ReplayClock()
        self.device = ReplayDevice([before, before, before, after, after], clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        points = iter([87953, 87953, 87953, 87933, 87933])
        budget = RefreshBudget(1, 0, 0)
        with patch.object(task, "_read_resources", side_effect=lambda locked: (next(points), 20)), patch(
            "tasks.equipment_reroll.equipment_reroll.monotonic", clock,
        ):
            reason = task.execute(policy(), budget)
        self.assertEqual(reason, "Refresh limit reached")
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual((budget.refreshes, budget.spent), (1, 20))

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
        self.assertEqual(reason, "Refresh limit reached")
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual((budget.refreshes, budget.spent), (1, 20))

    def test_confirmed_current_record_survives_layout_and_lighting_changes(self):
        self.sample_id = "speed_four_current"
        before, after = load_sample("replace_applied"), load_sample(self.sample_id)
        # Refresh changes the current column's position and text brightness,
        # but not its substats. Neither change should invoke current OCR again.
        after[194:363, 201:466] = (after[194:363, 201:466] * 0.85).astype("uint8")
        clock = ReplayClock()
        self.device = ReplayDevice([before, before, after, after], clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        original_read = task._read_substats

        def read(fields, **kwargs):
            if self.device.actions:
                raise AssertionError("刷新后不能重复识别已记录的当前装备")
            return original_read(fields, **kwargs)

        budget = RefreshBudget(1, 0, 0)
        with patch.object(task, "_read_substats", side_effect=read), patch(
            "tasks.equipment_reroll.equipment_reroll.monotonic", clock,
        ):
            reason = task.execute(policy(), budget)
        self.assertEqual(reason, "Refresh limit reached")
        self.assertEqual(task._recorded_current, APPLIED_STATS)
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
        self.assertEqual(reason, "Point budget reached")
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REPLACE", "EQUIPMENT_REROLL_REPLACE_CONFIRM"])
        self.assertEqual((budget.refreshes, budget.spent), (0, 0))
        self.assertEqual(task._recorded_current, APPLIED_STATS)

    def test_replace_click_discards_the_old_record_before_confirmation(self):
        self.sample_id = "replace_applied"
        self.device = ReplayDevice([load_sample(self.sample_id)])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        before = snapshot(candidate=APPLIED_STATS)
        task._recorded_current = before.current
        pending = task._click("replace", before)
        self.assertIsNone(task._recorded_current)
        self.assertEqual(pending.before.current, before.current)
        actual = task.read_snapshot()
        self.assertEqual(actual.current, APPLIED_STATS)
        self.assertNotEqual(actual.current, before.current)

    def test_running_tool_reads_real_speed_five_locks_and_followup_candidates(self):
        current_four = (Substat("HealthPercent", 8), Substat("DefensePercent", 8),
                        Substat("FlatHealth", 165), Substat("Speed", 4))
        expected = {
            "speed_four_percent_health": (current_four, 81913, (False,) * 4, (False,) * 4,
                                           (("CriticalDamage", 6), ("DefensePercent", 5),
                                            ("FlatDefense", 30), ("Resistance", 6))),
            "speed_four_unlocked_roll": (current_four, 81873, (False,) * 4, (False,) * 4,
                                          (("FlatDefense", 31), ("CriticalChance", 3),
                                           ("FlatHealth", 174), ("CriticalDamage", 5))),
            "speed_five_two_locks": (LOCKED_SPEED_FIVE_STATS, 60193, (False, False, True, True), (False,) * 4,
                                     (("FlatHealth", 173), ("CriticalDamage", 5),
                                      ("Speed", 5), ("DefensePercent", 8))),
            "speed_five_locked_roll": (LOCKED_SPEED_FIVE_STATS, 60043, (False, False, True, True),
                                       (True, True, False, False),
                                       (("HealthPercent", 8), ("CriticalChance", 5),
                                        ("Speed", 5), ("DefensePercent", 8))),
        }
        for sample_id, (current, points, locks, gold, candidate) in expected.items():
            with self.subTest(sample_id=sample_id):
                self.sample_id = sample_id
                self.device = ReplayDevice([load_sample(sample_id)])
                task = EquipmentReroll(SimpleNamespace(), self.device)
                # These screenshots were taken during an ongoing invocation.
                # Its confirmed current roll is already recorded; do not claim
                # this validates cold startup OCR on the grey locked column.
                task._recorded_current = current
                observed = task.read_snapshot()
                self.assertEqual(observed.current, current)
                self.assertEqual(observed.candidate, tuple(Substat(*stat) for stat in candidate))
                self.assertEqual((observed.points, observed.cost, observed.locked),
                                 (points, 150 if sum(locks) == 2 else 20, locks))
                self.assertEqual(GoldMarkerDetector().rows(self.device.image, 47), gold)
                self.assertTrue(task._is_ready())
                if sample_id.startswith("speed_five_"):
                    # Read the actual left-side digit too, independently of the
                    # supplied record and of the candidate's protected row.
                    value_field = list(task._row_fields(OCR_EQUIPMENT_REROLL_CURRENT))[5]
                    number = task._read_substat_fields([value_field], threshold=128, scale=2,
                                                       name="EquipmentRerollValues")
                    self.assertEqual(number[0][0].strip(), "5")
                    self.assertGreaterEqual(number[0][1], 0.8)
                    self.assertEqual(policy().desired_locks(observed.current), locks)

    def test_cold_start_reads_real_speed_five_current_without_record(self):
        for sample_id in ("speed_five_two_locks", "speed_five_locked_roll"):
            with self.subTest(sample_id=sample_id):
                self.sample_id = sample_id
                image = load_sample(sample_id)
                clock = ReplayClock()
                self.device = ReplayDevice([image, image], clock)
                task = EquipmentReroll(SimpleNamespace(), self.device)
                task.handle_network_error = lambda: False
                budget = RefreshBudget(0, 149, 0)
                with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock):
                    reason = task.execute(policy(), budget)
                self.assertEqual(reason, "Point budget reached")
                self.assertEqual(task._recorded_current, LOCKED_SPEED_FIVE_STATS)
                self.assertEqual(self.device.actions, [])
                self.assertEqual((budget.refreshes, budget.spent), (0, 0))

    def test_speed_source_template_matches_real_names_and_rejects_other_labels(self):
        # Exercise different values, columns and grey/white text. The source
        # template contains only the label, never the speed digit or lock.
        examples = (("initial", OCR_EQUIPMENT_REROLL_CURRENT, 3),
                    ("speed_four_current", OCR_EQUIPMENT_REROLL_CURRENT, 1),
                    ("speed_four", OCR_EQUIPMENT_REROLL_CANDIDATE, 1),
                    ("speed_five_two_locks", OCR_EQUIPMENT_REROLL_CURRENT, 2),
                    ("speed_five_locked_roll", OCR_EQUIPMENT_REROLL_CANDIDATE, 2))
        for sample_id, asset, speed_row in examples:
            with self.subTest(sample_id=sample_id, column=asset.name):
                self.sample_id = sample_id
                self.device = ReplayDevice([load_sample(sample_id)])
                task = EquipmentReroll(SimpleNamespace(), self.device)
                fields = list(task._row_fields(asset))[::2]
                with patch("tasks.equipment_reroll.equipment_reroll.SubstatOcr.ocr_multi_lines",
                           side_effect=AssertionError("Verified speed label must use its source template")) as ocr:
                    observed = task._read_substat_fields([fields[speed_row]], threshold=128, scale=2,
                                                        name="EquipmentRerollNames", labels=True)
                ocr.assert_not_called()
                self.assertEqual(observed[0][0], "速度")
                self.assertGreaterEqual(observed[0][1], 0.98)
                templates = StatNameTemplates()
                for row, field in enumerate(fields):
                    if row == speed_row:
                        continue
                    result = templates.match(crop(self.device.image, field.area), 2)
                    self.assertTrue(result is None or result[0] != "速度")
                speed = crop(self.device.image, fields[speed_row].area)
                self.assertIsNone(templates.match(speed, 3))
                self.assertIsNone(templates.match(np.zeros_like(speed), 2))

    def test_real_cold_start_rejects_speed_lock_under_health_priority(self):
        self.sample_id = "speed_five_two_locks"
        image = load_sample(self.sample_id)
        clock = ReplayClock()
        self.device = ReplayDevice([image, image], clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        selected = RerollPolicy((Target("HealthPercent", 8), Target("DefensePercent", 8),
                                 Target("Resistance", 8), Target("Speed", 5)))
        budget = RefreshBudget(0, 0, 0)
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch(
            "tasks.equipment_reroll.equipment_reroll.logger.critical",
        ) as critical, self.assertRaises(RequestHumanTakeover) as raised:
            task.execute(selected, budget)
        critical.assert_called_once()
        self.assertIn("Initial locked substats conflict", critical.call_args.args[0])
        self.assertIn("row 3: Speed 5", critical.call_args.args[0])
        self.assertEqual(raised.exception.args, ())
        self.assertEqual(self.device.actions, [])
        self.assertEqual((budget.refreshes, budget.spent), (0, 0))

    def test_real_speed_five_paid_refresh_preserves_two_locks_and_ignores_unrelated_gold(self):
        self.sample_id = "speed_five_locked_roll"
        before_image = load_sample("speed_five_two_locks")
        after_image = load_sample(self.sample_id)
        clock = ReplayClock()
        # Cold initialization and both protected columns now use real images.
        # Repeated old frames after the paid click still model balance delay.
        self.device = ReplayDevice([before_image] * 4 + [after_image] * 2, clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        budget = RefreshBudget(1, 150, 0)
        original_fields = task._read_substat_fields
        original_stats = task._read_substats
        numeric_fields = []
        startup_protected_fields = []

        def read_stats(fields, **kwargs):
            if self.device.actions:
                raise AssertionError("Confirmed current roll must not be scanned again")
            return original_stats(fields, **kwargs)

        def read_fields(fields, **kwargs):
            if not kwargs.get("labels"):
                if self.device.actions:
                    numeric_fields.extend(field.name for field in fields)
                else:
                    startup_protected_fields.extend(field.name for field in fields if "CANDIDATE" in field.name)
            return original_fields(fields, **kwargs)

        selected = RerollPolicy((Target("Speed", 5), Target("DefensePercent", 8),
                                 Target("Resistance", 8), Target("HealthPercent", 8)))
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch.object(
            task, "_read_substats", side_effect=read_stats,
        ), patch.object(task, "_read_substat_fields", side_effect=read_fields), patch.object(
            task, "_read_points", wraps=task._read_points,
        ) as points:
            reason = task.execute(selected, budget)
        self.assertEqual(reason, "Refresh limit reached")
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual(self.device.action_times, [1])
        self.assertEqual((budget.refreshes, budget.spent), (1, 150))
        self.assertEqual(task._recorded_current, LOCKED_SPEED_FIVE_STATS)
        self.assertEqual(numeric_fields, [])
        self.assertEqual(set(startup_protected_fields), {"OCR_EQUIPMENT_REROLL_CANDIDATE_VALUE_2",
                                                       "OCR_EQUIPMENT_REROLL_CANDIDATE_VALUE_3"})
        self.assertEqual((self.device.click_history_clears, self.device.stuck_history_clears), (1, 1))
        self.assertEqual([call.kwargs for call in points.call_args_list], [{}, {}, {"fresh": True}])
        self.assertEqual(task._point_estimate.points, 60043)

    def test_recorded_gray_rows_skip_ocr_markers_and_glyph_stability(self):
        self.sample_id = "speed_five_two_locks"
        source = load_sample(self.sample_id)
        self.device = ReplayDevice([source.copy()])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task._recognition_policy = policy()
        # Independently read the startup data before establishing the record.
        startup = task.read_snapshot()
        self.assertEqual(startup.current, LOCKED_SPEED_FIVE_STATS)
        task._recorded_current = startup.current
        before = task.read_snapshot()
        fields = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))
        for row in (2, 3):
            for field in fields[row * 2:row * 2 + 2]:
                left, top, right, bottom = field.area
                self.device.image[top:bottom, left:right] = 0
            top = EQUIPMENT_REROLL_ROLL_NORMAL.area[1] + row * 47
            self.device.image[top:top + 23, 524:545] = 0
        with patch.object(task, "_read_substat_fields", side_effect=AssertionError("Recorded gray rows must not be read")):
            after = task.read_snapshot()
        self.assertEqual(after, before)
        self.assertEqual(after.candidate[2:], LOCKED_SPEED_FIVE_STATS[2:])
        # Unlocked glyphs still participate in the two-frame stability check.
        left, top, _, _ = fields[1].area
        self.device.image[top + 5, left + 5] ^= 255
        changed = task.read_snapshot()
        self.assertNotEqual(changed.candidate.image_signature, after.candidate.image_signature)
        task._recorded_current = None
        with self.assertRaisesRegex(ValueError, "marker color"):
            task.read_snapshot()

    def test_improved_candidate_reads_unlocked_rows_and_reuses_confirmed_locks(self):
        self.sample_id = "speed_five_locked_roll"
        self.device = ReplayDevice([load_sample("speed_five_two_locks")])
        task = EquipmentReroll(SimpleNamespace(), self.device)
        selected = RerollPolicy((Target("Speed", 5), Target("DefensePercent", 8),
                                 Target("CriticalChance", 5), Target("HealthPercent", 8)))
        task._recognition_policy = selected
        task._recorded_current = task.read_snapshot().current
        self.device.image = load_sample(self.sample_id)
        read_fields = []
        original = task._read_substat_fields

        def read(fields, **kwargs):
            read_fields.extend(field.name for field in fields)
            return original(fields, **kwargs)

        with patch.object(task, "_read_substat_fields", side_effect=read):
            observed = task.read_snapshot()
        self.assertEqual(observed.candidate, (Substat("HealthPercent", 8), Substat("CriticalChance", 5),
                                               Substat("Speed", 5), Substat("DefensePercent", 8)))
        self.assertTrue(selected.should_replace(observed))
        self.assertTrue(read_fields)
        self.assertTrue(all(name.endswith(("_0", "_1")) for name in read_fields))

    def test_startup_mismatched_protected_value_stops_before_reusing_record(self):
        self.sample_id = "speed_five_two_locks"
        image = load_sample(self.sample_id)
        task = EquipmentReroll(SimpleNamespace(), ReplayDevice([image]))
        fields = list(task._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))
        left, top, right, bottom = fields[5].area
        image[top:bottom, left:right] = crop(load_sample("speed_four"), fields[3].area)
        clock = ReplayClock()
        self.device = ReplayDevice([image, image], clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch(
            "tasks.equipment_reroll.equipment_reroll.logger.critical",
        ) as critical, self.assertRaises(RequestHumanTakeover):
            task.execute(policy(), RefreshBudget(0, 0, 0))
        critical.assert_called_once()
        self.assertIn("Initial locked candidate does not match current substat at row 3", critical.call_args.args[0])
        self.assertIsNone(task._recorded_current)
        self.assertEqual(self.device.actions, [])

    def test_real_gold_animation_never_enables_actions_or_substat_ocr(self):
        self.sample_id = "speed_five_gold_animation"
        image = load_sample(self.sample_id)
        clock = ReplayClock()
        # Repetition deliberately simulates an animation that never settles;
        # it must reach the existing timeout without paying or applying a roll.
        self.device = ReplayDevice([image] * 22, clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        self.assertFalse(task._is_ready())
        budget = RefreshBudget(1, 150, 0)
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch.object(
            task, "read_snapshot", side_effect=AssertionError("Animated overlay must not be treated as ready"),
        ) as read, patch("tasks.equipment_reroll.equipment_reroll.logger.critical") as critical, \
                self.assertRaises(RequestHumanTakeover):
            task.execute(policy(), budget)
        read.assert_not_called()
        critical.assert_called_once()
        self.assertIn("no stable workshop substat selection screen was recognized", critical.call_args.args[0])
        self.assertEqual(self.device.actions, [])
        self.assertEqual((budget.refreshes, budget.spent), (0, 0))

    def test_manifest_covers_the_selected_samples(self):
        data = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["samples"]), 17)
        self.assertEqual(len({s["id"] for s in data["samples"]}), 17)
        for sample in data["samples"]:
            self.assertEqual(load_sample(sample["id"]).shape, (720, 1280, 3))

    def test_price_templates_match_real_prices_and_reject_delayed_or_unknown_prices(self):
        matcher = RefreshCostTemplates()
        cases = {"initial": (False,) * 4, "one_lock": (False, False, False, True),
                 "two_locks": (True, False, False, True),
                 "speed_five_two_locks": (False, False, True, True),
                 "speed_five_locked_roll": (False, False, True, True)}
        with patch("tasks.equipment_reroll.equipment_reroll.Ocr.ocr_multi_lines",
                   side_effect=AssertionError("Refresh prices must not use OCR")):
            for sample_id, locks in cases.items():
                with self.subTest(sample_id=sample_id):
                    image = load_sample(sample_id)
                    self.assertEqual(matcher.read(image, locks), (20, 60, 150)[sum(locks)])
                    wrong = (False,) * 4 if any(locks) else (True, False, False, False)
                    with self.assertRaisesRegex(ValueError, "price template"):
                        matcher.read(image, wrong)
                    image[627:660, 326:369] = 0
                    with self.assertRaisesRegex(ValueError, "price template"):
                        matcher.read(image, locks)

    def test_real_balance_glyphs_ignore_lighting_and_detect_payments(self):
        pairs = (("replace_applied", "speed_four_current"), ("speed_five_two_locks", "speed_five_locked_roll"))
        for first, second in pairs:
            with self.subTest(first=first, second=second):
                image = load_sample(first)
                original = point_signature(image)
                dimmed = point_signature((image * 0.85).astype("uint8"))
                self.assertTrue(PointBalanceEstimate.same_glyphs(original, dimmed))
                self.assertFalse(PointBalanceEstimate.same_glyphs(original, point_signature(load_sample(second))))
                # Animated dark backgrounds are not payment evidence.
                image[102:140, 975:1085][image[102:140, 975:1085].max(axis=2) < 70] = (35, 30, 40)
                self.assertTrue(PointBalanceEstimate.same_glyphs(original, point_signature(image)))
        with self.assertRaisesRegex(ValueError, "not visible"):
            point_signature(np.zeros((720, 1280, 3), dtype="uint8"))

    def test_estimated_balances_audit_every_ten_refreshes_and_at_exit(self):
        self.sample_id = "critical_damage"
        source = load_sample(self.sample_id)
        images = [with_point_balance(source, 87953 - i * 20) for i in range(26)]
        # Twenty-five identical candidate results still need twenty-five
        # distinct balances. Old frames, including a brightness change, must
        # not confirm a click or restart OCR on the unchanged balance.
        frames = [images[0], images[0], (images[0] * 0.99).astype("uint8"), images[0]]
        for image in images[1:]:
            dimmed = image.copy()
            # Only balance exposure varies here; candidate glyph animation
            # has its own separate stability tests and must still block clicks.
            dimmed[102:140, 975:1085] = (image[102:140, 975:1085] * 0.85).astype("uint8")
            frames.extend((image, dimmed))
        clock = ReplayClock()
        self.device = ReplayDevice(frames, clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        current = (Substat("FlatDefense", 32), Substat("Speed", 4),
                   Substat("FlatAttack", 40), Substat("DefensePercent", 8))
        # The balance values and initial current observation are explicit
        # synthetic inputs; price, candidate, stability and action loop are real.
        budget = RefreshBudget(25, 500, 0)
        audit_counts = []

        def read_points(images):
            self.assertEqual(len(images), 1)
            audit_counts.append(budget.refreshes)
            return [(str(87953 - budget.refreshes * 20), 0.99)]

        with patch.object(task, "_read_substats", return_value=current), patch(
            "tasks.equipment_reroll.equipment_reroll.Ocr.ocr_multi_lines", side_effect=read_points,
        ) as points_ocr, patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock):
            reason = task.execute(policy(), budget)
        self.assertEqual(reason, "Refresh limit reached")
        self.assertEqual(audit_counts, [0, 10, 20, 25])
        self.assertEqual(points_ocr.call_count, 4)
        self.assertEqual((budget.refreshes, budget.spent, task._point_estimate.points), (25, 500, 87453))
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"] * 25)
        self.assertTrue(all(b - a >= 2 for a, b in zip(self.device.action_times, self.device.action_times[1:])))

    def test_balance_audit_mismatch_stops_before_another_paid_action(self):
        self.sample_id = "critical_damage"
        source = load_sample(self.sample_id)
        images = [with_point_balance(source, 87953 - i * 20) for i in range(11)]
        frames = [image for image in images for _ in range(2)]
        clock = ReplayClock()
        self.device = ReplayDevice(frames, clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        current = (Substat("FlatDefense", 32), Substat("Speed", 4),
                   Substat("FlatAttack", 40), Substat("DefensePercent", 8))
        budget = RefreshBudget(0, 0, 0)
        with patch.object(task, "_read_substats", return_value=current), patch(
            "tasks.equipment_reroll.equipment_reroll.Ocr.ocr_multi_lines",
            side_effect=[[("87953", 0.99)], [("87752", 0.99)]],
        ) as points_ocr, patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch(
            "tasks.equipment_reroll.equipment_reroll.logger.critical",
        ) as critical, self.assertRaises(RequestHumanTakeover) as raised:
            task.execute(policy(), budget)
        self.assertEqual(points_ocr.call_count, 2)
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"] * 10)
        critical.assert_called_once()
        self.assertIn("estimated 87753, observed 87752", critical.call_args.args[0])
        self.assertEqual(raised.exception.args, ())
        self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_estimated_payment_waits_for_real_delayed_balance_without_repeating_click(self):
        self.sample_id = "speed_four_current"
        before, after = load_sample("replace_applied"), load_sample(self.sample_id)
        clock = ReplayClock()
        self.device = ReplayDevice([before, before, before, (before * 0.99).astype("uint8"), after, after], clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        budget = RefreshBudget(1, 0, 0)
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch.object(
            task, "_read_points", wraps=task._read_points,
        ) as points:
            reason = task.execute(policy(), budget)
        self.assertEqual(reason, "Refresh limit reached")
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual((budget.refreshes, budget.spent), (1, 20))
        # Startup may hit the raw-image cache on its second stable frame;
        # pending frames never call OCR, and exit forces one fresh verification.
        self.assertEqual([call.kwargs for call in points.call_args_list], [{}, {}, {"fresh": True}])

    def test_estimated_payment_never_counts_or_retries_an_unchanged_balance(self):
        self.sample_id = "replace_applied"
        image = load_sample(self.sample_id)
        clock = ReplayClock()
        self.device = ReplayDevice([image] * 22, clock)
        task = EquipmentReroll(SimpleNamespace(), self.device)
        task.handle_network_error = lambda: False
        budget = RefreshBudget(1, 0, 0)
        with patch("tasks.equipment_reroll.equipment_reroll.monotonic", clock), patch.object(
            task, "_read_points", wraps=task._read_points,
        ) as points, patch("tasks.equipment_reroll.equipment_reroll.logger.critical") as critical, \
                self.assertRaises(RequestHumanTakeover) as raised:
            task.execute(policy(), budget)
        self.assertEqual([call.kwargs for call in points.call_args_list], [{}, {}])
        self.assertEqual(self.device.actions, ["EQUIPMENT_REROLL_REFRESH"])
        self.assertEqual((budget.refreshes, budget.spent), (0, 0))
        self.assertEqual(task._point_estimate.points, 91483)
        self.assertEqual(self.device.click_history_clears, 0)
        critical.assert_called_once()
        self.assertIn("refresh action could not be confirmed", critical.call_args.args[0])
        self.assertEqual(raised.exception.args, ())
