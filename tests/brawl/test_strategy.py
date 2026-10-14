import unittest

from tasks.brawl.rules import HEROES, Imprint, RuleInputError
from tasks.brawl.strategy import HeroState, place_lineup, recommend_lineup, score_lineup


def roster(*names, imprint="NONE"):
    return tuple(HeroState(name, imprint) for name in names)


class StrategyTests(unittest.TestCase):
    def test_pair_threshold_beats_disconnected_individual_rarity(self):
        # Two summer thieves activate both faction and profession. Individually
        # higher rarity picks cannot reproduce these team-wide threshold bonuses.
        states = roster("赛兹", "卡琳", "操作员赛珂兰特")
        result = recommend_lineup(states, 1)
        self.assertEqual({state.name for state in result.team}, {"赛兹", "卡琳"})
        self.assertEqual({state.name for state in result.bench}, {"操作员赛珂兰特"})
        self.assertEqual({synergy.name for synergy in result.synergies}, {"夏日假期", "盗贼"})
        self.assertTrue(result.heuristic)

    def test_new_hero_can_complete_four_member_trait(self):
        states = roster("赛兹", "卡琳", "度假风优芬妮", "雅卡泰丝", "操作员赛珂兰特")
        result = recommend_lineup(states, 3)
        self.assertEqual({state.name for state in result.team}, {"赛兹", "卡琳", "度假风优芬妮", "雅卡泰丝"})
        self.assertEqual(next(item.threshold for item in result.synergies if item.name == "夏日假期"), 4)

    def test_talent_threshold_changes_whole_team_score(self):
        states = roster("赛兹", "黎洁特")
        without = score_lineup(states)
        with_bonus = score_lineup(states, {"盗贼": 1, "精灵师": 1})
        self.assertGreater(with_bonus, without)
        result = recommend_lineup(states, 1, {"盗贼": 1, "精灵师": 1})
        self.assertEqual({item.name for item in result.synergies}, {"神圣", "盗贼", "精灵师"})

    def test_higher_imprint_and_skill_unlock_improve_preference(self):
        blank = score_lineup(roster("黎洁特"))
        unlocked = score_lineup(roster("黎洁特", imprint="B"))
        self.assertGreater(unlocked, blank)
        self.assertGreater(score_lineup(roster("黎洁特", imprint="A")), unlocked)

    def test_incomplete_state_unknown_imprint_and_duplicates_are_rejected(self):
        with self.assertRaises(TypeError):
            HeroState("赛兹")
        for name, imprint in (("赛兹", None), ("赛兹", "C"), ("未知", "NONE")):
            with self.subTest(name=name, imprint=imprint), self.assertRaises(RuleInputError):
                HeroState(name, imprint)
        for states in ((), ("赛兹",), roster("赛兹", "赛兹"), roster("露彬", "露斌"), None):
            with self.subTest(states=states), self.assertRaises(RuleInputError):
                recommend_lineup(states, 1)

    def test_level_two_start_allows_three_and_never_fabricates_missing_members(self):
        states = roster("赛兹", "卡琳", "度假风优芬妮")
        self.assertEqual(len(recommend_lineup(states, 2).team), 3)
        self.assertEqual(len(recommend_lineup(states, 9).team), 3)
        self.assertEqual(len(recommend_lineup(states, 1).team), 2)

    def test_order_of_input_does_not_change_recommendation(self):
        states = roster("赛兹", "卡琳", "操作员赛珂兰特", "最后的碎片卡琳")
        self.assertEqual(recommend_lineup(states, 1), recommend_lineup(reversed(states), 1))

    def test_range_exceptions_determine_logical_front_and_back_rows(self):
        states = roster("露彬", "黎洁特", "格伦", "萝季", "莱娅")
        recommendation = recommend_lineup(states, 4)
        self.assertEqual(set(recommendation.team), set(states))
        placed = {item.name: item for item in recommendation.placements}
        self.assertEqual(len({(item.row, item.column) for item in placed.values()}), 5)
        self.assertEqual(placed["莱娅"].attack_range, 2)
        self.assertEqual(placed["莱娅"].row, 1)
        self.assertEqual(placed["露彬"].row, 0)
        self.assertEqual(placed["露彬"].attack_range, 1)
        self.assertEqual(placed["黎洁特"].row, 2)
        self.assertEqual(placed["黎洁特"].attack_range, 3)
        self.assertEqual(placed["格伦"].row, 3)
        self.assertEqual(placed["萝季"].row, 0)

    def test_ten_melee_heroes_use_unique_cells_and_second_front_row(self):
        names = tuple(hero.name for hero in HEROES.values() if hero.attack_range == 1)[:10]
        placements = place_lineup(roster(*names))
        cells = {(item.row, item.column) for item in placements}
        self.assertEqual(len(cells), 10)
        self.assertEqual({row for row, _ in cells}, {0, 1})
        self.assertTrue(all(0 <= row < 4 and 0 <= column < 7 for row, column in cells))
        with self.assertRaises(RuleInputError):
            place_lineup(roster(*tuple(HEROES)[:11]))

    def test_large_pool_search_returns_ten_distinct_owned_heroes(self):
        states = tuple(HeroState(name, Imprint.NONE) for name in tuple(HEROES)[:20])
        result = recommend_lineup(states, 9)
        self.assertEqual(result.search_method, "beam_heuristic")
        self.assertEqual(len(result.team), 10)
        self.assertEqual(len(result.bench), 10)
        self.assertEqual(len({state.name for state in result.team}), 10)
        self.assertEqual({state.name for state in result.team + result.bench}, {state.name for state in states})
        self.assertEqual(len({(cell.row, cell.column) for cell in result.placements}), 10)
        self.assertEqual(result.score, score_lineup(result.team))

    def test_full_roster_limit_tracks_level_and_ten_bench_slots(self):
        names = tuple(HEROES)
        for level in (1, 4):
            with self.subTest(level=level):
                limit = level + 1 + 10
                states = roster(*names[:limit])
                result = recommend_lineup(states, level)
                self.assertEqual(len(result.team), level + 1)
                self.assertEqual(len(result.bench), 10)
                self.assertEqual(set(result.team + result.bench), set(states))
                with self.assertRaises(RuleInputError):
                    recommend_lineup(roster(*names[:limit + 1]), level)
        # The catalogue lists species, not heroes the current run can hold.
        with self.assertRaises(RuleInputError):
            recommend_lineup(roster(*names), 9)
        with self.assertRaises(RuleInputError):
            recommend_lineup(roster(*names[:21]), 9)
