"""Subtract a completely recovered field from six positively read class counts."""

import re
from collections import Counter

import cv2
import numpy as np

from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl_talents import SYNERGY_TAB_SELECTED
from tasks.brawl.roster import RosterState
from tasks.brawl.rules import get_hero
from tasks.brawl.talents import _PROFESSIONS, read_empty_talents
from tasks.brawl.vision import BrawlScreen, BrawlVision, ObservationError


def read_resumed_talents(image, roster):
    """Require the current roster context and all six unique profession rows.

    A hidden profession cannot become zero in a mixed faction/profession list.
    The caller may instead create a verified empty field and use its complete
    list reader. Counts are allowed past their threshold (for example 6/4);
    the denominator is a synergy threshold, not an inventory capacity.
    """
    current = BrawlVision(image).observe()
    if (not isinstance(roster, RosterState) or current.state != BrawlScreen.PREPARATION
            or current.panel != roster.panel or current.game_mode != roster.game_mode):
        raise ObservationError("Current preparation does not confirm the recovered roster")
    if roster.panel.deployed == 0:
        return read_empty_talents(image, observation=current)
    if (not SYNERGY_TAB_SELECTED.match_template(image, similarity=.92)
            or not SYNERGY_TAB_SELECTED.match_color(image, threshold=15)):
        raise ObservationError("The synergy tab is not visibly selected")
    contribution = Counter(get_hero(hero.name).profession for hero in roster.heroes if hero.cell is not None)
    regions, anchors = [], []
    for name, _, icon, _ in _PROFESSIONS:
        matches = icon.match_multi_template(image, similarity=.92)
        if len(matches) != 1:
            raise ObservationError(f"The {name} profession row is absent or ambiguous")
        left, top, right, bottom = matches[0].area
        region = image[top:bottom, left:right]
        color = region.mean(axis=(0, 1))
        # Activated white and glowing icons have their own photographed frames.
        # Compare against every frame explicitly: wrapper matching does not set
        # its selected color when returning independent multi-match positions.
        if (np.mean(np.all(region == 0, axis=2)) >= .01
                or not any(np.max(np.abs(color - np.asarray(button.color))) <= 20 for button in icon.buttons)
                or any(abs(top - other) < 30 for other in anchors)):
            raise ObservationError("A profession icon is obscured, dimmed or overlaps another row")
        anchors.append(top)
        x1, y1, x2, y2 = right + 6, top - 1, right + 43, top + 22
        if not (0 <= x1 < x2 <= 110 and 90 <= y1 < y2 <= 640):
            raise ObservationError("A recovered profession count is clipped")
        # The narrow photographed digits lose slash/stroke spacing during the
        # model's fixed-height resize, notably an active thief's 3/6. Widen the
        # real pixels uniformly; do not repair text using expected hero counts
        # or lower the confidence threshold when a plausible value is returned.
        regions.append(cv2.resize(image[y1:y2, x1:x2], None, fx=1.5, fy=1,
                                  interpolation=cv2.INTER_CUBIC))
    values = Ocr(_PROFESSIONS[0][2], lang="en", name="BRAWL_RESUMED_TALENTS").ocr_multi_lines(regions)
    logger.attr("Brawl recovered talent counts", values)
    if len(values) != len(_PROFESSIONS):
        raise ObservationError("Recovered profession counts are incomplete")
    bonuses = {}
    for (name, _, _, maximum), (text, confidence) in zip(_PROFESSIONS, values):
        match = re.fullmatch(r"(\d{1,2})/([46])", re.sub(r"\s+", "", text))
        if (not np.isfinite(confidence) or confidence < .9 or match is None
                or int(match[2]) != maximum):
            raise ObservationError(f"The recovered {name} count is invalid or unread: {text!r}")
        bonus = int(match[1]) - contribution[name]
        if bonus not in (0, 1):
            raise ObservationError(f"The {name} count contradicts the recovered deployed heroes")
        bonuses[name] = bonus
    logger.attr("Brawl recovered talents", bonuses)
    return bonuses
