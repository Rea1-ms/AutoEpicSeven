# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline checks for shared game facts and their task consumers."""
import importlib
import json
import subprocess
import sys
import unittest
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE

from module.game_info import catalog
from module.game_info.timeline import render_timeline

NOW = datetime.fromisoformat("2030-01-22T11:00:00+08:00")






class CatalogTests(unittest.TestCase):
    def test_server_cap_override_and_default_outside_season(self):
        info = parse(document(cn_values={"max_level": 42}))
        self.assertEqual(info.level_cap("arena_pass", "CN", NOW), 42)
        self.assertEqual(info.level_cap("arena_pass", "OVERSEA", NOW), 40)
        self.assertEqual(info.level_cap("arena_pass", "CN", NOW - timedelta(seconds=1)), 38)
        self.assertEqual(info.level_cap("shadow_commission", "CN", NOW), 30)
        self.assertEqual(info.values("arena_pass", "CN", NOW)["source"], "Offline fixture")

    def test_delay_applies_only_to_configured_kind(self):
        data = document("free_gacha_20")
        data["events"][0].pop("cn_start")
        data["events"][0].pop("cn_end")
        info = parse(data)
        global_period, cn = info.periods
        self.assertEqual(cn.start - global_period.start, timedelta(days=21))
        self.assertTrue(cn.inferred)
        data["events"][0]["kind"] = "arena_pass"
        info = parse(data)
        self.assertEqual(len(info.periods), 1)
        self.assertIsNone(info.current("arena_pass", "CN", NOW))

    def test_cn_only_and_partial_override(self):
        data = document()
        data["events"][0].pop("oversea_start")
        data["events"][0].pop("oversea_end")
        self.assertEqual([p.server_family for p in parse(data).periods], ["CN"])
        data = document("free_gacha_20", cn_end="2030-02-23T12:00:00+08:00")
        data["events"][0].pop("cn_start")
        cn = parse(data).periods[1]
        self.assertEqual(cn.start, NOW)
        self.assertEqual(cn.end.day, 23)
        self.assertTrue(cn.inferred)

    def test_adjacent_seasons_and_observation_boundary(self):
        data = document()
        first = data["events"][0]
        second = dict(first, id="second", cn_start=first["cn_end"],
                      cn_end="2030-03-22T11:00:00+08:00",
                      oversea_start=first["oversea_end"], oversea_end="2030-03-01T11:00:00+08:00")
        data["events"].append(second)
        info = parse(data)
        boundary = datetime.fromisoformat(first["cn_end"])
        self.assertEqual(info.current("arena_pass", "CN", boundary).event_id, "second")
        self.assertEqual(info.current("arena_pass", "CN", boundary - timedelta(seconds=1)).event_id, "first")
        self.assertEqual(info.next_change("arena_pass", "CN", NOW - timedelta(seconds=1)), NOW)
        self.assertEqual(info.next_change("arena_pass", "CN", NOW), boundary)

    def test_invalid_documents_rejected(self):
        changes = [dict(oversea_start="2030-01-01T11:00:00"),
                   dict(cn_end="2029-01-01T11:00:00+08:00"),
                   dict(values={"max_level": True}), dict(values={"max_level": 0}),
                   dict(values={"max_level": "40"}), dict(source=""), dict(id="not.valid"),
                   dict(cn_values={"max_level": -1}), dict(misspelled_start="2030-01-01")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse(document(**change))
        for version in (True, 2, None):
            data = document()
            data["schema_version"] = version
            with self.assertRaises(ValueError):
                parse(data)
        data = document()
        data["events"][0].pop("cn_start")
        with self.assertRaisesRegex(ValueError, "Incomplete CN"):
            parse(data)

    def test_duplicate_fields_ids_and_overlaps_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            catalog.parse_info("schema_version: 1\nschema_version: 1\nevents: []")
        data = document()
        data["events"].append(deepcopy(data["events"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse(data)
        data["events"][1]["id"] = "second"
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            parse(data)

    def test_snapshot_is_immutable_and_file_replacement_is_seen(self):
        first = json.dumps(document(values={"max_level": 40}))
        second = json.dumps(document(values={"max_level": 45}))
        with patch.object(Path, "read_text", side_effect=[first, second]):
            old = catalog.load_info()
            new = catalog.load_info()
        self.assertEqual(old.level_cap("arena_pass", "CN", NOW), 40)
        self.assertEqual(new.level_cap("arena_pass", "CN", NOW), 45)
        with self.assertRaises(TypeError):
            old.periods[0].values["max_level"] = 90
        with self.assertRaises(TypeError):
            old.defaults["arena_pass"]["max_level"] = 90

    def test_errors_are_not_treated_as_empty_calendar(self):
        with patch.object(Path, "read_text", return_value="events: ["), self.assertRaisesRegex(ValueError, "Invalid game info"):
            catalog.load_info()
        info = parse(document())
        with self.assertRaisesRegex(ValueError, "server family"):
            info.current("arena_pass", "unknown", NOW)
        with self.assertRaisesRegex(ValueError, "max_level"):
            info.level_cap("unknown", "CN", NOW)

    def test_new_kind_is_queryable_but_not_an_activity_flow(self):
        from tasks.activity import calendar
        data = document("future_feature")
        info = parse(data)
        self.assertIsNotNone(info.current("future_feature", "CN", NOW))
        with patch.object(calendar, "load_info", return_value=info):
            self.assertEqual(calendar.load_calendar(), ())
        self.assertIn("Test season", render_timeline(info))

    def test_timeline_labels_inferred_dates_and_empty_calendar(self):
        data = document("free_gacha_20")
        data["events"][0].pop("cn_start")
        timeline = render_timeline(parse(data))
        self.assertIn("2030-01-22 11:00", timeline)
        self.assertIn("推算", timeline)
        data["events"] = []
        self.assertIn("尚未填写日期", render_timeline(parse(data)))

    def test_cli_reads_data_without_importing_tasks_or_writing_state(self):
        result = subprocess.run([sys.executable, "-B", "-m", "module.game_info", "current", "--server", "CN",
                                 "--at", "2026-09-18T12:00:00+08:00"], cwd=WORKTREE, capture_output=True,
                                encoding="utf-8", check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output["values"]["arena_pass"]["max_level"], 38)
        self.assertIn("free_gacha_20_2026_08_27", [period["id"] for period in output["active"]])
        result = subprocess.run([sys.executable, "-B", "-c",
            "import sys; from module.game_info.catalog import load_info; load_info(); "
            "assert not any(m == 'tasks' or m.startswith('tasks.') for m in sys.modules)"],
            cwd=WORKTREE, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)


class ConsumerTests(unittest.TestCase):
    def test_new_boundary_overrides_thursday_fallback(self):
        from tasks.arena.rewards import next_battle_pass_recheck
        record = datetime(2030, 1, 18, 12)
        self.assertEqual(next_battle_pass_recheck(record), datetime(2030, 1, 24, 11))
        boundary = datetime(2030, 1, 22, 11).astimezone()
        self.assertEqual(next_battle_pass_recheck(record, boundary), datetime(2030, 1, 22, 11))

    def test_season_change_or_cap_change_rechecks_full_progress(self):
        from tasks.arena import rewards
        rank = SimpleNamespace(value=38, total=38, time=NOW.replace(tzinfo=None) - timedelta(hours=1))
        config = SimpleNamespace(Emulator_PackageName="com.zlongame.cn.epicseven",
                                 Arena_ClaimBattlePassRewards=True, stored=SimpleNamespace(ArenaRank=rank))
        subject = rewards.ArenaRewardsMixin()
        subject.config = config
        info = parse(document(values={"max_level": 38}))
        with patch.object(rewards, "load_info", return_value=info), patch.object(rewards, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW.replace(tzinfo=None)
            with patch.object(rewards, "Timer", side_effect=RuntimeError("entered flow")):
                with self.assertRaisesRegex(RuntimeError, "entered flow"):
                    rewards.ArenaRewardsMixin._claim_battle_pass_rewards(subject)
            rank.time = NOW.replace(tzinfo=None)
            self.assertFalse(rewards.ArenaRewardsMixin._claim_battle_pass_rewards(subject))
            with patch.object(info.__class__, "level_cap", return_value=40), patch.object(rewards, "Timer", side_effect=RuntimeError("entered flow")):
                with self.assertRaisesRegex(RuntimeError, "entered flow"):
                    rewards.ArenaRewardsMixin._claim_battle_pass_rewards(subject)
        self.assertEqual(rank.value, 38)

    def test_arena_max_text_uses_current_cap_and_stores_it(self):
        from tasks.arena import dashboard
        ocr = dashboard.ArenaDigit(None, lang="cn", name="test", max_level=45)
        self.assertEqual(ocr.format_result("MAX"), 45)
        counter = SimpleNamespace(set=Mock())
        subject = SimpleNamespace(config=SimpleNamespace(stored=SimpleNamespace(ArenaRank=counter)),
                                  device=SimpleNamespace(image=None), _ocr_lang=lambda: "cn")
        with patch.object(dashboard, "level_cap", return_value=45), patch.object(dashboard, "ArenaDigit") as digit:
            digit.return_value.ocr_single_line.return_value = 45
            dashboard.ArenaDashboardMixin._ocr_arena_rank(subject)
            self.assertEqual(digit.call_args.kwargs["max_level"], 45)
        counter.set.assert_called_once_with(45, 45)

    def test_both_shadow_readers_use_shared_cap(self):
        for module_name, class_name, ocr_name in (
            ("tasks.dungeon.entry", "CombatEntryMixin", "CombatDigit"),
            ("tasks.item.data_update", "DataUpdate", "E7Digit"),
        ):
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                counter = SimpleNamespace(set=Mock())
                subject = SimpleNamespace(config=SimpleNamespace(stored=SimpleNamespace(ShadowCommission=counter)),
                                          device=SimpleNamespace(image=None), _ocr_lang=lambda: "cn")
                with patch.object(module, "level_cap", return_value=40), patch.object(module, ocr_name) as digit:
                    digit.return_value.ocr_single_line.return_value = 35
                    getattr(module, class_name)._ocr_shadow_commission_level(subject)
                    counter.set.assert_called_once_with(35, 40)
                    counter.set.reset_mock()
                    digit.return_value.ocr_single_line.return_value = 41
                    getattr(module, class_name)._ocr_shadow_commission_level(subject)
                    counter.set.assert_not_called()

    def test_stored_observations_survive_new_defaults_and_config_upgrade(self):
        from module.config.config_updater import ConfigUpdater
        from module.config.stored.classes import StoredArenaRank, StoredShadowCommission
        stamp = datetime(2026, 9, 18, 12)
        for cls, key, total in ((StoredArenaRank, "ArenaRank", 38), (StoredShadowCommission, "ShadowCommission", 30)):
            with self.subTest(key=key):
                old = {"DataUpdate": {"Dashboard": {key: {"value": total, "total": total, "time": stamp}}}}
                upgraded = ConfigUpdater().config_update(old)
                config = SimpleNamespace(data=upgraded, modified={}, auto_update=False, multi_set=nullcontext)
                counter = cls(f"DataUpdate.Dashboard.{key}")
                counter._bind(config)
                self.assertEqual((counter.value, counter.total, counter.time), (total, total, stamp))
                self.assertEqual(config.modified, {})
                counter.set(39, 45)
                self.assertEqual((counter.value, counter.total), (39, 45))
                self.assertEqual(config.modified[counter._key]["total"], 45)



from tests.support.game_info import document, parse
