import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.bench import BenchHero  # noqa: E402
from tasks.brawl.cursor import Cursor, agrees_selection, confirms_next, read_cursor  # noqa: E402
from tasks.brawl.rules import Imprint  # noqa: E402
from tasks.brawl.vision import BrawlMode, BrawlVision, PreparationPanel  # noqa: E402
from tests.support.brawl import frame  # noqa: E402


# These captures were recorded bottom to top. This order describes the visible
# queue, not a claim that the source sequence recorded our forward clicks.
QUEUE = ("queue-top", "queue-second", "queue-middle", "queue-karin", "queue-glen", "queue-bottom")
HEROES = (("维多丽卡", Imprint.C), ("克莉丝缇", Imprint.D), ("战斗型玛雅", Imprint.C),
          ("卡琳", Imprint.A), ("格伦", Imprint.C), ("辅助型拉兹", Imprint.C))


class BenchCursorTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def read(self, name):
        self.frame = frame(name)
        return read_cursor(self.frame)

    def test_live_opening_has_positive_top_and_current_adjacent_card(self):
        observed = self.read("opening-lorina-carmainerose")
        self.assertEqual(observed.selected, BenchHero("萝里娜", Imprint.D))
        self.assertEqual((observed.top, observed.bottom, observed.empty), (True, False, False))
        self.assertEqual(observed.next_area, (1030, 335, 1265, 375))
        self.assertEqual(observed.panel, PreparationPanel(1, 2, 0, 3, 50, 0))
        self.assertEqual(observed.reason, "")

    def test_captured_selected_cards_have_positive_endpoints_and_adjacent_targets(self):
        for index, (name, hero) in enumerate(zip(QUEUE, HEROES)):
            with self.subTest(sample=name):
                observed = self.read(name)
                self.assertEqual(observed.selected, BenchHero(*hero))
                self.assertEqual(observed.panel, PreparationPanel(5, 4, 0, 5, 65, 0))
                self.assertEqual(observed.game_mode, BrawlMode.NORMAL)
                self.assertIs(observed.top, True if index == 0 else None)
                self.assertIs(observed.bottom, index == len(QUEUE) - 1)
                self.assertIs(observed.empty, False)
                if index == len(QUEUE) - 1:
                    self.assertIsNone(observed.next_area)
                else:
                    self.assertEqual(observed.next_area, (1030, 335, 1265, 375))
                    # The card target is below the selected hero's plus control.
                    self.assertGreater(observed.next_area[1], 303)

    def test_captured_empty_bench_does_not_describe_deployed_heroes(self):
        observed = self.read("queue-empty")
        self.assertIsNone(observed.selected)
        self.assertEqual((observed.top, observed.bottom, observed.empty), (True, True, True))
        self.assertIsNone(observed.next_area)
        self.assertEqual((observed.panel.deployed, observed.panel.capacity), (3, 4))
        self.assertEqual(observed.game_mode, BrawlMode.NORMAL)

    def test_non_preparation_frames_never_inherit_a_previous_cursor(self):
        self.assertIsNotNone(self.read("queue-top").selected)
        for name in ("lobby", "formation-place", "combat", "transmit"):
            with self.subTest(sample=name):
                self.assertEqual(self.read(name), Cursor())
        self.assertEqual(read_cursor(np.zeros((720, 1280, 3), np.uint8)), Cursor())

    def test_erased_endpoint_backgrounds_and_unread_selected_names_stay_unknown(self):
        for name, area, field in (
                ("queue-top", (1030, 186, 1150, 231), "top"),
                ("queue-bottom", (1040, 337, 1150, 380), "bottom")):
            with self.subTest(sample=name, field=field):
                self.frame = frame(name).copy()
                x1, y1, x2, y2 = area
                self.frame[y1:y2, x1:x2] = 0
                observed = read_cursor(self.frame)
                self.assertIsNotNone(observed.selected)
                self.assertIsNone(getattr(observed, field))
                self.assertIs(observed.empty, False)
                self.assertIn("endpoint", observed.reason)
        self.frame = frame("queue-top").copy()
        self.frame[247:284, 996:1158] = 0
        observed = read_cursor(self.frame)
        self.assertIsNotNone(observed.panel)
        self.assertIsNone(observed.selected)
        self.assertEqual((observed.top, observed.bottom, observed.empty), (None, None, None))
        self.assertIn("Selected card is unread", observed.reason)
        self.frame = frame("queue-empty").copy()
        self.frame[310:372, 1080:1210] = 0
        observed = read_cursor(self.frame)
        self.assertIsNone(observed.empty)
        self.assertIsNone(observed.selected)

    def test_captured_adjacent_portraits_confirm_each_forward_selection(self):
        images = [frame(name) for name in QUEUE]
        for index, (before, after) in enumerate(zip(images, images[1:])):
            with self.subTest(before=QUEUE[index], after=QUEUE[index + 1]):
                self.frame = after
                self.assertIs(confirms_next(before, after), True)

    def test_supplied_cursors_reuse_recognition_but_still_check_current_pixels(self):
        before, after = frame("queue-top"), frame("queue-second")
        prior, current = read_cursor(before), read_cursor(after)
        changed = before.copy()
        changed[335:374, 1202:1268] = frame("queue-karin")[335:374, 1202:1268]
        self.frame = changed
        with patch("tasks.brawl.cursor.read_cursor", side_effect=AssertionError("Repeated cursor OCR")):
            self.assertIs(confirms_next(before, after, before_cursor=prior, after_cursor=current), True)
            self.assertIs(agrees_selection(before, before.copy(),
                                          before_cursor=prior, after_cursor=prior), True)
            # The selected name and endpoints still agree. Reusing those reads
            # must not bypass the independent, changed-neighbour pixel check.
            self.assertIs(agrees_selection(before, changed,
                                          before_cursor=prior, after_cursor=prior), False)
            wrong_context = replace(current, panel=replace(current.panel, coins=current.panel.coins + 1))
            self.assertIsNone(confirms_next(before, after,
                                           before_cursor=prior, after_cursor=wrong_context))

    def test_comparison_without_supplied_cursors_still_reads_each_image(self):
        before, after = frame("queue-top"), frame("queue-second")
        self.frame = after
        with patch("tasks.brawl.cursor.read_cursor", wraps=read_cursor) as reader:
            self.assertIs(confirms_next(before, after), True)
            self.assertEqual(reader.call_count, 2)
            self.assertIs(reader.call_args_list[0].args[0], before)
            self.assertIs(reader.call_args_list[1].args[0], after)
            reader.reset_mock()
            self.assertIs(agrees_selection(before, before), True)
            self.assertEqual(reader.call_count, 2)

    def test_supplied_cursors_preserve_image_and_server_validation(self):
        before, after = frame("queue-top"), frame("queue-second")
        prior, current = read_cursor(before), read_cursor(after)
        self.frame = after
        for comparison in (confirms_next, agrees_selection):
            for invalid in (None, np.zeros((360, 640, 3), np.uint8),
                            np.zeros((720, 1280, 3), np.float32)):
                with self.subTest(comparison=comparison.__name__, shape=getattr(invalid, "shape", None)):
                    with self.assertRaises(ValueError):
                        comparison(invalid, after, before_cursor=prior, after_cursor=current)
                    with self.assertRaises(ValueError):
                        comparison(before, invalid, before_cursor=prior, after_cursor=current)
        try:
            server.set_lang("cn")
            for comparison in (confirms_next, agrees_selection):
                with self.subTest(comparison=comparison.__name__), self.assertRaises(ValueError):
                    comparison(before, after, before_cursor=prior, after_cursor=current)
        finally:
            server.set_lang("global_cn")

    def test_changed_name_alone_cannot_confirm_a_skipped_or_wrong_card(self):
        for before_name, after_name in (("queue-top", "queue-top"),
                                         ("queue-top", "queue-middle"),
                                         ("queue-karin", "queue-bottom"),
                                         ("queue-glen", "queue-karin")):
            with self.subTest(before=before_name, after=after_name):
                before = frame(before_name)
                self.frame = frame(after_name)
                self.assertIs(confirms_next(before, self.frame), False)

    def test_missing_portraits_or_selection_context_cannot_confirm_a_transition(self):
        before, after = frame("queue-karin"), frame("queue-glen")
        for erase_before in (True, False):
            with self.subTest(erase_before=erase_before):
                a, b = before.copy(), after.copy()
                if erase_before:
                    a[335:374, 1202:1268] = 0
                else:
                    b[270:321, 1187:1275] = 0
                self.frame = b
                self.assertIsNone(confirms_next(a, b))
        self.frame = frame("queue-bottom")
        self.assertIsNone(confirms_next(self.frame, after))
        self.assertIsNone(confirms_next(before, frame("combat")))
        prior, current = read_cursor(before), read_cursor(after)
        for changed in (replace(current, panel=replace(current.panel, coins=current.panel.coins + 1)),
                        replace(current, game_mode=BrawlMode.CHALLENGE),
                        replace(current, selected=BenchHero("格伦", None))):
            # Synthetic context changes isolate the guard from the real
            # portrait pair. They are not additional captured observations.
            with self.subTest(changed=changed), patch(
                    "tasks.brawl.cursor.read_cursor", side_effect=(prior, changed)):
                self.assertIsNone(confirms_next(before, after))

    def test_identical_captured_visible_queues_agree_without_claiming_hidden_inventory(self):
        for name in QUEUE:
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertIs(agrees_selection(self.frame, self.frame.copy()), True)

    def test_same_selected_hero_with_changed_visible_neighbor_does_not_agree(self):
        original = frame("queue-top")
        self.frame = original.copy()
        # Replace only the next small portrait. Keep the selected name,
        # imprint, plus, next-card border and both endpoint regions intact.
        self.frame[335:374, 1202:1268] = frame("queue-karin")[335:374, 1202:1268]
        self.assertEqual(read_cursor(original).selected, read_cursor(self.frame).selected)
        self.assertIs(agrees_selection(original, self.frame), False)
        self.assertIs(agrees_selection(original, frame("queue-middle")), False)

    def test_obscured_visible_queue_does_not_authorize_reusing_a_click(self):
        original = frame("queue-top")
        self.frame = original.copy()
        self.frame[390:440, 1175:1275] = 0
        self.assertIsNotNone(read_cursor(self.frame).selected)
        self.assertIsNone(agrees_selection(original, self.frame))
        self.assertIsNone(agrees_selection(original, frame("combat")))
        empty = frame("queue-empty")
        self.assertIsNone(agrees_selection(empty, empty))

    def test_supplied_observation_still_validates_image_and_model_errors_propagate(self):
        self.frame = frame("queue-top")
        observed = BrawlVision(self.frame).observe()
        with patch("tasks.brawl.cursor.BrawlVision.observe", side_effect=AssertionError("repeated scene read")):
            self.assertEqual(read_cursor(self.frame, observation=observed).selected,
                             BenchHero("维多丽卡", Imprint.C))
        with patch("tasks.brawl.cursor.read_bench", side_effect=RuntimeError("model unavailable")), \
                self.assertRaisesRegex(RuntimeError, "model unavailable"):
            read_cursor(self.frame, observation=observed)
        for invalid in (None, np.zeros((360, 640, 3), np.uint8),
                        np.zeros((720, 1280, 3), np.float32)):
            with self.subTest(shape=getattr(invalid, "shape", None)), self.assertRaises(ValueError):
                read_cursor(invalid, observation=observed)
        try:
            server.set_lang("cn")
            with self.assertRaises(ValueError):
                read_cursor(self.frame, observation=observed)
        finally:
            server.set_lang("global_cn")
