"""Shared offline harnesses; callers initialize server state before importing."""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from tasks.base.page import page_main
from tasks.base.ui import UI


def timer_factory(clock):
    class Timer:
        def __init__(self, limit, count=0):
            self.limit, self.count, self.access, self.deadline = limit, count, 0, None
        def reset(self):
            self.deadline, self.access = clock() + self.limit, 0
            return self
        def start(self):
            if self.deadline is None:
                self.reset()
            return self
        def reached(self):
            self.access += 1
            return self.deadline is None or (clock() >= self.deadline and self.access > self.count)
    return Timer


class Route(UI):
    def __init__(self, frames, initial_image=True):
        self.frames, self.index = frames, 0
        self.clicks = []
        self.config = SimpleNamespace(Emulator_GameLanguage='auto')
        self.device = SimpleNamespace(
            image=None, screenshot=self.screenshot, click=self.click,
            stuck_record_add=Mock(),
        )
        self.interval_timer = {}
        self.interval_clear = Mock()
        if initial_image:
            self.device.image = self.frame['image']
        else:
            self.index = -1
    @property
    def frame(self):
        return self.frames[max(0, self.index)]
    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Navigation did not finish on supplied frames')
        self.device.image = self.frame['image']
    def click(self, button):
        self.clicks.append((self.frame['at'], button, tuple(button.button)))
    def is_in_main(self, interval=0):
        return page_main in self.frame['pages'] and not self.frame.get('overlay', False)
    def ui_page_appear(self, page, interval=0):
        assert interval == 0
        return self.is_in_main() if page == page_main else page in self.frame['pages']
    def ui_page_confirm(self, page):
        if self.frame.get('confirm_next'):
            self.screenshot()
        return False
    def handle_ui_recovery(self):
        return self.frame.get('recovery', False)
    def ui_additional(self):
        return False
    def handle_popup_confirm(self):
        return False
    def run_route(self, destination):
        self.reader = Mock()
        self.reader.ocr_single_line.side_effect = lambda image: self.frame.get('rank', '')
        with patch('tasks.base.account_level.Ocr', return_value=self.reader), patch(
            'tasks.base.ui.Timer', timer_factory(lambda: self.frame['at'])
        ):
            self.ui_goto(destination)
