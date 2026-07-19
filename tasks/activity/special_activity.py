import re
from datetime import datetime
from pathlib import Path
import json

from module.ocr.ocr import DigitCounter, Ocr
from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from tasks.base.page import (
    page_special_activity,
    page_special_activity_task,
    page_special_activity_gacha,
    page_special_activity_energy_drink,
)
from tasks.base.ui import UI
from tasks.activity.assets.assets_activity_special_26_6_25 import (
    LEAF_OBTAIN,
    LEAF_CHECKMARK,
    FAST_COMBAT_TIMES_OBTAIN,
    FAST_COMBAT_TIMES_CHECKMARK,
    TASK_REWARD_OBTAIN,
    TASK_CHECKMARK,
    TASK_NAVIGATE_NOW,
    OCR_FREE_GACHA_TIMES,
    FREE_GACHA,
    FREE_GACHA_AVAILABLE,
    FREE_GACHA_UNAVAILABLE,
    SPECIAL_ACTIVITY_GACHA_LOCKED,
    SPECIAL_ACTIVITY_TASK_GOTO_SPECIAL_ACTIVITY_GACHA,
    OCR_ENERGY_DRINK,
    ENERGY_DRINK_OBTAIN,
    SPECIAL_TOUCH_TO_CLOSE
)
from tasks.gacha.assets.assets_gacha import (
    SUMMON_NEW,
    SUMMON_SKIP,
    SUMMON_RESULT_BACK,
    SUMMON_FREE_CONTINUE
)


class SpecialActivity(UI):
    """
    Epic Seven Special Activity
    """

    SIGNIN_RATE_REWARD_LUMA_SIMILARITY = 0.8
    SIGNIN_RATE_REWARD_COLOR_THRESHOLD = 30
    GET_ENERGY_DRINK_FLOW_TIMEOUT_SECONDS = 30
    GET_DAILY_REWARD_FLOW_TIMEOUT_SECONDS = 30
    GET_TASK_REWARD_FLOW_TIMEOUT_SECONDS = 30
    FREE_GACHA_FLOW_TIMEOUT_SECONDS = 120
    CLICK_FAST_COMBAT_TIMES = 0

    def __init__(self, config, device=None, task=None):
        super().__init__(config, device=device, task=task)
        self._draw_count = 1
        self._draw_free = True
        self._in_standard_pool = False
        self._no_free = False

    def _ocr_lang(self) -> str:
        lang = self.config.Emulator_GameLanguage
        if lang in ("auto", "", None):
            return "cn"
        if lang in ("cn", "global_cn"):
            return "cn"
        return "cn"

    def _read_energy_drink_text(self) -> str:
        text = Ocr(
            OCR_ENERGY_DRINK,
            lang=self._ocr_lang(),
            name="EnergyDrink",
        ).ocr_single_line(self.device.image)
        return re.sub(r"\s+", "", text or "")

    def _parse_energy_drink(self) -> str:
        text = self._read_energy_drink_text()
        if re.fullmatch(r"\d+", text):
            return "number"
        if text == "获得信息":
            return "claimed"
        return "unknown"

    def _has_used_all_free_gacha_times(self) -> bool:
        current, _, total = DigitCounter(
            OCR_FREE_GACHA_TIMES,
            lang=self._ocr_lang(),
            name="FreeGachaTimes",
        ).ocr_single_line(self.device.image)
        if (current, total) == (100, 100):
            logger.attr("FreeGachaTimes", "100/100")
            return True
        return False

    def _save_result(self, tag="result"):
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        ts = now.strftime("%Y%m%d_%H%M%S_%f")
        folder = Path("log/special_activity") / day
        folder.mkdir(parents=True, exist_ok=True)

        image_path = folder / f"{ts}_{tag}.png"
        save_image(self.device.image, str(image_path))

        record = {
            "ts": ts,
            "tag": tag,
            "count": self._draw_count,
            "free": self._draw_free,
            "image": str(image_path),
        }
        with open(folder / "draws.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _wait_return_to_sa_gacha(self):
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()
            if timeout.reached():
                return False
            if self.ui_page_appear(page_special_activity_gacha):
                return True
            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    def handle_sa_touch_to_close(self, interval=2) -> bool:
        """
        Handle special activity touch to close.

        Returns:
            If handled.
        """
        if self.appear_then_click(SPECIAL_TOUCH_TO_CLOSE, interval=interval):
            return True
        return False

    def goto_free_gacha(self, skip_first_screenshot=True) -> bool | None:
        """
        Enter the special activity free-gacha page.

        Pages:
            in: page_special_activity_task
            out: page_special_activity_gacha, page_special_activity_task

        Returns:
            True: The free-gacha page was entered.
            False: The event-wide free-gacha limit has locked its entry.
            None: The entry flow timed out.
        """
        self.ui_goto(page_special_activity_task, skip_first_screenshot=skip_first_screenshot)

        timeout = Timer(self.FREE_GACHA_FLOW_TIMEOUT_SECONDS, count=60).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # The lock only appears after all 100 draws and their task rewards
            # are claimed. Treat it as normal completion before clicking the
            # adjacent entry; otherwise this loop would retry an unclickable UI.
            if self.appear(SPECIAL_ACTIVITY_GACHA_LOCKED):
                logger.info("SpecialActivity: free gacha entry locked, skip task")
                return False

            if self.ui_page_appear(page_special_activity_gacha):
                return True

            if timeout.reached():
                logger.warning("SpecialActivity: free gacha entry timeout")
                return None

            if self.appear_then_click(
                SPECIAL_ACTIVITY_TASK_GOTO_SPECIAL_ACTIVITY_GACHA,
                interval=2,
            ):
                timeout.reset()
                continue

            if self.ui_additional():
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue

    def run_get_daily_reward(self, skip_first_screenshot=True) -> bool:

        self.ui_goto(page_special_activity)

        logger.info("SpecialActivity: get daily reward")
        timeout = Timer(self.GET_DAILY_REWARD_FLOW_TIMEOUT_SECONDS, count=60).start()
        click_fast_combat_times = self.CLICK_FAST_COMBAT_TIMES

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # two meet
            if self.appear(LEAF_CHECKMARK) and self.appear(FAST_COMBAT_TIMES_CHECKMARK):
                logger.info("SpecialActivity: has obtained daily reward, skip task")
                return True

            # fast combat times >= 20
            if click_fast_combat_times >= 3:
                logger.warning("SpecialActivity: can't obtain fast combat times, skip for now")
                return False

            if timeout.reached():
                logger.warning("SpecialActivity flow timeout")
                return False

            if self.appear_then_click(LEAF_OBTAIN, interval=1):
                timeout.reset()
                continue

            if self.appear_then_click(FAST_COMBAT_TIMES_OBTAIN, interval=1):
                click_fast_combat_times += 1
                timeout.reset()
                continue

            if self.handle_ad_buff_x_close():
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue
    
    def run_get_task_reward(self, skip_first_screenshot=True) -> bool:

        self.ui_goto(page_special_activity_task)

        logger.info("SpecialActivity: get task reward")
        timeout = Timer(self.GET_TASK_REWARD_FLOW_TIMEOUT_SECONDS, count=60).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # checkmark or navigate right now
            if self.appear(TASK_CHECKMARK) or self.appear(TASK_NAVIGATE_NOW):
                logger.info("SpecialActivity: no task reward needs to be obtain, skip task")
                return True

            if timeout.reached():
                logger.warning("SpecialActivity flow timeout")
                return False

            if self.appear_then_click(TASK_REWARD_OBTAIN, interval=1):
                timeout.reset()
                continue

            # I forgot if x or touch to close
            if self.handle_ad_buff_x_close():
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue
    
    def run_free_gacha(self, skip_first_screenshot=True) -> bool:
        entered = self.goto_free_gacha(skip_first_screenshot=skip_first_screenshot)
        if entered is False:
            return True
        if entered is None:
            return False

        # Both limits are only meaningful on the free-gacha page. Check them
        # before announcing the flow so a skipped task never looks like a
        # summon attempt in the log.
        if self.appear(FREE_GACHA_UNAVAILABLE):
            logger.info("SpecialActivity: no free gacha times remaining today, skip task")
            return True

        if self._has_used_all_free_gacha_times():
            logger.info("SpecialActivity: all free gacha times have been used up, skip task")
            return True

        logger.info("SpecialActivity: run free gacha")
        timeout = Timer(self.FREE_GACHA_FLOW_TIMEOUT_SECONDS, count=60).start()

        result_saved = False
        returning_to_gacha = False

        self.device.screenshot_interval_set(1.0)
        try:
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                # The result-back click can be ignored by the client. Do not
                # finish until the stable free-gacha page confirms its effect.
                if returning_to_gacha and self.ui_page_appear(page_special_activity_gacha):
                    logger.info("SpecialActivity: returned to free gacha page")
                    return True

                # This can change after the last draw of the current daily
                # allowance. Keep the in-flow check so the state loop exits
                # cleanly instead of waiting for an unrelated screen change.
                if self.appear(FREE_GACHA_UNAVAILABLE):
                    logger.info("SpecialActivity: no free gacha times remaining today, skip task")
                    return True

                if timeout.reached():
                    logger.warning("Special activity summon flow timeout")
                    return False

                # 0) Start summon
                if self.appear(FREE_GACHA_AVAILABLE, interval=1):
                    self.device.click(FREE_GACHA)
                    continue

                # 1) Skip animation
                if self.appear_then_click(SUMMON_SKIP, interval=1):
                    continue

                # 2) New overlay
                if self.appear(SUMMON_NEW, interval=1):
                    if not result_saved:
                        self._save_result(tag="new")
                        result_saved = True
                    self.device.click(SUMMON_NEW)
                    continue

                # 3) Result page
                back = self.appear(SUMMON_RESULT_BACK)
                free_continue = self.appear(SUMMON_FREE_CONTINUE)
                if back and free_continue:
                    self._save_result(tag="result")
                    result_saved = True
                    if self.appear_then_click(SUMMON_FREE_CONTINUE, interval=1):
                        result_saved = False
                        timeout.reset()
                    continue
                if back:
                    if self.appear_then_click(SUMMON_RESULT_BACK, interval=2):
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
    
    def run_get_energy_drink(self, skip_first_screenshot=True) -> bool:

        self.ui_goto(page_special_activity_energy_drink)

        logger.info("SpecialActivity: get energy drink")
        timeout = Timer(self.GET_ENERGY_DRINK_FLOW_TIMEOUT_SECONDS, count=60).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            status = self._parse_energy_drink()
            if status == "claimed":
                logger.info("SpecialActivity: energy drink claimed")
                return True
            if status == "number":
                if self.appear_then_click(ENERGY_DRINK_OBTAIN, interval=1):
                    timeout.reset()
                    continue

            if timeout.reached():
                logger.warning("SpecialActivity flow timeout")
                return False

            if self.handle_network_error():
                timeout.reset()
                continue


    # main special activity starter
    def run(self) -> bool:
        logger.hr("SpecialActivity", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        self.ui_goto(page_special_activity)

        run_get_daily_reward = self.config.SpecialActivity_GetDailyReward
        run_get_task_reward = self.config.SpecialActivity_GetTaskReward
        run_free_gacha = self.config.SpecialActivity_GetFreeGacha
        run_get_energy_drink = self.config.SpecialActivity_GetEnergyDrink

        if not any(
            [
                run_get_daily_reward,
                run_get_task_reward,
                run_free_gacha,
                run_get_energy_drink
            ]
        ):
            logger.warning("SpecialActivity: all sub tasks disabled")
            self.config.task_delay(server_update=True)
            return True

        success = True
        if run_get_daily_reward:
            success = self.run_get_daily_reward(skip_first_screenshot=True) and success
        if run_get_task_reward:
            success = self.run_get_task_reward(skip_first_screenshot=True) and success
        if run_free_gacha:
            success = self.run_free_gacha(skip_first_screenshot=True) and success
        if run_get_energy_drink:
            success = self.run_get_energy_drink(skip_first_screenshot=True) and success

        if success:
            self.config.task_delay(server_update=True)
        else:
            self.config.task_delay(success=False)
        return success
