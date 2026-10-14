"""Read current overhead faction/profession pairs without guessing board cells."""

from dataclasses import dataclass

import cv2
import numpy as np

from tasks.brawl.assets.assets_brawl_traits import (
    FACTION_ARTIFICIAL,
    FACTION_CHAOS,
    FACTION_CLASS_CHANGE,
    FACTION_SUMMER,
    PROFESSION_KNIGHT,
    PROFESSION_MAGE,
    PROFESSION_RANGER,
    PROFESSION_THIEF,
    PROFESSION_WARRIOR,
)
from tasks.brawl.rules import hero_candidates
from tasks.brawl.vision import BrawlScreen, BrawlVision, PreparationPanel


@dataclass(frozen=True)
class HeaderObservation:
    faction: str
    profession: str
    candidates: tuple[str, ...]
    area: tuple[int, int, int, int]

    @property
    def name(self):
        return self.candidates[0] if len(self.candidates) == 1 else None


@dataclass(frozen=True)
class FieldTraitsObservation:
    headers: tuple[HeaderObservation, ...] = ()
    panel: PreparationPanel | None = None
    unknown_count: int | None = None
    identity_complete: bool = False
    reason: str = "No supported stable preparation scene"


@dataclass(frozen=True)
class _Icon:
    name: str
    area: tuple[int, int, int, int]

    @property
    def center(self):
        x1, y1, x2, y2 = self.area
        return ((x1 + x2) / 2, (y1 + y2) / 2)


def _find_icons(image, definitions):
    found = []
    for name, wrapper in definitions:
        for button in wrapper.buttons:
            template = button.image
            height, width = template.shape[:2]
            # Captures contain composited RGB, not an alpha channel. An oval
            # alone still includes the map visible THROUGH the glyph. Retain
            # neutral white ink and its immediate dark outline, leaving other
            # pixels out of both correlation and current-frame color checks.
            # This is conservative source support, not recovered alpha: an
            # occluded stroke must stay unread even if the rest of the header
            # was recognized in an earlier image.
            interior = np.zeros((height, width), dtype=np.uint8)
            cv2.ellipse(interior, (width // 2, height // 2),
                        (width // 2 - 2, height // 2 - 2), 0, 0, 360, 1, -1)
            if wrapper == PROFESSION_KNIGHT:
                # The photographed shield's isolated top edge is unnecessary
                # for its distinctive quartered core and lower outline.
                interior[:5] = 0
            bright = ((template.min(axis=2) >= 220)
                      & (np.ptp(template, axis=2) <= 25)).astype(np.uint8)
            # White map highlights can satisfy the ink color test too. For
            # inset glyphs, discard white components connected to a crop edge
            # BEFORE applying the oval; clipping first would strand background
            # islands inside it. The captured hat and hood touch their crop
            # edges themselves, so their verified inner cores are retained.
            if wrapper not in (PROFESSION_MAGE, PROFESSION_THIEF):
                _, labels = cv2.connectedComponents(bright)
                border = np.unique(np.concatenate((labels[0], labels[-1],
                                                   labels[:, 0], labels[:, -1])))
                bright[np.isin(labels, border)] = 0
            ink = (bright != 0) & (interior != 0)
            near_ink = cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), dtype=np.uint8))
            outline = (cv2.cvtColor(template, cv2.COLOR_RGB2GRAY) <= 140) & (near_ink != 0)
            if ink.sum() < 20 or outline.sum() < 10:
                continue
            mask = (ink | outline).astype(np.uint8)
            selected = mask.astype(bool)
            expected_ink = template[ink].mean(axis=0)
            x1, y1, x2, y2 = button.search
            scores = cv2.matchTemplate(image[y1:y2, x1:x2], template,
                                       cv2.TM_CCOEFF_NORMED, mask=mask)
            scores[~np.isfinite(scores)] = 0
            # At most ten heroes can be deployed. A larger number of matches
            # is not useful evidence; this bound also rejects pathological
            # inputs without iterating over every correlated pixel.
            for _ in range(20):
                _, score, _, point = cv2.minMaxLoc(scores)
                if score < 0.92:
                    break
                px, py = point
                scores[max(0, py - height // 2):py + height // 2 + 1,
                       max(0, px - width // 2):px + width // 2 + 1] = 0
                left, top = px + x1, py + y1
                region = image[top:top + height, left:left + width]
                # Correlation alone is brightness invariant. Validate both
                # halves of the stroke so a white block, dim overlay, or an
                # erased dark outline cannot pass as the original glyph.
                ink_color = region[ink].mean(axis=0)
                outline_color = region[outline].mean(axis=0)
                # The dark anti-aliased edge is partly transparent, so its
                # source RGB is not a stable color target. Its darkness and
                # contrast against white ink remain positive current evidence.
                if (np.mean(np.all(region[selected] == 0, axis=1)) >= 0.01
                        or np.max(np.abs(ink_color - expected_ink)) > 20
                        or np.max(outline_color) > 140
                        or np.min(ink_color - outline_color) < 100):
                    continue
                found.append(_Icon(name, (left, top, left + width, top + height)))
    # Two different meanings at the same position are uncertainty, not a
    # highest-score contest. Never let iteration order choose an identity.
    return tuple(icon for icon in found if not any(
        other.name != icon.name and abs(other.center[0] - icon.center[0]) < 8
        and abs(other.center[1] - icon.center[1]) < 8 for other in found))


def read_field_traits(image, *, observation=None):
    """Recognize supported adjacent header symbols in a fresh preparation frame.

    A pair supplies candidate identities only. Shared faction/profession
    combinations remain ambiguous, and an overhead position is never converted
    to a foot or board cell. Current imprints and unsupported symbols stay
    unknown; no previous image or bench selection completes missing evidence.
    """
    vision = BrawlVision(image)
    current = observation if observation is not None else vision.observe()
    if (current.state != BrawlScreen.PREPARATION or current.panel is None
            or current.game_mode is None):
        return FieldTraitsObservation()
    panel = current.panel
    factions = _find_icons(image, (
        ("卡厄思", FACTION_CHAOS), ("人造生命体", FACTION_ARTIFICIAL),
        ("转职", FACTION_CLASS_CHANGE), ("夏日假期", FACTION_SUMMER),
    ))
    professions = _find_icons(image, (
        ("战士", PROFESSION_WARRIOR), ("骑士", PROFESSION_KNIGHT),
        ("射手", PROFESSION_RANGER), ("魔导士", PROFESSION_MAGE),
        ("盗贼", PROFESSION_THIEF),
    ))
    pairs = tuple((faction, profession) for faction in factions for profession in professions
                  if 27 <= profession.center[0] - faction.center[0] <= 31
                  and abs(profession.center[1] - faction.center[1]) <= 2)
    headers = []
    for faction, profession in pairs:
        if (sum(other_faction == faction for other_faction, _ in pairs) != 1
                or sum(other_profession == profession for _, other_profession in pairs) != 1):
            continue
        candidates = hero_candidates(faction.name, profession.name)
        if not candidates:
            continue
        headers.append(HeaderObservation(
            faction.name, profession.name, candidates,
            (faction.area[0], min(faction.area[1], profession.area[1]),
             profession.area[2], max(faction.area[3], profession.area[3]))))
    if len(headers) > panel.deployed:
        return FieldTraitsObservation(panel=panel, unknown_count=panel.deployed,
                                      reason="Header pairs exceed the current deployed count")
    # Copies of a unique identity cannot be two deployed heroes: the game
    # combines duplicates. Do not publish a definite name at either location,
    # even when the deployed counter is large enough to contain both matches.
    identities = [header.name for header in headers if header.name is not None]
    if len(set(identities)) != len(identities):
        return FieldTraitsObservation(panel=panel, unknown_count=panel.deployed,
                                      reason="A unique header identity appears at multiple positions")
    known_count = sum(header.name is not None for header in headers)
    unknown_count = panel.deployed - known_count
    reason = ("All current identities have unique visible header pairs" if unknown_count == 0
              else "Some header symbols are unread or their identity combination is ambiguous")
    return FieldTraitsObservation(tuple(sorted(headers, key=lambda item: (item.area[1], item.area[0]))),
                                  panel, unknown_count, unknown_count == 0, reason)
