from module.base.button import ClickButton
from module.base.timer import Timer
from module.base.utils import color_similar, get_color
from module.logger import logger
from tasks.base.page import page_knights_weekly_task
from tasks.knights.assets.assets_knights_weekly_task import RECEIVE, WEEKLY_POINTS_1


class KnightsWeeklyTaskMixin:
    RECEIVE_MATCH_THRESHOLD = 40
    RECEIVE_CLICK_INTERVAL_SECONDS = 0.8
    WEEKLY_TASK_TIMEOUT_SECONDS = 30
    WEEKLY_TASK_DONE_CONFIRM_SECONDS = 1.5
    WEEKLY_POINTS_1_LUMA_SIMILARITY = 0.8
    WEEKLY_POINTS_1_COLOR_THRESHOLD = 30

    def _enter_weekly_task(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights: enter weekly task")
        self.ui_goto(page_knights_weekly_task, skip_first_screenshot=skip_first_screenshot)
        return True

    @staticmethod
    def _button_top(button: ClickButton) -> int:
        return button.area[1]

    def _find_receive_buttons(self) -> list[ClickButton]:
        buttons = RECEIVE.match_multi_template(self.device.image, threshold=self.RECEIVE_MATCH_THRESHOLD)
        if not buttons:
            return []
        buttons.sort(key=self._button_top)
        return buttons

    def _is_weekly_points_1_ready(self, interval=0) -> bool:
        """
        WEEKLY_POINTS_1 uses luma + color double check.
        """
        self.device.stuck_record_add(WEEKLY_POINTS_1)

        if interval and not self.interval_is_reached(WEEKLY_POINTS_1, interval=interval):
            return False

        appear = False
        if WEEKLY_POINTS_1.match_template_luma(self.device.image, similarity=self.WEEKLY_POINTS_1_LUMA_SIMILARITY):
            expected_color = WEEKLY_POINTS_1.buttons[0].color
            current_color = get_color(self.device.image, WEEKLY_POINTS_1.buttons[0].area)
            if color_similar(current_color, expected_color, threshold=self.WEEKLY_POINTS_1_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(WEEKLY_POINTS_1, interval=interval)

        return appear

    def run_weekly_task(self, skip_first_screenshot=True) -> bool:
        logger.hr("Knights Weekly Task", level=2)

        if not self._enter_weekly_task(skip_first_screenshot=skip_first_screenshot):
            return False

        timeout = Timer(self.WEEKLY_TASK_TIMEOUT_SECONDS, count=90).start()
        no_receive_confirm = Timer(self.WEEKLY_TASK_DONE_CONFIRM_SECONDS, count=4).start()
        wait_touch_timeout = Timer(2, count=6)
        wait_touch_close = False

        self.interval_clear(RECEIVE, interval=self.RECEIVE_CLICK_INTERVAL_SECONDS)

        # Phase 1: click RECEIVE until no more.
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Knights weekly task receive phase timeout")
                return False

            if wait_touch_close:
                if self.handle_touch_to_close(interval=0.5):
                    wait_touch_close = False
                    timeout.reset()
                    continue
                if wait_touch_timeout.reached():
                    wait_touch_close = False
                else:
                    continue

            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            receive_buttons = self._find_receive_buttons()
            if receive_buttons:
                if not self.interval_is_reached(RECEIVE, interval=self.RECEIVE_CLICK_INTERVAL_SECONDS):
                    continue
                self.device.click(receive_buttons[0])
                self.interval_reset(RECEIVE, interval=self.RECEIVE_CLICK_INTERVAL_SECONDS)
                wait_touch_close = True
                wait_touch_timeout.reset()
                timeout.reset()
                no_receive_confirm.reset()
                continue

            if no_receive_confirm.reached():
                logger.info("Knights weekly task: receive phase done")
                break

        # Phase 2: claim weekly points tier 1 when enabled.
        timeout.reset()
        no_points_confirm = Timer(self.WEEKLY_TASK_DONE_CONFIRM_SECONDS, count=4).start()
        self.interval_clear(WEEKLY_POINTS_1, interval=1)

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Knights weekly task points phase timeout")
                return False

            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if self._is_weekly_points_1_ready(interval=1):
                logger.info("Knights weekly task: claim weekly points tier 1")
                self.device.click(WEEKLY_POINTS_1)
                timeout.reset()
                no_points_confirm.reset()
                continue

            if no_points_confirm.reached():
                logger.info("Knights weekly task points phase done")
                return True
