# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline tests for single and ten-pull summon result recording.

Usage:
    python test/test_gacha_result.py
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

from tasks.gacha.assets.assets_gacha import SUMMON_RESULT_BACK  # noqa: E402
from tasks.gacha.gacha import Gacha  # noqa: E402
from tasks.gacha.result import (  # noqa: E402
    TEN_PULL_CARD_AREAS,
    SummonResultCaptureGate,
    SummonResultRecorder,
    TenPullResultCollector,
)


class _Recorder(SummonResultRecorder):
    def __init__(self, log_root: Path):
        self.RESULT_LOG_ROOT = log_root
        self.config = SimpleNamespace(
            Emulator_GameLanguage="global_cn",
            GachaResult_SaveScreenshot=True,
            GachaResult_OcrResult=True,
        )
        self.device = SimpleNamespace(image=object())
        self._draw_count = 1
        self._draw_free = True


class GachaResultTest(unittest.TestCase):
    def test_result_is_saved_before_back_click(self):
        events = []
        state = SimpleNamespace(value="result")

        class _Device:
            image = object()

            @staticmethod
            def screenshot():
                events.append("screenshot")

            @staticmethod
            def screenshot_interval_set(*_args):
                pass

            @staticmethod
            def click(button):
                events.append(f"click:{button.name}")
                state.value = "gacha"

        gacha = object.__new__(Gacha)
        gacha.device = _Device()
        gacha._draw_count = 1
        gacha._save_result = lambda tag: events.append(f"save:{tag}")
        gacha.appear = lambda button: (
            state.value == "result" and button is SUMMON_RESULT_BACK
        )
        gacha.interval_is_reached = lambda *_args, **_kwargs: True
        gacha.interval_reset = lambda *_args, **_kwargs: None
        gacha.ui_page_appear = lambda _page: state.value == "gacha"
        gacha.ui_additional = lambda: False
        gacha.handle_network_error = lambda: False

        gacha._handle_summon_flow()

        self.assertLess(
            events.index("save:result"),
            events.index("click:SUMMON_RESULT_BACK"),
        )

    def test_disabled_ten_pull_recording_skips_animation_collector(self):
        state = SimpleNamespace(value="result")

        class _Device:
            image = object()

            @staticmethod
            def screenshot():
                pass

            @staticmethod
            def screenshot_interval_set(*_args):
                pass

            @staticmethod
            def click(_button):
                state.value = "gacha"

        gacha = object.__new__(Gacha)
        gacha.config = SimpleNamespace(
            GachaResult_SaveScreenshot=False,
            GachaResult_OcrResult=False,
        )
        gacha.device = _Device()
        gacha._draw_count = 10
        gacha.appear = lambda button: (
            state.value == "result" and button is SUMMON_RESULT_BACK
        )
        gacha.interval_is_reached = lambda *_args, **_kwargs: True
        gacha.interval_reset = lambda *_args, **_kwargs: None
        gacha.ui_page_appear = lambda _page: state.value == "gacha"
        gacha.ui_additional = lambda: False
        gacha.handle_network_error = lambda: False

        with patch("tasks.gacha.gacha.TenPullResultCollector") as collector:
            gacha._handle_summon_flow()

        collector.assert_not_called()

    def test_capture_gate_keeps_one_record_while_click_retries(self):
        recorder = SimpleNamespace(_save_result=lambda tag: saved.append(tag))
        saved = []
        capture = SummonResultCaptureGate()

        self.assertTrue(capture.save_once(recorder, tag="result"))
        capture.mark_advance_requested()
        self.assertFalse(capture.save_once(recorder, tag="result"))
        capture.observe_transition(
            next_result_visible=False,
            previous_result_visible=True,
        )
        self.assertFalse(capture.save_once(recorder, tag="result"))
        self.assertEqual(saved, ["result"])

    def test_capture_gate_resets_after_confirmed_transition(self):
        recorder = SimpleNamespace(_save_result=lambda tag: saved.append(tag))
        saved = []
        capture = SummonResultCaptureGate()

        capture.save_once(recorder, tag="result")
        capture.mark_advance_requested()
        for _ in range(capture.TRANSITION_CONFIRM_FRAMES):
            capture.observe_transition(
                next_result_visible=False,
                previous_result_visible=False,
            )

        self.assertTrue(capture.save_once(recorder, tag="new"))
        self.assertEqual(saved, ["result", "new"])

    def test_result_record_contains_ocr_name(self):
        with retained_directory("gacha-record") as temp_dir:
            recorder = _Recorder(Path(temp_dir))
            with (
                patch("tasks.gacha.result.Ocr") as ocr,
                patch("tasks.gacha.result.save_image") as save_image,
            ):
                ocr.return_value.ocr_single_line.return_value = "  培妮拉★★★  "
                recorder._save_result(tag="result")

            records = list(Path(temp_dir).glob("*/draws.jsonl"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(record["result_name"], "培妮拉★★★")
            self.assertEqual(record["count"], 1)
            self.assertTrue(record["free"])
            save_image.assert_called_once()

    def test_result_ocr_without_screenshot(self):
        with retained_directory("gacha-record") as temp_dir:
            recorder = _Recorder(Path(temp_dir))
            recorder.config.GachaResult_SaveScreenshot = False
            with (
                patch("tasks.gacha.result.Ocr") as ocr,
                patch("tasks.gacha.result.save_image") as save_image,
            ):
                ocr.return_value.ocr_single_line.return_value = " 培妮拉 "
                recorder._save_result()

            record_path = next(Path(temp_dir).glob("*/draws.jsonl"))
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["result_name"], "培妮拉")
            self.assertNotIn("image", record)
            save_image.assert_not_called()

    def test_result_screenshot_without_ocr(self):
        with retained_directory("gacha-record") as temp_dir:
            recorder = _Recorder(Path(temp_dir))
            recorder.config.GachaResult_OcrResult = False
            with (
                patch.object(recorder, "_read_result_name") as read_name,
                patch("tasks.gacha.result.save_image") as save_image,
            ):
                recorder._save_result()

            record_path = next(Path(temp_dir).glob("*/draws.jsonl"))
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertIn("image", record)
            self.assertNotIn("result_name", record)
            read_name.assert_not_called()
            save_image.assert_called_once()

    def test_result_recording_can_be_disabled(self):
        with retained_directory("gacha-record") as temp_dir:
            recorder = _Recorder(Path(temp_dir))
            recorder.config.GachaResult_SaveScreenshot = False
            recorder.config.GachaResult_OcrResult = False
            with (
                patch.object(recorder, "_read_result_name") as read_name,
                patch("tasks.gacha.result.save_image") as save_image,
            ):
                recorder._save_result()

            self.assertEqual(list(Path(temp_dir).iterdir()), [])
            read_name.assert_not_called()
            save_image.assert_not_called()

    def test_ten_pull_collector_waits_for_clean_cards(self):
        collector = TenPullResultCollector()
        glow = np.full((720, 1280, 3), 255, dtype=np.uint8)
        clean = np.zeros((720, 1280, 3), dtype=np.uint8)
        recorder = SimpleNamespace(
            _save_ten_pull_result=lambda **kwargs: saved.append(kwargs)
        )
        saved = []

        self.assertFalse(collector.observe(glow))
        self.assertFalse(collector.observe(clean))
        self.assertTrue(collector.observe(clean))
        self.assertTrue(collector.save_once(recorder))
        self.assertFalse(collector.save_once(recorder))
        self.assertEqual(len(saved), 1)
        self.assertTrue(np.array_equal(saved[0]["image"], clean))
        self.assertEqual(len(saved[0]["card_images"]), 10)

    def test_ten_pull_timeout_skips_glowing_transmitted_slots(self):
        collector = TenPullResultCollector()
        collector.timeout = SimpleNamespace(reached=lambda: True)
        frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        x1, y1, x2, y2 = TEN_PULL_CARD_AREAS[0]
        frame[y1:y2, x1:x2] = 0

        self.assertTrue(collector.observe(frame))
        self.assertIsNotNone(collector.card_images[0])
        self.assertTrue(all(card is None for card in collector.card_images[1:]))

    def test_ten_pull_name_uses_last_text_candidate(self):
        self.assertEqual(
            SummonResultRecorder._select_ten_pull_name(
                ["NEV", "★★★★★", " 神圣牺牲· "]
            ),
            "神圣牺牲",
        )
        self.assertEqual(
            SummonResultRecorder._select_ten_pull_name(["★★★", "183", "哈坦"]),
            "哈坦",
        )



from tests.support.offline import retained_directory
