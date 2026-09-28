"""Device compatibility without constructing or connecting a real device."""

import contextlib
import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.daemon.benchmark import Benchmark
from module.device.connection import Connection
from module.device.device import Device
from module.device.method.adb import Adb
from module.device.method.ldopengl import LDOpenGL
from module.device.method.remove_warning import remove_screenshot_warning, remove_shell_warning
from module.device.method.utils import remove_prefix, remove_suffix, removeprefix, removesuffix
from module.device.pkg_resources import remove_suffix as package_remove_suffix
from module.exception import RequestHumanTakeover


def benchmark_for(sdk):
    bench = object.__new__(Benchmark)
    bench.device = SimpleNamespace(sdk_ver=sdk, is_chinac_phone_cloud=False,
                                   nemu_ipc_available=lambda: False, ldopengl_available=lambda: False)
    return bench


class BenchmarkTests(unittest.TestCase):
    def test_both_candidate_lists_respect_android_boundaries(self):
        for sdk in (22, 23, 32, 33):
            with self.subTest(sdk=sdk):
                bench = benchmark_for(sdk)
                complete, _ = bench.get_test_methods()
                bench.benchmark = Mock(return_value=('ADB', 'MaaTouch'))
                self.assertEqual(bench.run_simple_screenshot_benchmark(), 'ADB')
                simple = bench.benchmark.call_args.args[0]
                for candidates in (complete, simple):
                    self.assertIn('ADB', candidates)
                    for method in ('DroidCast', 'DroidCast_raw'):
                        self.assertEqual(method in candidates, 23 <= sdk <= 32)

    def test_auto_fallback_selects_compatible_method(self):
        for sdk in (22, 33):
            for method in ('DroidCast', 'DroidCast_raw'):
                with self.subTest(sdk=sdk, method=method):
                    device = object.__new__(Device)
                    device.sdk_ver = sdk
                    device.config = SimpleNamespace(Emulator_ScreenshotMethod=method,
                                                    Emulator_ControlMethod='MaaTouch',
                                                    multi_set=contextlib.nullcontext)
                    Device.method_check(device)
                    self.assertEqual(device.config.Emulator_ScreenshotMethod, 'auto')
                    bench = benchmark_for(sdk)
                    # Run the real candidate selection and ranking against fake timings.
                    bench.device.screenshot_methods = {name: (lambda: None) for name in
                                                       ('ADB', 'ADB_nc', 'uiautomator2',
                                                        'aScreenCap', 'aScreenCap_nc')}
                    bench.benchmark_test = Mock(return_value=0.1)
                    bench.show = Mock()
                    device.resolution_check_uiautomator2 = Mock()
                    with patch('module.daemon.benchmark.Benchmark', return_value=bench):
                        Device.run_simple_screenshot_benchmark(device)
                    self.assertEqual(device.config.Emulator_ScreenshotMethod, 'ADB')
                    self.assertGreater(bench.benchmark_test.call_count, 0)

    def test_benchmark_uses_elapsed_clock(self):
        bench = benchmark_for(33)
        bench.TEST_TOTAL, bench.TEST_BEST = 2, 1
        with patch('module.daemon.benchmark.time',
                   SimpleNamespace(perf_counter=Mock(side_effect=[10, 10.2, 20, 20.1]))):
            self.assertAlmostEqual(bench.benchmark_test(lambda: None), 0.1)


class WarningTests(unittest.TestCase):
    HEADERS = (
        'Failed to create //.cache for shader cache (Read-only file system)---disabling.\n',
        '[Warning] Multiple displays were found, but no display id was specified! Defaulting to the first display found, '
        'however this default is not guaranteed to be consistent across captures.\n'
        'A display id should be specified.\nSee "dumpsys SurfaceFlinger --display-id" for valid display IDs.\n',
        '[Warning] Multiple displays were found, but no display id was specified! Defaulting to the first display found, '
        'however this default is not guaranteed to be consistent across captures. A display id should be specified.\n'
        'A display ID can be specified with the [-d display-id] option.\n'
        'See "dumpsys SurfaceFlinger --display-id" for valid display IDs.\n',
        'long long=8 fun*=10\n',
        "amdgpu: os_same_file_description couldn't determine if two DRM fds reference the same file description.\n"
        'If they do, bad things may happen!\n',
    )

    def test_known_headers_preserve_bytes_and_text_payload(self):
        for header in self.HEADERS:
            with self.subTest(header=header):
                self.assertEqual(remove_screenshot_warning(header + 'payload\n'), 'payload\n')
                payload = b'\x89PNG\r\n\x1a\n\x00\xff'
                self.assertEqual(remove_screenshot_warning(header.encode() + payload), payload)

    def test_nonprefix_content_and_repeated_shell_warnings(self):
        for payload in ('image\namdgpu: content\n', b'image\nlong long=8\n'):
            self.assertEqual(remove_screenshot_warning(payload), payload)
        warning = 'WARNING: linker: unused entry\n' * 2
        self.assertEqual(remove_shell_warning(warning + 'data'), 'data')
        self.assertEqual(remove_shell_warning((warning + 'data').encode()), b'data')

    def test_png_decoder_accepts_each_header(self):
        image = np.arange(24 * 32 * 3, dtype=np.uint8).reshape(24, 32, 3)
        ok, png = cv2.imencode('.png', cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        self.assertTrue(ok)
        for header in self.HEADERS:
            with self.subTest(header=header):
                actual = Adb._Adb__load_screenshot(header.encode() + png.tobytes(), 0)
                np.testing.assert_array_equal(actual, image)

    def test_raw_decoder_strips_headers_for_http_and_netcat(self):
        image = np.arange(24 * 32 * 4, dtype=np.uint8).reshape(24, 32, 4)
        raw = np.array([32, 24, 1], dtype=np.uint32).tobytes() + image.tobytes()
        device = object.__new__(Adb)
        device.config = SimpleNamespace(DEVICE_OVER_HTTP=True)
        for header in self.HEADERS:
            with self.subTest(header=header):
                device.adb_shell = Mock(return_value=header.encode() + raw)
                device.adb_shell_nc = Mock(return_value=header.encode() + raw)
                np.testing.assert_array_equal(device.screenshot_adb(), image[:, :, :3])
                np.testing.assert_array_equal(device.screenshot_adb_nc(), image[:, :, :3])

    def test_prefix_suffix_aliases_and_empty_boundaries(self):
        self.assertIs(remove_prefix, removeprefix)
        self.assertIs(remove_suffix, removesuffix)
        for empty, value, suffix in (('', 'module.dist-info', '.dist-info'),
                                     (b'', b'module.dist-info', b'.dist-info')):
            for function in (removesuffix, package_remove_suffix):
                with self.subTest(function=function, value=value):
                    self.assertEqual(function(value, empty), value)
                    self.assertEqual(function(value, suffix), value[:-len(suffix)])
                    self.assertEqual(function(empty, empty), empty)
            self.assertEqual(removeprefix(value, empty), value)


class EmulatorTests(unittest.TestCase):
    def test_mac_emulator_serial_requires_engine_and_platform(self):
        for mac, family, serial, engine, expected in (
                (True, False, 'emulator-5554', 'MacPro', True),
                (True, False, 'emulator-5554', 'NEMUX', False),
                (True, False, '127.0.0.1:5555', 'MACPRO', False),
                (True, True, '127.0.0.1:16384', '', True),
                (False, False, 'emulator-5554', 'MACPRO', False)):
            with self.subTest(mac=mac, family=family, serial=serial, engine=engine):
                device = object.__new__(Connection)
                device.serial, device.is_mumu_family = serial, family
                device.nemud_player_engine = engine
                with patch('module.device.connection.IS_MACINTOSH', mac):
                    self.assertEqual(device.is_mumu_pro, expected)

    def test_mac_pro_keepalive_and_version_detection(self):
        device = object.__new__(Connection)
        device.is_mumu_family, device.is_mumu_pro = False, True
        self.assertTrue(device.is_mumu_over_version_356)
        for enabled in ('', 'false'):
            device.nemud_app_keep_alive = enabled
            self.assertTrue(device.check_mumu_app_keep_alive())
        device.nemud_app_keep_alive = 'true'
        with self.assertRaises(RequestHumanTakeover):
            device.check_mumu_app_keep_alive()

    def test_ldplayer_versions_and_minimized_command(self):
        # Windows registry is not available on the Linux CI worker. The tested
        # path classifier and command builder must never read the real registry.
        with patch.dict(sys.modules, {} if sys.platform == 'win32' else {'winreg': Mock()}):
            emulators = importlib.import_module('module.device.platform.emulator_windows')
            windows = importlib.import_module('module.device.platform.platform_windows')
        for version in (4, 9, 14):
            with self.subTest(version=version):
                instance = emulators.EmulatorInstance('emulator-5554', 'leidian2',
                                                     f'/offline/LDPlayer{version}/dnplayer.exe')
                self.assertEqual(instance.type, f'LDPlayer{version}')
                self.assertIn(instance.type, emulators.Emulator.LDPlayerFamily)
                platform = object.__new__(windows.PlatformWindows)
                platform.execute = Mock()
                platform._emulator_start(instance)
                command = platform.execute.call_args.args[0]
                self.assertIn('ldconsole.exe" launch --index 2', command)
                self.assertEqual(command.endswith(' --mini'), version in (9, 14))

    def test_ldplayer_shared_capture_requires_supported_type(self):
        device = object.__new__(LDOpenGL)
        device.is_ldplayer_bluestacks_family = True
        device.ldopengl = object()
        with patch('module.device.method.ldopengl.IS_WINDOWS', True):
            for version in (4, 9, 14):
                device.config = SimpleNamespace(EmulatorInfo_Emulator=f'LDPlayer{version}')
                self.assertEqual(device.ldopengl_available(), version in (9, 14))


if __name__ == '__main__':
    unittest.main()
