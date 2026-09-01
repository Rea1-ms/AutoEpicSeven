import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from module.base.timer import Timer
from module.base.utils import crop, save_image
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.gacha.assets.assets_gacha import (
    GACHA_10_1,
    GACHA_10_2,
    GACHA_10_3,
    GACHA_10_4,
    GACHA_10_5,
    GACHA_10_6,
    GACHA_10_7,
    GACHA_10_8,
    GACHA_10_9,
    GACHA_10_10,
    OCR_CHARACTER_NAME,
    OCR_REMAINING_GACHA_TIMES,
)


TEN_PULL_CARD_ASSETS = (
    GACHA_10_1,
    GACHA_10_2,
    GACHA_10_3,
    GACHA_10_4,
    GACHA_10_5,
    GACHA_10_6,
    GACHA_10_7,
    GACHA_10_8,
    GACHA_10_9,
    GACHA_10_10,
)
TEN_PULL_CARD_AREAS = tuple(asset.area for asset in TEN_PULL_CARD_ASSETS)


class TenPullResultCollector:
    """Wait for auto-transmit, then keep only the final retained results.

    Glowing cards are being transmitted and never enter inventory, so they must
    not be reconstructed from earlier frames. Two consecutive clean frames
    confirm that the animation has ended. On timeout, the latest whole frame is
    saved and only its non-overexposed slots are eligible for OCR. The collector
    never clicks or sleeps; the caller keeps taking state-loop screenshots.
    """

    CLEAN_OVEREXPOSURE_RATIO = 0.3
    CLEAN_CONFIRM_FRAMES = 2
    MAX_WAIT_SECONDS = 5
    MAX_WAIT_FRAMES = 5

    def __init__(self) -> None:
        self.timeout = Timer(
            self.MAX_WAIT_SECONDS,
            count=self.MAX_WAIT_FRAMES,
        ).start()
        self.image = None
        self.card_images = [None] * len(TEN_PULL_CARD_AREAS)
        self.card_scores = [float("inf")] * len(TEN_PULL_CARD_AREAS)
        self.clean_frames = 0
        self.ready = False
        self.saved = False

    @staticmethod
    def _overexposure_ratio(image) -> float:
        return float(np.mean(np.max(image, axis=2) > 245))

    def observe(self, image) -> bool:
        if self.ready:
            return True

        card_images = [crop(image, area, copy=True) for area in TEN_PULL_CARD_AREAS]
        scores = [self._overexposure_ratio(card) for card in card_images]
        clean = [score <= self.CLEAN_OVEREXPOSURE_RATIO for score in scores]
        self.image = image.copy()
        self.card_images = [
            card_image if is_clean else None
            for card_image, is_clean in zip(card_images, clean)
        ]
        self.card_scores = scores

        logger.attr(
            "TenPullOverexposure",
            [round(score, 3) for score in scores],
        )
        if all(clean):
            self.clean_frames += 1
        else:
            self.clean_frames = 0

        if self.clean_frames >= self.CLEAN_CONFIRM_FRAMES:
            self.ready = True
            logger.info("Ten-pull auto-transmit animation finished")
        elif self.timeout.reached():
            self.ready = True
            logger.warning(
                "Ten-pull result animation did not fully clear; "
                "OCR only non-overexposed slots from the latest frame "
                f"{[round(score, 3) for score in self.card_scores]}"
            )

        return self.ready

    def save_once(self, recorder) -> bool:
        if self.saved:
            return False
        if not self.ready or self.image is None:
            return False
        recorder._save_ten_pull_result(
            image=self.image,
            card_images=self.card_images,
        )
        self.saved = True
        return True


class SummonResultCaptureGate:
    """Keep exactly one screenshot for each visible summon result.

    A continue or next-page tap only proves that an input was sent; it does not
    prove that the game accepted it. The saved flag therefore survives retries.
    It is cleared after a positive next-result state, or after two consecutive
    animation frames where the previous result controls are gone. Requiring two
    frames prevents one damaged screenshot from creating duplicate records.
    """

    TRANSITION_CONFIRM_FRAMES = 2

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.saved = False
        self.advance_requested = False
        self.transition_frames = 0

    def save_once(self, recorder, tag: str) -> bool:
        if self.saved:
            return False
        recorder._save_result(tag=tag)
        self.saved = True
        return True

    def mark_advance_requested(self) -> None:
        self.advance_requested = True
        self.transition_frames = 0

    def observe_transition(
        self,
        *,
        next_result_visible: bool,
        previous_result_visible: bool,
    ) -> bool:
        if not self.advance_requested:
            return False
        if next_result_visible:
            self.reset()
            return True
        if previous_result_visible:
            self.transition_frames = 0
            return False

        self.transition_frames += 1
        if self.transition_frames >= self.TRANSITION_CONFIRM_FRAMES:
            self.reset()
            return True
        return False


class SummonResultRecorder:
    """Save summon result screenshots and their OCR metadata."""

    RESULT_LOG_ROOT = Path("log/gacha")

    def _ocr_lang(self) -> str:
        lang = getattr(self.config, "Emulator_GameLanguage", "cn")
        if lang in ("auto", "", None, "cn", "global_cn", "zh", "zh_cn"):
            return "cn"
        if lang in ("en", "global_en", "en_us"):
            return "en"
        if lang in ("jp", "ja", "ja_jp"):
            return "jp"
        if lang in ("tw", "zh_tw"):
            return "tw"
        return "cn"

    def _read_result_name(self, image) -> str:
        raw_name = Ocr(
            OCR_CHARACTER_NAME,
            lang=self._ocr_lang(),
            name="SummonResultName",
        ).ocr_single_line(image)
        result_name = re.sub(r"\s+", " ", raw_name or "").strip()
        logger.info(f"Summon result name: {result_name or '<OCR empty>'}")
        return result_name

    @staticmethod
    def _select_ten_pull_name(raw_texts: list[str]) -> str:
        candidates = []
        for raw_text in raw_texts:
            text = re.sub(r"\s+", " ", raw_text or "").strip()
            text = text.strip("★☆✦✧·•.,，。!！ ")
            if not text or not any(character.isalpha() for character in text):
                continue
            if text.upper() in {"NEW", "NEV"}:
                continue
            candidates.append(text)
        return candidates[-1] if candidates else ""

    @staticmethod
    def _is_auto_transmitted(raw_texts: list[str]) -> bool:
        return any(
            re.search(r"\+\s*[\d,]+", raw_text or "")
            for raw_text in raw_texts
        )

    def _read_ten_pull_names(self, card_images) -> list[str]:
        names = []
        for index, card_image in enumerate(card_images, start=1):
            if card_image is None:
                logger.info(f"Ten-pull result {index}: auto-transmitted, skip OCR")
                continue
            results = Ocr(
                OCR_CHARACTER_NAME,
                lang=self._ocr_lang(),
                name=f"TenPullResultName.{index}",
            ).detect_and_ocr(card_image, direct_ocr=True)
            raw_texts = [result.ocr_text for result in results]
            if self._is_auto_transmitted(raw_texts):
                logger.info(
                    f"Ten-pull result {index}: auto-transmitted by summon settings"
                )
                continue
            name = self._select_ten_pull_name(raw_texts)
            logger.info(f"Ten-pull result {index}: {name or '<OCR empty>'}")
            if name:
                names.append(name)
        logger.attr("TenPullResultNames", names)
        return names

    def _read_next_free_summon_count(self, image) -> int | None:
        raw_text = Ocr(
            OCR_REMAINING_GACHA_TIMES,
            lang=self._ocr_lang(),
            name="RemainingGachaTimes",
        ).ocr_single_line(image)
        logger.attr("RemainingGachaTimesRaw", raw_text)

        matched = re.search(r"(\d+)\s*/\s*(\d+)", raw_text or "")
        if matched:
            remaining, total = (int(value) for value in matched.groups())
            if total > 0 and 0 < remaining <= total:
                next_count = 10 if remaining >= 10 else 1
                logger.attr("RemainingGachaTimes", f"{remaining}/{total}")
                logger.attr("NextSummonCount", next_count)
                return next_count

        logger.warning("Unable to read remaining free summons from result page")
        return None

    def _write_result_record(
        self,
        *,
        image,
        tag: str,
        metadata: dict,
    ) -> None:
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        ts = now.strftime("%Y%m%d_%H%M%S_%f")
        folder = self.RESULT_LOG_ROOT / day
        folder.mkdir(parents=True, exist_ok=True)

        image_path = folder / f"{ts}_{tag}.png"
        save_image(image, str(image_path))

        record = {
            "ts": ts,
            "tag": tag,
            "count": self._draw_count,
            "free": self._draw_free,
            "image": str(image_path),
        }
        record.update(metadata)
        with open(folder / "draws.jsonl", "a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info(f"Summon result saved: {image_path}")

    def _save_result(self, tag: str = "result") -> None:
        image = self.device.image
        self._write_result_record(
            image=image,
            tag=tag,
            metadata={"result_name": self._read_result_name(image)},
        )

    def _save_ten_pull_result(self, image, card_images) -> None:
        result_names = self._read_ten_pull_names(card_images)
        self._write_result_record(
            image=image,
            tag="result",
            metadata={
                "result_names": result_names,
                "retained_count": len(result_names),
            },
        )
