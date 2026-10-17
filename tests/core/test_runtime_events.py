"""Local progress and system notifications without accounts or external delivery."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil
from requests import Response

from module.notify.notify import handle_notify, is_system_provider
from module.runtime_events import RuntimeEvents
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
