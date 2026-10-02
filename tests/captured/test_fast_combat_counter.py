# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")

"""Offline regressions for malformed fast-combat counts and bounded adjustment."""
import unittest
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]

from tests.support.history_september_runtime_fixes import Frames
from tasks.dungeon.prepare import CombatPrepare


def parse_frame(ocr, image):
    # Substitute recognition only; exercise the actual counter postprocessor,
    # format parser and fast-combat range validation for every raw OCR value.
    return ocr.format_result(ocr.after_process(image["raw"]))


class CountPrepare(Frames, CombatPrepare):
    def __init__(self, frames):
        super().__init__(frames)
        self.actions = []
        self.device.multi_click = self.multi_click

    def _ocr_lang(self):
        return "cn"

    def _handle_dungeon_additional(self):
        return self.frame.get("popup", False)

    def appear(self, button):
        return self.frame.get("buttons", True)

    def multi_click(self, button, n, interval):
        self.actions.append((self.index, button.name, n))


def make_frames(*raws, **extra):
    return [{"raw": raw, **extra} for raw in raws]






class ScreenshotCounterTests(unittest.TestCase):
    def test_existing_fast_combat_screenshots(self):
        from tests.support.history_fixtures import build_combat
        for filename, expected in (("prepare-times-hint.png", 1),
                                   ("superior-fast-on.png", 3),
                                   ("superior-prepare-large.png", 3)):
            with self.subTest(filename=filename):
                combat = build_combat(filename, "Superior")
                self.assertEqual(combat._ocr_fast_combat_current_times(), expected)

    def test_full_count_asset_screenshot(self):
        from module.base.utils import load_image
        from tests.support.history_fixtures import build_combat
        combat = build_combat("superior-fast-on.png", "Superior")
        combat.device.image = load_image(WORKTREE / "assets/share/dungeon/fast_combat/OCR_FAST_COMBAT_CURRENT_TIMES.png")
        self.assertEqual(combat._ocr_fast_combat_current_times(), 10)
