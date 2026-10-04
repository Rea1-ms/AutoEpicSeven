"""Inspect the run roster before choices that heal or spend hero HP."""

from module.logger import logger
from tasks.base.assets.assets_base_page import BACK
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import EVENT_HERO_ENTRY
from tasks.dimensional_exploration.policy import normalize


class EventHealthMixin:
    def reset_event_health(self):
        # HP belongs to one fully read option set, never to a run or profile.
        # Keep it across failed option clicks and unknown animation frames;
        # clear it on positive outcomes or a new option set, because damage,
        # healing and recruitment may have changed the roster in between.
        self._event_health_context = None
        self._event_hero_hp = None
        self._event_health_candidate = None
        self._event_health_requested = False

    def inspect_event_health(self, vision, choices, story):
        """Return whether the current option set has confirmed complete HP.

        Pages:
            in: event options
            out: event options with HP ready, or the owned-hero roster
        """
        context = (normalize(story), tuple(normalize(c.text) for c in choices))
        if context != self._event_health_context:
            self.reset_event_health()
            self._event_health_context = context
        if self._event_hero_hp is not None:
            return True, False
        if EVENT_HERO_ENTRY.match_template(vision.image) and self.click_action(EVENT_HERO_ENTRY):
            self._event_health_requested = True
            return False, True
        return False, False

    def handle_roster(self, vision):
        """Read two identical complete roster frames, then return to the caller.

        Pages:
            in: owned-hero roster opened from an exploration page
            out: the event or rest page that opened the roster
        """
        if self._rest_health_requested:
            return self.handle_rest_roster(vision)
        # After a restart the old caller is unknown. Back is reversible and
        # restores a recognizable page; recruiting/upgrading from this roster
        # would confuse owned heroes with the available recruitment list.
        if not self._event_health_requested:
            return self.click_action(BACK)
        heroes = vision.roster_health()
        if heroes is None:
            self._event_health_candidate = None
            return False
        if heroes != self._event_health_candidate:
            self._event_health_candidate = heroes
            return False
        self._event_hero_hp = tuple(h.percent for h in heroes)
        logger.attr("ExplorationHeroHP", [(h.name, f"{h.percent}%") for h in heroes])
        return self.click_action(BACK)
