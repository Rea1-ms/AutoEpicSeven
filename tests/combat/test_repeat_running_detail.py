# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline checks for the shared active-run and settlement detail window."""
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE

import module.config.server as server
from module.base.utils import load_image
from tasks.dungeon.assets.assets_dungeon_repeat_settlement import (
    SETTLEMENT_CLOSE,
    SETTLEMENT_INTERRUPT,
    SETTLEMENT_PROCESSING,
    SETTLEMENT_SETTLE,
    SETTLEMENT_WINDOW_CHECK,
)
from tasks.dungeon.execute import CombatExecuteMixin
from tasks.dungeon.repeat import CombatRepeatMixin
from tasks.dungeon.runtime import CombatRuntimeMixin


class Detection(CombatRepeatMixin, CombatExecuteMixin, CombatRuntimeMixin):
    COMBAT_CHECK_SIMILARITY = 0.8

    def __init__(self, *assets):
        self.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        for asset in assets:
            self.device.image = np.maximum(self.device.image, load_image(asset.buttons[0].file))

    def match_template_luma(self, button, similarity):
        return button.match_template_luma(self.device.image, similarity=similarity)


class RunningDetailTests(unittest.TestCase):
    def test_interrupt_is_running_in_both_supported_clients(self):
        for lang in ("cn", "global_cn"):
            with self.subTest(lang=lang):
                server.set_lang(lang)
                detector = Detection(SETTLEMENT_WINDOW_CHECK, SETTLEMENT_INTERRUPT)
                self.assertTrue(detector._is_repeat_combat_running())
                self.assertEqual(detector._detect_background_repeat_combat_state(), "running")

    def test_result_controls_are_not_running(self):
        for lang in ("cn", "global_cn"):
            server.set_lang(lang)
            for control in (SETTLEMENT_SETTLE, SETTLEMENT_CLOSE, SETTLEMENT_PROCESSING):
                with self.subTest(lang=lang, control=control.name):
                    detector = Detection(SETTLEMENT_WINDOW_CHECK, control)
                    self.assertFalse(detector._is_repeat_combat_running())
                    self.assertEqual(detector._detect_background_repeat_combat_state(), "result")

    def test_interrupt_text_requires_the_known_window(self):
        for lang in ("cn", "global_cn"):
            with self.subTest(lang=lang):
                server.set_lang(lang)
                detector = Detection(SETTLEMENT_INTERRUPT)
                self.assertFalse(detector._is_repeat_combat_running_window())
                self.assertIsNone(detector._detect_background_repeat_combat_state())

    def test_unsupported_client_does_not_use_interrupt_state(self):
        server.set_lang("global_en")
        detector = Detection(SETTLEMENT_WINDOW_CHECK, SETTLEMENT_INTERRUPT)
        self.assertFalse(detector._is_repeat_combat_running_window())
