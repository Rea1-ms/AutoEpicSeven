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
from module.base.button import ButtonWrapper
from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_CHECK
from tasks.activity.common_activity import CommonActivityBatch
from tasks.base.page import page_common_activity
from tests.support.history_e7wc_battle_gate import HISTORICAL_ACTIVITIES

F, G, K = 'free_gacha_20', 'e7wc_battle_gate', 'koharu_raffle'
MODES = (F, G, K)

# These retired-event captures predate the October title check. Keep using
# their real clock templates without restoring that fallback in production.
HISTORICAL_ACTIVITY_CHECK = ButtonWrapper(
    name='HISTORICAL_COMMON_ACTIVITY_CHECK',
    share=COMMON_ACTIVITY_CHECK.data_buttons['share'],
)


def frame(selected=None, rows=(), **kwargs):
    return dict(selected=selected, rows=[
        SimpleNamespace(ocr_text=HISTORICAL_ACTIVITIES[name][0] if name in MODES else name,
                        box=(25, y, 235, y + 20), score=1)
        for name, y in rows
    ], **kwargs)


class RealScreenshotBatchTests(unittest.TestCase):
    def test_all_pending_templates_choose_only_the_actual_selected_event(self):
        from tests.support.history_fixtures import read_input as load_image
        from unittest.mock import Mock

        server.set_lang('global_cn')
        fixtures = input_root()
        screenshots = {
            F: fixtures / 'activity_navigation/infinity_page_selected.png',
            G: fixtures / 'e7wc_battle_gate/battle_gate_available.png',
            K: fixtures / 'koharu_raffle/available.png',
        }
        for expected, path in screenshots.items():
            with self.subTest(event=expected):
                batch = object.__new__(CommonActivityBatch)
                batch.ACTIVITIES = HISTORICAL_ACTIVITIES
                batch.config = SimpleNamespace(Emulator_GameLanguage='auto')
                batch.interval_timer = {}
                batch.device = SimpleNamespace(image=load_image(str(path)), stuck_record_add=lambda button: None)
                workers = {mode: SimpleNamespace(run_claim=Mock(return_value=False)) for mode in MODES}
                pending = {mode: (keyword, button, workers[mode])
                           for mode, (keyword, button, _) in batch.ACTIVITIES.items()}
                with patch('tasks.activity.common_activity.Ocr') as ocr, patch(
                    'tasks.activity.common_activity.Timer'
                ) as timer, patch.object(page_common_activity, 'check_button', HISTORICAL_ACTIVITY_CHECK):
                    timer.return_value.start.return_value.reached.return_value = True
                    self.assertFalse(batch._run_pending(pending))
                workers[expected].run_claim.assert_called_once_with(navigate=False)
                for mode in MODES:
                    if mode != expected:
                        workers[mode].run_claim.assert_not_called()
                ocr.return_value.detect_and_ocr.assert_not_called()
