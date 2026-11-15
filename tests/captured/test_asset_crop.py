# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")
from tests.support.history_fixtures import fixture_path

"""Offline checks for the asset capture and crop developer tool."""

import hashlib
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

WORKTREE = Path(__file__).resolve().parents[2]
ORIGINAL_REPO = Path(__import__("subprocess").check_output(["git","rev-parse","--git-common-dir"],text=True).strip()).resolve().parent
SCREENSHOT = fixture_path('urgent_tasks/superior-detail.png')

from dev_tools.assets import (
    asset_archive,  # noqa: E402
    asset_crop,  # noqa: E402
    button_extract,  # noqa: E402
)
from dev_tools.assets.asset_crop import (  # noqa: E402
    AssetCropError,
    CropItem,
    CropRecipe,
    _validate_dependencies,
    build_asset_filename,
    create_output_plan,
    main,
    make_black_asset,
    resolve_original_repo,
    write_output_plan,
)
from dev_tools.assets.asset_crop_ui import InteractiveCropper  # noqa: E402
from dev_tools.capture import screenshot


class AssetCropTest(unittest.TestCase):
    def setUp(self):
        self.image = np.arange(720 * 1280 * 3, dtype=np.uint8).reshape(720, 1280, 3)

    def test_filename_suffix_order(self):
        self.assertEqual(build_asset_filename("SAMPLE"), "SAMPLE.png")
        self.assertEqual(build_asset_filename("SAMPLE", "button"), "SAMPLE.BUTTON.png")
        self.assertEqual(build_asset_filename("SAMPLE", "base", 2), "SAMPLE.2.png")
        self.assertEqual(build_asset_filename("SAMPLE", "button", 2), "SAMPLE.2.BUTTON.png")
        with self.assertRaises(AssetCropError):
            build_asset_filename("SAMPLE", "search", 2)

    def test_black_canvas_preserves_only_selected_pixels(self):
        box = (101, 202, 211, 244)
        output = make_black_asset(self.image, box)
        self.assertTrue(np.array_equal(output[202:244, 101:211], self.image[202:244, 101:211]))
        self.assertEqual(np.count_nonzero(output[:202]), 0)
        self.assertEqual(np.count_nonzero(output[244:]), 0)
        self.assertEqual(np.count_nonzero(output[202:244, :101]), 0)
        self.assertEqual(np.count_nonzero(output[202:244, 211:]), 0)
        self.assertEqual(output.shape, (720, 1280, 3))

    def test_recipe_round_trip_and_dependencies(self):
        base = CropItem("SAMPLE", "base", 1, "share", "dev_tools/probe", (1, 2, 30, 40))
        second = CropItem("SAMPLE", "base", 2, "share", "dev_tools/probe", (2, 3, 31, 41))
        button = CropItem("SAMPLE", "button", 2, "share", "dev_tools/probe", (3, 4, 32, 42))
        recipe = CropRecipe("source.png", "asset_crop", "round-trip", (base, second, button))
        restored = CropRecipe.from_dict(json.loads(json.dumps(recipe.to_dict())))
        self.assertEqual(restored, recipe)
        _validate_dependencies(restored.items, WORKTREE)
        with self.assertRaises(AssetCropError):
            _validate_dependencies((button,), WORKTREE)

    def test_output_paths_stay_in_expected_roots(self):
        item = CropItem("SAMPLE", "base", 1, "share", "dev_tools/probe", (10, 20, 30, 40))
        recipe = CropRecipe("probe.png", "asset_crop", "output-plan", (item,))
        plan = create_output_plan(
            self.image,
            recipe,
            worktree=WORKTREE,
            original_repo=ORIGINAL_REPO,
        )
        self.assertTrue(plan.test_image.is_relative_to(ORIGINAL_REPO / "test" / "screenshots"))
        self.assertTrue(plan.assets[0][1].is_relative_to(WORKTREE / "assets"))
        self.assertTrue(np.array_equal(plan.image, self.image))
        self.assertEqual(resolve_original_repo(WORKTREE), ORIGINAL_REPO)

    def test_existing_asset_requires_explicit_replace(self):
        item = CropItem(
            "URGENT_TASKS_SUPERIOR",
            "base",
            1,
            "global_cn",
            "dungeon/configs/urgent_tasks",
            (10, 20, 30, 40),
        )
        recipe = CropRecipe("probe.png", "asset_crop", "replace-check", (item,))
        plan = create_output_plan(
            self.image,
            recipe,
            worktree=WORKTREE,
            original_repo=ORIGINAL_REPO,
        )
        self.assertTrue(plan.conflicts)
        with self.assertRaises(AssetCropError):
            write_output_plan(plan)

    def test_interactive_undo_cancel_and_freeze(self):
        editor = InteractiveCropper(image=self.image)
        editor.boxes.append((1, 2, 3, 4))
        with (
            patch("cv2.namedWindow"),
            patch("cv2.resizeWindow"),
            patch("cv2.setMouseCallback"),
            patch("cv2.imshow"),
            patch("cv2.destroyWindow") as destroy,
            patch("cv2.getWindowProperty", return_value=1),
            patch("cv2.waitKeyEx", side_effect=[ord("z"), 27]),
        ):
            self.assertIsNone(editor.run())
        self.assertEqual(editor.boxes, [])
        destroy.assert_called_once_with(editor.WINDOW_NAME)

        live = InteractiveCropper(frame_getter=lambda: self.image)
        keys = iter((32, 13))

        def wait_key(_delay):
            key = next(keys)
            if key == 13:
                live.boxes.append((10, 20, 30, 40))
            return key

        with (
            patch("cv2.namedWindow"),
            patch("cv2.resizeWindow"),
            patch("cv2.setMouseCallback"),
            patch("cv2.imshow"),
            patch("cv2.destroyWindow"),
            patch("cv2.getWindowProperty", return_value=1),
            patch("cv2.waitKeyEx", side_effect=wait_key),
        ):
            result = live.run()
        self.assertIsNotNone(result)
        self.assertTrue(live.frozen)

    def test_mouse_selection_accounts_for_zoom_and_pan(self):
        editor = InteractiveCropper(image=self.image)
        editor._zoom_at((640, 360), 2)
        editor._mouse(cv2.EVENT_LBUTTONDOWN, 440, 260, 0, None)
        editor._mouse(cv2.EVENT_LBUTTONUP, 640, 360, 0, None)
        self.assertEqual(editor.boxes, [(540, 310, 640, 360)])

    def test_live_click_uses_image_coordinates_once_on_release(self):
        click = Mock()
        editor = InteractiveCropper(frame_getter=lambda: self.image, on_click=click)
        editor.frame = self.image.copy()
        editor._zoom_at((640, 360), 2)
        editor._mouse(cv2.EVENT_LBUTTONDOWN, 440, 260, 0, None)
        editor._mouse(cv2.EVENT_MOUSEMOVE, 441, 261, 0, None)
        click.assert_not_called()
        editor._mouse(cv2.EVENT_LBUTTONUP, 440, 260, 0, None)
        editor._mouse(cv2.EVENT_LBUTTONUP, 440, 260, 0, None)
        click.assert_called_once_with(540, 310)
        self.assertEqual(editor.boxes, [])

    def test_live_drag_preserves_endpoints_and_duration_without_click(self):
        click, swipe = Mock(), Mock()
        editor = InteractiveCropper(frame_getter=lambda: self.image, on_click=click, on_swipe=swipe)
        editor.frame = self.image.copy()
        with patch('dev_tools.assets.asset_crop_ui.time.monotonic', side_effect=[10, 10.4]):
            editor._mouse(cv2.EVENT_LBUTTONDOWN, 400, 300, 0, None)
            editor._mouse(cv2.EVENT_LBUTTONUP, 500, 400, 0, None)
        click.assert_not_called()
        self.assertEqual(swipe.call_args.args[:2], ((400, 300), (500, 400)))
        self.assertAlmostEqual(swipe.call_args.args[2], 0.4)
        self.assertEqual(editor.boxes, [])

    def test_frozen_selection_blocks_device_controls_and_resume(self):
        click, swipe, back = Mock(), Mock(), Mock()
        editor = InteractiveCropper(frame_getter=lambda: self.image, on_click=click,
                                    on_swipe=swipe, on_back=back)
        editor.frame = self.image.copy()
        editor._action('freeze')
        editor._mouse(cv2.EVENT_LBUTTONDOWN, 400, 300, 0, None)
        editor._mouse(cv2.EVENT_LBUTTONUP, 500, 400, 0, None)
        editor._action('back')
        editor._action('freeze')
        self.assertTrue(editor.frozen)
        self.assertEqual(editor.boxes, [(400, 300, 500, 400)])
        click.assert_not_called()
        swipe.assert_not_called()
        back.assert_not_called()
        editor.boxes.clear()
        editor._action('freeze')
        editor._action('back')
        self.assertFalse(editor.frozen)
        back.assert_called_once_with()

    def test_toolbar_capture_saves_original_pixels_without_device_click(self):
        capture, click = Mock(return_value='frame.png'), Mock()
        editor = InteractiveCropper(frame_getter=lambda: self.image, on_capture=capture, on_click=click)
        editor.frame = self.image.copy()
        editor._zoom_at((640, 360), 2)
        editor.boxes.append((10, 20, 30, 40))
        editor._mouse(cv2.EVENT_LBUTTONDOWN, 40, 740, 0, None)
        editor._mouse(cv2.EVENT_LBUTTONUP, 40, 740, 0, None)
        click.assert_not_called()
        capture.assert_called_once()
        self.assertTrue(np.array_equal(capture.call_args.args[0], self.image))
        self.assertIsNot(capture.call_args.args[0], editor.frame)
        self.assertEqual(editor._render().shape, (808, 1280, 3))

    def test_blank_viewport_and_toolbar_release_never_tap_device(self):
        click, capture = Mock(), Mock(return_value='frame.png')
        editor = InteractiveCropper(frame_getter=lambda: self.image, on_click=click, on_capture=capture)
        editor.frame = self.image.copy()
        editor.zoom = 0.5
        for start, end in (((900, 300), (900, 300)), ((300, 200), (900, 300)),
                           ((300, 200), (40, 740)), ((40, 740), (300, 200))):
            editor._mouse(cv2.EVENT_LBUTTONDOWN, *start, 0, None)
            editor._mouse(cv2.EVENT_LBUTTONUP, *end, 0, None)
        click.assert_not_called()
        capture.assert_not_called()

    def test_preview_f3_captures_frozen_frame_and_window_close_exits(self):
        capture = Mock(return_value='frame.png')
        getter = Mock(return_value=self.image)
        editor = InteractiveCropper(frame_getter=getter, on_capture=capture)
        with (
            patch('cv2.namedWindow'), patch('cv2.resizeWindow'), patch('cv2.setMouseCallback'),
            patch('cv2.imshow'), patch('cv2.destroyWindow'),
            patch('cv2.getWindowProperty', side_effect=[1, 1, 0]),
            patch('cv2.waitKeyEx', side_effect=[32, 0x720000, -1]),
        ):
            self.assertIsNone(editor.run())
        getter.assert_called_once_with()
        capture.assert_called_once()
        self.assertTrue(np.array_equal(capture.call_args.args[0], self.image))

    def test_saved_frame_keeps_source_without_uid_masking(self):
        output = WORKTREE / '.cache' / 'capture-preview-tests' / uuid4().hex
        source = self.image.copy()
        with redirect_stdout(io.StringIO()):
            path = screenshot.save_frame(self.image, str(output))
        saved = np.asarray(Image.open(path))
        self.assertEqual(saved.shape, source.shape)
        self.assertTrue(np.array_equal(saved, source))
        self.assertTrue(np.array_equal(self.image, source))

    def test_preview_adapter_uses_device_stack_and_saves_displayed_frame(self):
        device = Mock()
        device.screenshot.return_value = self.image
        editor = screenshot.create_preview(device)
        editor.on_click(400, 300)
        self.assertEqual(device.click.call_args.args[0].button, (400, 300, 401, 301))
        self.assertFalse(device.click.call_args.kwargs['control_check'])
        editor.on_swipe((400, 300), (500, 400), 0.4)
        device.swipe.assert_called_once_with((400, 300), (500, 400), duration=0.4)
        editor.on_back()
        device.adb_shell.assert_called_once_with(['input', 'keyevent', '4'])
        with patch.object(screenshot, 'save_frame', return_value='frame.png') as save:
            editor.on_capture(self.image)
        save.assert_called_once()
        device.screenshot.assert_not_called()
        frame = editor.frame_getter()
        self.assertTrue(np.array_equal(frame, self.image))

    def test_high_dpi_display_scale_preserves_source_coordinates(self):
        click = Mock()
        editor = InteractiveCropper(frame_getter=lambda: self.image, on_click=click, display_scale=0.75)
        editor.frame = self.image.copy()
        editor._mouse(cv2.EVENT_LBUTTONDOWN, round(400 * 0.75), round(300 * 0.75), 0, None)
        editor._mouse(cv2.EVENT_LBUTTONUP, round(400 * 0.75), round(300 * 0.75), 0, None)
        click.assert_called_once_with(400, 300)
        self.assertEqual(editor._render().shape, (round(808 * 0.75), round(1280 * 0.75), 3))

    def test_high_dpi_toolbar_uses_scaled_hitboxes(self):
        capture = Mock(return_value='frame.png')
        editor = InteractiveCropper(frame_getter=lambda: self.image, on_capture=capture, display_scale=0.75)
        editor.frame = self.image.copy()
        x = round(40 * 0.75)
        y = round((720 + 20) * 0.75)
        editor._mouse(cv2.EVENT_LBUTTONDOWN, x, y, 0, None)
        editor._mouse(cv2.EVENT_LBUTTONUP, x, y, 0, None)
        capture.assert_called_once()

    def test_gui_entry_reuses_crop_window_without_global_hotkey_dependency(self):
        with (
            patch.dict(sys.modules, {'pynput': None}),
            patch('dev_tools.assets.asset_crop.main', return_value=130) as crop_main,
        ):
            self.assertEqual(screenshot.main(['--serial', '127.0.0.1:5555']), 0)
        crop_main.assert_called_once_with(['live', '--serial', '127.0.0.1:5555', '--fps', '30'])

    def test_capture_frame_rate_defaults_and_custom_values_reach_device(self):
        with patch.object(asset_crop, 'main', return_value=130) as crop_main:
            self.assertEqual(screenshot.main(['--config', 'demo', '--fps', '60']), 0)
        crop_main.assert_called_once_with(['live', '--config', 'demo', '--fps', '60'])

        for command in ('live', 'capture'):
            for options, expected in (([], 30), (['--fps', '60'], 60)):
                with (
                    self.subTest(command=command, fps=expected),
                    patch.object(screenshot, 'create_device') as create_device,
                    patch.object(screenshot, 'create_preview') as create_preview,
                    patch.object(screenshot, 'take_screenshot', return_value=self.image),
                    patch.object(asset_crop, 'create_output_plan') as plan,
                    redirect_stdout(io.StringIO()),
                ):
                    create_preview.return_value.run.return_value = None
                    plan.return_value.result.return_value = {'dry_run': True}
                    result = asset_crop.main([
                        command, '--serial', '5555', '--suite', 'probe',
                        '--case', 'fps', '--dry-run', '--json', *options,
                    ])
                    self.assertEqual(result, 130 if command == 'live' else 0)
                    create_device.assert_called_once_with(
                        '5555',
                        frame_rate=expected,
                        screenshot_method=None,
                        control_method=None,
                    )

    def test_invalid_frame_rate_rejected_before_device_creation(self):
        entries = (
            (screenshot.main, []),
            (asset_crop.main, ['live']),
            (asset_crop.main, ['capture']),
        )
        for entry, prefix in entries:
            for value in ('0', '-1', '1.5', 'invalid'):
                with (
                    self.subTest(entry=prefix, value=value),
                    patch.object(screenshot, 'create_device') as create_device,
                    redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as error,
                ):
                    entry([*prefix, '--fps', value])
                self.assertEqual(error.exception.code, 2)
                create_device.assert_not_called()

    def test_capture_device_sets_frame_rate_before_initialization(self):
        from module.device.method.scrcpy.options import ScrcpyOptions

        for name, configs, rate in (('demo', ['demo'], 30), ('5555', [], 60)):
            with (
                self.subTest(name=name),
                patch.object(screenshot, 'alas_instance', return_value=configs),
                patch.object(screenshot, 'AzurLaneConfig') as config_type,
                patch.object(screenshot, 'Device') as device_type,
                redirect_stdout(io.StringIO()),
            ):
                device = screenshot.create_device(name, frame_rate=rate)
                config = config_type.return_value
                if configs:
                    config_type.assert_called_once_with(name, task=None)
                else:
                    config_type.assert_called_once_with('template')
                device_type.assert_called_once_with(
                    config,
                    scrcpy_frame_rate=rate,
                    screenshot_method_explicit=not bool(configs),
                )
                if not configs:
                    config.override.assert_called_once_with(
                        Emulator_Serial='127.0.0.1:5555', Emulator_PackageName='auto',
                        Emulator_ScreenshotMethod='scrcpy',
                    )
                device.disable_stuck_detection.assert_called_once_with()
                device.screenshot_interval_set.assert_called_once_with(0.)
                self.assertEqual(ScrcpyOptions.frame_rate, 10)

    def test_scrcpy_frame_rate_override_preserves_automation_default(self):
        from module.device.method.scrcpy.options import ScrcpyOptions

        for rate in (30, 60):
            with self.subTest(rate=rate):
                old_command = ScrcpyOptions.command_v120(frame_rate=rate)
                new_command = ScrcpyOptions.command_v125(frame_rate=rate)
                self.assertEqual(old_command[8], str(rate))
                self.assertIn(f'key_capture_rate={rate}', old_command[17])
                self.assertIn(f'max_fps={rate}', new_command)
                self.assertTrue(any(
                    arg.startswith('codec_options=') and f'key_capture_rate={rate}' in arg
                    for arg in new_command
                ))
        self.assertEqual(ScrcpyOptions.command_v120()[8], '10')
        self.assertIn('max_fps=10', ScrcpyOptions.command_v125())

    def test_capture_method_options_are_normalized_and_applied_before_device_init(self):
        with (
            patch.object(screenshot, 'alas_instance', return_value=[]),
            patch.object(screenshot, 'AzurLaneConfig') as config_type,
            patch.object(screenshot, 'Device') as device_type,
            redirect_stdout(io.StringIO()),
        ):
            screenshot.create_device(
                '5555',
                screenshot_method='adbnc',
                control_method='maatouch',
            )
        config = config_type.return_value
        config_type.assert_called_once_with('template')
        config.override.assert_any_call(
            Emulator_Serial='127.0.0.1:5555',
            Emulator_PackageName='auto',
            Emulator_ScreenshotMethod='ADB_nc',
        )
        config.override.assert_any_call(Emulator_ControlMethod='MaaTouch')
        device_type.assert_called_once_with(
            config,
            scrcpy_frame_rate=30,
            screenshot_method_explicit=True,
        )

        config_type.reset_mock()
        device_type.reset_mock()
        with (
            patch.object(screenshot, 'alas_instance', return_value=['demo']),
            patch.object(screenshot, 'AzurLaneConfig') as config_type,
            patch.object(screenshot, 'Device') as device_type,
            redirect_stdout(io.StringIO()),
        ):
            screenshot.create_device(
                'demo',
                screenshot_method='uiautomator',
                control_method='minitouch',
            )
        config = config_type.return_value
        config_type.assert_called_once_with('demo', task=None)
        config.override.assert_any_call(Emulator_ScreenshotMethod='uiautomator2')
        config.override.assert_any_call(Emulator_ControlMethod='minitouch')
        device_type.assert_called_once_with(
            config,
            scrcpy_frame_rate=30,
            screenshot_method_explicit=True,
        )

    def test_gui_entry_forwards_method_options(self):
        with patch.object(asset_crop, 'main', return_value=130) as crop_main:
            self.assertEqual(
                screenshot.main([
                    '--serial', '127.0.0.1:5555',
                    '--screenshot-method', 'adbnc',
                    '--control-method', 'minitouch',
                ]),
                0,
            )
        crop_main.assert_called_once_with([
            'live', '--serial', '127.0.0.1:5555', '--fps', '30',
            '--screenshot-method', 'ADB_nc', '--control-method', 'minitouch',
        ])

    def test_explicit_screenshot_method_skips_auto_emulator_override(self):
        from module.device.screenshot import Screenshot

        device = object.__new__(Screenshot)
        device._screenshot_method_explicit = True
        with (
            patch.object(device, 'nemu_ipc_available', return_value=True),
            patch.object(device, 'ldopengl_available', return_value=True),
        ):
            self.assertEqual(device.screenshot_method_override, '')

    def test_filtered_extract_does_not_clean_sibling_wrappers(self):
        row = button_extract.DataAssets(
            module="demo/probe",
            assets="SAMPLE",
            server="share",
            frame=1,
            file="./assets/share/demo/probe/SAMPLE.png",
            area=(1, 2, 3, 4),
            search=(1, 2, 3, 4),
            color=(10, 20, 30),
            button=(1, 2, 3, 4),
        )
        data = {"demo/probe": {"SAMPLE": {"share": {1: row}}}}
        with (
            patch.object(button_extract, "iter_assets", return_value=data),
            patch.object(button_extract.os, "makedirs"),
            patch.object(button_extract.os, "remove") as remove,
            patch.object(button_extract.CodeGenerator, "write") as write,
        ):
            generated = button_extract.generate_code(modules=["demo/probe"])
        remove.assert_not_called()
        write.assert_called_once()
        self.assertEqual(generated, ["./tasks/demo/assets/assets_demo_probe.py"])

    def test_make_dry_run_returns_structured_output_without_writing(self):
        output = io.StringIO()
        argv = [
            "make",
            "--image",
            str(SCREENSHOT),
            "--name",
            "ASSET_CROP_PROBE",
            "--kind",
            "base",
            "--box",
            "981,121,1190,193",
            "--namespace",
            "share",
            "--module",
            "dev_tools/probe",
            "--suite",
            "asset_crop",
            "--case",
            "dry-run",
            "--dry-run",
            "--json",
        ]
        with redirect_stdout(output):
            return_code = main(argv)
        result = json.loads(output.getvalue())
        self.assertEqual(return_code, 0)
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["status"], "preview")
        self.assertFalse(Path(result["test_image"]).exists())
        self.assertFalse(Path(result["recipe"]).exists())
        self.assertFalse(Path(result["assets"][0]["path"]).exists())


class AssetArchiveTest(unittest.TestCase):
    def setUp(self):
        # Keep synthetic files in the ignored workspace cache. Do not delete
        # them in cleanup: this repository preserves temporary artifacts.
        self.root = WORKTREE / '.cache' / 'asset-archive-tests' / uuid4().hex
        self.source = self.root / 'assets/global_cn/probe/SAMPLE.png'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'synthetic asset bytes')
        self.payload = self.source.read_bytes()
        self.pattern = 'assets/global_cn/probe/*.png'

    def plan(self):
        return asset_archive.create_archive_plan([self.pattern], root=self.root)

    def test_preview_records_hash_without_moving_or_creating_archive(self):
        output = io.StringIO()
        with patch.object(asset_archive, 'ROOT', self.root), redirect_stdout(output):
            self.assertEqual(asset_archive.main(['move', self.pattern, self.pattern]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan['status'], 'preview')
        self.assertEqual(len(plan['files']), 1)
        self.assertEqual(plan['files'][0]['sha256'], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.source.read_bytes(), self.payload)
        self.assertFalse((self.root / asset_archive.ARCHIVE).exists())

    def test_move_preserves_bytes_paths_and_verifiable_manifest(self):
        plan = asset_archive.write_archive(self.plan(), root=self.root)
        record = plan['files'][0]
        self.assertEqual(record['original_path'], 'assets/global_cn/probe/SAMPLE.png')
        self.assertEqual(record['archived_path'], '.cache/unused-assets/global_cn/probe/SAMPLE.png')
        self.assertEqual((self.root / record['archived_path']).read_bytes(), self.payload)
        self.assertFalse(self.source.exists())
        self.assertTrue(record['moved'])
        stored = json.loads((self.root / plan['manifest']).read_text(encoding='utf-8'))
        self.assertEqual(stored, plan)
        output = io.StringIO()
        with patch.object(asset_archive, 'ROOT', self.root), redirect_stdout(output):
            self.assertEqual(asset_archive.main(['verify', plan['manifest']]), 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'verified')

    def test_destination_collision_keeps_entire_batch_in_place(self):
        other = self.source.with_name('SECOND.png')
        other.write_bytes(b'another asset')
        plan = self.plan()
        target = self.root / plan['files'][-1]['archived_path']
        target.parent.mkdir(parents=True)
        target.write_bytes(b'existing archive')
        with self.assertRaises(FileExistsError):
            asset_archive.write_archive(plan, root=self.root)
        self.assertEqual(self.source.read_bytes(), self.payload)
        self.assertEqual(other.read_bytes(), b'another asset')
        self.assertEqual(target.read_bytes(), b'existing archive')
        self.assertFalse((self.root / plan['manifest']).exists())

    def test_changed_source_and_paths_outside_workspace_are_rejected(self):
        plan = self.plan()
        self.source.write_bytes(b'changed asset')
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            asset_archive.write_archive(plan, root=self.root)
        self.assertTrue(self.source.exists())
        self.assertFalse((self.root / asset_archive.ARCHIVE).exists())
        for pattern in ('../assets/*.png', 'tests/*.png', str(self.source)):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                asset_archive.create_archive_plan([pattern], root=self.root)
        plan = self.plan()
        plan['files'][0]['archived_path'] = '../outside.png'
        with self.assertRaisesRegex(ValueError, 'Path outside'):
            asset_archive.write_archive(plan, root=self.root)
        self.assertTrue(self.source.exists())

    def test_verification_detects_changes_and_accepts_prior_manifest_format(self):
        plan = asset_archive.write_archive(self.plan(), root=self.root)
        legacy = {'version': 1, 'files': [{key: value for key, value in plan['files'][0].items()
                                        if key != 'moved'}]}
        self.assertEqual(asset_archive.verify_archive(legacy, root=self.root)['count'], 1)
        (self.root / legacy['files'][0]['archived_path']).write_bytes(b'corrupt archive')
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            asset_archive.verify_archive(legacy, root=self.root)

    def test_interrupted_move_keeps_progress_and_remaining_sources(self):
        other = self.source.with_name('SECOND.png')
        other.write_bytes(b'another asset')
        plan = self.plan()
        original_rename = Path.rename

        def fail_second(source, target):
            if source == other:
                raise OSError('controlled interruption')
            return original_rename(source, target)

        with patch.object(Path, 'rename', autospec=True, side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'controlled interruption'):
                asset_archive.write_archive(plan, root=self.root)
        stored = json.loads((self.root / plan['manifest']).read_text(encoding='utf-8'))
        self.assertEqual(stored['status'], 'failed')
        self.assertEqual([record['moved'] for record in stored['files']], [True, False])
        self.assertEqual((self.root / stored['files'][0]['archived_path']).read_bytes(), self.payload)
        self.assertEqual(other.read_bytes(), b'another asset')
