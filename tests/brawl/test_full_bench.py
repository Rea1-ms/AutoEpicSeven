import unittest
from itertools import pairwise

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.bench import BenchHero, read_bench  # noqa: E402
from tasks.brawl.cursor import (  # noqa: E402
    agrees_selection,
    confirms_next,
    read_cursor,
)
from tasks.brawl.placement import capture_placement  # noqa: E402
from tasks.brawl.rules import Imprint  # noqa: E402
from tasks.brawl.vision import ObservationError, PreparationPanel  # noqa: E402
from tests.support.brawl import frame  # noqa: E402

FOCUS = (
    ("full-bench-initial", "克莉丝缇", Imprint.B),
    ("full-bench-repeat", "克莉丝缇", Imprint.B),
    ("focus-xiaochun", "小春", Imprint.B),
    ("focus-specimen-sez", "实验体赛兹", Imprint.B),
    ("focus-rin", "利纳柯", Imprint.B),
    ("focus-xiaochun-return", "小春", Imprint.B),
    ("focus-mercedes", "玫勒赛德丝", Imprint.C),
    ("focus-christy-return", "克莉丝缇", Imprint.B),
)


class FullBenchFocusTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_click_focus_keeps_full_deployment_and_resources(self):
        # The initial card is an initial focus, not evidence of a previous click.
        # Source sequence includes repeats and jumps; it is not a ten-card scan.
        for sample, name, imprint in FOCUS:
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                current = read_cursor(self.frame)
                self.assertEqual(current.selected, BenchHero(name, imprint))
                self.assertEqual(current.panel, PreparationPanel(21, 9, 10, 10, 37, 0))
                bench = read_bench(self.frame)
                self.assertFalse(bench.complete)
                self.assertTrue(any(entry.plus_area is not None for entry in bench.entries))

    def test_initial_and_repeated_focus_do_not_prove_another_obtained_hero(self):
        before = frame("full-bench-initial")
        self.frame = frame("full-bench-repeat")
        self.assertEqual(read_cursor(before).selected, read_cursor(self.frame).selected)
        self.assertIs(agrees_selection(before, self.frame), True)
        self.assertIs(confirms_next(before, self.frame), False)

    def test_real_nonadjacent_jumps_cannot_complete_a_forward_scan(self):
        for left, right in pairwise(FOCUS):
            with self.subTest(before=left[0], after=right[0]):
                self.frame = frame(right[0])
                self.assertIs(confirms_next(frame(left[0]), self.frame), False)

    def test_visible_plus_at_ten_of_ten_does_not_authorize_empty_placement(self):
        self.frame = frame("full-swap-before")
        self.assertEqual(read_cursor(self.frame).selected, BenchHero("小春", Imprint.B))
        with self.assertRaisesRegex(ObservationError, "remaining deployment"):
            capture_placement(self.frame, 6, 3)
