from module.logger import logger


class ArenaBurnoutMixin:
    """
    Burnout mode for the Arena task.

    Instead of waiting for the next server day after a finished run, burnout
    mode predicts when arena flags regenerate back to full (1 flag every
    hour, cap 5) and schedules the next run at that moment, so flags are
    spent on NPC combat as soon as they are full and none of the natural
    regeneration is wasted at the cap.

    Scope guards:
    - NPC combat must be enabled. Without it the task consumes no flags, and
      a full counter would re-trigger an immediate rerun forever.
    - When stored arena flags have never been OCR'd (`total <= 0`), fall
      back to daily scheduling instead of guessing.
    """

    ARENA_BURNOUT_RECHECK_MINUTES = 1

    def _arena_burnout_enabled(self) -> bool:
        mode = getattr(self.config, "Arena_BurnoutMode", "Daily")
        return mode is True or mode == "Burnout"

    def _arena_burnout_schedule(self) -> bool:
        """
        Schedule the next Arena run by flag regeneration.

        Returns:
            bool: True when burnout mode has scheduled the next run, False
                when the caller should fall back to daily scheduling.

        Pages:
            in: arena page (preferred; any page tolerated, OCR then falls
                back to the last stored flag estimate)
            out: unchanged
        """
        if not self._arena_burnout_enabled():
            return False
        if not getattr(self.config, "Arena_NPCCombat", False):
            logger.info(
                "Arena burnout: NPC combat disabled so nothing consumes flags, "
                "fallback to daily scheduling"
            )
            return False

        # Refresh the post-combat flag count from the arena resource bar.
        # A failed OCR is not fatal: `_consume_stored_arena_flags()` already
        # maintained a per-round estimate during NPC combat.
        self._update_arena_dashboard_snapshot(skip_first_screenshot=False)

        flag = self.config.stored.ArenaFlag
        if flag.total <= 0:
            logger.warning("Arena burnout: stored arena flag is unknown, fallback to daily scheduling")
            return False

        predicted = flag.predict_current()
        logger.attr("BurnoutArenaFlag", f"{predicted}/{flag.total}")

        if predicted >= flag.total:
            logger.info("Arena burnout: flags already full, run again shortly")
            self.config.task_delay(minute=self.ARENA_BURNOUT_RECHECK_MINUTES)
            return True

        target = flag.predict_reach_time(flag.total)
        logger.info(f"Arena burnout: flags {predicted} -> {flag.total}, wait until {target}")
        self.config.task_delay(target=target)
        return True

    def _arena_delay_after_run(self) -> None:
        """
        End-of-task scheduling for a finished (successful) Arena run.
        """
        if self._arena_burnout_schedule():
            return
        self.config.task_delay(server_update=True)
