"""Tower-local entry and recovery for shared navigation."""

import re

import cv2

from module.base.timer import Timer
from module.base.utils import crop
from module.config import server
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Digit
from tasks.automaton_tower.assets.assets_automaton_tower import (
    AUTOMATON_TOWER_BATTLE_START,
    AUTOMATON_TOWER_CHECK,
    AUTOMATON_TOWER_CHOOSE_TEAM,
    AUTOMATON_TOWER_DEVICE_CONFIRM,
    AUTOMATON_TOWER_DEVICE_DIALOG,
    AUTOMATON_TOWER_LANDING_CHECK,
    AUTOMATON_TOWER_PREPARE_CHECK,
    OCR_FLOOR_PREPARE,
)
from tasks.automaton_tower.assets.assets_automaton_tower_entry import (
    TOWER_SEASON_INFO,
    TOWER_STAGE_CONFIRM,
)
from tasks.automaton_tower.assets.assets_automaton_tower_settlement import (
    TOWER_CONQUERED,
)
from tasks.base.assets.assets_base_page import BACK
from tasks.base.page import page_combat_season, page_main
from tasks.base.ui import UI


class AutomatonTowerFloorOcr(Digit):
    """Read the white floor label on tower navigation pages."""

    def pre_process(self, image):
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        image = cv2.threshold(image, 80, 255, cv2.THRESH_BINARY)[1]
        image = cv2.resize(
            image,
            (image.shape[1] * 3, image.shape[0] * 3),
            interpolation=cv2.INTER_NEAREST,
        )
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)

    def has_floor_label(self, image) -> bool:
        # Digit.ocr_single_line would discard the surrounding characters.
        # Shared navigation needs the whole label to distinguish tower
        # preparation from ordinary stages that also display numbers.
        area = self.pre_process(crop(image, self.button.area, copy=False))
        label, _ = self.model.ocr_single_line(area)
        return re.fullmatch(r"第\s*[1-9]\d*\s*层", label.strip()) is not None


class AutomatonTowerEntry(UI):
    """Handle tower entry and hand off a verified exit to shared navigation."""

    def _is_entry_overlay(self) -> bool:
        if server.lang not in ("cn", "global_cn"):
            return False
        return self.appear(TOWER_SEASON_INFO, interval=0) or (
            server.lang == "cn" and self.appear(TOWER_STAGE_CONFIRM, interval=0)
        )

    def _handle_tower_entry(self) -> bool:
        if server.lang not in ("cn", "global_cn"):
            return False
        if self.appear_then_click(TOWER_SEASON_INFO, interval=2):
            logger.info("Automaton Tower: close season information")
            return True
        # Only the supplied CN dialog proves this confirmation's identity.
        # Its body explicitly starts the currently selected stage. Never use
        # a generic confirmation or click a stage/reset control behind it.
        if server.lang == "cn" and self.appear_then_click(TOWER_STAGE_CONFIRM, interval=2):
            logger.info("Automaton Tower: confirm currently selected stage")
            return True
        return False

    def _is_device_dialog(self) -> bool:
        # The level label changes between device rounds (for example Lv.1/2
        # and Lv.2/2). The confirmation control also identifies later rounds.
        return not self._is_entry_overlay() and (
            self.appear(AUTOMATON_TOWER_DEVICE_DIALOG)
            or self.appear(AUTOMATON_TOWER_DEVICE_CONFIRM)
        )

    def _is_prepare_page(self) -> bool:
        return not self._is_entry_overlay() and self.appear(AUTOMATON_TOWER_PREPARE_CHECK)

    def _tower_exit_state(self) -> str | None:
        if self._is_entry_overlay():
            return "season information" if self.appear(TOWER_SEASON_INFO) else "stage confirmation"
        if self._is_device_dialog():
            return "device selection"

        # Ordinary dungeon preparation can match both of the shared controls.
        # Only a full tower floor label authorizes handing that screen over.
        if self._is_prepare_page() and self.appear(AUTOMATON_TOWER_BATTLE_START):
            floor = AutomatonTowerFloorOcr(OCR_FLOOR_PREPARE, lang=self._ocr_lang())
            if floor.has_floor_label(self.device.image):
                return "preparation"
        if self.appear(AUTOMATON_TOWER_CHECK):
            if self.appear(AUTOMATON_TOWER_CHOOSE_TEAM):
                return "floor list"
            if self.appear(AUTOMATON_TOWER_LANDING_CHECK) or (
                server.lang == "global_cn" and self.match_template_color(TOWER_CONQUERED)
            ):
                return "landing"
        return None

    def leave_tower(self) -> bool:
        """Recover shared navigation from a confirmed tower screen.

        Pages:
            in: current screenshot with a possible tower screen
            out: combat season tab or main after a confirmed tower exit
        """
        if server.lang not in ("cn", "global_cn"):
            return False
        state = self._tower_exit_state()
        if state is None:
            return False

        logger.info(f"Automaton Tower: shared navigation reached tower {state}, leave to combat season")
        deadline = Timer(15).start()
        retry = Timer(2)
        previous_back_state = None
        skip_first_screenshot = True
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
                if self.ui_page_appear(page_combat_season) or self.ui_page_appear(page_main):
                    logger.info("Automaton Tower: left tower, resume shared navigation")
                    return True
                state = self._tower_exit_state()

            if state in ("stage confirmation", "device selection"):
                message = f"Automaton Tower: {state} blocks exit; preserving current screen"
                logger.warning(message)
                raise RequestHumanTakeover(message)
            if deadline.reached():
                message = "Automaton Tower: could not verify a safe exit; preserving current screen"
                logger.warning(message)
                raise RequestHumanTakeover(message)
            if state == "season information":
                self.appear_then_click(TOWER_SEASON_INFO, interval=2)
                continue
            if state is None:
                continue

            # The landing uses a different back-button tint. In the floor
            # list and preparation page, require a usable button so a dimmed
            # refill prompt cannot authorize a click on the screen behind it.
            back_visible = self.appear(BACK) if state == "landing" else self.match_template_color(BACK)
            if back_visible and (state != previous_back_state or retry.reached()):
                logger.info(f"Automaton Tower: leave {state}")
                self.device.click(BACK)
                previous_back_state = state
                retry.reset()

    def ui_page_appear(self, page, interval=0):
        # ui_goto checks its destination BEFORE calling ui_additional. A modal
        # can leave page titles visible behind it, including during a dropped
        # close click. Block that scan on every frame, independently of the
        # close action's retry interval; only a fresh usable page ends entry.
        if self._is_entry_overlay():
            return False
        return super().ui_page_appear(page, interval=interval)

    def ui_additional(self) -> bool:
        if self._is_entry_overlay():
            return self._handle_tower_entry()
        return super().ui_additional()

    def handle_popup_confirm(self, interval=2) -> bool:
        if self._is_entry_overlay():
            return False
        return super().handle_popup_confirm(interval=interval)

    def handle_popup_cancel(self, interval=2) -> bool:
        if self._is_entry_overlay():
            return False
        return super().handle_popup_cancel(interval=interval)
