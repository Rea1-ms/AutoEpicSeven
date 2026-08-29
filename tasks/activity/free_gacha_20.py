from module.base.timer import Timer
from module.logger import logger
from tasks.activity.assets.assets_activity_special_26_8_27 import (
    FREE_20_GACHA,
    FREE_20_GACHA_OBTAINED,
)
from tasks.base.page import page_common_activity, page_main
from tasks.base.ui import UI


class FreeGacha20(UI):
    """Claim the overseas event reward containing 20 free summons."""

    CLAIM_FLOW_TIMEOUT_SECONDS = 30

    def run_claim(self, skip_first_screenshot=True) -> bool:
        """Claim the reward and wait until the activity page confirms it.

        Pages:
            in: page_main, any
            out: page_common_activity, FREE_20_GACHA_OBTAINED
        """
        self.ui_goto(
            page_common_activity,
            skip_first_screenshot=skip_first_screenshot,
        )

        logger.info("SpecialActivity: claim 20 free summons")
        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS, count=60).start()
        claim_requested = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # The claimed marker is the only stable completion state. The
            # reward popup may close before the activity page has refreshed,
            # so observing TOUCH_TO_CLOSE alone must never finish the flow.
            if self.appear(FREE_20_GACHA_OBTAINED):
                logger.info("SpecialActivity: 20 free summons obtained")
                return True

            if timeout.reached():
                logger.warning("SpecialActivity: 20-free-summon claim timeout")
                return False

            if self.appear_then_click(FREE_20_GACHA, interval=2):
                claim_requested = True
                timeout.reset()
                continue

            if claim_requested and self.handle_touch_to_close(interval=2):
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue

    def run(self) -> bool:
        """Run the scheduled claim task and return to the main page.

        Pages:
            in: page_main, any
            out: page_main
        """
        logger.hr("SpecialActivity: 20 Free Summons", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not self.config.SpecialActivity_GetFreeGacha:
            logger.info("SpecialActivity: free-gacha reward disabled")
            self.config.task_delay(server_update=True)
            return True

        success = self.run_claim(skip_first_screenshot=True)
        if success:
            self.ui_goto(page_main, skip_first_screenshot=True)
            self.config.task_delay(server_update=True)
        else:
            self.config.task_delay(success=False)
        return success
