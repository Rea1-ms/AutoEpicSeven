from module.base.decorator import Config
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
from tasks.base.page import page_arena_hub, page_arena_mode_popup


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

    @Config.when(Emulator_PackageName='OVERSEA-Play')
    def _arena_entry_surface_page(self):
        return page_arena_hub

    @Config.when(Emulator_PackageName=None)
    def _arena_entry_surface_page(self):
        return page_arena_mode_popup

    @Config.when(Emulator_PackageName='OVERSEA-Play')
    def _arena_entry_surface_name(self) -> str:
        return "arena hub"

    @Config.when(Emulator_PackageName=None)
    def _arena_entry_surface_name(self) -> str:
        return "arena mode popup"

    def _ensure_arena_entry_surface(self, skip_first_screenshot=True) -> str:
        """
        Route to the arena entry boundary before running arena-specific logic.

        Arena currently has two server-specific entry surfaces:
        - CN: popup overlay
        - OVERSEA: formal hub page

        Keep the routing target abstract so page-graph navigation stays local
        to the correct server shape. The follow-up click into common arena is
        still handled by arena-specific state loops because weekly rewards /
        settling branches can interrupt that last hop.

        Returns:
            str: "arena" if already inside arena, "surface" otherwise.
        """
        surface_page = self._arena_entry_surface_page()
        surface_name = self._arena_entry_surface_name()

        if self._is_arena_page_ready(interval=0):
            logger.info("Arena: already in arena page")
            return "arena"

        if self.ui_page_appear(surface_page, interval=0):
            logger.info(f"Arena: already in {surface_name}")
            return "surface"

        logger.info(f"Arena: goto {surface_name}")
        self.ui_goto(surface_page, skip_first_screenshot=skip_first_screenshot)
        return "surface"

    @Config.when(Emulator_PackageName='OVERSEA-Play')
    def _enter_arena_from_entry_surface(self, skip_first_screenshot=True) -> str:
        """
        OVERSEA arena entry is now a formal page instead of an overlay popup.

        Important behavioral differences from the old popup flow:
        1. BACK now returns to page_main, so ui_goto() must treat this as a
           normal page in the static graph.
        2. The top-right toolbar remains active on the hub page.
        3. The final click from hub -> common arena can still be interrupted by
           weekly rewards or settling states, so it stays in a dedicated loop.
        """
        logger.info("Arena: enter from arena hub")
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

            if self.appear(WEEKLY_REWARDS_CHECK):
                if self._handle_weekly_rewards_popup():
                    timeout.reset()
                continue

            if self.appear_then_click(ARENA_COMMON_ENTRY, interval=1):
                logger.info("Arena hub: choose common arena")
                timeout.reset()
                continue

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

    @Config.when(Emulator_PackageName=None)
    def _enter_arena_from_entry_surface(self, skip_first_screenshot=True) -> str:
        logger.info("Arena: enter from arena mode popup")
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
        return self._enter_arena_from_entry_surface(skip_first_screenshot=True)
