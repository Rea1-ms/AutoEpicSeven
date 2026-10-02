# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root

"""Offline state-machine cases: visible work first, one shared sidebar sweep."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]

import module.config.server as server
from tasks.activity.common_activity import CommonActivityBatch

F, G, K = 'free_gacha_20', 'e7wc_battle_gate', 'koharu_raffle'
MODES = (F, G, K)


def frame(selected=None, rows=(), **kwargs):
    return dict(selected=selected, rows=[
        SimpleNamespace(ocr_text=CommonActivityBatch.ACTIVITIES[name][0] if name in MODES else name,
                        box=(25, y, 235, y + 20), score=1)
        for name, y in rows
    ], **kwargs)


class Batch(CommonActivityBatch):
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







class RealScreenshotBatchTests(unittest.TestCase):
    def test_all_pending_templates_choose_only_the_actual_selected_event(self):
        from tests.support.history_fixtures import read_input as load_image
        from unittest.mock import Mock

        server.set_lang('global_cn')
        fixtures = input_root()
        screenshots = {
            F: fixtures / 'activity_navigation/infinity_selected_oversea.png',
            G: fixtures / 'e7wc_battle_gate/battle_gate_available.png',
            K: fixtures / 'koharu_raffle/available.png',
        }
        for expected, path in screenshots.items():
            with self.subTest(event=expected):
                batch = object.__new__(CommonActivityBatch)
                batch.config = SimpleNamespace(Emulator_GameLanguage='auto')
                batch.interval_timer = {}
                batch.device = SimpleNamespace(image=load_image(str(path)), stuck_record_add=lambda button: None)
                if expected == F:
                    # This fixture retains only the real sidebar; the header and
                    # reward panel are masked out, so page detection is not under test.
                    batch.ui_page_appear = lambda page: True
                workers = {mode: SimpleNamespace(run_claim=Mock(return_value=False)) for mode in MODES}
                pending = {mode: (keyword, button, workers[mode])
                           for mode, (keyword, button, _) in batch.ACTIVITIES.items()}
                with patch('tasks.activity.common_activity.Ocr') as ocr, patch(
                    'tasks.activity.common_activity.Timer'
                ) as timer:
                    timer.return_value.start.return_value.reached.return_value = True
                    self.assertFalse(batch._run_pending(pending))
                workers[expected].run_claim.assert_called_once_with(navigate=False)
                for mode in MODES:
                    if mode != expected:
                        workers[mode].run_claim.assert_not_called()
                ocr.return_value.detect_and_ocr.assert_not_called()
