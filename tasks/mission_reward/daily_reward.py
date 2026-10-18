"""Shared daily observation and reward confirmation for supported servers."""

import re
from dataclasses import asdict

from module.base.timer import Timer
from module.config.utils import get_server_last_update
from module.logger import logger
from module.ocr.ocr import Digit
from tasks.base.page import page_main
from tasks.mission_reward.assets.assets_mission_reward_entries import (
    MISSION_REWARD_DAILY_ENTRY_CHECK,
)
from tasks.mission_reward.assets.assets_mission_reward_ocr import OCR_MISSION_POINTS
from tasks.mission_reward.current import CurrentMissionReward
from tasks.mission_reward.daily import DailyMissionMixin
from tasks.mission_reward.planning import acknowledge_daily_review
from tasks.mission_reward.scheduling import DAILY_PLAN_PATH, daily_snapshot_context


class DailyPointsOcr(Digit):
    def format_result(self, result):
        text = result.strip().replace(" ", "").replace("O", "0").replace("o", "0")
        # A failed OCR is unknown, never zero. Otherwise a lost read could
        # overwrite a completed day and reorder resource-consuming tasks.
        if re.fullmatch(r"\d{1,3}", text):
            return int(text)
        return None


class DailyMissionReward(DailyMissionMixin, CurrentMissionReward):
    DAILY_RETRY_MINUTES = 5
    DAILY_TASK_TITLE = "Daily Missions"

    def _daily_page_ready(self):
        return self.match_template_color(MISSION_REWARD_DAILY_ENTRY_CHECK)

    def _read_daily_points(self):
        return DailyPointsOcr(
            OCR_MISSION_POINTS, lang=self._mission_ocr_lang(), name="DailyMissionPoints"
        ).ocr_single_line(self.device.image)

    def _claim_daily_rewards_verified(self, skip_first_screenshot=True):
        """Confirm a stable daily page after available chests disappear.

        Pages:
            in: page_mission_reward_daily
            out: page_mission_reward_daily on success; current popup on failure

        Returns:
            tuple[bool, bool]: Whether finished, whether a claim was attempted.
        """
        timeout = Timer(self.REWARD_TIMEOUT_SECONDS, count=36).start()
        settled = Timer(self.REWARD_DONE_CONFIRM_SECONDS, count=4).start()
        clicked = False
        self.interval_clear(self.REWARD_ACTION, interval=2)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            page_ready = self._daily_page_ready()
            # Inspect without an action interval. A still-ready chest during
            # click cooldown must keep the completion timer reset. The timer
            # also resets on every overlay, so a popup cannot prove receipt.
            ready = (
                self._get_ready_reward_button(self.REWARD_BUTTONS, self.REWARD_ACTION)
                if page_ready else None
            )
            if page_ready and ready is None:
                if settled.reached():
                    return True, clicked
            else:
                settled.reset()
            if timeout.reached():
                logger.warning("Daily rewards: no verified completion before timeout")
                return False, clicked

            if ready is not None and self.interval_is_reached(self.REWARD_ACTION, interval=2):
                self.device.click(ready)
                self.interval_reset(self.REWARD_ACTION, interval=2)
                clicked = True
                continue
            if page_ready:
                continue
            if self.handle_network_error():
                settled.reset()
                continue
            if self.handle_touch_to_close(interval=2):
                settled.reset()
                continue

    def run(self) -> bool:
        """Refresh daily progress and return control to the normal scheduler.

        Pages:
            in: any supported navigation page
            out: page_main
        """
        logger.hr(self.DAILY_TASK_TITLE, level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        context = daily_snapshot_context(self.config)
        day = get_server_last_update(self.config.Scheduler_ServerUpdate).isoformat()
        # Invalidate before touching the UI. A crash, reset or unreadable list
        # must not leave an older complete/partial plan eligible for scheduling.
        self.config.cross_set(DAILY_PLAN_PATH, {})
        self._enter_mission_reward(skip_first_screenshot=False)
        points = self._read_daily_points()
        rewards_claimed, clicked = self._claim_daily_rewards_verified()
        missions = None
        if rewards_claimed:
            points = self._read_daily_points()
            if points is not None and points < 100:
                missions = self._scan_daily_missions(skip_first_screenshot=True)

        current_day = get_server_last_update(self.config.Scheduler_ServerUpdate).isoformat()
        valid = (points is not None and day == current_day
                 and context == daily_snapshot_context(self.config))
        plan = {}
        if valid:
            plan = {
                **context,
                "day": day,
                "points": points,
                "rewards_claimed": rewards_claimed,
                "missions": [asdict(mission) for mission in missions] if missions is not None else [],
            }
        with self.config.multi_set():
            self.config.cross_set(DAILY_PLAN_PATH, plan)
            if valid:
                self.config.stored.DailyActivity.set(points)

        self.ui_goto(page_main, skip_first_screenshot=True)
        if clicked and rewards_claimed:
            self.config.task_call("DataUpdate", force_call=False)

        if valid and rewards_claimed and (points >= 100 or missions is not None):
            acknowledge_daily_review(self.config)
            if points >= 100:
                logger.info("Daily goal complete: 100 activity and all available rewards claimed")
            else:
                logger.info(f"Daily activity {points}/100; prioritize enabled tasks when due")
            self.config.task_delay(server_update=True)
            return True
        logger.warning("Daily observation incomplete; retry without dispatching an old plan")
        self.config.task_delay(minute=self.DAILY_RETRY_MINUTES)
        return False
