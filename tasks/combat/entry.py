from module.base.button import ButtonWrapper
from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Digit
from tasks.base.assets.assets_base_page import BACK, MAIN_GOTO_COMBAT
from tasks.base.assets.assets_base_popup import (
    NETWORK_ERROR_ABNORMAL,
    NETWORK_ERROR_DISCONNECT,
    TOUCH_TO_CLOSE,
)
from tasks.base.resource_bar import RESOURCE_BAR_LAYOUT_COMBAT
from tasks.combat.assets import assets_combat_configs_element_altar as altar_elements
from tasks.combat.assets import assets_combat_configs_element_hunt as hunt_elements
from tasks.combat.assets.assets_combat_configs_entry import (
    ALTER_CHECK,
    COMMON_ENTRY,
    HUNT,
    HUNT_CHECK,
    OCR_SEASON_CHECK,
    SEASON_CHECK,
    SPIRIT_ALTAR,
    URGENT_TASKS,
)
from tasks.combat.assets.assets_combat_configs_popup import PACKAGE_FULL
from tasks.combat.assets.assets_combat_repeat_entry import REPEAT_COMBAT_MENU
from tasks.combat.plan import CombatPlan


class CombatDigit(Digit):
    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "")
        return super().after_process(result)


class CombatEntryMixin:
    def _is_combat_season_board(self) -> bool:
        return self.match_template_luma(SEASON_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_combat_general_board(self) -> bool:
        return (
            self.match_template_luma(SPIRIT_ALTAR, similarity=self.COMBAT_CHECK_SIMILARITY)
            or self.match_template_luma(HUNT, similarity=self.COMBAT_CHECK_SIMILARITY)
        )

    def _is_combat_urgent_board(self) -> bool:
        return self.match_template_luma(URGENT_TASKS, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_stage_page(self, plan: CombatPlan | None = None) -> bool:
        if plan is not None:
            return self.match_template_luma(plan.stage_check, similarity=self.COMBAT_CHECK_SIMILARITY)

        return (
            self.match_template_luma(ALTER_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)
            or self.match_template_luma(HUNT_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)
        )

    def _is_prepare_page(self) -> bool:
        return self.match_template_luma(REPEAT_COMBAT_MENU, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_selected_element(self, selected_button: ButtonWrapper) -> bool:
        return self.match_template_color(
            selected_button,
            similarity=self.COMBAT_CHECK_SIMILARITY,
            threshold=self.COMBAT_STATE_COLOR_THRESHOLD,
        )

    def _scroll_element_list(self) -> None:
        self.device.swipe(
            (self.COMBAT_SCROLL_X, self.COMBAT_SCROLL_START_Y),
            (self.COMBAT_SCROLL_X, self.COMBAT_SCROLL_END_Y),
            duration=(0.2, 0.3),
        )

    def _handle_combat_network_error(self, interval=1) -> bool:
        if self.appear(NETWORK_ERROR_DISCONNECT, interval=interval):
            logger.warning("Combat: network disconnected, retry")
            self.device.click(TOUCH_TO_CLOSE)
            return True

        if self.appear(NETWORK_ERROR_ABNORMAL, interval=interval):
            logger.warning("Combat: network abnormal, retry")
            self.device.click(TOUCH_TO_CLOSE)
            return True

        return False

    def _handle_combat_additional(self) -> bool:
        if self._handle_combat_network_error(interval=1):
            return True
        if self.ui_additional():
            return True
        if self.handle_popup_confirm(interval=1):
            return True
        return False

    def _raise_if_package_full(self) -> None:
        if self.appear(PACKAGE_FULL, interval=0):
            logger.critical(
                "Combat: package full detected after COMBAT_START. "
                "Please clear inventory before starting combat again."
            )
            raise RequestHumanTakeover

    def _ocr_shadow_commission_level(self) -> int:
        level = CombatDigit(
            OCR_SEASON_CHECK,
            lang=self._ocr_lang(),
            name="ShadowCommissionLevel",
        ).ocr_single_line(self.device.image)
        logger.attr("ShadowCommissionLevel", level)
        if 0 < level <= self.config.stored.E7ShadowCommission.FIXED_TOTAL:
            self.config.stored.E7ShadowCommission.set(level)
        return level

    def _update_combat_dashboard_snapshot(self, skip_first_screenshot=True) -> bool:
        updated = False
        if self.write_resource_bar_status(
            self.ocr_resource_bar_status(
                layout=RESOURCE_BAR_LAYOUT_COMBAT,
                layout_name="Combat",
                skip_first_screenshot=skip_first_screenshot,
                timeout_seconds=self.COMBAT_RESOURCE_BAR_TIMEOUT_SECONDS,
                timeout_count=self.COMBAT_RESOURCE_BAR_TIMEOUT_COUNT,
            )
        ):
            updated = True
        if self._ocr_shadow_commission_level() > 0:
            updated = True
        return updated

    def _enter_stage_page(self, plan: CombatPlan, skip_first_screenshot=True) -> bool:
        """
        Enter the stage list for the selected combat plan.

        Pages:
            in: main or combat hub pages
            out: stage page for the selected plan
        """
        logger.info(f"Combat: enter {plan.name}")
        timeout = Timer(self.COMBAT_ENTRY_TIMEOUT_SECONDS, count=80).start()
        left_prepare = False
        season_snapshot_done = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f"Combat: enter {plan.name} timeout")
                return False

            if self._is_stage_page(plan):
                return True

            if self._is_prepare_page():
                if not left_prepare:
                    logger.info("Combat: start from prepare page, back to stage for revalidation")
                    left_prepare = True
                if self.appear_then_click(BACK, interval=1):
                    timeout.reset()
                    continue

            if self._is_stage_page():
                if self.appear_then_click(BACK, interval=1):
                    timeout.reset()
                    continue

            if self._is_combat_season_board():
                if not season_snapshot_done:
                    self._update_combat_dashboard_snapshot(skip_first_screenshot=True)
                    season_snapshot_done = True
                if self.appear_then_click(COMMON_ENTRY, interval=1):
                    timeout.reset()
                    continue

            if self._is_combat_urgent_board():
                # Combat plans currently live under the common branch.
                # Do not bounce into season here when COMMON_ENTRY is briefly unstable.
                if self.appear_then_click(COMMON_ENTRY, interval=1):
                    timeout.reset()
                    continue

            if self._is_combat_general_board():
                if self.appear_then_click(plan.entry, interval=1):
                    timeout.reset()
                    continue

            if self.is_in_main(interval=0):
                if self.appear_then_click(MAIN_GOTO_COMBAT, interval=1):
                    timeout.reset()
                    continue

            if self._handle_combat_additional():
                timeout.reset()
                continue

    def _select_element(self, plan: CombatPlan, skip_first_screenshot=True) -> bool:
        element_name = self._combat_element()
        button, selected_button = plan.elements[element_name]
        logger.info(f"Combat: select element {element_name}")

        element_search = (
            hunt_elements.ELEMENT_SEARCH
            if plan.name == "Hunt"
            else altar_elements.ELEMENT_SEARCH
        )
        button.load_search(element_search.area)
        selected_button.load_search(element_search.area)

        timeout = Timer(self.COMBAT_SELECT_TIMEOUT_SECONDS, count=80).start()
        scroll_timer = Timer(self.COMBAT_SCROLL_INTERVAL_SECONDS, count=0).start()
        scroll_settle = Timer(self.COMBAT_SCROLL_SETTLE_SECONDS, count=0).clear()
        click_pending = Timer(self.COMBAT_ELEMENT_CLICK_PENDING_SECONDS, count=0).clear()
        scroll_count = 0
        selected_confirm = Timer(0.4, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f"Combat: select element {element_name} timeout")
                return False

            if self._is_prepare_page():
                return True

            if scroll_settle.started() and not scroll_settle.reached():
                continue

            if self._is_selected_element(selected_button):
                if not selected_confirm.started():
                    selected_confirm.start()
                elif selected_confirm.reached():
                    logger.info(f"Combat: selected element {element_name}")
                    return True
            else:
                selected_confirm.clear()

            if click_pending.started() and not click_pending.reached():
                continue

            if self.appear_then_click(button, interval=1):
                click_pending.reset()
                scroll_timer.reset()
                timeout.reset()
                selected_confirm.clear()
                continue

            if self._handle_combat_additional():
                click_pending.clear()
                timeout.reset()
                selected_confirm.clear()
                continue

            if scroll_timer.reached():
                if scroll_count >= self.COMBAT_MAX_SCROLLS:
                    logger.warning(f"Combat: element {element_name} not found after scrolling")
                    return False
                logger.info(f"Combat: scroll element list ({scroll_count + 1}/{self.COMBAT_MAX_SCROLLS})")
                self._scroll_element_list()
                scroll_count += 1
                scroll_timer.reset()
                scroll_settle.reset()
                click_pending.clear()
                timeout.reset()
                selected_confirm.clear()
                continue

    def _enter_prepare_page(self, plan: CombatPlan, skip_first_screenshot=True) -> bool:
        """
        Enter the prepare page for the selected grade.

        Pages:
            in: stage page
            out: prepare page
        """
        grade_name = self._combat_grade()
        grade_button = plan.grades[grade_name]
        logger.info(f"Combat: select grade {grade_name}")

        timeout = Timer(self.COMBAT_PREPARE_TIMEOUT_SECONDS, count=90).start()
        grade_pending = Timer(self.COMBAT_GRADE_PENDING_SECONDS, count=0).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f"Combat: select grade {grade_name} timeout")
                return False

            if self._is_prepare_page():
                return True

            if grade_pending.started() and not grade_pending.reached():
                continue

            if self._handle_combat_additional():
                timeout.reset()
                continue

            if self.appear_then_click(grade_button, interval=1):
                grade_pending.reset()
                timeout.reset()
                continue
