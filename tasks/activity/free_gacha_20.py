from module.base.timer import Timer
from module.logger import logger
from tasks.activity.assets.assets_activity_special_26_8_27 import (
    FREE_20_GACHA,
    FREE_20_GACHA_OBTAINED,
    FREE_20_GACHA_SELECTED,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_free_gacha_20_checked
from tasks.activity.calendar import DEFAULT_FREE_GACHA_20_ID
from tasks.base.page import page_common_activity
from tasks.base.ui import UI


class FreeGacha20(ActivityNavigationMixin, UI):
    """Claim the CN or overseas event reward containing 20 free summons."""

    CLAIM_FLOW_TIMEOUT_SECONDS = 30

    def __init__(self, config, device=None, task=None, activity_id=DEFAULT_FREE_GACHA_20_ID):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def run_claim(self, skip_first_screenshot=True, *, navigate=True) -> bool:
        """Claim the reward and wait until the activity page confirms it.

        Pages:
            in: page_main, any; selected event when navigate=False
            out: page_common_activity, FREE_20_GACHA_OBTAINED
        """
        if navigate:
            self.ui_goto(page_common_activity, skip_first_screenshot=skip_first_screenshot)
            if not self.select_activity("INFINITY", FREE_20_GACHA_SELECTED):
                return False
        else:
            if not skip_first_screenshot:
                self.device.screenshot()
                skip_first_screenshot = True
            if not self._activity_selected(FREE_20_GACHA_SELECTED):
                return False

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
                mark_free_gacha_20_checked(self.config, self.activity_id)
                logger.info("SpecialActivity: 20 free summons obtained")
                if claim_requested:
                    self.config.task_call("Gacha", force_call=False)
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
        """Run the scheduled claim and leave final navigation to the entry.

        Pages:
            in: page_main, any
            out: page_common_activity after a claim; current page when skipped
        """
        logger.hr("SpecialActivity: 20 Free Summons", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not self.config.LimitedActivity_GetFreeGacha:
            logger.info("SpecialActivity: free-gacha reward disabled")
            self.config.task_delay(server_update=True)
            return True

        success = self.run_claim(skip_first_screenshot=True)
        if success:
            self.config.task_delay(server_update=True)
        else:
            self.config.task_delay(success=False)
        return success
