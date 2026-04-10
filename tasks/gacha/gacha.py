"""
Epic Seven 召唤模块

流程:
    MAIN_GOTO_GACHA -> 进入召唤页 -> 选择常驻池
    优先点击免费10抽，其次免费1抽
    抽卡流程:
        SUMMON_NEW -> 截图 + 点击去遮罩
        SUMMON_NEXT_PAGE
        SUMMON_SKIP
        SUMMON_RESULT_BACK + SUMMON_FREE_CONTINUE -> 截图 -> 点击继续
        SUMMON_RESULT_BACK -> 截图 -> 返回召唤页
"""
from datetime import datetime
from pathlib import Path
import json

from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from tasks.base.page import page_gacha
from tasks.base.ui import UI
from tasks.gacha.assets.assets_gacha import (
    EPIC_BOOKMARK,
    GACHA_STANDARD_TAB,
    GOLDEN_INHERITANCE_FULL,
    SUMMON_TEN_FREE,
    SUMMON_ONE_FREE,
    SUMMON_NEW,
    SUMMON_SKIP,
    SUMMON_NEXT_PAGE,
    SUMMON_RESULT_BACK,
    SUMMON_FREE_CONTINUE,
)



class Gacha(UI):
    """
    召唤任务（常驻池）
    """

    TAB_SWIPE_START = (105, 600)
    TAB_SWIPE_END = (105, 300)
    GOLDEN_INHERITANCE_TIMEOUT_SECONDS = 6

    def __init__(self, config, device=None, task=None):
        super().__init__(config, device=device, task=task)
        self._draw_count = 0
        self._draw_free = False
        self._in_standard_pool = False
        self._no_free = False

    def _save_result(self, tag="result"):
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        ts = now.strftime("%Y%m%d_%H%M%S_%f")
        folder = Path("log/gacha") / day
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

    def _enter_gacha(self):
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        self.ui_goto(page_gacha)

    def _select_standard_tab(self) -> bool:
        logger.info("Select standard tab")
        swipe_timer = Timer(1, count=2).start()
        timeout = Timer(15, count=30).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Select standard tab timeout")
                return False

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

            if not self.ui_page_appear(page_gacha):
                self.ui_goto(page_gacha)
                timeout.reset()
                continue

            if self.appear(EPIC_BOOKMARK, interval=1, similarity=0.8):
                self._in_standard_pool = True
                return True

            if self.appear_then_click(GACHA_STANDARD_TAB, interval=2):
                continue

            if swipe_timer.reached():
                self.device.swipe(self.TAB_SWIPE_START, self.TAB_SWIPE_END, duration=(0.25, 0.35))
                swipe_timer.reset()
                continue

    def _start_summon(self) -> bool:
        logger.info("Start summon")
        timeout = Timer(10, count=20).start()
        no_free_timer = Timer(2, count=4).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("No free summon found")
                return False

            if self.ui_additional():
                no_free_timer.reset()
                continue
            if self.handle_network_error():
                no_free_timer.reset()
                continue

            if not self.ui_page_appear(page_gacha):
                self.ui_goto(page_gacha)
                timeout.reset()
                no_free_timer.reset()
                continue

            if self.appear(EPIC_BOOKMARK, interval=1, similarity=0.8):
                self._in_standard_pool = True

            if not self._in_standard_pool:
                if not self._select_standard_tab():
                    return False
                no_free_timer.reset()
                continue

            if self.appear_then_click(SUMMON_TEN_FREE, interval=2, similarity=0.9):
                self._draw_count = 10
                self._draw_free = True
                return True

            if self.appear_then_click(SUMMON_ONE_FREE, interval=2, similarity=0.9):
                self._draw_count = 1
                self._draw_free = True
                return True

            if self._in_standard_pool and no_free_timer.reached():
                logger.info("Free summon already used today, skip")
                self._no_free = True
                return False

    def _handle_summon_flow(self):
        logger.info("Summon flow")
        timeout = Timer(120, count=240).start()
        result_saved = False

        # Slow down screenshot interval during animation
        self.device.screenshot_interval_set(1.0)
        try:
            while 1:
                self.device.screenshot()

                if timeout.reached():
                    logger.warning("Summon flow timeout")
                    break

                # 1) New overlay
                if self.appear(SUMMON_NEW, interval=1):
                    if not result_saved:
                        self._save_result(tag="new")
                        result_saved = True
                    self.device.click(SUMMON_NEW)
                    continue

                # 2) Skip animation
                if self.appear_then_click(SUMMON_SKIP, interval=1):
                    continue

                # 3) Next page (optional)
                if SUMMON_NEXT_PAGE is not None:
                    if self.appear_then_click(SUMMON_NEXT_PAGE, interval=1):
                        continue

                # 4) Result page
                back = self.appear(SUMMON_RESULT_BACK)
                free_continue = self.appear(SUMMON_FREE_CONTINUE)
                if back and free_continue:
                    self._save_result(tag="result")
                    result_saved = True
                    self.device.click(SUMMON_FREE_CONTINUE)
                    result_saved = False
                    timeout.reset()
                    continue
                if back:
                    self._save_result(tag="result")
                    self.device.click(SUMMON_RESULT_BACK)
                    self._wait_return_to_gacha()
                    break

                if self.ui_additional():
                    timeout.reset()
                    continue
                if self.handle_network_error():
                    timeout.reset()
                    continue

        finally:
            self.device.screenshot_interval_set()

    def _wait_return_to_gacha(self):
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()
            if timeout.reached():
                break
            if self.ui_page_appear(page_gacha):
                break
            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    def _collect_golden_inheritance_full(self, skip_first_screenshot=True) -> bool:
        if not getattr(self.config, "Gacha_CollectGoldenInheritance", True):
            return False

        logger.info("Collect golden inheritance if full")
        timeout = Timer(self.GOLDEN_INHERITANCE_TIMEOUT_SECONDS, count=18).start()
        no_action_confirm = Timer(1.5, count=4).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                return False

            if self.appear_then_click(GOLDEN_INHERITANCE_FULL, interval=1):
                logger.info("Golden inheritance full handled")
                return True

            if self.ui_additional():
                timeout.reset()
                no_action_confirm.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                no_action_confirm.reset()
                continue

            if no_action_confirm.reached():
                logger.info("Golden inheritance not full, skip")
                return False

    def run(self):
        logger.hr("Gacha", level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_start()
        self._no_free = False
        self._enter_gacha()
        if not self._select_standard_tab():
            return False
        if not self._start_summon():
            if self._no_free:
                self._collect_golden_inheritance_full(skip_first_screenshot=True)
                self.config.task_delay(server_update=True)
                return True
            return False
        self._handle_summon_flow()
        self._collect_golden_inheritance_full(skip_first_screenshot=True)
        self.config.task_delay(server_update=True)
        return True
