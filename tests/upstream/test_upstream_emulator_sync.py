# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline regressions for the selected StarRailCopilot emulator backports.

Run with the checkout under test as cwd. Emulator execution, DLL loading,
registry enumeration, device access, and user config writes are all avoided.
"""
# ruff: noqa: E402
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


from module.device.device import Device
from module.device.connection_attr import ConnectionAttr
from module.device.method.nemu_ipc import NemuIpcImpl, NemuIpcIncompatible
from module.device.method.utils import get_serial_pair
from module.device.platform.emulator_base import (
    EmulatorInstanceBase,
    get_serial_pair as platform_serial_pair,
)
from module.device.platform.emulator_windows import EmulatorInstance
from module.device.platform.platform_base import PlatformBase
from module.device.platform.platform_windows import PlatformWindows


class SerialTests(unittest.TestCase):
    def test_mumu_copied_double_ports(self):
        for text, expected in (
            ('5555,16384', '127.0.0.1:16384'),
            ('5557，16416', '127.0.0.1:16416'),
            ('5555.16384', '127.0.0.1:16384'),
            ('\n 5555, 16384 \r\n', '127.0.0.1:16384'),
        ):
            with self.subTest(text=text):
                self.assertEqual(ConnectionAttr.revise_serial(text), expected)

    def test_existing_serial_formats_remain_supported(self):
        for text, expected in (
            ('16384', '127.0.0.1:16384'),
            ('127。0。0。1：5555', '127.0.0.1:5555'),
            ('夜神模拟器 127.0.0.1:62001', '127.0.0.1:62001'),
            ('MuMu模拟器12127.0.0.1:16384', '127.0.0.1:16384'),
            ('autoemulator-5554', 'emulator-5554'),
            ('emulator-5554', 'emulator-5554'),
            ('192.168.1.12:5555', '192.168.1.12:5555'),
            ('auto', 'auto'),
            ('wsa-0', 'wsa-0'),
            ('http://127.0.0.1:7912', 'http://127.0.0.1:7912'),
            ('ABC12345', 'ABC12345'),
        ):
            with self.subTest(text=text):
                self.assertEqual(ConnectionAttr.revise_serial(text), expected)

    def test_invalid_port_pairs_do_not_become_local_ports(self):
        for text, expected in (
            ('1234,16384', '1234.16384'),
            ('5555,20000', '5555.20000'),
            ('65536', '65536'),
            ('1000', '1000'),
        ):
            with self.subTest(text=text):
                self.assertEqual(ConnectionAttr.revise_serial(text), expected)

    def test_connection_and_discovery_use_the_same_extended_port_range(self):
        for offset in (0, 2, 32, 34, 62, 64):
            pair = (f'127.0.0.1:{5555+offset}', f'emulator-{5554+offset}')
            for serial in pair:
                with self.subTest(serial=serial):
                    self.assertEqual(get_serial_pair(serial), pair)
                    self.assertEqual(platform_serial_pair(serial), pair)

    def test_port_pair_boundaries(self):
        for serial in ('127.0.0.1:5554', '127.0.0.1:5620', 'emulator-5553', 'emulator-5619', '127.0.0.1:16384'):
            with self.subTest(serial=serial):
                self.assertEqual(get_serial_pair(serial), (None, None))
                self.assertEqual(platform_serial_pair(serial), (None, None))


class InstanceTests(unittest.TestCase):
    def test_mumu_android_12_and_15_instance_ids(self):
        for name, expected in (
            ('MuMuPlayer-12.0-3', 3),
            ('MuMuPlayerGlobal-12.0-0', 0),
            ('MuMuPlayer-15.0-7', 7),
            ('MuMuPlayerGlobal-15.0-12', 12),
            ('YXArkNights-12.0-1', 1),
            ('leidian0', None),
            ('MuMuPlayer-15.0-invalid', None),
        ):
            with self.subTest(name=name):
                instance = EmulatorInstanceBase('127.0.0.1:16384', name, 'unused.exe')
                self.assertEqual(instance.MuMuPlayer12_id, expected)

    def instance(self, name, emulator, path):
        return SimpleNamespace(serial='127.0.0.1:5555', name=name, type=emulator, path=path, MuMuPlayer12_id=None)

    def find(self, instances, **kwargs):
        platform = SimpleNamespace(
            serial='127.0.0.1:5555', all_emulator_instances=instances,
            iter_running_emulator=Mock(return_value=[]),
        )
        return PlatformBase.find_emulator_instance(platform, serial=platform.serial, **kwargs)

    def test_selected_type_beats_stale_name_and_path_on_shared_port(self):
        wrong = self.instance('old-name', 'BlueStacks5', 'old-path')
        right = self.instance('new-name', 'LDPlayer9', 'new-path')
        selected = self.find([wrong, right], name='old-name', path='old-path', emulator='LDPlayer9')
        self.assertIs(selected, right)

    def test_multiple_instances_of_selected_type_still_use_name(self):
        wrong = self.instance('match', 'BlueStacks5', 'a')
        first = self.instance('other', 'LDPlayer9', 'b')
        right = self.instance('match', 'LDPlayer9', 'c')
        self.assertIs(self.find([wrong, first, right], name='match', emulator='LDPlayer9'), right)

    def test_invalid_type_still_allows_name_selection(self):
        first = self.instance('first', 'BlueStacks5', 'a')
        right = self.instance('second', 'LDPlayer9', 'b')
        self.assertIs(self.find([first, right], name='second', emulator='unknown'), right)

    def test_unique_serial_remains_sufficient(self):
        right = self.instance('actual', 'LDPlayer9', 'actual-path')
        self.assertIs(self.find([right], name='stale', emulator='BlueStacks5'), right)


class StartupTests(unittest.TestCase):
    def test_mumu_launch_uses_backend_manager_for_each_instance(self):
        platform = SimpleNamespace(execute=Mock())
        cases = (
            ('D:/Apps/MuMu/shell/MuMuPlayer.exe', 'MuMuPlayer-12.0-1', 'D:/Apps/MuMu/shell/MuMuManager.exe', 1),
            ('D:/Apps/MuMu/nx_main/MuMuNxMain.exe', 'MuMuPlayer-15.0-2', 'D:/Apps/MuMu/nx_main/MuMuManager.exe', 2),
        )
        for path, name, manager, index in cases:
            with self.subTest(name=name):
                platform.execute.reset_mock()
                instance = EmulatorInstance('127.0.0.1:16384', name, path)
                PlatformWindows._emulator_start(platform, instance)
                platform.execute.assert_called_once_with(f'"{manager}" api -v {index} launch_player')

    def platform(self, watcher_results, command_results=None):
        commands = Mock(side_effect=command_results, return_value=True)
        return SimpleNamespace(
            _emulator_start=Mock(name='start'), _emulator_stop=Mock(name='stop'),
            _emulator_function_wrapper=commands,
            emulator_start_watch=Mock(side_effect=watcher_results),
        )

    def test_startup_timeout_retries_stop_start_before_success(self):
        platform = self.platform([False, True])
        self.assertTrue(PlatformWindows.emulator_start(platform))
        self.assertEqual(platform.emulator_start_watch.call_count, 2)
        self.assertEqual(platform._emulator_function_wrapper.call_args_list, [
            call(platform._emulator_stop), call(platform._emulator_start),
            call(platform._emulator_stop), call(platform._emulator_start),
        ])

    def test_three_startup_timeouts_report_failure(self):
        platform = self.platform([False, False, False])
        self.assertFalse(PlatformWindows.emulator_start(platform))
        self.assertEqual(platform.emulator_start_watch.call_count, 3)
        self.assertEqual(platform._emulator_function_wrapper.call_count, 6)

    def test_first_success_needs_no_retry(self):
        platform = self.platform([True])
        self.assertTrue(PlatformWindows.emulator_start(platform))
        self.assertEqual(platform._emulator_function_wrapper.call_count, 2)

    def test_failed_stop_never_starts_or_watches(self):
        platform = self.platform([], [False])
        self.assertFalse(PlatformWindows.emulator_start(platform))
        platform.emulator_start_watch.assert_not_called()
        platform._emulator_function_wrapper.assert_called_once_with(platform._emulator_stop)


class ScreenshotTests(unittest.TestCase):
    def check(self, method, sdk):
        config = SimpleNamespace(Emulator_ScreenshotMethod=method, Emulator_ControlMethod='MaaTouch')
        device = SimpleNamespace(
            config=config, sdk_ver=sdk, is_emulator=True, is_mumu_family=True,
            is_ldplayer_bluestacks_family=False, is_vmos=False,
        )
        Device.method_check(device)
        return config.Emulator_ScreenshotMethod

    def test_unsupported_droidcast_versions_use_auto(self):
        for method in ('DroidCast', 'DroidCast_raw'):
            for sdk in (22, 33, 35):
                with self.subTest(method=method, sdk=sdk):
                    self.assertEqual(self.check(method, sdk), 'auto')

    def test_supported_droidcast_versions_keep_selection(self):
        for method in ('DroidCast', 'DroidCast_raw'):
            for sdk in (23, 29, 32):
                with self.subTest(method=method, sdk=sdk):
                    self.assertEqual(self.check(method, sdk), method)

    def test_other_screenshot_methods_keep_selection(self):
        for method in ('ADB', 'uiautomator2', 'nemu_ipc', 'auto'):
            with self.subTest(method=method):
                self.assertEqual(self.check(method, 35), method)


class DllTests(unittest.TestCase):
    def load(self, present, fail_first=False):
        root = Path('D:/Apps/MuMu').resolve()
        paths = [str(root / relative) for relative in present]
        sentinel = object()
        with patch('module.device.method.nemu_ipc.os.path.exists', side_effect=lambda p: p in paths), \
                patch('module.device.method.nemu_ipc.ctypes.CDLL', return_value=sentinel) as loader:
            if fail_first:
                loader.side_effect = [OSError('old library cannot load'), sentinel]
            instance = NemuIpcImpl(str(root), instance_id=2)
        self.assertIs(instance.lib, sentinel)
        self.assertEqual(instance.connect_id, 0)
        return loader, paths

    def test_new_mumu_6_library_path(self):
        loader, paths = self.load(['nx_main/sdk/external_renderer_ipc.dll'])
        loader.assert_called_once_with(paths[0])

    def test_older_library_paths_still_work(self):
        for path in ('shell/sdk/external_renderer_ipc.dll', 'nx_device/12.0/shell/sdk/external_renderer_ipc.dll'):
            with self.subTest(path=path):
                loader, paths = self.load([path])
                loader.assert_called_once_with(paths[0])

    def test_existing_library_order_is_preserved(self):
        loader, paths = self.load(['shell/sdk/external_renderer_ipc.dll', 'nx_main/sdk/external_renderer_ipc.dll'])
        loader.assert_called_once_with(paths[0])

    def test_unloadable_old_library_can_use_new_path(self):
        loader, paths = self.load(
            ['shell/sdk/external_renderer_ipc.dll', 'nx_main/sdk/external_renderer_ipc.dll'], fail_first=True,
        )
        self.assertEqual(loader.call_args_list, [call(path) for path in paths])

    def test_missing_library_reports_incompatibility(self):
        with patch('module.device.method.nemu_ipc.os.path.exists', return_value=False), \
                patch('module.device.method.nemu_ipc.ctypes.CDLL') as loader:
            with self.assertRaises(NemuIpcIncompatible):
                NemuIpcImpl('D:/Apps/MuMu', instance_id=0)
        loader.assert_not_called()
