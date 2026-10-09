"""Shared offline harnesses; callers initialize server state before importing."""

from types import SimpleNamespace
from unittest.mock import patch
from tasks.activity.common_activity import CommonActivityBatch
from tests.support.history_e7wc_battle_gate import HISTORICAL_ACTIVITIES


F, G, K = 'free_gacha_20', 'e7wc_battle_gate', 'koharu_raffle'


MODES = (F, G, K)


class Batch(CommonActivityBatch):
    ACTIVITIES = HISTORICAL_ACTIVITIES

    def __init__(self, frames):
        self.config = SimpleNamespace(Emulator_GameLanguage='auto')
        self.frames = frames
        self.index = -1
        self.claims, self.clicks, self.swipes = [], [], []
        self.device = SimpleNamespace(
            image=None, screenshot=self.screenshot,
            click=lambda button: self.clicks.append((button.text, self.index)),
            swipe=lambda start, end, **kwargs: self.swipes.append((start, end)),
        )
        self.screenshot()

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Batch did not finish within supplied frames')
        self.device.image = self.frames[self.index]

    def ui_page_appear(self, page):
        return self.device.image.get('ready', True)

    def _activity_selected(self, button):
        selected = self.device.image['selected']
        selected = selected if isinstance(selected, tuple) else (selected,)
        return any(mode in MODES and self.ACTIVITIES[mode][1] is button for mode in selected)

    def interval_is_reached(self, *args, **kwargs):
        return True

    def interval_reset(self, *args, **kwargs):
        pass

    def handle_network_error(self):
        return self.device.image.get('network', False)

    def check(self, modes=MODES, failed=None):
        pending = {}
        for mode in modes:
            keyword, selected, _ = self.ACTIVITIES[mode]

            def claim(mode=mode, *, navigate):
                assert navigate is False
                assert self.device.image['selected'] == mode
                self.claims.append(mode)
                return mode != failed

            pending[mode] = (self._activity_text(keyword), selected, SimpleNamespace(run_claim=claim))
        with patch('tasks.activity.common_activity.Ocr') as ocr, patch('tasks.activity.common_activity.Timer') as timer:
            ocr.return_value.detect_and_ocr.side_effect = lambda image: image['rows']
            timer.return_value.start.return_value.reached.side_effect = lambda: self.device.image.get('timeout', False)
            result = self._run_pending(pending)
        self.ocr = ocr
        return result
