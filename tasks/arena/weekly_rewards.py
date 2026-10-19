"""Select the configured weekly ranking reward before claiming it."""

from module.logger import logger
from tasks.arena.assets.assets_arena_weekly_rewards import (
    WEEKLY_REWARDS_CLAIM,
    WEEKLY_REWARDS_MYSTIC,
    WEEKLY_REWARDS_MYSTIC_SELECTED,
    WEEKLY_REWARDS_SKYSTONE,
    WEEKLY_REWARDS_SKYSTONE_SELECTED,
)


class ArenaWeeklyRewardsMixin:
    ARENA_WEEKLY_REWARDS_SELECT_INTERVAL_SECONDS = 1
    ARENA_WEEKLY_REWARDS_CLAIM_INTERVAL_SECONDS = 1
    ARENA_WEEKLY_REWARDS_CLAIM_SIMILARITY = 0.85
    ARENA_WEEKLY_REWARDS_CLAIM_COLOR_THRESHOLD = 30

    def _handle_weekly_rewards_popup(self) -> bool:
        """Return True only after selecting or claiming a weekly reward."""
        if not self.appear(WEEKLY_REWARDS_SKYSTONE):
            return False

        reward = getattr(self.config, "Arena_WeeklyReward", "Skystone")
        button, selected = {
            "Skystone": (WEEKLY_REWARDS_SKYSTONE, WEEKLY_REWARDS_SKYSTONE_SELECTED),
            "MysticMedals": (WEEKLY_REWARDS_MYSTIC, WEEKLY_REWARDS_MYSTIC_SELECTED),
        }[reward]

        # A bright claim button proves that *some* reward is selected, not
        # that the configured reward is selected. Re-read its own border on
        # every frame; a click may fail or leave the previous border visible.
        # Never claim from the click intent or the other reward's border.
        if self.appear(selected):
            if self.match_template_color(
                WEEKLY_REWARDS_CLAIM,
                interval=self.ARENA_WEEKLY_REWARDS_CLAIM_INTERVAL_SECONDS,
                similarity=self.ARENA_WEEKLY_REWARDS_CLAIM_SIMILARITY,
                threshold=self.ARENA_WEEKLY_REWARDS_CLAIM_COLOR_THRESHOLD,
            ):
                self.device.click(WEEKLY_REWARDS_CLAIM)
                logger.info(f"Arena: claim weekly reward {reward}")
                return True
            return False

        if self.appear_then_click(
            button, interval=self.ARENA_WEEKLY_REWARDS_SELECT_INTERVAL_SECONDS,
        ):
            logger.info(f"Arena: select weekly reward {reward}")
            return True

        return False
