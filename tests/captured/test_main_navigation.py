# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Offline rank and actual UI-route regressions. Never connects to an emulator."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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


def timer_factory(clock):
    class Timer:
        def __init__(self, limit, count=0):
            self.limit, self.count, self.access, self.deadline = limit, count, 0, None
        def reset(self):
            self.deadline, self.access = clock() + self.limit, 0
            return self
        def start(self):
            if self.deadline is None:
                self.reset()
            return self
        def reached(self):
            self.access += 1
            return self.deadline is None or (clock() >= self.deadline and self.access > self.count)
    return Timer


class Route(UI):
    def __init__(self, frames, initial_image=True):
        self.frames, self.index = frames, 0
        self.clicks = []
        self.config = SimpleNamespace(Emulator_GameLanguage='auto')
        self.device = SimpleNamespace(
            image=None, screenshot=self.screenshot, click=self.click,
            stuck_record_add=Mock(),
        )
        self.interval_timer = {}
        self.interval_clear = Mock()
        if initial_image:
            self.device.image = self.frame['image']
        else:
            self.index = -1
    @property
    def frame(self):
        return self.frames[max(0, self.index)]
    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Navigation did not finish on supplied frames')
        self.device.image = self.frame['image']
    def click(self, button):
        self.clicks.append((self.frame['at'], button, tuple(button.button)))
    def is_in_main(self, interval=0):
        return page_main in self.frame['pages'] and not self.frame.get('overlay', False)
    def ui_page_appear(self, page, interval=0):
        assert interval == 0
        return self.is_in_main() if page == page_main else page in self.frame['pages']
    def ui_page_confirm(self, page):
        if self.frame.get('confirm_next'):
            self.screenshot()
        return False
    def handle_ui_recovery(self):
        return self.frame.get('recovery', False)
    def ui_additional(self):
        return False
    def handle_popup_confirm(self):
        return False
    def run_route(self, destination):
        self.reader = Mock()
        self.reader.ocr_single_line.side_effect = lambda image: self.frame.get('rank', '')
        with patch('tasks.base.account_level.Ocr', return_value=self.reader), patch(
            'tasks.base.ui.Timer', timer_factory(lambda: self.frame['at'])
        ):
            self.ui_goto(destination)


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
