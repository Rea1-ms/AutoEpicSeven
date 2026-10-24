import unittest
from dataclasses import replace
from unittest.mock import patch

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.bench import BenchHero  # noqa: E402
from tasks.brawl.economy_policy import (  # noqa: E402
    EconomyAction,
    plan_economy,
    transfer_candidates,
)
from tasks.brawl.heroes import HeroResult  # noqa: E402
from tasks.brawl.roster import (  # noqa: E402
    FormationKind,
    FormationStep,
    RosterHero,
    RosterState,
    begin_roster,
    commit_step,
    derive_after,
    next_step,
    reconcile,
)
from tasks.brawl.rules import HEROES, Imprint, Resources, RuleInputError  # noqa: E402
from tasks.brawl.scanner import (  # noqa: E402
    BenchScanResult,
    BrawlBenchScanner,
    ScanOutcome,
)
from tasks.brawl.strategy import (  # noqa: E402
    HeroState,
    place_lineup,
    recommend_lineup,
    score_lineup,
)
from tasks.brawl.vision import BrawlMode, PreparationPanel  # noqa: E402


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
        melee_mages = place_lineup(roster("卡萝", "卡麦萝兹"))
        self.assertEqual(len({(item.row, item.column) for item in melee_mages}), 2)
        self.assertTrue(all(item.row == 0 and item.attack_range == 1 for item in melee_mages))

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


class EconomyPolicyTests(unittest.TestCase):
    """Synthetic verified rosters; finance cases isolate explicit eligibility.

    Transfer preservation is exercised against the actual lineup search below.
    The treasury boundary cases provide a known eligible set so changes to the
    lineup heuristic do not silently change their five-sale budget scenarios.
    No observed transfer price is inferred from a character's rarity or imprint.
    """

    def state(self, *, level=2, deployed=3, count=3, coins=0, tokens=0, revision=7):
        heroes = tuple(RosterHero(name, "NONE", (index // 4, index % 4)
                                  if index < deployed else None)
                       for index, name in enumerate(tuple(HEROES)[:count]))
        panel = PreparationPanel(3, level, deployed, level + 1, coins, tokens)
        return RosterState(heroes, panel, BrawlMode.NORMAL, revision)

    def eligible(self, state):
        names = tuple(hero.name for hero in state.heroes if hero.cell is None)
        protected = tuple(hero.name for hero in state.heroes if hero.cell is not None)
        return patch("tasks.brawl.economy_policy.transfer_candidates", return_value=(names, protected))

    def quote(self, state, coins=10, tokens=0):
        return {hero.name: Resources(coins, tokens) for hero in state.heroes if hero.cell is None}

    def test_affordable_upgrade_precedes_tokens_and_missing_members(self):
        state = self.state(deployed=1, count=1, coins=50, tokens=40)
        plan = plan_economy(state)
        self.assertEqual(plan.action, EconomyAction.UPGRADE)
        self.assertEqual(plan.revision, 7)
        self.assertEqual(plan.targets, ())

    def test_normal_summon_only_fills_missing_members_with_available_coins(self):
        short = self.state(deployed=2, count=2, coins=10)
        self.assertEqual(plan_economy(short).action, EconomyAction.SUMMON_NORMAL)
        poor = replace(short, panel=replace(short.panel, coins=9))
        self.assertEqual(plan_economy(poor).action, EconomyAction.SAVE)
        full = self.state(coins=49)
        self.assertEqual(plan_economy(full).action, EconomyAction.SAVE)
        pending_formation = self.state(deployed=2, count=3, coins=49)
        self.assertEqual(plan_economy(pending_formation).reason, "formation_required")

    def test_tokens_wait_for_forty_and_never_choose_lower_summon(self):
        for tokens in (0, 10, 20, 39):
            with self.subTest(tokens=tokens):
                self.assertEqual(plan_economy(self.state(tokens=tokens)).action, EconomyAction.SAVE)
        self.assertEqual(plan_economy(self.state(tokens=40)).action, EconomyAction.SUMMON_UPPER)
        full_bench = self.state(count=13, tokens=40)
        self.assertNotEqual(plan_economy(full_bench).action, EconomyAction.SUMMON_UPPER)

    def test_challenge_does_not_inherit_normal_economy(self):
        state = replace(self.state(coins=500, tokens=80), game_mode=BrawlMode.CHALLENGE)
        self.assertEqual(plan_economy(state).action, EconomyAction.UNSUPPORTED)
        self.assertEqual(transfer_candidates(state)[0], ())

    def test_field_current_next_and_future_trait_members_remain_reserved(self):
        names = ("赛兹", "卡琳", "度假风优芬妮", "雅卡泰丝", "黎洁特", "卡萝", "格伦")
        heroes = tuple(RosterHero(name, "SSS" if index < 2 else "NONE",
                                  (0, index) if index < 2 else None)
                       for index, name in enumerate(names))
        state = RosterState(heroes, PreparationPanel(3, 1, 2, 2, 0, 0), BrawlMode.NORMAL)
        candidates, protected = transfer_candidates(state, {"战士": 1})
        pool = tuple(HeroState(hero.name, hero.imprint) for hero in heroes)
        current = recommend_lineup(pool, 1, {"战士": 1})
        following = recommend_lineup(pool, 2, {"战士": 1})
        required = {hero.name for hero in current.team + following.team} | {"赛兹", "卡琳"}
        self.assertTrue(required.issubset(protected))
        # The fourth summer member is not present in either tiny recommendation,
        # but is already owned and enables the later four-member threshold.
        self.assertIn("度假风优芬妮", protected)
        self.assertIn("雅卡泰丝", protected)
        self.assertFalse(set(candidates) & set(protected))
        self.assertEqual(set(candidates) | set(protected), set(names))

    def test_sale_cannot_leave_the_next_level_without_a_teammate(self):
        state = self.state(count=4, coins=49)
        with self.eligible(state):
            plan = plan_economy(state, observed_quotes=self.quote(state, 50), quote_revision=7)
        self.assertEqual(plan.action, EconomyAction.SAVE)
        self.assertEqual(plan.reason, "retain_next_capacity")

    def test_quotes_require_all_candidates_and_the_same_roster_revision(self):
        state = self.state(count=6, coins=30)
        quotes = self.quote(state)
        with self.eligible(state):
            plan = plan_economy(state)
            self.assertEqual(plan.action, EconomyAction.QUOTE_TRANSFER)
            self.assertEqual(set(plan.targets), set(quotes))
            partial = {next(iter(quotes)): Resources(20, 0)}
            self.assertEqual(plan_economy(state, observed_quotes=partial, quote_revision=7).action,
                             EconomyAction.QUOTE_TRANSFER)
            with self.assertRaises(RuleInputError):
                plan_economy(replace(state, revision=8), observed_quotes=quotes, quote_revision=7)

    def test_invalid_quotes_and_remaining_allowances_are_rejected(self):
        state = self.state(count=6)
        bench = state.heroes[-1].name
        for quotes, revision in ((None, 7), ({bench: Resources(1, 0)}, None),
                                 ({bench: Resources(1, 0)}, True),
                                 ({state.heroes[0].name: Resources(1, 0)}, 7),
                                 ({bench: Resources(-1, 0)}, 7), ({bench: Resources(0, 0)}, 7),
                                 ({bench: Resources(True, 0)}, 7), ({bench: (10, 0)}, 7)):
            with self.subTest(quotes=quotes, revision=revision), self.assertRaises(RuleInputError):
                plan_economy(state, observed_quotes=quotes, quote_revision=revision)
        for budget in (-1, 6, True, 2.5):
            with self.subTest(budget=budget), self.assertRaises(RuleInputError):
                plan_economy(state, transfer_budget=budget)
        with self.assertRaises(RuleInputError):
            plan_economy(state, max_level_slot_freed=1)

    def test_five_small_quotes_can_fund_upgrade_but_six_cannot(self):
        state = self.state(count=10, coins=0)
        with self.eligible(state):
            exact = plan_economy(state, observed_quotes=self.quote(state), quote_revision=7)
            too_small = plan_economy(state, observed_quotes=self.quote(state, 9), quote_revision=7)
        self.assertEqual(exact.action, EconomyAction.TRANSFER)
        self.assertEqual(len(exact.targets), 5)
        self.assertEqual(exact.expected_reward, Resources(50, 0))
        self.assertEqual(too_small.action, EconomyAction.SAVE)
        self.assertEqual(too_small.targets, ())

    def test_remaining_budget_is_not_reset_and_token_quotes_do_not_fund_coins(self):
        state = self.state(count=10, coins=10)
        with self.eligible(state):
            reduced = plan_economy(state, observed_quotes=self.quote(state), quote_revision=7,
                                   transfer_budget=3)
            tokens_only = plan_economy(state, observed_quotes=self.quote(state, 0, 40), quote_revision=7)
            exhausted = plan_economy(state, transfer_budget=0)
        self.assertEqual(reduced.action, EconomyAction.SAVE)
        self.assertEqual(tokens_only.action, EconomyAction.SAVE)
        self.assertEqual(exhausted.reason, "transfer_allowance_exhausted")

    def test_lower_imprint_is_preferred_when_both_quotes_fund_the_upgrade(self):
        state = self.state(count=6, coins=40)
        names = ("卡萝", "格伦")
        heroes = (*state.heroes[:4], RosterHero(names[0], "NONE"), RosterHero(names[1], "SSS"))
        state = replace(state, heroes=heroes)
        with patch("tasks.brawl.economy_policy.transfer_candidates", return_value=(names, ())):
            plan = plan_economy(state, observed_quotes={name: Resources(10, 0) for name in names},
                                quote_revision=7)
        self.assertEqual(plan.action, EconomyAction.TRANSFER)
        self.assertEqual(plan.targets, ("卡萝",))

    def test_max_level_full_bench_frees_only_one_slot_once_in_the_round(self):
        state = self.state(level=9, deployed=10, count=20)
        with self.eligible(state):
            plan = plan_economy(state, observed_quotes=self.quote(state, 1, 1), quote_revision=7)
            repeated = plan_economy(state, observed_quotes=self.quote(state), quote_revision=7,
                                    max_level_slot_freed=True)
        self.assertEqual(plan.action, EconomyAction.TRANSFER)
        self.assertEqual(len(plan.targets), 1)
        self.assertEqual(plan.reason, "free_one_bench_slot")
        self.assertEqual(repeated.action, EconomyAction.SAVE)
        self.assertEqual(plan_economy(self.state(level=9, deployed=10, count=19)).action, EconomyAction.SAVE)

    def test_transfer_group_without_melee_does_not_discount_two_sales(self):
        state = self.state(count=7, coins=40)
        names = ("玫勒赛德丝", "缪斯黎玛", "格伦")
        state = replace(state, heroes=(*state.heroes[:4], *(RosterHero(name, "NONE") for name in names)))
        # The first pair has no active trait and would be penalized for lacking
        # melee in combat. Its combat score is below either member's individual
        # value, but that combat penalty must not reduce the cost of discarding
        # both heroes when any single observed quote already funds the upgrade.
        self.assertEqual(score_lineup(roster(*names[:2])), 1)
        self.assertTrue(all(score_lineup(roster(*names[:2])) < score_lineup(roster(name)) for name in names[:2]))
        with patch("tasks.brawl.economy_policy.transfer_candidates", return_value=(names, ())):
            plan = plan_economy(state, observed_quotes={name: Resources(10, 0) for name in names},
                                quote_revision=7)
        self.assertEqual(plan.action, EconomyAction.TRANSFER)
        self.assertEqual(len(plan.targets), 1)
        self.assertEqual(plan.expected_reward, Resources(10, 0))


class NewGameRosterTests(unittest.TestCase):
    """All states below are explicit synthetic observations, except opening frames."""

    def state(self, *heroes, level=2, revision=0):
        panel = PreparationPanel(1, level, sum(hero.cell is not None for hero in heroes),
                                 level + 1, 70, 0)
        return RosterState(tuple(heroes), panel, BrawlMode.NORMAL, revision)

    def scan(self, state):
        return BenchScanResult(tuple(BenchHero(hero.name, hero.imprint) for hero in state.heroes
                                     if hero.cell is None), state.panel, state.game_mode, ScanOutcome.COMPLETE)

    def cells(self, state):
        return tuple(hero.cell for hero in state.heroes if hero.cell is not None)

    def test_real_normal_and_challenge_openings_initialize_empty_new_games(self):
        from tests.support.brawl import BrawlReplay
        from tests.support.offline import ControlledClock

        server.set_lang("global_cn")
        for name, mode in (("normal-opening", BrawlMode.NORMAL),
                           ("challenge-opening", BrawlMode.CHALLENGE)):
            with self.subTest(mode=mode), ControlledClock() as clock:
                replay = BrawlReplay(BrawlBenchScanner, [name] * 4, clock)
                self.frame = replay.image
                scan = replay.task.scan()
                state = begin_roster(scan)
                self.assertEqual(state.heroes, ())
                self.assertEqual(state.panel, PreparationPanel(1, 2, 0, 3, 70, 0))
                self.assertEqual(state.game_mode, mode)
                self.assertEqual(state.revision, 0)
                self.assertEqual(replay.actions, [])

    def test_new_game_accepts_complete_bench_but_rejects_partial_or_midgame_takeover(self):
        state = self.state(RosterHero("赛兹", "B"), RosterHero("卡琳", "C"))
        scan = self.scan(state)
        self.assertEqual(begin_roster(scan), state)
        invalid = (
            replace(scan, outcome=ScanOutcome.TIMEOUT),
            replace(scan, panel=replace(scan.panel, round_number=2)),
            replace(scan, panel=replace(scan.panel, deployed=1)),
            replace(scan, panel=None), replace(scan, game_mode=None),
            replace(scan, heroes=(BenchHero("赛兹", None),)),
            replace(scan, heroes=(BenchHero("露斌", Imprint.B),)),
            replace(scan, heroes=(scan.heroes[0], scan.heroes[0])),
        )
        for item in invalid:
            with self.subTest(scan=item), self.assertRaises(RuleInputError):
                begin_roster(item)

    def test_roster_rejects_unknown_identity_imprint_cells_and_inconsistent_counts(self):
        for args in (("未知", "B"), ("露斌", "B"), ("赛兹", None), ("赛兹", "C"),
                     ("赛兹", "B", (7, 0)), ("赛兹", "B", (0, 4)), ("赛兹", "B", (True, 0))):
            with self.subTest(args=args), self.assertRaises(RuleInputError):
                RosterHero(*args)
        state = self.state(RosterHero("赛兹", "B", (0, 0)))
        changes = (
            {"heroes": state.heroes * 2},
            {"heroes": (*state.heroes, RosterHero("卡琳", "C", (0, 0)))},
            {"panel": replace(state.panel, deployed=0)},
            {"panel": replace(state.panel, capacity=4)},
            {"panel": replace(state.panel, coins=-1)},
            {"panel": replace(state.panel, round_number=26)},
            {"panel": replace(state.panel, level=True)},
            {"game_mode": "normal"}, {"revision": True}, {"revision": -1},
            {"heroes": tuple(RosterHero(name, "NONE") for name in tuple(HEROES)[:11]),
             "panel": replace(state.panel, deployed=0)},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(RuleInputError):
                replace(state, **change)

    def test_desired_lineup_must_include_full_known_team_and_unique_screen_cells(self):
        state = self.state(RosterHero("赛兹", "B"), RosterHero("卡琳", "C"))
        for wanted in ((), (("赛兹", (0, 0)),), (("赛兹", (0, 0)), ("赛兹", (0, 1))),
                       (("赛兹", (0, 0)), ("卡琳", (0, 0))),
                       (("赛兹", (0, 0)), ("露彬", (0, 1))),
                       (("赛兹", (0, 0)), ("卡琳", (0, 4)))):
            with self.subTest(wanted=wanted), self.assertRaises(RuleInputError):
                next_step(state, wanted)
        self.assertIsNone(next_step(self.state(), ()))

    def test_place_removes_exact_bench_identity_only_after_all_evidence_agrees(self):
        state = self.state(RosterHero("赛兹", "B"), RosterHero("卡琳", "C"))
        step = next_step(state, {"赛兹": (1, 2), "卡琳": (3, 1)})
        expected = derive_after(state, step)
        self.assertEqual(step.kind, FormationKind.PLACE)
        self.assertEqual(state.panel.deployed, 0)
        self.assertEqual(expected.panel.deployed, 1)
        self.assertEqual(expected.revision, 1)
        after = self.scan(expected)
        actual = commit_step(state, step, after, self.cells(expected))
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual.heroes), 2)
        for bad_scan, cells in ((self.scan(state), self.cells(expected)), (after, ()),
                                (replace(after, panel=replace(after.panel, coins=60)), self.cells(expected)),
                                (replace(after, heroes=(BenchHero(step.hero, Imprint.B),)), self.cells(expected))):
            with self.subTest(scan=bad_scan, cells=cells), self.assertRaises(RuleInputError):
                commit_step(state, step, bad_scan, cells)
        with self.assertRaises(RuleInputError):
            derive_after(actual, step)

    def test_full_bench_uses_swap_without_selling_or_disturbing_correct_hero(self):
        names = tuple(HEROES)[:12]
        state = self.state(RosterHero(names[0], "NONE", (0, 0)),
                           RosterHero(names[1], "NONE", (0, 1)),
                           *(RosterHero(name, "NONE") for name in names[2:]), level=1)
        desired = {names[0]: (0, 0), names[2]: (0, 1)}
        step = next_step(state, desired)
        self.assertEqual(step, FormationStep(FormationKind.SWAP, names[2], None, (0, 1), names[1], 0))
        expected = derive_after(state, step)
        actual = commit_step(state, step, self.scan(expected), self.cells(expected))
        self.assertEqual(actual.panel, state.panel)
        self.assertEqual(len(self.scan(actual).heroes), 10)
        self.assertEqual({hero.name for hero in actual.heroes}, set(names))
        self.assertEqual(actual.heroes[0], state.heroes[0])
        self.assertIsNone(next_step(actual, desired))

    def test_cycle_breaks_at_unused_cell_and_converges_without_moving_correct_hero(self):
        state = self.state(RosterHero("赛兹", "B", (0, 0)), RosterHero("卡琳", "C", (0, 1)),
                           RosterHero("露彬", "B", (6, 3)))
        desired = {"赛兹": (0, 1), "卡琳": (0, 0), "露彬": (6, 3)}
        actions = []
        for _ in range(5):
            step = next_step(state, desired)
            if step is None:
                break
            actions.append(step)
            self.assertEqual(step.kind, FormationKind.MOVE)
            self.assertNotEqual(step.hero, "露彬")
            self.assertNotIn(step.target, self.cells(state))
            expected = derive_after(state, step)
            state = commit_step(state, step, self.scan(expected), self.cells(expected))
        self.assertEqual(len(actions), 3)
        self.assertNotIn(actions[0].target, desired.values())
        self.assertEqual({hero.name: hero.cell for hero in state.heroes}, desired)
        self.assertIsNone(next_step(state, desired))

    def test_replacement_then_vacant_moves_and_placement_finish_complete_recommendation(self):
        state = self.state(RosterHero("赛兹", "B", (0, 0)), RosterHero("露彬", "B", (0, 1)),
                           RosterHero("卡琳", "C"), RosterHero("度假风优芬妮", "B"))
        desired = {"赛兹": (0, 1), "卡琳": (0, 0), "度假风优芬妮": (6, 3)}
        actions = []
        for _ in range(8):
            step = next_step(state, desired)
            if step is None:
                break
            actions.append(step.kind)
            expected = derive_after(state, step)
            state = commit_step(state, step, self.scan(expected), self.cells(expected))
        self.assertEqual(actions[0], FormationKind.SWAP)
        self.assertIn(FormationKind.PLACE, actions)
        self.assertEqual({hero.name: hero.cell for hero in state.heroes if hero.cell is not None}, desired)
        self.assertEqual(self.scan(state).heroes, (BenchHero("露彬", Imprint.B),))

    def test_explicit_withdrawal_needs_free_bench_and_exact_after_identity(self):
        state = self.state(RosterHero("赛兹", "B", (6, 3)), RosterHero("卡琳", "C"))
        step = FormationStep(FormationKind.WITHDRAW, "赛兹", (6, 3), None, None, 0)
        expected = derive_after(state, step)
        actual = commit_step(state, step, self.scan(expected), ())
        self.assertEqual(actual.panel.deployed, 0)
        self.assertEqual({hero.name for hero in self.scan(actual).heroes}, {"赛兹", "卡琳"})
        extra = tuple(name for name in HEROES if name not in ("赛兹", "卡琳"))[:9]
        full = replace(state, heroes=(*state.heroes, *(RosterHero(name, "NONE") for name in extra)))
        with self.assertRaises(RuleInputError):
            derive_after(full, step)

    def test_forged_move_source_swap_target_and_duplicate_occupied_evidence_are_rejected(self):
        state = self.state(RosterHero("赛兹", "B", (0, 0)), RosterHero("卡琳", "C", (0, 1)),
                           RosterHero("露彬", "B"))
        invalid = (
            FormationStep(FormationKind.MOVE, "赛兹", (0, 1), (1, 1), None, 0),
            FormationStep(FormationKind.MOVE, "赛兹", (0, 0), (0, 1), None, 0),
            FormationStep(FormationKind.SWAP, "露彬", None, (0, 0), "卡琳", 0),
            FormationStep(FormationKind.PLACE, "赛兹", None, (1, 1), None, 0),
            FormationStep(FormationKind.PLACE, "露彬", None, (1, 1), "赛兹", 0),
            FormationStep("move", "赛兹", (0, 0), (1, 1), None, 0),
        )
        for step in invalid:
            with self.subTest(step=step), self.assertRaises(RuleInputError):
                derive_after(state, step)
        step = FormationStep(FormationKind.MOVE, "赛兹", (0, 0), (1, 1), None, 0)
        expected = derive_after(state, step)
        for occupied in (((1, 1), (0, 1), (0, 1)), ((0, 0), (0, 1))):
            with self.subTest(occupied=occupied), self.assertRaises(RuleInputError):
                commit_step(state, step, self.scan(expected), occupied)

    def test_new_bench_hero_needs_result_and_duplicate_result_is_idempotent(self):
        state = self.state(RosterHero("赛兹", "B", (0, 0)))
        after = replace(self.scan(state), heroes=(BenchHero("卡琳", Imprint.C),),
                        panel=replace(state.panel, coins=60))
        result = HeroResult("卡琳", Imprint.C, None)
        actual = reconcile(state, after, ((0, 0),), (result, result))
        self.assertEqual(actual.heroes, (*state.heroes, RosterHero("卡琳", "C")))
        self.assertEqual(actual.revision, 1)
        self.assertIs(reconcile(actual, after, ((0, 0),), (result,)), actual)
        for results in ((), (HeroResult("卡琳", Imprint.B, Imprint.C),),
                        (HeroResult("卡琳", Imprint.B, None),)):
            with self.subTest(results=results), self.assertRaises(RuleInputError):
                reconcile(state, after, ((0, 0),), results)

    def test_field_upgrade_chain_preserves_cell_and_repeated_chain_does_not_upgrade_again(self):
        state = self.state(RosterHero("赛兹", "B", (0, 0)))
        scan = self.scan(state)
        results = (HeroResult("赛兹", Imprint.A, Imprint.B),
                   HeroResult("赛兹", Imprint.A, Imprint.B),
                   HeroResult("赛兹", Imprint.S, Imprint.A))
        actual = reconcile(state, scan, ((0, 0),), results)
        self.assertEqual(actual.heroes, (RosterHero("赛兹", "S", (0, 0)),))
        self.assertIs(reconcile(actual, scan, ((0, 0),), results), actual)
        with self.assertRaises(RuleInputError):
            reconcile(state, scan, ((0, 0),), (HeroResult("赛兹", Imprint.S, None),))
        with self.assertRaises(RuleInputError):
            reconcile(state, scan, ((0, 0),), (HeroResult("赛兹", Imprint.S, Imprint.B),))

    def test_conflicting_result_chains_or_unconfirmed_new_name_never_update_roster(self):
        state = self.state(RosterHero("赛兹", "B", (0, 0)))
        scan = self.scan(state)
        invalid = (
            (HeroResult("赛兹", Imprint.A, Imprint.B), HeroResult("赛兹", Imprint.SS, Imprint.S)),
            (HeroResult("赛兹", Imprint.S, Imprint.A),),
            (HeroResult("卡琳", Imprint.C, None),),
            (HeroResult("赛兹", Imprint.A, None),),
            (HeroResult("露斌", Imprint.B, None),),
        )
        for results in invalid:
            with self.subTest(results=results), self.assertRaises(RuleInputError):
                reconcile(state, scan, ((0, 0),), results)
        self.assertEqual(state.heroes[0].imprint, Imprint.B)

    def test_bench_upgrade_needs_matching_result_and_rejects_downgrade_or_unread_imprint(self):
        state = self.state(RosterHero("卡琳", "C"))
        scan = replace(self.scan(state), heroes=(BenchHero("卡琳", Imprint.B),))
        with self.assertRaises(RuleInputError):
            reconcile(state, scan, ())
        actual = reconcile(state, scan, (), (HeroResult("卡琳", Imprint.B, Imprint.C),))
        self.assertEqual(actual.heroes[0].imprint, Imprint.B)
        with self.assertRaises(RuleInputError):
            reconcile(actual, self.scan(state), ())
        with self.assertRaises(RuleInputError):
            reconcile(state, scan, (), (HeroResult("卡琳", Imprint.C, None),))
        with self.assertRaises(RuleInputError):
            reconcile(state, scan, (), (HeroResult("卡琳", None, Imprint.C),))
        with self.assertRaises(RuleInputError):
            reconcile(state, replace(scan, heroes=(BenchHero("卡琳", None),)), ())

    def test_reconciliation_allows_next_round_level_and_resources_without_losing_positions(self):
        state = self.state(RosterHero("赛兹", "B", (6, 3)))
        panel = replace(state.panel, round_number=2, level=3, capacity=4, coins=110, synergy_tokens=3)
        after = replace(self.scan(state), panel=panel)
        actual = reconcile(state, after, ((6, 3),))
        self.assertEqual(actual.heroes, state.heroes)
        self.assertEqual(actual.panel, panel)
        self.assertEqual(actual.revision, 1)
        for scan in (replace(after, game_mode=BrawlMode.CHALLENGE),
                     replace(after, panel=replace(panel, round_number=3)),
                     replace(after, panel=replace(panel, level=4, capacity=5)),
                     replace(after, panel=replace(panel, level=1, capacity=2)),
                     replace(after, panel=replace(panel, deployed=0))):
            with self.subTest(scan=scan), self.assertRaises(RuleInputError):
                reconcile(state, scan, ((6, 3),))

    def test_reconciliation_rejects_missing_heroes_manual_location_changes_and_partial_bench(self):
        state = self.state(RosterHero("赛兹", "B", (0, 0)), RosterHero("卡琳", "C"))
        scan = self.scan(state)
        for after, occupied in ((replace(scan, heroes=()), ((0, 0),)),
                                (replace(scan, heroes=(*scan.heroes, BenchHero("赛兹", Imprint.B))), ((0, 0),)),
                                (replace(scan, outcome=ScanOutcome.TIMEOUT), ((0, 0),)),
                                (scan, ((0, 1),)), (scan, ())):
            with self.subTest(after=after, occupied=occupied), self.assertRaises(RuleInputError):
                reconcile(state, after, occupied)
        self.assertIs(reconcile(state, scan, ((0, 0),)), state)

    def test_scan_completion_and_identity_must_be_explicit_before_any_update(self):
        state = self.state(RosterHero("卡琳", "C"))
        scan = self.scan(state)
        step = FormationStep(FormationKind.PLACE, "卡琳", None, (0, 0), None, 0)
        expected = derive_after(state, step)
        for after in (replace(scan, outcome="complete"),
                      replace(scan, heroes=(BenchHero(["卡琳"], Imprint.C),)),
                      replace(scan, heroes=(BenchHero(None, Imprint.C),)),
                      replace(scan, heroes=[BenchHero("卡琳", Imprint.C)])):
            with self.subTest(after=after):
                with self.assertRaises(RuleInputError):
                    begin_roster(after)
                with self.assertRaises(RuleInputError):
                    reconcile(state, after, ())
                with self.assertRaises(RuleInputError):
                    commit_step(state, step, replace(after, panel=expected.panel), ((0, 0),))
        self.assertEqual(state.heroes, (RosterHero("卡琳", "C"),))

    def test_twenty_owned_heroes_can_replace_and_reposition_a_full_team(self):
        names = tuple(HEROES)[:20]
        initial_cells = tuple((index // 4, index % 4) for index in range(10))
        state = self.state(*(RosterHero(name, "NONE", cell)
                             for name, cell in zip(names[:10], initial_cells)),
                           *(RosterHero(name, "NONE") for name in names[10:]), level=9)
        desired_names = names[:5] + names[10:15]
        desired = dict(zip(desired_names, reversed(initial_cells)))
        original = {hero.name: hero.imprint for hero in state.heroes}
        actions = []
        for _ in range(30):
            step = next_step(state, desired)
            if step is None:
                break
            actions.append(step.kind)
            self.assertNotIn(step.kind, (FormationKind.PLACE, FormationKind.WITHDRAW))
            expected = derive_after(state, step)
            state = commit_step(state, step, self.scan(expected), self.cells(expected))
            self.assertEqual(state.panel.deployed, 10)
            self.assertEqual(len(self.scan(state).heroes), 10)
            self.assertEqual({hero.name: hero.imprint for hero in state.heroes}, original)
        self.assertEqual(actions.count(FormationKind.SWAP), 5)
        self.assertIn(FormationKind.MOVE, actions)
        self.assertIsNone(next_step(state, desired))
        self.assertEqual({hero.name: hero.cell for hero in state.heroes if hero.cell is not None}, desired)
        self.assertEqual(state.revision, len(actions))

    def test_acquisition_upgrade_chain_and_full_imprint_refund_preserve_exact_roster(self):
        state = self.state(RosterHero("赛兹", "SSS", (6, 3)), RosterHero("卡琳", "NONE"))
        scan = replace(self.scan(state), heroes=(BenchHero("卡琳", Imprint.C),
                                                 BenchHero("格伦", Imprint.D)))
        results = (HeroResult("格伦", Imprint.NONE, None),
                   HeroResult("卡琳", Imprint.C, Imprint.NONE),
                   HeroResult("赛兹", Imprint.SSS, Imprint.SSS),
                   HeroResult("格伦", Imprint.D, Imprint.NONE))
        actual = reconcile(state, scan, ((6, 3),), results)
        self.assertEqual(actual.heroes, (state.heroes[0], RosterHero("卡琳", "C"),
                                        RosterHero("格伦", "D")))
        self.assertEqual(actual.revision, 1)
        self.assertIs(reconcile(actual, scan, ((6, 3),), results), actual)
        self.assertIs(reconcile(actual, scan, ((6, 3),), results[1:]), actual)
        for bad_results in ((HeroResult("格伦", Imprint.D, Imprint.NONE),),
                            (*results, HeroResult("格伦", Imprint.C, Imprint.D)),
                            (*results, HeroResult("卡琳", Imprint.B, Imprint.C))):
            with self.subTest(results=bad_results), self.assertRaises(RuleInputError):
                reconcile(state, scan, ((6, 3),), bad_results)
