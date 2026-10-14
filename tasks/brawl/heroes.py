"""Read the single hero on a supported summon result, never a complete roster."""

import re
from dataclasses import dataclass

import cv2
import numpy as np

from module.config import server
from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl import (
    HERO_OBTAIN_CHECK, HERO_OVERFLOW_CHECK, HERO_RESULT_CHECK, HERO_UNLOCK_CHECK,
    IMPRINT_A, IMPRINT_ARROW, IMPRINT_B, IMPRINT_C, IMPRINT_D, IMPRINT_NONE,
    IMPRINT_S, IMPRINT_SS, IMPRINT_SSS, OCR_HERO_NAME, RANK_UP_CHECK,
)
from tasks.brawl.rules import Imprint, RuleInputError, get_hero, validate_imprint


@dataclass(frozen=True)
class HeroResult:
    name: str
    imprint: Imprint
    previous_imprint: Imprint | None


_BADGES = (
    (Imprint.NONE, IMPRINT_NONE), (Imprint.D, IMPRINT_D), (Imprint.C, IMPRINT_C),
    (Imprint.B, IMPRINT_B), (Imprint.A, IMPRINT_A),
    (Imprint.S, IMPRINT_S), (Imprint.SS, IMPRINT_SS), (Imprint.SSS, IMPRINT_SSS),
)
_LEFT_BADGE = (771, 382, 845, 422)
_RIGHT_BADGE = (947, 382, 1023, 422)
_ARROW_AREA = (891, 388, 912, 416)


def _crop(image, area):
    left, top, right, bottom = area
    return image[top:bottom, left:right]


def _badge_mask(image, imprint):
    red, green, blue = image.astype(np.int16).transpose(2, 0, 1)
    if imprint == Imprint.C:
        mask = (green > 110) & (green - red > 50) & (green - blue > 60)
    elif imprint == Imprint.B:
        mask = (blue > 120) & (blue - red > 70) & (blue - green > 10)
    elif imprint == Imprint.A:
        mask = (blue > 120) & (red - green > 30) & (blue - green > 60)
    elif imprint >= Imprint.S:
        mask = (red > 140) & (green < 65) & (blue < 65)
    else:
        mask = (red > 50) & (red < 150) & (abs(red - green) < 10) & (abs(red - blue) < 10)
    return mask.astype(np.uint8) * 255


def _read_badge(region):
    matches = []
    for imprint, wrapper in _BADGES:
        observed = _badge_mask(region, imprint)
        for asset in wrapper.buttons:
            template = _badge_mask(asset.image, imprint)
            if not np.any(observed) or not np.any(template):
                continue
            _, score, _, (x, y) = cv2.minMaxLoc(
                cv2.matchTemplate(observed, template, cv2.TM_CCOEFF_NORMED))
            if score < 0.9:
                continue
            if imprint >= Imprint.S:
                # SS can resemble a substring of SSS. The entire red badge
                # must fit this match, so a shorter glyph cannot win when the
                # last letter is still present outside its template rectangle.
                height, width = template.shape
                covered = np.count_nonzero(observed[y:y + height, x:x + width])
                if covered / np.count_nonzero(observed) < 0.95:
                    continue
            matches.append(imprint)
    unique = set(matches)
    return unique.pop() if len(unique) == 1 else None


def _has_arrow(image):
    def chroma(region):
        rgb = region.astype(np.float32)
        return rgb.max(axis=2) - rgb.min(axis=2)

    # The same orange arrow overlays purple, yellow and brown hero artwork.
    # RGB/luminance correlation changes with that background; chroma preserves
    # its shape without guessing a transition from a missing right-hand badge.
    region = chroma(_crop(image, _ARROW_AREA))
    return any(cv2.minMaxLoc(cv2.matchTemplate(
        region, chroma(asset.image), cv2.TM_CCOEFF_NORMED))[1] >= 0.9
        for asset in IMPRINT_ARROW.buttons)


def _read_name(image):
    lines = Ocr(OCR_HERO_NAME, lang="cn", name="BRAWL_HERO_NAME").detect_and_ocr(image)
    if not lines or any(not np.isfinite(line.score) or line.score < 0.8 for line in lines):
        return None
    text = "".join(line.ocr_text for line in sorted(lines, key=lambda line: (line.box[1], line.box[0])))
    try:
        return get_hero(re.sub(r"\s+", "", text)).name
    except RuleInputError:
        return None


def read_hero_result(image):
    """Return a verified result or None for incomplete/unsupported observations.

    ``previous_imprint=None`` means no upgrade pair is displayed. It does not
    imply that the hero was unowned before the run. A full-imprint refund leaves
    the retained hero at SSS; its currency icons must never be read as a badge.
    This function carries no roster or acquisition counter between frames.
    """
    if server.lang != "global_cn":
        raise ValueError("Brawl hero recognition currently supports international Chinese only")
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3) or image.dtype != np.uint8:
        raise ValueError("Brawl requires a 1280x720 RGB uint8 screenshot")
    if any(wrapper.match_template(image, similarity=0.9)
           for wrapper in (RANK_UP_CHECK, HERO_UNLOCK_CHECK)):
        return None
    if not (HERO_OBTAIN_CHECK.match_template(image, similarity=0.9)
            or HERO_RESULT_CHECK.match_template(image, similarity=0.9)):
        return None
    name = _read_name(image)
    if name is None:
        return None
    if HERO_OVERFLOW_CHECK.match_template(image, similarity=0.9):
        return HeroResult(name, Imprint.SSS, Imprint.SSS)
    left = _read_badge(_crop(image, _LEFT_BADGE))
    right = _read_badge(_crop(image, _RIGHT_BADGE))
    arrow = _has_arrow(image)
    if left is None or arrow != (right is not None):
        return None
    current, previous = (right, left) if arrow else (left, None)
    try:
        validate_imprint(name, current)
        if previous is not None:
            validate_imprint(name, previous)
            first = {3: Imprint.D, 4: Imprint.C, 5: Imprint.B}[get_hero(name).rarity.stars]
            expected = first if previous == Imprint.NONE else int(previous) + 1
            if current != expected:
                return None
    except RuleInputError:
        return None
    return HeroResult(name, current, previous)
