# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Offline rank and actual UI-route regressions. Never connects to an emulator."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image

WORKTREE = Path(__file__).resolve().parents[2]
import module.config.server as server
server.set_lang('cn')
from tasks.base.account_level import AccountLevel, parse_account_level
from tasks.base.assets.assets_base_main_page import OCR_ACCOUNT_LEVEL
from tasks.base.assets.assets_base_page import MAIN_GOTO_COMMON_ACTIVITY, MAIN_GOTO_SECRET_SHOP
from tasks.base.page import Page, page_main
from tasks.base.ui import UI

SAMPLE = input_root('main_navigation') / 'srank20_20260919.png'


def image_from_asset(asset, offset=(0, 0)):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    x1, y1, x2, y2 = asset.area
    dx, dy = offset
    original = np.array(Image.open(WORKTREE / asset.file).convert('RGB'))
    image[y1+dy:y2+dy, x1+dx:x2+dx] = original[y1:y2, x1:x2]
    return image


def frame(at, image=None, pages=None, rank='Rank.69', **extra):
    if image is None:
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
    return dict(at=at, image=image, pages=[page_main] if pages is None else pages, rank=rank, **extra)


class RankTests(unittest.TestCase):
    def test_real_rank_and_super_rank_ocr_use_same_crop(self):
        from module.ocr.ocr import Ocr
        reader = Ocr(OCR_ACCOUNT_LEVEL, lang='cn')
        for path, expected in (
            (WORKTREE / 'assets/share/base/main_page/OCR_ACCOUNT_LEVEL.png', AccountLevel('rank', 69)),
            (SAMPLE, AccountLevel('srank', 20)),
        ):
            with self.subTest(path=path.name):
                text = reader.ocr_single_line(load_image(path))
                self.assertEqual(parse_account_level(text), expected)


class MatchingTests(unittest.TestCase):
    def tearDown(self):
        server.set_lang('cn')
        Page.clear_connection()
    def test_real_super_rank_page_and_global_entries(self):
        server.set_lang('global_cn')
        ui = UI.__new__(UI)
        ui.config = SimpleNamespace(Emulator_GameLanguage='cn')
        ui.device = SimpleNamespace(image=load_image(SAMPLE), stuck_record_add=Mock())
        self.assertTrue(ui.is_in_main())
        for wrapper, expected_y in ((MAIN_GOTO_COMMON_ACTIVITY, 484), (MAIN_GOTO_SECRET_SHOP, 356)):
            with self.subTest(entry=wrapper.name):
                click = ui._ui_match_route_entry(page_main, wrapper)
                self.assertIsNotNone(click)
                self.assertLessEqual(abs(click.button[1] - expected_y), 2)
                self.assertEqual(ui.account_level, AccountLevel('srank', 20))
