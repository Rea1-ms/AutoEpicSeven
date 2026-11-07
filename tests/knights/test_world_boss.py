"""World-boss counters, owned stamina protection and deferred entry."""

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server

server.set_lang('global_cn')

from module.config.config_manual import ManualConfig
from tasks.base.resource_scheduling import ResourceCoordinator
from tasks.dungeon.dungeon import Combat
from tasks.knights.assets.assets_knights_world_boss_entry_resources import (
    WORLD_BOSS_NO_STAMINA,
    WORLD_BOSS_STAMINA_CANCEL,
)
from tasks.knights.knights import Knights
from tasks.knights.world_boss_resources import (
    record_world_boss_attempts,
    world_boss_stamina_reserve,
)
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ControlledClock, fixture_image
from tests.support.resources import ResourceConfig, resource_values

MANIFEST = Path(__file__).resolve().parents[1] / 'fixtures/knights/manifest.json'
ATTEMPTS = 'world-boss-attempts-20261107'
NO_STAMINA = 'world-boss-no-stamina-20261107'


def enabled_config():
    config = ResourceConfig()
    config.cross_set('Knights.Scheduler.Enable', True)
    config.cross_set('Knights.Knights.WorldBoss', True)
    config.task.command = 'Knights'
    return config


class WorldBossCaptureTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.task = object.__new__(Knights)
        self.task.config = enabled_config()
        self.task.device = SimpleNamespace(image=None)

    def test_real_entry_reads_two_attempts_and_six_stamina(self):
        self.task.device.image = fixture_image(MANIFEST, ATTEMPTS)
        self.assertEqual(self.task._read_world_boss_attempts(), 2)
        value = self.task._read_world_boss_stamina()
        self.assertIsNotNone(value)
        self.assertEqual((value.value, value.total), (6, 360))
        self.assertEqual(world_boss_stamina_reserve(self.task.config), 40)

    def test_missing_denominator_is_unknown_after_a_valid_read(self):
        self.task.device.image = fixture_image(MANIFEST, ATTEMPTS)
        self.assertEqual(self.task._read_world_boss_attempts(), 2)
        self.task.device.image[648:683, 1080:1104] = 0
        self.assertIsNone(self.task._read_world_boss_attempts())
        self.task.device.image[648:683, 1048:1104] = 0
        self.assertIsNone(self.task._read_world_boss_attempts())

    def test_stamina_popup_has_specific_cancel_and_rejects_entry(self):
        image = fixture_image(MANIFEST, NO_STAMINA)
        self.assertTrue(WORLD_BOSS_NO_STAMINA.match_template(image))
        self.assertTrue(WORLD_BOSS_STAMINA_CANCEL.match_template(image))
        image = fixture_image(MANIFEST, ATTEMPTS)
        self.assertFalse(WORLD_BOSS_NO_STAMINA.match_template(image))
        self.assertFalse(WORLD_BOSS_STAMINA_CANCEL.match_template(image))


class WorldBossResourceTests(unittest.TestCase):
    def setUp(self):
        self.config = enabled_config()
        self.now = datetime(2026, 11, 7, 13, 0)
        self.day = self.now.replace(hour=2)
        patches = (
            patch('tasks.knights.world_boss_resources.get_server_last_update', side_effect=lambda _: self.day),
            patch('tasks.knights.world_boss_resources.get_server_next_update', side_effect=lambda _: self.day + timedelta(days=1)),
            patch('tasks.knights.world_boss_resources.local_now', side_effect=lambda: self.now),
            patch('tasks.base.resource_scheduling.local_now', side_effect=lambda: self.now),
            patch('tests.support.resources.local_now', side_effect=lambda: self.now),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_complete_counter_required_with_zero_distinct_from_unknown(self):
        for text, expected in [('2/2', 2), ('1/2', 1), ('0/2', 0), (' 1 / 2 ', 1),
                               ('2/', None), ('22', None), ('3/2', None), ('1/3', None), ('', None)]:
            with self.subTest(text=text):
                self.assertEqual(Knights._parse_world_boss_attempts(text), expected)

    def test_reserve_defaults_to_forty_then_uses_observed_remainder(self):
        self.assertEqual(world_boss_stamina_reserve(self.config), 40)
        for remaining, expected in [(1, 20), (0, 0), (2, 40)]:
            record_world_boss_attempts(self.config, remaining)
            self.assertEqual(world_boss_stamina_reserve(self.config), expected)
        self.day += timedelta(days=1)
        self.assertEqual(world_boss_stamina_reserve(self.config), 40)

    def test_disabled_world_boss_or_task_has_no_reserve(self):
        for path in ('Knights.Scheduler.Enable', 'Knights.Knights.WorldBoss'):
            with self.subTest(path=path):
                self.config.cross_set(path, False)
                self.assertEqual(world_boss_stamina_reserve(self.config), 0)
                self.config.cross_set(path, True)

    def test_server_observations_are_not_shared(self):
        record_world_boss_attempts(self.config, 0)
        self.config.Emulator_PackageName = 'com.zlongame.cn.epicseven'
        self.assertEqual(world_boss_stamina_reserve(self.config), 40)

    def test_combat_spends_only_the_unreserved_amount_and_keeps_priority(self):
        task = object.__new__(Combat)
        task.config = self.config
        for owned, expected in [(39, 0), (40, 0), (60, 20), (120, 80)]:
            self.assertEqual(task._combat_available_stamina(owned), expected)
        record_world_boss_attempts(self.config, 1)
        self.assertEqual(task._combat_available_stamina(40), 20)
        record_world_boss_attempts(self.config, 0)
        self.assertEqual(task._combat_available_stamina(40), 40)
        priority = [item.strip() for item in ManualConfig.SCHEDULER_PRIORITY.split('>')]
        self.assertLess(priority.index('Combat'), priority.index('Knights'))

    def test_only_next_entry_is_required_for_retry_not_two_entries(self):
        task = object.__new__(Knights)
        task.config = self.config
        task._defer_world_boss(resource_values(stamina=(3, 360))['stamina'])
        self.assertEqual(task._world_boss_retry_target, self.now + timedelta(minutes=68))
        self.config.task_delay(target=task._world_boss_retry_target)
        task._schedule_world_boss_wait()
        sync = ResourceCoordinator(self.config)
        sync.observe(resource_values(stamina=(20, 360)), 'Mail')
        self.assertEqual(self.config.cross_get('Knights.Scheduler.NextRun'), self.now)

    def test_unknown_urgent_stamina_never_spends_the_reserved_balance(self):
        task = object.__new__(Combat)
        task.config = self.config
        task._navigate_urgent_tasks = Mock(return_value=True)
        task._urgent_tasks_unavailable = False
        task._urgent_tasks_daily_complete = False
        task._urgent_tasks_remaining_count = 2
        task._read_urgent_tasks_stamina = Mock(return_value=None)
        task._prepare_fast_combat = Mock(side_effect=AssertionError('unknown stamina'))
        task._prepare_repeat_combat = Mock(side_effect=AssertionError('unknown stamina'))
        task._run_normal_combat = Mock(side_effect=AssertionError('unknown stamina'))
        self.assertEqual(task._run_urgent_tasks_daily(), (False, 0))
        task._prepare_fast_combat.assert_not_called()
        task._prepare_repeat_combat.assert_not_called()
        task._run_normal_combat.assert_not_called()
        self.assertEqual(world_boss_stamina_reserve(self.config), 40)

    def test_combat_wait_includes_reserve_and_releases_on_observed_zero(self):
        self.config.task.command = 'Combat'
        self.config.task_delay(minute=120)
        sync = ResourceCoordinator(self.config)
        sync.wait('Combat', 'stamina', 20)
        sync.observe(resource_values(stamina=(40, 360)), 'Main')
        self.assertEqual(self.config.cross_get('Combat.Scheduler.NextRun'), self.now + timedelta(minutes=80))
        record_world_boss_attempts(self.config, 0)
        sync.observe(resource_values(stamina=(40, 360)), 'WorldBoss')
        self.assertEqual(self.config.cross_get('Combat.Scheduler.NextRun'), self.now)

    def test_wait_invalidated_when_world_boss_setting_changes(self):
        self.config.task_delay(minute=120)
        sync = ResourceCoordinator(self.config)
        sync.wait('Knights', 'stamina', 20)
        previous = self.config.cross_get('Knights.Scheduler.NextRun')
        self.config.cross_set('Knights.Knights.WorldBoss', False)
        sync.observe(resource_values(stamina=(100, 360)), 'Main')
        self.assertEqual(self.config.cross_get('Knights.Scheduler.NextRun'), previous)


class WorldBossReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames):
        replay = InteractionReplay(Knights, frames, self.clock, guards=False)
        task = replay.task
        task.config = enabled_config()
        task.handle_network_error = lambda: False
        task._handle_world_boss_touch_to_close = lambda **kw: False
        task._is_world_boss_home = lambda **kw: 'CHOOSE_TEAM' in replay.frame['buttons']
        task._is_choose_team_ready = lambda **kw: 'CHOOSE_TEAM' in replay.frame['buttons']
        def attempts():
            value = replay.frame.get('attempts')
            if value is not None:
                task._world_boss_remaining_attempts = value
                record_world_boss_attempts(task.config, value)
            return value
        task._read_world_boss_attempts = attempts
        task._read_world_boss_stamina = lambda: resource_values(
            stamina=(replay.frame.get('stamina', 80), 360))['stamina']
        return replay

    def test_zero_attempts_exit_without_any_click(self):
        replay = self.replay([scene('CHOOSE_TEAM', attempts=0, stamina=0)])
        self.assertEqual(replay.task._world_boss_once(), 'exhausted')
        self.assertEqual(replay.actions, [])

    def test_insufficient_stamina_defers_without_starting_or_buying(self):
        replay = self.replay([scene('CHOOSE_TEAM', attempts=2, stamina=3)])
        self.assertEqual(replay.task._world_boss_once(), 'no_stamina')
        self.assertEqual(replay.actions, [])
        self.assertIsNotNone(replay.task._world_boss_retry_target)

    def test_unknown_attempts_wait_and_never_enter_or_finish(self):
        replay = self.replay([scene('CHOOSE_TEAM', attempts=None)] * 35)
        self.assertEqual(replay.task._world_boss_once(), 'resource_unknown')
        self.assertEqual(replay.actions, [])

    def test_popup_cancel_retries_and_back_is_confirmed_before_exit(self):
        popup = scene('WORLD_BOSS_NO_STAMINA', 'WORLD_BOSS_STAMINA_CANCEL', 'BATTLE_START', seconds=0.6)
        replay = self.replay([popup] * 4 + [scene('BATTLE_START', seconds=0.6)] * 4
                             + [scene('CHOOSE_TEAM', attempts=1, stamina=3)])
        self.assertEqual(replay.task._world_boss_once(), 'no_stamina')
        self.assertEqual(replay.clicks('WORLD_BOSS_STAMINA_CANCEL'), [0, 2])
        self.assertEqual(replay.clicks('BACK'), [4, 6])
        self.assertEqual(replay.clicks('BATTLE_START'), [])
        self.assertEqual(replay.index, 8)

    def test_sufficient_single_entry_starts_once_despite_repeated_frame(self):
        replay = self.replay([
            scene('CHOOSE_TEAM', attempts=2, stamina=20),
            scene('FORM_A_TEAM'), scene('AUTO_CONFIG', 'BATTLE_START', stamina=20),
            scene('BATTLE_START', stamina=20), scene('BATTLE_START', stamina=20),
            scene('BATTLE_RESULT_CONFIRM'), scene('CHOOSE_TEAM', attempts=1, stamina=0),
        ])
        self.assertEqual(replay.task._world_boss_once(), 'completed')
        self.assertEqual(replay.clicks('BATTLE_START'), [2])
        self.assertEqual(replay.clicks('BATTLE_RESULT_CONFIRM'), [5])
        self.assertEqual(replay.index, 6)

    def test_reentry_reads_manual_remaining_instead_of_local_rounds(self):
        replay = self.replay([scene('CHOOSE_TEAM', attempts=1, stamina=0)])
        replay.task._world_boss_completed_rounds = 0
        self.assertEqual(replay.task._world_boss_once(), 'no_stamina')
        self.assertEqual(world_boss_stamina_reserve(replay.task.config), 20)

    def test_resuming_team_panel_returns_to_counter_before_entry(self):
        replay = self.replay([scene('BATTLE_START'), scene('CHOOSE_TEAM', attempts=0)])
        self.assertEqual(replay.task._world_boss_once(), 'exhausted')
        self.assertEqual(replay.actions, [(0, 'BACK')])

    def test_completed_round_rereads_remaining_and_defers_the_second_entry(self):
        replay = self.replay([
            scene('CHOOSE_TEAM', attempts=2, stamina=20), scene('FORM_A_TEAM'),
            scene('AUTO_CONFIG', 'BATTLE_START', stamina=20),
            scene('BATTLE_RESULT_CONFIRM'), scene('CHOOSE_TEAM', attempts=1, stamina=0),
        ])
        task = replay.task
        task._enter_world_boss = Mock(return_value='entered')
        task._claim_weekly_contribution_rewards = Mock(return_value=True)
        task._back_to_knights_from_world_boss = Mock(return_value=True)
        self.assertTrue(task.run_world_boss())
        self.assertEqual(task._world_boss_completed_rounds, 1)
        self.assertEqual(world_boss_stamina_reserve(task.config), 20)
        self.assertIsNotNone(task._world_boss_retry_target)
        self.assertEqual(replay.clicks('BATTLE_START'), [2])
        task._back_to_knights_from_world_boss.assert_called_once()

    def test_insufficient_boss_keeps_other_knights_tasks_and_earliest_retry(self):
        for reminder_minutes in (30, 120):
            with self.subTest(reminder_minutes=reminder_minutes):
                replay = self.replay([scene('CHOOSE_TEAM', attempts=2, stamina=3)])
                task = replay.task
                config = task.config
                for option, value in {
                    'Knights_ClaimSigninRateReward': False, 'Knights_WeeklyTask': True,
                    'Knights_Support': True, 'Knights_SupportLowerLevelFairyFlower': True,
                    'Knights_SupportBeginnerPenguin': False,
                    'KnightsTeamBattle_TeamBattle': False, 'Knights_WorldBoss': True,
                }.items():
                    setattr(config, option, value)
                task.device.app_is_running = lambda: True
                task._reset_team_battle_status_runtime = Mock()
                task._enter_knights = Mock(return_value=True)
                task._settle_knights_home = Mock()
                task._pending_shop_purchases = Mock(return_value=['explicit-purchase'])
                task._enter_world_boss = Mock(return_value='entered')
                task._claim_weekly_contribution_rewards = Mock(return_value=True)
                task._back_to_knights_from_world_boss = Mock(return_value=True)
                task.ui_goto = Mock()
                order = []
                for method in ('run_support', 'run_shop', 'run_weekly_task'):
                    setattr(task, method, Mock(side_effect=lambda method=method, order=order, **kw: order.append(method) or True))
                now = datetime(2026, 11, 7, 13, 0)
                reminder = now + timedelta(minutes=reminder_minutes)
                task._get_team_battle_next_delay_target = Mock(return_value=reminder)
                with patch('tasks.knights.world_boss_resources.local_now', return_value=now), \
                        patch('tasks.knights.world_boss_resources.get_server_next_update',
                              return_value=now + timedelta(hours=13)):
                    self.assertTrue(task.run())
                self.assertEqual(order, ['run_support', 'run_shop', 'run_weekly_task'])
                self.assertEqual(config.cross_get('Knights.Scheduler.NextRun'),
                                 min(reminder, now + timedelta(minutes=68)))
                _, bucket = ResourceCoordinator(config)._load()
                self.assertEqual(bucket['waits']['Knights']['need'], 20)
                self.assertEqual(world_boss_stamina_reserve(config), 40)
                self.assertEqual(replay.actions, [])
