"""Small image comparisons and strict currency OCR for secret-shop transactions."""
import re
from dataclasses import dataclass

import cv2
import numpy as np

from module.ocr.ocr import OcrWhiteLetterOnComplexBackground


class ShopCurrencyOcr(OcrWhiteLetterOnComplexBackground):
    def pre_process(self, image):
        image = super().pre_process(image)
        # The recognizer compresses long amounts to its input width. A narrow
        # trailing repeated digit was consistently lost in both supplied views;
        # widening the glyphs preserves it without inventing digits afterwards.
        return cv2.resize(image, None, fx=1.5, fy=1, interpolation=cv2.INTER_CUBIC)

    @staticmethod
    def parse_amount(text):
        text = text.strip().replace(' ', '').replace('，', ',')
        if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)', text):
            return None
        return int(text.replace(',', ''))


@dataclass(frozen=True)
class ShopGoods:
    # Only item icons: portraits, price labels, quantities, the resource bar
    # and the merchant's animation must never count as a changed assortment.
    rows: tuple[np.ndarray, ...]

    @classmethod
    def capture(cls, image):
        if image.shape[:2] != (720, 1280):
            raise ValueError('Secret shop expects a 1280x720 screenshot')
        return cls(tuple(cv2.cvtColor(image[y:y + 84, 562:642], cv2.COLOR_RGB2GRAY).copy()
                         for y in (98, 243, 388, 533)))

    def matches(self, other, similarity=0.90):
        if other is None:
            return False
        # A little positional slack handles the list settling. Normalized
        # correlation tolerates a sold icon becoming dim without mistaking its
        # brightness for a new item. Confirmed refreshes require a stable new
        # snapshot in the task loop before any difference can count.
        return all(float(cv2.matchTemplate(
            cv2.copyMakeBorder(current, 3, 3, 3, 3, cv2.BORDER_REPLICATE),
            previous, cv2.TM_CCOEFF_NORMED,
        ).max()) >= similarity for previous, current in zip(self.rows, other.rows))


def same_currency_image(before, after):
    if before is None or after is None or before.shape != after.shape:
        return False

    def letters(image):
        low = image.min(axis=2)
        spread = image.max(axis=2).astype(np.int16) - low
        return ((low > 160) & (spread < 40)).astype(np.float32)

    # The translucent resource bar changes with the background even when its
    # amount is unchanged. Compare only white glyphs with two pixels of slack;
    # whole-crop averages both react to that background and dilute a changed
    # final digit. These masks are for observation caching, never for OCR input.
    previous, current = letters(before), letters(after)
    total = float(previous.sum() + current.sum())
    if total == 0:
        return True
    overlap = float(cv2.matchTemplate(
        cv2.copyMakeBorder(current, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=0),
        previous, cv2.TM_CCORR,
    ).max())
    return 2 * overlap / total >= 0.95
