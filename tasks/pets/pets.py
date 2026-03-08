"""
Epic Seven 宠物商店模块

流程:
    menu -> MENU_GOTO_PETS -> PETS_CHECK
    ADOPTION_ENTRY -> ADOPTION_ONE_FREE
    轮询:
        - ADOPTION_RESULT: 截图保存 -> 关闭 AD_BUFF_X_CLOSE 两次 -> 回到 PETS_CHECK
        - PETS_PACK_FULL: 关闭 AD_BUFF_X_CLOSE 一次 -> 回到 PETS_CHECK

    TODO: 暂未测试满仓直接退出是否生效
"""
from datetime import datetime
from pathlib import Path

from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from module.ocr.ocr import DigitCounter
from tasks.base.page import page_pets
from tasks.base.ui import UI
from tasks.pets.assets.assets_pets import (
    ADOPTION_CHECK,
    ADOPTION_ENTRY,
    ADOPTION_ONE_FREE,
    ADOPTION_RESULT,
    OCR_PACK_FULL,
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

    def _precheck_pack_full_on_pets(self) -> bool | None:
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        if not self.appear(page_pets.check_button):
            return None

        for _ in range(2):
            full = self._check_pack_full_by_ocr()
            if full is True:
                self._pack_full = True
                self._pack_full_checked = True
                return True
            if full is False:
                self._pack_full_checked = True
                return False
            self.device.screenshot()

        return None

    def _check_pack_full_by_ocr(self) -> bool | None:
        lang = self.config.Emulator_GameLanguage
        if lang == "auto" or not lang:
            lang = "cn"
        current, _, total = DigitCounter(
            OCR_PACK_FULL, lang=lang, name="OCR_PACK_FULL"
        ).ocr_single_line(self.device.image)
        if total <= 0:
            logger.debug("Pets pack OCR miss or invalid total")
            return None
        if current > total:
            logger.warning(f'Pets pack OCR invalid: {current}/{total}')
            return None
        if current >= total:
            logger.warning(f'Pets pack full: {current}/{total}')
            return True
        return False

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

            in_adoption = self.appear(ADOPTION_CHECK)
            if in_adoption and not self._pack_full_checked:
                full = self._check_pack_full_by_ocr()
                if full is True:
                    self._pack_full = True
                    return False
                if full is False:
                    self._pack_full_checked = True
                else:
                    self._pack_full_retry += 1
                    if self._pack_full_retry >= 2:
                        self._pack_full_checked = True

            if self.appear_then_click(ADOPTION_ONE_FREE, interval=1):
                return True
            if in_adoption and no_free_timer.reached():
                logger.info("Free adoption already used today, skip")
                self._no_free = True
                return False

            if not in_adoption:
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

            if self.appear(ADOPTION_RESULT):
                if not result_saved:
                    self._save_result(tag="adoption")
                    result_saved = True
                    close_need = 2

            if self.appear(PETS_PACK_FULL):
                if close_need == 0:
                    close_need = 1

            # test if interval sets to 0
            if close_need > 0:
                if self.handle_ad_buff_x_close(interval=0):
                    close_need -= 1
                    timeout.reset()
                continue

            if self.handle_ad_buff_x_close(interval=0):
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
        self._pack_full = False
        self._pack_full_checked = False
        self._pack_full_retry = 0
        self._enter_pets()
        self._precheck_pack_full_on_pets()
        if self._pack_full:
            self.ui_goto(page_pets)
            self.config.task_delay(server_update=True)
            return True
        if not self._enter_adoption():
            return False
        if not self._adopt_one_free():
            if self._no_free or self._pack_full:
                self.ui_goto(page_pets)
                self.config.task_delay(server_update=True)
                return True
            return False
        if not self._wait_adoption_result():
            return False

        self.ui_goto(page_pets)
        self.config.task_delay(server_update=True)
        return True
