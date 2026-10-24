"""Opening policy checks with explicit child-operation outcomes, not live clicks."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.bench import BenchHero  # noqa: E402
from tasks.brawl.formation import (  # noqa: E402
    BrawlFormation,
    FormationOutcome,
    FormationResult,
)
from tasks.brawl.heroes import HeroResult  # noqa: E402
from tasks.brawl.normal_summon import NormalSummonRequest  # noqa: E402
from tasks.brawl.opening import BrawlOpening  # noqa: E402
from tasks.brawl.roster import RosterHero, RosterState, reconcile_transfer  # noqa: E402
from tasks.brawl.rules import (  # noqa: E402
    Imprint,
    Resources,
    RuleInputError,
    SummonKind,
)
from tasks.brawl.scanner import BenchScanResult, ScanOutcome  # noqa: E402
from tasks.brawl.summon import SummonOutcome, SummonReport, SummonRequest  # noqa: E402
from tasks.brawl.upgrade import UpgradeOutcome, UpgradeRequest  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlObservation,
    BrawlScreen,
    ObservationError,
    PreparationPanel,
)
from tests.support.brawl import BrawlReplay  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402

LORINA = BenchHero("萝里娜", Imprint.D)
CARMAINEROSE = BenchHero("卡麦萝兹", Imprint.D)
GLENN = BenchHero("格伦", Imprint.D)


def observation(panel, mode=BrawlMode.NORMAL):
    return BrawlObservation(BrawlScreen.PREPARATION, panel, game_mode=mode)


def scan(panel, heroes=(), *, mode=BrawlMode.NORMAL, outcome=ScanOutcome.COMPLETE):
    return BenchScanResult(heroes, panel, mode, outcome)


def operation(kind, result, after=None):
    return SimpleNamespace(kind=kind, result=result, after=after)


class OpeningPolicyTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def scenario(self, initial, operations, *, fresh=None):
        """Model child contracts; their verified return frames are supplied explicitly.

        These tests do not bypass or emulate purchase loops. Their own replay
        tests cover payment intervals and result dismissal. Here a child call
        may finish only with the prescribed outcome and observation, allowing
        policy assertions about the next permitted call and the full roster.
        """
        frames = [np.zeros((720, 1280, 3), np.uint8) for _ in range(2 if fresh else 1)]
        replay = BrawlReplay(BrawlOpening, frames, self.clock)
        self.frame = replay.image
        observations = {id(frames[0]): initial}
        if fresh is not None:
            observations[id(frames[1])] = fresh
        remaining = list(operations)
        calls = []
        modules = {}
        # Retain every supplied image so Python cannot reuse an earlier id.
        images = list(frames)

        def current(image):
            return observations[id(image)]

        def invoke(kind, *args, **kwargs):
            self.assertEqual(kwargs, {"skip_first_screenshot": True})
            calls.append((kind, args))
            self.assertTrue(remaining, f"Unexpected additional {kind} operation")
            expected = remaining.pop(0)
            self.assertEqual(kind, expected.kind)
            if expected.after is not None:
                image = np.zeros((720, 1280, 3), np.uint8)
                images.append(image)
                observations[id(image)] = expected.after
                replay.image = image
            if isinstance(expected.result, Exception):
                raise expected.result
            if callable(expected.result):
                return expected.result(*args, **kwargs)
            return expected.result

        def factory(kind, method):
            def create(config, device):
                child = SimpleNamespace(config=config, device=device, interval_timer={}, ACTION_TIMER=kind)
                setattr(child, method, Mock(side_effect=lambda *args, **kwargs: invoke(kind, *args, **kwargs)))
                modules[kind] = child
                return child
            return create

        capture = Mock(side_effect=lambda image: NormalSummonRequest(
            current(image).panel, current(image).game_mode))
        patches = (
            patch("tasks.brawl.opening.BrawlVision", side_effect=lambda image: SimpleNamespace(
                observe=lambda: current(image))),
            patch("tasks.brawl.opening.BrawlUpgrade", side_effect=factory("upgrade", "upgrade_once")),
            patch("tasks.brawl.opening.BrawlBenchScanner", side_effect=factory("scan", "scan")),
            patch("tasks.brawl.opening.BrawlNormalSummon", side_effect=factory("summon", "summon_once")),
            patch("tasks.brawl.opening.capture_normal_summon", capture),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return SimpleNamespace(task=replay.task, replay=replay, calls=calls, modules=modules,
                               remaining=remaining, capture=capture)

    def test_all_affordable_upgrades_precede_scanning_and_draws_without_a_reserve(self):
        first = PreparationPanel(1, 1, 0, 2, 100, 0)
        second = PreparationPanel(1, 2, 0, 3, 80, 0)
        third = PreparationPanel(1, 3, 0, 4, 30, 0)
        steps = [operation("upgrade", UpgradeOutcome.CONFIRMED, observation(second)),
                 operation("upgrade", UpgradeOutcome.CONFIRMED, observation(third)),
                 operation("scan", scan(third))]
        heroes = (LORINA, CARMAINEROSE, GLENN)
        for index, hero in enumerate(heroes, start=1):
            after = replace(third, coins=30 - index * 10)
            steps.extend((operation("summon", SummonReport(SummonOutcome.CONFIRMED,
                                                           HeroResult(hero.name, hero.imprint, None)),
                                    observation(after)),
                          operation("scan", scan(after, heroes[:index]))))
        replay = self.scenario(observation(first), steps)
        self.assertEqual(replay.task.prepare(), observation(replace(third, coins=0)))
        self.assertEqual([kind for kind, _ in replay.calls],
                         ["upgrade", "upgrade", "scan", "summon", "scan", "summon", "scan", "summon", "scan"])
        self.assertEqual([args[0] for kind, args in replay.calls if kind == "upgrade"],
                         [UpgradeRequest(first, BrawlMode.NORMAL), UpgradeRequest(second, BrawlMode.NORMAL)])
        self.assertEqual([args[0].panel.coins for kind, args in replay.calls if kind == "summon"], [30, 20, 10])
        self.assertFalse(replay.remaining)

    def test_duplicate_imprint_does_not_fill_a_slot_and_only_the_missing_unique_hero_is_bought(self):
        before = PreparationPanel(1, 2, 0, 3, 40, 0)
        duplicate = replace(before, coins=30)
        full = replace(before, coins=20)
        upgraded = replace(LORINA, imprint=Imprint.C)
        replay = self.scenario(observation(before), [
            operation("scan", scan(before, (LORINA, CARMAINEROSE))),
            operation("summon", SummonReport(SummonOutcome.CONFIRMED,
                                             HeroResult(LORINA.name, Imprint.C, Imprint.D)), observation(duplicate)),
            operation("scan", scan(duplicate, (upgraded, CARMAINEROSE))),
            operation("summon", SummonReport(SummonOutcome.CONFIRMED,
                                             HeroResult(GLENN.name, Imprint.D, None)), observation(full)),
            operation("scan", scan(full, (upgraded, CARMAINEROSE, GLENN))),
        ])
        self.assertEqual(replay.task.prepare(), observation(full))
        self.assertEqual([kind for kind, _ in replay.calls], ["scan", "summon", "scan", "summon", "scan"])
        self.assertEqual(replay.capture.call_count, 2)
        self.assertFalse(replay.remaining)

    def test_existing_full_roster_stops_after_complete_scan_without_spending(self):
        panel = PreparationPanel(1, 2, 0, 3, 49, 0)
        replay = self.scenario(observation(panel), [operation("scan", scan(panel, (LORINA, CARMAINEROSE, GLENN)))])
        self.assertEqual(replay.task.prepare(), observation(panel))
        self.assertEqual([kind for kind, _ in replay.calls], ["scan"])
        replay.capture.assert_not_called()

    def test_low_balance_returns_existing_heroes_but_rejects_an_empty_roster(self):
        for coins in (0, 9):
            for heroes in ((), (LORINA,)):
                with self.subTest(coins=coins, heroes=heroes):
                    panel = PreparationPanel(1, 2, 0, 3, coins, 0)
                    replay = self.scenario(observation(panel), [operation("scan", scan(panel, heroes))])
                    if heroes:
                        self.assertEqual(replay.task.prepare(), observation(panel))
                    else:
                        with self.assertRaises(ObservationError):
                            replay.task.prepare()
                    self.assertEqual([kind for kind, _ in replay.calls], ["scan"])
                    replay.capture.assert_not_called()

    def test_maximum_level_does_not_request_an_unsupported_upgrade(self):
        panel = PreparationPanel(1, 9, 0, 10, 0, 0)
        replay = self.scenario(observation(panel), [operation("scan", scan(panel, (LORINA,)))])
        self.assertEqual(replay.task.prepare(), observation(panel))
        self.assertEqual([kind for kind, _ in replay.calls], ["scan"])

    def test_children_share_device_configuration_and_the_same_action_gate(self):
        panel = PreparationPanel(1, 2, 0, 3, 0, 0)
        replay = self.scenario(observation(panel), [operation("scan", scan(panel, (LORINA,)))])
        replay.task.prepare()
        self.assertEqual(set(replay.modules), {"upgrade", "scan", "summon"})
        for child in replay.modules.values():
            self.assertIs(child.device, replay.task.device)
            self.assertIs(child.config, replay.task.config)
            self.assertIs(child.interval_timer, replay.task.interval_timer)
            self.assertEqual(child.ACTION_TIMER, "BrawlBenchScan")

    def test_nonopening_scenes_and_incomplete_context_cannot_start_children(self):
        panel = PreparationPanel(1, 2, 0, 3, 70, 0)
        for current in (observation(replace(panel, round_number=2)), observation(replace(panel, deployed=1)),
                        observation(panel, mode=None), observation(None),
                        BrawlObservation(BrawlScreen.HERO_RESULT, game_mode=BrawlMode.NORMAL)):
            with self.subTest(observation=current):
                replay = self.scenario(current, [])
                with self.assertRaises(ObservationError):
                    replay.task.prepare()
                self.assertFalse(replay.calls)
                self.assertFalse(replay.modules)

    def test_requested_fresh_first_frame_replaces_a_stale_spendable_panel(self):
        stale = observation(PreparationPanel(1, 2, 0, 3, 70, 0))
        fresh = BrawlObservation(BrawlScreen.COMBAT, game_mode=BrawlMode.NORMAL)
        replay = self.scenario(stale, [], fresh=fresh)
        with self.assertRaises(ObservationError):
            replay.task.prepare(skip_first_screenshot=False)
        self.assertEqual(replay.replay.index, 1)
        self.assertFalse(replay.calls)

    def test_unconfirmed_upgrade_never_scans_or_buys_heroes(self):
        panel = PreparationPanel(1, 2, 0, 3, 70, 0)
        for outcome in (UpgradeOutcome.TIMEOUT, UpgradeOutcome.BALANCE_MISMATCH, UpgradeOutcome.CONTEXT_CHANGED):
            with self.subTest(outcome=outcome):
                replay = self.scenario(observation(panel), [operation("upgrade", outcome)])
                with self.assertRaises(ObservationError):
                    replay.task.prepare()
                self.assertEqual([kind for kind, _ in replay.calls], ["upgrade"])

    def test_upgrade_confirmation_requires_matching_level_capacity_debit_and_mode(self):
        before = PreparationPanel(1, 2, 0, 3, 70, 0)
        after = PreparationPanel(1, 3, 0, 4, 20, 0)
        for observed in (observation(before), observation(replace(after, coins=21)),
                         observation(replace(after, capacity=3)), observation(replace(after, synergy_tokens=1)),
                         observation(after, BrawlMode.CHALLENGE)):
            with self.subTest(observed=observed):
                replay = self.scenario(observation(before), [operation("upgrade", UpgradeOutcome.CONFIRMED, observed)])
                with self.assertRaises(ObservationError):
                    replay.task.prepare()
                self.assertEqual([kind for kind, _ in replay.calls], ["upgrade"])

    def test_incomplete_or_context_changed_initial_scan_never_authorizes_a_draw(self):
        panel = PreparationPanel(1, 2, 0, 3, 20, 0)
        for result in (scan(panel, (LORINA,), outcome=ScanOutcome.TIMEOUT),
                       scan(replace(panel, coins=19), (LORINA,)), scan(panel, (LORINA,), mode=BrawlMode.CHALLENGE),
                       scan(panel, (LORINA, LORINA))):
            with self.subTest(scan=result):
                replay = self.scenario(observation(panel), [operation("scan", result)])
                with self.assertRaises(ObservationError):
                    replay.task.prepare()
                self.assertEqual([kind for kind, _ in replay.calls], ["scan"])
                replay.capture.assert_not_called()

    def test_unconfirmed_or_missing_summon_result_stops_without_another_action(self):
        panel = PreparationPanel(1, 2, 0, 3, 20, 0)
        for report in (SummonReport(SummonOutcome.TIMEOUT), SummonReport(SummonOutcome.CONFIRMED),
                       SummonReport(SummonOutcome.BALANCE_MISMATCH, HeroResult(LORINA.name, Imprint.D, None))):
            with self.subTest(report=report):
                replay = self.scenario(observation(panel), [operation("scan", scan(panel)), operation("summon", report)])
                with self.assertRaises(ObservationError):
                    replay.task.prepare()
                self.assertEqual([kind for kind, _ in replay.calls], ["scan", "summon"])

    def test_summon_return_requires_exact_coin_debit_and_unchanged_context(self):
        before = PreparationPanel(1, 2, 0, 3, 20, 0)
        after = replace(before, coins=10)
        hero = HeroResult(LORINA.name, Imprint.D, None)
        for observed in (observation(before), observation(replace(after, coins=9)),
                         observation(replace(after, synergy_tokens=1)), observation(replace(after, level=3, capacity=4)),
                         observation(after, BrawlMode.CHALLENGE)):
            with self.subTest(observed=observed):
                replay = self.scenario(observation(before), [operation("scan", scan(before)),
                                      operation("summon", SummonReport(SummonOutcome.CONFIRMED, hero), observed)])
                with self.assertRaises(ObservationError):
                    replay.task.prepare()
                self.assertEqual([kind for kind, _ in replay.calls], ["scan", "summon"])

    def test_new_name_or_imprint_must_match_the_entire_post_summon_bench(self):
        before = PreparationPanel(1, 2, 0, 3, 20, 0)
        after = replace(before, coins=10)
        result = HeroResult(CARMAINEROSE.name, Imprint.D, None)
        for result_scan in (scan(after, (LORINA, GLENN)), scan(after, (CARMAINEROSE,)),
                            scan(after, (LORINA, replace(CARMAINEROSE, imprint=Imprint.C))),
                            scan(after, (LORINA, CARMAINEROSE), outcome=ScanOutcome.TIMEOUT)):
            with self.subTest(scan=result_scan):
                replay = self.scenario(observation(before), [operation("scan", scan(before, (LORINA,))),
                                      operation("summon", SummonReport(SummonOutcome.CONFIRMED, result), observation(after)),
                                      operation("scan", result_scan)])
                with self.assertRaises(ObservationError):
                    replay.task.prepare()
                self.assertEqual([kind for kind, _ in replay.calls], ["scan", "summon", "scan"])
                self.assertEqual(replay.capture.call_count, 1)

    def test_capture_changed_since_the_full_scan_prevents_payment(self):
        panel = PreparationPanel(1, 2, 0, 3, 20, 0)
        replay = self.scenario(observation(panel), [operation("scan", scan(panel, (LORINA,)))])
        replay.capture.side_effect = None
        replay.capture.return_value = NormalSummonRequest(replace(panel, coins=30), BrawlMode.NORMAL)
        with self.assertRaises(ObservationError):
            replay.task.prepare()
        self.assertEqual([kind for kind, _ in replay.calls], ["scan"])

    def test_paid_duplicate_must_advance_the_known_imprint(self):
        before = PreparationPanel(1, 2, 0, 3, 20, 0)
        after = replace(before, coins=10)
        for previous in (None, Imprint.D):
            with self.subTest(previous=previous):
                report = SummonReport(SummonOutcome.CONFIRMED, HeroResult(LORINA.name, Imprint.D, previous))
                replay = self.scenario(observation(before), [
                    operation("scan", scan(before, (LORINA,))),
                    operation("summon", report, observation(after)),
                    operation("scan", scan(after, (LORINA,))),
                ])
                with self.assertRaisesRegex(ObservationError, "paid draw did not advance the known hero imprint"):
                    replay.task.prepare()
                self.assertEqual([kind for kind, _ in replay.calls], ["scan", "summon", "scan"])
                self.assertEqual(replay.capture.call_count, 1)
                self.assertFalse(replay.remaining)

    def test_final_scan_must_leave_the_same_stable_preparation_for_formation(self):
        panel = PreparationPanel(1, 2, 0, 3, 0, 0)
        for after in (observation(replace(panel, coins=10)),
                      BrawlObservation(BrawlScreen.FORMATION_SELECTION, game_mode=BrawlMode.NORMAL)):
            with self.subTest(after=after):
                replay = self.scenario(observation(panel), [operation("scan", scan(panel, (LORINA,)), after)])
                with self.assertRaises(ObservationError):
                    replay.task.prepare()
                self.assertEqual([kind for kind, _ in replay.calls], ["scan"])


class EconomyEvidenceTests(unittest.TestCase):
    """Economic commits need fresh complete evidence and one pending owner."""

    @staticmethod
    def state():
        return RosterState((RosterHero("赛兹", Imprint.B, (0, 0)),
                            RosterHero("萝里娜", Imprint.C), RosterHero("卡麦萝兹", Imprint.D)),
                           PreparationPanel(3, 2, 1, 3, 30, 3), BrawlMode.NORMAL, 8)

    @staticmethod
    def evidence(state, panel=None, heroes=None):
        members = tuple(BenchHero(hero.name, hero.imprint) for hero in state.heroes if hero.cell is None)
        return scan(panel or state.panel, members if heroes is None else heroes, mode=state.game_mode)

    def formation(self, state=None):
        state = state or self.state()
        task = object.__new__(BrawlFormation)
        task._roster = state
        task._valid = True
        task._scan_result = self.evidence(state)
        task._opening_evidence = object()
        task.scan = Mock(return_value=task._scan_result)
        task._probe = Mock(return_value=frozenset(hero.cell for hero in state.heroes if hero.cell is not None))
        return task

    def test_transfer_removes_explicit_bench_before_same_name_is_granted_again(self):
        before = self.state()
        after = self.evidence(before, replace(before.panel, coins=40, synergy_tokens=4),
                              (CARMAINEROSE, LORINA))
        result = reconcile_transfer(before, after, ((0, 0),), (LORINA.name,),
                                    (HeroResult(LORINA.name, Imprint.D, None),))
        self.assertEqual({hero.name: hero.imprint for hero in result.heroes},
                         {"赛兹": Imprint.B, LORINA.name: Imprint.D, CARMAINEROSE.name: Imprint.D})
        self.assertEqual(result.heroes[0].cell, (0, 0))
        self.assertEqual(result.revision, before.revision + 1)
        self.assertEqual(before.heroes[1].imprint, Imprint.C)

    def test_identical_transfer_gift_still_invalidates_the_previous_quote_revision(self):
        before = self.state()
        result = reconcile_transfer(before, self.evidence(before), ((0, 0),), (CARMAINEROSE.name,),
                                    (HeroResult(CARMAINEROSE.name, Imprint.D, None),))
        self.assertEqual(result.heroes, before.heroes)
        self.assertEqual(result.panel, before.panel)
        self.assertEqual(result.revision, before.revision + 1)

    def test_transfer_never_infers_unlisted_missing_heroes_or_unseen_gifts(self):
        before = self.state()
        after = self.evidence(before, heroes=(CARMAINEROSE,))
        bad = (
            (replace(after, heroes=()), ()),
            (replace(after, heroes=(CARMAINEROSE, GLENN)), ()),
            (replace(after, heroes=(CARMAINEROSE, LORINA)), ()),
            (replace(after, heroes=(CARMAINEROSE, LORINA)),
             (HeroResult(LORINA.name, Imprint.D, Imprint.NONE),)),
        )
        for observed, results in bad:
            with self.subTest(observed=observed, results=results), self.assertRaises(RuleInputError):
                reconcile_transfer(before, observed, ((0, 0),), (LORINA.name,), results)
        actual = reconcile_transfer(before, after, ((0, 0),), (LORINA.name,))
        self.assertEqual(tuple(hero.name for hero in actual.heroes), ("赛兹", CARMAINEROSE.name))

    def test_transfer_requires_one_to_five_unique_owned_bench_identities(self):
        before = self.state()
        for removed in ((), [LORINA.name], (LORINA.name, LORINA.name), ("赛兹",), ("格伦",),
                        ("萝里娜", "卡麦萝兹", "格伦", "卡萝", "卡琳", "露彬")):
            with self.subTest(removed=removed), self.assertRaises(RuleInputError):
                reconcile_transfer(before, self.evidence(before), ((0, 0),), removed)

    def test_transfer_cannot_move_field_change_round_or_hide_an_incomplete_scan(self):
        before = self.state()
        after = self.evidence(before, heroes=(CARMAINEROSE,))
        variants = (
            (replace(after, panel=replace(before.panel, round_number=4)), ((0, 0),)),
            (replace(after, panel=replace(before.panel, level=3, capacity=4)), ((0, 0),)),
            (replace(after, panel=replace(before.panel, deployed=0)), ((0, 0),)),
            (replace(after, game_mode=BrawlMode.CHALLENGE), ((0, 0),)),
            (replace(after, outcome=ScanOutcome.TIMEOUT), ((0, 0),)),
            (replace(after, panel=None), ((0, 0),)),
            (after, ((0, 1),)),
        )
        for observed, occupied in variants:
            with self.subTest(observed=observed, occupied=occupied), self.assertRaises(RuleInputError):
                reconcile_transfer(before, observed, occupied, (LORINA.name,))

    def test_begin_economy_locks_formation_and_scan_until_exact_confirmation(self):
        task = self.formation()
        before = task.begin_economy()
        self.assertIs(before, task.roster)
        self.assertFalse(task.valid)
        self.assertIsNone(task._opening_evidence)
        with self.assertRaises(ObservationError):
            _ = task.verified_scan
        with self.assertRaises(ObservationError):
            task.begin_economy()
        result = task.confirm_economy(before, before.panel)
        self.assertEqual(result.outcome, FormationOutcome.READY)
        self.assertTrue(task.valid)
        self.assertIs(task.verified_scan, task.scan.return_value)
        task.scan.assert_called_once_with(skip_first_screenshot=True)
        task._probe.assert_called_once_with(task.scan.return_value, frozenset({(0, 0)}))
        self.assertIsNone(task._economy_before)

    def test_confirmation_rejects_equal_but_unowned_or_reused_transaction(self):
        task = self.formation()
        before = task.begin_economy()
        result = task.confirm_economy(replace(before), before.panel)
        self.assertEqual(result.outcome, FormationOutcome.FAILED)
        self.assertFalse(task.valid)
        task.scan.assert_not_called()
        task = self.formation()
        before = task.begin_economy()
        self.assertEqual(task.confirm_economy(before, before.panel).outcome, FormationOutcome.READY)
        self.assertEqual(task.confirm_economy(before, before.panel).outcome, FormationOutcome.FAILED)
        self.assertEqual(task.scan.call_count, 1)

    def test_exact_balance_or_context_mismatch_fails_before_any_grid_probe(self):
        for changed in ("coins", "synergy_tokens", "round_number"):
            with self.subTest(field=changed):
                task = self.formation()
                before = task.begin_economy()
                wrong = replace(before.panel, **{changed: getattr(before.panel, changed) + 1})
                task.scan.return_value = self.evidence(before, wrong)
                result = task.confirm_economy(before, before.panel)
                self.assertEqual(result.outcome, FormationOutcome.FAILED)
                self.assertFalse(task.valid)
                self.assertIs(task.roster, before)
                self.assertIsNone(task._economy_before)
                task._probe.assert_not_called()

    def test_confirmed_sale_retains_only_observed_names_and_requires_fresh_occupied_cells(self):
        for occupied in (frozenset({(0, 0)}), frozenset({(1, 0)})):
            with self.subTest(occupied=occupied):
                task = self.formation()
                before = task.begin_economy()
                expected = replace(before.panel, coins=40, synergy_tokens=4)
                task.scan.return_value = self.evidence(before, expected, (CARMAINEROSE, GLENN))
                task._probe.return_value = occupied
                result = task.confirm_economy(before, expected, removed=(LORINA.name,),
                                              hero_results=(HeroResult(GLENN.name, Imprint.D, None),),
                                              skip_first_screenshot=False)
                task.scan.assert_called_once_with(skip_first_screenshot=False)
                if occupied == frozenset({(0, 0)}):
                    self.assertEqual(result.outcome, FormationOutcome.READY)
                    self.assertEqual({hero.name for hero in task.roster.heroes},
                                     {"赛兹", CARMAINEROSE.name, GLENN.name})
                    self.assertEqual(task.roster.revision, before.revision + 1)
                else:
                    self.assertEqual(result.outcome, FormationOutcome.FAILED)
                    self.assertIs(task.roster, before)
                    self.assertFalse(task.valid)


class EconomyOrchestrationTests(unittest.TestCase):
    """Use real economy choices and roster commits around explicit child results."""

    @staticmethod
    def state(*heroes, coins=0, tokens=0, level=2, round_number=3):
        members = heroes or (RosterHero("赛兹", Imprint.B, (0, 0)),
                             RosterHero("萝里娜", Imprint.D, (0, 1)),
                             RosterHero("卡麦萝兹", Imprint.D, (0, 2)))
        return RosterState(tuple(members), PreparationPanel(round_number, level,
                           sum(hero.cell is not None for hero in members), level + 1, coins, tokens),
                           BrawlMode.NORMAL, 2)

    def scenario(self, before, operations):
        from tasks.brawl.economy import BrawlEconomy

        task = object.__new__(BrawlEconomy)
        task.config = SimpleNamespace()
        task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), np.uint8), screenshot=Mock())
        task.interval_timer = {}
        current = SimpleNamespace(state=before)
        formation = EconomyEvidenceTests().formation(before)
        formation.scan.side_effect = lambda **kwargs: EconomyEvidenceTests.evidence(current.state)
        formation._probe.side_effect = lambda *args: frozenset(
            hero.cell for hero in current.state.heroes if hero.cell is not None)
        calls, modules = [], {}
        remaining = list(operations)

        def arrange(bonuses, **kwargs):
            self.assertEqual(bonuses, {})
            self.assertEqual(kwargs, {"skip_first_screenshot": True})
            self.assertTrue(formation.valid, "An invalid transaction cannot start formation")
            self.assertEqual(formation.roster.panel, current.state.panel)
            state = formation.roster
            # Placement itself has separate replays. This fake returns an exact
            # arranged roster while the actual economic commit still requires
            # the supplied scan, occupied cells, results and resource changes.
            members = tuple(replace(hero, cell=(index // 4, index % 4) if index < state.panel.capacity else None)
                            for index, hero in enumerate(state.heroes))
            arranged = replace(state, heroes=members,
                               panel=replace(state.panel, deployed=min(len(members), state.panel.capacity)))
            if arranged != state:
                arranged = replace(arranged, revision=state.revision + 1)
            current.state = formation._roster = arranged
            formation._scan_result = EconomyEvidenceTests.evidence(arranged)
            return FormationResult(FormationOutcome.COMPLETE, arranged)

        formation.arrange = Mock(side_effect=arrange)
        original_confirm = formation.confirm_economy
        formation.confirm_economy = Mock(wraps=original_confirm)

        def invoke(kind, method, *args, **kwargs):
            calls.append((kind, method, args, kwargs))
            self.assertEqual(kwargs.get("skip_first_screenshot"), True)
            self.assertFalse(formation.valid, "Every external action must hold the transaction lock")
            self.assertTrue(remaining, f"Unexpected {kind}.{method}")
            expected = remaining.pop(0)
            self.assertEqual((kind, method), expected.kind)
            if expected.after is not None:
                current.state = expected.after
            if isinstance(expected.result, Exception):
                raise expected.result
            if callable(expected.result):
                return expected.result(*args, **kwargs)
            return expected.result

        def factory(kind, methods):
            def create(config, device):
                child = SimpleNamespace(config=config, device=device, interval_timer={}, ACTION_TIMER=kind)
                for method in methods:
                    setattr(child, method, Mock(side_effect=lambda *args, _method=method, **kwargs:
                                               invoke(kind, _method, *args, **kwargs)))
                modules[kind] = child
                return child
            return create

        normal_capture = Mock(side_effect=lambda image: NormalSummonRequest(
            current.state.panel, current.state.game_mode))
        upper_capture = Mock(side_effect=lambda image, kind: SummonRequest(
            current.state.panel, current.state.game_mode, kind))
        for item in (
            patch("tasks.brawl.economy.BrawlVision", side_effect=lambda image: SimpleNamespace(
                observe=lambda: observation(current.state.panel, current.state.game_mode))),
            patch("tasks.brawl.economy.BrawlUpgrade", side_effect=factory("upgrade", ("upgrade_once",))),
            patch("tasks.brawl.economy.BrawlNormalSummon", side_effect=factory("normal", ("summon_once",))),
            patch("tasks.brawl.economy.BrawlSummon", side_effect=factory("upper", ("summon_once",))),
            patch("tasks.brawl.economy.BrawlAutoTransfer", side_effect=factory("transfer", ("preview", "finish"))),
            patch("tasks.brawl.economy.capture_normal_summon", normal_capture),
            patch("tasks.brawl.economy.capture_summon", upper_capture),
        ):
            item.start()
            self.addCleanup(item.stop)
        return SimpleNamespace(task=task, formation=formation, calls=calls, remaining=remaining,
                               current=current, modules=modules, normal_capture=normal_capture,
                               upper_capture=upper_capture)

    def test_affordable_upgrade_precedes_forty_token_draw_and_keeps_full_roster_coins(self):
        before = self.state(coins=60, tokens=40)
        upgraded = replace(before, panel=replace(before.panel, level=3, capacity=4, coins=10))
        drawn = replace(upgraded, heroes=(*before.heroes, RosterHero("格伦", Imprint.D)),
                        panel=replace(upgraded.panel, synergy_tokens=0))
        replay = self.scenario(before, [
            operation(("upgrade", "upgrade_once"), UpgradeOutcome.CONFIRMED, upgraded),
            operation(("upper", "summon_once"),
                      SummonReport(SummonOutcome.CONFIRMED, HeroResult("格伦", Imprint.D, None)), drawn),
        ])
        result = replay.task.prepare(replay.formation, {})
        self.assertEqual(result.outcome, FormationOutcome.COMPLETE)
        self.assertEqual([(kind, method) for kind, method, _, _ in replay.calls],
                         [("upgrade", "upgrade_once"), ("upper", "summon_once")])
        self.assertEqual(result.roster.panel, PreparationPanel(3, 3, 4, 4, 10, 0))
        replay.normal_capture.assert_not_called()
        self.assertEqual(replay.upper_capture.call_args.args[1], SummonKind.UPPER)
        self.assertEqual(replay.formation.confirm_economy.call_count, 2)
        self.assertEqual(replay.formation.arrange.call_count, 3)
        self.assertFalse(replay.remaining)

    def test_full_team_saves_coins_and_subforty_tokens_without_any_payment(self):
        replay = self.scenario(self.state(coins=49, tokens=39), [])
        result = replay.task.prepare(replay.formation, {})
        self.assertEqual(result.outcome, FormationOutcome.COMPLETE)
        self.assertEqual((result.roster.panel.coins, result.roster.panel.synergy_tokens), (49, 39))
        self.assertFalse(replay.calls)
        replay.normal_capture.assert_not_called()
        replay.upper_capture.assert_not_called()
        replay.formation.confirm_economy.assert_not_called()

    def test_missing_members_draw_again_after_duplicate_and_stop_when_unique_count_is_full(self):
        before = self.state(RosterHero("萝里娜", Imprint.D, (0, 0)),
                            RosterHero("卡麦萝兹", Imprint.D, (0, 1)), coins=30, tokens=39)
        duplicate = replace(before, heroes=(replace(before.heroes[0], imprint=Imprint.C), before.heroes[1]),
                            panel=replace(before.panel, coins=20))
        full = replace(duplicate, heroes=(*duplicate.heroes, RosterHero("格伦", Imprint.D)),
                       panel=replace(duplicate.panel, coins=10))
        replay = self.scenario(before, [
            operation(("normal", "summon_once"),
                      SummonReport(SummonOutcome.CONFIRMED, HeroResult("萝里娜", Imprint.C, Imprint.D)), duplicate),
            operation(("normal", "summon_once"),
                      SummonReport(SummonOutcome.CONFIRMED, HeroResult("格伦", Imprint.D, None)), full),
        ])
        result = replay.task.prepare(replay.formation, {})
        self.assertEqual(result.outcome, FormationOutcome.COMPLETE)
        self.assertEqual(len(result.roster.heroes), 3)
        self.assertEqual(result.roster.panel, PreparationPanel(3, 2, 3, 3, 10, 39))
        self.assertEqual([args[0].panel.coins for _, _, args, _ in replay.calls], [30, 20])
        self.assertFalse(replay.remaining)

    def test_stale_or_missing_paid_result_invalidates_before_scan_or_another_payment(self):
        before = self.state(RosterHero("萝里娜", Imprint.D, (0, 0)), coins=20)
        for report in (SummonReport(SummonOutcome.CONFIRMED), SummonReport(SummonOutcome.TIMEOUT),
                       SummonReport(SummonOutcome.CONFIRMED, HeroResult("萝里娜", Imprint.D, None)),
                       SummonReport(SummonOutcome.CONFIRMED, HeroResult("萝里娜", Imprint.D, Imprint.D))):
            with self.subTest(report=report):
                replay = self.scenario(before, [operation(("normal", "summon_once"), report)])
                result = replay.task.prepare(replay.formation, {})
                self.assertEqual(result.outcome, FormationOutcome.FAILED)
                self.assertFalse(replay.formation.valid)
                self.assertEqual(len(replay.calls), 1)
                replay.formation.confirm_economy.assert_not_called()
                self.assertEqual(replay.formation.arrange.call_count, 1)

    def test_unconfirmed_upgrade_does_not_unlock_or_request_the_available_upper_draw(self):
        for outcome in (UpgradeOutcome.TIMEOUT, UpgradeOutcome.BALANCE_MISMATCH, UpgradeOutcome.CONTEXT_CHANGED):
            with self.subTest(outcome=outcome):
                replay = self.scenario(self.state(coins=60, tokens=40), [
                    operation(("upgrade", "upgrade_once"), outcome)])
                result = replay.task.prepare(replay.formation, {})
                self.assertEqual(result.outcome, FormationOutcome.FAILED)
                self.assertFalse(replay.formation.valid)
                replay.upper_capture.assert_not_called()
                replay.formation.confirm_economy.assert_not_called()

    def test_paid_result_with_wrong_return_balance_stops_before_rearrangement(self):
        before = self.state(RosterHero("萝里娜", Imprint.D, (0, 0)), coins=20)
        wrong = replace(before, heroes=(*before.heroes, RosterHero("格伦", Imprint.D)),
                        panel=replace(before.panel, coins=11))
        replay = self.scenario(before, [operation(("normal", "summon_once"),
            SummonReport(SummonOutcome.CONFIRMED, HeroResult("格伦", Imprint.D, None)), wrong)])
        result = replay.task.prepare(replay.formation, {})
        self.assertEqual(result.outcome, FormationOutcome.FAILED)
        self.assertFalse(replay.formation.valid)
        self.assertEqual(replay.formation.arrange.call_count, 1)
        replay.formation._probe.assert_not_called()

    def test_changed_capture_cannot_pay_and_all_children_share_the_formation_action_gate(self):
        before = self.state(RosterHero("萝里娜", Imprint.D, (0, 0)), coins=20)
        replay = self.scenario(before, [])
        replay.normal_capture.side_effect = None
        replay.normal_capture.return_value = NormalSummonRequest(replace(before.panel, coins=30), before.game_mode)
        result = replay.task.prepare(replay.formation, {})
        self.assertEqual(result.outcome, FormationOutcome.FAILED)
        self.assertFalse(replay.calls)
        for child in replay.modules.values():
            self.assertIs(child.config, replay.task.config)
            self.assertIs(child.device, replay.task.device)
            self.assertIs(child.interval_timer, replay.task.interval_timer)
            self.assertEqual(child.ACTION_TIMER, "BrawlBenchScan")

    def test_challenge_mode_and_invalid_sessions_cannot_rearrange_or_spend(self):
        for mode, valid in ((BrawlMode.CHALLENGE, True), (BrawlMode.NORMAL, False)):
            with self.subTest(mode=mode, valid=valid):
                replay = self.scenario(replace(self.state(coins=100, tokens=40), game_mode=mode), [])
                replay.formation._valid = valid
                result = replay.task.prepare(replay.formation, {})
                self.assertEqual(result.outcome, FormationOutcome.FAILED)
                replay.formation.arrange.assert_not_called()
                self.assertFalse(replay.modules)
                self.assertFalse(replay.calls)

    @staticmethod
    def preview(before, names, reward):
        return SimpleNamespace(names=names, header=before.panel, game_mode=before.game_mode, quote=reward)

    @staticmethod
    def transfer_report(before, names, reward, *, committed=False, after=None, gifts=()):
        return SimpleNamespace(names=names, panel=(after or before).panel,
                               game_mode=before.game_mode, quote=reward,
                               committed=committed, hero_results=gifts)

    def scripted_planner(self, choices):
        from tasks.brawl.economy_policy import EconomyPlan

        remaining = list(choices)

        def decide(state, bonuses, **kwargs):
            self.assertTrue(remaining, "Unexpected extra economic decision")
            action, targets, reward = remaining.pop(0)
            return EconomyPlan(action, "explicit_orchestration_test", state.revision,
                               targets=targets, expected_reward=reward)

        planner = Mock(side_effect=decide)
        item = patch("tasks.brawl.economy.plan_economy", planner)
        item.start()
        self.addCleanup(item.stop)
        return planner, remaining

    def test_each_quote_is_cancelled_and_reverified_before_batch_sale_and_upgrade(self):
        from tasks.brawl.economy_policy import EconomyAction

        initial = self.state(coins=30)
        before = replace(initial, heroes=(*initial.heroes, RosterHero("格伦", Imprint.D),
                                          RosterHero("卡萝", Imprint.D), RosterHero("卡琳", Imprint.C)))
        names, reward = ("格伦", "卡萝"), Resources(20, 2)
        sold = replace(before, heroes=tuple(hero for hero in before.heroes if hero.name not in names),
                       panel=replace(before.panel, coins=50, synergy_tokens=2))
        upgraded = replace(sold, panel=replace(sold.panel, level=3, capacity=4, coins=0))
        steps = []
        for name in names:
            preview = self.preview(before, (name,), Resources(10, 1))
            steps.extend((operation(("transfer", "preview"), preview),
                          operation(("transfer", "finish"),
                                    self.transfer_report(before, (name,), preview.quote))))
        preview = self.preview(before, names, reward)
        steps.extend((operation(("transfer", "preview"), preview),
                      operation(("transfer", "finish"),
                                self.transfer_report(before, names, reward, committed=True, after=sold), sold),
                      operation(("upgrade", "upgrade_once"), UpgradeOutcome.CONFIRMED, upgraded)))
        replay = self.scenario(before, steps)
        planner, choices = self.scripted_planner([
            (EconomyAction.QUOTE_TRANSFER, names, Resources(0, 0)),
            (EconomyAction.TRANSFER, names, reward),
            (EconomyAction.UPGRADE, (), Resources(0, 0)),
            (EconomyAction.SAVE, (), Resources(0, 0)),
        ])
        result = replay.task.prepare(replay.formation, {})
        self.assertEqual(result.outcome, FormationOutcome.COMPLETE)
        self.assertEqual(result.roster.panel, PreparationPanel(3, 3, 4, 4, 0, 2))
        self.assertEqual([kwargs["commit"] for kind, method, _, kwargs in replay.calls if method == "finish"],
                         [False, False, True])
        self.assertEqual([call.kwargs["transfer_budget"] for call in planner.call_args_list], [5, 5, 3, 5])
        self.assertEqual(planner.call_args_list[1].kwargs["observed_quotes"],
                         {name: Resources(10, 1) for name in names})
        self.assertEqual(planner.call_args_list[1].kwargs["quote_revision"], before.revision)
        self.assertIsNone(planner.call_args_list[2].kwargs["observed_quotes"])
        self.assertIsNone(planner.call_args_list[3].kwargs["observed_quotes"])
        self.assertEqual(replay.formation.confirm_economy.call_count, 4)
        self.assertEqual(replay.formation.confirm_economy.call_args_list[2].kwargs["removed"], names)
        self.assertFalse(replay.remaining)
        self.assertFalse(choices)

    def test_changed_final_quote_is_cancelled_without_sale_or_new_action(self):
        from tasks.brawl.economy_policy import EconomyAction

        initial = self.state(coins=30)
        before = replace(initial, heroes=(*initial.heroes, RosterHero("格伦", Imprint.D)))
        actual = Resources(19, 1)
        preview = self.preview(before, ("格伦",), actual)
        replay = self.scenario(before, [
            operation(("transfer", "preview"), preview),
            operation(("transfer", "finish"), self.transfer_report(before, ("格伦",), actual)),
        ])
        self.scripted_planner([(EconomyAction.TRANSFER, ("格伦",), Resources(20, 1))])
        result = replay.task.prepare(replay.formation, {})
        self.assertEqual(result.outcome, FormationOutcome.FAILED)
        self.assertFalse(replay.calls[-1][3]["commit"])
        replay.formation.confirm_economy.assert_not_called()
        self.assertFalse(replay.formation.valid)
        # The scenario's arrangement returns an equivalent immutable roster
        # object. Identity is not part of the no-sale contract; no field may
        # differ and the revision must remain unchanged.
        self.assertEqual(replay.formation.roster, before)

    def test_quote_cancellation_cannot_hide_hero_rewards_or_a_changed_roster(self):
        from tasks.brawl.economy_policy import EconomyAction

        initial = self.state(coins=30)
        before = replace(initial, heroes=(*initial.heroes, RosterHero("格伦", Imprint.D)))
        reward = Resources(20, 1)
        preview = self.preview(before, ("格伦",), reward)
        replay = self.scenario(before, [
            operation(("transfer", "preview"), preview),
            operation(("transfer", "finish"), self.transfer_report(before, ("格伦",), reward,
                      gifts=(HeroResult("卡琳", Imprint.C, None),))),
        ])
        self.scripted_planner([(EconomyAction.QUOTE_TRANSFER, ("格伦",), Resources(0, 0))])
        result = replay.task.prepare(replay.formation, {})
        self.assertEqual(result.outcome, FormationOutcome.FAILED)
        self.assertFalse(replay.formation.valid)
        replay.formation.confirm_economy.assert_not_called()

    def test_transfer_gift_refreshes_identity_and_persistent_allowance_without_resetting_it(self):
        from tasks.brawl.economy_policy import EconomyAction

        initial = self.state(coins=0)
        before = replace(initial, heroes=(*initial.heroes, RosterHero("格伦", Imprint.C)))
        reward = Resources(1, 1)
        gift = HeroResult("格伦", Imprint.D, None)
        after = replace(before, heroes=(*initial.heroes, RosterHero("格伦", Imprint.D)),
                        panel=replace(before.panel, coins=1, synergy_tokens=1))
        preview = self.preview(before, ("格伦",), reward)
        replay = self.scenario(before, [
            operation(("transfer", "preview"), preview),
            operation(("transfer", "finish"), self.transfer_report(before, ("格伦",), reward,
                      committed=True, after=after, gifts=(gift,)), after),
        ])
        # Four earlier sales belong to this same funding level. Re-entering
        # preparation or receiving a new copy must not grant five more sales.
        replay.task._funding_level = before.panel.level
        replay.task._transferred = 4
        planner, choices = self.scripted_planner([
            (EconomyAction.TRANSFER, ("格伦",), reward),
            (EconomyAction.SAVE, (), Resources(0, 0)),
            (EconomyAction.SAVE, (), Resources(0, 0)),
        ])
        first = replay.task.prepare(replay.formation, {})
        second = replay.task.prepare(replay.formation, {})
        self.assertEqual(first.outcome, FormationOutcome.COMPLETE)
        self.assertEqual(second.outcome, FormationOutcome.COMPLETE)
        self.assertEqual([call.kwargs["transfer_budget"] for call in planner.call_args_list], [1, 0, 0])
        self.assertEqual(second.roster.heroes[-1].imprint, Imprint.D)
        self.assertEqual(second.roster.revision, before.revision + 1)
        self.assertIsNone(planner.call_args_list[1].kwargs["observed_quotes"])
        self.assertFalse(choices)

    def test_max_level_slot_clearance_stays_consumed_when_a_gift_refills_the_bench(self):
        replay, queue_round, sales = self.max_level_transfer_scenario()
        queue_round()
        first = replay.task.prepare(replay.formation, {})
        self.assertEqual(first.outcome, FormationOutcome.COMPLETE)
        self.assertEqual(sum(hero.cell is None for hero in first.roster.heroes), 10)
        self.assertEqual(len(sales), 1)
        count = len(replay.calls)
        # The gift restores a full bench, but both the remaining loop and an
        # explicit re-entry in this round must preserve the consumed allowance.
        second = replay.task.prepare(replay.formation, {})
        self.assertEqual(second.outcome, FormationOutcome.COMPLETE)
        self.assertEqual(len(replay.calls), count)
        self.assertEqual(len(sales), 1)
        self.assertTrue(replay.task._slot_freed)
        self.assertEqual(replay.task._transferred, 0)
        self.assertFalse(replay.remaining)

    def max_level_transfer_scenario(self):
        from tasks.brawl.economy_policy import transfer_candidates
        from tasks.brawl.rules import HEROES
        from tasks.brawl.strategy import HeroState, recommend_lineup

        # This is a complete synthetic twenty-hero pool. Use the real selected
        # team so current-field protection and the lineup recommendation agree;
        # only UI operations are faked, not policy choices or roster commits.
        pool = tuple(HeroState(name, Imprint.NONE) for name in tuple(HEROES)[:20])
        recommendation = recommend_lineup(pool, 9)
        ordered = (*recommendation.team, *recommendation.bench)
        before = self.state(*(RosterHero(hero.name, hero.imprint,
                                        (index // 4, index % 4) if index < 10 else None)
                              for index, hero in enumerate(ordered)), level=9)
        replay = self.scenario(before, [])
        sales = []
        reward = Resources(1, 0)

        def preview(names, observed_scan, **kwargs):
            state = replay.current.state
            self.assertEqual(observed_scan, EconomyEvidenceTests.evidence(state))
            candidates, protected = transfer_candidates(state, {})
            self.assertEqual(len(names), 1)
            self.assertIn(names[0], candidates)
            self.assertNotIn(names[0], protected)
            return self.preview(state, names, reward)

        def finish(selected, *, commit, **kwargs):
            state = replay.current.state
            self.assertEqual(selected.header, state.panel)
            if not commit:
                return self.transfer_report(state, selected.names, reward)
            self.assertEqual(sum(hero.cell is None for hero in state.heroes), 10)
            self.assertNotIn(state.panel.round_number, [number for number, _ in sales])
            name, = selected.names
            sales.append((state.panel.round_number, name))
            # A same-name new copy is an actual acquisition event, not a reason
            # to preserve the quote revision or repeat the slot-clearing sale.
            after = replace(state,
                            heroes=(*(hero for hero in state.heroes if hero.name != name),
                                    RosterHero(name, Imprint.NONE)),
                            panel=replace(state.panel, coins=state.panel.coins + reward.coins))
            replay.current.state = after
            return self.transfer_report(state, selected.names, reward, committed=True, after=after,
                                        gifts=(HeroResult(name, Imprint.NONE, None),))

        def queue_round():
            candidates, _ = transfer_candidates(replay.formation.roster, {})
            self.assertTrue(candidates)
            # Each candidate is quoted and cancelled, followed by one sale.
            for _ in range(len(candidates) + 1):
                replay.remaining.extend((operation(("transfer", "preview"), preview),
                                         operation(("transfer", "finish"), finish)))

        return replay, queue_round, sales

    def test_max_level_can_clear_again_after_five_rounds_without_resetting_upgrade_budget(self):
        replay, queue_round, sales = self.max_level_transfer_scenario()
        first_round = replay.formation.roster.panel.round_number
        for offset in range(6):
            if offset:
                previous = replay.formation.roster
                replay.current.state = replace(previous,
                    panel=replace(previous.panel, round_number=previous.panel.round_number + 1))
                refreshed = replay.formation.refresh()
                self.assertEqual(refreshed.outcome, FormationOutcome.READY)
            queue_round()
            result = replay.task.prepare(replay.formation, {})
            self.assertEqual(result.outcome, FormationOutcome.COMPLETE)
            self.assertEqual(result.roster.panel.coins, offset + 1)
            self.assertEqual(sum(hero.cell is None for hero in result.roster.heroes), 10)
            self.assertEqual(replay.task._funding_level, 9)
            self.assertEqual(replay.task._transferred, 0)
            self.assertFalse(replay.remaining)
        self.assertEqual([number for number, _ in sales], list(range(first_round, first_round + 6)))
        self.assertEqual(replay.task._transferred, 0)
        self.assertEqual(sum(kwargs["commit"] for _, method, _, kwargs in replay.calls if method == "finish"), 6)
