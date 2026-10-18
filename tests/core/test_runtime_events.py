"""Local progress and system notifications without accounts or external delivery."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil
import yaml
from onepush.providers.custom import Custom
from requests import Response

from module.config.config_updater import ConfigUpdater
from module.notify.config import has_system_provider
from module.notify.notify import handle_notify, is_system_provider
from module.runtime_events import RuntimeEvents
from tasks.knights.team_battle_status import (
    KnightsTeamBattleStatusMixin,
    TeamBattleCrestStatus,
)
from tests.support.offline import ControlledClock, retained_directory


class RuntimeEventsTests(unittest.TestCase):
    def setUp(self):
        directory = retained_directory('runtime-events')
        self.path = Path(directory.__enter__()) / 'events.sqlite3'
        self.addCleanup(directory.__exit__, None, None, None)
        self.clock = ControlledClock()
        self.events = self.reader()

    def reader(self):
        return RuntimeEvents(self.path, clock=lambda: self.clock.now)

    def test_progress_is_shared_by_instances_and_isolated_by_configuration(self):
        reader = self.reader()
        self.assertIsNone(reader.get_progress('offline-a'))
        self.assertTrue(self.events.publish_progress(
            'offline-a', 'running', completed=1, total=3, task='Mail',
        ))
        self.assertTrue(self.events.publish_progress(
            'offline-b', 'waiting', completed=2, total=2,
        ))
        # A long battle must keep its progress while its worker identity is alive.
        self.clock.advance(600)
        progress = reader.get_progress('offline-a')
        self.assertEqual(
            {key: progress[key] for key in ('state', 'completed', 'total', 'task')},
            {'state': 'running', 'completed': 1, 'total': 3, 'task': 'Mail'},
        )
        self.assertEqual(reader.get_progress('offline-b')['state'], 'waiting')
        self.assertIsNone(reader.get_progress('offline-missing'))

    def test_desktop_priority_and_concurrent_consumption_deliver_exactly_once(self):
        self.assertTrue(self.events.register_client('desktop-old', 'electron'))
        self.clock.advance(1)
        self.assertTrue(self.events.register_client('desktop-new', 'electron'))
        self.clock.advance(1)
        self.assertTrue(self.events.register_client('browser-newest', 'browser'))
        self.assertTrue(self.events.notify('Offline title', 'Offline body'))
        self.assertEqual(self.reader().poll_notifications('browser-newest'), [])
        self.assertEqual(self.reader().poll_notifications('desktop-old'), [])
        barrier = Barrier(2)

        def consume(_):
            reader = self.reader()
            barrier.wait(timeout=5)
            return reader.poll_notifications('desktop-new')

        with ThreadPoolExecutor(max_workers=2) as workers:
            received = [item for batch in workers.map(consume, range(2)) for item in batch]
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0]['id'], str)
        self.assertTrue(received[0]['id'])
        self.assertEqual(received[0]['title'], 'Offline title')
        self.assertEqual(received[0]['content'], 'Offline body')
        self.assertEqual(self.reader().poll_notifications('desktop-new'), [])

    def test_expired_clients_and_messages_are_not_replayed_on_reconnect(self):
        self.assertFalse(self.events.notify('No client', 'Must not queue'))
        self.events.register_client('browser', 'browser')
        self.assertTrue(self.events.notify('Old message', 'Must expire with its client'))
        self.clock.advance(16)
        self.assertFalse(self.events.notify('Expired client', 'Must not queue'))
        self.events.register_client('browser', 'browser')
        self.assertEqual(self.reader().poll_notifications('browser'), [])
        self.assertTrue(self.events.notify('Unconsumed', 'Must expire despite heartbeats'))
        for _ in range(3):
            self.clock.advance(10)
            self.events.register_client('browser', 'browser')
        self.assertEqual(self.reader().poll_notifications('browser'), [])
        self.assertTrue(self.events.notify('Fresh message', 'Current session'))
        self.assertEqual(self.reader().poll_notifications('browser')[0]['title'], 'Fresh message')

    def test_unregister_discards_old_delivery_and_falls_back_to_open_browser(self):
        self.events.register_client('browser', 'browser')
        self.events.register_client('desktop', 'electron')
        self.assertTrue(self.events.notify('Before close', 'Desktop only'))
        self.events.unregister_client('desktop')
        self.assertTrue(self.events.notify('After close', 'Browser only'))
        self.assertEqual(self.reader().poll_notifications('desktop'), [])
        messages = self.reader().poll_notifications('browser')
        self.assertEqual([item['title'] for item in messages], ['After close'])
        self.events.unregister_client('browser')
        self.assertFalse(self.events.notify('All closed', 'Must not queue'))

    def test_dead_or_reused_worker_cannot_leave_running_progress(self):
        for state in ('running', 'waiting', 'completed', 'error'):
            self.events.publish_progress(state, state, completed=1, total=2, task='Mail')
        with patch('module.runtime_events.psutil.Process', side_effect=psutil.NoSuchProcess(123)):
            for state in ('running', 'waiting'):
                progress = self.reader().get_progress(state)
                self.assertEqual(progress['state'], 'stopped')
                self.assertEqual(progress['task'], '')
                self.assertEqual(progress['completed'], 1)
            for state in ('completed', 'error'):
                self.assertEqual(self.reader().get_progress(state)['state'], state)
        replacement = Mock()
        replacement.is_running.return_value = True
        replacement.create_time.return_value = 0.0
        replacement.status.return_value = psutil.STATUS_RUNNING
        with patch('module.runtime_events.psutil.Process', return_value=replacement):
            self.assertEqual(self.reader().get_progress('running')['state'], 'stopped')


class NotificationRoutingTests(unittest.TestCase):
    def test_independent_switch_sends_locally_before_remote_without_forwarding_it(self):
        response = Response()
        response.status_code = 200
        order = []
        notifier = SimpleNamespace(params={'required': []}, name='offline', notify=Mock(
            side_effect=lambda **kwargs: order.append('remote') or response,
        ))
        with patch('module.notify.notify.runtime_events') as events, \
                patch('module.notify.notify.get_notifier', return_value=notifier):
            events.notify.side_effect = lambda **kwargs: order.append('system') or True
            self.assertTrue(handle_notify('provider: telegram', system_notification=True,
                                          title='Title', content='Body'))
            self.assertEqual(order, ['system', 'remote'])
            events.notify.assert_called_once_with(title='Title', content='Body')
            notifier.notify.assert_called_once_with(title='Title', content='Body')

    def test_local_failure_or_exception_does_not_prevent_remote_delivery(self):
        response = Response()
        response.status_code = 200
        for result in (False, RuntimeError('offline queue failure')):
            notifier = SimpleNamespace(params={'required': []}, name='offline',
                                       notify=Mock(return_value=response))
            with self.subTest(result=result), \
                    patch('module.notify.notify.runtime_events') as events, \
                    patch('module.notify.notify.get_notifier', return_value=notifier):
                if isinstance(result, Exception):
                    events.notify.side_effect = result
                else:
                    events.notify.return_value = result
                self.assertTrue(handle_notify('provider: telegram', system_notification=True,
                                              title='Title', content='Body'))
                events.notify.assert_called_once()
                notifier.notify.assert_called_once()

    def test_remote_failure_or_invalid_config_does_not_undo_local_acceptance(self):
        for config in ('invalid: [', 'provider: offline'):
            with self.subTest(config=config), \
                    patch('module.notify.notify.runtime_events') as events, \
                    patch('module.notify.notify.get_notifier', side_effect=RuntimeError('offline')):
                events.notify.return_value = True
                self.assertTrue(handle_notify(config, system_notification=True,
                                              title='Title', content='Body'))
                events.notify.assert_called_once_with(title='Title', content='Body')

    def test_explicit_disable_overrides_legacy_local_provider(self):
        with patch('module.notify.notify.runtime_events') as events, \
                patch('module.notify.notify.get_notifier') as network:
            self.assertFalse(handle_notify('provider: system', system_notification=False))
            events.notify.assert_not_called()
            network.assert_not_called()

    def test_channel_list_continues_after_success_failure_and_invalid_entries(self):
        success = Response()
        success.status_code = 200
        failure = Response()
        failure.status_code = 503
        for first in (success, failure, RuntimeError('offline transport failure')):
            one = Mock()
            one.side_effect = first if isinstance(first, Exception) else None
            one.return_value = first
            two = Mock(return_value=success)
            notifiers = {
                'first': SimpleNamespace(params={'required': []}, name='first', notify=one),
                'second': SimpleNamespace(params={'required': []}, name='second', notify=two),
            }
            config = '- provider: first\n- not-a-mapping\n- provider: second\n'
            with self.subTest(first=first), \
                    patch('module.notify.notify.get_notifier', side_effect=notifiers.__getitem__), \
                    patch('module.notify.notify.runtime_events') as events:
                self.assertTrue(handle_notify(config, system_notification=False, title='Title'))
                one.assert_called_once_with(title='Title')
                two.assert_called_once_with(title='Title')
                events.notify.assert_not_called()

    def test_all_failed_destinations_remain_retryable(self):
        notifier = SimpleNamespace(params={'required': []}, name='offline', notify=Mock(return_value=None))
        with patch('module.notify.notify.get_notifier', return_value=notifier), \
                patch('module.notify.notify.runtime_events') as events:
            events.notify.return_value = False
            self.assertFalse(handle_notify('- provider: first\n- provider: second',
                                           system_notification=True, title='Title'))
            self.assertEqual(notifier.notify.call_count, 2)
            events.notify.assert_called_once()

    def test_legacy_yaml_documents_keep_merging_provider_and_options(self):
        response = Response()
        response.status_code = 200
        notifier = SimpleNamespace(params={'required': []}, name='offline', notify=Mock(return_value=response))
        with patch('module.notify.notify.get_notifier', return_value=notifier):
            self.assertTrue(handle_notify('provider: telegram\n---\nchat_id: offline\n', title='Title'))
            notifier.notify.assert_called_once_with(chat_id='offline', title='Title')

    def test_custom_channel_builds_message_data_when_no_data_mapping_is_configured(self):
        response = Response()
        response.status_code = 200
        notifier = Mock(spec=Custom)
        notifier.params = {'required': []}
        notifier.name = 'offline-custom'
        notifier.notify.return_value = response
        with patch('module.notify.notify.get_notifier', return_value=notifier):
            self.assertTrue(handle_notify('provider: custom', title='Title', content='Body'))
        notifier.notify.assert_called_once_with(
            title='Title', content='Body', datatype='json', data={'title': 'Title', 'content': 'Body'},
        )

    def test_team_battle_reminder_passes_independent_switch_and_only_deduplicates_acceptance(self):
        now = datetime(2026, 10, 20, 10, 30, tzinfo=timezone.utc)
        for enabled in (False, True):
            for accepted in (False, True):
                task = KnightsTeamBattleStatusMixin()
                task.config = SimpleNamespace(
                    KnightsTeamBattle_Reminder=True, KnightsTeamBattle_ReminderLeadMinutes=60,
                    KnightsTeamBattle_ReminderLastEnd='', Error_OnePushConfig='provider: telegram',
                    Error_SystemNotification=enabled, config_name='offline-test',
                )
                with self.subTest(enabled=enabled, accepted=accepted), \
                        patch('tasks.knights.team_battle_status.datetime') as clock, \
                        patch('tasks.knights.team_battle_status.handle_notify', return_value=accepted) as notify:
                    clock.now.return_value = now
                    status = TeamBattleCrestStatus(current=3, remain=3, total=3)
                    task._send_or_schedule_team_battle_reminder(status)
                    self.assertEqual(notify.call_args.args[0], 'provider: telegram')
                    self.assertIs(notify.call_args.kwargs['system_notification'], enabled)
                    self.assertEqual(task.config.KnightsTeamBattle_ReminderLastEnd,
                                     '2026-10-20 11:00:00' if accepted else '')
                    task._send_or_schedule_team_battle_reminder(status)
                    self.assertEqual(notify.call_count, 1 if accepted else 2)

    def test_multiple_legacy_system_entries_only_queue_once(self):
        with patch('module.notify.notify.runtime_events') as events:
            events.notify.return_value = True
            self.assertTrue(handle_notify('- provider: system\n- provider: 系统', title='Title'))
            events.notify.assert_called_once_with(title='Title', content='')

    def test_invalid_legacy_settings_do_not_enable_system_notifications(self):
        for value in ('invalid: [', 'not-a-mapping', None, '- 1', '', '---\n---\n'):
            with self.subTest(value=value):
                self.assertFalse(has_system_provider(value))

    def test_system_provider_uses_local_queue_and_reports_acceptance(self):
        events = Mock(spec=RuntimeEvents)
        with patch('module.notify.notify.runtime_events', events), \
                patch('module.notify.notify.get_notifier') as network:
            for provider in ('system', '系统'):
                config = f'provider: {provider}'
                self.assertTrue(is_system_provider(config))
                for accepted in (False, True):
                    with self.subTest(provider=provider, accepted=accepted):
                        events.notify.return_value = accepted
                        self.assertIs(
                            handle_notify(config, title='Offline title', content='Offline body'),
                            accepted,
                        )
                        events.notify.assert_called_with(title='Offline title', content='Offline body')
            network.assert_not_called()
        self.assertFalse(is_system_provider('provider: telegram'))
        self.assertFalse(is_system_provider('invalid: ['))

    def test_network_providers_preserve_real_response_success_semantics(self):
        success = Response()
        success.status_code = 200
        failure = Response()
        failure.status_code = 503
        rejected = Response()
        rejected.status_code = 200
        rejected._content = b'{"status":"failed","wording":"offline rejection"}'
        for provider, response, expected in (
            ('telegram', success, True),
            ('telegram', failure, False),
            ('telegram', None, False),
            ('telegram', object(), False),
            ('gocqhttp', rejected, False),
        ):
            notifier = SimpleNamespace(
                params={'required': []}, name='offline', notify=Mock(return_value=response),
            )
            with self.subTest(provider=provider, response=response), \
                    patch('module.notify.notify.get_notifier', return_value=notifier), \
                    patch('module.notify.notify.runtime_events') as events:
                self.assertIs(
                    handle_notify(f'provider: {provider}', title='Title', content='Body'), expected,
                )
                notifier.notify.assert_called_once_with(title='Title', content='Body')
                events.notify.assert_not_called()


class NotificationConfigMigrationTests(unittest.TestCase):
    def test_legacy_local_opt_in_is_migrated_without_changing_remote_settings(self):
        updater = ConfigUpdater()
        for provider, expected in (('system', True), ('系统', True), ('telegram', False)):
            with self.subTest(provider=provider):
                old = {'Alas': {'Error': {'OnePushConfig': f'provider: {provider}'}}}
                updated = updater.config_update(old)
                self.assertIs(updated['Alas']['Error']['SystemNotification'], expected)
                self.assertEqual(updated['Alas']['Error']['OnePushConfig'], f'provider: {provider}')

    def test_saved_switch_wins_over_legacy_provider_on_repeated_reload(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                old = {'Alas': {'Error': {'OnePushConfig': 'provider: system',
                                          'SystemNotification': enabled}}}
                result = ConfigUpdater().config_update(old)
                self.assertIs(result['Alas']['Error']['SystemNotification'], enabled)
                self.assertEqual(result, ConfigUpdater().config_update(result))

    def test_switch_source_defaults_off_and_all_languages_have_real_labels(self):
        import json

        root = Path(__file__).resolve().parents[2]
        argument = yaml.safe_load((root / 'module/config/argument/argument.yaml').read_text(encoding='utf-8'))
        self.assertIs(argument['Error']['SystemNotification'], False)
        generated = json.loads((root / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        self.assertEqual(generated['Alas']['Error']['SystemNotification'], {'type': 'checkbox', 'value': False})
        template = json.loads((root / 'config/template.json').read_text(encoding='utf-8'))
        self.assertIs(template['Alas']['Error']['SystemNotification'], False)
        for language in ('zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'es-ES'):
            with self.subTest(language=language):
                data = json.loads((root / 'module/config/i18n' / f'{language}.json').read_text(encoding='utf-8'))
                label = data['Error']['SystemNotification']
                self.assertTrue(label['name'])
                self.assertTrue(label['help'])
                self.assertNotIn('SystemNotification.', label['name'])
