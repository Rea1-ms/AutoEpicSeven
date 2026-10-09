# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline regressions for Urgent Tasks prepare hints, including incomplete OCR."""
import unittest
from pathlib import Path
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
from tests.support.history_september_runtime_fixes import timers
from tests.support.history_urgent_tasks_first_clear import Daily
from tasks.dungeon.assets.assets_dungeon_fast_combat import FAST_COMBAT_TIMES_PLUS, FAST_COMBAT_TIMES_MINUS


from tests.support.history_urgent_tasks_hint import Flow

def frame(at, hint=False, count=1, page="prepare", fast=True):
    return dict(at=at, hint=hint, count=count, page=page, fast=fast)


class HintFlowTests(unittest.TestCase):
    def run_flow(self, flow, method, **kwargs):
        timer = timers(lambda: flow.frame['at'])
        with patch('tasks.dungeon.urgent_tasks.Timer', timer), patch('tasks.dungeon.prepare.Timer', timer):
            return getattr(flow, method)(**kwargs)

    def run_fast(self, frames):
        flow = Flow(frames)
        result = self.run_flow(flow, '_prepare_fast_combat', stamina=300,
                               prepare_check=flow._is_urgent_tasks_prepare_ready,
                               additional_handler=flow._handle_urgent_tasks_prepare_additional)
        return flow, result

    def test_navigation_waits_for_hint_to_disappear_including_click_cooldown(self):
        flow = Flow([frame(0, page='detail'), frame(.2, hint=True), frame(.4, hint=True),
                     frame(1.3, hint=True), frame(1.5)])
        self.assertTrue(self.run_flow(flow, '_navigate_urgent_tasks'))
        self.assertEqual(flow.frame['at'], 1.5)
        self.assertEqual(flow.actions, [(0, 'READY_TO_FIGHT', 1),
                                      (.2, 'OCR_URGENT_COMBAT_TIMES_REMAINING', 1),
                                      (1.3, 'OCR_URGENT_COMBAT_TIMES_REMAINING', 1)])
        self.assertEqual(flow.reads, [(0, 'daily')])

    def test_navigation_does_not_extend_deadline_for_persistent_hint(self):
        flow = Flow([frame(0, page='detail')] + [frame(t, hint=True) for t in range(1, 37)])
        self.assertFalse(self.run_flow(flow, '_navigate_urgent_tasks'))
        self.assertEqual(flow.frame['at'], 35)

    def test_stamina_waits_for_hint_to_disappear(self):
        flow = Flow([frame(0, hint=True), frame(.2, hint=True), frame(.4)])
        self.assertEqual(self.run_flow(flow, '_read_urgent_tasks_stamina'), 300)
        self.assertEqual(flow.reads, [(.4, 'stamina')])

    def test_stamina_timeout_does_not_read_through_persistent_hint(self):
        flow = Flow([frame(t, hint=True) for t in range(5)])
        self.assertIsNone(self.run_flow(flow, '_read_urgent_tasks_stamina'))
        self.assertEqual(flow.reads, [])

    def test_pet_check_does_not_exit_during_hint_click_cooldown(self):
        flow = Flow([frame(0, hint=True), frame(.2, hint=True), frame(.4)])
        self.assertFalse(self.run_flow(flow, '_urgent_tasks_prepare_has_pet'))
        self.assertEqual(flow.frame['at'], .4)

    def test_hint_is_closed_before_fast_ocr_and_count_adjustment(self):
        flow, result = self.run_fast([frame(0, hint=True), frame(.2, hint=True),
                                     frame(1.1, hint=True), frame(1.3), frame(2.2), frame(3, count=4)])
        self.assertEqual(result, ('ready', 4))
        self.assertEqual(flow.actions, [(0, 'OCR_URGENT_COMBAT_TIMES_REMAINING', 1),
                                      (1.1, 'OCR_URGENT_COMBAT_TIMES_REMAINING', 1),
                                      (2.2, 'FAST_COMBAT_TIMES_PLUS', 3)])
        self.assertTrue(all(at >= 1.3 for at, _ in flow.reads))

    def test_hint_after_fast_enable_is_closed_before_reading_counts(self):
        flow, result = self.run_fast([frame(0, fast=False), frame(.2, hint=True), frame(.4, count=4)])
        self.assertEqual(result, ('ready', 4))
        self.assertEqual(flow.actions, [(0, 'enable', 1), (.2, 'OCR_URGENT_COMBAT_TIMES_REMAINING', 1)])
        self.assertEqual(flow.reads, [(.4, 'remaining'), (.4, 'count')])

    def test_late_hint_blocks_success_even_if_current_count_matches_target(self):
        flow, result = self.run_fast([frame(0), frame(1), frame(1.2, hint=True, count=4),
                                     frame(1.4, hint=True, count=4), frame(1.6, count=4)])
        self.assertEqual(result, ('ready', 4))
        self.assertEqual(flow.frame['at'], 1.6)
        self.assertEqual(flow.actions, [(1, 'FAST_COMBAT_TIMES_PLUS', 3),
                                      (1.2, 'OCR_URGENT_COMBAT_TIMES_REMAINING', 1)])
        self.assertNotIn((1.4, 'count'), flow.reads)

    def test_hint_before_decreasing_counts_is_closed_first(self):
        flow, result = self.run_fast([frame(0, hint=True, count=7), frame(.2, count=7),
                                     frame(1.1, count=7), frame(2, count=4)])
        self.assertEqual(result, ('ready', 4))
        self.assertEqual(flow.actions, [(0, 'OCR_URGENT_COMBAT_TIMES_REMAINING', 1),
                                      (1.1, 'FAST_COMBAT_TIMES_MINUS', 3)])

    def test_persistent_hint_blocks_all_fast_reads_until_timeout(self):
        flow, result = self.run_fast([frame(t, hint=True) for t in range(20)])
        self.assertEqual(result, ('failed', 0))
        self.assertEqual(flow.frame['at'], 18)
        self.assertEqual(flow.reads, [])

    def test_count_loop_times_out_without_clicking_through_hint(self):
        flow = Flow([frame(t, hint=True, count=4) for t in range(20)])
        result = self.run_flow(flow, '_set_prepare_count', target=4,
                               ocr_getter=flow._ocr_fast_combat_current_times,
                               plus_button=FAST_COMBAT_TIMES_PLUS, minus_button=FAST_COMBAT_TIMES_MINUS,
                               label='FastCombatCurrentTimes',
                               prepare_check=flow._is_urgent_tasks_prepare_ready,
                               additional_handler=flow._handle_urgent_tasks_prepare_additional)
        self.assertFalse(result)
        self.assertEqual(flow.frame['at'], 18)
        self.assertEqual(flow.reads, [])
        self.assertTrue(all('HINT' in name or name == 'OCR_URGENT_COMBAT_TIMES_REMAINING'
                            for _, name, _ in flow.actions))

    def test_daily_run_supplies_hint_checks_to_shared_fast_preparation(self):
        daily = Daily()
        self.assertEqual(daily._run_urgent_tasks_daily(), (True, 5))
        self.assertEqual(daily.fast_calls[0]['prepare_check'], daily._is_urgent_tasks_prepare_ready)
        self.assertEqual(daily.fast_calls[0]['additional_handler'], daily._handle_urgent_tasks_prepare_additional)
