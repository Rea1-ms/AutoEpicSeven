"""Small, bounded recognition caches scoped to one manually started run."""

from collections import OrderedDict
from hashlib import sha256

import cv2
import numpy as np

from module.base.button import match_template
from module.base.utils import color_similar, crop, extract_white_letters, get_color
from tasks.equipment_reroll.assets.assets_equipment_reroll import (
    EQUIPMENT_REROLL_COST_UNLOCKED, EQUIPMENT_REROLL_COST_ONE_LOCK, EQUIPMENT_REROLL_COST_TWO_LOCKS,
    OCR_EQUIPMENT_REROLL_POINTS,
    EQUIPMENT_REROLL_ROLL_FULL, EQUIPMENT_REROLL_ROLL_NORMAL,
    EQUIPMENT_REROLL_STAT_CRITICAL_DAMAGE, EQUIPMENT_REROLL_STAT_SPEED,
)
from tasks.equipment_reroll.rules import REFRESH_COSTS, STAT_LABELS


class RefreshCostTemplates:
    """Recognize the three fixed prices, including lock/price update delays."""

    def read(self, image, locked):
        templates = (EQUIPMENT_REROLL_COST_UNLOCKED, EQUIPMENT_REROLL_COST_ONE_LOCK,
                     EQUIPMENT_REROLL_COST_TWO_LOCKS)
        region = crop(image, templates[0].area)
        matches = [index for index, template in enumerate(templates)
                   if match_template(region, template.matched_button.image, similarity=0.95)]
        if len(matches) != 1 or matches[0] != sum(locked):
            raise ValueError("Refresh price template does not match the current lock count")
        return REFRESH_COSTS[matches[0]]


def point_signature(image):
    """Observe yellow balance glyphs without recognizing their numeric value.

    Relative yellow contrast removes dark backgrounds and uniform brightness
    changes. A changed candidate alone cannot prove payment: even an identical
    reroll must update these balance glyphs before another paid click is allowed.
    """
    region = crop(image, OCR_EQUIPMENT_REROLL_POINTS.area).astype("int16")
    yellow = np.minimum(region[:, :, 0], region[:, :, 1]) - region[:, :, 2]
    mask = (yellow > max(30, float(yellow.max()) * 0.6)).astype("uint8")
    if int(mask.sum()) < 10:
        raise ValueError("Point balance glyphs are not visible yet")
    return mask.tobytes()


class PointBalanceEstimate:
    """A per-invocation ledger, committed only after stable action confirmation.

    The user permits estimated balances between ten-refresh OCR audits. Glyph
    changes establish completion, not the exact deduction; audits must agree
    with the ledger. A pending click never spends this ledger by itself, and a
    lost/unchanged balance never authorizes retrying the paid action.
    """

    CHECK_EVERY = 10

    def __init__(self):
        self.points = None
        self.signature = b""
        self.pending_cost = None
        self.unchecked_refreshes = 0
        self.observed_signature = b""

    @staticmethod
    def same_glyphs(first, second):
        if not first or not second or len(first) != len(second):
            return False
        a, b = np.frombuffer(first, dtype="uint8"), np.frombuffer(second, dtype="uint8")
        union = np.count_nonzero(a | b)
        return bool(union and np.count_nonzero(a ^ b) / union <= 0.04)

    def observe(self, signature):
        if self.points is None:
            return None, signature
        if self.same_glyphs(self.signature, signature):
            self.observed_signature = b""
            return self.points, self.signature
        if self.pending_cost is None:
            raise ValueError("Point balance glyphs changed outside a pending refresh")
        # Canonicalize minor exposure differences in the NEW balance too.
        # Otherwise an already-paid result could fail two-frame stability
        # forever while one antialiased pixel changes with the background.
        if not self.same_glyphs(self.observed_signature, signature):
            self.observed_signature = signature
        return self.points - self.pending_cost, self.observed_signature

    def initialize(self, points, signature):
        self.points, self.signature = points, signature
        self.observed_signature = b""

    def confirm_refresh(self, points, signature):
        if self.pending_cost is None or points != self.points - self.pending_cost:
            raise ValueError("Estimated refresh payment does not match the pending price")
        self.points, self.signature = points, signature
        self.pending_cost = None
        self.observed_signature = b""
        self.unchecked_refreshes += 1

    def verify(self, points):
        if points != self.points:
            raise ValueError(f"Point balance audit mismatch: estimated {self.points}, observed {points}")
        self.unchecked_refreshes = 0


class GoldMarkerDetector:
    """Classify the four fixed candidate icons; unknown colors are not 'no gold'."""

    def __init__(self):
        # Loading the real crops also makes a missing asset fail explicitly.
        self.gold_color = cv2.mean(EQUIPMENT_REROLL_ROLL_FULL.matched_button.image)[:3]
        self.normal_color = cv2.mean(EQUIPMENT_REROLL_ROLL_NORMAL.matched_button.image)[:3]

    def rows(self, image, row_step, *, skip_rows=()):
        left, top, right, bottom = EQUIPMENT_REROLL_ROLL_NORMAL.area
        result = []
        for row in range(4):
            if row in skip_rows:
                result.append(False)
                continue
            offset = row * row_step
            area = (left, top + offset, right, bottom + offset)
            color = get_color(image, area)
            gold = color_similar(color, self.gold_color, threshold=10)
            # Grey arrows and the candidate's inherited lock icon have different
            # brightness. Both must stay distinct from the gold reference.
            normal = color_similar(color, self.normal_color, threshold=20)
            if gold == normal:
                raise ValueError(f"Candidate marker color could not be confirmed at row {row + 1}")
            result.append(gold)
        return tuple(result)


def candidate_signature(image, fields, gold, *, skip_rows=()):
    """Observe all glyphs without OCR, ignoring the animated dark background."""
    masks = []
    for index, field in enumerate(fields):
        if index // 2 in skip_rows:
            continue
        region = crop(image, field.area)
        if index % 2 == 0:
            # Four-character Chinese labels fit here; the decorative horizontal
            # rule farther right must not restart the text stability wait.
            region = region[:, :90]
        foreground = 255 - extract_white_letters(region, threshold=255)
        mask = (foreground > 190).astype("uint8")
        if int(mask.sum()) < (20 if index % 2 == 0 else 10):
            raise ValueError("Candidate substat text is not fully visible yet")
        masks.append(mask.tobytes())
    skipped = bytes(row in skip_rows for row in range(4))
    return sha256(bytes(gold) + skipped + b"".join(masks)).digest()


class RecognitionCache:
    def __init__(self, limit=64):
        self.limit = limit
        self.entries = OrderedDict()

    def get(self, key):
        if key not in self.entries:
            return None
        self.entries.move_to_end(key)
        return self.entries[key]

    def put(self, key, value):
        self.entries[key] = value
        self.entries.move_to_end(key)
        while len(self.entries) > self.limit:
            self.entries.popitem(last=False)


class StatNameTemplates:
    """Reuse verified source assets and labels confirmed by OCR in this run.

    Unknown or ambiguous glyphs still need Chinese OCR; an absent template is
    never evidence that a desired stat is absent. Fonts are separated by scale,
    bounding boxes must agree, and the complete label is compared so prefixes
    cannot turn critical damage into critical chance. Learned samples do not
    persist across runs, and template matches never teach new templates themselves.
    """

    SIMILARITY = 0.98
    MARGIN = 0.03

    def __init__(self):
        self.templates = {}
        # This source glyph was cropped from a real comparison screen and is
        # covered by full-stat and unrelated-label regression tests. Modal font
        # scale 3 remains separate; it must not reuse this comparison template.
        self.remember(EQUIPMENT_REROLL_STAT_CRITICAL_DAMAGE.matched_button.image, 2, "暴击伤害")
        # The comparison screen's grey locked speed label scores below 0.8
        # in Chinese OCR despite matching this independently verified real
        # candidate glyph. Match only the name; speed 4/5 still requires numeric
        # OCR and must never be inferred from this template or a lock icon.
        for source in EQUIPMENT_REROLL_STAT_SPEED.buttons:
            self.remember(source.image, 2, "速度")

    @staticmethod
    def feature(image):
        foreground = 255 - extract_white_letters(image, threshold=255)
        low, high = int(foreground.min()), int(foreground.max())
        if high - low < 20:
            return None
        mask = (foreground > low + (high - low) * 0.5).astype("uint8")
        x, y, width, height = cv2.boundingRect(mask)
        if width < 10 or height < 8:
            return None
        glyphs = foreground[y:y + height, x:x + width]
        return cv2.normalize(glyphs, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")

    def match(self, image, font):
        feature = self.feature(image)
        if feature is None:
            return None
        scores = []
        for (template_font, name), templates in self.templates.items():
            if template_font != font:
                continue
            score = 0
            for template in templates:
                if any(abs(a - b) > 2 for a, b in zip(feature.shape, template.shape)):
                    continue
                search = cv2.copyMakeBorder(feature, 2, 2, 2, 2, cv2.BORDER_CONSTANT)
                similarity = float(cv2.minMaxLoc(cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED))[1])
                score = max(score, similarity)
            scores.append((score, name))
        scores.sort(reverse=True)
        if not scores or scores[0][0] < self.SIMILARITY:
            return None
        if len(scores) > 1 and scores[0][0] - scores[1][0] < self.MARGIN:
            return None
        return scores[0][1], scores[0][0]

    def remember(self, image, font, name):
        if name not in STAT_LABELS.values():
            raise ValueError(f"Unrecognized substat name: {name}")
        feature = self.feature(image)
        if feature is None:
            return
        templates = self.templates.setdefault((font, name), [])
        if not any(np.array_equal(feature, template) for template in templates):
            templates.append(feature)
            if len(templates) > 4:
                templates.pop(0)
