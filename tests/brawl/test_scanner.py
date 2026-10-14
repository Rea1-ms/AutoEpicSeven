import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.bench import BenchHero  # noqa: E402
from tasks.brawl.rules import HEROES, Imprint  # noqa: E402
from tasks.brawl.scanner import BrawlBenchScanner, ScanOutcome  # noqa: E402
from tasks.brawl.vision import BrawlMode, BrawlObservation, BrawlScreen, PreparationPanel  # noqa: E402
from tests.support.brawl import frame  # noqa: E402
from tests.support.offline import ControlledClock, record_action  # noqa: E402


PANEL = PreparationPanel(5, 4, 0, 5, 65, 0)
NEXT = (1070, 331, 1170, 374)


def cursor(name="维多丽卡", imprint=Imprint.C, *, top=False, bottom=False, empty=False,
           next_area=NEXT, panel=PANEL, mode=BrawlMode.NORMAL, state=BrawlScreen.PREPARATION):
    """Explicit synthetic observations, never claimed to be screenshot evidence."""
    selected = BenchHero(name, imprint) if name is not None else None
    reading = SimpleNamespace(selected=selected, top=top, bottom=bottom, empty=empty,
                              next_area=next_area, panel=panel, game_mode=mode)
    observation = BrawlObservation(state, panel, game_mode=mode)
    return SimpleNamespace(cursor=reading, observation=observation)


class ScanReplay:
    """Only screenshot calls advance input; clicks and swipes merely record."""

    def __init__(self, frames, clock, seconds=0.5):
        self.frames = [frame(item) if isinstance(item, str) else item for item in frames]
        self.index = 0
        self.clock = clock
        self.seconds = seconds
        self.image = self._image()
        self.actions = []
        self.task = object.__new__(BrawlBenchScanner)
        self.task.config = SimpleNamespace()
        self.task.device = self
        self.task.interval_timer = {}

    def _image(self):
        current = self.frames[self.index]
        if isinstance(current, np.ndarray):
            return current
        # The first pixel distinguishes synthetic frames for pending-image
        # checks; their UI observations are provided explicitly by each test.
        image = np.zeros((720, 1280, 3), np.uint8)
        image[0, 0, 0] = self.index
        return image

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError("Replay exhausted before positive completion or bounded exit")
        self.clock.advance(self.seconds)
        self.image = self._image()

    def click(self, button):
        action = (self.index, str(button), tuple(button.button))
        self.actions.append(action)
        record_action(action)

    def swipe(self, start, end, name):
        action = (self.index, name, start, end)
        self.actions.append(action)
        record_action(action)


class BenchScannerTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames, *, synthetic=True, seconds=0.5, confirmed=True, agreement=True):
        replay = ScanReplay(frames, self.clock, seconds)
        self.frame = replay.image
        if synthetic:
            patches = (
                patch("tasks.brawl.scanner.BrawlVision", side_effect=lambda image: SimpleNamespace(
                    observe=lambda: replay.frames[replay.index].observation)),
                patch("tasks.brawl.scanner.read_cursor", side_effect=lambda image, **kw:
                      replay.frames[replay.index].cursor),
                patch("tasks.brawl.scanner.confirms_next", return_value=confirmed),
                patch("tasks.brawl.scanner.agrees_selection", return_value=agreement),
            )
            for item in patches:
                item.start()
                self.addCleanup(item.stop)
        return replay

    def test_captured_reverse_order_simulates_a_complete_forward_scan(self):
        # The user recorded bottom-to-top card selections. Reversing those
        # stable captures simulates forward responses; it is not a recording
        # of our actual clicks or their timing. Duplicates explicitly model
        # delayed responses while retaining the real production intervals.
        names = ("queue-top", "queue-second", "queue-middle", "queue-karin", "queue-glen")
        replay = self.replay([name for name in names for _ in range(7)] + ["queue-bottom"],
                             synthetic=False)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.COMPLETE)
        self.assertTrue(result.coverage_complete)
        self.assertTrue(result.roster_complete)
        self.assertEqual(result.free_slots, 4)
        self.assertEqual([(hero.name, hero.imprint) for hero in result.heroes], [
            ("维多丽卡", Imprint.C), ("克莉丝缇", Imprint.D), ("战斗型玛雅", Imprint.C),
            ("卡琳", Imprint.A), ("格伦", Imprint.C), ("辅助型拉兹", Imprint.C),
        ])
        self.assertEqual([action[0] for action in replay.actions], [0, 7, 14, 21, 28])
        self.assertTrue(all(action[1] == "BrawlBenchNext" for action in replay.actions))
        self.assertTrue(all(1000 <= action[2][0] < 1280 and 327 <= action[2][1] < 550
                            for action in replay.actions))

    def test_captured_empty_bench_needs_no_swipe_or_click(self):
        replay = self.replay(["queue-empty"], synthetic=False)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.COMPLETE)
        self.assertEqual(result.heroes, ())
        self.assertTrue(result.coverage_complete)
        self.assertEqual(result.free_slots, 10)
        # The bench is empty, but the captured panel still has 3/4 deployed
        # heroes. Their identities are not evidence supplied by this scan.
        self.assertEqual(result.panel.deployed, 3)
        self.assertFalse(result.roster_complete)
        self.assertEqual(replay.actions, [])

    def test_return_to_top_retries_only_with_interval_then_walks_forward(self):
        middle = cursor("卡琳")
        first = cursor(top=True)
        last = cursor("格伦", bottom=True, next_area=None)
        replay = self.replay([middle] * 8 + [first] * 7 + [last])
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.COMPLETE)
        self.assertEqual([action[:2] for action in replay.actions], [
            (0, "BrawlBenchTop"), (7, "BrawlBenchTop"), (14, "BrawlBenchNext"),
        ])
        self.assertEqual(replay.actions[0][2:], ((1150, 205), (1150, 520)))
        self.assertEqual([hero.name for hero in result.heroes], ["维多丽卡", "格伦"])

    def test_repeated_middle_frames_never_prove_top_or_complete(self):
        replay = self.replay([cursor("卡琳")] * 30)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.ACTION_LIMIT)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.heroes, ())
        self.assertEqual([action[0] for action in replay.actions], [0, 7, 14])

    def test_unconfirmed_next_click_is_not_repeated_or_counted(self):
        replay = self.replay([cursor(top=True)] * 30)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.TIMEOUT)
        self.assertFalse(result.roster_complete)
        self.assertEqual([hero.name for hero in result.heroes], ["维多丽卡"])
        self.assertEqual(len(replay.actions), 1)
        self.assertEqual(replay.actions[0][1], "BrawlBenchNext")

    def test_unread_imprint_waits_without_skipping_the_card(self):
        replay = self.replay([cursor(imprint=None, top=True)] * 30)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.TIMEOUT)
        self.assertEqual(result.heroes, ())
        self.assertEqual(replay.actions, [])

    def test_unknown_frames_do_not_click_or_erase_pending_identity(self):
        unknown = cursor(state=BrawlScreen.UNKNOWN, panel=None, mode=None)
        first = cursor(top=True)
        last = cursor("格伦", bottom=True, next_area=None)
        replay = self.replay([unknown, first, unknown, unknown, last])
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.COMPLETE)
        self.assertEqual([action[:2] for action in replay.actions], [(1, "BrawlBenchNext")])
        self.assertEqual(len(result.heroes), 2)

    def test_wrong_selected_portrait_cannot_finish_at_a_visible_bottom(self):
        replay = self.replay([cursor(top=True), cursor("格伦", bottom=True, next_area=None)],
                             confirmed=False)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.TRANSITION_MISMATCH)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(len(result.heroes), 1)
        self.assertEqual(len(replay.actions), 1)

    def test_unknown_portrait_confirmation_waits_and_times_out_incomplete(self):
        replay = self.replay([cursor(top=True)] + [cursor("格伦", bottom=True, next_area=None)] * 30,
                             confirmed=None)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.TIMEOUT)
        self.assertEqual(len(result.heroes), 1)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(len(replay.actions), 1)

    def test_preparation_panel_or_mode_change_invalidates_the_scan(self):
        for changed in (cursor("格伦", panel=replace(PANEL, coins=66)),
                        cursor("格伦", mode=BrawlMode.CHALLENGE)):
            with self.subTest(changed=changed):
                replay = self.replay([cursor(top=True), changed])
                result = replay.task.scan()
                self.assertEqual(result.outcome, ScanOutcome.CONTEXT_CHANGED)
                self.assertFalse(result.coverage_complete)
                self.assertEqual(len(replay.actions), 1)

    def test_overlay_or_combat_interrupts_before_any_further_action(self):
        for state in (BrawlScreen.TRANSMIT_SELECTION, BrawlScreen.FORMATION_SELECTION,
                      BrawlScreen.COMBAT, BrawlScreen.RUN_DEFEAT):
            with self.subTest(state=state):
                replay = self.replay([cursor(top=True), cursor(state=state)])
                result = replay.task.scan()
                self.assertEqual(result.outcome, ScanOutcome.SCENE_CHANGED)
                self.assertEqual(len(replay.actions), 1)

    def test_complete_bench_with_deployed_heroes_is_not_a_complete_roster(self):
        replay = self.replay([cursor(top=True, bottom=True, next_area=None,
                                     panel=replace(PANEL, deployed=1))])
        result = replay.task.scan()
        self.assertTrue(result.coverage_complete)
        self.assertFalse(result.roster_complete)
        self.assertEqual(replay.actions, [])

    def test_positive_last_frame_exits_before_timeout_or_action_limit(self):
        replay = self.replay([cursor(top=True), cursor("格伦", bottom=True, next_area=None)], seconds=13)
        replay.task.MAX_ACTIONS = 1
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.COMPLETE)
        self.assertEqual(len(replay.actions), 1)

    def test_action_budget_is_not_treated_as_inventory_capacity(self):
        replay = self.replay([cursor(top=True), cursor("格伦")])
        replay.task.MAX_ACTIONS = 1
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.ACTION_LIMIT)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(len(result.heroes), 2)

    def test_ten_verified_selections_still_require_a_positive_bottom(self):
        names = tuple(HEROES)[:10]
        first_nine = [cursor(name, Imprint.NONE, top=index == 0)
                      for index, name in enumerate(names[:9]) for _ in range(7)]
        for bottom, expected in ((True, ScanOutcome.COMPLETE), (None, ScanOutcome.TIMEOUT)):
            with self.subTest(bottom=bottom):
                last = cursor(names[-1], Imprint.NONE, bottom=bottom, next_area=None)
                replay = self.replay(first_nine + [last] * 30)
                result = replay.task.scan()
                self.assertEqual(result.outcome, expected)
                self.assertEqual(tuple(hero.name for hero in result.heroes), names)
                self.assertEqual(len(replay.actions), 9)
                self.assertEqual(result.coverage_complete, bottom is True)
                self.assertEqual(result.free_slots, 0 if bottom else None)

    def test_tenth_selection_with_another_card_stops_without_an_eleventh_click(self):
        names = tuple(HEROES)[:10]
        frames = [cursor(name, Imprint.NONE, top=index == 0)
                  for index, name in enumerate(names) for _ in range(7)]
        replay = self.replay(frames)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.CAPACITY_CONFLICT)
        self.assertEqual(len(result.heroes), 10)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(len(replay.actions), 9)
        self.assertIsNone(result.free_slots)

    def test_action_budget_still_waits_for_the_last_pending_click(self):
        replay = self.replay([cursor(top=True)] * 8
                             + [cursor("格伦", bottom=True, next_area=None)])
        replay.task.MAX_ACTIONS = 1
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.COMPLETE)
        self.assertEqual(len(result.heroes), 2)
        self.assertEqual(len(replay.actions), 1)

    def test_duplicate_selected_identity_rejects_a_cycle(self):
        first = cursor(top=True)
        middle = cursor("格伦")
        replay = self.replay([first] * 7 + [middle] * 7 + [first])
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.TRANSITION_MISMATCH)
        self.assertEqual(len(result.heroes), 2)
        self.assertEqual(len(replay.actions), 2)

    def test_changed_imprint_invalidates_the_existing_inventory(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                first = cursor(top=True, next_area=NEXT if pending else None)
                replay = self.replay([first, cursor(imprint=Imprint.B, top=True)])
                result = replay.task.scan()
                self.assertEqual(result.outcome, ScanOutcome.CONTEXT_CHANGED)
                self.assertFalse(result.coverage_complete)
                self.assertEqual(result.heroes, (BenchHero("维多丽卡", Imprint.C),))
                self.assertEqual(len(replay.actions), int(pending))

    def test_changed_imprint_on_an_earlier_hero_is_not_a_duplicate_cycle(self):
        first = cursor(top=True)
        middle = cursor("格伦")
        changed = cursor(imprint=Imprint.B, top=True)
        replay = self.replay([first] * 7 + [middle] * 7 + [changed])
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.CONTEXT_CHANGED)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(len(result.heroes), 2)
        self.assertEqual(len(replay.actions), 2)

    def test_visible_queue_change_during_the_interval_prevents_the_next_click(self):
        # The first click completes before the 3-second interval. While that
        # interval is pending, the same selected hero has changed neighbours.
        replay = self.replay([cursor(top=True), cursor("格伦"), cursor("格伦")],
                             agreement=False)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.CONTEXT_CHANGED)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(len(result.heroes), 2)
        self.assertEqual(len(replay.actions), 1)

    def test_occluded_queue_during_the_interval_does_not_authorize_a_click(self):
        replay = self.replay([cursor(top=True)] + [cursor("格伦")] * 30, agreement=None)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.TIMEOUT)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(len(result.heroes), 2)
        self.assertEqual(len(replay.actions), 1)

    def test_malformed_next_target_never_clicks_the_formation_plus(self):
        replay = self.replay([cursor(top=True, next_area=(943, 268, 977, 303))] * 30)
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.TIMEOUT)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(replay.actions, [])

    def test_first_screenshot_can_replace_a_stale_page_without_acting(self):
        replay = self.replay([cursor(state=BrawlScreen.COMBAT),
                             cursor(top=True, bottom=True, next_area=None)])
        result = replay.task.scan(skip_first_screenshot=False)
        self.assertEqual(result.outcome, ScanOutcome.COMPLETE)
        self.assertEqual(replay.index, 1)
        self.assertEqual(replay.actions, [])
