"""Read visible bench cards and join overlapping observations conservatively."""

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl import (
    BENCH_IMPRINT_A,
    BENCH_IMPRINT_B,
    BENCH_IMPRINT_C,
    BENCH_IMPRINT_D,
    BENCH_IMPRINT_NONE,
    BENCH_NAME_CHRISTY,
    BENCH_PLUS,
    BENCH_SMALL_IMPRINT_A,
    BENCH_SMALL_IMPRINT_C,
    OCR_BENCH,
)
from tasks.brawl.name_cache import read_name_lines, remember_name
from tasks.brawl.rules import (
    BENCH_CAPACITY,
    Imprint,
    RuleInputError,
    get_hero,
    validate_imprint,
)
from tasks.brawl.vision import (
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)

SELECTED_IMPRINT_AREA = (998, 286, 1031, 322)


@dataclass(frozen=True)
class BenchEntry:
    """A visible plus locates the formation control, not a free team slot."""

    name: str
    imprint: Imprint | None
    name_area: tuple[int, int, int, int]
    plus_area: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class BenchViewport:
    entries: tuple[BenchEntry, ...]
    panel: PreparationPanel | None = None
    reason: str = ""
    complete: bool = field(default=False, init=False)


@dataclass(frozen=True)
class BenchHero:
    name: str
    imprint: Imprint | None


@dataclass(frozen=True)
class BenchRoster:
    heroes: tuple[BenchHero, ...]
    panel: PreparationPanel
    reason: str = "Bench endpoints are not verified"
    complete: bool = field(default=False, init=False)


def _known_name(line):
    if not np.isfinite(line.score) or line.score < 0.8:
        return None
    try:
        return get_hero(re.sub(r"\s+", "", line.ocr_text)).name
    except RuleInputError:
        return None


def _selected_imprint(image):
    # Keep template matching and OCR inside the same badge lane. Captured
    # faction glyphs begin to its right; including them turns A into "A&".
    # Full badge templates avoid repeated OCR without borrowing a prior rank.
    found = []
    for imprint, button in (
        (Imprint.NONE, BENCH_IMPRINT_NONE), (Imprint.D, BENCH_IMPRINT_D),
        (Imprint.C, BENCH_IMPRINT_C), (Imprint.B, BENCH_IMPRINT_B),
        (Imprint.A, BENCH_IMPRINT_A),
    ):
        search = button.search
        try:
            button.load_search(SELECTED_IMPRINT_AREA)
            if button.match_template_color(image, similarity=0.9, threshold=20):
                found.append(imprint)
        finally:
            button.load_search(search)
    return found[0] if len(found) == 1 else None


def _ocr_imprint(image, area):
    x1, y1, x2, y2 = area
    region = cv2.resize(image[y1:y2, x1:x2], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    [(text, score)] = Ocr(OCR_BENCH, lang="en", name="BRAWL_BENCH_IMPRINT").ocr_multi_lines([region])
    if not np.isfinite(score) or score < 0.8:
        return None
    text = text.strip().upper()
    # A missed gray dash is not proof of NONE. Only an explicitly recognized
    # dash or a matching dash asset represents the visible no-imprint badge.
    return Imprint.NONE if text == "-" else Imprint.__members__.get(text)


def _small_imprint(image, area):
    # The two rear card sizes render C differently from the selected card.
    # English OCR splits its confidence between upper/lowercase (C), or fails
    # on the complete small purple A. Match full captured glyphs at each size;
    # the rear cards have their own name-relative badge geometry.
    found = []
    for imprint, asset in ((Imprint.C, BENCH_SMALL_IMPRINT_C), (Imprint.A, BENCH_SMALL_IMPRINT_A)):
        for button in asset.buttons:
            search = button.search
            try:
                button.load_search(area)
                if button.match_template_color(image, similarity=0.9, threshold=20):
                    found.append(imprint)
                    break
            finally:
                button.load_search(search)
    if found:
        return found[0] if len(found) == 1 else None
    return _ocr_imprint(image, area)


def read_bench(image, *, observation=None):
    """Read complete visible card text without claiming a complete hero pool.

    The enlarged selected card hides portions of both neighboring cards. Those
    names can still be OCR-readable, but their row identity and badge cannot be
    paired reliably. Exclude them before reading badges. A known name with an
    unread badge remains explicit partial evidence, never an assumed imprint.
    An optional observation, including its panel, must belong to this exact
    image. Reusing it avoids repeated numeric OCR without caching across frames.
    """
    vision = BrawlVision(image)
    observed = observation if observation is not None else vision.observe()
    if observed.state not in (BrawlScreen.PREPARATION, BrawlScreen.FORMATION_SELECTION,
                              BrawlScreen.TRANSMIT_SELECTION):
        return BenchViewport((), reason="No supported bench scene")
    panel = observed.panel
    if panel is None and observed.state != BrawlScreen.PREPARATION:
        try:
            panel = vision.preparation_panel()
        except ObservationError:
            pass
    lines = read_name_lines(image)
    entries = []
    issues = []
    for line in sorted(lines, key=lambda item: (item.box[1], item.box[0])):
        x1, y1, x2, y2 = map(int, line.box)
        selected = 996 <= x1 <= 1010 and 247 <= y1 < y2 <= 284
        small = 1043 <= x1 <= 1086 and 370 <= y1 < y2 and y2 + 35 <= 550
        if not selected and not small:
            if _known_name(line) is not None:
                issues.append(f"Name outside a complete card anchor: {line.ocr_text!r}, "
                              f"area={(x1, y1, x2, y2)}, confidence={line.score:.3f}")
            continue
        name = _known_name(line)
        # The captured complete Christy name loses its last glyph in Chinese
        # OCR. Require the whole photographed name at the selected-card anchor;
        # a truncated string alone must never become a global hero alias.
        if (name is None and selected
                and BENCH_NAME_CHRISTY.match_template_color(image, similarity=0.95, threshold=20)):
            name = "克莉丝缇"
        if name is None:
            if selected:
                issues.append(f"Selected name rejected: {line.ocr_text!r}, "
                              f"area={(x1, y1, x2, y2)}, confidence={line.score:.3f}")
            continue
        remember_name(line, name)
        plus_area = None
        if selected:
            imprint = _selected_imprint(image)
            if imprint is None:
                imprint = _ocr_imprint(image, SELECTED_IMPRINT_AREA)
            if observed.state != BrawlScreen.TRANSMIT_SELECTION:
                pluses = BENCH_PLUS.match_multi_template(image, similarity=0.92)
                if len(pluses) == 1:
                    plus_area = tuple(map(int, pluses[0].area))
                else:
                    issues.append(f"Selected formation plus matches={len(pluses)}; expected one")
        else:
            left = 1000 + 25 * round((x1 - 1000) / 25)
            imprint = _small_imprint(image, (left - 3, y2 + 5, left + 31, y2 + 35))
        if imprint is not None:
            try:
                validate_imprint(name, imprint)
            except RuleInputError:
                imprint = None
        if selected and imprint is None:
            issues.append(f"Selected imprint unread: {name!r}, area={SELECTED_IMPRINT_AREA}")
        entries.append(BenchEntry(name, imprint, (x1, y1, x2, y2), plus_area))
    names = [entry.name for entry in entries]
    if len(names) != len(set(names)):
        return BenchViewport((), reason="Duplicate bench hero names are ambiguous")
    reason = "Bench endpoints are not verified"
    if any(entry.imprint is None for entry in entries):
        reason += "; some visible imprints are unread"
    if not entries:
        reason += "; no complete card text is readable"
        if not issues:
            issues.append("No selected name anchor; OCR=" + repr([
                (line.ocr_text, tuple(map(int, line.box)), round(float(line.score), 3))
                for line in lines]))
    if issues:
        reason += "; " + "; ".join(issues)
    return BenchViewport(tuple(entries), panel, reason)


def merge_bench_views(views):
    """Join overlapping reads from one preparation state, without stale targets.

    Repeated frames and overlapping subsets carry no endpoint evidence. A
    stalled swipe is indistinguishable from not scrolling, so even a coherent
    union remains incomplete. Return hero facts only; screen coordinates from
    earlier scroll positions must never be reused as current click targets.
    """
    views = tuple(views)
    if not views:
        raise ObservationError("No bench views supplied")
    scope = views[0].panel
    if scope is None:
        raise ObservationError("Bench preparation panel is unread")
    merged = []
    known = {}
    for view in views:
        # A failed battle can return to the same round number with different
        # money and heroes. The whole panel must match; round/level alone is
        # not an adequate guard against merging across a retry or purchase.
        if view.panel != scope:
            raise ObservationError("Bench preparation panels disagree")
        if not view.entries:
            raise ObservationError("Bench view has no readable hero names")
        incoming = []
        for entry in view.entries:
            name = get_hero(entry.name).name
            if entry.imprint is not None:
                validate_imprint(name, entry.imprint)
            hero = BenchHero(name, entry.imprint)
            if hero.name in incoming:
                raise ObservationError("Bench view contains duplicate heroes")
            incoming.append(hero.name)
            prior = known.get(hero.name)
            if (prior is not None and prior.imprint is not None and hero.imprint is not None
                    and prior.imprint != hero.imprint):
                raise ObservationError("Bench imprint observations disagree")
            if prior is None or prior.imprint is None:
                known[hero.name] = hero
        if len(known) > BENCH_CAPACITY:
            raise ObservationError("Bench observations exceed the ten-seat capacity")
        if not merged:
            merged = incoming
            continue
        if any(merged[start:start + len(incoming)] == incoming
               for start in range(len(merged) - len(incoming) + 1)):
            continue
        if any(incoming[start:start + len(merged)] == merged
               for start in range(len(incoming) - len(merged) + 1)):
            merged = incoming
            continue
        overlaps = []
        for size in range(1, min(len(merged), len(incoming)) + 1):
            if merged[-size:] == incoming[:size]:
                overlaps.append(merged + incoming[size:])
            if incoming[-size:] == merged[:size]:
                overlaps.append(incoming[:-size] + merged)
        overlaps = [candidate for candidate in overlaps if len(candidate) == len(set(candidate))]
        if len(overlaps) != 1:
            raise ObservationError("Bench views have no unambiguous ordered overlap")
        merged = overlaps[0]
    return BenchRoster(tuple(known[name] for name in merged), scope)
