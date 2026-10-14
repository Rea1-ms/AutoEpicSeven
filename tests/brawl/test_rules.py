import unittest
from collections import Counter

from tasks.brawl.rules import (
    BENCH_CAPACITY,
    HEROES,
    SYNERGIES,
    Imprint,
    Rarity,
    Resources,
    RuleInputError,
    UnknownRuleError,
    active_synergies,
    available_heroes,
    get_hero,
    hero_candidates,
    imprint_after_acquisitions,
    maximum_retained_copies,
    overflow_reward,
    parse_imprint,
    summon_cost,
    summon_odds,
    synergy_counts,
    team_capacity,
    transfer_reward,
    unlocked_skill_count,
    upgrade_cost,
    validate_imprint,
)


class CatalogueTests(unittest.TestCase):
    def test_all_34_heroes_have_the_guide_rarities_and_traits(self):
        self.assertEqual(len(HEROES), 34)
        self.assertEqual(Counter(hero.rarity for hero in HEROES.values()), {
            Rarity.NORMAL_3: 7, Rarity.NORMAL_4: 5, Rarity.MOONLIGHT_4: 5,
            Rarity.NORMAL_5: 11, Rarity.MOONLIGHT_5: 6,
        })
        self.assertEqual({hero.faction for hero in HEROES.values()}, {
            "转职", "夏日假期", "猎龙人", "奇迹女仆", "人造生命体", "继承的意志", "失落科技", "卡厄思", "神圣",
        })
        for hero in HEROES.values():
            self.assertIn(hero.faction, SYNERGIES)
            self.assertTrue(SYNERGIES[hero.profession].profession)

    def test_range_exceptions_and_explicit_spelling_alias(self):
        self.assertEqual(get_hero("莱娅").attack_range, 2)
        self.assertEqual(get_hero("黎洁特").attack_range, 3)
        self.assertEqual(get_hero("露斌"), get_hero("露彬"))
        self.assertEqual(get_hero("露彬").attack_range, 1)
        self.assertEqual(get_hero("指挥型莱伊卡").attack_range, 4)
        self.assertEqual(get_hero("卡麦萝兹").attack_range, 3)
        self.assertEqual(get_hero("塔玛林尔").attack_range, 1)

    def test_total_rank_unlocks_at_each_boundary(self):
        for name, required in (("蕾诺娅", 2), ("仆人克萝爱", 5), ("黎洁特", 10),
                               ("实验体赛兹", 15), ("操作员赛珂兰特", 20)):
            with self.subTest(name=name):
                self.assertNotIn(name, {hero.name for hero in available_heroes(required - 1)})
                self.assertIn(name, {hero.name for hero in available_heroes(required)})
        self.assertEqual(len(available_heroes(0)), 29)
        self.assertEqual(len(available_heroes(20)), 34)

    def test_unknown_names_and_unread_fields_are_rejected(self):
        for name in ("克劳乌", "", None, [], "赛茲"):
            with self.subTest(name=name), self.assertRaises(RuleInputError):
                get_hero(name)
        for imprint in (None, "", "未知", "S S", 3, True):
            with self.subTest(imprint=imprint), self.assertRaises(RuleInputError):
                parse_imprint(imprint)
        self.assertEqual(parse_imprint("NONE"), Imprint.NONE)
        self.assertEqual(parse_imprint("SSS"), Imprint.SSS)


class TraitResolutionTests(unittest.TestCase):
    def test_unique_pairs_identify_twenty_one_heroes(self):
        expected = {
            ("人造生命体", "射手"): "指挥型莱伊卡",
            ("人造生命体", "盗贼"): "实验体赛兹",
            ("人造生命体", "骑士"): "战斗型玛雅",
            ("卡厄思", "射手"): "蕾诺娅",
            ("卡厄思", "战士"): "小春",
            ("卡厄思", "盗贼"): "梅铃",
            ("夏日假期", "战士"): "度假风优芬妮",
            ("夏日假期", "精灵师"): "雅卡泰丝",
            ("失落科技", "盗贼"): "最后的碎片卡琳",
            ("失落科技", "精灵师"): "未知可能性雅卡泰丝",
            ("失落科技", "骑士"): "最后的骑士克劳乌",
            ("奇迹女仆", "射手"): "缪斯黎玛",
            ("奇迹女仆", "战士"): "莱娅",
            ("猎龙人", "战士"): "玫拉妮",
            ("神圣", "精灵师"): "黎洁特",
            ("继承的意志", "射手"): "露彬",
            ("继承的意志", "战士"): "护卫队长克劳乌",
            ("继承的意志", "盗贼"): "利纳柯",
            ("继承的意志", "骑士"): "维多丽卡",
            ("转职", "射手"): "格伦",
            ("转职", "战士"): "萝里娜",
        }
        for traits, name in expected.items():
            with self.subTest(traits=traits):
                self.assertEqual(hero_candidates(*traits), (name,))

    def test_six_shared_pairs_keep_all_thirteen_candidates(self):
        expected = {
            ("人造生命体", "魔导士"): ("辅助型拉兹", "玫勒赛德丝"),
            ("夏日假期", "盗贼"): ("赛兹", "卡琳"),
            ("失落科技", "射手"): ("操作员赛珂兰特", "西风处刑者修里"),
            ("奇迹女仆", "精灵师"): ("仆人克萝爱", "塔玛林尔"),
            ("猎龙人", "骑士"): ("赛娜", "萝季", "克莉丝缇"),
            ("转职", "魔导士"): ("卡麦萝兹", "卡萝"),
        }
        for traits, names in expected.items():
            with self.subTest(traits=traits):
                self.assertEqual(hero_candidates(*traits), names)

    def test_invalid_or_missing_traits_cannot_supply_an_identity(self):
        for faction, profession in (
            (None, "射手"), ("转职", None), ("", "射手"), ("转职", ""),
            ([], "射手"), ("转职", {}), (True, "射手"), ("转职", 1),
            ("未知阵营", "射手"), ("转职", "未知职业"), ("射手", "转职"),
            ("转职", "夏日假期"), ("骑士", "射手"), ("转职 ", "射手"),
        ):
            with self.subTest(faction=faction, profession=profession), self.assertRaises(RuleInputError):
                hero_candidates(faction, profession)
        # Both labels are known, but that does not prove a catalogue hero exists.
        self.assertEqual(hero_candidates("神圣", "射手"), ())
        self.assertEqual(hero_candidates("卡厄思", "精灵师"), ())


class EconomyTests(unittest.TestCase):
    def test_level_costs_and_capacity_include_talent_start_and_cap(self):
        self.assertEqual([upgrade_cost(level) for level in range(1, 9)], [20, 50, 70, 100, 120, 150, 200, 250])
        self.assertEqual([team_capacity(level) for level in (1, 2, 9)], [2, 3, 10])
        self.assertEqual(BENCH_CAPACITY, 10)
        with self.assertRaises(RuleInputError):
            upgrade_cost(9)
        for level in (0, 10, None, True, 1.0, "2"):
            with self.subTest(level=level), self.assertRaises(RuleInputError):
                team_capacity(level)

    def test_summon_prices_use_distinct_currencies(self):
        self.assertEqual(summon_cost("normal"), Resources(10, 0))
        self.assertEqual(summon_cost("lower"), Resources(0, 10))
        self.assertEqual(summon_cost("upper"), Resources(0, 40))
        with self.assertRaises(RuleInputError):
            summon_cost("free")

    def test_known_normal_odds_and_missing_levels(self):
        self.assertEqual(list(summon_odds("normal", 8).values()), [0.10, 0.20, 0.33, 0.25, 0.12])
        self.assertEqual(list(summon_odds("normal", 9).values()), [0.05, 0.10, 0.25, 0.40, 0.20])
        for level in range(1, 8):
            with self.subTest(level=level), self.assertRaises(UnknownRuleError):
                summon_odds("normal", level)
        with self.assertRaises(RuleInputError):
            summon_odds("normal")

    def test_fate_odds_are_constant_but_invalid_observed_levels_fail(self):
        self.assertEqual(summon_odds("lower", 1), summon_odds("lower", 9))
        self.assertEqual(list(summon_odds("lower").values()), [0, 0.60, 0.40, 0, 0])
        self.assertEqual(list(summon_odds("upper").values()), [0, 0, 0, 0.70, 0.30])
        self.assertAlmostEqual(sum(summon_odds("normal", 8).values()), 1)
        for kind, level in (("unknown", 8), ("upper", 0), ("lower", True)):
            with self.subTest(kind=kind, level=level), self.assertRaises(RuleInputError):
                summon_odds(kind, level)


class ImprintTests(unittest.TestCase):
    def test_initial_ownership_and_first_duplicate_follow_each_rarity(self):
        for name, initial in (("利纳柯", Imprint.B), ("战斗型玛雅", Imprint.C), ("萝里娜", Imprint.D)):
            with self.subTest(name=name):
                self.assertEqual(imprint_after_acquisitions(name, 1, True), initial)
                self.assertEqual(imprint_after_acquisitions(name, 1, False), Imprint.NONE)
                self.assertEqual(imprint_after_acquisitions(name, 2, False), initial)
                self.assertEqual(imprint_after_acquisitions(name, 2, True), Imprint(initial + 1))

    def test_progression_and_overflow_stop_at_sss(self):
        self.assertEqual([imprint_after_acquisitions("利纳柯", count, True) for count in range(1, 7)],
                         [Imprint.B, Imprint.A, Imprint.S, Imprint.SS, Imprint.SSS, Imprint.SSS])
        self.assertEqual(maximum_retained_copies("利纳柯", True), 5)
        self.assertEqual(maximum_retained_copies("仆人克萝爱", False), 6)
        self.assertEqual(maximum_retained_copies("萝里娜", False), 8)

    def test_impossible_rarity_imprints_are_not_accepted(self):
        for name, imprint in (("赛兹", "D"), ("利纳柯", "C"), ("萝季", "D")):
            with self.subTest(name=name, imprint=imprint), self.assertRaises(RuleInputError):
                validate_imprint(name, imprint)
        self.assertEqual(validate_imprint("萝季", "C"), Imprint.C)
        self.assertEqual(validate_imprint("赛兹", "NONE"), Imprint.NONE)

    def test_imprint_history_requires_strict_count_and_ownership(self):
        for copies, owned in ((0, True), (-1, True), (1.0, True), (True, True), (1, None), (1, 1), (1, "yes")):
            with self.subTest(copies=copies, owned=owned), self.assertRaises(RuleInputError):
                imprint_after_acquisitions("利纳柯", copies, owned)

    def test_identical_current_imprints_do_not_imply_identical_transfer_value(self):
        self.assertEqual(imprint_after_acquisitions("利纳柯", 1, True), Imprint.B)
        self.assertEqual(imprint_after_acquisitions("利纳柯", 2, False), Imprint.B)
        self.assertEqual(transfer_reward("利纳柯", 1, True), Resources(7, 10))
        self.assertEqual(transfer_reward("利纳柯", 2, False), Resources(14, 20))
        self.assertEqual(transfer_reward("仆人克萝爱", 1, False), Resources(10, 20))
        self.assertEqual(transfer_reward("仆人克萝爱", 2, False), Resources(20, 40))

    def test_manual_transfer_examples_and_acquisition_multipliers(self):
        self.assertEqual(transfer_reward("萝季", 2, True), Resources(6, 4))
        self.assertEqual(transfer_reward("战斗型玛雅", 2, True), Resources(10, 10))
        self.assertEqual(transfer_reward("利纳柯", 3, True), Resources(21, 30))
        self.assertEqual(transfer_reward("利纳柯", 4, True), Resources(28, 40))
        # The guide labels two different examples S. Acquisition count remains
        # authoritative here; no label is silently repaired to infer that count.
        with self.assertRaises(RuleInputError):
            transfer_reward("利纳柯", None, True)

    def test_overflow_is_one_white_copy_and_cannot_be_sold_twice(self):
        self.assertEqual(overflow_reward("最后的骑士克劳乌"), Resources(10, 20))
        self.assertEqual(overflow_reward("利纳柯"), Resources(7, 10))
        self.assertEqual(overflow_reward("战斗型玛雅"), Resources(5, 5))
        self.assertEqual(overflow_reward("萝季"), Resources(3, 2))
        self.assertEqual(overflow_reward("萝里娜"), Resources(2, 0))
        with self.assertRaises(RuleInputError):
            transfer_reward("利纳柯", 6, True)
        self.assertEqual(transfer_reward("利纳柯", 5, True), Resources(35, 50))

    def test_skill_thresholds_and_unrecorded_low_rarity_skills(self):
        for name, before, after, count in (
            ("黎洁特", "NONE", "B", 3), ("塔玛林尔", "B", "A", 2),
            ("度假风优芬妮", "B", "A", 3), ("赛娜", "B", "A", 3),
            ("梅铃", "NONE", "B", 3), ("小春", "NONE", "B", 3),
        ):
            with self.subTest(name=name):
                self.assertEqual(unlocked_skill_count(name, before), count - 1)
                self.assertEqual(unlocked_skill_count(name, after), count)
        self.assertEqual(unlocked_skill_count("赛兹", "NONE"), 2)
        with self.assertRaises(UnknownRuleError):
            unlocked_skill_count("萝里娜", "D")


class SynergyTests(unittest.TestCase):
    def test_full_threshold_table_matches_the_guide(self):
        expected = {name: (2, 4) for name in ("转职", "夏日假期", "猎龙人", "奇迹女仆", "人造生命体",
                                               "继承的意志", "失落科技", "魔导士", "精灵师")}
        expected.update({name: (2, 4, 6) for name in ("战士", "骑士", "射手", "盗贼")})
        expected.update({"卡厄思": (2, 3), "神圣": (1,)})
        self.assertEqual({name: rule.thresholds for name, rule in SYNERGIES.items()}, expected)

    def test_profession_talent_adds_one_without_inventing_faction_members(self):
        counts = synergy_counts(["赛兹"], {"盗贼": 1})
        self.assertEqual(counts["盗贼"], 2)
        self.assertEqual(counts["夏日假期"], 1)
        self.assertEqual([(item.name, item.threshold) for item in active_synergies(["赛兹"], {"盗贼": 1})],
                         [("盗贼", 2)])
        self.assertEqual(active_synergies([], {"盗贼": 1}), ())

    def test_chaos_and_divine_use_their_special_thresholds(self):
        pair = {item.name: item.threshold for item in active_synergies(["小春", "梅铃"])}
        three = {item.name: item.threshold for item in active_synergies(["小春", "梅铃", "蕾诺娅"])}
        self.assertEqual(pair["卡厄思"], 2)
        self.assertEqual(three["卡厄思"], 3)
        self.assertEqual([(item.name, item.threshold) for item in active_synergies(["黎洁特"])], [("神圣", 1)])

    def test_only_highest_active_tier_is_returned(self):
        team = ["萝季", "赛娜", "玫拉妮", "克莉丝缇"]
        hunter = [item for item in active_synergies(team) if item.name == "猎龙人"]
        self.assertEqual(len(hunter), 1)
        self.assertEqual((hunter[0].count, hunter[0].threshold, hunter[0].tier), (4, 4, 2))
        knight = [item for item in active_synergies(team, {"骑士": 1}) if item.name == "骑士"][0]
        self.assertEqual((knight.count, knight.threshold, knight.tier), (4, 4, 2))

    def test_duplicate_names_aliases_unknown_talents_and_partial_inputs_fail(self):
        for names, bonuses in ((["赛兹", "赛兹"], {}), (["露彬", "露斌"], {}), (["赛兹"], {"夏日假期": 1}),
                               (["赛兹"], {"盗贼": 2}), (["赛兹"], {"盗贼": None}), (["赛兹"], {"盗贼": True}),
                               ("赛兹", {}), (None, {}), (["赛兹", None], {}), (["赛兹"], [])):
            with self.subTest(names=names, bonuses=bonuses), self.assertRaises(RuleInputError):
                synergy_counts(names, bonuses)
