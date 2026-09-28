"""Deployment error handling with all file operations and requests mocked."""

import asyncio
import importlib
import json
import mimetypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, call, patch

import requests

from deploy.git_over_cdn.client import GitOverCdnClient
from module.webui.patch import patch_mimetype


class AdbReplacementTests(unittest.TestCase):
    def manager(self):
        # Importing the Windows deploy module installs an event-loop policy;
        # restore it so test order cannot affect other modules.
        policy = asyncio.get_event_loop_policy()
        try:
            module = importlib.import_module('deploy.Windows.emulator')
        finally:
            asyncio.set_event_loop_policy(policy)
        manager = object.__new__(module.EmulatorManager)
        manager.adb = 'replacement.exe'
        manager.iter_adb_to_replace = Mock(return_value=iter(['one.exe', 'two.exe']))
        manager.adb_path_to_backup = Mock(side_effect=lambda path, **_: path + '.bak')
        manager.adb_kill = Mock()
        return manager

    def test_locked_binary_does_not_prevent_next_replacement(self):
        manager = self.manager()
        with patch('deploy.Windows.emulator.shutil.move', side_effect=[PermissionError('locked'), None]) as move, \
                patch('deploy.Windows.emulator.shutil.copy') as copy:
            manager.adb_replace()
        self.assertEqual(move.call_count, 2)
        copy.assert_called_once_with('replacement.exe', 'two.exe')

    def test_failed_copy_restores_original_then_continues(self):
        manager = self.manager()
        with patch('deploy.Windows.emulator.shutil.move'), \
                patch('deploy.Windows.emulator.shutil.copy', side_effect=[OSError('copy failed'), None, None]) as copy:
            manager.adb_replace()
        self.assertEqual(copy.call_args_list, [call('replacement.exe', 'one.exe'),
                                              call('one.exe.bak', 'one.exe'),
                                              call('replacement.exe', 'two.exe')])

    def test_failed_restore_keeps_backup_and_continues(self):
        manager = self.manager()
        with patch('deploy.Windows.emulator.shutil.move') as move, \
                patch('deploy.Windows.emulator.shutil.copy',
                      side_effect=[PermissionError('copy failed'), PermissionError('restore failed'), None]) as copy, \
                patch('deploy.Windows.emulator.os.remove', side_effect=AssertionError('No deletion allowed')):
            manager.adb_replace()
        self.assertEqual(move.call_args_list, [call('one.exe', 'one.exe.bak'), call('two.exe', 'two.exe.bak')])
        self.assertEqual(copy.call_args_list[-1], call('replacement.exe', 'two.exe'))


class DownloadTests(unittest.TestCase):
    COMMIT = 'a' * 40

    def response(self, status=200, text=None):
        return SimpleNamespace(status_code=status, text=json.dumps({'commit': self.COMMIT}) if text is None else text)

    def test_single_address_and_cached_commit(self):
        client = GitOverCdnClient('https://primary.invalid/pack/', '/offline')
        session = Mock()
        session.get.return_value = self.response()
        with patch.object(GitOverCdnClient, 'session', new_callable=PropertyMock, return_value=session):
            self.assertEqual(client.latest_commit, self.COMMIT)
            self.assertEqual(client.latest_commit, self.COMMIT)
        session.get.assert_called_once_with('https://primary.invalid/pack/latest.json', timeout=3)

    def test_request_and_response_errors_try_next_address(self):
        failures = [requests.ConnectionError('offline'), self.response(status=503), self.response(text='broken'),
                    self.response(text='{}'), self.response(text='[]'), self.response(text='{"commit":12}'),
                    self.response(text='{"commit":"../invalid"}')]
        for failure in failures:
            with self.subTest(failure=failure):
                client = GitOverCdnClient(['https://primary.invalid', 'https://backup.invalid/'], '/offline')
                session = Mock()
                session.get.side_effect = [failure, self.response()]
                with patch.object(GitOverCdnClient, 'session', new_callable=PropertyMock, return_value=session):
                    self.assertEqual(client.latest_commit, self.COMMIT)
                self.assertEqual(client.urlpath('/pack.zip'), 'https://backup.invalid/pack.zip')
                self.assertEqual(session.get.call_count, 2)

    def test_all_addresses_fail_without_altering_repository(self):
        client = GitOverCdnClient(['https://one.invalid', 'https://two.invalid'], '/offline')
        client.current_commit = 'b' * 40
        client.git_command, client.download_pack = Mock(), Mock()
        session = Mock()
        session.get.side_effect = requests.Timeout('offline')
        with patch.object(GitOverCdnClient, 'session', new_callable=PropertyMock, return_value=session):
            self.assertFalse(client.update(keep_changes=True))
        self.assertEqual(client.url, 'https://one.invalid')
        self.assertEqual(session.get.call_count, 2)
        client.git_command.assert_not_called()
        client.download_pack.assert_not_called()

    def test_update_preserves_local_changes_policy(self):
        client = GitOverCdnClient('https://primary.invalid', '/offline')
        client.current_commit, client.latest_commit = 'b' * 40, self.COMMIT
        client.download_pack, client.update_refs = Mock(return_value=True), Mock(return_value=True)
        client.git_command = Mock()
        self.assertTrue(client.update(keep_changes=True))
        self.assertEqual(client.git_command.call_args_list,
                         [call('stash'), call('reset', '--hard', 'origin/master'), call('stash', 'pop')])

    def test_empty_address_is_rejected(self):
        for urls in ([], '', ['https://primary.invalid', ' ']):
            with self.subTest(urls=urls), self.assertRaises(ValueError):
                GitOverCdnClient(urls, '/offline')


class WebTypeTests(unittest.TestCase):
    def test_polluted_file_types_are_reset_without_loading_system_table(self):
        keys = ('inited', '_db', 'encodings_map', 'suffix_map', 'types_map', 'common_types')
        original = {key: getattr(mimetypes, key) for key in keys}
        with patch.multiple(mimetypes, **original), patch.object(mimetypes, 'init',
                                                               side_effect=AssertionError('Read system table')):
            mimetypes.inited = True
            mimetypes._db = mimetypes.MimeTypes(filenames=())
            mimetypes._db.add_type('text/plain', '.js')
            mimetypes._db.add_type('application/broken', '.css')
            patch_mimetype()
            self.assertIn(mimetypes.guess_type('app.js')[0], ('application/javascript', 'text/javascript'))
            self.assertEqual(mimetypes.guess_type('app.css')[0], 'text/css')
            self.assertEqual(mimetypes.guess_type('icon.svg')[0], 'image/svg+xml')


if __name__ == '__main__':
    unittest.main()
