"""
Epic Seven 宠物商店模块

流程:
    menu -> MENU_GOTO_PETS -> PETS_CHECK
    ADOPTION_ENTRY -> ADOPTION_ONE_FREE
    轮询:
        - ADOPTION_RESULT: 截图保存 -> 关闭 AD_BUFF_X_CLOSE 两次 -> 回到 PETS_CHECK
        - PETS_PACK_FULL: 关闭 AD_BUFF_X_CLOSE 一次 -> 回到 PETS_CHECK
"""
from datetime import datetime
from pathlib import Path

from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from tasks.base.page import page_pets
from tasks.base.ui import UI
from tasks.pets.assets.assets_pets import (
    ADOPTION_CHECK,
    ADOPTION_ENTRY,
    ADOPTION_ONE_FREE,
    ADOPTION_RESULT,
    PETS_PACK_FULL,
)


class Pets(UI):
    """
    宠物商店
    """

    def _save_result(self, tag="adoption"):
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        ts = now.strftime("%Y%m%d_%H%M%S_%f")
        folder = Path("log/pets") / day
        folder.mkdir(parents=True, exist_ok=True)
        image_path = folder / f"{ts}_{tag}.png"
        save_image(self.device.image, str(image_path))

    def _enter_pets(self) -> bool:
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        self.ui_goto(page_pets)
        return True

    def _enter_adoption(self) -> bool:
        logger.info("Enter adoption")
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Enter adoption timeout")
                return False

            if self.appear(ADOPTION_CHECK):
                return True

            if self.appear_then_click(ADOPTION_ENTRY, interval=2):
                continue

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    def _adopt_one_free(self) -> bool:
        logger.info("Adopt one free")
        timeout = Timer(10, count=20).start()
        no_free_timer = Timer(2, count=4).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Adoption click timeout")
                return False

            if self.appear_then_click(ADOPTION_ONE_FREE, interval=2):
                return True
            if self.appear(ADOPTION_CHECK) and no_free_timer.reached():
                logger.info("Free adoption already used today, skip")
                self._no_free = True
                return False

            if not self.appear(ADOPTION_CHECK):
                if self.ui_additional():
                    continue
            if self.handle_network_error():
                continue

    def _wait_adoption_result(self) -> bool:
        logger.info("Wait adoption result")
        timeout = Timer(20, count=40).start()
        result_saved = False
        close_need = 0

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Adoption result timeout")
                return False

            if self.appear(page_pets.check_button):
                return True

            if self.appear(ADOPTION_RESULT, interval=1):
                if not result_saved:
                    self._save_result(tag="adoption")
                    result_saved = True
                    close_need = 2

            if self.appear(PETS_PACK_FULL, interval=1):
                if close_need == 0:
                    close_need = 1

            if close_need > 0:
                if self.handle_ad_buff_x_close(interval=2):
                    close_need -= 1
                    timeout.reset()
                continue

            if self.handle_ad_buff_x_close(interval=2):
                timeout.reset()
                continue

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

    def run(self):
        logger.hr("Pets", level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_start()

        self._no_free = False
        self._enter_pets()
        if not self._enter_adoption():
            return False
        if not self._adopt_one_free():
            if self._no_free:
                self.ui_goto(page_pets)
                self.config.task_delay(server_update=True)
                return True
            return False
        if not self._wait_adoption_result():
            return False

        self.ui_goto(page_pets)
        self.config.task_delay(server_update=True)
        return True
