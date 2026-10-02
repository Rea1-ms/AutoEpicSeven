# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline Koharu task claims, event dispatch, and battle-triggered rechecks."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

from tasks.activity.koharu_raffle import (
    KoharuRaffle, CHUN_ALL_TASK_DONE, CHUN_GATE_CHECK, CHUN_GATE_SELECTED,
    CHUN_TASK_REWARD_NONE, CHUN_TASK_REWARD_PENDING,
)

EVENT_ID = 'koharu_raffle_2026_09_17'
NOW = datetime.fromisoformat('2026-09-19T12:00:00+08:00')
SCREENSHOTS = Path(__file__).parent / 'screenshots/koharu_raffle'


class Config:
    SpecialActivity_BuyHucheMysticMedals = False
    Emulator_PackageName = 'com.stove.epic7.google'
    Emulator_GameLanguage = 'auto'
    Scheduler_ServerUpdate = '02:00'
    LimitedActivity_GetKoharuRaffleReward = True
    enabled = True

    def __init__(self):
        self.values = {}
        self.delays = []

    def cross_get(self, path, default=None):
        return self.values.get(path, default)

    def cross_set(self, path, value):
        self.values[path] = value

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)

    def is_task_enabled(self, task):
        return self.enabled


class Claim(KoharuRaffle):
    def __init__(self, frames):
        self.config = Config()
        self.activity_id = EVENT_ID
        self.frames = frames
        self.index = -1
        self.actions = []
        self.routes = []
        self.device = SimpleNamespace(screenshot=self.screenshot, click=self.click,
                                      swipe=self.swipe, app_is_running=lambda: True)
        self.device.screenshot()

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Claim did not finish on supplied frames')

    def click(self, button):
        assert button is CHUN_TASK_REWARD_PENDING
        self.actions.append('claim')

    def swipe(self, start, end, **kwargs):
        self.actions.append('top')
        assert 260 < start[0] < 730 and end[1] > start[1]

    def select_activity(self, keyword, selected):
        assert keyword == '收集抽奖券' and selected is CHUN_GATE_SELECTED
        return True

    def ui_goto(self, page, **kwargs):
        self.routes.append(page)

    def match_template_color(self, button, **kwargs):
        if button is CHUN_GATE_CHECK:
            return self.frame in ('pending', 'none', 'done', 'scrolled')
        expected = {CHUN_TASK_REWARD_PENDING: 'pending',
                    CHUN_TASK_REWARD_NONE: 'none', CHUN_ALL_TASK_DONE: 'done'}
        return self.frame == expected[button]

    def handle_touch_to_close(self):
        if self.frame == 'popup':
            self.actions.append('close')
            return True
        return False

    def handle_network_error(self):
        if self.frame == 'network':
            self.actions.append('network')
            return True
        return False





__all__ = ['Claim']
