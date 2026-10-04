"""Choose a single rest action using the complete owned roster's current HP."""

from module.base.button import ClickButton
from module.logger import logger
from tasks.base.assets.assets_base_page import BACK
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    REST_ACTION, REST_HERO_ENTRY, ROOM_DONE, ROOM_LEAVE,
)
from tasks.dimensional_exploration.recognition.vision import match_in


def needs_rest_healing(hero_hp: tuple[int, ...] | None) -> bool | None:
    if not hero_hp:
        return None
    # Compare sums instead of a rounded mean: exactly 30% minimum and 60%
    # average do not meet the user's strictly-below recovery thresholds.
    return min(hero_hp) < 30 or sum(hero_hp) < 60 * len(hero_hp)


class RestMixin:
    def reset_rest_health(self):
        self._rest_health_requested = False
        self._rest_health_candidate = None
        self._rest_hero_hp = None
        self._rest_action = None

    def observe_rest_health(self, state):
        # A roster round trip and unknown animations belong to the same room.
        # Positive exits (including upgrade/revive) invalidate its HP because
        # the action may have changed the roster. Never reuse event HP here.
        if state not in ("rest", "roster", "unknown"):
            self.reset_rest_health()

    def handle_rest_roster(self, vision):
        """Confirm a complete, stable roster and return to the rest room.

        Pages:
            in: owned-hero roster opened by this rest inspection
            out: the same rest room
        """
        heroes = vision.roster_health()
        if heroes is None:
            self._rest_health_candidate = None
            return False
        if heroes != self._rest_health_candidate:
            self._rest_health_candidate = heroes
            return False
        self._rest_hero_hp = tuple(h.percent for h in heroes)
        logger.attr("ExplorationRestHP", [(h.name, f"{h.percent}%") for h in heroes])
        return self.click_action(BACK)

    def inspect_rest_health(self, vision):
        if self._rest_hero_hp is not None:
            self._rest_health_requested = False
            return True, False
        if self.appear(REST_HERO_ENTRY) and self.click_action(REST_HERO_ENTRY):
            self._rest_health_requested = True
            return False, True
        return False, False

    def handle_rest(self, vision):
        """Choose recovery or upgrade after recognizing the current room.

        Pages:
            in: rest room
            out: owned roster, upgrade/revive selection, or room exit
        """
        if match_in(self.device.image, ROOM_DONE, ROOM_DONE.search):
            self.reset_rest_health()
            return self.click_action(ROOM_LEAVE)
        if self._upgrade_exhausted:
            return self.click_action(ROOM_LEAVE) if self.appear(ROOM_LEAVE) else False
        buttons = list(REST_ACTION.iter_buttons())
        available = [vision.bright_text(button, minimum=80) for button in buttons]
        row = self._rest_action
        if row is None:
            # Life recovery normally wins, but the user's critical-team rule
            # moves both revive and healing ahead of it below 30% average.
            # If those choices compete, HP is required even when healing is
            # disabled: choosing life before reading the roster would hide an
            # available revive. A pending action never re-enters this decision.
            if available[2] and (available[1] or available[3]):
                ready, clicked = self.inspect_rest_health(vision)
                if not ready:
                    return clicked
                hp = self._rest_hero_hp
                # Compare the exact sum, not a rounded displayed average;
                # dead heroes' zero HP remains part of the complete roster.
                order = (1, 3, 2, 0) if hp and sum(hp) < 30 * len(hp) else (2, 1, 3, 0)
                row = next((r for r in order if available[r]), None)
            else:
                # With no competing life-recovery choice, revive always wins
                # and the roster cannot change this choice in either regime.
                row = next((r for r in (2, 1) if available[r]), None)
            if row is None and available[3]:
                ready, clicked = self.inspect_rest_health(vision)
                if not ready:
                    return clicked
                row = 3 if needs_rest_healing(self._rest_hero_hp) else 0
                if not available[row]:
                    # Healthy heroes must not be healed just because upgrade
                    # is disabled. Leaving is allowed by its positive button.
                    return self.click_action(ROOM_LEAVE) if self.appear(ROOM_LEAVE) else False
            elif row is None and available[0]:
                row = 0
        if row is None or not available[row]:
            return False
        if self.click_action(ClickButton(buttons[row].area, name="ExplorationRestAction")):
            # One action consumes the room. A successful click is only a
            # pending action: retry it on unchanged frames, and wait for the
            # done marker or an action subpage before choosing anything else.
            if self._rest_action is None:
                name = ("upgrade", "revive", "recover life", "heal")[row]
                hp = self._rest_hero_hp
                reason = f"Minimum HP {min(hp)}%, average HP {sum(hp)/len(hp):.1f}%" if hp else "Use available option priority"
                logger.attr("ExplorationRest", f"{name}：{reason}")
            self._rest_action = row
            return True
        return False
