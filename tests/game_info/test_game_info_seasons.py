# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Cycle facts remain separate from explicit server season windows."""
import unittest
from datetime import datetime, timedelta
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE
from module.game_info.catalog import load_info
from tests.support.game_info import document, parse, NOW


class CycleFactsTests(unittest.TestCase):
    def test_confirmed_cycles_are_queryable_for_both_servers(self):
        info = load_info()
        for family in ('CN', 'OVERSEA'):
            for kind, cycle, maximum in (('arena_pass', 84, 38), ('shadow_commission', 21, 30)):
                with self.subTest(family=family, kind=kind):
                    values = info.values(kind, family)
                    self.assertEqual(values['cycle_days'], cycle)
                    self.assertEqual(values['max_level'], maximum)

    def test_invalid_cycle_in_defaults_or_period_overrides_is_rejected(self):
        for value in (True, False, 0, -21, '84', 21.0):
            for location in ('defaults', 'values', 'cn_values'):
                with self.subTest(value=value, location=location):
                    data = document()
                    if location == 'defaults':
                        data['defaults']['arena_pass']['cycle_days'] = value
                    else:
                        data['events'][0][location] = {'cycle_days': value}
                    with self.assertRaises(ValueError):
                        parse(data)

    def test_specific_cycle_can_override_shared_default_per_server(self):
        data = document(values={'cycle_days': 84}, cn_values={'cycle_days': 63})
        data['defaults']['arena_pass']['cycle_days'] = 90
        info = parse(data)
        self.assertEqual(info.values('arena_pass', 'CN', NOW)['cycle_days'], 63)
        self.assertEqual(info.values('arena_pass', 'OVERSEA', NOW)['cycle_days'], 84)

    def test_cycle_length_alone_does_not_create_unconfirmed_future_seasons(self):
        data = document()
        data['defaults']['arena_pass']['cycle_days'] = 84
        info = parse(data)
        future = NOW + timedelta(days=365)
        self.assertIsNone(info.current('arena_pass', 'CN', future))
        self.assertIsNone(info.next_change('arena_pass', 'CN', future))
        self.assertEqual(info.values('arena_pass', 'CN', future)['cycle_days'], 84)
        self.assertEqual(info.level_cap('arena_pass', 'CN', future), 38)


class CurrentSeasonTests(unittest.TestCase):
    def test_arena_seasons_and_endpoints_are_separate_by_server(self):
        info = load_info()
        at = datetime.fromisoformat('2026-09-19T17:00:00+08:00')
        for family, season, end in (
            ('CN', 3, '2026-10-04T20:00:00+08:00'),
            ('OVERSEA', 4, '2026-12-06T20:00:00+08:00'),
        ):
            with self.subTest(family=family):
                period = info.current('arena_pass', family, at)
                self.assertEqual(period.end.isoformat(), end)
                self.assertEqual(period.values['season'], season)
                self.assertEqual(period.end - period.start, timedelta(days=84))
                self.assertEqual(info.next_change('arena_pass', family, at), period.end)
                self.assertFalse(period.inferred)  # No CN-delay rule applies.
                self.assertIn('倒推', period.source)

    def test_arena_end_agrees_with_existing_sunday_settlement_window(self):
        from tasks.arena.entry import is_arena_settling_period
        info = load_info()
        for period in info.periods:
            if period.kind == 'arena_pass':
                self.assertEqual(period.end.weekday(), 6)
                self.assertTrue(is_arena_settling_period(period.end))
                self.assertFalse(is_arena_settling_period(period.end - timedelta(seconds=1)))

    def test_shadow_servers_share_the_same_21_day_period(self):
        info = load_info()
        at = datetime.fromisoformat('2026-09-19T17:00:00+08:00')
        periods = [info.current('shadow_commission', family, at) for family in ('CN', 'OVERSEA')]
        self.assertEqual(periods[0].event_id, periods[1].event_id)
        for period in periods:
            self.assertEqual(period.start.isoformat(), '2026-09-17T11:00:00+08:00')
            self.assertEqual(period.end.isoformat(), '2026-10-08T11:00:00+08:00')
            self.assertEqual(period.end - period.start, timedelta(days=21))
            self.assertEqual((period.end - at).days, 18)

    def test_period_boundaries_do_not_extend_finished_seasons(self):
        info = load_info()
        for period in info.periods:
            if period.kind not in ('arena_pass', 'shadow_commission'):
                continue
            with self.subTest(event=period.event_id, family=period.server_family):
                self.assertEqual(info.current(period.kind, period.server_family, period.start), period)
                self.assertEqual(info.current(period.kind, period.server_family, period.end - timedelta(seconds=1)), period)
                self.assertIsNone(info.current(period.kind, period.server_family, period.end))
                expected = 38 if period.kind == 'arena_pass' else 30
                self.assertEqual(info.level_cap(period.kind, period.server_family, period.end), expected)

    def test_seasons_appear_in_timeline_but_not_activity_dispatch(self):
        from module.game_info.timeline import render_timeline
        from tasks.activity.calendar import load_calendar
        kinds = {period.mode for period in load_calendar()}
        self.assertNotIn('arena_pass', kinds)
        self.assertNotIn('shadow_commission', kinds)
        timeline = render_timeline(load_info())
        self.assertIn('第三赛季', timeline)
        self.assertIn('第四赛季', timeline)
        self.assertIn('暗影情报委托', timeline)
