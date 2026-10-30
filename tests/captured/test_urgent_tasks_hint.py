# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")

"""Offline regressions for Urgent Tasks prepare hints, including incomplete OCR."""
import unittest
from pathlib import Path
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
from tests.support.history_fixtures import build_combat


def frame(at, hint=False, count=1, page="prepare", fast=True):
    return dict(at=at, hint=hint, count=count, page=page, fast=fast)


class HintScreenshotTests(unittest.TestCase):
    def test_all_supplied_hint_screenshots_are_detected_without_ocr(self):
        names = ('normal-prepare.png', 'normal-prepare-large.png', 'superior-prepare.png',
                 'superior-prepare-large.png', 'superior-fast-on.png', 'superior-fast-off.png',
                 'prepare-times-hint.png')
        for name in names:
            with self.subTest(name=name):
                combat = build_combat(name, 'Superior')
                combat.device.screenshot = lambda: None
                combat.device.screenshot()
                clicks = []
                combat.device.click = lambda button: clicks.append(button.name)
                combat.interval_is_reached = lambda *args, **kwargs: True
                combat.interval_reset = lambda *args, **kwargs: None
                with patch('tasks.dungeon.urgent_tasks.DigitCounter.ocr_single_line',
                           side_effect=AssertionError('Hint dismissal must not depend on numeric OCR')):
                    self.assertTrue(combat._is_urgent_tasks_times_hint())
                    self.assertFalse(combat._is_urgent_tasks_prepare_ready())
                    self.assertTrue(combat._dismiss_urgent_tasks_times_hint())
                self.assertEqual(clicks, ['OCR_URGENT_COMBAT_TIMES_REMAINING'])
                combat.interval_is_reached = lambda *args, **kwargs: False
                self.assertFalse(combat._dismiss_urgent_tasks_times_hint())
                self.assertFalse(combat._is_urgent_tasks_prepare_ready())
                self.assertEqual(len(clicks), 1)

    def test_unrelated_pages_do_not_report_ready_prepare(self):
        for name in ('superior-detail.png', 'battle-auto-on.png', 'battle-result.png'):
            with self.subTest(name=name):
                combat = build_combat(name, 'Superior')
                combat.device.screenshot = lambda: None
                combat.device.screenshot()
                self.assertFalse(combat._is_urgent_tasks_prepare_ready())
