"""Read a stable bench selection and verify its visible adjacent portrait."""

from dataclasses import dataclass

import cv2
import numpy as np

from tasks.brawl.assets.assets_brawl import (
    QUEUE_BOTTOM_CHECK, QUEUE_EMPTY_CHECK, QUEUE_NEXT_CHECK, QUEUE_TOP_CHECK,
)
from tasks.brawl.bench import BenchHero, read_bench
from tasks.brawl.vision import BrawlMode, BrawlScreen, BrawlVision, PreparationPanel


@dataclass(frozen=True)
class Cursor:
    selected: BenchHero | None = None
    top: bool | None = None
    bottom: bool | None = None
    empty: bool | None = None
    next_area: tuple[int, int, int, int] | None = None
    panel: PreparationPanel | None = None
    game_mode: BrawlMode | None = None


def read_cursor(image, *, observation=None):
    """Read only stable preparation; missing OCR never establishes an endpoint.

    The photographed arena background positively identifies each empty end.
    A different background, an obscured region, or a black rectangle remains
    unknown. The lower neighboring card has its own frame anchor, independent
    of its partly covered name; its portrait is checked after selection.
    """
    vision = BrawlVision(image)
    observed = observation if observation is not None else vision.observe()
    if (observed.state != BrawlScreen.PREPARATION or observed.panel is None
            or observed.game_mode is None):
        return Cursor()
    scope = {"panel": observed.panel, "game_mode": observed.game_mode}
    if vision.matches(QUEUE_EMPTY_CHECK, color=True):
        return Cursor(top=True, bottom=True, empty=True, **scope)
    entries = read_bench(image, observation=observed).entries
    selected = [entry for entry in entries
                if 996 <= entry.name_area[0] <= 1010
                and 247 <= entry.name_area[1] < entry.name_area[3] <= 284
                and entry.plus_area is not None]
    if len(selected) != 1:
        return Cursor(**scope)
    hero = BenchHero(selected[0].name, selected[0].imprint)
    top = True if vision.matches(QUEUE_TOP_CHECK, color=True) else None
    bottom = vision.matches(QUEUE_BOTTOM_CHECK, color=True)
    neighbor = vision.matches(QUEUE_NEXT_CHECK, color=True)
    if bottom and neighbor:
        return Cursor(selected=hero, top=top, empty=False, **scope)
    return Cursor(selected=hero, top=top, bottom=True if bottom else False if neighbor else None,
                  empty=False, next_area=(1030, 335, 1265, 375) if neighbor else None, **scope)


def _usable(region):
    # Correlation on uniform or erased pixels is not identity evidence. This
    # deliberately rejects damaged portraits instead of declaring a mismatch.
    return (region.size > 0 and float(region.std()) >= 12
            and float(np.mean(np.all(region < 4, axis=2))) < .01)


def _same_context(before, after):
    return (before.panel is not None and before.game_mode is not None
            and before.panel == after.panel and before.game_mode == after.game_mode)


def confirms_next(before, after):
    """Verify that the prior adjacent card's portrait became the selected one.

    The adjacent card is smaller and partly covered by the current selection.
    Its visible face survives enlargement at approximately 1.08 times scale.
    Search that photographed range within the new selected portrait, rather
    than trusting a changed name or arbitrary movement of the list.
    """
    prior, current = read_cursor(before), read_cursor(after)
    if not _same_context(prior, current):
        return None
    if (prior.selected is None or current.selected is None
            or prior.selected.imprint is None or current.selected.imprint is None
            or prior.next_area is None):
        return None
    source = before[335:374, 1202:1268]
    target = after[270:321, 1187:1275]
    if not _usable(source) or not _usable(target):
        return None
    score = 0.0
    for scale in (1.05, 1.06, 1.07, 1.08, 1.09, 1.10, 1.11, 1.12):
        resized = cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        score = max(score, float(cv2.matchTemplate(target, resized, cv2.TM_CCOEFF_NORMED).max()))
    return score >= .92


def agrees_selection(before, after):
    """Check that the current visible queue still agrees while a click waits.

    This compares only the visible portion, never the hidden inventory. Both
    frames must independently identify the same selected hero and preparation
    context. Unknown endpoints are allowed if the same adjacent card anchor
    is positively visible in each frame. No historical click area is reused.
    """
    prior, current = read_cursor(before), read_cursor(after)
    if not _same_context(prior, current):
        return None
    if prior.selected is None or current.selected is None:
        return None
    if prior.selected != current.selected:
        return False
    if prior.empty is not False or current.empty is not False:
        return None
    for cursor in (prior, current):
        if cursor.bottom is None:
            return None
    if prior.bottom != current.bottom or (prior.next_area is None) != (current.next_area is None):
        return False
    regions = [(image[175:550, 1000:1280]) for image in (before, after)]
    if not all(_usable(region) for region in regions):
        return None
    # Test each visible row separately. One changed small portrait must not be
    # diluted by the larger unchanged selected card or empty arena background.
    for start, end in ((0, 63), (63, 152), (152, 208), (208, 266), (266, 321), (321, 375)):
        a, b = (region[start:end] for region in regions)
        similarity = float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED).max())
        if similarity < .97 or np.max(np.abs(a.mean((0, 1)) - b.mean((0, 1)))) > 10:
            return False
    return True
