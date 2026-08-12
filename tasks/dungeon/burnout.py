from module.logger import logger
from tasks.base.resource_bar import RESOURCE_BAR_LAYOUT_MAIN

# Stamina cost of one combat run from in-game data.
# Dimensional hunt is intentionally absent: it consumes leaves instead of
# stamina (1 leaf = 160 stamina) and is out of burnout-mode scope for now.
EPISODE4_STAMINA_COST = 20
SAINT37_STAMINA_COST = 8
HUNT_STAMINA_COST = {
    "Mid": 16,
    "High": 18,
    "Hell": 20,
}
ALTAR_STAMINA_COST = {
    "Pri": 9,
    "Mid": 10,
    "High": 11,
    "Hell": 12,
}


class CombatBurnoutMixin:
    """
    Burnout mode for the daily Combat task.

    Instead of waiting for the next server day after a settled run, burnout
    mode predicts when natural stamina regeneration (1 point every 4 minutes)
    will cover the stamina cost of the next combat batch, and schedules the
    next run at that moment. Combined with the normal combat flow this burns
    stamina as soon as it becomes sufficient, all day long.

    Scope guards:
    - CombatFarm keeps its own 1-minute background loop and never uses this.
    - Dimensional hunt consumes leaves, not stamina, so it falls back to the
      daily server-update scheduling.
    - When stored stamina has never been OCR'd (`total <= 0`), fall back to
      daily scheduling instead of guessing.
    """

    COMBAT_BURNOUT_RECHECK_MINUTES = 1

    def _combat_burnout_enabled(self) -> bool:
        if self._combat_is_farm_task():
            return False
        mode = getattr(self.config, "Combat_BurnoutMode", "Daily")
        return mode is True or mode == "Burnout"

    def _combat_stage_stamina_cost(self) -> int | None:
        """
        Returns:
            int | None: Stamina cost of one run for the configured target,
                None when the target does not consume stamina.
        """
        domain = self._dungeon_domain()
        if domain == "Episode4":
            return EPISODE4_STAMINA_COST
        if domain == "Saint37":
            return SAINT37_STAMINA_COST
        if domain == "Hunt":
            return HUNT_STAMINA_COST.get(self._combat_grade())
        if domain == "SpiritAltar":
            return ALTAR_STAMINA_COST.get(self._combat_grade())
        return None

    def _combat_burnout_stage_cost(self) -> int | None:
        """Compatibility entry for burnout scheduling and existing tests."""
        return self._combat_stage_stamina_cost()

    def _combat_burnout_batch_need(self) -> int | None:
        """
        Stamina needed to start the next burnout run.

        Burnout combat uses the maximum currently affordable count on the
        prepare page, so fixed-count settings must not affect wake-up time.
        One stage worth of stamina is sufficient; the current resource value
        determines how many stages the next fast or repeat batch can run.
        """
        cost = self._combat_burnout_stage_cost()
        if cost is None:
            return None
        return cost

    def _combat_burnout_refresh_status(self) -> bool:
        """
        Refresh stored stamina from the main-page resource bar.

        Burnout scheduling runs at the settle points of Combat.run(), which
        are both on the main page, and the main resource bar carries the
        post-burn stamina (plus arena flags as a free bonus for the arena
        burnout mode). A failed OCR is not fatal: scheduling then falls back
        to predicting from the last stored record.

        Pages:
            in: page_main
            out: page_main
        """
        parsed = self.ocr_resource_bar_status(
            layout=RESOURCE_BAR_LAYOUT_MAIN,
            layout_name="Main",
            skip_first_screenshot=False,
            timeout_seconds=self.COMBAT_RESOURCE_BAR_TIMEOUT_SECONDS,
            timeout_count=self.COMBAT_RESOURCE_BAR_TIMEOUT_COUNT,
        )
        if parsed is None:
            logger.warning("Combat burnout: main resource bar OCR failed, use stored stamina record")
            return False
        return self.write_resource_bar_status(parsed)

    def _combat_burnout_schedule(self) -> bool:
        """
        Schedule the next Combat run by stamina regeneration.

        Returns:
            bool: True when burnout mode has scheduled the next run, False
                when the caller should fall back to daily scheduling.
        """
        if not self._combat_burnout_enabled():
            return False

        need = self._combat_burnout_batch_need()
        if need is None:
            logger.info(
                "Combat burnout: current target consumes leaves instead of stamina, "
                "fallback to daily scheduling"
            )
            return False

        self._combat_burnout_refresh_status()
        stamina = self.config.stored.Stamina
        if stamina.total <= 0:
            logger.warning("Combat burnout: stored stamina is unknown, fallback to daily scheduling")
            return False

        # Natural regeneration can never exceed the cap, so a batch larger
        # than the cap waits until stamina is full instead of forever.
        need = min(need, stamina.total)
        predicted = stamina.predict_current()
        logger.attr("BurnoutStamina", f"{predicted}/{stamina.total}")
        logger.attr("BurnoutStaminaNeed", need)

        if predicted >= need:
            logger.info("Combat burnout: stamina already sufficient, run again shortly")
            self.config.task_delay(minute=self.COMBAT_BURNOUT_RECHECK_MINUTES)
            return True

        target = stamina.predict_reach_time(need)
        logger.info(f"Combat burnout: stamina {predicted} -> {need}, wait until {target}")
        self.config.task_delay(target=target)
        return True
