# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import fixture_path

"""Offline checks for the asset capture and crop developer tool."""

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np


WORKTREE = Path(__file__).resolve().parents[2]
ORIGINAL_REPO = Path(__import__("subprocess").check_output(["git","rev-parse","--git-common-dir"],text=True).strip()).resolve().parent
SCREENSHOT = fixture_path('urgent_tasks/superior-detail.png')

from dev_tools import button_extract  # noqa: E402
from dev_tools.asset_crop import (  # noqa: E402
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
from dev_tools.asset_crop_ui import InteractiveCropper  # noqa: E402


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
        self.assertEqual(np.count_nonzero(plan.image[680:720, 0:180]), 0)
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
            patch("cv2.waitKey", side_effect=[ord("z"), 27]),
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
            patch("cv2.waitKey", side_effect=wait_key),
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
