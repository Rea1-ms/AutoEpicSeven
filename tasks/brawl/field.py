"""Recognize captured field sprites without inventing missing identities."""

from dataclasses import dataclass

import numpy as np

from tasks.brawl.assets.assets_brawl_field import (
    FIELD_AUXILIARY_LOTS,
    FIELD_BATTLE_MAYA,
    FIELD_GLEN,
    FIELD_KARIN,
    FIELD_XIAOCHUN,
)
from tasks.brawl.rules import Imprint
from tasks.brawl.vision import BrawlScreen, BrawlVision, PreparationPanel

SUPPORTED_FIELD_HEROES = ("格伦", "辅助型拉兹", "卡琳", "战斗型玛雅", "小春")


@dataclass(frozen=True)
class FieldHero:
    name: str
    area: tuple[int, int, int, int]
    foot: tuple[int, int]
    imprint: Imprint | None = None


@dataclass(frozen=True)
class FieldObservation:
    heroes: tuple[FieldHero, ...] = ()
    panel: PreparationPanel | None = None
    unknown_count: int | None = None
    identity_complete: bool = False
    reason: str = "No supported stable preparation scene"

    @property
    def complete(self):
        """A named sprite does not supply its current imprint or the bench."""
        return self.identity_complete and all(hero.imprint is not None for hero in self.heroes)


def read_field(image, *, observation=None):
    """Read current-frame identity patches and calibrated feet in preparation.

    Only independently checked captured idle poses are supported. A hero in a
    different pose may remain unread even if its name is supported. Patch areas
    describe the matched head/torso, not a full-body bounding box. Feet use the
    displacement measured in each source capture and are pixel anchors only;
    no mapping to the strategy's front/back axes is implied.

    Templates exclude every overhead imprint. The captured source's letter must
    never become the new frame's imprint: the same sprite appears before and
    after upgrades, and pose matching is not badge recognition. No previous
    frame is stored or used to complete a partially visible formation.
    """
    vision = BrawlVision(image)
    current = observation if observation is not None else vision.observe()
    if (current.state != BrawlScreen.PREPARATION or current.panel is None
            or current.game_mode is None):
        return FieldObservation()
    panel = current.panel
    found = []
    for name, button, foot_offset in (
        ("格伦", FIELD_GLEN, (17, 122)),
        ("辅助型拉兹", FIELD_AUXILIARY_LOTS, (19, 105)),
        ("卡琳", FIELD_KARIN, (21, 94)),
        ("战斗型玛雅", FIELD_BATTLE_MAYA, (13, 91)),
        ("小春", FIELD_XIAOCHUN, (17, 107)),
    ):
        matches = button.match_multi_template(image, similarity=0.92)
        if len(matches) > 1:
            return FieldObservation(panel=panel, unknown_count=panel.deployed,
                                    reason="A field identity has multiple candidate positions")
        if not matches:
            continue
        x1, y1, x2, y2 = map(int, matches[0].area)
        region = image[y1:y2, x1:x2]
        # A black redaction across a head must not survive by correlating the
        # remaining torso. Color is checked after locating the sprite, avoiding
        # monochrome or darkened copies which retain the same image structure.
        if (np.mean(np.all(region == 0, axis=2)) >= 0.01
                or np.max(np.abs(region.mean(axis=(0, 1)) - np.array(button.color))) > 20):
            continue
        foot = (x1 + foot_offset[0], y1 + foot_offset[1])
        if not (290 <= foot[0] <= 865 and 200 <= foot[1] <= 615):
            continue
        if any(abs(foot[0] - hero.foot[0]) < 30 and abs(foot[1] - hero.foot[1]) < 30
               for hero in found):
            return FieldObservation(panel=panel, unknown_count=panel.deployed,
                                    reason="Different field identities overlap at one position")
        found.append(FieldHero(name, (x1, y1, x2, y2), foot))
    if len(found) > panel.deployed:
        return FieldObservation(panel=panel, unknown_count=panel.deployed,
                                reason="Recognized identities exceed the current deployed count")
    unknown = panel.deployed - len(found)
    reason = ("All deployed identities are visible; current imprints are unread" if found and unknown == 0
              else "The preparation panel confirms an empty field" if panel.deployed == 0
              else "Some deployed identities or idle poses are unsupported or obscured")
    return FieldObservation(tuple(sorted(found, key=lambda hero: (hero.foot[1], hero.foot[0]))),
                            panel, unknown, unknown == 0, reason)
