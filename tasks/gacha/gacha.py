"""
Epic Seven 召唤模块

流程:
    MAIN_GOTO_GACHA -> 进入召唤页 -> 选择常驻池
    优先点击免费10抽，其次免费1抽
    单抽流程:
        SUMMON_NEW -> 截图 + OCR 名称 + 点击去遮罩
        SUMMON_NEXT_PAGE -> 截图 + OCR 名称 + 翻页
        SUMMON_SKIP
        SUMMON_RESULT_BACK + SUMMON_FREE_CONTINUE -> 截图 + OCR 名称 -> 点击继续
        SUMMON_RESULT_BACK -> 截图 + OCR 名称 -> 返回召唤页
    十连结果:
        等待自动传送结束 -> 保存最终整页 -> OCR 未传送卡位 -> 点击继续或返回
"""
from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_gacha
from tasks.base.ui import UI
from tasks.gacha.assets.assets_gacha import (
    EPIC_BOOKMARK,
    GACHA_STANDARD_TAB,
    GOLDEN_INHERITANCE_FULL,
    SUMMON_FREE_UNAVAILABLE,
    SUMMON_TEN_FREE,
    SUMMON_ONE_FREE,
    SUMMON_NEW,
    SUMMON_SKIP,
    SUMMON_NEXT_PAGE,
    SUMMON_RESULT_BACK,
    SUMMON_FREE_CONTINUE,
)
from tasks.gacha.result import (
    SummonResultCaptureGate,
    SummonResultRecorder,
    TenPullResultCollector,
)
from tasks.mission_reward.scheduling import should_schedule_mission_reward


class Gacha(SummonResultRecorder, UI):
    """
    召唤任务（常驻池）
    """

    TAB_SWIPE_START = (105, 600)
    TAB_SWIPE_END = (105, 300)
    GOLDEN_INHERITANCE_TIMEOUT_SECONDS = 6

    @staticmethod
    def _should_schedule_mission_reward_after_free_summon(free_summon_completed: bool) -> bool:
        return bool(free_summon_completed)

    def __init__(self, config, device=None, task=None):
        super().__init__(config, device=device, task=task)
        self._draw_count = 0
        self._draw_free = False
        self._in_standard_pool = False
        self._no_free = False

    def _enter_gacha(self):
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        self.ui_goto(page_gacha)

    def _select_standard_tab(self) -> bool:
        logger.info("Select standard tab")
        swipe_timer = Timer(1, count=2).start()
        timeout = Timer(15, count=30).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Select standard tab timeout")
                return False

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

            if not self.ui_page_appear(page_gacha):
                self.ui_goto(page_gacha)
                timeout.reset()
                continue

            if self.appear(EPIC_BOOKMARK, interval=1, similarity=0.8):
                self._in_standard_pool = True
                return True

            if self.appear_then_click(GACHA_STANDARD_TAB, interval=2):
                continue

            if swipe_timer.reached():
                self.device.swipe(self.TAB_SWIPE_START, self.TAB_SWIPE_END, duration=(0.25, 0.35))
                swipe_timer.reset()
                continue

    def _start_summon(self) -> bool:
        logger.info("Start summon")
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Start summon timeout")
                return False

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

            if not self.ui_page_appear(page_gacha):
                self.ui_goto(page_gacha)
                timeout.reset()
                continue

            if self.appear(EPIC_BOOKMARK, interval=1, similarity=0.8):
                self._in_standard_pool = True

            if not self._in_standard_pool:
                if not self._select_standard_tab():
                    return False
                continue

            if self.appear(SUMMON_FREE_UNAVAILABLE):
                logger.info("Free summon already used today, skip")
                self._no_free = True
                return False

            if self.appear_then_click(SUMMON_TEN_FREE, interval=2, similarity=0.9):
                self._draw_count = 10
                self._draw_free = True
                return True

            if self.appear_then_click(SUMMON_ONE_FREE, interval=2, similarity=0.9):
                self._draw_count = 1
                self._draw_free = True
                return True

    def _handle_summon_flow(self):
        logger.info("Summon flow")
        timeout = Timer(120, count=240).start()
        capture = SummonResultCaptureGate()
        ten_pull_collector = None
        pending_draw_count = None
        returning_to_gacha = False

        # Slow down screenshot interval during animation
        self.device.screenshot_interval_set(1.0)
        try:
            while 1:
                self.device.screenshot()

                if timeout.reached():
                    logger.warning("Summon flow timeout")
                    break

                if returning_to_gacha and self.ui_page_appear(page_gacha):
                    break

                new = self.appear(SUMMON_NEW)
                skip = self.appear(SUMMON_SKIP)
                next_page = (
                    SUMMON_NEXT_PAGE is not None
                    and self.appear(SUMMON_NEXT_PAGE)
                )
                back = self.appear(SUMMON_RESULT_BACK)
                free_continue = self.appear(SUMMON_FREE_CONTINUE)

                transition_completed = capture.observe_transition(
                    next_result_visible=skip or new,
                    previous_result_visible=next_page or back,
                )
                if transition_completed and pending_draw_count is not None:
                    self._draw_count = pending_draw_count
                    pending_draw_count = None
                    ten_pull_collector = None
                    logger.attr("SummonResultCount", self._draw_count)

                # 1) New overlay
                if new and self.interval_is_reached(SUMMON_NEW, interval=1):
                    capture.save_once(self, tag="new")
                    self.device.click(SUMMON_NEW)
                    self.interval_reset(SUMMON_NEW, interval=1)
                    continue

                # 2) Skip animation
                if skip and self.interval_is_reached(SUMMON_SKIP, interval=1):
                    self.device.click(SUMMON_SKIP)
                    self.interval_reset(SUMMON_SKIP, interval=1)
                    continue

                # 3) Next page (optional)
                if next_page and self.interval_is_reached(SUMMON_NEXT_PAGE, interval=1):
                    capture.save_once(self, tag="result")
                    self.device.click(SUMMON_NEXT_PAGE)
                    self.interval_reset(SUMMON_NEXT_PAGE, interval=1)
                    capture.mark_advance_requested()
                    continue

                # 4) Result page
                if back and free_continue:
                    if self._draw_count == 10 and self._result_recording_enabled():
                        if ten_pull_collector is None:
                            ten_pull_collector = TenPullResultCollector()
                        if not ten_pull_collector.observe(self.device.image):
                            continue
                        ten_pull_collector.save_once(self)
                    else:
                        capture.save_once(self, tag="result")
                    if pending_draw_count is None:
                        pending_draw_count = self._read_next_free_summon_count(
                            self.device.image
                        )
                        if pending_draw_count is None:
                            continue
                    if self.interval_is_reached(SUMMON_FREE_CONTINUE, interval=1):
                        self.device.click(SUMMON_FREE_CONTINUE)
                        self.interval_reset(SUMMON_FREE_CONTINUE, interval=1)
                        capture.mark_advance_requested()
                        timeout.reset()
                    continue
                if back:
                    if self._draw_count == 10 and self._result_recording_enabled():
                        if ten_pull_collector is None:
                            ten_pull_collector = TenPullResultCollector()
                        if not ten_pull_collector.observe(self.device.image):
                            continue
                        ten_pull_collector.save_once(self)
                    else:
                        capture.save_once(self, tag="result")
                    if self.interval_is_reached(SUMMON_RESULT_BACK, interval=2):
                        self.device.click(SUMMON_RESULT_BACK)
                        self.interval_reset(SUMMON_RESULT_BACK, interval=2)
                        returning_to_gacha = True
                        timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue
                if self.handle_network_error():
                    timeout.reset()
                    continue

        finally:
            self.device.screenshot_interval_set()

    def _wait_return_to_gacha(self):
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()
            if timeout.reached():
                break
            if self.ui_page_appear(page_gacha):
                break
            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    def _collect_golden_inheritance_full(self, skip_first_screenshot=True) -> bool:
        if not getattr(self.config, "Gacha_CollectGoldenInheritance", True):
            return False

        logger.info("Collect golden inheritance if full")
        timeout = Timer(self.GOLDEN_INHERITANCE_TIMEOUT_SECONDS, count=18).start()
        no_action_confirm = Timer(1.5, count=4).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                return False

            if self.appear_then_click(GOLDEN_INHERITANCE_FULL, interval=1):
                logger.info("Golden inheritance full handled")
                return True

            if self.ui_additional():
                timeout.reset()
                no_action_confirm.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                no_action_confirm.reset()
                continue

            if no_action_confirm.reached():
                logger.info("Golden inheritance not full, skip")
                return False

    def run(self):
        logger.hr("Gacha", level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_start()
        self._draw_count = 0
        self._draw_free = False
        self._in_standard_pool = False
        self._no_free = False
        self._enter_gacha()
        if not self._select_standard_tab():
            return False
        if not self._start_summon():
            if self._no_free:
                self._collect_golden_inheritance_full(skip_first_screenshot=True)
                self.config.task_delay(server_update=True)
                return True
            return False
        self._handle_summon_flow()
        self._collect_golden_inheritance_full(skip_first_screenshot=True)
        if (
            self._should_schedule_mission_reward_after_free_summon(self._draw_free)
            and should_schedule_mission_reward(self.config)
        ):
            self.config.task_call("MissionReward", force_call=False)
        self.config.task_delay(server_update=True)
        return True
