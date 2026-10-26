"""Recognize static bench labels once per run, with current-frame glyph proof."""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar

import cv2
import numpy as np
from pponnxcr.predict_system import BoxedResult, perspective_crop, sorted_boxes

from module.base.utils import corner2area, extract_white_letters
from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl import BENCH_NAME_CHRISTY, OCR_BENCH

_ACTIVE_NAMES = ContextVar("brawl_bench_names", default=None)


def name_kind(area):
    x1, y1, _, y2 = area
    if 996 <= x1 <= 1010 and 247 <= y1 < y2 <= 284:
        return "selected"
    if 1043 <= x1 <= 1086 and 370 <= y1 < y2 and y2 + 35 <= 550:
        return "small"
    return None


class BenchNameCache:
    """Cache labels, never a selected hero, badge, row position or full roster.

    A current detector box and complete glyph comparison are required on every
    frame. The selected and rear-card fonts stay separate; a short name cannot
    match only the prefix of a longer one. Only an independently accepted OCR
    result teaches a template, so repeated matches cannot drift the reference.
    Samples live in one explicitly started run and the bounded store evicts old
    labels. Unknown, clipped or ambiguous names always return to OCR.
    """

    MAX_LABELS = 128
    MAX_VARIANTS = 4
    SIMILARITY = .98
    MARGIN = .03

    def __init__(self):
        self.templates = OrderedDict()

    @staticmethod
    def feature(image):
        foreground = 255 - extract_white_letters(image, threshold=255)
        low, high = int(foreground.min()), int(foreground.max())
        if high - low < 20:
            return None
        mask = (foreground > low + (high - low) * .5).astype("uint8")
        x, y, width, height = cv2.boundingRect(mask)
        if width < 10 or height < 8:
            return None
        glyphs = foreground[y:y + height, x:x + width]
        return cv2.normalize(glyphs, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")

    def match(self, image, kind):
        feature = self.feature(image)
        if feature is None or kind is None:
            return None
        scores = []
        for (font, name), variants in self.templates.items():
            if font != kind:
                continue
            score = 0.
            for template in variants:
                if any(abs(a - b) > 2 for a, b in zip(feature.shape, template.shape)):
                    continue
                search = cv2.copyMakeBorder(feature, 2, 2, 2, 2, cv2.BORDER_CONSTANT)
                value = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
                score = max(score, float(cv2.minMaxLoc(value)[1]))
            scores.append((score, name))
        scores.sort(reverse=True)
        if not scores or scores[0][0] < self.SIMILARITY:
            return None
        if len(scores) > 1 and scores[0][0] - scores[1][0] < self.MARGIN:
            return None
        return scores[0][1], scores[0][0]

    def remember(self, line, name, *, font=None):
        # Christy's detector box can omit part of its final glyph. Its existing
        # complete-name asset remains the independent proof on every frame;
        # never learn the truncated detector crop as a canonical full name.
        if getattr(line, "cached_name", False) or (font is None and name == "克莉丝缇"):
            return
        self.remember_region(getattr(line, "name_pixels", line.text_img),
                             font or name_kind(line.box), name)

    def remember_region(self, image, kind, name):
        feature = self.feature(image)
        if kind is None or feature is None:
            return
        key = kind, name
        variants = self.templates.setdefault(key, [])
        self.templates.move_to_end(key)
        if not any(np.array_equal(feature, prior) for prior in variants):
            variants.append(feature)
            if len(variants) > self.MAX_VARIANTS:
                variants.pop(0)
        while len(self.templates) > self.MAX_LABELS:
            self.templates.popitem(last=False)

    def read(self, image, button=OCR_BENCH, *, font=None, log_name="BRAWL_BENCH_NAMES"):
        """Detect this frame's boxes, then OCR only labels not already proved."""
        ocr = Ocr(button, lang="cn", name=log_name)
        left, top, right, bottom = button.area
        region = image[top:bottom, left:right]
        boxes, _ = ocr.model.text_detector(region, None, None)
        if boxes is None:
            return []
        lines, unknown = [], []
        for box in sorted_boxes(boxes):
            area = tuple(map(int, corner2area(box + (left, top))))
            kind = font or name_kind(area)
            # Only these anchors can produce actionable complete names. Text
            # on hidden neighboring cards and unrelated badges is not evidence
            # for read_bench, and need not consume the recognition model.
            if kind is None:
                continue
            crop = perspective_crop(region, box.copy())
            x1, y1, x2, y2 = area
            # The detector can tilt its quadrilateral when a nearby badge or
            # plus changes, even though every name pixel stays identical. Keep
            # the perspective crop for OCR, but compare original current-frame
            # pixels for learned glyphs. Rewarping the same label would create
            # an artificial font change and repeatedly invoke recognition.
            # The current box still bounds the entire label: no old coordinates
            # or a template's narrower rectangle are reused for this crop.
            name_pixels = image[y1:y2, x1:x2]
            if (font is None and kind == "selected"
                    and BENCH_NAME_CHRISTY.match_template_color(image, similarity=.95, threshold=20)):
                matched = ("克莉丝缇", 1.)
            else:
                matched = self.match(name_pixels, kind)
            line = BoxedResult(area, crop, "", 0.)
            line.name_pixels = name_pixels
            line.cached_name = matched is not None
            if matched is None:
                unknown.append(line)
            else:
                line.ocr_text, line.score = matched
            lines.append(line)
        if unknown:
            recognized = ocr.ocr_multi_lines([line.text_img for line in unknown])
            for line, (text, score) in zip(unknown, recognized):
                line.ocr_text, line.score = text, score
        return lines


@contextmanager
def bench_name_cache():
    """Give one manually started run its own static-label recognition memory."""
    cache = BenchNameCache()
    token = _ACTIVE_NAMES.set(cache)
    try:
        yield cache
    finally:
        _ACTIVE_NAMES.reset(token)


def read_name_lines(image, button=OCR_BENCH, *, font=None, log_name="BRAWL_BENCH_NAMES"):
    cache = _ACTIVE_NAMES.get()
    if cache is None:
        return Ocr(button, lang="cn", name=log_name).detect_and_ocr(image)
    return cache.read(image, button, font=font, log_name=log_name)


def remember_name(line, name, *, font=None):
    cache = _ACTIVE_NAMES.get()
    if cache is not None:
        cache.remember(line, name, font=font)


def match_name_region(image, font):
    """Match a complete fixed name field, with its own page/font namespace."""
    cache = _ACTIVE_NAMES.get()
    matched = None if cache is None else cache.match(image, font)
    return None if matched is None else matched[0]


def remember_name_region(image, font, name):
    """Remember only after the caller validated the full OCR text as a hero."""
    cache = _ACTIVE_NAMES.get()
    if cache is not None:
        cache.remember_region(image, font, name)
