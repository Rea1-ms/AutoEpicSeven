# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline rank and actual UI-route regressions. Never connects to an emulator."""
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
import module.config.server as server
server.set_lang('cn')
from module.base.button import ClickButton
from module.exception import GamePageUnknownError
from tasks.base.account_level import AccountLevel, parse_account_level
from tasks.base.assets.assets_base_main_page import OCR_ACCOUNT_LEVEL
from tasks.base.assets.assets_base_page import MAIN_GOTO_COMMON_ACTIVITY, MAIN_GOTO_SECRET_SHOP
from tasks.base.page import Page, page_main, page_common_activity, page_secret_shop, page_sanctuary, page_inventory
from tasks.base.route_entry import match_route_entry
from tasks.base.ui import UI

SAMPLE = Path(__file__).parent / 'screenshots' / 'main_navigation' / 'srank20_20260919.png'


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
    def test_rank_boundary_and_super_rank_are_distinct(self):
        for text, kind, value, unlocked in (
            ('Rank.1', 'rank', 1, False), ('Rank.69', 'rank', 69, False),
            ('Rank.70', 'rank', 70, True), ('S.Rank.1', 'srank', 1, True),
            ('S.Rank.20', 'srank', 20, True), (' s. Rank 。 20 ', 'srank', 20, True),
            ('SRank20', 'srank', 20, True),
        ):
            with self.subTest(text=text):
                result = parse_account_level(text)
                self.assertEqual((result.kind, result.value, result.milestone_unlocked), (kind, value, unlocked))
    def test_invalid_or_prefixless_ocr_is_unknown(self):
        for text in ('', '20', '69', 'Rank.0', 'Rank.71', 'Rank.100', 'S.Rank.0', 'S.Rank.21', 'xRank.70', 'Rank.69junk'):
            with self.subTest(text=text):
                self.assertIsNone(parse_account_level(text))
    def test_non_main_does_not_read_and_clears_previous_hint(self):
        ui = Route([frame(0, pages=[page_secret_shop])])
        ui.account_level = AccountLevel('srank', 20)
        with patch('tasks.base.account_level.Ocr') as reader:
            self.assertIsNone(ui.read_main_account_level())
            reader.assert_not_called()
            self.assertIsNone(ui.account_level)
    def test_overlay_does_not_read_rank(self):
        ui = Route([frame(0, overlay=True)])
        with patch('tasks.base.account_level.Ocr') as reader:
            self.assertIsNone(ui.read_main_account_level())
            reader.assert_not_called()
    def test_unknown_rank_clears_previous_hint_and_normalizes_language(self):
        ui = Route([frame(0)])
        ui.account_level = AccountLevel('srank', 20)
        with patch('tasks.base.account_level.Ocr') as reader:
            reader.return_value.ocr_single_line.return_value = '20'
            self.assertIsNone(ui.read_main_account_level())
            reader.assert_called_once_with(OCR_ACCOUNT_LEVEL, lang='cn', name='AccountLevel')
            self.assertIsNone(ui.account_level)
    def test_current_account_does_not_inherit_another_instance(self):
        first, second = Route([frame(0)]), Route([frame(0)])
        first.account_level = AccountLevel('srank', 20)
        self.assertIsNone(second.account_level)


class MatchingTests(unittest.TestCase):
    def tearDown(self):
        server.set_lang('cn')
        Page.clear_connection()
    def test_priority_is_geometry_based_for_each_server_and_rank(self):
        for lang in ('cn', 'global_cn'):
            server.set_lang(lang)
            for wrapper in (MAIN_GOTO_COMMON_ACTIVITY, MAIN_GOTO_SECRET_SHOP):
                for lower in (False, True):
                    with self.subTest(lang=lang, entry=wrapper.name, lower=lower):
                        expected = sorted(wrapper.buttons, key=lambda asset: asset.area[1], reverse=lower)[0]
                        with patch.object(expected, 'match_template_luma', return_value=True) as detect:
                            click = match_route_entry(wrapper, np.zeros((720, 1280, 3), dtype=np.uint8), lower)
                        detect.assert_called_once()
                        self.assertEqual(click.button, expected.button)
    def test_click_snapshot_ignores_wrapper_cache_and_later_offsets(self):
        wrapper = MAIN_GOTO_COMMON_ACTIVITY
        upper, lower = sorted(wrapper.buttons, key=lambda asset: asset.area[1])
        self.assertTrue(wrapper.match_template_luma(image_from_asset(lower)))
        click = match_route_entry(wrapper, image_from_asset(upper, (3, 2)), True)
        self.assertIsInstance(click, ClickButton)
        expected = tuple(value + (3 if index % 2 == 0 else 2) for index, value in enumerate(upper._button))
        self.assertEqual(click.button, expected)
        self.assertIsNone(match_route_entry(wrapper, np.zeros((720, 1280, 3), dtype=np.uint8), False))
        self.assertEqual(click.button, expected)


class NavigationTests(unittest.TestCase):
    def tearDown(self):
        server.set_lang('cn')
        Page.clear_connection()
    def test_both_servers_both_layouts_go_through_actual_ui_goto(self):
        for lang in ('cn', 'global_cn'):
            server.set_lang(lang)
            for wrapper, target in ((MAIN_GOTO_COMMON_ACTIVITY, page_common_activity), (MAIN_GOTO_SECRET_SHOP, page_secret_shop)):
                for index, asset in enumerate(sorted(wrapper.buttons, key=lambda asset: asset.area[1])):
                    with self.subTest(lang=lang, target=target.name, index=index):
                        rank = 'S.Rank.1' if index else 'Rank.69'
                        nav = Route([frame(0, image_from_asset(asset), rank=rank), frame(.2, pages=[target])])
                        nav.run_route(target)
                        self.assertEqual(len(nav.clicks), 1)
                        self.assertEqual(nav.clicks[0][2], asset._button)
                        self.assertIsInstance(nav.clicks[0][1], ClickButton)
                        self.assertEqual(nav.index, 1)
    def test_wrong_or_unknown_rank_still_tries_other_layout(self):
        upper = min(MAIN_GOTO_COMMON_ACTIVITY.buttons, key=lambda asset: asset.area[1])
        for rank in ('S.Rank.20', 'nonsense'):
            with self.subTest(rank=rank):
                nav = Route([frame(0, image_from_asset(upper), rank=rank), frame(.2, pages=[page_common_activity])])
                nav.run_route(page_common_activity)
                self.assertEqual(nav.clicks[0][2], upper._button)
    def test_no_match_times_out_without_default_or_stale_click(self):
        wrapper = MAIN_GOTO_COMMON_ACTIVITY
        self.assertTrue(wrapper.match_template_luma(image_from_asset(wrapper.buttons[0])))
        nav = Route([frame(at, pages=[page_main, page_inventory]) for at in range(10)])
        with self.assertRaisesRegex(GamePageUnknownError, 'UI route entry not found'):
            nav.run_route(page_common_activity)
        self.assertEqual(nav.clicks, [])
        self.assertTrue(all(page.parent is None for page in Page.iter_pages()))
    def test_temporary_miss_retries_on_fresh_frame(self):
        asset = MAIN_GOTO_COMMON_ACTIVITY.buttons[1]
        nav = Route([frame(0), frame(.3, image_from_asset(asset)), frame(.5, pages=[page_common_activity])])
        nav.run_route(page_common_activity)
        self.assertEqual([(at, box) for at, _, box in nav.clicks], [(.3, asset._button)])
    def test_layout_change_after_failed_click_relocates(self):
        lower, upper = MAIN_GOTO_COMMON_ACTIVITY.buttons
        nav = Route([
            frame(0, image_from_asset(lower), rank='S.Rank.20'),
            frame(.4, image_from_asset(upper)),
            frame(2.1, image_from_asset(upper)),
            frame(2.3, pages=[page_common_activity]),
        ])
        nav.run_route(page_common_activity)
        self.assertEqual([(at, box) for at, _, box in nav.clicks], [(0, lower._button), (2.1, upper._button)])
        self.assertEqual(nav.reader.ocr_single_line.call_count, 2)
    def test_destination_exit_does_not_wait_for_click_interval(self):
        asset = MAIN_GOTO_COMMON_ACTIVITY.buttons[0]
        nav = Route([frame(0, image_from_asset(asset)), frame(.1), frame(.2, pages=[page_common_activity])])
        nav.run_route(page_common_activity)
        self.assertEqual(len(nav.clicks), 1)
        self.assertEqual(nav.index, 2)
    def test_empty_initial_image_is_captured(self):
        asset = MAIN_GOTO_COMMON_ACTIVITY.buttons[0]
        nav = Route([frame(0, image_from_asset(asset)), frame(.2, pages=[page_common_activity])], initial_image=False)
        nav.run_route(page_common_activity)
        self.assertEqual(len(nav.clicks), 1)
        self.assertEqual(nav.index, 1)
    def test_confirmation_new_frame_with_overlay_blocks_click(self):
        asset = MAIN_GOTO_COMMON_ACTIVITY.buttons[0]
        nav = Route([
            frame(0, image_from_asset(asset), confirm_next=True),
            frame(.2, image_from_asset(asset), overlay=True),
            frame(.4, image_from_asset(asset)), frame(.6, pages=[page_common_activity]),
        ])
        nav.run_route(page_common_activity)
        self.assertEqual([at for at, _, _ in nav.clicks], [.4])
        self.assertEqual(nav.reader.ocr_single_line.call_count, 1)
    def test_fixed_route_needs_neither_rank_nor_entry_matching(self):
        nav = Route([frame(0), frame(.2, pages=[page_sanctuary])])
        with patch.object(nav, '_ui_match_route_entry') as matcher:
            nav.run_route(page_sanctuary)
            matcher.assert_not_called()
        self.assertEqual(len(nav.clicks), 1)
        nav.reader.ocr_single_line.assert_not_called()
    def test_network_recovery_precedes_target_exit(self):
        nav = Route([frame(0, pages=[page_common_activity], recovery=True), frame(.5, pages=[page_common_activity])])
        nav.run_route(page_common_activity)
        self.assertEqual(nav.index, 1)
        self.assertEqual(nav.clicks, [])
    def test_server_import_and_graph_routes_in_fresh_processes(self):
        code = """
import module.config.server as server
server.server = {package!r}
server.set_lang({lang!r})
from tasks.base.ui import UI
from tasks.base.page import Page, page_main, page_secret_shop, page_common_activity, page_menu
for destination in (page_secret_shop, page_common_activity):
    assert destination in page_main.links_need_match
    Page.init_connection(destination)
    assert page_main.parent == destination
    assert page_menu.parent is not None
    Page.init_connection(page_main)
    assert destination.parent == page_main
    Page.clear_connection()
"""
        for package, lang in (('CN-Official', 'cn'), ('OVERSEA-Play', 'global_cn'), ('OVERSEA-Play', 'global_en')):
            with self.subTest(package=package, lang=lang):
                result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code.format(package=package, lang=lang)], cwd=WORKTREE, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
