"""Meirin screenshots and explicit synthetic states with production timers."""

from itertools import pairwise
from pathlib import Path
from unittest.mock import Mock

from module.config import server
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import fixture_image, record_frame

server.set_lang('global_cn')

from tasks.activity.meirin_reward import MeirinReward

MANIFEST = Path(__file__).resolve().parents[1] / 'fixtures/historical/manifest.json'
EVENT_ID = 'meirin_reward_2026_10_08'
AVAILABLE = 'meirin-reward-available'
AVAILABLE_SHIFTED = 'meirin-reward-available-shifted'
UNAVAILABLE = 'meirin-reward-unavailable'
POPUP = 'meirin-reward-popup'
LOADING = 'meirin-reward-loading'
SIDEBAR = 'meirin-reward-sidebar-visible'
CAPTURES = (
    'meirin-reward-home', 'meirin-reward-sidebar-top', SIDEBAR,
    AVAILABLE, AVAILABLE_SHIFTED, LOADING, POPUP, UNAVAILABLE,
)


class Config:
    Emulator_PackageName = 'com.stove.epic7.google'
    Emulator_GameLanguage = 'auto'
    Scheduler_ServerUpdate = '02:00'
    LimitedActivity_GetMeirinReward = True
    LimitedActivity_GetKoharuRaffleReward = False
    LimitedActivity_GetFreeGacha = False
    SpecialActivity_BuyHucheMysticMedals = False
    enabled = True

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
        return self.enabled


def captured(fixture, *, complete=False, seconds=0.5):
    return scene(fixture=fixture, complete=complete, seconds=seconds, page='page_common_activity')


class MeirinReplay(InteractionReplay):
    """Advance captures only on screenshot calls; clicking never changes a frame."""

    def __init__(self, frames, clock):
        self.images = {}
        self.action_times = []
        super().__init__(MeirinReward, frames, clock)
        self.task.config = Config()
        self.task.activity_id = EVENT_ID
        self.task.match_template_color = self.match_template_color
        self.task.match_template_luma = self.match_template_luma
        self.task.ui_goto = Mock()

    def screenshot(self):
        super().screenshot()
        fixture = self.frame.get('fixture')
        if fixture:
            if fixture not in self.images:
                self.images[fixture] = fixture_image(MANIFEST, fixture)
            self.image = self.images[fixture].copy()
            record_frame(fixture)
            if self.frame.get('complete'):
                # No capture shows all tasks complete. This explicitly synthetic
                # first-row check moves the genuine last-row icon upward by 500
                # pixels and removes "Go now"; it proves the rule, not a live
                # completion screen. The unmodified source remains a negative.
                self.image[166:234, 795:885] = self.image[190, 780]
                self.image[186:216, 818:857] = self.images[fixture][686:716, 818:857]
                record_frame('synthetic:meirin-first-row-completed')

    def appear(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return MeirinReward.appear(self.task, button, interval=interval, **kwargs)
        return super().appear(button, interval=interval, **kwargs)

    def match_template_color(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return MeirinReward.match_template_color(self.task, button, interval=interval, **kwargs)
        return self.appear(button, interval=interval, **kwargs)

    def match_template_luma(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return MeirinReward.match_template_luma(self.task, button, interval=interval, **kwargs)
        return self.appear(button, interval=interval, **kwargs)

    def click(self, button):
        super().click(button)
        self.action_times.append((self.index, button.name, self.clock.now))

    def assert_retry_interval(self, testcase, name, seconds):
        times = [at for _, action, at in self.action_times if action == name]
        testcase.assertGreaterEqual(len(times), 2, self.actions)
        testcase.assertTrue(all(b - a > seconds for a, b in pairwise(times)), self.action_times)
