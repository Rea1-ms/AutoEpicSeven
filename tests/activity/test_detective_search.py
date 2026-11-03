"""Detective-search point recognition and daily reward interaction replays."""

import unittest
from unittest.mock import patch

from module.config import server
from tests.support.offline import ROOT, fixture_image

server.set_lang("global_cn")

from module.ocr.ocr import Ocr
from tasks.activity.assets.assets_activity_detective_search import (
    OCR_DETECTIVE_POINTS,
)
from tasks.activity.detective_search import DetectiveSearch
from tests.support.activity_replay import (
    AVAILABLE,
    HOME,
    POPUP,
    RECEIVED,
    ActivityReplayCase,
    capture,
)

MANIFEST = ROOT / "tests/fixtures/historical/manifest.json"


class DetectiveSearchRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_ocr_reads_detective_points(self):
        image = fixture_image(MANIFEST, AVAILABLE)
        text = Ocr(OCR_DETECTIVE_POINTS, lang='cn').ocr_single_line(image)
        self.assertEqual(text.replace(' ', ''), '720/720')


class DetectiveSearchReplayTests(ActivityReplayCase):
    def test_detective_real_four_tiers_and_partial_receipt(self):
        for name, partial, expected in ((AVAILABLE, False, ["pending"] * 4),
                                        (RECEIVED, False, ["received"] * 4),
                                        (AVAILABLE, True, ["received"] + ["pending"] * 3)):
            r = self.replay(DetectiveSearch, [capture(name, partial=partial)])
            self.assertEqual([r.task._reward_state(a) for a in DetectiveSearch.REWARD_AREAS], expected)

    def test_detective_claim_popup_then_all_receipts_records_completion(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE), capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "DETECTIVE_CLAIM_180"), (1, "TOUCH_TO_CLOSE")])
        self.assertTrue(r.task.config.values)

    def test_detective_receipts_before_popup_do_not_finish_pending_reward(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE)] + [capture(RECEIVED)] * 4
                        + [capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [5])
        self.assertEqual(r.index, 6)

    def test_detective_dropped_tap_retries_only_original_tier(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE)] * 7 + [capture(POPUP)] * 7 + [capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, "DETECTIVE_CLAIM_180", 2)
        r.assert_retried(self, "TOUCH_TO_CLOSE", 2)
        self.assertFalse(r.clicks("DETECTIVE_CLAIM_360"))

    def test_detective_partial_claim_advances_to_next_tier_only_after_popup(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE, partial=True), capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions[0], (0, "DETECTIVE_CLAIM_360"))
        self.assertTrue(552 <= r.click_areas[0][2][0] < 730)

    def test_detective_zero_points_keeps_battle_recheck_without_clicking(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE, locked=True)])
        with patch("tasks.activity.detective_search.Ocr") as ocr:
            ocr.return_value.ocr_single_line.return_value = "0/720"
            self.assertTrue(r.task.run_claim(navigate=False))
        self.assertFalse(r.actions)
        self.assertFalse(r.task.config.values)

    def test_detective_unknown_points_time_out_without_completion(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE, locked=True, seconds=1)] * 48)
        with patch("tasks.activity.detective_search.Ocr") as ocr:
            ocr.return_value.ocr_single_line.return_value = "unreadable"
            self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.actions)
        self.assertFalse(r.task.config.values)

    def test_detective_missing_popup_cannot_extend_deadline(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE)] + [capture(RECEIVED, seconds=1)] * 48)
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.task.config.values)

    def test_detective_wrong_tab_is_rejected(self):
        r = self.replay(DetectiveSearch, [capture(HOME)])
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.actions)

    def test_detective_network_dialog_does_not_confirm_pending_reward(self):
        from tests.support.interaction import scene
        network = scene("NETWORK_ERROR_ABNORMAL", "TOUCH_TO_CLOSE")
        r = self.replay(DetectiveSearch, [capture(AVAILABLE), network] + [capture(RECEIVED)] * 4
                        + [capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [1, 6])
        self.assertEqual(r.index, 7)
