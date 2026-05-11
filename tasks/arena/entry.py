from module.base.timer import Timer
from module.logger import logger
from tasks.arena.assets.assets_arena import (
    ARENA_CHECK,
    ARENA_COMMON_ENTRY,
    ARENA_SETTLING,
    WEEKLY_REWARDS_CHECK,
    WEEKLY_REWARDS_CLAIM,
    WEEKLY_REWARDS_SELECTED,
)
from tasks.base.page import page_arena_mode_popup


class ArenaEntryMixin:
    ARENA_ENTRY_TIMEOUT_SECONDS = 45
    ARENA_CHECK_LUMA_SIMILARITY = 0.8
    ARENA_CHECK_COLOR_THRESHOLD = 5
    ARENA_WEEKLY_REWARDS_SELECT_INTERVAL_SECONDS = 1
    ARENA_WEEKLY_REWARDS_CLAIM_INTERVAL_SECONDS = 1
    ARENA_WEEKLY_REWARDS_CLAIM_SIMILARITY = 0.85
    ARENA_WEEKLY_REWARDS_CLAIM_COLOR_THRESHOLD = 30

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
        Handle the weekly rewards popup that blocks arena entry.

        Popup semantics are easy to mix up after asset refreshes, so keep the
        state rules explicit here:
            1. `WEEKLY_REWARDS_CHECK` means the popup is active, and the reward
               entry can be clicked when the reward is not selected yet.
            2. `WEEKLY_REWARDS_SELECTED` only marks that the reward has already
               been selected. It is not the click target anymore.
            3. `WEEKLY_REWARDS_CLAIM` must use template-plus-color matching so
               the loop only clicks the bright enabled button, and naturally
               ignores the grey disabled state after claim is consumed.

        Returns:
            bool: True if an action is taken and caller should refresh frame.
        """
        if not self.appear(WEEKLY_REWARDS_SELECTED):
            if self.appear_then_click(
                WEEKLY_REWARDS_CHECK,
                interval=self.ARENA_WEEKLY_REWARDS_SELECT_INTERVAL_SECONDS,
            ):
                logger.info("Arena: weekly rewards select reward")
                return True

        if self.match_template_color(
            WEEKLY_REWARDS_CLAIM,
            interval=self.ARENA_WEEKLY_REWARDS_CLAIM_INTERVAL_SECONDS,
            similarity=self.ARENA_WEEKLY_REWARDS_CLAIM_SIMILARITY,
            threshold=self.ARENA_WEEKLY_REWARDS_CLAIM_COLOR_THRESHOLD,
        ):
            self.device.click(WEEKLY_REWARDS_CLAIM)
            logger.info("Arena: weekly rewards claim selected reward")
            return True

        return False

    def _ensure_arena_entry_surface(self, skip_first_screenshot=True) -> str:
        """
        Route to the arena entry boundary before running arena-specific logic.

        Arena uses a mode-selection popup as the real entry surface:
        - route switching should only care about reaching this popup
        - the popup -> common arena transition stays inside arena state logic

        Returns:
            str: "arena" if already inside arena, "popup" otherwise.
        """
        if self._is_arena_page_ready(interval=0):
            logger.info("Arena: already in arena page")
            return "arena"

        if self.ui_page_appear(page_arena_mode_popup, interval=0):
            logger.info("Arena: already in arena mode popup")
            return "popup"

        logger.info("Arena: goto arena entry surface")
        self.ui_goto(page_arena_mode_popup, skip_first_screenshot=skip_first_screenshot)
        return "popup"

    def _enter_arena_from_popup(self, skip_first_screenshot=True) -> str:
        logger.info("Arena: enter from arena entry surface")
        timeout = Timer(self.ARENA_ENTRY_TIMEOUT_SECONDS, count=180).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Arena entry timeout")
                return "failed"

            if self._is_arena_page_ready(interval=1):
                logger.info("Arena page reached")
                return "entered"

            if self.appear(ARENA_SETTLING, interval=1):
                if self.handle_ad_buff_x_close(interval=0.5):
                    logger.info("Arena is in settling period, skip until next server update")
                    return "settling"
                continue

            # Weekly rewards popup must block all arena-entry clicks until the
            # branch is fully resolved. Otherwise the overlay can keep the
            # common-arena entry visible while still intercepting clicks.
            if self.appear(WEEKLY_REWARDS_CHECK):
                if self._handle_weekly_rewards_popup():
                    timeout.reset()
                continue

            if self.appear_then_click(ARENA_COMMON_ENTRY, interval=1):
                logger.info("Arena popup: choose common arena")
                timeout.reset()
                continue

            # Do not call ui_additional() here, otherwise AD_BUFF_X_CLOSE may
            # close the arena mode popup before selecting common arena.
            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

    def _enter_arena(self, skip_first_screenshot=True) -> str:
        surface = self._ensure_arena_entry_surface(skip_first_screenshot=skip_first_screenshot)
        if surface == "arena":
            return "entered"
        return self._enter_arena_from_popup(skip_first_screenshot=True)
