# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline state-machine cases: visible work first, one shared sidebar sweep."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

import module.config.server as server
from tasks.activity.common_activity import CommonActivityBatch
from tests.support.history_cn_september_update import Claim as FreeClaim
from tests.support.history_e7wc_battle_gate import Claim as GateClaim
from tests.support.history_e7wc_battle_gate import HISTORICAL_ACTIVITIES
from tests.support.history_koharu_raffle import Claim as KoharuClaim

F, G, K = 'free_gacha_20', 'e7wc_battle_gate', 'koharu_raffle'
MODES = (F, G, K)


def frame(selected=None, rows=(), **kwargs):
    return dict(selected=selected, rows=[
        SimpleNamespace(ocr_text=HISTORICAL_ACTIVITIES[name][0] if name in MODES else name,
                        box=(25, y, 235, y + 20), score=1)
        for name, y in rows
    ], **kwargs)


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


class BatchStateTests(unittest.TestCase):
    def test_current_selected_then_visible_screen_order_without_scroll(self):
        rows = ((F, 500), (G, 200))
        batch = Batch([
            frame(K, rows), frame(K, rows), frame(K, rows), frame(G, rows),
            frame(G, rows), frame(G, rows), frame(F, rows),
        ])
        self.assertTrue(batch.check())
        self.assertEqual(batch.claims, [K, G, F])
        self.assertEqual([text for text, _ in batch.clicks], ['激战门', 'INFINITY'])
        self.assertEqual(batch.swipes, [])
        self.assertEqual(batch.ocr.call_args.kwargs['lang'], 'cn')

    def test_visible_order_does_not_follow_calendar_order(self):
        rows = ((F, 500), (K, 100))
        batch = Batch([frame(rows=rows), frame(rows=rows), frame(K, rows),
                       frame(K, rows), frame(K, rows), frame(F, rows)])
        self.assertTrue(batch.check((F, K)))
        self.assertEqual(batch.claims, [K, F])
        self.assertEqual(batch.swipes, [])

    def test_selected_event_needs_no_sidebar_ocr_or_click(self):
        batch = Batch([frame(F)])
        self.assertTrue(batch.check((F,)))
        batch.ocr.return_value.detect_and_ocr.assert_not_called()
        self.assertEqual(batch.clicks + batch.swipes, [])

    def test_moving_target_waits_for_stable_text_coordinates(self):
        batch = Batch([frame(rows=((F, 300),)), frame(rows=((F, 250),)),
                       frame(rows=((F, 200),)), frame(rows=((F, 200),)), frame(F)])
        self.assertTrue(batch.check((F,)))
        self.assertEqual(batch.clicks, [('INFINITY', 3)])
        self.assertEqual(batch.swipes, [])

    def test_dropped_click_retries_same_target_even_when_other_row_is_higher(self):
        batch = Batch([
            frame(rows=((F, 100),)), frame(rows=((F, 100),)),
            frame(rows=((G, 50), (F, 100))), frame(rows=((G, 50), (F, 100))),
            frame(F), frame(rows=((G, 100),)), frame(rows=((G, 100),)), frame(G),
        ])
        self.assertTrue(batch.check((F, G)))
        self.assertEqual([text for text, _ in batch.clicks], ['INFINITY', 'INFINITY', '激战门'])
        self.assertEqual(batch.claims, [F, G])
        self.assertEqual(batch.swipes, [])

    def test_wrong_selected_pending_event_does_not_override_clicked_target(self):
        rows = ((F, 200), (G, 400))
        batch = Batch([frame(rows=rows), frame(rows=rows), frame(G, rows), frame(G, rows),
                       frame(F), frame(G)])
        self.assertTrue(batch.check((F, G)))
        self.assertEqual(batch.claims, [F, G])
        self.assertEqual([text for text, _ in batch.clicks], ['INFINITY', 'INFINITY'])

    def test_click_without_selected_marker_never_claims(self):
        batch = Batch([frame(rows=((F, 200),)), frame(rows=((F, 200),)),
                       frame(rows=((F, 200),), timeout=True)])
        self.assertFalse(batch.check((F,)))
        self.assertEqual(batch.claims, [])
        self.assertEqual(len(batch.clicks), 1)
        self.assertEqual(batch.swipes, [])

    def test_duplicate_keyword_does_not_pick_an_arbitrary_row(self):
        batch = Batch([frame(rows=((F, 100), (F, 200))),
                       frame(rows=((F, 100), (F, 200)), timeout=True)])
        self.assertFalse(batch.check((F,)))
        self.assertEqual(batch.claims + batch.clicks + batch.swipes, [])

    def test_multiple_selected_templates_are_not_accepted(self):
        batch = Batch([frame((F, G), timeout=True)])
        self.assertFalse(batch.check((F, G)))
        self.assertEqual(batch.claims + batch.clicks + batch.swipes, [])

    def test_network_transition_invalidates_previous_click_coordinates(self):
        batch = Batch([frame(rows=((F, 200),)), frame(ready=False, network=True),
                       frame(rows=((F, 200),)), frame(rows=((F, 200),)), frame(F)])
        self.assertTrue(batch.check((F,)))
        self.assertEqual(batch.clicks, [('INFINITY', 3)])

    def test_one_reverse_is_preserved_after_an_intermediate_claim(self):
        other = (('other1', 200), ('other2', 400))
        batch = Batch([
            frame(K), frame(rows=other), frame(rows=other),
            frame(rows=other), frame(rows=other),
            frame(rows=((G, 200),)), frame(rows=((G, 200),)), frame(G),
            frame(rows=other), frame(rows=other),
            frame(rows=((F, 200),)), frame(rows=((F, 200),)), frame(F),
        ])
        self.assertTrue(batch.check())
        self.assertEqual(batch.claims, [K, G, F])
        self.assertEqual([start[1] > end[1] for start, end in batch.swipes], [True, False, False])

    def test_blank_ocr_is_not_mistaken_for_a_scroll_boundary(self):
        batch = Batch([frame()] * 10)
        batch.ACTIVITY_SCROLL_DOWN_LIMIT = batch.ACTIVITY_SCROLL_UP_LIMIT = 2
        self.assertFalse(batch.check((F,)))
        self.assertEqual([start[1] > end[1] for start, end in batch.swipes], [True, True, False, False])
        self.assertEqual(batch.claims, [])

    def test_unchanged_visible_rows_bound_both_sweep_directions(self):
        other = (('other1', 200), ('other2', 400))
        batch = Batch([frame(rows=other)] * 6)
        self.assertFalse(batch.check((F,)))
        self.assertEqual([start[1] > end[1] for start, end in batch.swipes], [True, False])

    def test_failed_claim_stops_without_touching_other_events(self):
        batch = Batch([frame(K, ((F, 200), (G, 400)))])
        self.assertFalse(batch.check(failed=K))
        self.assertEqual(batch.claims, [K])
        self.assertEqual(batch.clicks + batch.swipes, [])

    def test_empty_queue_koharu_is_checked_only_once_in_the_batch(self):
        batch = Batch([frame(K), frame(K, ((F, 200),)), frame(K, ((F, 200),)), frame(F)])
        self.assertTrue(batch.check((F, K)))
        self.assertEqual(batch.claims, [K, F])


class ReusedSelectedTabTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_wrong_selected_tab_is_rejected_before_claim_actions(self):
        for factory, frames in ((FreeClaim, ['claim']), (GateClaim, ['available']), (KoharuClaim, ['pending'])):
            with self.subTest(factory=factory):
                claim = factory(frames)
                with patch.object(claim, '_activity_selected', return_value=False), patch.object(
                    claim, 'ui_goto'
                ) as route, patch.object(claim, 'select_activity') as select:
                    self.assertFalse(claim.run_claim(navigate=False))
                    route.assert_not_called()
                    select.assert_not_called()
                    self.assertEqual(claim.config.values, {})

    def test_existing_claim_flows_and_records_work_without_sidebar_navigation(self):
        cases = (
            (FreeClaim, ['claim', 'popup', 'obtained'], True),
            (GateClaim, ['available', 'popup', 'received'], True),
            (KoharuClaim, ['pending', 'pending', 'popup', 'none'], False),
        )
        for factory, frames, marked in cases:
            with self.subTest(factory=factory):
                claim = factory(frames)
                with patch.object(claim, '_activity_selected', return_value=True), patch.object(
                    claim, 'ui_goto'
                ) as route, patch.object(claim, 'select_activity') as select:
                    self.assertTrue(claim.run_claim(navigate=False))
                    route.assert_not_called()
                    select.assert_not_called()
                self.assertEqual(bool(claim.config.values), marked)

    def test_requested_fresh_screenshot_precedes_selected_guard(self):
        for factory, frames in ((FreeClaim, ['other', 'obtained']), (GateClaim, ['other', 'received']),
                                (KoharuClaim, ['other', 'none', 'none'])):
            with self.subTest(factory=factory):
                claim = factory(frames)
                observed = []
                def selected(button):
                    observed.append(claim.index if hasattr(claim, 'index') else claim.frame)
                    return True
                with patch.object(claim, '_activity_selected', side_effect=selected):
                    self.assertTrue(claim.run_claim(skip_first_screenshot=False, navigate=False))
                self.assertEqual(observed, [1])
