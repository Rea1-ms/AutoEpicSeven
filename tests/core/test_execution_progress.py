"""Scheduler progress counts successful work instead of attempts or cancellations."""

import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.alas import AzurLaneAutoScript
from module.config.config import TaskEnd
from module.exception import HandledError, ScriptError
from module.execution_progress import ExecutionProgress
from module.runtime_events import RuntimeEvents


class ExecutionProgressTests(unittest.TestCase):
    def setUp(self):
        self.events = Mock(spec=RuntimeEvents)
        self.progress = ExecutionProgress('offline-test', events=self.events)

    def assert_progress(self, state, completed, total, task=''):
        self.events.publish_progress.assert_called_with(
            'offline-test', state, completed=completed, total=total, task=task,
        )

    def test_failed_attempts_and_duplicate_success_do_not_inflate_completion(self):
        self.assertFalse(self.progress.update_pending(['Mail', 'Gacha']))
        self.progress.start_task('Mail')
        self.progress.finish_task('Mail', False)
        self.assert_progress('running', 0, 2, 'Mail')
        self.assertFalse(self.progress.update_pending(['Mail', 'Gacha']))
        self.progress.start_task('Mail')
        self.progress.finish_task('Mail', True)
        self.progress.finish_task('Mail', True)
        self.assert_progress('running', 1, 2, 'Mail')
        self.assertFalse(self.progress.update_pending(['Gacha']))
        self.progress.start_task('Gacha')
        self.progress.finish_task('Gacha', True)
        self.assert_progress('running', 2, 2, 'Gacha')
        self.assertTrue(self.progress.update_pending([]))
        self.assertFalse(self.progress.update_pending([]))

    def test_tasks_added_mid_batch_expand_total_without_losing_completed_work(self):
        self.progress.update_pending(['Mail'])
        self.progress.start_task('Mail')
        self.progress.finish_task('Mail', True)
        self.assertFalse(self.progress.update_pending(['Gacha', 'Shop']))
        self.assert_progress('running', 1, 3)
        for command in ('Gacha', 'Shop'):
            self.progress.start_task(command)
            self.progress.finish_task(command, True)
        self.assert_progress('running', 3, 3, 'Shop')
        self.assertTrue(self.progress.update_pending([]))

    def test_initial_restart_skip_does_not_count_phantom_work(self):
        self.progress.update_pending(['Restart', 'Mail'], skip_restart=True)
        self.assert_progress('running', 0, 1)
        self.progress.start_task('Mail')
        self.progress.finish_task('Mail', True)
        self.assertTrue(self.progress.update_pending(['Restart'], skip_restart=True))
        self.progress.update_pending(['Restart'])
        self.assert_progress('running', 0, 1)
        self.progress.start_task('Restart')
        self.progress.finish_task('Restart', True)
        self.assertTrue(self.progress.update_pending([]))

    def test_disabled_pending_task_cannot_turn_partial_success_into_batch_completion(self):
        self.progress.update_pending(['Mail', 'Gacha'])
        self.progress.start_task('Mail')
        self.progress.finish_task('Mail', True)
        self.assertFalse(self.progress.update_pending([]))
        self.assert_progress('waiting', 1, 2)
        self.progress.update_pending(['Arena'])
        self.assert_progress('running', 0, 1)
        self.assertEqual(self.progress.completed, 0)
        self.assertEqual(self.progress.total, 1)

    def test_waiting_and_stop_report_explicit_state_without_claiming_success(self):
        self.assertFalse(self.progress.update_pending([]))
        self.assert_progress('waiting', 0, None)
        self.progress.update_pending(['Mail'])
        self.progress.start_task('Mail')
        self.progress.finish_task('Mail', False)
        self.progress.waiting()
        self.assert_progress('waiting', 0, 1)
        self.progress.stop('error')
        self.assert_progress('error', 0, 1)
        self.progress.stop()
        self.assert_progress('stopped', 0, 1)
        self.assertEqual(self.progress.completed, 0)

    def test_requeued_successful_task_requires_a_new_success_before_batch_completion(self):
        for discover_in_queue in (False, True):
            with self.subTest(discover_in_queue=discover_in_queue):
                progress = ExecutionProgress('offline-test', events=self.events)
                progress.update_pending(['Mail'])
                progress.start_task('Mail')
                progress.finish_task('Mail', True)
                if discover_in_queue:
                    progress.update_pending(['Mail'])
                progress.start_task('Mail')
                self.assert_progress('running', 1, 2, 'Mail')
                progress.finish_task('Mail', False)
                self.assert_progress('running', 1, 2, 'Mail')
                self.assertFalse(progress.update_pending([]))
                self.assert_progress('waiting', 1, 2)


class SchedulerProgressIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.events = Mock(spec=RuntimeEvents)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('module.alas.logger'))
        self.stack.enter_context(patch('module.alas.del_cached_property'))
        self.stack.enter_context(patch('module.base.resource.release_resources'))
        self.notify = self.stack.enter_context(patch('module.alas.handle_notify'))
        self.stack.enter_context(patch(
            'module.alas.ExecutionProgress',
            side_effect=lambda name: ExecutionProgress(name, events=self.events),
        ))

    def runner(self):
        runner = object.__new__(AzurLaneAutoScript)
        runner.config_name = 'offline-test'
        runner.config = SimpleNamespace(
            Error_OnePushConfig='provider: telegram',
            Error_SystemNotification=True,
            Optimization_WhenTaskQueueEmpty='stay_there',
            task_call=Mock(), task_delay=Mock(), bind=Mock(),
            cross_get=Mock(return_value=False), pending_task=[], get_next=Mock(),
        )
        runner.device = SimpleNamespace(
            screenshot=Mock(), screenshot_tracking=[], package='offline', sleep=Mock(),
            stuck_record_clear=Mock(), click_record_clear=Mock(), release_during_wait=Mock(),
        )
        runner.checker = SimpleNamespace(
            wait_until_available=Mock(), is_recovered=Mock(return_value=False), check_now=Mock(),
        )
        runner.execution_progress = ExecutionProgress('offline-test', events=self.events)
        runner._scheduler_active = False
        runner.failure_record = {}
        runner.is_first_task = True
        runner.stop_event = None
        runner.save_error_log = Mock()
        runner.error_postprocess = Mock()
        return runner

    def test_standalone_success_and_taskend_publish_completed(self):
        for ending in (None, TaskEnd()):
            with self.subTest(ending=ending):
                self.events.reset_mock()
                runner = self.runner()
                runner.mail = Mock(side_effect=ending)
                self.assertTrue(runner.run('mail'))
                runner.mail.assert_called_once_with()
                runner.device.screenshot.assert_called_once_with()
                self.events.publish_progress.assert_called_with(
                    'offline-test', 'completed', completed=1, total=1, task='',
                )
                self.assertEqual(
                    [args[1] for args, _ in self.events.publish_progress.call_args_list],
                    ['running', 'running', 'completed'],
                )
        self.notify.assert_not_called()

    def test_standalone_fatal_error_and_keyboard_interrupt_do_not_claim_completion(self):
        for ending, propagated, state in (
            (ScriptError('offline failure'), SystemExit, 'error'),
            (KeyboardInterrupt(), KeyboardInterrupt, 'stopped'),
        ):
            with self.subTest(state=state):
                self.events.reset_mock()
                self.notify.reset_mock()
                runner = self.runner()
                runner.mail = Mock(side_effect=ending)
                with self.assertRaises(propagated) as error:
                    runner.run('mail')
                self.events.publish_progress.assert_called_with(
                    'offline-test', state, completed=0, total=1, task='',
                )
                states = [args[1] for args, _ in self.events.publish_progress.call_args_list]
                self.assertNotIn('completed', states)
                if state == 'error':
                    self.assertEqual(error.exception.code, 1)
                    self.notify.assert_called_once()
                    self.assertTrue(self.notify.call_args.kwargs['system_notification'])
                    runner.save_error_log.assert_called_once_with()
                else:
                    self.notify.assert_not_called()
                    runner.save_error_log.assert_not_called()

    def test_scheduler_skips_initial_restart_retries_then_announces_completed_batch(self):
        runner = self.runner()
        runner.restart = Mock()
        runner.mail = Mock(side_effect=[HandledError('offline retry'), None])
        runner.wait_until = Mock(side_effect=KeyboardInterrupt())
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        scheduler_clock = self.stack.enter_context(patch('module.alas.datetime'))
        scheduler_clock.now.return_value = now
        due = now - timedelta(days=1)
        restart = SimpleNamespace(command='Restart', next_run=due)
        mail = SimpleNamespace(command='Mail', next_run=due)
        future_mail = SimpleNamespace(command='Mail', next_run=due + timedelta(days=2))
        schedule = iter((
            (restart, [restart, mail]),
            (mail, [mail]),
            (mail, [mail]),
            (future_mail, []),
        ))

        def next_task():
            task, pending = next(schedule)
            runner.config.pending_task = pending
            return task

        runner.config.get_next.side_effect = next_task
        with self.assertRaises(KeyboardInterrupt):
            runner.loop()
        runner.restart.assert_not_called()
        runner.config.task_delay.assert_called_once_with(server_update=True)
        self.assertEqual(runner.mail.call_count, 2)
        self.assertEqual(runner.device.screenshot.call_count, 2)
        runner.checker.check_now.assert_called_once_with()
        self.assertEqual(runner.failure_record, {'Mail': 0})
        self.assertFalse(runner._scheduler_active)
        self.assertFalse(runner.is_first_task)
        self.notify.assert_not_called()
        runner.wait_until.assert_called_once_with(future_mail.next_run)
        calls = self.events.publish_progress.call_args_list
        self.assertTrue(all(kwargs['task'] != 'Restart' for _, kwargs in calls))
        self.assertTrue(all(kwargs['total'] in (None, 1) for _, kwargs in calls))
        self.assertIn(call('offline-test', 'running', completed=0, total=1, task='Mail'), calls)
        self.assertIn(call('offline-test', 'running', completed=1, total=1, task='Mail'), calls)
        self.assertIn(call('offline-test', 'waiting', completed=1, total=1, task=''), calls)
        self.events.publish_progress.assert_called_with(
            'offline-test', 'stopped', completed=0, total=None, task='',
        )

    def test_completed_batch_with_system_switch_off_does_not_send_remote_completion(self):
        runner = self.runner()
        runner.config.Error_SystemNotification = False
        runner.execution_progress.update_pending(['Mail'])
        runner.execution_progress.start_task('Mail')
        runner.execution_progress.finish_task('Mail', True)
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        scheduler_clock = self.stack.enter_context(patch('module.alas.datetime'))
        scheduler_clock.now.return_value = now
        runner.config.get_next.return_value = SimpleNamespace(
            command='Mail', next_run=now - timedelta(days=1),
        )
        runner.get_next_task()
        self.notify.assert_not_called()
