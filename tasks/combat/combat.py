from module.logger import logger
from tasks.base.page import page_combat
from tasks.base.resource_bar import ResourceBarMixin
from tasks.base.ui import UI
from tasks.combat.entry import CombatEntryMixin
from tasks.combat.execute import CombatExecuteMixin
from tasks.combat.plan import COMBAT_PLANS, HUNT_PLAN, CombatPlan
from tasks.combat.prepare import CombatPrepare
from tasks.combat.runtime import CombatRuntimeMixin


class Combat(CombatRuntimeMixin, CombatExecuteMixin, CombatEntryMixin, CombatPrepare, ResourceBarMixin, UI):
    COMBAT_RESOURCE_BAR_TIMEOUT_SECONDS = 1
    COMBAT_RESOURCE_BAR_TIMEOUT_COUNT = 2
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

    @staticmethod
    def _should_schedule_mission_reward(completed_sessions: int, runtime_active: bool) -> bool:
        """
        Only leave combat to claim mission rewards after at least one combat
        session has fully settled and no background repeat-combat session is
        still running.
        """
        return completed_sessions > 0 and not runtime_active

    @staticmethod
    def _runtime_active_after_success(event_mode: bool, use_fast_combat: bool) -> bool:
        """
        Describe the post-success runtime state before it is written to config.

        This is intentionally derived from the flow mode instead of reading the
        current runtime flag. In success branches that launch repeat combat into
        the background, runtime has not been persisted yet at the moment we
        decide whether MissionReward may interrupt the flow. Using the old
        runtime value here would falsely treat the session as settled and cause
        MissionReward/DataUpdate to steal focus while repeat combat is about to
        continue in background.
        """
        return event_mode or not use_fast_combat

    def _combat_plan(self) -> CombatPlan:
        domain = getattr(self.config, "Combat_Domain", "Hunt")
        return COMBAT_PLANS.get(domain, HUNT_PLAN)

    def _combat_mode(self) -> str:
        return getattr(self.config, "Combat_Mode", "Task")

    def _combat_is_event_mode(self) -> bool:
        return self._combat_mode() == "Event"

    def _combat_element(self) -> str:
        return getattr(self.config, "Combat_Element", "Water")

    def _combat_grade(self) -> str:
        plan = self._combat_plan()
        if plan.name == "SpiritAltar":
            return getattr(self.config, "Combat_AltarGrade", "Hell")
        return getattr(self.config, "Combat_HuntGrade", "Hell")

    def _combat_fast_enabled(self) -> bool:
        return bool(getattr(self.config, "Combat_FastCombat", True))

    def _combat_supports_fast_combat(self, plan: CombatPlan | None = None) -> bool:
        """
        Return whether the current combat target provides a fast-combat toggle.

        Dimensional Hunt uses Spectral Cores and does not expose the fast-combat
        button on the prepare page. GUI-side hiding alone is not enough because
        an old persisted config may still keep FastCombat=True. Keep this rule
        on the backend so state loops do not try to click a missing toggle and
        get stuck on the prepare page.
        """
        if plan is None:
            plan = self._combat_plan()

        return not (plan.name == "Hunt" and self._combat_grade() == "Dimensional")

    def _combat_should_use_fast(self) -> bool:
        if not self._combat_supports_fast_combat():
            return False
        if self._combat_is_event_mode():
            return True
        return self._combat_fast_enabled()

    def run(self) -> bool:
        logger.hr("Combat", level=1)
        completed_sessions = 0

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
            or self._is_combat_urgent_board()
        ):
            logger.info("Combat: detected combat context, skip goto main")
        elif not self.is_in_main(interval=0):
            # Route into the combat hub directly instead of always backing out
            # to main first. _enter_stage_page() will normalize season/common
            # boards afterwards, while menu-aware page routing can now choose
            # the shorter shared-toolbar path from other supported pages.
            self.ui_goto(page_combat, skip_first_screenshot=True)

        self._adopt_existing_background_repeat_combat()

        if self._combat_runtime_active():
            session = self._combat_runtime_session()
            session_combat_mode = session.get("combat_mode", "Task")
            logger.info("Combat: background session active, watch current session")
            logger.attr("CombatSessionMode", session_combat_mode)
            if session.get("source"):
                logger.attr("CombatSessionSource", session.get("source"))
            logger.attr("CombatSessionDomain", session.get("domain"))
            logger.attr("CombatSessionElement", session.get("element"))
            logger.attr("CombatSessionGrade", session.get("grade"))

            if not self.is_in_main(interval=0):
                # Return to main so the session can keep running in background.
                self.ui_goto_main()

            status = self._watch_repeat_combat(skip_first_screenshot=True)
            if status == "finished":
                completed_sessions += 1
                self._combat_runtime_clear()
                if session_combat_mode != "Event":
                    if self._should_schedule_mission_reward(
                        completed_sessions,
                        runtime_active=self._combat_runtime_active(),
                    ):
                        self.config.task_call("MissionReward", force_call=False)
                    self.config.task_delay(server_update=True)
                    return True
                logger.info("Combat: event background session finished, restart combat")

            if status == "lost":
                logger.warning("Combat: background session lost, relaunch combat")
                self._combat_runtime_clear()
            else:
                self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
                return True

        plan = self._combat_plan()
        combat_mode = self._combat_mode()
        event_mode = self._combat_is_event_mode()
        use_fast_combat = self._combat_should_use_fast()

        logger.attr("CombatMode", combat_mode)
        logger.attr("CombatDomain", plan.name)
        logger.attr("CombatElement", self._combat_element())
        logger.attr("CombatGrade", self._combat_grade())
        logger.attr("CombatFastCombatSupported", self._combat_supports_fast_combat(plan))
        logger.attr("CombatFastCombat", use_fast_combat)
        logger.attr("CombatFastCombatCount", self._combat_fast_count())
        logger.attr("CombatRepeatCombatCount", self._combat_repeat_count())

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
                fast_prepare = self._prepare_fast_combat(use_max=event_mode, skip_first_screenshot=True)
                if fast_prepare == "fallback":
                    use_fast_combat = False
                else:
                    success = fast_prepare == "ready"

        if success:
            if event_mode:
                if use_fast_combat:
                    success = self._run_fast_combat(skip_first_screenshot=True)
                    if success:
                        completed_sessions += 1
                    if success:
                        success = self._prepare_repeat_combat(use_max=True, skip_first_screenshot=True)
                    if success:
                        success = self._run_repeat_combat(skip_first_screenshot=True)
                else:
                    success = self._prepare_repeat_combat(use_max=True, skip_first_screenshot=True)
                    if success:
                        success = self._run_repeat_combat(skip_first_screenshot=True)
            else:
                if not use_fast_combat:
                    success = self._prepare_repeat_combat(skip_first_screenshot=True)
                if success:
                    if use_fast_combat:
                        success = self._run_fast_combat(skip_first_screenshot=True)
                        if success:
                            completed_sessions += 1
                    else:
                        success = self._run_repeat_combat(skip_first_screenshot=True)

        if success and use_fast_combat and not event_mode:
            success = self._leave_to_main(skip_first_screenshot=True)

        if success:
            runtime_active_after_success = self._runtime_active_after_success(
                event_mode=event_mode,
                use_fast_combat=use_fast_combat,
            )
            if self._should_schedule_mission_reward(
                completed_sessions,
                runtime_active=runtime_active_after_success,
            ):
                self.config.task_call("MissionReward", force_call=False)
            if event_mode:
                self._combat_runtime_set(self._combat_runtime_build(plan))
                self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
            elif use_fast_combat:
                self._combat_runtime_clear()
                self.config.task_delay(server_update=True)
            else:
                self._combat_runtime_set(self._combat_runtime_build(plan))
                self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
            return True

        self._combat_runtime_clear()
        self._leave_to_main(skip_first_screenshot=True)
        if self._should_schedule_mission_reward(
            completed_sessions,
            runtime_active=self._combat_runtime_active(),
        ):
            self.config.task_call("MissionReward", force_call=False)
        self.config.task_delay(success=False)
        return False
