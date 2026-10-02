# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

from tests.support.offline import OfflineAssertions

"""Offline regression checks for the September CN event and repeat-combat update."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

from tasks.activity.free_gacha_20 import FreeGacha20


class Config:
    SpecialActivity_BuyHucheMysticMedals = False
    Emulator_PackageName = "com.zlongame.cn.epicseven"
    Emulator_GameLanguage = "auto"
    Scheduler_ServerUpdate = "03:00"
    LimitedActivity_GetFreeGacha = True
    SpecialActivity_GetTaskReward = True
    SpecialActivity_GetDailyReward = True

    def __init__(self):
        self.values = {}
        self.delays = []
        self.calls = []

    def cross_get(self, path, default=None):
        return self.values.get(path, default)

    def cross_set(self, path, value):
        self.values[path] = value

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)

    def task_call(self, task, **kwargs):
        self.calls.append((task, kwargs))

    def is_task_enabled(self, task):
        return True


class Claim(FreeGacha20):
    def __init__(self, frames):
        self.config = Config()
        self.activity_id = "test_campaign"
        self.frames = frames
        self.frame = -1
        self.clicks = 0
        self.popups = 0
        self.device = SimpleNamespace(screenshot=self.screenshot)
        self.device.screenshot()

    def screenshot(self):
        self.frame += 1
        if self.frame >= len(self.frames):
            raise AssertionError("Claim did not terminate on the supplied frames")

    def select_activity(self, *args, **kwargs):
        return True

    def ui_goto(self, *args, **kwargs):
        pass

    def appear(self, button, **kwargs):
        return button.name == "FREE_20_GACHA_OBTAINED" and self.frames[self.frame] == "obtained"

    def appear_then_click(self, button, **kwargs):
        if button.name == "FREE_20_GACHA" and self.frames[self.frame] == "claim":
            self.clicks += 1
            return True
        return False

    def handle_touch_to_close(self, **kwargs):
        if self.frames[self.frame] == "popup":
            self.popups += 1
            return True
        return False

    def handle_network_error(self):
        return False




class NavigationTests(OfflineAssertions):
    def navigate(self, frames, up_limit=4, down_limit=8):
        from tasks.activity.navigation import ActivityNavigationMixin
        from tasks.activity.assets.assets_activity_special_26_8_27 import FREE_20_GACHA_SELECTED

        class Navigation(ActivityNavigationMixin):
            def __init__(self):
                self.config = Config()
                self.frame = -1
                self.clicks = []
                self.swipes = []
                self.device = SimpleNamespace(screenshot=self.screenshot, click=self.click,
                    swipe=self.swipe, image=None)
                self.ACTIVITY_SCROLL_UP_LIMIT = up_limit
                self.ACTIVITY_SCROLL_DOWN_LIMIT = down_limit
                self.screenshot()

            def screenshot(self):
                self.frame += 1
                if self.frame >= len(frames):
                    raise AssertionError("Navigation failed to terminate")
                self.device.image = self.frame

            def click(self, button):
                self.clicks.append((self.frame, button.text, button.button))

            def swipe(self, start, end, **kwargs):
                self.swipes.append((start, end))

            def match_template_color(self, button):
                return frames[self.frame].get("selected", False)

            def ui_page_appear(self, page):
                return True

            def interval_is_reached(self, *args, **kwargs):
                return True

            def interval_reset(self, *args, **kwargs):
                pass

            def handle_network_error(self):
                return False

        def ocr_results(index):
            frame = frames[index]
            text = frame.get("text", "INFINITY00")
            y = frame.get("y", 200)
            return [SimpleNamespace(ocr_text=text, box=(20, y, 140, y + 20), score=1)]

        with patch("tasks.activity.navigation.Ocr") as ocr:
            ocr.return_value.detect_and_ocr.side_effect = ocr_results
            nav = Navigation()
            success = nav.select_activity("INFINITY", FREE_20_GACHA_SELECTED)
            self.assertEqual(ocr.call_args.kwargs["lang"], "cn")
        return nav, success

__all__ = ['Claim', 'NavigationTests']
