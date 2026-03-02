from module.base.timer import Timer
from module.logger import logger
from tasks.arena.assets.assets_arena import (
    ARENA_CHECK,
    ARENA_COMMON_ENTRY,
    ARENA_ENTRY,
    ARENA_SETTLING,
    WEEKLY_REWARDS_CHECK,
    WEEKLY_REWARDS_CLAIM,
    WEEKLY_REWARDS_SELECTED,
)
from tasks.base.page import page_main
from tasks.base.ui import UI


class Arena(UI):
    """
    Arena task.

    Current scope:
        main page -> arena entry popup -> common arena entry
        handle weekly rewards popup branch before arena main page
    """

    ARENA_ENTRY_TIMEOUT_SECONDS = 45
    ARENA_ENTRY_RETRY_SECONDS = 1.2
    ARENA_CHECK_LUMA_SIMILARITY = 0.8
    ARENA_CHECK_COLOR_THRESHOLD = 5

    def _is_arena_page_ready(self, interval=0) -> bool:
        """
        ARENA_CHECK uses luma + color double check:
            avoid false-positive when weekly rewards popup overlays arena page.
        """
        self.device.stuck_record_add(ARENA_CHECK)

        if interval and not self.interval_is_reached(ARENA_CHECK, interval=interval):
            return False

        appear = False
        if ARENA_CHECK.match_template_luma(self.device.image, similarity=self.ARENA_CHECK_LUMA_SIMILARITY):
            if ARENA_CHECK.match_color(self.device.image, threshold=self.ARENA_CHECK_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(ARENA_CHECK, interval=interval)

        return appear

    def _handle_weekly_rewards_popup(self) -> bool:
        """
        Handle weekly rewards popup branch during arena entry.

        Returns:
            bool: True if an action is taken.
        """
        # Step 1: detect weekly rewards layer by selected marker.
        # Do not use interval here, otherwise the following click on the same
        # asset can be blocked by interval timer.
        if not self.appear(WEEKLY_REWARDS_SELECTED):
            self._arena_weekly_selected_clicked = False
            return False

        logger.info("Arena: weekly rewards popup detected")

        # Step 2: click selected entry once, then wait check marker.
        if not getattr(self, "_arena_weekly_selected_clicked", False):
            if self.appear_then_click(WEEKLY_REWARDS_SELECTED, interval=1):
                self._arena_weekly_selected_clicked = True
                logger.info("Arena: weekly rewards selected")
                return True
            return False

        # Step 3: verify selected state by WEEKLY_REWARDS_CHECK, then claim.
        if not self.appear(WEEKLY_REWARDS_CHECK):
            return False

        if not self.config.Arena_ClaimWeeklyRewards:
            logger.info("Arena: weekly rewards claim disabled by config")
            return True

        if self.appear_then_click(WEEKLY_REWARDS_CLAIM, interval=1):
            logger.info("Arena: weekly rewards claimed")
            return True

        return False

    def _enter_arena(self, skip_first_screenshot=True) -> str:
        logger.info("Arena: enter")
        timeout = Timer(self.ARENA_ENTRY_TIMEOUT_SECONDS, count=180).start()
        entry_retry = Timer(self.ARENA_ENTRY_RETRY_SECONDS, count=0).start()
        self._arena_weekly_selected_clicked = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Arena entry timeout")
                return "failed"

            # End condition: arena page reached.
            if self._is_arena_page_ready(interval=1):
                logger.info("Arena page reached")
                return "entered"

            # Arena maintenance/settling period.
            # Close by AD_BUFF_X_CLOSE and finish this task early.
            if self.appear(ARENA_SETTLING, interval=1):
                if self.handle_ad_buff_x_close(interval=0.5):
                    logger.info("Arena is in settling period, skip until next server update")
                    return "settling"
                continue

            # Weekly rewards branch: selected -> check -> claim.
            if self._handle_weekly_rewards_popup():
                timeout.reset()
                continue

            # Popup branch: choose common arena entry.
            if self.appear_then_click(ARENA_COMMON_ENTRY, interval=1):
                logger.info("Arena popup: choose common arena")
                timeout.reset()
                continue

            # IMPORTANT:
            # Do not call ui_additional() here, otherwise AD_BUFF_X_CLOSE may
            # close the arena mode popup before selecting common arena.
            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            # Step 1: click ARENA_ENTRY on main page.
            if self.appear(page_main.check_button) and entry_retry.reached():
                self.device.click(ARENA_ENTRY)
                entry_retry.reset()
                timeout.reset()
                logger.info("Arena: main page -> arena entry")
                continue

    def run(self) -> bool:
        logger.hr("Arena", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()

        self.ui_goto_main()
        status = self._enter_arena(skip_first_screenshot=True)

        if status == "settling":
            self.config.task_delay(server_update=True)
            return True

        if status == "entered":
            # Arena flow is currently entry-oriented. Use success interval so it
            # can run again today, instead of delaying to next server update.
            self.config.task_delay(success=True)
            return True

        self.config.task_delay(success=False)
        return False
