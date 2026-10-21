"""Arena settlement time boundaries independent of the host's local timezone."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from module.config import server
from module.config.config_generated import GeneratedConfig
from module.config.config_updater import ConfigUpdater

server.set_lang('global_cn')

from tasks.arena.entry import is_arena_settling_period


class ArenaRuleTests(unittest.TestCase):
    def test_weekly_reward_dropdown_defaults_to_skystone(self):
        root = Path(__file__).resolve().parents[2]
        arguments = yaml.safe_load((root / 'module/config/argument/argument.yaml').read_text(encoding='utf-8'))
        self.assertEqual(arguments['Arena']['WeeklyReward']['value'], 'Skystone')
        self.assertEqual(arguments['Arena']['WeeklyReward']['option'], ['Skystone', 'MysticMedals'])
        emitted = json.loads((root / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        self.assertEqual(emitted['Arena']['Arena']['WeeklyReward'], {
            'type': 'select', 'value': 'Skystone', 'option': ['Skystone', 'MysticMedals'],
        })
        template = json.loads((root / 'config/template.json').read_text(encoding='utf-8'))
        self.assertEqual(template['Arena']['Arena']['WeeklyReward'], 'Skystone')
        self.assertEqual(GeneratedConfig.Arena_WeeklyReward, 'Skystone')
        for language in ('zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'es-ES'):
            words = json.loads((root / 'module/config/i18n' / f'{language}.json').read_text(encoding='utf-8'))
            for key in ('name', 'help', 'Skystone', 'MysticMedals'):
                self.assertTrue(words['Arena']['WeeklyReward'][key])
                self.assertNotIn('Arena.WeeklyReward.', words['Arena']['WeeklyReward'][key])
        for saved, expected in (({}, 'Skystone'), ({'WeeklyReward': 'MysticMedals'}, 'MysticMedals')):
            with self.subTest(saved=saved):
                updated = ConfigUpdater().config_update({'Arena': {'Arena': saved}})
                self.assertEqual(updated['Arena']['Arena']['WeeklyReward'], expected)

    def test_settlement_window_includes_start_and_excludes_end(self):
        for timestamp, expected in (
            ('2026-10-18T19:59:59+08:00', False),
            ('2026-10-18T20:00:00+08:00', True),
            ('2026-10-18T23:59:59+08:00', True),
            ('2026-10-19T00:00:00+08:00', True),
            ('2026-10-19T01:59:59+08:00', True),
            ('2026-10-19T02:00:00+08:00', False),
            ('2026-10-20T01:00:00+08:00', False),
        ):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(is_arena_settling_period(datetime.fromisoformat(timestamp)), expected)

    def test_settlement_window_uses_china_time_for_offsets_and_naive_values(self):
        china = timezone(timedelta(hours=8))
        for aware, expected in (
            (datetime(2026, 10, 18, 19, 59, 59, tzinfo=china), False),
            (datetime(2026, 10, 18, 20, tzinfo=china), True),
            (datetime(2026, 10, 19, 1, 59, 59, tzinfo=china), True),
            (datetime(2026, 10, 19, 2, tzinfo=china), False),
        ):
            for value in (
                aware.replace(tzinfo=None),
                aware.astimezone(timezone.utc),
                aware.astimezone(timezone(timedelta(hours=-7))),
            ):
                with self.subTest(value=value):
                    self.assertEqual(is_arena_settling_period(value), expected)
