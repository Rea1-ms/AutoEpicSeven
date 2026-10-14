import contextlib
import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server
from tasks.brawl.rules import HEROES

server.set_lang("global_cn")

from tasks.brawl.brawl import analyze, main, parse_roster, unique_fields  # noqa: E402
from tasks.brawl.vision import BrawlObservation, BrawlScreen, PreparationPanel  # noqa: E402
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
        for name, expected in (("hero-new-12", {"name": "最后的骑士克劳乌", "imprint": "SSS", "previous_imprint": "SS"}),
                               ("hero-start-03", {"name": "卡萝", "imprint": "D", "previous_imprint": None})):
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
