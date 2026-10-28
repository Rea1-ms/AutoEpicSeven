"""Confirm all three heart slots before reporting remaining brawl lives."""

from itertools import pairwise

import cv2
import numpy as np

from tasks.brawl.assets.assets_brawl_lives import LIFE_ACTIVE, LIFE_LOST
from tasks.brawl.vision import BrawlScreen, BrawlVision


def read_lives(image, *, observation=None) -> int | None:
    """Return the current preparation's complete heart count, or unknown.

    The optional observation must belong to this exact screenshot. Combat,
    rewards and overlays are deliberately unsupported: their background and
    shading differ from the photographed preparation hearts. This function
    never interprets a missing red match as a lost life. Every slot must match
    either the complete red heart or the complete gray heart, with its current
    color, and remaining lives must form the game's left-aligned sequence.

    A count alone is not evidence of a defeat. The caller must compare counts
    from the start and return of the same positively observed battle; no count
    or prior battle result is cached here.
    """
    vision = BrawlVision(image)
    current = observation if observation is not None else vision.observe(read_panel=False)
    if current.state != BrawlScreen.PREPARATION or current.game_mode is None:
        return None

    left, top, right, bottom = LIFE_ACTIVE.area
    spacing = (LIFE_LOST.area[0] - left) // 2
    states = []
    for index in range(3):
        pixels = image[top:bottom, left + index * spacing:right + index * spacing]
        color = pixels.mean(axis=(0, 1))
        matches = []
        for asset in (LIFE_ACTIVE, LIFE_LOST):
            # Exact slot crops forbid borrowing a neighboring heart when one
            # is missing or displaced. Color alone could accept a plain gray
            # patch, while normalized shape alone could accept a dim overlay.
            similarity = cv2.matchTemplate(
                pixels, asset.matched_button.image, cv2.TM_CCOEFF_NORMED)[0, 0]
            matches.append(similarity > 0.9 and np.max(np.abs(color - asset.color)) <= 12)
        active, lost = matches
        if active == lost:
            return None
        states.append(active)
    if any(not before and after for before, after in pairwise(states)):
        return None
    return int(sum(states))
