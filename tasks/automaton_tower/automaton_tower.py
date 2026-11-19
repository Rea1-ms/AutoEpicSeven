"""Run the monthly Automaton Tower on supported Chinese clients.

The tower is a combat-season activity, but its floor preparation and device
selection screens are separate from the normal dungeon graph.  Keep the
activity loop local so a battle result cannot be mistaken for a normal dungeon
result or start the next floor from the wrong page.
"""

from __future__ import annotations

import cv2

from module.base.button import ClickButton
from module.base.timer import Timer
from module.config import server
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Digit
from tasks.automaton_tower.assets.assets_automaton_tower import (
    AUTOMATON_TOWER_BATTLE_START,
    AUTOMATON_TOWER_CHECK,
    AUTOMATON_TOWER_CHOOSE_TEAM,
    AUTOMATON_TOWER_CLEAR,
    AUTOMATON_TOWER_DEVICE_CONFIRM,
    AUTOMATON_TOWER_DEVICE_DIALOG,
    AUTOMATON_TOWER_ENTER,
    AUTOMATON_TOWER_ENTRY,
    AUTOMATON_TOWER_FLOOR_CHECK,
    AUTOMATON_TOWER_LANDING_CHECK,
    AUTOMATON_TOWER_NEXT_FLOOR,
    AUTOMATON_TOWER_PREPARE_CHECK,
    AUTOMATON_TOWER_SPEED_1X,
    AUTOMATON_TOWER_SPEED_2X,
    OCR_FLOOR_LIST,
    OCR_FLOOR_PREPARE,
)
from tasks.automaton_tower.entry import AutomatonTowerEntry
from tasks.automaton_tower.policy import ProfileError
from tasks.automaton_tower.profile_store import ProfileStore, StaminaSettings
from tasks.automaton_tower.stamina import (
    FLOOR_STAMINA,
    LEAF_COST,
    LEAF_STAMINA,
    REFILL_AMOUNT,
    REFILL_BUY,
    REFILL_SELECT_LEAF,
    FloorStamina,
    TowerStaminaOcr,
    read_leaf_balance,
    refill_selection,
)
from tasks.base.assets.assets_base_page import BACK
from tasks.base.page import (
    page_combat_season,
)
from tasks.base.resource_bar import RESOURCE_BAR_LAYOUT_COMBAT
from tasks.dungeon.assets.assets_dungeon_action import AUTO_COMBAT, COMBAT_RESULT_LEAVE
from tasks.dungeon.assets.assets_dungeon_popup import LEIF_SELECTED
from tasks.dungeon.assets.assets_dungeon_state import (
    AUTO_COMBAT_EXIST,
    COMBAT_RESULT_CLEAR,
)
from tasks.dungeon.execute import detect_auto_combat_state
from tasks.knights.assets.assets_knights_world_boss_entry_resources import (
    WORLD_BOSS_NO_STAMINA as STAMINA_DIALOG,
)
from tasks.knights.assets.assets_knights_world_boss_entry_resources import (
    WORLD_BOSS_STAMINA_CANCEL as STAMINA_CANCEL,
)


class AutomatonTowerFloorOcr(Digit):
    """Read the white ``第N层`` label from tower navigation pages."""

    def pre_process(self, image):
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        image = cv2.threshold(image, 80, 255, cv2.THRESH_BINARY)[1]
        image = cv2.resize(
            image,
            (image.shape[1] * 3, image.shape[0] * 3),
            interpolation=cv2.INTER_NEAREST,
        )
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)


class AutomatonTower(AutomatonTowerEntry):
    """Complete all currently unlocked floors in the monthly tower."""

    FLOOR_TIMEOUT_SECONDS = 45
    BATTLE_TIMEOUT_SECONDS = 900
    COMBAT_TOGGLE_INTERVAL_SECONDS = 2
    REFILL_TIMEOUT_SECONDS = 12
    STAMINA_CANCEL_TIMEOUT_SECONDS = 5
    DEVICE_SELECT_POINT = (270, 320)
    stamina_settings = StaminaSettings()

    def _unsupported(self) -> bool:
        if server.lang in ("cn", "global_cn"):
            return False
        logger.info(f"Automaton Tower: unsupported language={server.lang}, skip")
        return True

    def _ensure_app_running(self) -> None:
        if self.device.app_is_running():
            return
        from tasks.login.login import Login

        Login(self.config, device=self.device).app_start()

    def _is_tower_page(self) -> bool:
        return (
            not self._is_entry_overlay()
            and self.appear(AUTOMATON_TOWER_CHECK, interval=0)
            and self.appear(AUTOMATON_TOWER_LANDING_CHECK, interval=0)
        )

    def _is_floor_page(self) -> bool:
        # The floor label is dynamic (for example, ``第 10 层``), so its
        # template is not a reliable page identity once the number grows to
        # two digits.  The choose-team control is fixed to the floor-list
        # page and is absent from the preparation page, which makes it a
        # stable positive fallback for every unlocked floor.
        return not self._is_entry_overlay() and (
            self.appear(AUTOMATON_TOWER_FLOOR_CHECK, interval=0)
            or self.appear(AUTOMATON_TOWER_CHOOSE_TEAM, interval=0)
        )

    def _is_prepare_page(self) -> bool:
        return not self._is_entry_overlay() and self.appear(AUTOMATON_TOWER_PREPARE_CHECK, interval=0)

    def _is_stamina_dialog(self) -> bool:
        return self.match_template_color(STAMINA_DIALOG, threshold=30)

    def _tower_stamina(self) -> int | None:
        result = self.inspect_resource_bar_status(
            RESOURCE_BAR_LAYOUT_COMBAT, "AutomatonTower", log_result=False,
        )
        stamina = (result.final or {}).get("stamina")
        return stamina.value if stamina is not None else None

    def _read_stamina_text(self, button) -> str:
        return TowerStaminaOcr(button, lang=self._ocr_lang()).ocr_single_line(self.device.image).strip()

    def _floor_stamina(self, current: int | None) -> FloorStamina | None:
        reading = FloorStamina.from_text(self._read_stamina_text(FLOOR_STAMINA))
        return reading if reading is not None and reading.current == current else None

    def _refill_stamina(self, before: FloorStamina | None) -> bool:
        settings = self.stamina_settings
        if not settings.enabled or server.lang != "global_cn":
            logger.info("Automaton Tower: insufficient stamina; leaf refill is disabled or unsupported")
            return False
        if before is None or not 0 < before.cost - before.current <= LEAF_STAMINA:
            logger.warning("Automaton Tower: cannot verify that one leaf covers the current floor")
            return False

        deadline = Timer(self.REFILL_TIMEOUT_SECONDS).start()
        select_retry = Timer(1).clear()
        select_attempts = 0
        purchase_requested = False
        while not deadline.reached():
            if self._is_stamina_dialog():
                if not purchase_requested and self.match_template_color(STAMINA_CANCEL, threshold=30):
                    selection = refill_selection(self.device.image)
                    if selection == "skystone" and select_attempts < 3 and select_retry.reached():
                        self.device.click(REFILL_SELECT_LEAF)
                        select_attempts += 1
                        select_retry.reset()
                    elif selection == "leaf" and self.match_template_color(LEIF_SELECTED, threshold=30):
                        balance = read_leaf_balance(self.device.image, self._ocr_lang())
                        if balance is not None and balance <= settings.reserve_leaves:
                            logger.info("Automaton Tower: insufficient leaves above the configured reserve")
                            return False
                        if (
                            balance is not None
                            and self._read_stamina_text(LEAF_COST) == "1"
                            and self._read_stamina_text(REFILL_AMOUNT) == str(LEAF_STAMINA)
                        ):
                            logger.info(
                                f"Automaton Tower: request {LEAF_STAMINA} stamina for one leaf (owned={balance})"
                            )
                            self.device.click(REFILL_BUY)
                            purchase_requested = True
                            deadline.reset()
            elif self._is_prepare_page() and self.match_template_color(
                AUTOMATON_TOWER_BATTLE_START, threshold=30,
            ):
                if not purchase_requested:
                    return False
                after = self._floor_stamina(self._tower_stamina())
                if (
                    after is not None and after.cost == before.cost
                    and after.current >= before.current + LEAF_STAMINA
                    and after.current >= after.cost
                ):
                    logger.info(f"Automaton Tower: leaf refill verified, stamina={after.current}")
                    return True
            self.device.screenshot()

        logger.warning("Automaton Tower: leaf refill could not be verified; no further purchase")
        return False

    def _is_device_dialog(self) -> bool:
        # The level label changes between device rounds (for example Lv.1/2
        # and Lv.2/2).  The confirmation control is popup-specific, so keep it
        # as a fallback identity instead of missing a later round because the
        # label text changed.
        return not self._is_entry_overlay() and (
            self.appear(AUTOMATON_TOWER_DEVICE_DIALOG, interval=0)
            or self.appear(AUTOMATON_TOWER_DEVICE_CONFIRM, interval=0)
        )

    def _read_floor(self, button, page_name: str) -> int | None:
        floor = AutomatonTowerFloorOcr(
            button,
            lang=self._ocr_lang(),
            name=f"AutomatonTowerFloor.{page_name}",
        ).ocr_single_line(self.device.image)
        if floor > 0:
            self.current_floor = floor
            logger.attr("AutomatonTowerFloor", floor)
            return floor

        self.current_floor = None
        logger.warning(f"Automaton Tower: {page_name} floor OCR unavailable")
        return None

    def _enter_tower(self) -> bool:
        """Enter the tower and retain whichever local state the game restores.

        Pages:
            in: main or any page reachable by ``ui_goto``
            out: tower landing, floor, preparation or a pending tower modal
        """
        logger.hr("Enter Automaton Tower", level=1)
        if (
            self._is_stamina_dialog() or self._is_entry_overlay() or self._is_tower_page() or self._is_floor_page()
            or self._is_prepare_page() or self._is_device_dialog()
        ):
            return True
        # Entering a started season may restore the floor list or its pending
        # device reward directly. Navigate to the known season container, then
        # accept those positive local destinations without sending BACK merely
        # to force the initial difficulty page to reappear. The short local
        # loop owns only this one entry edge; shared navigation stays in UI.
        self.ui_goto(page_combat_season, skip_first_screenshot=True)
        timeout = Timer(self.FLOOR_TIMEOUT_SECONDS, count=90).start()
        skip_first_screenshot = True
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                self._is_stamina_dialog() or self._is_entry_overlay() or self._is_tower_page() or self._is_floor_page()
                or self._is_prepare_page() or self._is_device_dialog()
            ):
                return True

            if timeout.reached():
                logger.warning("Automaton Tower: tower entry timeout")
                return False

            if self.ui_page_appear(page_combat_season) and self.appear_then_click(
                AUTOMATON_TOWER_ENTRY, interval=2
            ):
                continue

            if self.ui_additional():
                continue

    def _enter_floor(self, skip_first_screenshot=True) -> bool:
        """Open the current floor and the existing team.

        Pages:
            in: tower landing, floor list or a pending device choice
            out: tower preparation page
        """
        timeout = Timer(self.FLOOR_TIMEOUT_SECONDS, count=90).start()
        # A floor list can stay visible while the choose-team request is
        # retried.  Read it once per visible list and retry only when OCR
        # failed; a device dialog resets the gate because its return frame is
        # the next authoritative floor-list observation.
        floor_page_ocr = False
        stamina_logged = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self._is_stamina_dialog():
                return False

            device_dialog = self._is_device_dialog()
            if not device_dialog and self._is_prepare_page():
                self._read_floor(OCR_FLOOR_PREPARE, "prepare")
                return True

            if timeout.reached():
                logger.warning("Automaton Tower: enter floor timeout")
                return False

            if self._is_entry_overlay():
                self._handle_tower_entry()
                continue

            # Re-entering after leaving a clear result restores the unfinished
            # device choice over the floor list. Resolve it before considering
            # any controls visible behind the overlay. The child returns only
            # on a known page; process that same frame instead of losing it.
            if device_dialog:
                if not self._select_first_device():
                    return False
                floor_page_ocr = False
                skip_first_screenshot = True
                continue

            floor_page = self._is_floor_page()
            tower_page = not floor_page and self._is_tower_page()
            if not stamina_logged and (floor_page or tower_page):
                current = self._tower_stamina()
                if current is not None:
                    logger.info(f"Automaton Tower: stamina before climbing={current}")
                    stamina_logged = True

            if floor_page:
                if not floor_page_ocr:
                    floor_page_ocr = self._read_floor(OCR_FLOOR_LIST, "floor_list") is not None
                if self.appear_then_click(AUTOMATON_TOWER_CHOOSE_TEAM, interval=1):
                    continue

            if tower_page and self.match_template_color(
                AUTOMATON_TOWER_ENTER, interval=1, threshold=30
            ):
                self.device.click(AUTOMATON_TOWER_ENTER)
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
        if getattr(self, "device_policy", None) is not None:
            from tasks.automaton_tower.device_selection import DeviceSelector

            return DeviceSelector(self, self.device_policy, self.device_record).run(skip_first_screenshot)

        x, y = self.DEVICE_SELECT_POINT
        first_device = ClickButton((x, y, x + 1, y + 1), name="AUTOMATON_TOWER_FIRST_DEVICE")
        select_retry = Timer(2)
        confirm_retry = Timer(2)
        timeout = Timer(20).start()
        debug = getattr(self, "device_debug", None)
        debug_started = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self._is_entry_overlay():
                if timeout.reached():
                    if debug is not None:
                        debug.finish("timeout")
                    return False
                self._handle_tower_entry()
                continue

            device_dialog = self._is_device_dialog()
            if not device_dialog:
                destination = None
                if self._is_floor_page():
                    destination = "floor_list"
                elif self._is_prepare_page():
                    destination = "prepare"
                elif self.appear(AUTOMATON_TOWER_CLEAR, interval=0) or self.appear(
                    COMBAT_RESULT_CLEAR, interval=0
                ):
                    destination = "clear"
                if destination is not None:
                    if debug is not None:
                        debug.finish(destination, confirmed=True)
                    return True

            if timeout.reached():
                if debug is not None:
                    debug.finish("timeout")
                logger.warning("Automaton Tower: device selection timeout")
                return False

            if device_dialog and self.appear(AUTOMATON_TOWER_DEVICE_CONFIRM, interval=0):
                if debug is not None and not debug_started:
                    debug.begin(self.device.image, getattr(self, "current_floor", None), "first_card")
                    debug_started = True
                # A request is not a selection. The disabled confirmation
                # proves the choice is still pending, so the same first card
                # can be retried. A green button preserves an existing choice.
                # Observe readiness without reserving the confirm interval:
                # selection and confirmation must not delay each other's first
                # action. Retries never extend the original deadline, and
                # unknown transition frames cannot authorize either click.
                if AUTOMATON_TOWER_DEVICE_CONFIRM.match_color(self.device.image, threshold=30):
                    if confirm_retry.reached():
                        if debug is not None:
                            debug.requested(self.device.image)
                        self.device.click(AUTOMATON_TOWER_DEVICE_CONFIRM)
                        confirm_retry.reset()
                elif select_retry.reached():
                    self.device.click(first_device)
                    select_retry.reset()
                continue

            if self.ui_additional() or self.handle_popup_confirm(interval=1):
                continue

    def _run_battle(self, skip_first_screenshot=True) -> str:
        """Run one tower battle, or stop on an unresolved stamina shortage.

        Pages:
            in: tower preparation, active battle or pending device choice
            out: clear result, leave result or the last observed failed state
        """
        timeout = Timer(self.BATTLE_TIMEOUT_SECONDS, count=1800).start()
        start_requested = False
        stamina_before = None
        refilled = False
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

            if self._is_stamina_dialog():
                if not start_requested or refilled or not self._refill_stamina(stamina_before):
                    return "stamina"
                refilled = True
                start_requested = False
                start_retry.clear()
                self.interval_clear(AUTOMATON_TOWER_BATTLE_START, interval=1)
                skip_first_screenshot = True
                continue

            if self._is_entry_overlay():
                self._handle_tower_entry()
                continue

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

            if self._is_prepare_page() and (
                not start_requested or start_retry.reached()
            ) and self.match_template_color(AUTOMATON_TOWER_BATTLE_START, interval=1, threshold=30):
                current = self._tower_stamina()
                stamina_before = self._floor_stamina(current)
                if stamina_before is not None:
                    logger.info(
                        f"Automaton Tower: stamina={current}, floor cost={stamina_before.cost}, "
                        f"rough floors at this cost={stamina_before.current // stamina_before.cost}"
                    )
                    missing = stamina_before.cost - stamina_before.current
                    if missing > 0 and (
                        not self.stamina_settings.enabled or server.lang != "global_cn"
                        or missing > LEAF_STAMINA or refilled
                    ):
                        logger.info("Automaton Tower: insufficient stamina; no leaf refill available")
                        return "stamina"
                else:
                    logger.info(f"Automaton Tower: stamina={current}, floor cost or estimate unavailable")
                self.device.click(AUTOMATON_TOWER_BATTLE_START)
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
        # The result page may return to a floor list before opening the next
        # preparation page.  Avoid logging the same OCR value on every retry,
        # while still allowing a failed read to be retried on the next frame.
        floor_page_ocr = False
        skip_first_screenshot = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self._is_entry_overlay():
                if timeout.reached():
                    return "failed"
                self._handle_tower_entry()
                continue

            # The reward modal can appear after battle reports clear. Its dimmed
            # background still matches NEXT_FLOOR, so only resume background
            # actions on the fresh destination frame confirmed by the selector.
            if self._is_device_dialog():
                if not self._select_first_device():
                    return "failed"
                timeout.reset()
                skip_first_screenshot = True
                continue

            if self._is_prepare_page():
                self._read_floor(OCR_FLOOR_PREPARE, "prepare")
                return "next"

            if self.appear_then_click(AUTOMATON_TOWER_NEXT_FLOOR, interval=1):
                timeout.reset()
                continue

            # Some versions return to the floor list before the next floor is
            # opened.  Reuse the same positive floor identity and choose-team
            # action instead of clicking a stale result coordinate.
            if self._is_floor_page():
                if not floor_page_ocr:
                    floor_page_ocr = self._read_floor(OCR_FLOOR_LIST, "floor_list") is not None
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

    def _cancel_stamina_dialog(self) -> None:
        """Dismiss a visible refill prompt without leaving the tower."""
        if not self._is_stamina_dialog():
            return

        deadline = Timer(self.STAMINA_CANCEL_TIMEOUT_SECONDS).start()
        cancel_retry = Timer(3)
        while not deadline.reached():
            if self._is_stamina_dialog():
                if cancel_retry.reached() and self.match_template_color(STAMINA_CANCEL, threshold=30):
                    self.device.click(STAMINA_CANCEL)
                    cancel_retry.reset()
            elif self._is_prepare_page() or self._is_floor_page() or self._is_tower_page():
                return
            self.device.screenshot()

        logger.warning("Automaton Tower: could not confirm that the stamina dialog closed; stopping")

    def _return_to_main(self) -> bool:
        """Leave tower-local pages and confirm the main page."""
        timeout = Timer(30, count=90).start()
        while 1:
            self.device.screenshot()
            if timeout.reached():
                return False
            if self._is_stamina_dialog():
                if self.match_template_color(STAMINA_CANCEL, interval=3, threshold=30):
                    self.device.click(STAMINA_CANCEL)
                continue
            if self._is_entry_overlay():
                self._handle_tower_entry()
                continue
            if self.is_in_main(interval=0):
                return True
            if self.ui_additional() or self.handle_popup_confirm(interval=1):
                timeout.reset()
                continue
            if self.appear(BACK, interval=1):
                self.device.click(BACK)
                timeout.reset()

    def run(self) -> bool:
        """Run all unlocked tower floors in this manual session."""
        if self._unsupported():
            return False

        from tasks.automaton_tower.device_debug import DeviceDebug
        from tasks.automaton_tower.device_record import DeviceRecord

        try:
            store = ProfileStore()
            self.stamina_settings = store.stamina(self.config.config_name)
            self.device_policy = store.active(self.config.config_name)
            self.device_debug = (
                DeviceDebug(self.config.config_name, server.lang)
                if store.debug_enabled(self.config.config_name)
                else None
            )
        except ProfileError as exc:
            logger.critical(f"Automaton Tower: invalid settings: {exc}")
            raise RequestHumanTakeover(str(exc)) from exc
        if self.device_policy is not None:
            self.device_record = DeviceRecord(self.config.config_name, server.lang, self.device_policy.name)
            logger.info(f"Automaton Tower: device profile={self.device_policy.name}")
        if self.device_debug is not None:
            logger.info(f"Automaton Tower: debug captures={self.device_debug.path}")

        self._ensure_app_running()
        if getattr(self.device, "image", None) is None:
            self.device.screenshot()
        self.current_floor = None
        success = False
        human_takeover = False
        stamina_stop = False
        try:
            entered = self._enter_tower()
            if entered and self._enter_floor():
                while True:
                    floor = self.current_floor if self.current_floor is not None else "unknown"
                    logger.info(f"Automaton Tower: run floor {floor}")
                    result = self._run_battle()
                    stamina_stop = result == "stamina"
                    if result != "clear":
                        break
                    next_floor = self._next_floor()
                    if next_floor == "failed":
                        break
                    if next_floor == "finished":
                        success = True
                        break
            elif self._is_stamina_dialog():
                stamina_stop = True
        except RequestHumanTakeover:
            # Preserve the failing offer for manual inspection. Navigating BACK
            # here would hide the very card that needs a screenshot or a rule.
            human_takeover = True
            raise
        finally:
            if not human_takeover:
                if stamina_stop:
                    self._cancel_stamina_dialog()
                elif not self.is_in_main(interval=0) and not self._return_to_main():
                    success = False

        return success
