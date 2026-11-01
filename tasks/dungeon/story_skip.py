"""Story controls shared by the manually started progression loops."""

from module.base.button import ClickButton
from tasks.dungeon.assets.assets_dungeon_action import AUTO_COMBAT
from tasks.dungeon.assets.assets_dungeon_episode_battle import (
    EPISODE_BATTLE_CONTROLS_CHECK,
)
from tasks.dungeon.assets.assets_dungeon_episode_story import (
    EPISODE_STORY_SKIP,
    EPISODE_STORY_SKIP_CHECK,
    EPISODE_STORY_SKIP_CONFIRM,
)
from tasks.dungeon.assets.assets_dungeon_story_push_common import (
    STORY_HISTORY_CHECK,
)
from tasks.dungeon.execute import detect_auto_combat_state

# The history control authorizes this blank-area click. Its matched position
# opens story history and must never become the reveal action's click target.
STORY_INTRO_REVEAL = ClickButton((920, 150, 1000, 210), name='STORY_INTRO_REVEAL')


class StorySkipMixin:
    ACTION_INTERVAL = 2
    _story_intro_clicked = False

    def _reset_story_intro(self):
        # Rearm only at task entry or a positively recognized non-story page.
        # The history control also stays visible during ordinary dialogue. Its
        # disappearance during fades/blackouts cannot establish a new story. This latch
        # across unknown frames prevents repeated blank taps in the same scene.
        self._story_intro_clicked = False

    def _story_intro_visible(self):
        # Story artwork belongs to each event and cannot authorize a shared
        # action. Use only the story-history control, without waiting for title
        # text. Color rejects the same control dimmed beneath an overlay.
        return self.match_template_color(STORY_HISTORY_CHECK, threshold=25)

    def _story_visible(self):
        return (
            self.appear(EPISODE_STORY_SKIP_CHECK)
            or self.appear(EPISODE_STORY_SKIP)
            or self._story_intro_visible()
        )

    def _click(self, button, interval=None):
        if interval is None:
            interval = self.ACTION_INTERVAL
        if self.interval_is_reached(button, interval=interval):
            self.device.click(button)
            self.interval_reset(button, interval=interval)
            return True
        return False

    def _handle_story_skip(self):
        if self.appear(EPISODE_STORY_SKIP_CHECK):
            self._story_intro_clicked = True
            if self.appear(EPISODE_STORY_SKIP_CONFIRM):
                return self._click(EPISODE_STORY_SKIP_CONFIRM)
            return False
        if self.appear(EPISODE_STORY_SKIP):
            # A visible skip already establishes that no reveal is needed.
            # Keep that observation through subsequent toolbar animation too.
            self._story_intro_clicked = True
            # The opening frame can show skip before it accepts the first tap.
            # Retry this action sooner; confirmation and other actions retain
            # their own independent intervals and remain immediately eligible.
            return self._click(EPISODE_STORY_SKIP, interval=.5)
        if not self._story_intro_clicked and self._story_intro_visible():
            # One tap is enough to request reveal. An ignored request may wait
            # for the game's normal animation; never retry through this latch.
            # This action has no shared cooldown with skip or confirmation, so
            # their first available frame remains immediately actionable.
            self.device.click(STORY_INTRO_REVEAL)
            self._story_intro_clicked = True
            return True
        return False

    def _story_auto_state(self):
        state = detect_auto_combat_state(self.appear)
        if state is not None:
            return state
        # Story interruptions can disable auto combat against elite enemies,
        # where the numbered enemy target does not exist. Missing reticle and
        # skill-auto indicators alone are also normal during skill animations.
        # Require the current manual-mode button AND the bright battle toolbar
        # before treating their absence as an actionable auto-off observation.
        # Auto-forbidden battles are rejected by the caller before this check.
        if self.match_template_color(AUTO_COMBAT, threshold=25) and self.match_color(
            EPISODE_BATTLE_CONTROLS_CHECK, threshold=25,
        ):
            return False
        return None
