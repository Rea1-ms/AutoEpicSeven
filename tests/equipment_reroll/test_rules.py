import unittest
from dataclasses import replace

from tasks.equipment_reroll.rules import (
    RefreshBudget, RejectedCandidate, RerollPolicy, Snapshot, Substat, Target, UnreadSubstat, parse_points, parse_substat,
)


def stats(speed=3, defense=6, health=6, resistance=7):
    return (Substat("Speed", speed), Substat("DefensePercent", defense),
            Substat("HealthPercent", health), Substat("Resistance", resistance))


def snapshot(current=None, candidate=None, points=1000, locked=(False,) * 4):
    return Snapshot(stats() if current is None else current, stats() if candidate is None else candidate, locked, points,
                    (20, 60, 150)[sum(locked)])


def policy():
    return RerollPolicy((Target("Speed", 5), Target("DefensePercent", 8),
                         Target("Resistance", 8), Target("HealthPercent", 8)))


class RuleTests(unittest.TestCase):
    def test_speed_is_first_even_when_ranked_third(self):
        p = RerollPolicy((Target("DefensePercent", 8), Target("Resistance", 8),
                          Target("Speed", 5), Target("HealthPercent", 8)))
        self.assertEqual(p.targets[0], Target("Speed", 5))
        self.assertTrue(p.should_replace(snapshot(stats(4, 8, 8, 8), stats(5, 4, 4, 4))))
        self.assertFalse(p.should_replace(snapshot(stats(5, 4, 4, 4), stats(4, 8, 8, 8))))

    def test_no_lock_before_speed_five(self):
        self.assertEqual(policy().desired_locks(stats(4, 8, 8, 8)), (False,) * 4)

    def test_lock_only_achieved_priorities(self):
        p = policy()
        self.assertEqual(p.desired_locks(stats(5, 6, 8, 8)), (True, False, False, False))
        self.assertEqual(p.desired_locks(stats(5, 8, 8, 8)), (True, True, False, False))

    def test_lower_priority_improvement_and_ties(self):
        p = policy()
        self.assertTrue(p.should_replace(snapshot(stats(5, 8, 8, 6), stats(5, 8, 4, 8))))
        self.assertFalse(p.should_replace(snapshot(stats(5, 8, 8, 8), stats(5, 8, 8, 8))))

    def test_without_speed_uses_user_order(self):
        p = RerollPolicy((Target("DefensePercent", 8), Target("HealthPercent", 8),
                         Target("Resistance", 8), Target("CriticalDamage", 7)))
        current = (Substat("CriticalDamage", 7), Substat("HealthPercent", 8),
                   Substat("Resistance", 8), Substat("DefensePercent", 6))
        self.assertEqual(p.desired_locks(current), (False,) * 4)

    def test_percentages_and_flat_values_are_distinct(self):
        for name, flat, percent in (("攻击力", "FlatAttack", "AttackPercent"),
                                    ("防御力", "FlatDefense", "DefensePercent"),
                                    ("生命值", "FlatHealth", "HealthPercent")):
            self.assertEqual(parse_substat(name, "8%"), Substat(percent, 8))
            self.assertEqual(parse_substat(name, "250"), Substat(flat, 250))
            with self.assertRaises(ValueError):
                parse_substat(name, "8")

    def test_malformed_or_impossible_values_are_rejected(self):
        for name, value in (("速度", "5%"), ("速度", "6"), ("速度", "¥5"),
                            ("效果抗性", "8"), ("暴击率", "6%"), ("攻击力", "0%")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                parse_substat(name, value)
        for text in ("90,37", "9O,373", "", "-20", "点数20"):
            with self.assertRaises(ValueError):
                parse_points(text)
        self.assertEqual(parse_points("90,373"), 90373)

    def test_invalid_configuration_is_rejected(self):
        for value in (4, 6, -1):
            with self.assertRaises(ValueError):
                Target("Speed", value)
        with self.assertRaises(ValueError):
            RerollPolicy((Target("Speed", 5),) * 4)
        with self.assertRaises(ValueError):
            RefreshBudget(-1, 0, 0)

    def test_flat_targets_can_exceed_observed_values(self):
        self.assertEqual(Target("FlatHealth", 250).value, 250)

    def test_budget_boundaries_include_lock_cost(self):
        self.assertIsNone(RefreshBudget(0, 150, 850).stop_reason(snapshot(locked=(True, True, False, False))))
        self.assertIsNotNone(RefreshBudget(0, 149, 0).stop_reason(snapshot(locked=(True, True, False, False))))
        self.assertIsNotNone(RefreshBudget(0, 0, 851).stop_reason(snapshot(locked=(True, True, False, False))))
        self.assertIsNotNone(RefreshBudget(2, 0, 0, refreshes=2).stop_reason(snapshot()))

    def test_refresh_requires_exact_payment_and_preserved_locks(self):
        before = snapshot(locked=(True, False, False, False))
        b = RefreshBudget(0, 0, 0)
        self.assertFalse(b.confirm_refresh(before, replace(before, candidate=stats(3, 8, 8, 8))))
        self.assertEqual((b.refreshes, b.spent), (0, 0))
        self.assertTrue(b.confirm_refresh(before, replace(before, points=940)))
        self.assertEqual((b.refreshes, b.spent), (1, 60))
        with self.assertRaises(ValueError):
            b.confirm_refresh(before, replace(before, points=939))
        with self.assertRaises(ValueError):
            b.confirm_refresh(before, replace(before, points=940, candidate=stats(4)))

    def test_snapshot_requires_complete_and_consistent_rows(self):
        with self.assertRaises(ValueError):
            replace(snapshot(), cost=60)
        with self.assertRaises(ValueError):
            replace(snapshot(), current=(Substat("Speed", 3),) * 4)

    def test_partial_candidate_can_only_be_used_by_the_rejecting_policy(self):
        candidate = RejectedCandidate((UnreadSubstat("攻击力"), UnreadSubstat("生命值"),
                                       UnreadSubstat("防御力"), UnreadSubstat("暴击率")), policy().targets, b"frame")
        self.assertFalse(policy().should_replace(snapshot(candidate=candidate)))
        other = RerollPolicy((Target("CriticalChance", 5), Target("HealthPercent", 8),
                              Target("DefensePercent", 8), Target("FlatAttack", 44)))
        with self.assertRaisesRegex(ValueError, "current targets"):
            other.should_replace(snapshot(candidate=candidate))

    def test_partial_candidate_must_preserve_locked_rows_for_paid_confirmation(self):
        before = snapshot(current=stats(5), locked=(True, False, False, False))
        rows = (before.current[0], UnreadSubstat("防御力"), UnreadSubstat("生命值"), UnreadSubstat("攻击力"))
        candidate = RejectedCandidate(rows, policy().targets, b"paid")
        self.assertTrue(RefreshBudget(0, 0, 0).confirm_refresh(before, replace(before, candidate=candidate, points=940)))
        wrong = replace(candidate, rows=(UnreadSubstat("速度"), *rows[1:]))
        with self.assertRaisesRegex(ValueError, "preserve locked substat"):
            RefreshBudget(0, 0, 0).confirm_refresh(before, replace(before, candidate=wrong, points=940))

    def test_initial_locks_must_follow_configured_order_and_reach_targets(self):
        selected = RerollPolicy((Target("HealthPercent", 8), Target("DefensePercent", 8),
                                 Target("Resistance", 8), Target("Speed", 5)))
        current = stats(5, 8, 6, 8)
        with self.assertRaisesRegex(ValueError, "Initial locked substats conflict.*Speed 5"):
            selected.validate_initial_locks(snapshot(current=current, locked=(True, False, False, False)))
        for locks in ((False,) * 4, (False, False, True, False), (False, True, True, False)):
            selected.validate_initial_locks(snapshot(current=stats(5, 8, 8, 8), locked=locks))
        for current, locks in ((stats(5, 8, 6, 8), (False, False, True, False)),
                               (stats(5, 6, 8, 8), (False, True, False, False)),
                               (stats(5, 8, 8, 8), (False, False, False, True))):
            with self.assertRaises(ValueError):
                selected.validate_initial_locks(snapshot(current=current, locked=locks))
