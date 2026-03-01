from module.base.button import ClickButton
from module.base.timer import Timer
from module.base.utils import color_similar, get_color
from module.logger import logger
from tasks.base.page import page_knights_donate
from tasks.knights.assets.assets_knights_donate import DONATE_ACTION


class KnightsDonateMixin:
    DONATE_ACTION_LUMA_SIMILARITY = 0.8
    DONATE_ACTION_COLOR_THRESHOLD = 30
    DONATE_ACTION_INTERVAL_SECONDS = 0.6
    DONATE_TIMEOUT_SECONDS = 20

    def _enter_donate(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights: enter donate")
        self.ui_goto(page_knights_donate, skip_first_screenshot=skip_first_screenshot)
        return True

    def _is_donate_action_enabled(self, button: ClickButton) -> bool:
        """
        Check if this action button is currently clickable (not greyed out).
        """
        expected_color = DONATE_ACTION.buttons[0].color
        current_color = get_color(self.device.image, button.area)
        return color_similar(current_color, expected_color, threshold=self.DONATE_ACTION_COLOR_THRESHOLD)

    def _find_enabled_donate_actions(self) -> list[ClickButton]:
        actions = DONATE_ACTION.match_multi_template(self.device.image, similarity=self.DONATE_ACTION_LUMA_SIMILARITY)
        if not actions:
            return []

        enabled = [button for button in actions if self._is_donate_action_enabled(button)]
        enabled.sort(key=lambda button: button.area[1])
        return enabled

    def run_donate(self, skip_first_screenshot=True) -> bool:
        logger.hr("Knights Donate", level=2)

        if not self._enter_donate(skip_first_screenshot=skip_first_screenshot):
            return False

        timeout = Timer(self.DONATE_TIMEOUT_SECONDS, count=60).start()
        no_action_confirm = Timer(1.5, count=4).start()
        self.interval_clear(DONATE_ACTION, interval=self.DONATE_ACTION_INTERVAL_SECONDS)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Knights donate timeout")
                return False

            if self.handle_network_error():
                timeout.reset()
                no_action_confirm.reset()
                continue

            actions = self._find_enabled_donate_actions()
            if actions:
                if not self.interval_is_reached(DONATE_ACTION, interval=self.DONATE_ACTION_INTERVAL_SECONDS):
                    continue
                self.device.click(actions[0])
                self.interval_reset(DONATE_ACTION, interval=self.DONATE_ACTION_INTERVAL_SECONDS)
                timeout.reset()
                no_action_confirm.reset()
                continue

            if no_action_confirm.reached():
                logger.info("Knights donate: no enabled donate actions, done")
                return True
