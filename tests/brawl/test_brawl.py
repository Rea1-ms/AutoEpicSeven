import contextlib
import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from module.config import server
from tasks.brawl.rules import HEROES

server.set_lang("global_cn")

from tasks.brawl.brawl import analyze, main, parse_roster, unique_fields  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlObservation,
    BrawlScreen,
    PreparationPanel,
)
from tests.support.brawl import frame  # noqa: E402
from tests.support.offline import retained_directory  # noqa: E402


def complete_roster():
    """Explicit synthetic manual input; no hero identity is inferred from pixels."""
    return {
        "complete": True,
        "round_number": 7,
        "level": 4,
        "heroes": [{"name": name, "imprint": "NONE"} for name in
                   ("赛兹", "卡琳", "度假风优芬妮", "雅卡泰丝", "萝季", "格伦")],
        "class_bonuses": {"盗贼": 1},
    }


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.panel = PreparationPanel(round_number=7, level=4, deployed=5, capacity=5,
                                      coins=145, synergy_tokens=0)

    def report(self, panel=None, state=BrawlScreen.PREPARATION, roster=None):
        from tasks.brawl.bench import BenchViewport

        observation = BrawlObservation(state=state, panel=panel, reason="synthetic observation")
        with patch("tasks.brawl.vision.BrawlVision") as vision, \
                patch("tasks.brawl.bench.read_bench", return_value=BenchViewport(())), \
                patch("tasks.brawl.cursor.read_cursor", return_value=None):
            vision.return_value.observe.return_value = observation
            return analyze(np.zeros((720, 1280, 3), dtype=np.uint8), roster)

    def test_full_level_and_distinct_currency_affordability(self):
        report = self.report(replace(self.panel, level=9, capacity=10, deployed=10, coins=9, synergy_tokens=40))
        economy = report["economy"]
        self.assertIsNone(economy["upgrade_cost"])
        self.assertFalse(economy["can_afford_upgrade"])
        self.assertFalse(economy["summons"]["normal"]["affordable"])
        self.assertTrue(economy["summons"]["lower"]["affordable"])
        self.assertTrue(economy["summons"]["upper"]["affordable"])
        self.assertEqual(sum(economy["summons"]["normal"]["odds"].values()), 1)
        json.dumps(report, ensure_ascii=False)

    def test_missing_low_level_odds_and_roster_remain_unknown(self):
        report = self.report(self.panel)
        self.assertIsNone(report["economy"]["summons"]["normal"]["odds"])
        self.assertIsNone(report["lineup"])
        self.assertIsNone(report["run_complete"])
        self.assertEqual(report["economy"]["upgrade_cost"], 100)

    def test_round_clear_and_overlay_never_become_complete_or_spendable(self):
        for state in (BrawlScreen.ROUND_CLEAR, BrawlScreen.LEVEL_UP, BrawlScreen.UNKNOWN):
            with self.subTest(state=state):
                report = self.report(state=state)
                self.assertIsNone(report["run_complete"])
                self.assertIsNone(report["economy"])
                self.assertIsNone(report["lineup"])
                with self.assertRaises(ValueError):
                    self.report(state=state, roster=complete_roster())
        self.assertIsNone(self.report()["economy"])

    def test_manual_roster_produces_serializable_explicit_imprints(self):
        report = self.report(self.panel, roster=complete_roster())
        self.assertEqual(len(report["lineup"]["team"]), 5)
        self.assertEqual(len(report["lineup"]["bench"]), 1)
        self.assertTrue(report["lineup"]["heuristic"])
        self.assertEqual({hero["imprint"] for hero in report["lineup"]["team"]}, {"NONE"})
        json.dumps(report, ensure_ascii=False)

    def test_incomplete_stale_and_missing_imprint_inputs_fail(self):
        variants = []
        for field, value in (("complete", False), ("level", 5), ("round_number", 8),
                             ("round_number", 7.0), ("heroes", []), ("class_bonuses", None)):
            data = complete_roster()
            data[field] = value
            variants.append(data)
        data = complete_roster()
        data["heroes"][0].pop("imprint")
        variants.append(data)
        data = complete_roster()
        data["heroes"] = data["heroes"][:4]
        variants.append(data)
        data = complete_roster()
        data["extra"] = True
        variants.append(data)
        for data in variants:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_roster(data, self.panel)

    def test_other_servers_do_not_load_recognition_or_change_language(self):
        try:
            for lang in ("cn", "global_en"):
                server.set_lang(lang)
                with patch("tasks.brawl.vision.BrawlVision") as vision, self.assertRaises(ValueError):
                    analyze(None)
                vision.assert_not_called()
                self.assertEqual(server.lang, lang)
        finally:
            server.set_lang("global_cn")

    def test_real_screenshot_reaches_report_and_manual_lineup(self):
        self.frame = frame("prepare-level4")
        report = analyze(self.frame, complete_roster())
        self.assertEqual(report["observation"]["panel"]["coins"], 145)
        self.assertEqual(report["economy"]["upgrade_cost"], 100)
        self.assertEqual(len(report["lineup"]["team"]), 5)
        self.assertTrue(report["economy"]["summons"]["normal"]["affordable"])
        self.assertFalse(report["economy"]["summons"]["lower"]["affordable"])
        self.frame = frame("round-clear")
        self.assertIsNone(analyze(self.frame)["run_complete"])

    def test_cli_preserves_existing_reports_and_fails_on_missing_source(self):
        with retained_directory("brawl-cli") as directory:
            output = Path(directory) / "report.json"
            output.write_text("existing report", encoding="utf-8")
            with patch("module.base.utils.load_image", return_value=np.zeros((720, 1280, 3), dtype=np.uint8)), \
                    patch("tasks.brawl.brawl.analyze", return_value={"run_complete": None}), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["--image", "synthetic.png", "--out", str(output)])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing report")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["--image", str(Path(directory) / "missing.png")])
            self.assertEqual(error.exception.code, 2)

    def test_final_results_and_intermediate_overlays_have_distinct_reports(self):
        for name, result in (("normal-victory15", "victory"), ("normal-victory16", "victory"),
                             ("normal-defeat", "defeat"), ("challenge-defeat", "defeat")):
            with self.subTest(sample=name):
                self.frame = frame(name)
                report = analyze(self.frame)
                self.assertIs(report["run_complete"], True)
                self.assertEqual(report["run_result"], result)
                self.assertIsNone(report["economy"])
                self.assertIsNone(report["lineup"])
        for name in ("rank-up15", "rank-up16", "hero-unlock", "formation-place", "formation-move"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                report = analyze(self.frame)
                self.assertIsNone(report["run_complete"])
                self.assertIsNone(report["economy"])

    def test_round_animations_can_be_missed_without_inventing_completion(self):
        # A loss can retry the same round, with a fresh balance and roster.
        # Neither round number nor a transient banner is an acquisition counter.
        for names in (("normal-round2", "round-failed", "normal-after-loss"),
                      ("normal-round2", "normal-after-loss")):
            reports = [analyze(frame(name)) for name in names]
            self.assertTrue(all(report["run_complete"] is None for report in reports))
            self.assertEqual(reports[0]["observation"]["panel"]["round_number"], 2)
            self.assertEqual(reports[-1]["observation"]["panel"]["round_number"], 2)
            self.assertEqual(reports[0]["observation"]["panel"]["coins"], 55)
            self.assertEqual(reports[-1]["observation"]["panel"]["coins"], 70)
        for name in ("round-clear", "round-failed", "last-round-failed"):
            report = analyze(frame(name))
            self.assertTrue(report["automatic_transition"])
            self.assertIsNone(report["economy"])
            self.assertIsNone(report["run_complete"])

    def test_single_hero_result_is_serializable_without_inventing_a_roster(self):
        for name, expected in (
                ("hero-new-12", {"name": "最后的骑士克劳乌", "imprint": "SSS",
                                 "previous_imprint": "SS", "is_overflow": False}),
                ("hero-start-03", {"name": "卡萝", "imprint": "D",
                                   "previous_imprint": None, "is_overflow": False}),
                ("hero-new-08", {"name": "小春", "imprint": "SSS",
                                 "previous_imprint": "SSS", "is_overflow": True})):
            with self.subTest(sample=name):
                self.frame = frame(name)
                report = analyze(self.frame)
                self.assertEqual(report["hero_result"], expected)
                self.assertIsNone(report["lineup"])
                self.assertIsNone(report["economy"])
                self.assertIsNone(report["run_complete"])
                json.dumps(report, ensure_ascii=False)

    def test_visible_bench_and_grid_do_not_become_a_complete_roster(self):
        for name, occupied in (("formation-place", ()), ("formation-move", ((3, 3),))):
            with self.subTest(sample=name):
                self.frame = frame(name)
                report = analyze(self.frame)
                self.assertFalse(report["bench"]["complete"])
                self.assertGreater(len(report["bench"]["entries"]), 0)
                self.assertEqual(len(report["board"]["cells"]), 28)
                self.assertIsNone(report["board"]["selected_source"])
                actual = tuple((cell["screen_row"], cell["screen_column"])
                               for cell in report["board"]["occupied_swap_targets"])
                self.assertEqual(actual, occupied)
                self.assertIsNone(report["lineup"])
                self.assertIsNone(report["economy"])
                self.assertIsNone(report["transmit"])
                self.assertIsNone(report["run_complete"])
                json.dumps(report, ensure_ascii=False)
        report = analyze(frame("normal-deployed"))
        self.assertIsNone(report["board"])
        self.assertFalse(report["bench"]["complete"])
        self.assertIsNone(report["lineup"])

    def test_transmit_quote_and_return_do_not_claim_an_offline_sale(self):
        for name, selected, coins in (("transmit", False, 0), ("transmit-selected", True, 2)):
            with self.subTest(sample=name):
                self.frame = frame(name)
                report = analyze(self.frame)
                self.assertEqual(report["transmit"]["selected"], selected)
                self.assertEqual(report["transmit"]["quote"], {"coins": coins, "tokens": 0})
                self.assertEqual(report["transmit"]["header"]["coins"], 25)
                self.assertFalse(report["transmit"]["confirmed"])
                self.assertFalse(report["bench"]["complete"])
                self.assertTrue(all(entry["plus_area"] is None for entry in report["bench"]["entries"]))
                self.assertIsNone(report["economy"])
                self.assertIsNone(report["lineup"])
                json.dumps(report, ensure_ascii=False)
        report = analyze(frame("transmit-return"))
        self.assertEqual(report["observation"]["panel"]["coins"], 27)
        self.assertIsNone(report["transmit"])

    def test_cursor_endpoints_do_not_make_a_single_frame_a_complete_roster(self):
        for name, hero, top, bottom, empty in (
            ("queue-top", "维多丽卡", True, False, False),
            ("queue-bottom", "辅助型拉兹", None, True, False),
            ("queue-empty", None, True, True, True),
        ):
            with self.subTest(sample=name):
                self.frame = frame(name)
                report = analyze(self.frame)
                cursor = report["bench_cursor"]
                selected = cursor["selected"]
                self.assertEqual(None if selected is None else selected["name"], hero)
                self.assertEqual((cursor["top"], cursor["bottom"], cursor["empty"]), (top, bottom, empty))
                self.assertIsNone(report["lineup"])
                self.assertIsNone(report["run_complete"])
                json.dumps(report, ensure_ascii=False)

    def test_field_identity_without_imprint_does_not_create_a_complete_roster(self):
        self.frame = frame("field-glen")
        report = analyze(self.frame)
        self.assertEqual(report["field"]["heroes"][0]["name"], "格伦")
        self.assertEqual(report["field"]["heroes"][0]["foot"], (731, 409))
        self.assertTrue(report["field"]["identity_complete"])
        self.assertIsNone(report["field"]["heroes"][0]["imprint"])
        self.assertFalse(report["field"]["complete"])
        self.assertIsNone(report["lineup"])
        json.dumps(report, ensure_ascii=False)

    def test_unread_transmit_quote_stays_unknown_but_model_errors_propagate(self):
        from tasks.brawl.vision import ObservationError

        self.frame = frame("transmit-selected")
        with patch("tasks.brawl.transmit.read_transmit_panel", side_effect=ObservationError("unread quote")):
            report = analyze(self.frame)
        self.assertIsNone(report["transmit"])
        self.assertIsNone(report["economy"])
        with patch("tasks.brawl.transmit.read_transmit_panel", side_effect=RuntimeError("model unavailable")), \
                self.assertRaisesRegex(RuntimeError, "model unavailable"):
            analyze(self.frame)

    def test_idle_motion_traits_report_identity_without_inventing_a_click_position(self):
        for sample in ("full-swap-stable", "upper-summon-stable"):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                report = analyze(self.frame)
                matched = [row for row in report["field_traits"]["headers"] if row["name"] == "小春"]
                self.assertEqual(len(matched), 1)
                self.assertEqual(matched[0]["candidates"], ("小春",))
                self.assertEqual((matched[0]["faction"], matched[0]["profession"]), ("卡厄思", "战士"))
                self.assertNotIn("foot", matched[0])
                self.assertNotIn("小春", [hero["name"] for hero in report["field"]["heroes"]])
                self.assertIsNone(report["lineup"])
                json.dumps(report, ensure_ascii=False)
        self.frame = frame("upgrade")
        self.assertIsNone(analyze(self.frame)["field_traits"])

    def test_withdraw_control_reports_selected_cell_without_claiming_bench_space_or_completion(self):
        self.frame = frame("withdraw-grid")
        report = analyze(self.frame)
        control = report["withdraw"]
        self.assertEqual(control["source"], {"screen_row": 6, "screen_column": 3, "center": (794, 577)})
        self.assertEqual(control["button_area"], (750, 545, 767, 562))
        self.assertFalse(control["confirmed"])
        self.assertFalse(control["bench_space_verified"])
        self.assertIsNone(report["lineup"])
        json.dumps(report, ensure_ascii=False)
        self.frame = frame("withdraw-after")
        report = analyze(self.frame)
        self.assertIsNone(report["withdraw"])
        self.assertEqual(report["observation"]["panel"]["deployed"], 9)
        self.assertIn("小春", [entry["name"] for entry in report["bench"]["entries"]])
        self.assertEqual(report["bench_cursor"]["selected"]["name"], "利纳柯")


class RosterInputTests(unittest.TestCase):
    def test_complete_roster_limit_uses_current_deployed_count_not_field_capacity(self):
        names = tuple(HEROES)
        for deployed in (0, 3, 5):
            with self.subTest(deployed=deployed):
                panel = PreparationPanel(round_number=7, level=4, deployed=deployed, capacity=5,
                                         coins=145, synergy_tokens=0)
                data = complete_roster()
                data["heroes"] = [{"name": name, "imprint": "NONE"} for name in names[:deployed + 10]]
                states, bonuses = parse_roster(data, panel)
                self.assertEqual(len(states) - deployed, 10)
                self.assertEqual(bonuses, data["class_bonuses"])
                data["heroes"].append({"name": names[deployed + 10], "imprint": "NONE"})
                with self.assertRaisesRegex(ValueError, "当前上场人数加10名候补"):
                    parse_roster(data, panel)

    def test_duplicate_fields_cannot_hide_conflicting_roster_values(self):
        for source in ('{"complete": false, "complete": true}', '{"class_bonuses": {"盗贼": 0, "盗贼": 1}}'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                json.loads(source, object_pairs_hook=unique_fields)

    def test_unread_fields_cannot_be_turned_into_defaults(self):
        panel = SimpleNamespace(round_number=7, level=4, deployed=5)
        for data in (None, [], {}, {"complete": True}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_roster(data, panel)


class ToolEntryTests(unittest.TestCase):
    """Synthetic boundary observations; formation itself has separate replays.

    The shared setup exercises the unchanged challenge one-battle boundary.
    Normal-run cases explicitly enable the continuous economic controller.
    """

    def setUp(self):
        from tasks.brawl.combat import RoundOutcome, RoundResult
        from tasks.brawl.entry import EntryResult
        from tasks.brawl.formation import FormationOutcome, FormationResult
        from tasks.brawl.roster import RosterHero, RosterState
        from tasks.brawl.tool import BrawlTool
        from tasks.brawl.vision import BrawlMode

        server.set_lang("global_cn")
        self.addCleanup(server.set_lang, "global_cn")
        self.config = SimpleNamespace(Emulator_GameLanguage="cn")
        self.bonuses = {"骑士": 1, "战士": 0, "射手": 0, "魔导士": 0, "精灵师": 0, "盗贼": 1}
        self.panel = PreparationPanel(round_number=1, level=1, deployed=0,
                                      capacity=2, coins=10, synergy_tokens=0)
        self.observation = BrawlObservation(BrawlScreen.PREPARATION, self.panel,
                                            game_mode=BrawlMode.CHALLENGE)
        self.roster = RosterState((RosterHero("卡琳", "NONE"),), self.panel, BrawlMode.CHALLENGE)
        placed = replace(self.roster, heroes=(RosterHero("卡琳", "NONE", (3, 2)),),
                         panel=replace(self.panel, deployed=1), revision=1)
        self.ready = FormationResult(FormationOutcome.READY, self.roster)
        self.complete = FormationResult(FormationOutcome.COMPLETE, placed, steps=("confirmed step",))
        self.next_roster = replace(placed, panel=replace(placed.panel, round_number=2, coins=20), revision=2)
        self.next_complete = replace(self.complete, roster=self.next_roster, steps=())
        self.battle = RoundResult(RoundOutcome.NEXT_PREPARATION, panel=self.next_roster.panel)
        self.device = SimpleNamespace(image=object(), screenshot=Mock(), click=Mock())
        self.task = object.__new__(BrawlTool)
        self.task.config = self.config
        self.task.device = self.device
        self.task.interval_timer = {}
        stack = contextlib.ExitStack()
        self.stack = stack
        self.addCleanup(stack.close)
        self.vision = stack.enter_context(patch("tasks.brawl.vision.BrawlVision"))
        self.vision.return_value.observe.return_value = self.observation
        self.relics_constructor = stack.enter_context(patch("tasks.brawl.relics.BrawlRelics"))
        self.relics = self.relics_constructor.return_value
        self.relics.read.return_value = self.bonuses
        self.entry_constructor = stack.enter_context(patch("tasks.brawl.entry.BrawlEntry"))
        self.entry = self.entry_constructor.return_value
        self.entry.interval_timer = {}
        self.entry.enter.side_effect = lambda **kwargs: EntryResult(
            self.vision(self.device.image).observe(), resumed=True)
        self.opening_constructor = stack.enter_context(patch("tasks.brawl.opening.BrawlOpening"))
        self.opening = self.opening_constructor.return_value
        self.opening.prepare.side_effect = lambda **kwargs: self.vision.return_value.observe.return_value
        self.opening.interval_timer = {}
        self.read_talents = stack.enter_context(patch("tasks.brawl.talents.read_opening_talents"))
        self.read_talents.return_value = self.bonuses
        self.constructor = stack.enter_context(patch("tasks.brawl.formation.BrawlFormation"))
        self.formation = self.constructor.return_value
        self.formation.begin.return_value = self.ready
        self.formation.arrange.side_effect = [self.complete, self.next_complete]
        self.formation.interval_timer = {}
        self.combat_constructor = stack.enter_context(patch("tasks.brawl.combat.BrawlCombat"))
        self.combat = self.combat_constructor.return_value
        self.combat.play_round.return_value = self.battle
        self.log = stack.enter_context(patch("tasks.brawl.tool.logger"))

    def _normal_run(self):
        from tasks.brawl.combat import RoundOutcome, RoundResult
        from tasks.brawl.formation import FormationOutcome
        from tasks.brawl.vision import BrawlMode

        self.observation = replace(self.observation, game_mode=BrawlMode.NORMAL)
        self.vision.return_value.observe.return_value = self.observation
        self.roster = replace(self.roster, game_mode=BrawlMode.NORMAL)
        self.ready = replace(self.ready, roster=self.roster)
        self.complete = replace(self.complete, roster=replace(self.complete.roster, game_mode=BrawlMode.NORMAL))
        self.next_roster = replace(self.next_roster, game_mode=BrawlMode.NORMAL)
        self.next_complete = replace(self.next_complete, roster=self.next_roster)
        self.formation.begin.return_value = self.ready
        self.formation.arrange.side_effect = [self.complete]
        self.formation.refresh.return_value = replace(self.next_complete, outcome=FormationOutcome.READY)
        self.formation.invalidate.side_effect = lambda reason: replace(
            self.complete, outcome=FormationOutcome.FAILED, reason=reason)
        self.economy_constructor = self.stack.enter_context(patch("tasks.brawl.economy.BrawlEconomy"))
        self.economy = self.economy_constructor.return_value
        self.economy.prepare.side_effect = [self.complete, self.next_complete]
        self.terminal = RoundResult(RoundOutcome.RUN_ENDED, reason="run_victory")
        self.combat.play_round.side_effect = [self.battle, self.terminal]

    def test_normal_run_reconciles_rewards_once_before_economy_and_second_battle(self):
        from tasks.brawl.formation import FormationOutcome
        from tasks.brawl.heroes import HeroResult
        from tasks.brawl.rules import Imprint

        self._normal_run()
        reward = HeroResult("卡琳", Imprint.C, Imprint.NONE)
        self.next_roster = replace(self.next_roster, heroes=(replace(self.next_roster.heroes[0], imprint=Imprint.C),))
        self.next_complete = replace(self.next_complete, roster=self.next_roster)
        self.formation.refresh.return_value = replace(self.next_complete, outcome=FormationOutcome.READY)
        self.economy.prepare.side_effect = [self.complete, self.next_complete]
        self.combat.play_round.side_effect = [replace(self.battle, hero_results=(reward,)), self.terminal]
        events = Mock()
        events.attach_mock(self.formation, "formation")
        events.attach_mock(self.economy, "economy")
        events.attach_mock(self.combat, "combat")

        self.assertIs(self.task.run(), True)

        self.assertEqual(events.mock_calls, [
            call.formation.begin(skip_first_screenshot=True),
            call.economy.prepare(self.formation, self.bonuses, skip_first_screenshot=True),
            call.combat.play_round(self.complete.roster.panel, self.roster.game_mode, skip_first_screenshot=True),
            call.formation.refresh(hero_results=(reward,), skip_first_screenshot=True),
            call.economy.prepare(self.formation, self.bonuses, skip_first_screenshot=True),
            call.combat.play_round(self.next_roster.panel, self.roster.game_mode, skip_first_screenshot=True),
            call.formation.invalidate("The run ended; the last formation is no longer actionable"),
        ])
        self.assertIs(self.economy.interval_timer, self.formation.interval_timer)
        self.assertIs(self.combat.interval_timer, self.entry.interval_timer)
        self.assertIs(self.opening.interval_timer, self.combat.interval_timer)
        self.relics.read.assert_called_once_with(skip_first_screenshot=True)
        self.read_talents.assert_not_called()
        self.formation.arrange.assert_not_called()
        self.device.click.assert_not_called()

    def test_normal_same_round_losses_refresh_each_reward_before_retry_without_retry_limit(self):
        from tasks.brawl.formation import FormationOutcome
        from tasks.brawl.heroes import HeroResult
        from tasks.brawl.rules import Imprint

        self._normal_run()
        rewards = [HeroResult("卡琳", after, before) for before, after in (
            (Imprint.NONE, Imprint.C), (Imprint.C, Imprint.B), (Imprint.B, Imprint.A))]
        # Same round is not the same economic or hero state. The orchestration
        # must pass each newly confirmed panel to combat, including after a loss,
        # and consume only the latest ordered imprint transition during refresh.
        refreshed = [replace(
            self.complete, outcome=FormationOutcome.READY,
            roster=replace(self.complete.roster,
                           heroes=(replace(self.complete.roster.heroes[0], imprint=hero.imprint),),
                           panel=replace(self.complete.roster.panel, coins=20 + index * 10),
                           revision=index + 2)) for index, hero in enumerate(rewards)]
        retries = [replace(self.battle, panel=state.roster.panel, hero_results=(hero,),
                           reason="Round failed; retry preparation confirmed")
                   for hero, state in zip(rewards, refreshed)]
        self.combat.play_round.side_effect = [*retries, self.terminal]
        prepared = [self.complete, *(replace(state, outcome=FormationOutcome.COMPLETE) for state in refreshed)]
        self.economy.prepare.side_effect = prepared
        self.formation.refresh.side_effect = refreshed

        self.assertIs(self.task.run(), True)

        self.assertEqual(self.formation.refresh.call_args_list, [
            call(hero_results=(hero,), skip_first_screenshot=True) for hero in rewards])
        self.assertEqual(self.combat.play_round.call_args_list, [
            call(state.roster.panel, self.roster.game_mode, skip_first_screenshot=True) for state in prepared])
        self.assertEqual(self.economy.prepare.call_count, 4)
        self.device.click.assert_not_called()

    def test_normal_economy_failure_stops_before_any_battle(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.formation import FormationOutcome

        self._normal_run()
        failed = replace(self.complete, outcome=FormationOutcome.FAILED,
                         reason="Transfer reward remains unconfirmed")
        self.economy.prepare.side_effect = [failed]
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()
        self.combat.play_round.assert_not_called()
        self.formation.refresh.assert_not_called()
        self.assertIn(failed.reason, self.log.info.call_args.args[0])
        self.device.click.assert_not_called()

    def test_normal_economy_exception_invalidates_session_before_propagating(self):
        self._normal_run()
        self.economy.prepare.side_effect = RuntimeError("synthetic economic screenshot failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic economic screenshot failure"):
            self.task.run()
        self.formation.invalidate.assert_called_once_with("Economy interrupted before verified preparation")
        self.combat.play_round.assert_not_called()

    def test_normal_unexplained_reward_roster_stops_before_next_economy_or_battle(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.formation import FormationOutcome

        self._normal_run()
        self.formation.refresh.return_value = replace(self.next_complete, outcome=FormationOutcome.FAILED,
                                                        reason="Unexplained bench hero")
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()
        self.assertEqual(self.economy.prepare.call_count, 1)
        self.assertEqual(self.combat.play_round.call_count, 1)
        self.assertIn("Unexplained bench hero", self.log.info.call_args.args[0])
        self.device.click.assert_not_called()

    def test_normal_reward_refresh_exception_invalidates_without_economic_retry(self):
        self._normal_run()
        self.formation.refresh.side_effect = RuntimeError("synthetic post-battle scan failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic post-battle scan failure"):
            self.task.run()
        self.formation.invalidate.assert_called_once_with("Post-battle reconciliation interrupted before verification")
        self.assertEqual(self.combat.play_round.call_count, 1)
        self.assertEqual(self.economy.prepare.call_count, 1)

    def test_normal_changed_preparation_after_battle_invalidates_before_economic_decisions(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.formation import FormationOutcome

        self._normal_run()
        changed = replace(self.next_roster, panel=replace(self.next_roster.panel, coins=999))
        self.formation.refresh.return_value = replace(self.next_complete, outcome=FormationOutcome.READY, roster=changed)
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()
        self.formation.invalidate.assert_called_once_with("Preparation changed after the confirmed battle boundary")
        self.assertEqual(self.economy.prepare.call_count, 1)
        self.assertEqual(self.combat.play_round.call_count, 1)

    def test_normal_terminal_result_retained_without_refresh_or_another_economy_pass(self):
        self._normal_run()
        self.combat.play_round.side_effect = [self.terminal]

        self.assertIs(self.task.run(), True)

        self.assertEqual(self.economy.prepare.call_count, 1)
        self.assertEqual(self.combat.play_round.call_count, 1)
        self.formation.refresh.assert_not_called()
        self.formation.invalidate.assert_called_once()
        self.assertIn("final result", self.log.info.call_args.args[0])
        self.device.click.assert_not_called()

    def test_normal_saved_lineup_keeps_one_battle_without_unknown_economic_state(self):
        """Keep the legacy test ID; a normal save now continues after reconstruction."""
        self._normal_run()
        self.vision.return_value.observe.return_value = replace(self.observation, panel=self.complete.roster.panel)
        self.formation.recover.return_value = replace(self.complete, outcome=self.ready.outcome)
        self.formation.class_bonuses = self.bonuses

        with patch("tasks.brawl.resume.BrawlResume", return_value=self.formation):
            self.assertIs(self.task.run(), True)

        self.formation.recover.assert_called_once_with(class_bonuses=self.bonuses, skip_first_screenshot=True)
        self.formation.begin.assert_not_called()
        self.constructor.assert_not_called()
        self.read_talents.assert_not_called()
        self.opening_constructor.assert_not_called()
        self.assertEqual(self.economy.prepare.call_count, 2)
        self.assertEqual(self.combat.play_round.call_count, 2)
        self.formation.refresh.assert_called_once_with(hero_results=(), skip_first_screenshot=True)
        self.assertIs(self.economy.interval_timer, self.formation.interval_timer)
        self.assertIn("final result", self.log.info.call_args.args[0])
        self.device.click.assert_not_called()

    def test_challenge_keeps_one_battle_and_never_constructs_normal_economy(self):
        with patch("tasks.brawl.economy.BrawlEconomy") as economy_constructor:
            self.assertIs(self.task.run(), True)
        economy_constructor.assert_not_called()
        self.assertEqual(self.combat.play_round.call_count, 1)
        self.assertEqual(self.formation.arrange.call_count, 2)
        self.formation.refresh.assert_not_called()

    def test_supported_automatic_language_values_require_no_manual_talent_settings(self):
        from tasks.brawl.tool import validate_language

        for language in (None, "", "auto", "cn"):
            with self.subTest(language=language):
                self.config.Emulator_GameLanguage = language
                self.assertIsNone(validate_language(self.config))
        self.log.critical.assert_not_called()
        self.device.screenshot.assert_not_called()
        self.read_talents.assert_not_called()

    def test_unsupported_game_language_stops_before_screenshot_and_talent_reading(self):
        from module.exception import RequestHumanTakeover

        for language in ("en", "tw", "jp"):
            with self.subTest(language=language), patch.object(self.config, "Emulator_GameLanguage", language), \
                    self.assertRaises(RequestHumanTakeover) as raised:
                self.task.run()
            self.assertEqual(raised.exception.args, ())
        self.device.screenshot.assert_not_called()
        self.device.click.assert_not_called()
        self.vision.assert_not_called()
        self.read_talents.assert_not_called()
        self.constructor.assert_not_called()
        self.assertEqual(self.log.critical.call_count, 3)
        for logged in self.log.critical.call_args_list:
            self.assertTrue(logged.args[0].isascii())

    def test_unsupported_asset_language_stops_without_changing_server_or_actions(self):
        from module.exception import RequestHumanTakeover

        for language in ("cn", "global_en", None):
            with self.subTest(language=language), patch.object(server, "lang", language):
                with self.assertRaises(RequestHumanTakeover):
                    self.task.run()
                self.assertEqual(server.lang, language)
            self.device.screenshot.assert_not_called()
            self.vision.assert_not_called()
            self.read_talents.assert_not_called()
            self.constructor.assert_not_called()
        self.device.click.assert_not_called()

    def test_preflight_rejects_unknown_overlay_and_incomplete_preparation(self):
        from module.exception import RequestHumanTakeover

        observations = [replace(self.observation, state=state, reason="synthetic blocked state")
                        for state in (BrawlScreen.UNKNOWN, BrawlScreen.COMBAT,
                                      BrawlScreen.FORMATION_SELECTION, BrawlScreen.HERO_RESULT)]
        observations.extend((replace(self.observation, panel=None),
                             replace(self.observation, game_mode=None)))
        for observation in observations:
            with self.subTest(observation=observation):
                self.vision.return_value.observe.return_value = observation
                with self.assertRaises(RequestHumanTakeover):
                    self.task.run()
        self.assertEqual(self.device.screenshot.call_count, len(observations))
        self.read_talents.assert_not_called()
        self.constructor.assert_not_called()
        self.device.click.assert_not_called()
        self.assertEqual(self.log.critical.call_count, len(observations))

    def test_preflight_rejects_empty_mid_round_before_scan(self):
        from module.exception import RequestHumanTakeover

        for panel in (replace(self.panel, round_number=2), replace(self.panel, round_number=7)):
            with self.subTest(panel=panel):
                self.vision.return_value.observe.return_value = replace(self.observation, panel=panel)
                with self.assertRaises(RequestHumanTakeover):
                    self.task.run()
        self.constructor.assert_not_called()
        self.read_talents.assert_not_called()
        self.device.click.assert_not_called()
        self.assertEqual(self.device.screenshot.call_count, 2)

    def test_resumed_deployed_formation_plays_once_without_guessing_talents_or_rearranging(self):
        from tasks.brawl.heroes import HeroResult
        from tasks.brawl.rules import Imprint

        deployed = replace(self.panel, round_number=7, level=4, capacity=5, deployed=2)
        self.vision.return_value.observe.return_value = replace(self.observation, panel=deployed)
        reward = HeroResult("卡琳", Imprint.D, None)
        self.combat.play_round.return_value = replace(self.battle, panel=replace(deployed, round_number=8),
                                                      hero_results=(reward,))

        self.assertIs(self.task.run(), True)

        self.entry.enter.assert_called_once_with(skip_first_screenshot=True, only_free=True)
        self.opening_constructor.assert_not_called()
        self.read_talents.assert_not_called()
        self.constructor.assert_not_called()
        self.combat.play_round.assert_called_once_with(deployed, self.observation.game_mode,
                                                       skip_first_screenshot=True)
        self.assertIs(self.combat.interval_timer, self.entry.interval_timer)
        self.assertIn("collected 1 hero results", self.log.info.call_args.args[0])
        self.assertIn("round 8", self.log.info.call_args.args[0])
        self.device.click.assert_not_called()

    def test_normal_saved_run_recovers_roster_and_talents_before_continuous_economy(self):
        from tasks.brawl.vision import BrawlMode

        saved = replace(self.observation, game_mode=BrawlMode.NORMAL,
                        panel=replace(self.panel, round_number=7, deployed=1))
        self.vision.return_value.observe.return_value = saved
        with patch("tasks.brawl.resume.BrawlResume") as constructor, \
                patch.object(self.task, "_run_tracked", return_value=True) as tracked:
            recovered = constructor.return_value
            recovered.class_bonuses = self.bonuses
            recovered.recover.return_value = self.ready
            self.assertIs(self.task.run(), True)
            recovered.recover.assert_called_once_with(class_bonuses=self.bonuses, skip_first_screenshot=True)
            self.assertIs(self.task.formation, recovered)
            self.assertIs(recovered.interval_timer, self.entry.interval_timer)
            tracked.assert_called_once_with(self.ready, self.bonuses)
        self.combat.play_round.assert_not_called()
        self.opening_constructor.assert_not_called()
        self.constructor.assert_not_called()

    def test_normal_later_empty_saved_field_also_uses_recovery_without_opening_purchases(self):
        from tasks.brawl.vision import BrawlMode

        self.vision.return_value.observe.return_value = replace(
            self.observation, game_mode=BrawlMode.NORMAL, panel=replace(self.panel, round_number=7))
        with patch("tasks.brawl.resume.BrawlResume") as constructor, \
                patch.object(self.task, "_run_tracked", return_value=True) as tracked:
            constructor.return_value.class_bonuses = self.bonuses
            constructor.return_value.recover.return_value = self.ready
            self.assertIs(self.task.run(), True)
            tracked.assert_called_once_with(self.ready, self.bonuses)
        self.opening_constructor.assert_not_called()

    def test_failed_normal_recovery_never_starts_economy_or_a_battle(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.formation import FormationOutcome
        from tasks.brawl.vision import BrawlMode

        self.vision.return_value.observe.return_value = replace(
            self.observation, game_mode=BrawlMode.NORMAL, panel=replace(self.panel, deployed=1))
        with patch("tasks.brawl.resume.BrawlResume") as constructor, \
                patch.object(self.task, "_run_tracked") as tracked:
            constructor.return_value.class_bonuses = None
            constructor.return_value.recover.return_value = replace(
                self.ready, outcome=FormationOutcome.FAILED, reason="Unread saved identity")
            with self.assertRaises(RequestHumanTakeover):
                self.task.run()
            tracked.assert_not_called()
        self.combat.play_round.assert_not_called()
        self.opening_constructor.assert_not_called()

    def test_resumed_failed_battle_stops_without_constructing_or_invalidating_an_unknown_roster(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.combat import RoundOutcome

        self.vision.return_value.observe.return_value = replace(
            self.observation, panel=replace(self.panel, deployed=1))
        self.combat.play_round.return_value = replace(self.battle, outcome=RoundOutcome.FAILED,
                                                      reason="Synthetic unread resumed result")
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()
        self.constructor.assert_not_called()
        self.formation.invalidate.assert_not_called()
        self.read_talents.assert_not_called()
        self.assertEqual(self.combat.play_round.call_count, 1)

    def test_free_only_setting_is_forwarded_without_changing_the_saved_lineup(self):
        self.config.Brawl_OnlyFree = False
        deployed = replace(self.panel, deployed=1)
        self.vision.return_value.observe.return_value = replace(self.observation, panel=deployed)

        self.assertIs(self.task.run(), True)

        self.entry.enter.assert_called_once_with(skip_first_screenshot=True, only_free=False)
        self.constructor.assert_not_called()
        self.read_talents.assert_not_called()
        self.combat.play_round.assert_called_once_with(deployed, self.observation.game_mode,
                                                       skip_first_screenshot=True)

    def test_invalid_free_only_setting_stops_before_screenshot_or_entry(self):
        from module.exception import RequestHumanTakeover

        for value in (None, 0, 1, "false", "true"):
            with self.subTest(value=value):
                self.config.Brawl_OnlyFree = value
                with self.assertRaises(RequestHumanTakeover):
                    self.task.run()
        self.device.screenshot.assert_not_called()
        self.entry_constructor.assert_not_called()
        self.combat_constructor.assert_not_called()

    def test_resumed_terminal_result_stays_open_without_opening_or_arrangement(self):
        from tasks.brawl.combat import RoundOutcome

        self.vision.return_value.observe.return_value = replace(
            self.observation, panel=replace(self.panel, deployed=1))
        self.combat.play_round.return_value = replace(self.battle, outcome=RoundOutcome.RUN_ENDED,
                                                      panel=None, reason="run_defeat")
        self.assertIs(self.task.run(), True)
        self.constructor.assert_not_called()
        self.opening_constructor.assert_not_called()
        self.read_talents.assert_not_called()
        self.assertEqual(self.combat.play_round.call_count, 1)
        self.device.click.assert_not_called()

    def test_entry_failure_keeps_its_reason_and_stops_before_opening_or_formation(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.vision import ObservationError

        self.entry.enter.side_effect = ObservationError("Synthetic conflicting lobby controls")
        with self.assertRaises(RequestHumanTakeover) as raised:
            self.task.run()
        self.assertIsInstance(raised.exception.__cause__, ObservationError)
        self.entry.enter.assert_called_once_with(skip_first_screenshot=True, only_free=True)
        self.opening_constructor.assert_not_called()
        self.constructor.assert_not_called()
        self.combat_constructor.assert_not_called()
        self.assertIn("Synthetic conflicting lobby controls", self.log.critical.call_args.args[0])

    def test_unread_frame_reports_takeover_but_model_failures_are_not_hidden(self):
        from module.exception import RequestHumanTakeover

        self.vision.side_effect = ValueError("synthetic invalid frame shape")
        with self.assertRaises(RequestHumanTakeover) as raised:
            self.task.run()
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertIn("synthetic invalid frame shape", self.log.critical.call_args.args[0])
        self.vision.side_effect = RuntimeError("synthetic model unavailable")
        with self.assertRaisesRegex(RuntimeError, "synthetic model unavailable"):
            self.task.run()
        self.constructor.assert_not_called()
        self.read_talents.assert_not_called()
        self.device.click.assert_not_called()

    def test_success_uses_fresh_frame_and_runs_one_arrangement_with_observed_bonuses(self):
        fresh = object()
        self.device.screenshot.side_effect = lambda: setattr(self.device, "image", fresh)

        self.assertIs(self.task.run(), True)

        self.device.screenshot.assert_called_once_with()
        self.vision.assert_called_once_with(fresh)
        self.entry_constructor.assert_called_once_with(config=self.config, device=self.device)
        self.entry.enter.assert_called_once_with(skip_first_screenshot=True, only_free=True)
        self.opening.prepare.assert_called_once_with(skip_first_screenshot=True)
        self.assertIs(self.opening.interval_timer, self.entry.interval_timer)
        self.relics.read.assert_called_once_with(skip_first_screenshot=True)
        self.assertIs(self.relics.interval_timer, self.entry.interval_timer)
        self.read_talents.assert_not_called()
        self.constructor.assert_called_once_with(config=self.config, device=self.device)
        self.assertEqual(self.formation.method_calls, [
            call.begin(skip_first_screenshot=True),
            call.arrange(self.bonuses, skip_first_screenshot=True),
            call.arrange(self.bonuses, hero_results=(), skip_first_screenshot=True),
        ])
        self.combat_constructor.assert_called_once_with(config=self.config, device=self.device)
        self.combat.play_round.assert_called_once_with(self.complete.roster.panel, self.roster.game_mode,
                                                       skip_first_screenshot=True)
        self.assertIs(self.combat.interval_timer, self.formation.interval_timer)
        self.device.click.assert_not_called()
        self.log.critical.assert_not_called()
        self.assertTrue(any("卡琳" in str(item) and "row 4" in str(item) and "column 3" in str(item)
                            for item in self.log.info.call_args_list))
        self.assertIn("preparation", self.log.info.call_args.args[0].lower())

    def test_opening_result_is_read_before_talents_and_keeps_the_shared_action_interval(self):
        opened = replace(self.observation, panel=replace(self.panel, level=2, capacity=3, coins=0))
        self.opening.prepare.side_effect = None
        self.opening.prepare.return_value = opened
        events = Mock()
        events.attach_mock(self.opening, "opening")
        events.attach_mock(self.relics, "relics")
        events.attach_mock(self.entry, "entry")
        events.attach_mock(self.formation, "formation")

        self.assertIs(self.task.run(), True)

        self.assertEqual(events.mock_calls[:4], [
            call.relics.read(skip_first_screenshot=True),
            call.entry.enter(skip_first_screenshot=True, only_free=True),
            call.opening.prepare(skip_first_screenshot=True),
            call.formation.begin(skip_first_screenshot=True),
        ])
        self.read_talents.assert_not_called()
        self.assertIs(self.opening.interval_timer, self.formation.interval_timer)
        self.assertIs(self.entry.interval_timer, self.combat.interval_timer)

    def test_opening_failure_stops_before_talents_or_roster_without_retry(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.vision import ObservationError

        self.opening.prepare.side_effect = ObservationError("Synthetic unresolved opening purchase")
        with self.assertRaises(RequestHumanTakeover) as raised:
            self.task.run()
        self.assertIsInstance(raised.exception.__cause__, ObservationError)
        self.opening.prepare.assert_called_once_with(skip_first_screenshot=True)
        self.read_talents.assert_not_called()
        self.constructor.assert_not_called()
        self.combat_constructor.assert_not_called()

    def test_unread_opening_talents_stop_before_scan_without_defaulting_or_retry(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.vision import ObservationError

        self.relics.read.side_effect = ObservationError("Missing harmony profession node")
        with self.assertRaises(RequestHumanTakeover) as raised:
            self.task.run()
        self.assertIsInstance(raised.exception.__cause__, ObservationError)
        self.entry.enter.assert_not_called()
        self.opening_constructor.assert_not_called()
        self.relics.read.assert_called_once_with(skip_first_screenshot=True)
        self.read_talents.assert_not_called()
        self.constructor.assert_not_called()
        self.device.click.assert_not_called()
        self.log.critical.assert_called_once()
        self.assertTrue(self.log.critical.call_args.args[0].isascii())
        self.assertTrue(any("Missing harmony profession node" in str(item) for item in self.log.method_calls))

    def test_old_account_talent_values_cannot_override_or_block_observed_bonuses(self):
        # Legacy flat values may survive in an existing account object. Only the
        # current relic visit authorizes bonuses, even when old data is bad.
        for field, value in {
            "TalentKnight": False, "TalentWarrior": True, "TalentRanger": "true",
            "TalentMage": None, "TalentSoulWeaver": 7, "TalentThief": False,
        }.items():
            setattr(self.config, f"Brawl_{field}", value)

        self.assertIs(self.task.run(), True)

        self.relics.read.assert_called_once_with(skip_first_screenshot=True)
        self.read_talents.assert_not_called()
        self.assertEqual(self.formation.arrange.call_args_list, [
            call(self.bonuses, skip_first_screenshot=True),
            call(self.bonuses, hero_results=(), skip_first_screenshot=True),
        ])
        self.assertIs(self.config.Brawl_TalentKnight, False)
        self.assertIs(self.config.Brawl_TalentWarrior, True)
        self.log.critical.assert_not_called()

    def test_runtime_log_messages_are_english_while_game_identity_data_is_preserved(self):
        self.assertIs(self.task.run(), True)

        # Hero and profession names are read-only game data, not translated log
        # prose. Strip only these known identities before checking message text.
        identities = ("卡琳", *self.bonuses)
        messages = [str(logged.args[0]) for logged in self.log.method_calls if logged.args]
        self.assertTrue(messages)
        for message in messages:
            with self.subTest(message=message):
                for identity in identities:
                    message = message.replace(identity, "")
                self.assertTrue(message.isascii())

    def test_empty_bench_invalidates_session_without_arrangement(self):
        from module.exception import RequestHumanTakeover

        self.formation.begin.return_value = replace(self.ready, roster=replace(self.roster, heroes=()))
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()
        self.formation.begin.assert_called_once_with(skip_first_screenshot=True)
        self.formation.invalidate.assert_called_once()
        self.formation.arrange.assert_not_called()
        self.assertIn("bench", self.log.critical.call_args.args[0].lower())
        self.assertIn("empty", self.log.critical.call_args.args[0].lower())
        self.assertTrue(self.log.critical.call_args.args[0].isascii())

    def test_begin_failure_logs_reason_and_never_retries_or_arranges(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.formation import FormationOutcome, FormationResult

        self.formation.begin.return_value = FormationResult(FormationOutcome.FAILED, None, "incomplete scan")
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()
        self.formation.begin.assert_called_once_with(skip_first_screenshot=True)
        self.formation.arrange.assert_not_called()
        self.assertIn("bench", self.log.critical.call_args.args[0].lower())
        self.assertTrue(self.log.critical.call_args.args[0].isascii())
        self.assertIn("incomplete scan", self.log.info.call_args.args[0])

    def test_failed_arrangement_keeps_confirmed_steps_and_stops_without_retry(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.formation import FormationOutcome

        self.formation.arrange.side_effect = None
        self.formation.arrange.return_value = replace(self.complete, outcome=FormationOutcome.FAILED,
                                                      reason="post-action identity is unread")
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()
        self.formation.begin.assert_called_once_with(skip_first_screenshot=True)
        self.formation.arrange.assert_called_once_with(self.bonuses, skip_first_screenshot=True)
        self.assertIn("formation", self.log.critical.call_args.args[0].lower())
        self.assertIn("1", self.log.info.call_args.args[0])
        self.assertTrue(self.log.critical.call_args.args[0].isascii())
        self.assertTrue(self.log.info.call_args.args[0].isascii())
        self.assertIn("post-action identity is unread", self.log.info.call_args.args[0])
        self.device.click.assert_not_called()

    def test_one_battle_forwards_collected_rewards_before_next_formation_and_stops(self):
        from tasks.brawl.heroes import HeroResult
        from tasks.brawl.rules import Imprint

        reward = HeroResult("卡琳", Imprint.D, Imprint.NONE)
        self.combat.play_round.return_value = replace(self.battle, hero_results=(reward,))
        events = Mock()
        events.attach_mock(self.formation, "formation")
        events.attach_mock(self.combat, "combat")

        self.assertIs(self.task.run(), True)

        self.assertEqual(events.mock_calls, [
            call.formation.begin(skip_first_screenshot=True),
            call.formation.arrange(self.bonuses, skip_first_screenshot=True),
            call.combat.play_round(self.complete.roster.panel, self.roster.game_mode, skip_first_screenshot=True),
            call.formation.arrange(self.bonuses, hero_results=(reward,), skip_first_screenshot=True),
        ])
        self.assertIn("round 2", self.log.info.call_args.args[0])
        self.device.click.assert_not_called()

    def test_failed_battle_invalidates_old_roster_without_second_arrangement(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.combat import RoundOutcome

        self.combat.play_round.return_value = replace(self.battle, outcome=RoundOutcome.FAILED,
                                                      reason="Hero reward remains unread")
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()

        self.formation.arrange.assert_called_once_with(self.bonuses, skip_first_screenshot=True)
        self.formation.invalidate.assert_called_once_with("Hero reward remains unread")
        self.assertIn("Hero reward remains unread", self.log.critical.call_args.args[0])

    def test_battle_exception_invalidates_session_and_preserves_failure(self):
        self.combat.play_round.side_effect = RuntimeError("synthetic screenshot failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic screenshot failure"):
            self.task.run()
        self.formation.invalidate.assert_called_once()
        self.assertEqual(self.formation.arrange.call_count, 1)

    def test_final_run_result_stays_open_without_refresh_or_another_battle(self):
        from tasks.brawl.combat import RoundOutcome

        self.combat.play_round.return_value = replace(self.battle, outcome=RoundOutcome.RUN_ENDED,
                                                      panel=None, reason="run_defeat")
        self.assertIs(self.task.run(), True)
        self.formation.invalidate.assert_called_once()
        self.assertEqual(self.formation.arrange.call_count, 1)
        self.assertEqual(self.combat.play_round.call_count, 1)
        self.assertIn("final result", self.log.info.call_args.args[0])
        self.device.click.assert_not_called()

    def test_unexplained_post_battle_roster_stops_without_retrying_arrangement(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.formation import FormationOutcome

        failed = replace(self.complete, outcome=FormationOutcome.FAILED,
                         reason="Unexplained bench hero", steps=())
        self.formation.arrange.side_effect = [self.complete, failed]
        with self.assertRaises(RequestHumanTakeover):
            self.task.run()
        self.assertEqual(self.formation.arrange.call_count, 2)
        self.assertEqual(self.combat.play_round.call_count, 1)
        self.assertIn("Unexplained bench hero", self.log.info.call_args.args[0])

    def test_failed_round_reforms_same_round_without_starting_another_battle(self):
        self.combat.play_round.return_value = replace(self.battle, panel=self.complete.roster.panel,
                                                      reason="Round failed; preparation returned on the same round")
        self.formation.arrange.side_effect = [self.complete, self.complete]
        self.assertIs(self.task.run(), True)
        self.assertEqual(self.formation.arrange.call_count, 2)
        self.assertEqual(self.combat.play_round.call_count, 1)
        self.assertIn("round 1", self.log.info.call_args.args[0])

    def test_manual_runner_binds_task_before_device_and_resolves_language_before_run(self):
        from tasks.brawl.tool import run_tool

        events = []

        def load_config(name, task):
            events.append(("config", name, task))
            return self.config

        def build_device(*, config):
            self.assertIs(config, self.config)
            events.append(("device",))
            server.set_lang("global_cn")
            return self.device

        with patch.object(server, "lang", None), \
                patch("module.config.config.AzurLaneConfig", side_effect=load_config) as config_factory, \
                patch("module.device.device.Device", side_effect=build_device) as device_factory, \
                patch("tasks.brawl.tool.BrawlTool", return_value=self.task) as tool_factory:
            self.assertIsNone(run_tool("synthetic_offline_profile"))
            self.assertEqual(server.lang, "global_cn")
        self.assertEqual(events, [("config", "synthetic_offline_profile", "Brawl"), ("device",)])
        config_factory.assert_called_once_with("synthetic_offline_profile", task="Brawl")
        device_factory.assert_called_once_with(config=self.config)
        tool_factory.assert_called_once_with(config=self.config, device=self.device)
        self.assertEqual(self.formation.arrange.call_args_list, [
            call(self.bonuses, skip_first_screenshot=True),
            call(self.bonuses, hero_results=(), skip_first_screenshot=True),
        ])

    def test_manual_runner_rejects_invalid_settings_before_constructing_device(self):
        from module.exception import RequestHumanTakeover
        from tasks.brawl.tool import run_tool

        self.config.Emulator_GameLanguage = "en"
        with patch("module.config.config.AzurLaneConfig", return_value=self.config), \
                patch("module.device.device.Device") as device_factory, \
                patch("tasks.brawl.tool.BrawlTool") as tool_factory, \
                self.assertRaises(RequestHumanTakeover):
            run_tool("synthetic_offline_profile")
        device_factory.assert_not_called()
        tool_factory.assert_not_called()
        self.device.screenshot.assert_not_called()

    def test_tool_dispatch_uses_manual_runner_and_aes_entry_preserves_task_binding(self):
        from aes import AutoEpicSeven
        from module.webui.submodule.utils import get_tool_runner
        from tasks.brawl.tool import run_tool

        self.assertIs(get_tool_runner("Brawl"), run_tool)
        app = object.__new__(AutoEpicSeven)
        app.__dict__["config"] = self.config
        app.__dict__["device"] = self.device
        with patch("tasks.brawl.tool.BrawlTool") as constructor:
            app.brawl()
        constructor.assert_called_once_with(config=self.config, device=self.device, task="Brawl")
        constructor.return_value.run.assert_called_once_with()

    def test_source_configuration_is_manual_only_without_talent_switches(self):
        import yaml

        from module.config.config_manual import ManualConfig
        from module.webui.task_extension import TASK_DETAIL_RENDERERS

        folder = Path(__file__).resolve().parents[2] / "module" / "config"
        source = {}
        for name in ("task", "argument", "default", "override"):
            source[name] = yaml.safe_load((folder / "argument" / f"{name}.yaml").read_text(encoding="utf-8"))
        self.assertEqual(source["task"]["Tool"]["tasks"]["Brawl"], ["Scheduler", "Brawl"])
        self.assertEqual(source["task"]["Tool"]["page"], "tool")
        self.assertEqual(source["argument"]["Brawl"], {"OnlyFree": True})
        self.assertIs(source["default"]["Brawl"]["Brawl"]["OnlyFree"], True)
        from module.config.config_updater import ConfigGenerator

        generated_option = ConfigGenerator().args["Brawl"]["Brawl"]["OnlyFree"]
        self.assertIs(generated_option["value"], True)
        self.assertEqual(generated_option["type"], "checkbox")
        self.assertIs(source["default"]["Brawl"]["Scheduler"]["Enable"], False)
        scheduler = source["override"]["Brawl"]["Scheduler"]
        self.assertEqual(scheduler["Enable"], {"value": False, "display": "hide", "option": [False]})
        for field in ("NextRun", "Command", "ServerUpdate"):
            self.assertEqual(scheduler[field]["display"], "hide")
        self.assertIn("Brawl", ManualConfig.SCHEDULER_PRIORITY.split())
        self.assertTrue(callable(TASK_DETAIL_RENDERERS["Brawl"]))
        with patch("pywebio.output.put_text") as put_text, \
                patch("module.webui.lang.t", return_value="Opening instructions") as translate:
            TASK_DETAIL_RENDERERS["Brawl"](SimpleNamespace())
        translate.assert_called_once_with("Task.Brawl.help")
        put_text.assert_called_once_with("Opening instructions")
        locales = list((folder / "i18n").glob("*.json"))
        self.assertEqual(len(locales), 5)
        for file in locales:
            with self.subTest(locale=file.name):
                translations = json.loads(file.read_text(encoding="utf-8"))
                self.assertTrue(translations["Task"]["Brawl"]["name"].strip())
                help_text = translations["Task"]["Brawl"]["help"]
                self.assertTrue(help_text.strip())
                self.assertNotEqual(help_text, "Task.Brawl.help")
                option = translations["Brawl"]["OnlyFree"]
                self.assertTrue(option["name"].strip())
                self.assertTrue(option["help"].strip())
                self.assertNotIn("Brawl.OnlyFree", option["name"] + option["help"])

    def test_generated_menu_and_config_preserve_manual_defaults_and_remove_old_talents(self):
        from module.config.config_generated import GeneratedConfig
        from module.config.config_updater import ConfigUpdater

        root = Path(__file__).resolve().parents[2]
        folder = root / "module" / "config" / "argument"
        menu = json.loads((folder / "menu.json").read_text(encoding="utf-8"))
        args = json.loads((folder / "args.json").read_text(encoding="utf-8"))
        template = json.loads((root / "config" / "template.json").read_text(encoding="utf-8"))
        self.assertIn("Brawl", menu["Tool"]["tasks"])
        self.assertEqual(menu["Tool"]["page"], "tool")
        self.assertIs(template["Brawl"]["Scheduler"]["Enable"], False)
        self.assertEqual(args["Brawl"]["Scheduler"]["Enable"]["display"], "hide")
        self.assertEqual(args["Brawl"]["Scheduler"]["Enable"]["option"], [False])
        self.assertEqual(template["Brawl"]["Scheduler"]["Command"], "Brawl")
        self.assertEqual(set(args["Brawl"]), {"Scheduler", "Brawl"})
        self.assertEqual(set(template["Brawl"]), {"Scheduler", "Brawl"})
        self.assertIs(args["Brawl"]["Brawl"]["OnlyFree"]["value"], True)
        self.assertIs(template["Brawl"]["Brawl"]["OnlyFree"], True)
        self.assertIs(GeneratedConfig.Brawl_OnlyFree, True)
        old_fields = ("TalentKnight", "TalentWarrior", "TalentRanger",
                      "TalentMage", "TalentSoulWeaver", "TalentThief")
        for field in old_fields:
            self.assertFalse(hasattr(GeneratedConfig, f"Brawl_{field}"))
        # Upgrade explicit memory data only; never open a real account profile.
        old = {"Brawl": {"Scheduler": {"Enable": True},
                         "Brawl": {field: True for field in old_fields}}}
        updated = ConfigUpdater().config_update(old)
        self.assertIs(updated["Brawl"]["Scheduler"]["Enable"], False)
        self.assertEqual(set(updated["Brawl"]), {"Scheduler", "Brawl"})
        self.assertEqual(updated["Brawl"]["Brawl"], {"OnlyFree": True})
        self.assertIs(old["Brawl"]["Scheduler"]["Enable"], True)
        self.assertEqual(old["Brawl"]["Brawl"], {field: True for field in old_fields})
        opted_in = {"Brawl": {"Brawl": {"OnlyFree": False}}}
        self.assertIs(ConfigUpdater().config_update(opted_in)["Brawl"]["Brawl"]["OnlyFree"], False)
