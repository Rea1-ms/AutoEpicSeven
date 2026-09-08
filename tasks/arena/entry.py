from datetime import datetime, timedelta, timezone

from module.base.decorator import Config
from module.base.timer import Timer
from module.logger import logger
from tasks.arena.assets.assets_arena import (
    ARENA_CHECK,
    ARENA_COMMON_ENTRY,
    SETTLING_INFO_ICON,
    WEEKLY_REWARDS_CHECK,
    WEEKLY_REWARDS_CLAIM,
    WEEKLY_REWARDS_SELECTED,
)
from tasks.base.page import page_arena_hub


ARENA_SETTLING_TIMEZONE = timezone(timedelta(hours=8), name="UTC+8")


def is_arena_settling_period(now: datetime | None = None) -> bool:
    """Return whether `now` falls in the weekly arena settlement window."""
    if now is None:
        now = datetime.now(ARENA_SETTLING_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ARENA_SETTLING_TIMEZONE)
    else:
        now = now.astimezone(ARENA_SETTLING_TIMEZONE)

    return (now.weekday() == 6 and now.hour >= 20) or (
        now.weekday() == 0 and now.hour < 2
    )


class ArenaEntryMixin:
    ARENA_ENTRY_TIMEOUT_SECONDS = 45
    ARENA_CHECK_LUMA_SIMILARITY = 0.8
    ARENA_CHECK_COLOR_THRESHOLD = 5
    ARENA_SETTLING_CHECK_LUMA_SIMILARITY = 0.8
    ARENA_SETTLING_CHECK_COLOR_THRESHOLD = 5
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
        if ARENA_CHECK.match_template_luma(
            self.device.image, similarity=self.ARENA_CHECK_LUMA_SIMILARITY
        ):
            if ARENA_CHECK.match_color(
                self.device.image, threshold=self.ARENA_CHECK_COLOR_THRESHOLD
            ):
                appear = True

        if appear and interval:
            self.interval_reset(ARENA_CHECK, interval=interval)

        return appear

    def _is_settling_npc_page_ready(self) -> bool:
        """
        Confirm the settlement-only NPC page without accepting a dimmed frame.

        The weekly ranking reward popup may leave the page marker visible
        underneath its overlay. The color check keeps that blocked frame from
        being accepted as an actionable NPC page.
        """
        self.device.stuck_record_add(SETTLING_INFO_ICON)

        if not SETTLING_INFO_ICON.match_template_luma(
            self.device.image,
            similarity=self.ARENA_SETTLING_CHECK_LUMA_SIMILARITY,
        ):
            return False

        return SETTLING_INFO_ICON.match_color(
            self.device.image,
            threshold=self.ARENA_SETTLING_CHECK_COLOR_THRESHOLD,
        )

    def _is_arena_combat_home_ready(self) -> bool:
        return (
            self._is_arena_page_ready(interval=0) or self._is_settling_npc_page_ready()
        )

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

        Keep the routing target abstract so page-graph navigation stays local
        to the correct server shape. The follow-up click into common arena is
        still handled by arena-specific state loops because weekly rewards /
        settling branches can interrupt that last hop.

        Returns:
            str: "arena" if already inside arena, "surface" otherwise.
        """
        if self._is_arena_page_ready(interval=0):
            logger.info("Arena: already in arena page")
            return "arena"

        if self.ui_page_appear(page_arena_hub, interval=0):
            logger.info("Arena: already in arena hub")
            return "surface"

        logger.info("Arena: goto arena hub")
        self.ui_goto(page_arena_hub, skip_first_screenshot=skip_first_screenshot)
        return "surface"

    @Config.when(Emulator_PackageName="OVERSEA-Play")
    def _enter_arena_from_entry_surface(self, skip_first_screenshot=True) -> str:
        """
        arena entry is now a formal page instead of an overlay popup.

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

    def _enter_settling_arena(self, skip_first_screenshot=True) -> str:
        """
        Enter the NPC-only arena page during weekly rank settlement.

        Pages:
            in: any page
            out: settlement-only arena NPC page
        """
        if self._is_settling_npc_page_ready():
            logger.info("Arena: already in settlement NPC page")
            return "settling_npc"

        self.ui_goto(page_arena_hub, skip_first_screenshot=skip_first_screenshot)
        timeout = Timer(self.ARENA_ENTRY_TIMEOUT_SECONDS, count=180).start()
        skip_first_screenshot = True

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Arena settlement entry timeout")
                return "failed"

            if self._is_settling_npc_page_ready():
                logger.info("Arena settlement NPC page reached")
                return "settling_npc"

            if self.appear(WEEKLY_REWARDS_CHECK):
                if self._handle_weekly_rewards_popup():
                    timeout.reset()
                continue

            if self.appear_then_click(ARENA_COMMON_ENTRY, interval=1):
                logger.info("Arena hub: choose common arena during settlement")
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

    def _enter_arena(self, skip_first_screenshot=True) -> str:
        surface = self._ensure_arena_entry_surface(
            skip_first_screenshot=skip_first_screenshot
        )
        if surface == "arena":
            return "entered"
        return self._enter_arena_from_entry_surface(skip_first_screenshot=True)

    def arena_goto(self, skip_first_screenshot=True) -> str:
        """
        Route to the currently available arena surface.

        Pages:
            in: any page
            out: normal arena page or settlement-only arena NPC page
        """
        if is_arena_settling_period():
            logger.info("Arena: weekly settlement window active (UTC+8)")
            return self._enter_settling_arena(
                skip_first_screenshot=skip_first_screenshot
            )

        return self._enter_arena(skip_first_screenshot=skip_first_screenshot)
