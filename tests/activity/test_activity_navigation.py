"""Shared activity title, page-marker and selected-tab navigation checks."""

import unittest
from unittest.mock import patch

from module.config import server
from tests.support.offline import ROOT, fixture_image

server.set_lang("global_cn")

from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_CHECK
from tasks.activity.assets.assets_activity_completion_record import (
    COMPLETION_RECORD_CHECK,
)
from tasks.activity.assets.assets_activity_detective_search import (
    DETECTIVE_SEARCH_CHECK,
)
from tasks.activity.assets.assets_activity_epic_path import (
    EPIC_PATH_CHECK,
)
from tasks.activity.completion_record import CompletionRecord
from tasks.activity.detective_search import DetectiveSearch
from tasks.activity.epic_path import EpicPath
from tests.support.activity_replay import (
    ATTENDANCE,
    AVAILABLE,
    DAILY,
    HOME,
    HOME_AFTER,
    POPUP,
    RECEIVED,
    ActivityReplayCase,
    capture,
)

MANIFEST = ROOT / "tests/fixtures/historical/manifest.json"


class ActivityNavigationRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_activity_title_identifies_epic_path_without_clock(self):
        image = fixture_image(MANIFEST, "20261029-203759-503")
        self.assertTrue(COMMON_ACTIVITY_CHECK.match_template_color(image))

    def test_title_rejects_both_dialog_overlays(self):
        for name in (HOME, AVAILABLE, RECEIVED, HOME_AFTER, ATTENDANCE, POPUP, DAILY):
            with self.subTest(name=name):
                image = fixture_image(MANIFEST, name)
                self.assertEqual(COMMON_ACTIVITY_CHECK.match_template_color(image), name not in (POPUP, DAILY))

    def test_page_markers_are_mutually_exclusive_and_reject_overlays(self):
        for name in (HOME, AVAILABLE, RECEIVED, HOME_AFTER, ATTENDANCE, POPUP, DAILY):
            image = fixture_image(MANIFEST, name)
            for button, expected in ((EPIC_PATH_CHECK, name in (HOME, HOME_AFTER)),
                                     (DETECTIVE_SEARCH_CHECK, name in (AVAILABLE, RECEIVED)),
                                     (COMPLETION_RECORD_CHECK, name == ATTENDANCE)):
                with self.subTest(name=name, button=button.name):
                    self.assertEqual(button.match_template_color(image), expected)


class ActivityNavigationReplayTests(ActivityReplayCase):
    def test_fixed_page_markers_preserve_search_bounds_and_skip_sidebar_ocr(self):
        for task, name, marker in ((CompletionRecord, ATTENDANCE, COMPLETION_RECORD_CHECK),
                                   (DetectiveSearch, AVAILABLE, DETECTIVE_SEARCH_CHECK),
                                   (EpicPath, HOME, EPIC_PATH_CHECK)):
            r = self.replay(task, [capture(name)])
            before = [b.search for b in marker.iter_buttons()]
            with patch("tasks.activity.navigation.Ocr") as ocr:
                self.assertTrue(r.task.select_activity("unused", marker))
                ocr.return_value.detect_and_ocr.assert_not_called()
            self.assertEqual([b.search for b in marker.iter_buttons()], before)
            self.assertFalse(r.actions)
