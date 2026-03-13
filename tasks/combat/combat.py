from dataclasses import dataclass

from module.base.button import ButtonWrapper
from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from tasks.base.assets.assets_base_page import BACK, MAIN_GOTO_COMBAT
from tasks.base.assets.assets_base_popup import (
    NETWORK_ERROR_ABNORMAL,
    NETWORK_ERROR_DISCONNECT,
    TOUCH_TO_CLOSE,
)
from tasks.base.ui import UI
from tasks.combat.assets.assets_combat_action import COMBAT_START
from tasks.combat.assets import assets_combat_configs_element_altar as altar_elements
from tasks.combat.assets import assets_combat_configs_element_hunt as hunt_elements
from tasks.combat.assets.assets_combat_configs_entry import (
    ALTER_CHECK,
    COMMON_ENTRY,
    HUNT,
    HUNT_CHECK,
    SEASON_CHECK,
    SPIRIT_ALTAR,
)
from tasks.combat.assets import assets_combat_configs_grade_altar as altar_grades
from tasks.combat.assets import assets_combat_configs_grade_hunt as hunt_grades
from tasks.combat.assets.assets_combat_configs_popup import PACKAGE_FULL
from tasks.combat.assets.assets_combat_fast_combat import (
    FAST_COMBAT_LOCKED,
    FAST_COMBAT_OFF,
    FAST_COMBAT_ON,
    FAST_COMBAT_RESULT_CLOSE,
    FAST_COMBAT_WINDOW,
)
from tasks.combat.assets.assets_combat_repeat_result import (
    REPEAT_COMBAT_CHECK,
    REPEAT_COMBAT_OVER,
)
from tasks.combat.assets.assets_combat_repeat_entry import (
    REPEAT_COMBAT_MENU,
    REPEAT_COMBAT_OFF,
    REPEAT_COMBAT_ON,
)
from tasks.combat.assets.assets_combat_repeat_status_bar import MINIMIZE, WINDOW


@dataclass(frozen=True)
class CombatPlan:
    name: str
    entry: ButtonWrapper
    stage_check: ButtonWrapper
    elements: dict[str, tuple[ButtonWrapper, ButtonWrapper]]
    grades: dict[str, ButtonWrapper]


ALTAR_PLAN = CombatPlan(
    name="SpiritAltar",
    entry=SPIRIT_ALTAR,
    stage_check=ALTER_CHECK,
    elements={
        "Dark": (altar_elements.DARK, altar_elements.DARK_SELECTED),
        "Light": (altar_elements.LIGHT, altar_elements.LIGHT_SELECTED),
        "Water": (altar_elements.WATER, altar_elements.WATER_SELECTED),
        "Fire": (altar_elements.FIRE, altar_elements.FIRE_SELECTED),
        "Nature": (altar_elements.NATURE, altar_elements.NATURE_SELECTED),
    },
    grades={
        "Pri": altar_grades.PRI,
        "Mid": altar_grades.MID,
        "High": altar_grades.HIGH,
        "Hell": altar_grades.HELL,
    },
)

HUNT_PLAN = CombatPlan(
    name="Hunt",
    entry=HUNT,
    stage_check=HUNT_CHECK,
    elements={
        "Dark": (hunt_elements.DARK, hunt_elements.DARK_SELECTED),
        "Light": (hunt_elements.LIGHT, hunt_elements.LIGHT_SELECTED),
        "Water": (hunt_elements.WATER, hunt_elements.WATER_SELECTED),
        "Fire": (hunt_elements.FIRE, hunt_elements.FIRE_SELECTED),
        "Nature": (hunt_elements.NATURE, hunt_elements.NATURE_SELECTED),
    },
    grades={
        "Mid": hunt_grades.MID,
        "High": hunt_grades.HIGH,
        "Hell": hunt_grades.HELL,
        "Dimensional": hunt_grades.DIMENSIONAL,
    },
)

COMBAT_PLANS = {
    "SpiritAltar": ALTAR_PLAN,
    "Hunt": HUNT_PLAN,
}


class Combat(UI):
    COMBAT_RUNTIME_PATH = "Combat.CombatRuntime.Session"
    COMBAT_CHECK_SIMILARITY = 0.8
    COMBAT_STATE_COLOR_THRESHOLD = 30
    COMBAT_ENTRY_TIMEOUT_SECONDS = 25
    COMBAT_SELECT_TIMEOUT_SECONDS = 20
    COMBAT_PREPARE_TIMEOUT_SECONDS = 25
    COMBAT_RUN_TIMEOUT_SECONDS = 90
    COMBAT_WATCH_TIMEOUT_SECONDS = 20
    COMBAT_MISSING_CHECK_CONFIRM_SECONDS = 1
    COMBAT_EXIT_TIMEOUT_SECONDS = 25
    COMBAT_SCROLL_INTERVAL_SECONDS = 1
    COMBAT_SCROLL_SETTLE_SECONDS = 1.5
    COMBAT_ELEMENT_CLICK_PENDING_SECONDS = 1.2
    COMBAT_BACKGROUND_CHECK_MINUTES = 1
    COMBAT_GRADE_PENDING_SECONDS = 2.5
    COMBAT_START_PENDING_SECONDS = 4.5
    COMBAT_TOGGLE_INTERVAL_SECONDS = 1
    COMBAT_START_INTERVAL_SECONDS = 1.2
    COMBAT_RESULT_INTERVAL_SECONDS = 1
    COMBAT_MAX_SCROLLS = 6
    COMBAT_SCROLL_X = 1100
    COMBAT_SCROLL_START_Y = 620
    COMBAT_SCROLL_END_Y = 220

    def _combat_plan(self) -> CombatPlan:
        domain = getattr(self.config, "Combat_Domain", "Hunt")
        return COMBAT_PLANS.get(domain, HUNT_PLAN)

    def _combat_element(self) -> str:
        return getattr(self.config, "Combat_Element", "Water")

    def _combat_grade(self) -> str:
        plan = self._combat_plan()
        if plan.name == "SpiritAltar":
            return getattr(self.config, "Combat_AltarGrade", "Hell")
        return getattr(self.config, "Combat_HuntGrade", "Hell")

    def _combat_fast_enabled(self) -> bool:
        return bool(getattr(self.config, "Combat_FastCombat", True))

    def _combat_runtime_session(self) -> dict:
        session = self.config.cross_get(self.COMBAT_RUNTIME_PATH, default={})
        return session if isinstance(session, dict) else {}

    def _combat_runtime_active(self) -> bool:
        return bool(self._combat_runtime_session().get("active"))

    def _combat_runtime_set(self, session: dict) -> None:
        self.config.cross_set(self.COMBAT_RUNTIME_PATH, session)

    def _combat_runtime_clear(self) -> None:
        self._combat_runtime_set({})

    def _combat_runtime_build(self, plan: CombatPlan) -> dict:
        return {
            "active": True,
            "mode": "repeat_background",
            "domain": plan.name,
            "element": self._combat_element(),
            "grade": self._combat_grade(),
        }

    def _combat_runtime_build_detected_existing(self) -> dict:
        return {
            "active": True,
            "mode": "repeat_background",
            "source": "detected_existing",
        }

    def _is_combat_season_board(self) -> bool:
        return self.match_template_luma(SEASON_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_combat_general_board(self) -> bool:
        return (
            self.match_template_luma(SPIRIT_ALTAR, similarity=self.COMBAT_CHECK_SIMILARITY)
            or self.match_template_luma(HUNT, similarity=self.COMBAT_CHECK_SIMILARITY)
        )

    def _is_stage_page(self, plan: CombatPlan | None = None) -> bool:
        if plan is not None:
            return self.match_template_luma(plan.stage_check, similarity=self.COMBAT_CHECK_SIMILARITY)

        return (
            self.match_template_luma(ALTER_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)
            or self.match_template_luma(HUNT_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)
        )

    def _is_prepare_page(self) -> bool:
        return self.match_template_luma(REPEAT_COMBAT_MENU, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_fast_combat_locked(self) -> bool:
        return self.match_template_luma(FAST_COMBAT_LOCKED, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_fast_combat_on(self) -> bool:
        return self.match_template_luma(FAST_COMBAT_ON, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_fast_combat_off(self) -> bool:
        if self._is_fast_combat_locked():
            return True
        return self.match_template_luma(FAST_COMBAT_OFF, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_repeat_combat_on(self) -> bool:
        return self.match_color(REPEAT_COMBAT_ON, threshold=self.COMBAT_STATE_COLOR_THRESHOLD)

    def _is_repeat_result_window(self) -> bool:
        return self.match_template_luma(WINDOW, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_repeat_combat_over(self) -> bool:
        return self.match_template_luma(REPEAT_COMBAT_OVER, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _has_repeat_combat_check(self) -> bool:
        return self.match_template_luma(REPEAT_COMBAT_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_repeat_combat_running(self) -> bool:
        if self._is_repeat_combat_over() or self._is_repeat_result_window():
            return False
        return self._has_repeat_combat_check()

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

    def _adopt_existing_background_repeat_combat(self) -> bool:
        if self._combat_runtime_active():
            return False
        if not self.is_in_main(interval=0):
            return False
        if not self._is_background_repeat_combat_running():
            return False

        logger.info("Combat: detected existing background repeat combat before task start")
        self._combat_runtime_set(self._combat_runtime_build_detected_existing())
        return True

    def _enter_stage_page(self, plan: CombatPlan, skip_first_screenshot=True) -> bool:
        logger.info(f"Combat: enter {plan.name}")
        timeout = Timer(self.COMBAT_ENTRY_TIMEOUT_SECONDS, count=80).start()
        left_prepare = False

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

    def _ensure_fast_combat_state(self, enabled: bool) -> bool:
        if self._is_fast_combat_locked():
            return not enabled

        if enabled:
            if self._is_fast_combat_on():
                return True
            if self.interval_is_reached(FAST_COMBAT_OFF, interval=self.COMBAT_TOGGLE_INTERVAL_SECONDS):
                logger.info("Combat: enable fast combat")
                self.device.click(FAST_COMBAT_OFF)
                self.interval_reset(FAST_COMBAT_OFF, interval=self.COMBAT_TOGGLE_INTERVAL_SECONDS)
            return False

        if self._is_fast_combat_off():
            return True
        if self.interval_is_reached(FAST_COMBAT_OFF, interval=self.COMBAT_TOGGLE_INTERVAL_SECONDS):
            logger.info("Combat: disable fast combat")
            self.device.click(FAST_COMBAT_OFF)
            self.interval_reset(FAST_COMBAT_OFF, interval=self.COMBAT_TOGGLE_INTERVAL_SECONDS)
        return False

    def _ensure_repeat_combat_enabled(self) -> bool:
        if self._is_repeat_combat_on():
            return True
        if self.appear_then_click(REPEAT_COMBAT_OFF, interval=1):
            logger.info("Combat: enable repeat combat")
            return False
        return False

    def _run_fast_combat(self, skip_first_screenshot=True) -> bool:
        logger.info("Combat: run fast combat")
        timeout = Timer(self.COMBAT_RUN_TIMEOUT_SECONDS, count=240).start()
        stage = "prepare"
        start_pending = Timer(self.COMBAT_START_PENDING_SECONDS, count=0).clear()
        prepare_confirm = Timer(0.4, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: fast combat timeout")
                return False

            self._raise_if_package_full()

            if self._handle_combat_additional():
                timeout.reset()
                continue

            if stage == "prepare":
                if not self._ensure_fast_combat_state(enabled=True):
                    timeout.reset()
                    continue

                if self.appear_then_click(COMBAT_START, interval=self.COMBAT_START_INTERVAL_SECONDS):
                    logger.info("Combat: start fast combat")
                    stage = "pending"
                    start_pending.reset()
                    timeout.reset()
                    continue
                continue

            if stage == "pending":
                if self.appear_then_click(FAST_COMBAT_RESULT_CLOSE, interval=self.COMBAT_RESULT_INTERVAL_SECONDS):
                    stage = "result"
                    timeout.reset()
                    continue

                if self.appear(FAST_COMBAT_WINDOW) or self.appear(FAST_COMBAT_RESULT_CLOSE):
                    stage = "result"
                    timeout.reset()
                    continue

                if start_pending.reached() and self._is_prepare_page():
                    logger.info("Combat: fast combat start pending timeout, retry")
                    stage = "prepare"
                    timeout.reset()
                    continue
                continue

            if stage == "result":
                if self.appear_then_click(FAST_COMBAT_RESULT_CLOSE, interval=self.COMBAT_RESULT_INTERVAL_SECONDS):
                    timeout.reset()
                    continue

                if self.appear(FAST_COMBAT_WINDOW):
                    timeout.reset()
                    continue

                if self._is_prepare_page():
                    if not prepare_confirm.started():
                        prepare_confirm.start()
                    elif prepare_confirm.reached():
                        logger.info("Combat: fast combat finished")
                        return True
                else:
                    prepare_confirm.clear()
                continue

    def _run_repeat_combat(self, skip_first_screenshot=True) -> bool:
        logger.info("Combat: run repeat combat")
        timeout = Timer(self.COMBAT_RUN_TIMEOUT_SECONDS, count=240).start()
        stage = "prepare"
        start_pending = Timer(self.COMBAT_START_PENDING_SECONDS, count=0).clear()
        main_confirm = Timer(0.4, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: repeat combat timeout")
                return False

            self._raise_if_package_full()

            if self._handle_combat_additional():
                timeout.reset()
                continue

            if stage == "prepare":
                if not self._ensure_fast_combat_state(enabled=False):
                    timeout.reset()
                    continue

                if not self._ensure_repeat_combat_enabled():
                    timeout.reset()
                    continue

                if self.appear_then_click(COMBAT_START, interval=self.COMBAT_START_INTERVAL_SECONDS):
                    logger.info("Combat: start repeat combat")
                    stage = "pending"
                    start_pending.reset()
                    timeout.reset()
                    continue
                continue

            if stage == "pending":
                if self.appear_then_click(MINIMIZE, interval=1):
                    stage = "background"
                    logger.info("Combat: minimize repeat combat")
                    timeout.reset()
                    continue

                if start_pending.reached() and self._is_prepare_page():
                    logger.info("Combat: repeat combat start pending timeout, retry")
                    stage = "prepare"
                    timeout.reset()
                    continue
                continue

            if stage == "background":
                if self.appear_then_click(MINIMIZE, interval=1):
                    timeout.reset()
                    continue
                if self.is_in_main(interval=0) and self._is_repeat_combat_running():
                    if not main_confirm.started():
                        main_confirm.start()
                    elif main_confirm.reached():
                        logger.info("Combat: repeat combat running in background")
                        return True
                else:
                    main_confirm.clear()
                continue

    def _watch_repeat_combat(self, skip_first_screenshot=True) -> str:
        logger.info("Combat: watch repeat combat")
        timeout = Timer(self.COMBAT_WATCH_TIMEOUT_SECONDS, count=60).start()
        stage = "watch"
        result_main_confirm = Timer(0.4, count=2).clear()
        missing_check_confirm = Timer(self.COMBAT_MISSING_CHECK_CONFIRM_SECONDS, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: repeat combat watch timeout, keep session active")
                return "running"

            if self._handle_combat_network_error(interval=1):
                logger.warning("Combat: background repeat combat has network error, keep session active")
                return "running"

            if stage == "watch":
                if self.appear_then_click(REPEAT_COMBAT_OVER, interval=1):
                    logger.info("Combat: repeat combat over, open result")
                    stage = "result"
                    missing_check_confirm.clear()
                    timeout.reset()
                    continue

                if self._is_repeat_result_window():
                    stage = "result"
                    missing_check_confirm.clear()
                    timeout.reset()
                    continue

                if self._is_repeat_combat_running():
                    missing_check_confirm.clear()
                    logger.info("Combat: repeat combat still running in background")
                    return "running"

                if self.is_in_main(interval=0):
                    if not missing_check_confirm.started():
                        logger.info("Combat: repeat combat check missing once, confirm again")
                        missing_check_confirm.start()
                    elif missing_check_confirm.reached():
                        logger.warning("Combat: repeat combat session active but check is missing")
                        return "lost"
                else:
                    missing_check_confirm.clear()

                if self._handle_combat_additional():
                    missing_check_confirm.clear()
                    timeout.reset()
                    continue
                continue

            if stage == "result":
                if self.appear_then_click(REPEAT_COMBAT_OVER, interval=1):
                    timeout.reset()
                    continue

                if self._is_repeat_result_window():
                    if self.handle_ad_buff_x_close(interval=0.5):
                        logger.info("Combat: close repeat combat result")
                        stage = "finish"
                        timeout.reset()
                        result_main_confirm.clear()
                        continue
                    timeout.reset()
                    continue

                if self.handle_ad_buff_x_close(interval=0.5):
                    logger.info("Combat: close repeat combat result")
                    stage = "finish"
                    timeout.reset()
                    result_main_confirm.clear()
                    continue

                if self._handle_combat_additional():
                    timeout.reset()
                    continue
                continue

            if stage == "finish":
                if self._is_repeat_result_window():
                    if self.handle_ad_buff_x_close(interval=0.5):
                        timeout.reset()
                        continue
                    timeout.reset()
                    continue

                if self.is_in_main(interval=0):
                    if not result_main_confirm.started():
                        result_main_confirm.start()
                    elif result_main_confirm.reached():
                        logger.info("Combat: repeat combat finished")
                        return "finished"
                else:
                    result_main_confirm.clear()

                if self._handle_combat_additional():
                    timeout.reset()
                    continue
                continue

    def _leave_to_main(self, skip_first_screenshot=True) -> bool:
        logger.info("Combat: return to main")
        timeout = Timer(self.COMBAT_EXIT_TIMEOUT_SECONDS, count=80).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: return to main timeout")
                return False

            if self.is_in_main(interval=0):
                return True

            if self._handle_combat_additional():
                timeout.reset()
                continue

            if (
                self._is_prepare_page()
                or self._is_stage_page()
                or self._is_combat_general_board()
                or self._is_combat_season_board()
            ):
                if self.appear_then_click(BACK, interval=1):
                    timeout.reset()
                    continue

    def run(self) -> bool:
        logger.hr("Combat", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()

        if (
            self._is_prepare_page()
            or self._is_stage_page()
            or self._is_combat_general_board()
            or self._is_combat_season_board()
        ):
            logger.info("Combat: detected combat context, skip goto main")
        elif not self.is_in_main(interval=0):
            self.ui_goto_main()

        self._adopt_existing_background_repeat_combat()

        if self._combat_runtime_active():
            session = self._combat_runtime_session()
            logger.info("Combat: background session active, watch current session")
            if session.get("source"):
                logger.attr("CombatSessionSource", session.get("source"))
            logger.attr("CombatSessionDomain", session.get("domain"))
            logger.attr("CombatSessionElement", session.get("element"))
            logger.attr("CombatSessionGrade", session.get("grade"))

            if not self.is_in_main(interval=0):
                self.ui_goto_main()

            status = self._watch_repeat_combat(skip_first_screenshot=True)
            if status == "finished":
                self._combat_runtime_clear()
                self.config.task_delay(server_update=True)
                return True

            if status == "lost":
                logger.warning("Combat: background session lost, relaunch combat")
                self._combat_runtime_clear()
            else:
                self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
                return True

        plan = self._combat_plan()
        use_fast_combat = self._combat_fast_enabled()

        logger.attr("CombatDomain", plan.name)
        logger.attr("CombatElement", self._combat_element())
        logger.attr("CombatGrade", self._combat_grade())
        logger.attr("CombatFastCombat", use_fast_combat)

        success = self._enter_stage_page(plan, skip_first_screenshot=True)
        if success:
            success = self._select_element(plan, skip_first_screenshot=True)
        if success:
            success = self._enter_prepare_page(plan, skip_first_screenshot=True)

        if success and use_fast_combat and self._is_fast_combat_locked():
            logger.warning("Combat: fast combat locked, fallback to repeat combat")
            use_fast_combat = False

        if success:
            if use_fast_combat:
                success = self._run_fast_combat(skip_first_screenshot=True)
            else:
                success = self._run_repeat_combat(skip_first_screenshot=True)

        if success and use_fast_combat:
            success = self._leave_to_main(skip_first_screenshot=True)

        if success:
            if use_fast_combat:
                self._combat_runtime_clear()
                self.config.task_delay(server_update=True)
            else:
                self._combat_runtime_set(self._combat_runtime_build(plan))
                self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
            return True

        self._combat_runtime_clear()
        self._leave_to_main(skip_first_screenshot=True)
        self.config.task_delay(success=False)
        return False
