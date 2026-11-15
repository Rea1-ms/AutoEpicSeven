"""Run the monthly Automaton Tower on the overseas Chinese client.

The tower is a combat-season activity, but its floor preparation and device
selection screens are separate from the normal dungeon graph.  Keep the
activity loop local so a battle result cannot be mistaken for a normal dungeon
result or send the scheduler through the wrong route.
"""

from __future__ import annotations

from module.base.button import ClickButton
from module.base.timer import Timer
from module.config import server
from module.config.utils import get_server_next_month_update
from module.logger import logger
from tasks.automaton_tower.assets.assets_automaton_tower import (
    AUTOMATON_TOWER_BATTLE_START,
    AUTOMATON_TOWER_CHECK,
    AUTOMATON_TOWER_CHOOSE_TEAM,
    AUTOMATON_TOWER_CLEAR,
    AUTOMATON_TOWER_DEVICE_CONFIRM,
    AUTOMATON_TOWER_DEVICE_DIALOG,
    AUTOMATON_TOWER_ENTER,
    AUTOMATON_TOWER_FLOOR_CHECK,
    AUTOMATON_TOWER_NEXT_FLOOR,
    AUTOMATON_TOWER_PREPARE_CHECK,
    AUTOMATON_TOWER_SPEED_1X,
    AUTOMATON_TOWER_SPEED_2X,
)
from tasks.base.assets.assets_base_page import BACK
from tasks.base.page import (
    page_automaton_tower,
)
from tasks.base.ui import UI
from tasks.dungeon.assets.assets_dungeon_action import AUTO_COMBAT, COMBAT_RESULT_LEAVE
from tasks.dungeon.assets.assets_dungeon_state import AUTO_COMBAT_EXIST, COMBAT_RESULT_CLEAR
from tasks.dungeon.execute import detect_auto_combat_state


class AutomatonTower(UI):
    """Complete all currently unlocked floors in the monthly tower."""

    FLOOR_TIMEOUT_SECONDS = 45
    BATTLE_TIMEOUT_SECONDS = 900
    COMBAT_TOGGLE_INTERVAL_SECONDS = 2
    DEVICE_SELECT_POINT = (270, 320)

    def _unsupported(self) -> bool:
        if server.lang == "global_cn":
            return False
        logger.info(f"Automaton Tower: unsupported language={server.lang}, skip")
        return True

    def _next_month_target(self):
        # Keep the task importable before the configuration generator has been
        # run on a fresh checkout.
        trigger = getattr(self.config, "Scheduler_ServerUpdate", "02:00")
        return get_server_next_month_update(trigger)

    def _ensure_app_running(self) -> None:
        if self.device.app_is_running():
            return
        from tasks.login.login import Login

        Login(self.config, device=self.device).app_start()

    def _is_tower_page(self) -> bool:
        return self.appear(AUTOMATON_TOWER_CHECK, interval=0)

    def _is_floor_page(self) -> bool:
        return self.appear(AUTOMATON_TOWER_FLOOR_CHECK, interval=0)

    def _is_prepare_page(self) -> bool:
        return self.appear(AUTOMATON_TOWER_PREPARE_CHECK, interval=0)

    def _is_device_dialog(self) -> bool:
        # The level label changes between device rounds (for example Lv.1/2
        # and Lv.2/2).  The confirmation control is popup-specific, so keep it
        # as a fallback identity instead of missing a later round because the
        # label text changed.
        return (
            self.appear(AUTOMATON_TOWER_DEVICE_DIALOG, interval=0)
            or self.appear(AUTOMATON_TOWER_DEVICE_CONFIRM, interval=0)
        )

    def _enter_tower(self) -> bool:
        """Enter the tower landing page from the combat season page.

        Pages:
            in: main or any page reachable by ``ui_goto``
            out: Automaton Tower landing page
        """
        logger.hr("Enter Automaton Tower", level=1)
        self.ui_goto(page_automaton_tower, skip_first_screenshot=True)
        return self._is_tower_page()

    def _enter_floor(self, skip_first_screenshot=True) -> bool:
        """Open the current floor and the existing team.

        Pages:
            in: tower landing, floor list or a pending device choice
            out: tower preparation page
        """
        timeout = Timer(self.FLOOR_TIMEOUT_SECONDS, count=90).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            device_dialog = self._is_device_dialog()
            if not device_dialog and self._is_prepare_page():
                return True

            if timeout.reached():
                logger.warning("Automaton Tower: enter floor timeout")
                return False

            # Re-entering after leaving a clear result restores the unfinished
            # device choice over the floor list. Resolve it before considering
            # any controls visible behind the overlay. The child returns only
            # on a known page; process that same frame instead of losing it.
            if device_dialog:
                if not self._select_first_device():
                    return False
                skip_first_screenshot = True
                continue

            if self._is_floor_page() and self.appear_then_click(AUTOMATON_TOWER_CHOOSE_TEAM, interval=1):
                continue

            if self._is_tower_page() and self.appear_then_click(AUTOMATON_TOWER_ENTER, interval=1):
                continue

            if self.ui_additional() or self.handle_popup_confirm(interval=1):
                continue

    def _select_first_device(self, skip_first_screenshot=True) -> bool:
        """Select an offered device and wait for a confirmed destination.

        The card art is intentionally not treated as a completion signal: a
        selected card remains visible.  The green confirmation button is the
        positive evidence that the selection can be committed.

        Pages:
            in: pending or already selected device dialog
            out: floor list, battle preparation or clear result
        """
        x, y = self.DEVICE_SELECT_POINT
        first_device = ClickButton((x, y, x + 1, y + 1), name="AUTOMATON_TOWER_FIRST_DEVICE")
        select_retry = Timer(2)
        confirm_retry = Timer(2)
        timeout = Timer(20).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            device_dialog = self._is_device_dialog()
            if not device_dialog and (
                self._is_floor_page()
                or self._is_prepare_page()
                or self.appear(AUTOMATON_TOWER_CLEAR, interval=0)
                or self.appear(COMBAT_RESULT_CLEAR, interval=0)
            ):
                return True

            if timeout.reached():
                logger.warning("Automaton Tower: device selection timeout")
                return False

            if device_dialog and self.appear(AUTOMATON_TOWER_DEVICE_CONFIRM, interval=0):
                # A request is not a selection. The disabled confirmation
                # proves the choice is still pending, so the same first card
                # can be retried. A green button preserves an existing choice.
                # Observe readiness without reserving the confirm interval:
                # selection and confirmation must not delay each other's first
                # action. Retries never extend the original deadline, and
                # unknown transition frames cannot authorize either click.
                if AUTOMATON_TOWER_DEVICE_CONFIRM.match_color(self.device.image, threshold=30):
                    if confirm_retry.reached():
                        self.device.click(AUTOMATON_TOWER_DEVICE_CONFIRM)
                        confirm_retry.reset()
                elif select_retry.reached():
                    self.device.click(first_device)
                    select_retry.reset()
                continue

            if self.ui_additional() or self.handle_popup_confirm(interval=1):
                continue

    def _run_battle(self, skip_first_screenshot=True) -> str:
        """Run one tower battle and return ``clear``, ``leave`` or ``failed``.

        Pages:
            in: tower preparation, active battle or pending device choice
            out: clear result, leave result or the last observed failed state
        """
        timeout = Timer(self.BATTLE_TIMEOUT_SECONDS, count=1800).start()
        start_requested = False
        start_retry = Timer(6, count=0).clear()
        auto_retry = Timer(self.COMBAT_TOGGLE_INTERVAL_SECONDS, count=0).clear()
        speed_retry = Timer(self.COMBAT_TOGGLE_INTERVAL_SECONDS, count=0).clear()

        while 1:
            if skip_first_screenshot and self.device.image is not None:
                skip_first_screenshot = False
            else:
                skip_first_screenshot = False
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Automaton Tower: battle timeout")
                return "failed"

            if self._is_device_dialog():
                if not self._select_first_device():
                    return "failed"
                timeout.reset()
                skip_first_screenshot = True
                continue

            if self.appear(AUTOMATON_TOWER_CLEAR, interval=0) or self.appear(
                COMBAT_RESULT_CLEAR, interval=0
            ):
                logger.info("Automaton Tower: floor clear")
                return "clear"

            if self.appear(COMBAT_RESULT_LEAVE, interval=0):
                logger.warning("Automaton Tower: battle result is not a clear")
                return "leave"

            if self._is_prepare_page():
                if not start_requested or start_retry.reached():
                    if self.appear_then_click(AUTOMATON_TOWER_BATTLE_START, interval=1):
                        start_requested = True
                        start_retry.reset()
                        timeout.reset()
                        continue

            # In-battle speed is separate from dungeon's instant fast combat.
            # A missing 2x icon is not proof of 1x: animations can hide both.
            # Require the visible 1x glyph and its usable colour before a
            # request, then wait for 2x or retry only the same 1x action.
            speed_2x = self.match_template_color(
                AUTOMATON_TOWER_SPEED_2X, threshold=30
            )
            speed_1x = self.match_template_color(
                AUTOMATON_TOWER_SPEED_1X, threshold=30
            )
            auto_state = detect_auto_combat_state(self.appear)

            # Device checks its 60-second guard BEFORE capturing a new frame.
            # Refresh it on positive battle HUD evidence throughout combat,
            # otherwise the eventual device dialog can fail before recognition.
            # The auto icon proves battle presence only; it is identical in
            # manual mode. Only dungeon's positive manual HUD permits toggling.
            # Unknown animation frames keep the existing guard and deadline.
            if (
                speed_1x or speed_2x or auto_state is not None
                or self.appear(AUTO_COMBAT_EXIST, interval=0)
            ):
                self.device.stuck_record_clear()

            if not speed_2x and speed_1x and speed_retry.reached():
                logger.info("Automaton Tower: enable double speed")
                self.device.click(AUTOMATON_TOWER_SPEED_1X)
                speed_retry.reset()
                continue

            # Retry intervals belong to requests, not observations. An enabled
            # HUD must not reserve the next click interval. A click itself also
            # does not latch auto on: subsequent frames still confirm the mode.
            if auto_state is False and auto_retry.reached():
                logger.info("Automaton Tower: enable automatic combat")
                self.device.click(AUTO_COMBAT)
                auto_retry.reset()
                continue

            if self.ui_additional() or self.handle_network_error():
                timeout.reset()

    def _next_floor(self) -> str:
        """Advance from a clear result and report the positively observed outcome.

        Returns:
            ``"next"`` when a new preparation page is visible,
            ``"finished"`` when the result page remains at its final state,
            ``"failed"`` when the page never reaches either state.
        """
        timeout = Timer(self.FLOOR_TIMEOUT_SECONDS, count=90).start()
        while 1:
            self.device.screenshot()

            if self._is_prepare_page():
                return "next"

            if self.appear_then_click(AUTOMATON_TOWER_NEXT_FLOOR, interval=1):
                timeout.reset()
                continue

            # Some versions return to the floor list before the next floor is
            # opened.  Reuse the same positive floor identity and choose-team
            # action instead of clicking a stale result coordinate.
            if self._is_floor_page():
                if self.appear_then_click(AUTOMATON_TOWER_CHOOSE_TEAM, interval=1):
                    timeout.reset()
                    continue

            if timeout.reached():
                # The result page exposes BACK for both an ordinary delayed
                # transition and the final floor.  Treat it as final only
                # after the full bounded wait, so a slow network cannot turn
                # a late NEXT_FLOOR button into a false success.
                if self.appear(BACK, interval=0):
                    logger.info("Automaton Tower: final floor reached")
                    return "finished"
                logger.warning("Automaton Tower: next floor timeout")
                return "failed"

            if self.ui_additional() or self.handle_popup_confirm(interval=1):
                timeout.reset()

    def _return_to_main(self) -> bool:
        """Leave tower-local pages and confirm the main page."""
        timeout = Timer(30, count=90).start()
        while 1:
            self.device.screenshot()
            if timeout.reached():
                return False
            if self.is_in_main(interval=0):
                return True
            if self.ui_additional() or self.handle_popup_confirm(interval=1):
                timeout.reset()
                continue
            if self.appear(BACK, interval=1):
                self.device.click(BACK)
                timeout.reset()

    def run(self) -> bool:
        """Run all unlocked tower floors, then schedule the next monthly check."""
        if self._unsupported():
            self.config.task_delay(target=self._next_month_target())
            return False

        self._ensure_app_running()
        success = False
        try:
            if self._enter_tower() and self._enter_floor():
                floor = 1
                while True:
                    logger.info(f"Automaton Tower: run floor {floor}")
                    result = self._run_battle()
                    if result != "clear":
                        break
                    next_floor = self._next_floor()
                    if next_floor == "failed":
                        break
                    if next_floor == "finished":
                        success = True
                        break
                    floor += 1
        finally:
            # Never leave a battle/result screen as the scheduler's next start
            # point.  The helper only clicks BACK after a positive main-page
            # check or a known tower-local control is visible.
            returned_to_main = self.is_in_main(interval=0)
            if not returned_to_main:
                returned_to_main = self._return_to_main()
            if success and returned_to_main:
                self.config.task_delay(target=self._next_month_target())
            else:
                self.config.task_delay(success=False)
                success = False

        return success
