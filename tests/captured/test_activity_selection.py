# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Real sidebar selection regression for overseas INFINITY task navigation."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

WORKTREE = Path(__file__).resolve().parents[2]

import module.config.server as server
from tasks.activity.assets.assets_activity_special_26_8_27 import FREE_20_GACHA_SELECTED
from tasks.activity.free_gacha_20 import FreeGacha20

FIXTURES = input_root('')


class Navigation(FreeGacha20):
    def __init__(self, frames):
        self.config = SimpleNamespace(Emulator_GameLanguage='auto')
        self.frames = frames
        self.index = -1
        self.clicks = []
        self.swipes = []
        self.device = SimpleNamespace(
            image=None, screenshot=self.screenshot,
            click=lambda button: self.clicks.append(button),
            swipe=lambda *args, **kwargs: self.swipes.append(args),
            stuck_record_add=lambda button: None,
        )
        self.device.screenshot()

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Selection did not stop on the real selected screenshot')
        self.device.image = self.frames[self.index]

    def ui_page_appear(self, page):
        return True

    def interval_is_reached(self, *args, **kwargs):
        return True

    def interval_reset(self, *args, **kwargs):
        pass

    def handle_network_error(self):
        return False


class ActivitySelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selected = load_image(str(FIXTURES / 'activity_navigation/infinity_selected_oversea.png'))
        cls.other = load_image(str(FIXTURES / 'e7wc_battle_gate/battle_gate_available.png'))

    def setUp(self):
        server.set_lang('global_cn')

    def test_reproduces_old_template_failure_on_already_selected_sidebar(self):
        old = FREE_20_GACHA_SELECTED.data_buttons['cn']
        self.assertFalse(old.match_template_color(self.selected))
        self.assertTrue(Navigation([self.selected])._activity_selected(FREE_20_GACHA_SELECTED))

    def test_already_selected_returns_before_ocr_or_click(self):
        nav = Navigation([self.selected])
        with patch('tasks.activity.navigation.Ocr') as ocr:
            self.assertTrue(nav.select_activity('INFINITY', FREE_20_GACHA_SELECTED))
            ocr.return_value.detect_and_ocr.assert_not_called()
        self.assertEqual(nav.clicks, [])
        self.assertEqual(nav.swipes, [])

    def test_click_stops_as_soon_as_selected_state_arrives(self):
        nav = Navigation([self.other, self.other, self.selected])
        row = SimpleNamespace(ocr_text='INFINITY00', box=(25, 188, 133, 209), score=1)
        with patch('tasks.activity.navigation.Ocr') as ocr:
            ocr.return_value.detect_and_ocr.return_value = [row]
            self.assertTrue(nav.select_activity('INFINITY', FREE_20_GACHA_SELECTED))
        self.assertEqual(len(nav.clicks), 1)
        self.assertEqual(nav.index, 2)
        self.assertEqual(nav.swipes, [])

    def test_dropped_click_still_retries_before_real_selection(self):
        nav = Navigation([self.other] * 4 + [self.selected])
        row = SimpleNamespace(ocr_text='INFINITY00', box=(25, 188, 133, 209), score=1)
        with patch('tasks.activity.navigation.Ocr') as ocr:
            ocr.return_value.detect_and_ocr.return_value = [row]
            self.assertTrue(nav.select_activity('INFINITY', FREE_20_GACHA_SELECTED))
        self.assertEqual(len(nav.clicks), 2)
        self.assertEqual(nav.index, 4)

    def test_scroll_position_uses_common_search_and_restores_wrapper(self):
        buttons = tuple(FREE_20_GACHA_SELECTED.iter_buttons())
        searches = [b.search for b in buttons]
        for shift in (-300, 0, 220):
            with self.subTest(shift=shift):
                shifted = np.roll(self.selected, shift, axis=0)
                self.assertTrue(Navigation([shifted])._activity_selected(FREE_20_GACHA_SELECTED))
                self.assertEqual([b.search for b in buttons], searches)
                self.assertTrue(all(tuple(b._button_offset) == (0, 0) for b in buttons))

    def test_other_selected_event_does_not_match_free_summons(self):
        self.assertFalse(Navigation([self.other])._activity_selected(FREE_20_GACHA_SELECTED))
        koharu = load_image(str(FIXTURES / 'koharu_raffle/available.png'))
        self.assertFalse(Navigation([koharu])._activity_selected(FREE_20_GACHA_SELECTED))

    def test_text_without_selected_color_is_not_accepted(self):
        grayscale = np.repeat(self.selected.mean(axis=2, keepdims=True).astype(np.uint8), 3, axis=2)
        self.assertFalse(Navigation([grayscale])._activity_selected(FREE_20_GACHA_SELECTED))

    def test_cn_keeps_original_template(self):
        server.set_lang('cn')
        path = FREE_20_GACHA_SELECTED.matched_button.file
        self.assertIn('/cn/', path)
        self.assertTrue(Navigation([load_image(path)])._activity_selected(FREE_20_GACHA_SELECTED))
        server.set_lang('global_cn')
        self.assertIn('/global_cn/', FREE_20_GACHA_SELECTED.matched_button.file)
