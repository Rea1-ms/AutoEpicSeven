import module.config.server as server_


from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_special_activity
from tasks.base.ui import UI


class SpecialActivity(UI):
    """
    Epic Seven Special Activity
    """

    SIGNIN_RATE_REWARD_LUMA_SIMILARITY = 0.8
    SIGNIN_RATE_REWARD_COLOR_THRESHOLD = 30

    def run_get_daily_reward(self, skip_first_screenshot=True) -> bool:

        self.goto(special_activity_daily_reward)

        logger.info("SpecialActivity: get daily reward")
        timeout = Timer(self.GET_DAILY_REWARD_FLOW_TIMEOUT_SECONDS, count=60).start()
        click_fast_combat_times = self.CLICK_FAST_COMBAT_TIMES
        self._reset_get_daily_reward_status_runtime()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # two meet
            if self.appear(LEAF_CHECKMARK) and self.appear(FAST_COMBAT_TIMES_CHECKMARK):
                logger.info("SpecialActivity: has obtained daily reward, skip task")
                return False

            # fast combat times >= 20
            if click_fast_combat_times >= 3:
                logger.info("SpecialActivity: can't obtain fast combat times, skip for now")
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

            # I forgot if x or touch to close
            if self.handle_ad_buff_x_close():
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue
    
    def run_get_task_reward(self, skip_first_screenshot=True) -> bool:

        self.goto(special_activity_task_reward)

        logger.info("SpecialActivity: get task reward")
        timeout = Timer(self.GET_TASK_REWARD_FLOW_TIMEOUT_SECONDS, count=60).start()
        self._reset_get_task_reward_status_runtime()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # checkmark or navigate right now
            if self.appear(TASK_CHECKMARK) or self.appear(TASK_NAVIGATE_NOW):
                logger.info("SpecialActivity: no task reward needs to be obtain, skip task")
                return False

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

        self.goto(special_activity_free_gacha)

        free_gacha_available = True

        logger.info("SpecialActivity: run free gacha")
        timeout = Timer(self.FREE_GACHA_FLOW_TIMEOUT_SECONDS, count=60).start()
        self._reset_free_gacha_status_runtime()
        try:
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if timeout.reached():
                    logger.warning("Special activity summon flow timeout")
                    break

                if self.appear(FREE_GACHA_UNAVAILABLE):
                    logger.info("SpecialActivity: no free gacha times, skip task")
                    break

                # 0) Start summon
                if self.appear(FREE_GACHA_AVAILABLE, interval=1):
                    self.device.click(FREE_SUMMON)
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
                free_continue = self.appear(FREE_GACHA_CONTINUE)
                if back and free_continue:
                    self._save_result(tag="result")
                    result_saved = True
                    self.device.click(FREE_GACHA_CONTINUE)
                    result_saved = False
                    timeout.reset()
                    continue
                if back:
                    self._save_result(tag="result")
                    self.device.click(SUMMON_RESULT_BACK)
                    self._wait_return_to_free_gacha()
                    break

                if self.ui_additional():
                    timeout.reset()
                    continue
                    
                if self.handle_network_error():
                    timeout.reset()
                    continue

        finally:
            self.device.screenshot_interval_set()
    
    def run_get_energy_drink(self, skip_first_screenshot=True) -> bool:

        self.goto(special_activity_energy_drink)

        logger.info("SpecialActivity: get energy drink")
        timeout = Timer(self.GET_ENERGY_DRINK_FLOW_TIMEOUT_SECONDS, count=60).start()
        self._reset_get_energy_drink_status_runtime()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # TODO: OCR required
            if self.appear(TASK_CHECKMARK) or self.appear(TASK_NAVIGATE_NOW):
                logger.info(
                    "SpecialActivity: no energy drink needs to be obtain, skip task"
                )
                return False

            if timeout.reached():
                logger.warning("SpecialActivity flow timeout")
                return False

            if self.appear_then_click(ENERGY_DRINK_OBTAIN, interval=1):
                timeout.reset()
                continue

            # I forgot if x or touch to close
            if self.handle_ad_buff_x_close():
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue


    # main special activity starter
    def run(self) -> bool:
        logger.hr("SpecialActivity", level=1)

        if server_.is_cn_server(self.config.Emulator_PackageName) :
            logger.info("SpecialActivity: cn server, skip task")
            return False

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        self.ui_goto(special_activity)

        run_get_daily_reward = self.config.SpecialActivity.GetDailyReward
        run_get_task_reward = self.config.SpecialActivity.GetTaskReward
        run_free_gacha = self.config.SpecialActivity.FreeGacha
        run_get_energy_drink = self.config.SpecialActivity.GetEnergyDrink

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

        self.config.task_delay(server_update=True)
        return success