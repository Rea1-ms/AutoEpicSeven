from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_knights
from tasks.knights.assets.assets_knights_activity_weekly_task import RECEIVE
from tasks.knights.assets.assets_knights_activity_support_entry import SUPPORT_CHECK
from tasks.knights.assets.assets_knights_activity_weekly_task_entry import (
    WEEKLY_TASK_CHECK,
    WEEKLY_TASK_ENTRY,
)
from tasks.knights.assets.assets_knights_activity_weekly_task_weekly_points import (
    WEEKLY_POINTS_1,
    WEEKLY_POINTS_2,
    WEEKLY_POINTS_3,
    WEEKLY_POINTS_4,
)


class KnightsWeeklyTaskMixin:
    WEEKLY_TASK_ENTRY_TIMEOUT_SECONDS = 20
    WEEKLY_TASK_ENTRY_CLICK_INTERVAL_SECONDS = 1.2
    RECEIVE_MATCH_THRESHOLD = 40
    RECEIVE_CLICK_INTERVAL_SECONDS = 0.8
    WEEKLY_TASK_TIMEOUT_SECONDS = 30
    WEEKLY_TASK_DONE_CONFIRM_SECONDS = 1.5
    WEEKLY_POINTS_ACTION = "knights_WEEKLY_POINTS_ACTION"
    WEEKLY_POINTS_CLICK_INTERVAL_SECONDS = 1
    WEEKLY_POINTS_LUMA_SIMILARITY = 0.8
    WEEKLY_POINTS_COLOR_THRESHOLD = 30
    WEEKLY_POINTS_BUTTONS = (
        WEEKLY_POINTS_1,
        WEEKLY_POINTS_2,
        WEEKLY_POINTS_3,
        WEEKLY_POINTS_4,
    )

    def _enter_weekly_task(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights: enter weekly task")
        if self.appear(WEEKLY_TASK_CHECK):
            logger.info("Knights weekly task page already reached")
            return True

        if not self._enter_support(skip_first_screenshot=skip_first_screenshot):
            return False

        timeout = Timer(self.WEEKLY_TASK_ENTRY_TIMEOUT_SECONDS, count=60).start()
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Knights weekly task entry timeout")
                return False

            if self.appear(WEEKLY_TASK_CHECK, interval=1):
                logger.info("Knights weekly task page reached")
                return True

            if self.appear(SUPPORT_CHECK):
                if self.appear_then_click(WEEKLY_TASK_ENTRY, interval=self.WEEKLY_TASK_ENTRY_CLICK_INTERVAL_SECONDS):
                    logger.info("Knights: open weekly task")
                    timeout.reset()
                    continue

            if self.appear(page_knights.check_button):
                if not self._enter_support(skip_first_screenshot=True):
                    return False
                timeout.reset()
                continue

            if self.is_in_main(interval=0):
                logger.warning("Knights weekly task entry exited to main page unexpectedly")
                return False

            if self.handle_network_error():
                timeout.reset()
                continue

    @staticmethod
    def _button_top(button: ClickButton) -> int:
        return button.area[1]

    def _find_receive_buttons(self) -> list[ClickButton]:
        buttons = RECEIVE.match_multi_template(self.device.image, threshold=self.RECEIVE_MATCH_THRESHOLD)
        if not buttons:
            return []
        buttons.sort(key=self._button_top)
        return buttons

    def _get_ready_weekly_points_button(self, interval=0):
        if interval and not self.interval_is_reached(self.WEEKLY_POINTS_ACTION, interval=interval):
            return None

        for button in self.WEEKLY_POINTS_BUTTONS:
            self.device.stuck_record_add(button)
            if button.match_template_color(
                self.device.image,
                similarity=self.WEEKLY_POINTS_LUMA_SIMILARITY,
                threshold=self.WEEKLY_POINTS_COLOR_THRESHOLD,
            ):
                if interval:
                    self.interval_reset(self.WEEKLY_POINTS_ACTION, interval=interval)
                return button

        return None

    def run_weekly_task(self, skip_first_screenshot=True) -> bool:
        logger.hr("Knights Weekly Task", level=2)

        if not self._enter_weekly_task(skip_first_screenshot=skip_first_screenshot):
            return False

        timeout = Timer(self.WEEKLY_TASK_TIMEOUT_SECONDS, count=90).start()
        no_receive_confirm = Timer(self.WEEKLY_TASK_DONE_CONFIRM_SECONDS, count=4).start()
        wait_touch_timeout = Timer(2, count=6)
        wait_touch_close = False

        self.interval_clear(RECEIVE, interval=self.RECEIVE_CLICK_INTERVAL_SECONDS)

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

        timeout.reset()
        no_points_confirm = Timer(self.WEEKLY_TASK_DONE_CONFIRM_SECONDS, count=4).start()
        self.interval_clear(self.WEEKLY_POINTS_ACTION, interval=self.WEEKLY_POINTS_CLICK_INTERVAL_SECONDS)

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

            ready_button = self._get_ready_weekly_points_button(interval=self.WEEKLY_POINTS_CLICK_INTERVAL_SECONDS)
            if ready_button is not None:
                logger.info(f"Knights weekly task: claim weekly points via {ready_button.name}")
                self.device.click(ready_button)
                timeout.reset()
                no_points_confirm.reset()
                continue

            if no_points_confirm.reached():
                logger.info("Knights weekly task points phase done")
                return True
