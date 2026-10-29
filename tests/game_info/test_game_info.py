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

    def test_shared_window_applies_to_both_servers_before_legacy_delay(self):
        data = document('free_gacha_20')
        event = data['events'][0]
        for key in ('oversea_start', 'oversea_end', 'cn_start', 'cn_end'):
            event.pop(key)
        event.update(start='2030-01-01T11:00:00+08:00', end='2030-02-01T11:00:00+08:00')
        oversea, cn = parse(data).periods
        self.assertEqual((oversea.start, oversea.end), (cn.start, cn.end))
        self.assertEqual(cn.start.isoformat(), event['start'])
        self.assertFalse(cn.inferred)
        # Historical records without shared dates retain their original scope.
        event['oversea_start'] = event.pop('start')
        event['oversea_end'] = event.pop('end')
        data['cn_delay_days'] = {}
        self.assertEqual([p.server_family for p in parse(data).periods], ['OVERSEA'])

    def test_shared_window_supports_independent_server_overrides(self):
        data = document('free_gacha_20')
        event = data['events'][0]
        for key in ('oversea_start', 'oversea_end', 'cn_start', 'cn_end'):
            event.pop(key)
        event.update(start='2030-01-01T02:00:00+08:00', end='2030-02-01T02:00:00+08:00',
                     oversea_start='2030-01-02T02:00:00+08:00', cn_end='2030-02-01T03:00:00+08:00')
        oversea, cn = parse(data).periods
        self.assertEqual(oversea.start.isoformat(), event['oversea_start'])
        self.assertEqual(oversea.end.isoformat(), event['end'])
        self.assertEqual(cn.start.isoformat(), event['start'])
        self.assertEqual(cn.end.isoformat(), event['cn_end'])
        self.assertFalse(cn.inferred)
        self.assertIsNone(parse(data).current('free_gacha_20', 'OVERSEA', oversea.end))
        self.assertIsNotNone(parse(data).current('free_gacha_20', 'CN', oversea.end))

    def test_incomplete_or_invalid_shared_windows_are_rejected(self):
        for change in (
            dict(start='2030-01-01T02:00:00+08:00'),
            dict(end='2030-02-01T02:00:00+08:00'),
            dict(start='2030-01-01T02:00:00', end='2030-02-01T02:00:00+08:00'),
            dict(start='2030-02-01T02:00:00+08:00', end='2030-01-01T02:00:00+08:00'),
            dict(start='2030-01-01T02:00:00+08:00', end='2030-01-01T02:00:00+08:00'),
            dict(start='2030-01-01T02:00:00+08:00', end='2030-02-01T02:00:00+08:00',
                 cn_end='2029-12-31T02:00:00+08:00'),
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse(document(**change))
        data = document()
        data['events'][0].pop('oversea_start')
        with self.assertRaisesRegex(ValueError, 'Incomplete OVERSEA'):
            parse(data)

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

    def test_timeline_archives_each_server_at_its_exact_end(self):
        info = catalog.load_info()
        at = datetime.fromisoformat('2026-10-29T11:00:00+08:00')
        timeline = render_timeline(info, at)
        current, history = timeline.split('<details>', 1)
        chart = current.split('```mermaid', 1)[1].split('```', 1)[0]
        self.assertIn('CN_free_gacha_20_2026_08_27', chart)
        self.assertNotIn('OVERSEA_free_gacha_20_2026_08_27', chart)
        for event_id in ('meirin_reward_2026_10_08', 'huche_shop_2026_09_17',
                         'koharu_raffle_2026_09_17', 'e7wc_battle_gate_2026_09_10'):
            self.assertNotIn(f'OVERSEA_{event_id}', chart)
        self.assertIn('CN_meirin_reward_2026_10_08', chart)
        self.assertIn('CN_huche_shop_cn_2026_10_08', chart)
        self.assertIn('与梅铃一起吧', history)
        self.assertIn('| 已结束 |', history)
        before = render_timeline(info, at - timedelta(seconds=1)).split('<details>', 1)[0]
        self.assertIn('OVERSEA_meirin_reward_2026_10_08', before)
        self.assertIn('OVERSEA_huche_shop_2026_09_17', before)
        self.assertIsNotNone(info.current('meirin_reward', 'OVERSEA', at - timedelta(seconds=1)))
        self.assertIsNone(info.current('meirin_reward', 'OVERSEA', at))

    def test_alignment_transition_preserves_confirmed_windows_and_daily_resets(self):
        from module.config.server import SERVER_UPDATE
        from tasks.activity import calendar
        import yaml

        self.assertEqual(yaml.safe_load(catalog.INFO_PATH.read_text(encoding='utf-8'))['cn_delay_days'], {})
        info = catalog.load_info()
        at = datetime.fromisoformat('2026-10-29T22:26:00+08:00')
        for kind, end in (
            ('huche_shop', '2026-11-19T11:00:00+08:00'),
            ('meirin_reward', '2026-11-19T11:00:00+08:00'),
            ('detective_search', '2026-11-19T11:00:00+08:00'),
            ('completion_record', '2026-12-18T03:00:00+08:00'),
            ('epic_path', '2026-12-18T03:00:00+08:00'),
        ):
            with self.subTest(kind=kind):
                period = info.current(kind, 'CN', at)
                self.assertEqual(period.end.isoformat(), end)
                self.assertFalse(period.inferred)
                self.assertIsNotNone(info.current(kind, 'CN', period.end - timedelta(seconds=1)))
                self.assertIsNone(info.current(kind, 'CN', period.end))
        cn_shop = info.current('huche_shop', 'CN', at)
        self.assertEqual(cn_shop.start.isoformat(), '2026-10-08T11:00:00+08:00')
        self.assertEqual(dict(cn_shop.values), {})  # CN prices and stock are not verified.
        self.assertIsNone(info.current('huche_shop', 'OVERSEA', at))
        self.assertIsNone(info.current('meirin_reward', 'OVERSEA', at))
        self.assertIsNone(info.current('support_reward', 'CN', at))
        for kind in ('completion_record', 'detective_search', 'epic_path'):
            self.assertEqual(info.current(kind, 'CN', at).start, at)
        for kind in ('completion_record', 'epic_path'):
            self.assertEqual(info.current(kind, 'OVERSEA', at).end.isoformat(), '2026-12-18T02:00:00+08:00')
        self.assertEqual(SERVER_UPDATE['CN-Official'], '03:00')
        self.assertEqual(SERVER_UPDATE['OVERSEA-Play'], '02:00')
        config = SimpleNamespace(Emulator_PackageName='com.zlongame.cn.epicseven')
        with patch.object(calendar.server, 'lang', 'cn'):
            self.assertEqual([p.mode for p in calendar.active_activities(config, at)], ['free_gacha_20'])
        current, history = render_timeline(info, at).split('<details>', 1)
        for kind in ('completion_record', 'detective_search', 'epic_path'):
            self.assertIn(f'CN_{kind}_2026_10_29', current)
        self.assertIn('CN_huche_shop_cn_2026_10_08', current)
        self.assertIn('CN_meirin_reward_2026_10_08', current)
        self.assertIn('德朗商店 | 国际服', history)

    def test_timeline_keeps_new_events_and_marks_unconfirmed_seasons(self):
        info = catalog.load_info()
        at = datetime.fromisoformat('2026-10-29T22:00:00+08:00')
        current = render_timeline(info, at).split('<details>', 1)[0]
        for kind in ('completion_record', 'detective_search', 'epic_path'):
            self.assertIn(f'OVERSEA_{kind}_2026_10_29', current)
        self.assertIn('国服竞技场通行证', current)
        self.assertIn('国际服暗影情报委托', current)
        self.assertIn('国服暗影情报委托', current)
        self.assertIn('当前期日期尚未确认', current)
        for kind, family in (('arena_pass', 'CN'), ('shadow_commission', 'CN'),
                             ('shadow_commission', 'OVERSEA')):
            self.assertIsNone(info.current(kind, family, at))
        future = render_timeline(info, datetime.fromisoformat('2027-01-01T00:00:00+08:00'))
        self.assertIn('当前没有已填写且尚未结束的时段', future)
        self.assertNotIn('```mermaid', future)

    def test_timeline_cli_respects_at_and_normalizes_timezone(self):
        at = datetime.fromisoformat('2026-10-29T03:00:00+00:00')
        expected = render_timeline(catalog.load_info(), at)
        self.assertIn('更新时点：2026-10-29 11:00', expected)
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-m', 'module.game_info',
                                 'timeline', '--at', at.isoformat()], cwd=WORKTREE, capture_output=True,
                                text=True, encoding='utf-8', check=True, timeout=30)
        self.assertEqual(result.stdout, expected)


class ConsumerTests(unittest.TestCase):
    def test_new_boundary_overrides_thursday_fallback(self):
        from tasks.arena.rewards import next_battle_pass_recheck
        record = datetime(2030, 1, 18, 12)
        self.assertEqual(next_battle_pass_recheck(record), datetime(2030, 1, 24, 11))
        boundary = datetime(2030, 1, 22, 11).astimezone()
        self.assertEqual(next_battle_pass_recheck(record, boundary), datetime(2030, 1, 22, 11))

    def test_season_change_or_cap_change_rechecks_full_progress(self):
        from tasks.arena import rewards
        # Stored observations use scheduler-local naive time. Dropping the
        # calendar timezone directly moves the observation past the CN season
        # boundary on UTC hosts and prevents the expected immediate recheck.
        local_now = NOW.astimezone().replace(tzinfo=None)
        rank = SimpleNamespace(value=38, total=38, time=local_now - timedelta(hours=1))
        config = SimpleNamespace(Emulator_PackageName="com.zlongame.cn.epicseven",
                                 Arena_ClaimBattlePassRewards=True, stored=SimpleNamespace(ArenaRank=rank))
        subject = rewards.ArenaRewardsMixin()
        subject.config = config
        info = parse(document(values={"max_level": 38}))
        with patch.object(rewards, "load_info", return_value=info), \
                patch.object(rewards, "datetime", wraps=datetime) as clock, \
                patch.object(catalog, "datetime", wraps=datetime) as facts_clock:
            # Both the reward guard and the facts lookup must observe the same
            # instant; freezing only the guard leaves level_cap date-dependent.
            clock.now.return_value = local_now
            facts_clock.now.return_value = NOW
            with patch.object(rewards, "Timer", side_effect=RuntimeError("entered flow")):
                with self.assertRaisesRegex(RuntimeError, "entered flow"):
                    rewards.ArenaRewardsMixin._claim_battle_pass_rewards(subject)
            rank.time = local_now
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
