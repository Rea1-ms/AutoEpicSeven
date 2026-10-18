# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Real page confirmation and sidebar navigation for overseas free summons."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

WORKTREE = Path(__file__).resolve().parents[2]

import module.config.server as server
from tasks.activity.assets.assets_activity_special_26_8_27 import FREE_20_GACHA_SELECTED
from tasks.activity.free_gacha_20 import FreeGacha20
from tests.support.offline import ControlledClock, fixture_image, record_action

FIXTURES = input_root('')


class Navigation(FreeGacha20):
    def __init__(self, frames, clock=None):
        self.config = SimpleNamespace(Emulator_GameLanguage='auto')
        self.interval_timer = {}
        self.clock = clock
        self.frames = frames
        self.index = -1
        self.clicks = []
        self.click_frames = []
        self.swipes = []
        self.device = SimpleNamespace(
            image=None, screenshot=self.screenshot,
            click=self.click,
            swipe=lambda *args, **kwargs: self.swipes.append(args),
            stuck_record_add=lambda button: None,
        )
        self.device.screenshot()

    def click(self, button):
        self.clicks.append(button)
        self.click_frames.append(self.index)
        record_action(f'frame={self.index}: click {button}')

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Selection did not stop on the real selected screenshot')
        self.device.image = self.frames[self.index]
        if self.clock is not None:
            self.clock.advance(1)

    def ui_page_appear(self, page):
        return True

    def handle_network_error(self):
        return False


class ActivitySelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selected = load_image(str(FIXTURES / 'activity_navigation/infinity_page_selected.png'))
        cls.other = load_image(str(FIXTURES / 'e7wc_battle_gate/battle_gate_available.png'))

    def setUp(self):
        server.set_lang('global_cn')
        clock = ControlledClock()
        self.clock = clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.addCleanup(server.set_lang, 'global_cn')

    def test_reproduces_old_template_failure_on_already_selected_sidebar(self):
        old = FREE_20_GACHA_SELECTED.data_buttons['cn']
        self.assertFalse(old.match_template_color(self.selected))
        self.assertTrue(Navigation([self.selected])._activity_selected(FREE_20_GACHA_SELECTED))
        # The historical input masks the page body. A selected sidebar alone
        # no longer proves that the overseas free-summons page has loaded.
        sidebar = load_image(str(FIXTURES / 'activity_navigation/infinity_selected_oversea.png'))
        self.assertFalse(Navigation([sidebar])._activity_selected(FREE_20_GACHA_SELECTED))

    def test_already_selected_returns_before_ocr_or_click(self):
        nav = Navigation([self.selected], self.clock)
        with patch('tasks.activity.navigation.Ocr') as ocr:
            self.assertTrue(nav.select_activity('INFINITY', FREE_20_GACHA_SELECTED))
            ocr.return_value.detect_and_ocr.assert_not_called()
        self.assertEqual(nav.clicks, [])
        self.assertEqual(nav.swipes, [])

    def test_click_stops_as_soon_as_selected_state_arrives(self):
        nav = Navigation([self.other, self.other, self.selected], self.clock)
        row = SimpleNamespace(ocr_text='INFINITY00', box=(25, 188, 133, 209), score=1)
        with patch('tasks.activity.navigation.Ocr') as ocr:
            ocr.return_value.detect_and_ocr.return_value = [row]
            self.assertTrue(nav.select_activity('INFINITY', FREE_20_GACHA_SELECTED))
        self.assertEqual(len(nav.clicks), 1)
        self.assertEqual(nav.click_frames, [1])
        self.assertEqual(nav.index, 2)
        self.assertEqual(nav.swipes, [])

    def test_dropped_click_still_retries_before_real_selection(self):
        # The production timer requires strictly more than two seconds.
        # Keep an unchanged frame beyond that boundary so the retry occurs
        # before the page finally changes; never bypass the interval.
        nav = Navigation([self.other] * 5 + [self.selected], self.clock)
        row = SimpleNamespace(ocr_text='INFINITY00', box=(25, 188, 133, 209), score=1)
        with patch('tasks.activity.navigation.Ocr') as ocr:
            ocr.return_value.detect_and_ocr.return_value = [row]
            self.assertTrue(nav.select_activity('INFINITY', FREE_20_GACHA_SELECTED))
        self.assertEqual(len(nav.clicks), 2)
        self.assertEqual(nav.click_frames, [1, 4])
        self.assertEqual(nav.index, 5)

    def test_scroll_position_uses_common_search_and_restores_wrapper(self):
        buttons = tuple(FREE_20_GACHA_SELECTED.iter_buttons())
        searches = [b.search for b in buttons]
        for shift in (-300, 0, 220):
            with self.subTest(shift=shift):
                # Scrolling moves only the sidebar, never the page body.
                shifted = self.selected.copy()
                shifted[:, :260] = np.roll(shifted[:, :260], shift, axis=0)
                self.assertTrue(Navigation([shifted])._activity_selected(FREE_20_GACHA_SELECTED))
                self.assertEqual([b.search for b in buttons], searches)
                self.assertTrue(all(tuple(b._button_offset) == (0, 0) for b in buttons))

    def test_missing_page_anchor_rejects_orange_selected_sidebar(self):
        masked = self.selected.copy()
        masked[403:453, 1055:1119] = 0
        self.assertFalse(Navigation([masked])._activity_selected(FREE_20_GACHA_SELECTED))

    def test_page_anchor_is_not_accepted_in_sidebar(self):
        misplaced = np.zeros_like(self.selected)
        misplaced[300:310, 100:124] = self.selected[423:433, 1075:1099]
        self.assertFalse(Navigation([misplaced])._activity_selected(FREE_20_GACHA_SELECTED))

    def test_dimmed_page_is_not_selected(self):
        dimmed = (self.selected.astype(float) * 0.5).astype(np.uint8)
        self.assertFalse(Navigation([dimmed])._activity_selected(FREE_20_GACHA_SELECTED))

    def test_existing_complete_captures_confirm_page_with_hidden_tab(self):
        for fixture in ('meirin-reward-sidebar-visible', 'meirin-reward-sidebar-top'):
            with self.subTest(fixture=fixture):
                image = fixture_image(WORKTREE / 'tests/fixtures/historical/manifest.json', fixture)
                nav = Navigation([image], self.clock)
                with patch('tasks.activity.navigation.Ocr') as ocr:
                    self.assertTrue(nav.select_activity('INFINITY', FREE_20_GACHA_SELECTED))
                    ocr.return_value.detect_and_ocr.assert_not_called()
                self.assertEqual(nav.clicks, [])
                self.assertEqual(nav.swipes, [])

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
        for shift in (-300, 0, 220):
            with self.subTest(shift=shift):
                image = np.roll(load_image(path), shift, axis=0)
                self.assertTrue(Navigation([image])._activity_selected(FREE_20_GACHA_SELECTED))
        server.set_lang('global_cn')
        self.assertIn('/global_cn/', FREE_20_GACHA_SELECTED.matched_button.file)
