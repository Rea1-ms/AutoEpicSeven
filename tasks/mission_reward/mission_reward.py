"""
Epic Seven mission reward task.

Flow:
    menu -> mission reward popup -> daily tab -> weekly tab -> main page

Pages:
    in: page_main, page_menu, page_mission_reward, page_mission_reward_daily, page_mission_reward_weekly
    out: page_main
"""
import re

from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit
from tasks.base.page import page_main, page_mission_reward_daily, page_mission_reward_weekly
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
from tasks.mission_reward.assets.assets_mission_reward_weekly import (
    WEEKLY_POINTS_1,
    WEEKLY_POINTS_2,
    WEEKLY_POINTS_3,
    WEEKLY_POINTS_4,
    WEEKLY_POINTS_5,
    WEEKLY_POINTS_6,
)


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


class MissionReward(UI):
    DAILY_REWARD_ACTION = "MISSION_REWARD_DAILY_ACTION"
    WEEKLY_REWARD_ACTION = "MISSION_REWARD_WEEKLY_ACTION"
    REWARD_CLICK_INTERVAL_SECONDS = 1
    REWARD_COLOR_THRESHOLD = 30
    REWARD_TIMEOUT_SECONDS = 12
    REWARD_DONE_CONFIRM_SECONDS = 1.5
    TOUCH_CLOSE_WAIT_SECONDS = 2

    DAILY_REWARD_BUTTONS = (
        DAILY_POINTS_1,
        DAILY_POINTS_2,
        DAILY_POINTS_3,
        DAILY_POINTS_4,
        DAILY_POINTS_5,
        DAILY_POINTS_6,
    )
    WEEKLY_REWARD_BUTTONS = (
        WEEKLY_POINTS_1,
        WEEKLY_POINTS_2,
        WEEKLY_POINTS_3,
        WEEKLY_POINTS_4,
        WEEKLY_POINTS_5,
        WEEKLY_POINTS_6,
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
            out: page_mission_reward_daily
        """
        logger.info("Mission reward: enter daily tab")
        self.ui_goto(page_mission_reward_daily, skip_first_screenshot=skip_first_screenshot)
        return True

    def _goto_weekly_tab(self, skip_first_screenshot=True) -> bool:
        """
        Pages:
            in: page_mission_reward, page_mission_reward_daily, page_mission_reward_weekly
            out: page_mission_reward_weekly
        """
        logger.info("Mission reward: switch to weekly tab")
        self.ui_goto(page_mission_reward_weekly, skip_first_screenshot=skip_first_screenshot)
        return True

    def _ocr_mission_points(self, label: str) -> int:
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

    def _claim_rewards(self, label: str, buttons, action_name: str, skip_first_screenshot=True) -> bool:
        """
        Claim all available rewards in one tab.

        Clicking any ready chest should consume all pending rewards for the tab.

        Returns:
            bool: Whether at least one claim click was issued.
        """
        logger.hr(f"{label} Rewards", level=2)

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
                logger.warning(f"Mission reward: {label.lower()} claim timeout")
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
                logger.info(f"Mission reward: claim {label.lower()} rewards via {ready_button.name}")
                self.device.click(ready_button)
                claimed = True
                wait_touch_close = True
                wait_touch_timeout.reset()
                timeout.reset()
                no_action_confirm.reset()
                continue

            if no_action_confirm.reached():
                logger.info(f"Mission reward: {label.lower()} rewards phase done")
                return claimed

    def run(self) -> bool:
        logger.hr("Mission Reward", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        run_daily = getattr(self.config, "MissionReward_ClaimDailyRewards", True)
        run_weekly = getattr(self.config, "MissionReward_ClaimWeeklyRewards", True)

        if not any([run_daily, run_weekly]):
            logger.warning("Mission reward: all sub tasks disabled")
            self.config.task_delay(server_update=True)
            return True

        daily_points = None
        weekly_points = None

        self._enter_mission_reward(skip_first_screenshot=False)

        if run_daily:
            daily_points = self._ocr_mission_points("Daily")
            self._claim_rewards(
                label="Daily",
                buttons=self.DAILY_REWARD_BUTTONS,
                action_name=self.DAILY_REWARD_ACTION,
                skip_first_screenshot=True,
            )

        if run_weekly:
            self._goto_weekly_tab(skip_first_screenshot=True)
            weekly_points = self._ocr_mission_points("Weekly")
            self._claim_rewards(
                label="Weekly",
                buttons=self.WEEKLY_REWARD_BUTTONS,
                action_name=self.WEEKLY_REWARD_ACTION,
                skip_first_screenshot=True,
            )

        with self.config.multi_set():
            if daily_points is not None:
                self.config.stored.E7DailyActivity.value = daily_points
            if weekly_points is not None:
                self.config.stored.E7WeeklyActivity.value = weekly_points

        self.ui_goto(page_main, skip_first_screenshot=True)
        self.config.task_delay(server_update=True)
        return True
