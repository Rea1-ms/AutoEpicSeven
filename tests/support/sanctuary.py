"""Sanctuary fixtures with explicit capture servers and offline frame replays."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from module.config import server
from tests.support.offline import ROOT, fixture_image, record_action, record_frame

server.set_lang('global_cn')

from tasks.sanctuary.monthly import ALREADY_STORED, CUSTODY, POPUP_CANCEL, PURIFY  # noqa: E402
from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import (  # noqa: E402
    DEPOSIT_REWARD_TIER_A,
    DEPOSIT_REWARD_TIER_B,
    DEPOSIT_REWARD_TIER_S,
)
from tasks.sanctuary.sanctuary import Sanctuary  # noqa: E402


PROJECT_ROOT = ROOT
MANIFEST = ROOT / 'tests' / 'fixtures' / 'sanctuary' / 'manifest.json'
FIXTURE_IDS = {
    'full_s_20260921.png': 'sanctuary-full-s-20260921',
    'full_b_20260921.png': 'sanctuary-full-b-20260921',
    'full_a_20260921.png': 'sanctuary-full-a-20260921',
    'full_a_stable_20260921.png': 'sanctuary-full-a-stable-20260921',
}


def screenshot(name):
    return captured_screenshot(FIXTURE_IDS[name])


def captured_screenshot(fixture_id, *, expected_server='global_cn'):
    """Check the capture's source server, independent of active asset dispatch."""
    item = json.loads(MANIFEST.read_text(encoding='utf-8'))['fixtures'][fixture_id]
    if item['server'] != expected_server:
        raise ValueError(f"Capture server mismatch: {fixture_id} is {item['server']}, expected {expected_server}")
    if item.get('capture_type') != 'emulator_screenshot':
        raise ValueError(f'Fixture is not a captured emulator screenshot: {fixture_id}')
    return fixture_image(MANIFEST, fixture_id)


class CapturedMonthlyReplay:
    """Replay explicit captured observations without inferring unrecorded actions."""

    def __init__(self, initial_id, fixture_ids, clock):
        self.fixture_ids = iter(fixture_ids)
        self.clock = clock
        self.frames_read = 0
        self.actions = []
        self.task = Sanctuary.__new__(Sanctuary)
        self.task.device = SimpleNamespace(
            image=captured_screenshot(initial_id), screenshot=self.screenshot, click=self.click,
        )
        self.task.appear = self.appear
        self.task.handle_touch_to_close = lambda **kwargs: False
        self.task.ui_additional = lambda: False
        self.task.handle_network_error = lambda: False

    def screenshot(self):
        try:
            fixture_id = next(self.fixture_ids)
        except StopIteration:
            raise AssertionError('Custody did not finish within captured observations') from None
        self.task.device.image = captured_screenshot(fixture_id)
        self.frames_read += 1
        self.clock.advance(1)

    def appear(self, asset, interval=0, **kwargs):
        return asset.match_template(self.task.device.image, **kwargs)

    def click(self, asset):
        self.actions.append(record_action(asset.name))


# Preserve the historical replay's elapsed-time contract. Explicit frame
# timestamps drive deadlines independently of machine speed and access counts;
# changing that contract would invalidate the original action assertions.
def replay_timer(clock):
    class ClockTimer:
        def __init__(self, limit, count=0):
            self.limit, self.deadline = limit, None
        def start(self):
            if self.deadline is None:
                self.reset()
            return self
        def reset(self):
            self.deadline = clock() + self.limit
            return self
        def clear(self):
            self.deadline = None
            return self
        def reached(self):
            return self.deadline is None or clock() >= self.deadline
    return ClockTimer


class MonthlyReplay:
    """Synthetic asset placement plus recorded counter values; no device access."""
    def __init__(self, frames, mode='Smart'):
        import numpy as np
        from PIL import Image
        from tasks.sanctuary.monthly import DEPOSIT_BOX_NOT_FULL
        self.frames, self.index, self.at, self.actions = [{}] + frames, -1, 0, []
        self.task = Sanctuary.__new__(Sanctuary)
        self.task.config = SimpleNamespace(SanctuaryMonthly_RewardTier=mode, task_delay=lambda **kw: None)
        self.task.interval_timer = {}
        self.task.device = SimpleNamespace(
            image=None, screenshot=self.screenshot, click=self.click,
            click_record_clear=lambda: None, stuck_record_add=lambda button: None,
        )
        self.np = np
        self.assets = (PURIFY, DEPOSIT_BOX_NOT_FULL, ALREADY_STORED)
        self.deposit_assets = {
            'S': DEPOSIT_REWARD_TIER_S,
            'A': DEPOSIT_REWARD_TIER_A,
            'B': DEPOSIT_REWARD_TIER_B,
        }
        self.crops = {}
        for button in (*self.assets, *self.deposit_assets.values()):
            asset = button.buttons[0]
            self.crops[button.name] = np.array(Image.open(PROJECT_ROOT / asset.file).convert('RGB'))[
                asset.area[1]:asset.area[3], asset.area[0]:asset.area[2]
            ]
        self.task._ocr_lang = lambda: 'cn'
        self.task._is_monthly_claimed = lambda: self.frame.get('claimed', False)
        self.task._ocr_purify_times = lambda *args, **kwargs: self.frame.get(
            'counter', (400, -390, 10, self.frame.get('layout', 'full'))
        )
        self.task.handle_popup_cancel = self.cancel
        self.task.handle_touch_to_close = lambda **kwargs: False
        self.task.ui_additional = lambda: False
        self.task.handle_network_error = lambda: False
        self.task.appear = self.appear
        self.task.appear_then_click = self.appear_then_click
        self.task._ocr_heart_level = lambda ocr: 11
        self.task._detect_current_reward_tier = lambda ocr: self.frame.get('tier', 'B')
        self.task._ensure_app_running = lambda: None
        self.task._enter_sanctuary = lambda: True
        self.task._back_to_sanctuary = lambda: True
        self.task._enter_monthly = lambda: True
        self.screenshot()

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Flow did not finish on supplied frames')
        self.at = self.frame.get('at', self.index * 0.5)
        record_frame(f'sanctuary-synthetic:{self.index}')
        image = self.np.zeros((720, 1280, 3), dtype=self.np.uint8)
        for button, key, default in zip(self.assets, ('purify', 'slot', 'stored'), (True, True, False)):
            if not self.frame.get(key, default):
                continue
            area = button.buttons[0].area
            offset = 126 if button is PURIFY and self.frame.get('layout', 'full') == 'full' else 0
            image[area[1]:area[3], area[0]+offset:area[2]+offset] = self.crops[button.name]
        # These are synthetic frames, not captured game screenshots. Paste
        # the actual tier templates into the five known slot positions so the
        # production matcher, rather than a mocked count, confirms the receipt.
        # The duplicate-click notice remains independent and cannot create a
        # deposit label merely because a frame declares it visible.
        deposit_tiers = self.frame.get('deposit_tiers', (None,) * 5)
        if len(deposit_tiers) != 5:
            raise AssertionError('Synthetic deposit frame must describe five slots')
        first_area = DEPOSIT_REWARD_TIER_S.buttons[0].area
        for slot, tier in enumerate(deposit_tiers):
            if tier is None:
                continue
            button = self.deposit_assets[tier]
            left = first_area[0] + slot * 87
            image[first_area[1]:first_area[3], left:left + first_area[2] - first_area[0]] = self.crops[button.name]
        self.task.device.image = image

    def appear(self, button, **kwargs):
        if button is POPUP_CANCEL:
            return self.frame.get('popup', False)
        if button is CUSTODY:
            return self.frame.get('custody', True)
        if button in self.assets:
            return button.match_template(self.task.device.image)
        return False

    def appear_then_click(self, button, **kwargs):
        if button is CUSTODY and self.frame.get('custody', True):
            self.click(button)
            return True
        return False

    def cancel(self, **kwargs):
        if self.frame.get('cancel', False):
            self.click(POPUP_CANCEL)
            return True
        return False

    def click(self, button):
        self.actions.append(record_action((self.index, button.name, tuple(button.button))))

    def run(self, public=False):
        clock = replay_timer(lambda: self.at)
        with patch('tasks.sanctuary.monthly.Timer', clock), patch('module.base.base.Timer', clock), patch.object(
            CUSTODY, 'match_color', side_effect=lambda *args, **kwargs: self.frame.get('custody', True)
        ):
            if public:
                return self.task.run_monthly_task()
            return self.task._monthly_purify()
