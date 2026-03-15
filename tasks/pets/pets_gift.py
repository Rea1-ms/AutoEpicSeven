from datetime import datetime, timedelta

from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_menu
from tasks.base.ui import UI


class PetsGift(UI):
    """
    Collect the periodic pets gift from menu.
    """

    PETS_GIFT_COOLDOWN = timedelta(hours=22)
    PETS_GIFT_BUFFER = timedelta(minutes=5)
    PETS_GIFT_LAST_CLAIM_VALIDITY = timedelta(days=7)
    PETS_GIFT_FUTURE_TOLERANCE = timedelta(hours=1)
    PETS_GIFT_TIMEOUT_SECONDS = 12
    PETS_GIFT_DONE_CONFIRM_SECONDS = 1.5

    def _enter_menu(self, skip_first_screenshot=True) -> bool:
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        self.ui_goto(page_menu, skip_first_screenshot=skip_first_screenshot)
        return True

    def _claim_menu_pets_gift(self, skip_first_screenshot=True) -> bool:
        logger.info("Pets gift: check menu reward")
        timeout = Timer(self.PETS_GIFT_TIMEOUT_SECONDS, count=36).start()
        done_confirm = Timer(self.PETS_GIFT_DONE_CONFIRM_SECONDS, count=4).start()
        claimed = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Pets gift: menu reward timeout")
                return claimed

            if not self.appear(page_menu.check_button):
                if self.ui_additional():
                    timeout.reset()
                    done_confirm.reset()
                    continue
                if self.handle_network_error():
                    timeout.reset()
                    done_confirm.reset()
                    continue

            if self.handle_menu_pets_gift():
                claimed = True
                timeout.reset()
                done_confirm.reset()
                continue

            if self.ui_additional():
                timeout.reset()
                done_confirm.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                done_confirm.reset()
                continue

            if done_confirm.reached():
                return claimed

    def _get_last_claim_at(self) -> datetime | None:
        last_claim = getattr(self.config, "PetsGift_LastClaimAt", None)
        if not isinstance(last_claim, datetime):
            return None

        now = datetime.now()
        if last_claim > now + self.PETS_GIFT_FUTURE_TOLERANCE:
            logger.warning(f"Pets gift last claim is in the future, ignore: {last_claim}")
            return None
        if now - last_claim > self.PETS_GIFT_LAST_CLAIM_VALIDITY:
            logger.info(f"Pets gift last claim expired, ignore: {last_claim}")
            return None

        return last_claim

    def _get_next_ready_time(self, last_claim: datetime | None) -> datetime | None:
        if last_claim is None:
            return None
        return (last_claim + self.PETS_GIFT_COOLDOWN + self.PETS_GIFT_BUFFER).replace(microsecond=0)

    def _schedule_after_claim(self, claim_time: datetime) -> None:
        target = self._get_next_ready_time(claim_time)
        logger.attr("PetsGiftLastClaimAt", claim_time)
        logger.attr("PetsGiftNextReady", target)
        with self.config.multi_set():
            self.config.PetsGift_LastClaimAt = claim_time
            self.config.task_delay(target=target)

    def _schedule_after_miss(self) -> None:
        now = datetime.now()
        last_claim = self._get_last_claim_at()
        target = self._get_next_ready_time(last_claim)

        if target is not None and target > now:
            logger.info("Pets gift not ready yet, keep waiting for predicted ready time")
            logger.attr("PetsGiftLastClaimAt", last_claim)
            logger.attr("PetsGiftNextReady", target)
            self.config.task_delay(target=target)
            return

        if target is not None:
            logger.info("Pets gift predicted ready time already passed, fallback to server update")
            logger.attr("PetsGiftLastClaimAt", last_claim)
            logger.attr("PetsGiftPredictedReady", target)

        self.config.task_delay(server_update=True)

    def run(self):
        logger.hr("Pets Gift", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not self._enter_menu(skip_first_screenshot=True):
            self.config.task_delay(success=False)
            return False

        claimed = self._claim_menu_pets_gift(skip_first_screenshot=True)
        self.ui_goto_main()

        if claimed:
            self._schedule_after_claim(datetime.now().replace(microsecond=0))
        else:
            self._schedule_after_miss()

        return True
