from datetime import datetime, timedelta

from module.logger import logger
from tasks.base.resource_bar import RESOURCE_BAR_LAYOUT_MAIN
from tasks.base.resource_scheduling import (
    RESOURCE_RECOVERY_SECONDS,
    RESOURCE_REFRESH_RETRY_MINUTES,
    ResourceCoordinator,
    local_now,
)
from tasks.dungeon.episode_materials import EPISODE_STAMINA_COST

SERVER_REPEAT_LEIF_STAMINA = 80

# Stamina cost of one combat run from in-game data.
# Dimensional hunt is intentionally absent: it consumes its own resource and
# is out of burnout-mode scope for now.
EPISODE4_STAMINA_COST = EPISODE_STAMINA_COST[4]
SAINT37_STAMINA_COST = 8
URGENT_TASKS_STAMINA_COST = {
    "Normal": 15,
    "Superior": 30,
}
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
    - Unknown, expired, or consumed stamina requests a fresh read and a
      bounded retry instead of scheduling from an old dashboard amount.
    """

    COMBAT_BURNOUT_RECHECK_MINUTES = 1

    def _combat_burnout_enabled(self) -> bool:
        if self._combat_is_farm_task():
            return False
        if self._dungeon_domain() == "UrgentTasks":
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
            episode = getattr(self.config, "Combat_EpisodeNumber", 4)
            return EPISODE_STAMINA_COST.get(int(episode))
        if domain == "Saint37":
            return SAINT37_STAMINA_COST
        if domain == "UrgentTasks":
            return URGENT_TASKS_STAMINA_COST.get(self._combat_grade())
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
        The legacy flow wakes for one stage. The global server-managed flow
        waits for one complete 80-stamina unit so fast combat cannot consume
        the partial remainder before a repeat batch can be formed.
        """
        cost = self._combat_burnout_stage_cost()
        if cost is None:
            return None
        if self._uses_server_repeat_combat():
            # The new server-repeat selector consumes 80-stamina Leif units.
            # Waiting for a whole unit prevents the fast-combat remainder from
            # repeatedly spending stamina before a server batch can be formed.
            return SERVER_REPEAT_LEIF_STAMINA
        return cost

    def _combat_burnout_refresh_status(self) -> bool:
        """
        Refresh stored stamina from the main-page resource bar.

        Burnout scheduling runs at the settle points of Combat.run(), which
        are both on the main page, and the main resource bar carries the
        post-burn stamina (plus arena flags as a free bonus for the arena
        burnout mode). A failed OCR may still leave a fresh, unconsumed
        prepare-page observation available to scheduling.

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
        self.write_resource_bar_status(parsed)
        if not parsed or "stamina" not in parsed:
            logger.warning("Combat burnout: main stamina unreadable, check fresh resource observation")
            return False
        return True

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
        coordinator = ResourceCoordinator(self.config)
        now = local_now().replace(microsecond=0)
        observed = coordinator.observation("stamina", now=now)
        # Dashboard values are for display and can predate an entire combat
        # session. Only a fresh, unconsumed observation can authorize a short
        # rerun or anchor recovery. In particular, a missing home flag must
        # never resurrect the old 415/360 after the menu just reported 8.
        if observed is None or (
            observed["value"] < need
            and observed.get("capacity", observed["total"]) <= 0
        ):
            logger.warning("Combat burnout: stamina or recovery cap unknown, request refresh and retry later")
            coordinator.request("Combat", keys=("stamina",))
            self.config.task_delay(minute=RESOURCE_REFRESH_RETRY_MINUTES)
            coordinator.wait("Combat", "stamina", need)
            return True

        value = observed["value"]
        capacity = observed.get("capacity", observed["total"])
        logger.attr("BurnoutStamina", f"{value}/{capacity}")
        logger.attr("BurnoutStaminaNeed", need)
        if value >= need:
            # Stockpiled stamina can afford the batch even above a small or
            # unknown natural cap. Check the fresh amount before cap limits.
            logger.info("Combat burnout: stamina already sufficient, run again shortly")
            self.config.task_delay(minute=self.COMBAT_BURNOUT_RECHECK_MINUTES)
            return True

        if self._uses_server_repeat_combat() and need > capacity:
            # A low-level natural cap cannot form a server-repeat unit. Keep
            # the real 80-point gate for external gifts instead of waking on
            # a full but still unaffordable bar over and over.
            self.config.task_delay(server_update=True)
            coordinator.wait("Combat", "stamina", need)
            return True

        # Natural regeneration can never exceed the cap, so a batch larger
        # than the cap waits until stamina is full instead of forever.
        need = min(need, capacity)
        observed_at = datetime.fromisoformat(observed["at"])
        target = observed_at + timedelta(seconds=(need - value) * RESOURCE_RECOVERY_SECONDS["stamina"])

        if target <= now:
            logger.info("Combat burnout: stamina already sufficient, run again shortly")
            self.config.task_delay(minute=self.COMBAT_BURNOUT_RECHECK_MINUTES)
            return True

        logger.info(f"Combat burnout: stamina {value} -> {need}, wait until {target}")
        self.config.task_delay(target=target)
        coordinator.wait("Combat", "stamina", need)
        return True
