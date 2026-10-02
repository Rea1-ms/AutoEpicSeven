# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Guild War OCR replay and bounded retry tests; no device or notification calls."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

WORKTREE = Path(__file__).resolve().parents[2]
import module.config.server as server
server.set_lang('cn')
from tasks.knights.team_battle import KnightsTeamBattleMixin, OcrKnightsCrest
from tasks.knights.team_battle_status import TeamBattleCrestStatus
from tasks.knights.assets.assets_knights_gvg import KNIGHTS_CREST, OCR_KNIGHTS_CREST

SAMPLE = input_root('knights') / 'guild_war_3_of_3_20260919.png'
IMAGE = load_image(SAMPLE)
ZERO_IMAGE = np.array(Image.open(WORKTREE / 'assets/share/knights/gvg/OCR_KNIGHTS_CREST.png').convert('RGB'))


def read(image, lang='cn'):
    reader = KnightsTeamBattleMixin()
    reader.device = SimpleNamespace(image=image)
    reader.config = SimpleNamespace(Emulator_GameLanguage=lang)
    return reader._ocr_knights_crest()


def shifted(image, dx, dy=0):
    result = np.zeros_like(image)
    x1, x2 = max(0, -dx), min(image.shape[1], image.shape[1] - dx)
    y1, y2 = max(0, -dy), min(image.shape[0], image.shape[0] - dy)
    result[y1+dy:y2+dy, x1+dx:x2+dx] = image[y1:y2, x1:x2]
    return result


class OcrTests(unittest.TestCase):
    def tearDown(self):
        server.set_lang('cn')
    def test_real_3_of_3_and_0_of_3_on_both_asset_namespaces(self):
        for namespace in ('cn', 'global_cn'):
            server.set_lang(namespace)
            for image, current in ((IMAGE, 3), (ZERO_IMAGE, 0)):
                with self.subTest(namespace=namespace, current=current):
                    self.assertEqual(read(image), TeamBattleCrestStatus(current, current, 3))
    def test_counter_tracks_horizontal_toolbar_shift(self):
        for image, current in ((IMAGE, 3), (ZERO_IMAGE, 0)):
            for dx in (-54, -30, 3):
                with self.subTest(current=current, dx=dx):
                    self.assertEqual(read(shifted(image, dx)), TeamBattleCrestStatus(current, current, 3))
    def test_counter_tracks_small_vertical_shift(self):
        for dy in (-3, 3):
            with self.subTest(dy=dy):
                self.assertEqual(read(shifted(IMAGE, -30, dy)), TeamBattleCrestStatus(3, 3, 3))
    def test_no_current_crest_rejects_stale_match_before_ocr(self):
        self.assertTrue(KNIGHTS_CREST.match_template_luma(IMAGE, similarity=.7))
        with patch('tasks.knights.team_battle.OcrKnightsCrest') as ocr:
            self.assertIsNone(read(np.zeros_like(IMAGE)))
            ocr.assert_not_called()
    def test_left_neighbour_and_crest_are_excluded(self):
        noisy = IMAGE.copy()
        noisy[20:44, 860:883] = 255
        with patch('tasks.knights.team_battle.OcrKnightsCrest') as ocr:
            ocr.return_value.ocr_single_line.return_value = (3, 0, 3)
            self.assertEqual(read(noisy, 'auto'), TeamBattleCrestStatus(3, 3, 3))
            region = ocr.call_args.args[0]
            self.assertGreater(region.area[0], KNIGHTS_CREST.area[2] + KNIGHTS_CREST.button_offset[0])
            self.assertLessEqual(region.area[2] - region.area[0], 40)
            self.assertEqual(ocr.call_args.kwargs['lang'], 'cn')
    def test_single_digit_counter_parsing(self):
        ocr = OcrKnightsCrest(OCR_KNIGHTS_CREST, lang='cn')
        for current in range(4):
            self.assertEqual(ocr.format_result(f'{current}/3'), (current, 3-current, 3))
        self.assertEqual(ocr.format_result(ocr.after_process(' O ／ 3 ')), (0, 3, 3))
    def test_extra_digits_and_noise_are_not_repaired(self):
        ocr = OcrKnightsCrest(OCR_KNIGHTS_CREST, lang='cn')
        for text in ('31/34', '3/34', '31/3', '03/3', '3/3x', 'x3/3', '3 3/3', '', '3', '3/3 4'):
            with self.subTest(text=text):
                self.assertEqual(ocr.format_result(text), (0, 0, 0))
    def test_three_attack_semantic_guard_remains(self):
        for current, total in ((4, 3), (3, 4), (3, 34), (31, 34), (0, 0)):
            with self.subTest(current=current, total=total):
                with patch.object(OcrKnightsCrest, 'ocr_single_line', return_value=(current, total-current, total)):
                    self.assertIsNone(read(IMAGE))
    def test_legacy_wide_crop_contains_non_counter_text(self):
        # The exact spurious characters vary with animation/model state; only
        # assert the crop geometry, not that every run must produce 31/34.
        self.assertLess(OCR_KNIGHTS_CREST.area[0], KNIGHTS_CREST.area[0])
        left, top, right, bottom = OCR_KNIGHTS_CREST.area
        self.assertTrue(np.any(IMAGE[top:bottom, left:KNIGHTS_CREST.area[2]]))
        self.assertEqual(read(IMAGE).to_counter(), '3/3')


class Flow(KnightsTeamBattleMixin):
    def __init__(self, statuses):
        self.statuses, self.index = statuses, 0
        self.device = SimpleNamespace(image=0, screenshot=self.screenshot)
        self._reset_team_battle_status_runtime = Mock()
        self._update_team_battle_dashboard_invalid = Mock()
        self._update_team_battle_dashboard_counter = Mock()
        self._send_or_schedule_team_battle_reminder = Mock()
        self._back_to_knights_from_team_battle = Mock(return_value=True)
        self.appear = Mock(return_value=False)
        self._is_team_battle_member_insufficient = Mock(return_value=False)
        self._is_team_battle_home = Mock(return_value=True)
    def screenshot(self):
        self.index += 1
        if self.index >= len(self.statuses):
            raise AssertionError('Retried more than the supplied frames')
        self.device.image = self.index
    def _ocr_knights_crest(self):
        return self.statuses[self.index]


class FlowTests(unittest.TestCase):
    def test_transient_bad_read_retries_before_updating_status_or_reminder(self):
        valid = TeamBattleCrestStatus(3, 3, 3)
        flow = Flow([None, None, valid])
        self.assertTrue(flow.run_team_battle())
        self.assertEqual(flow.index, 2)
        flow._update_team_battle_dashboard_invalid.assert_not_called()
        flow._update_team_battle_dashboard_counter.assert_called_once_with(valid)
        flow._send_or_schedule_team_battle_reminder.assert_called_once_with(valid)
        flow._back_to_knights_from_team_battle.assert_called_once_with(skip_first_screenshot=True)
    def test_repeated_bad_reads_are_bounded_and_do_not_become_zero_attacks(self):
        flow = Flow([None, None, None])
        self.assertTrue(flow.run_team_battle())
        self.assertEqual(flow.index, 2)
        flow._update_team_battle_dashboard_invalid.assert_called_once()
        flow._update_team_battle_dashboard_counter.assert_not_called()
        flow._send_or_schedule_team_battle_reminder.assert_not_called()
    def test_real_zero_is_accepted_without_retries(self):
        valid = TeamBattleCrestStatus(0, 0, 3)
        flow = Flow([valid])
        self.assertTrue(flow.run_team_battle())
        self.assertEqual(flow.index, 0)
        flow._update_team_battle_dashboard_counter.assert_called_once_with(valid)
        flow._update_team_battle_dashboard_invalid.assert_not_called()
