"""Read profession talent bonuses from the current empty opening board."""

import re
from itertools import pairwise

import numpy as np

from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl_talents import (
    SYNERGY_TAB_SELECTED,
    TALENT_KNIGHT_ICON,
    TALENT_MAGE_ICON,
    TALENT_RANGER_ICON,
    TALENT_SOUL_WEAVER_ICON,
    TALENT_THIEF_ICON,
    TALENT_WARRIOR_ICON,
)
from tasks.brawl.vision import BrawlScreen, BrawlVision, ObservationError

_PROFESSIONS = (
    ("战士", "Warrior", TALENT_WARRIOR_ICON, 6),
    ("骑士", "Knight", TALENT_KNIGHT_ICON, 6),
    ("射手", "Ranger", TALENT_RANGER_ICON, 6),
    ("魔导士", "Mage", TALENT_MAGE_ICON, 4),
    ("盗贼", "Thief", TALENT_THIEF_ICON, 6),
    ("精灵师", "Soul Weaver", TALENT_SOUL_WEAVER_ICON, 4),
)


def read_opening_talents(image, *, observation=None):
    """Read talents only from the original first-round empty preparation."""
    current = observation if observation is not None else BrawlVision(image).observe()
    if current.panel is None or current.panel.round_number != 1:
        raise ObservationError("Talent reading requires a stable empty first-round preparation")
    return read_empty_talents(image, observation=current)


def read_empty_talents(image, *, observation=None):
    """Return all six 0/1 bonuses, or reject incomplete current-frame evidence.

    The optional observation must belong to this same screenshot. Counts are
    talent evidence only with an explicitly empty field, including recovery.
    The game hides locked professions entirely. Missing icons therefore mean
    zero only after every visible count is paired with a unique profession and
    the compact list reaches its photographed bottom anchor without gaps.
    This prevents a failed icon match from silently disabling a talent. Row
    order never supplies identity, and previous frames never supply a count.
    A selected synergy tab is required even with caller-provided evidence.
    """
    vision = BrawlVision(image)
    current = observation if observation is not None else vision.observe()
    if (current.state != BrawlScreen.PREPARATION or current.panel is None
            or current.game_mode is None or current.panel.deployed != 0):
        raise ObservationError("Talent reading requires a stable empty preparation")
    # Only the upper part is needed: historical fixtures redact the unrelated
    # account area below y=680. Template identity and current fixed-area color
    # independently reject the unselected tab and translucent overlays.
    if (not SYNERGY_TAB_SELECTED.match_template(image, similarity=0.92)
            or not SYNERGY_TAB_SELECTED.match_color(image, threshold=15)):
        raise ObservationError("The synergy tab is not visibly selected")

    regions = []
    anchors = []
    visible = []
    for profession in _PROFESSIONS:
        _, label, icon, _ = profession
        matches = icon.match_multi_template(image, similarity=0.92)
        logger.attr(f"Brawl talent {label} anchors", [tuple(item.area) for item in matches])
        if not matches:
            continue
        if len(matches) != 1:
            raise ObservationError(f"The {label} talent icon is ambiguous")
        left, top, right, bottom = matches[0].area
        color = image[top:bottom, left:right].mean(axis=(0, 1))
        if np.max(np.abs(color - np.asarray(icon.color))) > 20:
            raise ObservationError(f"The {label} talent icon is dimmed or obscured")
        if any(abs(top - other_top) < 30 for _, other_top, _, _ in anchors):
            raise ObservationError("Profession talent icons overlap")
        anchors.append((left, top, right, bottom))
        # The narrow number region excludes the progress bar directly below
        # it. Including that bar can turn a complete '1/6' into '1/6-'.
        number_area = (right + 6, top - 1, right + 43, top + 22)
        x1, y1, x2, y2 = number_area
        if not (0 <= x1 < x2 <= 110 and 90 <= y1 < y2 <= 640):
            raise ObservationError(f"The {label} talent count is clipped")
        regions.append(image[y1:y2, x1:x2])
        visible.append(profession)

    # Detect the entire count column independently of icon matches. An orphan
    # number is evidence of an unread row, never evidence of a locked class.
    # All captured opening lists are bottom-aligned with 37-pixel row spacing.
    # An entirely empty/unread sidebar has no positive list anchor yet, so it
    # remains unsupported instead of becoming six invented zero bonuses.
    ocr = Ocr(TALENT_WARRIOR_ICON, lang="en", name="BRAWL_OPENING_TALENTS")
    rows = ocr.detect_and_ocr(image[90:620, 60:102], direct_ocr=True)
    logger.attr("Brawl talent count rows", [
        (row.ocr_text, tuple(row.box), round(float(row.score), 3)) for row in rows])
    tops = sorted(top for _, top, _, _ in anchors)
    if (not tops or len(rows) != len(anchors) or abs(tops[-1] - 569) > 3
            or any(abs(after - before - 37) > 3 for before, after in pairwise(tops))):
        raise ObservationError("The visible profession list is incomplete or obscured")
    for row in rows:
        if (not np.isfinite(row.score) or row.score < 0.9
                or re.fullmatch(r"[01]/[46]", re.sub(r"\s+", "", row.ocr_text)) is None):
            raise ObservationError("A visible profession count row is unread")
    for _, top, _, bottom in anchors:
        if sum(abs((row.box[1] + row.box[3]) / 2 + 90 - (top + bottom) / 2) <= 8
               for row in rows) != 1:
            raise ObservationError("Profession icons and count rows do not pair uniquely")

    values = ocr.ocr_multi_lines(regions)
    if len(values) != len(visible):
        raise ObservationError("Profession talent counts are incomplete")
    bonuses = dict.fromkeys((name for name, _, _, _ in _PROFESSIONS), 0)
    for (name, label, _, maximum), (text, confidence) in zip(visible, values):
        if not np.isfinite(confidence) or confidence < 0.9:
            raise ObservationError(f"The {label} talent count has low recognition confidence")
        match = re.fullmatch(r"([01])/([46])", re.sub(r"\s+", "", text))
        if match is None or int(match[2]) != maximum:
            raise ObservationError(f"The {label} talent count is invalid: {text!r}")
        bonuses[name] = int(match[1])
    logger.attr("Brawl opening talents", ", ".join(
        f"{label}={bonuses[name]}" for name, label, _, _ in _PROFESSIONS))
    return bonuses
