"""
Epic Seven mission reward task.

Flow:
    menu -> mission reward popup -> main page

Pages:
    in: page_main, page_menu, page_mission_reward
    out: page_main
"""
import re

from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit
from tasks.base.page import page_main, page_mission_reward
from tasks.base.ui import UI
from tasks.mission_reward.assets.assets_mission_reward_daily import (
    DAILY_POINTS_1,
    DAILY_POINTS_2,
    DAILY_POINTS_3,
    DAILY_POINTS_4,
    DAILY_POINTS_5,
    DAILY_POINTS_6,
)
from tasks.mission_reward.assets.assets_mission_reward_ocr import OCR_MISSION_POINTS



class OcrMissionPoints(Digit):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")
        result = result.replace(" ", "")
        return result

    def format_result(self, result):
        found = re.search(r"(\d+)", result)
        if not found:
            return 0
        return int(found.group(1))


class CurrentMissionReward(UI):
    REWARD_ACTION = "MISSION_REWARD_ACTION"
    REWARD_CLICK_INTERVAL_SECONDS = 1
    REWARD_COLOR_THRESHOLD = 30
    REWARD_TIMEOUT_SECONDS = 12
    REWARD_DONE_CONFIRM_SECONDS = 1.5
    TOUCH_CLOSE_WAIT_SECONDS = 2

    REWARD_BUTTONS = (
        DAILY_POINTS_1,
        DAILY_POINTS_2,
        DAILY_POINTS_3,
        DAILY_POINTS_4,
        DAILY_POINTS_5,
        DAILY_POINTS_6,
    )

    def _mission_ocr_lang(self) -> str:
        lang = getattr(self.config, "Emulator_GameLanguage", "cn")
        if lang in ("auto", "", None, "cn", "global_cn", "zh", "zh_cn"):
            return "cn"
        if lang in ("en", "global_en", "en_us"):
            return "en"
        if lang in ("jp", "ja", "ja_jp"):
            return "jp"
        if lang in ("tw", "zh_tw"):
            return "tw"
        return "cn"

    def _enter_mission_reward(self, skip_first_screenshot=True) -> bool:
        """
        Pages:
            in: page_main, page_menu
            out: page_mission_reward
        """
        logger.info("Mission reward: enter")
        self.ui_goto(page_mission_reward, skip_first_screenshot=skip_first_screenshot)
        return True

    def _ocr_mission_points(self, label: str = "Daily") -> int:
        ocr = OcrMissionPoints(
            OCR_MISSION_POINTS,
            lang=self._mission_ocr_lang(),
            name=f"{label}MissionPoints",
        )
        points = ocr.ocr_single_line(self.device.image)
        logger.attr(f"{label}MissionPoints", points)
        return points

    def _get_ready_reward_button(self, buttons, action_name: str, interval=0):
        """
        Reward chests stay at fixed positions and mainly differ by brightness.
        Use match_color() first, per repository rule.
        """
        if interval and not self.interval_is_reached(action_name, interval=interval):
            return None

        for button in buttons:
            self.device.stuck_record_add(button)
            if button.match_color(self.device.image, threshold=self.REWARD_COLOR_THRESHOLD):
                if interval:
                    self.interval_reset(action_name, interval=interval)
                return button

        return None

    def _claim_rewards(self, buttons, action_name: str, skip_first_screenshot=True) -> bool:
        """
        Claim all available rewards.

        Clicking any ready chest should consume all pending rewards for the tab.

        Returns:
            bool: Whether at least one claim click was issued.
        """
        logger.hr(f"Rewards", level=2)

        timeout = Timer(self.REWARD_TIMEOUT_SECONDS, count=36).start()
        no_action_confirm = Timer(self.REWARD_DONE_CONFIRM_SECONDS, count=4).start()
        wait_touch_timeout = Timer(self.TOUCH_CLOSE_WAIT_SECONDS, count=6)
        wait_touch_close = False
        claimed = False

        self.interval_clear(action_name, interval=self.REWARD_CLICK_INTERVAL_SECONDS)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f"Mission reward claim timeout")
                return claimed

            if wait_touch_close:
                if self.handle_touch_to_close(interval=0.5):
                    wait_touch_close = False
                    timeout.reset()
                    no_action_confirm.reset()
                    continue

                ready_button = self._get_ready_reward_button(
                    buttons, action_name=action_name, interval=self.REWARD_CLICK_INTERVAL_SECONDS
                )
                if ready_button is None:
                    wait_touch_close = False

                if wait_touch_timeout.reached():
                    wait_touch_close = False
                else:
                    continue

            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                no_action_confirm.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                no_action_confirm.reset()
                continue

            ready_button = self._get_ready_reward_button(
                buttons, action_name=action_name, interval=self.REWARD_CLICK_INTERVAL_SECONDS
            )
            if ready_button is not None:
                logger.info(f"Mission reward: claim rewards via {ready_button.name}")
                self.device.click(ready_button)
                claimed = True
                wait_touch_close = True
                wait_touch_timeout.reset()
                timeout.reset()
                no_action_confirm.reset()
                continue

            if no_action_confirm.reached():
                logger.info(f"Mission reward: rewards phase done")
                return claimed

    def run(self) -> bool:
        logger.hr("Mission Reward", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        run_daily = getattr(self.config, "MissionReward_ClaimDailyRewards", True)

        if not run_daily:
            logger.warning("Mission reward: tasks disabled")
            self.config.task_delay(server_update=True)
            return True

        daily_points = None

        self._enter_mission_reward(skip_first_screenshot=False)

        claimed_daily = False

        if run_daily:
            daily_points = self._ocr_mission_points("Daily")
            claimed_daily = self._claim_rewards(
                buttons=self.REWARD_BUTTONS,
                action_name=self.REWARD_ACTION,
                skip_first_screenshot=True,
            )

        with self.config.multi_set():
            if daily_points is not None:
                self.config.stored.DailyActivity.set(daily_points)
            # Current oversea UI removed weekly mission points entirely.
            # Clear the legacy weekly counter so the dashboard does not keep
            # showing a stale pre-update value for this config profile.
            self.config.stored.WeeklyActivity.clear()

        self.ui_goto(page_main, skip_first_screenshot=True)
        if claimed_daily:
            self.config.task_call("DataUpdate", force_call=False)
        self.config.task_delay(server_update=True)
        return True


MissionReward = CurrentMissionReward
