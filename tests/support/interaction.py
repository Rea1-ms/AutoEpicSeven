"""Fresh observations independent of clicks, with production intervals/guards."""
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

from module.base.timer import Timer
from module.device.device import Device
from tests.support.offline import record_action, record_frame


def scene(*buttons, page=None, **values):
    return dict(buttons=frozenset(buttons), page=page, **values)


class InteractionReplay:
    # Reuse only pure guard methods; constructing a real Device is forbidden.
    stuck_record_add = Device.stuck_record_add
    stuck_record_clear = Device.stuck_record_clear
    stuck_record_check = Device.stuck_record_check
    click_record_add = Device.click_record_add
    click_record_clear = Device.click_record_clear
    click_record_check = Device.click_record_check
    handle_control_check = Device.handle_control_check

    def __init__(self, task_type, frames, clock, *, guards=True):
        self.frames, self.clock, self.index = frames, clock, -1
        self.actions, self.intervals = [], []
        self.guards = guards
        self.detect_record, self.click_record = set(), deque(maxlen=30)
        self.stuck_timer = Timer(60, count=60).start()
        self.task = object.__new__(task_type)
        self.task.device = self
        self.task.interval_timer = {}
        self.values = {}
        self.task.config = SimpleNamespace(
            Emulator_GameLanguage='cn', Emulator_PackageName='com.stove.epic7.google',
            Scheduler_ServerUpdate='02:00', cross_get=lambda key, default=None: self.values.get(key, default),
            cross_set=lambda key, value: self.values.__setitem__(key, value),
            task_call=Mock(), task_delay=Mock(),
            GachaResult_SaveScreenshot=False, GachaResult_OcrResult=False,
        )
        self.task.appear = self.appear
        self.task.match_template_color = self.appear
        self.task.match_template_luma = self.appear
        self.task.ui_page_appear = lambda page, **kw: self.frame['page'] == page.name
        self.task.ui_additional = lambda: False
        self.screenshot()

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        if self.guards:
            self.stuck_record_check()
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Interaction replay exhausted before a verified exit or recovery')
        self.clock.advance(self.frame.get('seconds', 0.5))
        self.image = self.frame
        record_frame(f'interaction-synthetic:{self.index}')

    def appear(self, button, interval=0, **kwargs):
        self.stuck_record_add(button)
        if interval and not self.task.interval_is_reached(button, interval=interval):
            return False
        found = button.name in self.frame['buttons']
        if found and interval:
            self.task.interval_reset(button, interval=interval)
        return found

    def click(self, button):
        if self.guards:
            self.handle_control_check(button)
        self.actions.append(record_action((self.index, button.name)))

    def swipe(self, *args, **kwargs):
        self.actions.append(record_action((self.index, 'swipe')))

    def sleep(self, seconds):
        self.clock.advance(seconds)

    def app_is_running(self):
        return True

    def screenshot_interval_set(self, *args):
        self.intervals.append(args)

    def clicks(self, name):
        return [index for index, action in self.actions if action == name]

    def assert_retried(self, testcase, name, minimum_interval):
        indexes = self.clicks(name)
        testcase.assertGreaterEqual(len(indexes), 2, f'{name} never retried: {self.actions}')
        times = [sum(f.get('seconds', 0.5) for f in self.frames[:i + 1]) for i in indexes]
        testcase.assertTrue(all(b - a >= minimum_interval for a, b in zip(times, times[1:])), self.actions)
