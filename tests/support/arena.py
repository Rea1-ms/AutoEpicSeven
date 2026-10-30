"""Arena round replays with production intervals and explicit observations."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from module.config import server
from module.device.device import Device
from tests.support.interaction import InteractionReplay
from tests.support.offline import fixture_image, record_frame

server.set_lang('global_cn')

from tasks.arena.arena import Arena
from tasks.arena.assets.assets_arena import CHALLENGE

MANIFEST = Path(__file__).resolve().parents[1] / 'fixtures/arena/manifest.json'


class ArenaReplay(InteractionReplay):
    """Only observations are substituted; clicks never advance the frame."""

    click_record_remove = Device.click_record_remove

    def __init__(self, frames, clock):
        self.images = {}
        self.action_times = []
        super().__init__(Arena, frames, clock)
        self.task._is_arena_combat_home_ready = self.home_ready
        self.task._is_challenge_ready = self.challenge_ready
        self.task._is_challenge_exhausted = self.challenge_exhausted
        self.task._ocr_fast_battle_times = self.fast_times
        self.task.handle_network_error = lambda: False
        self.task.handle_popup_cancel = lambda **kw: False
        value, total = self.frame.get('flags') or (5, 5)
        self.flags = SimpleNamespace(value=value, total=total, set=self.store_flags)
        self.task.config.stored = SimpleNamespace(ArenaFlag=self.flags)
        self.task.config.multi_set = nullcontext
        self.task._ocr_arena_flag_status = Mock(side_effect=self.flag_status)
        self.task._consume_stored_arena_flags = Mock(
            side_effect=lambda count: Arena._consume_stored_arena_flags(self.task, count),
        )

    def screenshot(self):
        super().screenshot()
        fixture = self.frame.get('fixture')
        if fixture:
            if fixture not in self.images:
                self.images[fixture] = fixture_image(MANIFEST, fixture)
            self.image = self.images[fixture].copy()
            record_frame(fixture)

    def click(self, button):
        super().click(button)
        self.action_times.append((button.name, self.clock.now))

    def appear(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return Arena.appear(self.task, button, interval=interval, **kwargs)
        return super().appear(button, interval=interval, **kwargs)

    def home_ready(self):
        if self.frame.get('fixture'):
            return Arena._is_arena_combat_home_ready(self.task)
        return self.frame.get('home_ready', False)

    def challenge_ready(self, interval=0):
        if self.frame.get('fixture'):
            return Arena._is_challenge_ready(self.task, interval=interval)
        return self.frame.get('challenge_ready', False) and self.appear(CHALLENGE, interval=interval)

    def challenge_exhausted(self):
        if self.frame.get('fixture'):
            return Arena._is_challenge_exhausted(self.task)
        return self.frame.get('challenge_exhausted', False)

    def fast_times(self):
        if self.frame.get('fixture'):
            return Arena._ocr_fast_battle_times(self.task)
        return self.frame.get('fast_times', (9, 1, 10))

    def store_flags(self, value, total):
        self.flags.value, self.flags.total = value, total

    def flag_status(self, skip_first_screenshot=True):
        if not skip_first_screenshot:
            self.screenshot()
        if self.frame.get('fixture'):
            return Arena._ocr_arena_flag_status(self.task)
        status = self.frame.get('fresh_flags', self.frame.get('flags', (5, 5)))
        if status is not None:
            self.store_flags(*status)
        return status
