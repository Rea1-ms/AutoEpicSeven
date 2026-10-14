import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.heroes import HeroResult, read_hero_result  # noqa: E402
from tasks.brawl.rules import Imprint  # noqa: E402
from tasks.brawl.vision import BrawlScreen, BrawlVision  # noqa: E402
from tests.support.brawl import frame  # noqa: E402


class HeroRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_result_names_and_imprint_transitions(self):
        expected = {
            "hero-new-01": ("萝季", Imprint.B, Imprint.C),
            "hero-new-02": ("梅铃", Imprint.SS, Imprint.S),
            "hero-new-07": ("莱娅", Imprint.SSS, Imprint.SS),
            "hero-new-12": ("最后的骑士克劳乌", Imprint.SSS, Imprint.SS),
            "hero-new-15": ("指挥型莱伊卡", Imprint.SS, Imprint.S),
            "hero-new-16": ("梅铃", Imprint.SSS, Imprint.SS),
            "hero-new-19": ("蕾诺娅", Imprint.A, Imprint.B),
            "hero-new-27": ("卡琳", Imprint.B, Imprint.C),
        }
        for name, values in expected.items():
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertEqual(read_hero_result(self.frame), HeroResult(*values))

    def test_single_and_wrapped_names_preserve_explicit_no_imprint(self):
        expected = {
            "hero-new-05": ("最后的碎片卡琳", Imprint.C, None),
            "hero-new-09": ("辅助型拉兹", Imprint.C, None),
            "hero-new-10": ("赛娜", Imprint.B, None),
            "hero-new-14": ("露彬", Imprint.NONE, None),
            "hero-new-28": ("未知可能性雅卡泰丝", Imprint.C, None),
            "hero-start-03": ("卡萝", Imprint.D, None),
            "hero-start-10": ("萝季", Imprint.C, None),
        }
        for name, values in expected.items():
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertEqual(read_hero_result(self.frame), HeroResult(*values))

    def test_overflow_keeps_retained_hero_at_maximum_imprint(self):
        self.frame = frame("hero-new-08")
        self.assertEqual(read_hero_result(self.frame), HeroResult("小春", Imprint.SSS, Imprint.SSS))

    def test_transmit_gift_result_and_later_preparation_keep_their_evidence_separate(self):
        self.frame = frame("transmit-gift-result")
        self.assertEqual(BrawlVision(self.frame).observe().state, BrawlScreen.HERO_RESULT)
        self.assertEqual(read_hero_result(self.frame), HeroResult("黎洁特", Imprint.S, Imprint.A))
        original = self.frame.copy()
        # Remove only the glyph using an adjacent strip of the same purple
        # background. Keeping the surrounding artwork guards against a new
        # badge template matching the background instead of the letter.
        self.frame[386:418, 775:802] = original[386:418, 811:838]
        self.assertIsNone(read_hero_result(self.frame))
        for area in ((895, 392, 907, 412), (982, 386, 1009, 418)):
            self.frame = original.copy()
            x1, y1, x2, y2 = area
            self.frame[y1:y2, x1:x2] = 0
            with self.subTest(missing=area):
                self.assertIsNone(read_hero_result(self.frame))
        # The same hero's later S -> SS transition must not become A -> S
        # merely because it has the same purple artwork behind the badges.
        self.frame = frame("upper-summon-result")
        self.assertEqual(read_hero_result(self.frame), HeroResult("黎洁特", Imprint.SS, Imprint.S))
        # The user identified this capture as a transfer gift. The matching later
        # balance supports the return scene, but the unseen transfer sequence
        # supplies no evidence for a purchase, transfer profit, or gift count.
        self.frame = frame("synergy-summon-before")
        returned = BrawlVision(self.frame).observe()
        self.assertEqual(returned.state, BrawlScreen.PREPARATION)
        self.assertEqual((returned.panel.coins, returned.panel.synergy_tokens), (90, 54))
        self.assertIsNone(read_hero_result(self.frame))

    def test_incomplete_badge_or_arrow_never_reports_old_imprint_as_new(self):
        original = frame("hero-new-12")
        for area in ((947, 382, 1023, 422), (891, 388, 912, 416), (771, 382, 845, 422)):
            self.frame = original.copy()
            x1, y1, x2, y2 = area
            self.frame[y1:y2, x1:x2] = 0
            with self.subTest(area=area):
                self.assertIsNone(read_hero_result(self.frame))

    def test_unknown_low_confidence_names_and_unrelated_frames_remain_unknown(self):
        self.frame = frame("hero-new-05")
        for text, score in (("未知英雄", 0.99), ("最后的碎片卡琳", 0.79),
                            ("最后的碎片卡琳", float("nan"))):
            with self.subTest(text=text, score=score), patch(
                    "tasks.brawl.heroes.Ocr.detect_and_ocr",
                    return_value=[SimpleNamespace(ocr_text=text, score=score, box=(0, 0, 10, 10))]):
                self.assertIsNone(read_hero_result(self.frame))
        for name in ("prepare-level4", "round-clear", "lobby"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertIsNone(read_hero_result(self.frame))
        self.assertIsNone(read_hero_result(np.zeros((720, 1280, 3), dtype=np.uint8)))

    def test_model_failures_propagate_and_unsupported_inputs_fail(self):
        self.frame = frame("hero-new-05")
        with patch("tasks.brawl.heroes.Ocr.detect_and_ocr", side_effect=ValueError("model unavailable")), \
                self.assertRaisesRegex(ValueError, "model unavailable"):
            read_hero_result(self.frame)
        for invalid in (None, np.zeros((360, 640, 3), dtype=np.uint8)):
            with self.assertRaises(ValueError):
                read_hero_result(invalid)
        try:
            server.set_lang("cn")
            with self.assertRaises(ValueError):
                read_hero_result(self.frame)
        finally:
            server.set_lang("global_cn")
