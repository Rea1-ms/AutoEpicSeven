"""Advance captured event introductions before their choices become visible."""

from tasks.dimensional_exploration.assets.assets_dimensional_exploration_event_intro import EVENT_INTRO_GIANT_EYE


class EventIntroductionMixin:
    def event_introduction_button(self, vision):
        # The giant eye has a completed opening paragraph without EVENT_NEXT;
        # waiting for that arrow or payable rows would stall forever. Match
        # the captured paragraph, not the illustration shared by its choices
        # and results. Empty/typing frames and unknown events must keep waiting.
        if not self.appear(EVENT_INTRO_GIANT_EYE, similarity=0.9):
            return None
        # The tap belongs only to the introduction. Even a template false
        # positive must not spend resources on an already visible option.
        if vision.event_choices():
            return None
        # Advancing this paragraph is not an option click or outcome receipt.
        # Leave pending history and sampling untouched; the parent state loop
        # rechecks the next screenshot and retains its normal action interval.
        return EVENT_INTRO_GIANT_EYE
