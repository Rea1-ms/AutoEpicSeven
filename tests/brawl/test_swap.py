import unittest
from dataclasses import replace
from unittest.mock import patch

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.assets.assets_brawl import (  # noqa: E402
    FORMATION_GRID_CHECK,
    FORMATION_SOURCE_SELECTED,
)
from tasks.brawl.assets.assets_brawl_actions import SWAP_GRID_ANCHOR  # noqa: E402
from tasks.brawl.bench import BenchHero, read_bench  # noqa: E402
from tasks.brawl.board import read_board, read_formation_grid, read_swap_targets  # noqa: E402
from tasks.brawl.brawl import analyze  # noqa: E402
from tasks.brawl.cursor import read_cursor  # noqa: E402
from tasks.brawl.field import read_field  # noqa: E402
from tasks.brawl.rules import Imprint  # noqa: E402
from tasks.brawl.scanner import BenchScanResult, ScanOutcome  # noqa: E402
from tasks.brawl.swap import (  # noqa: E402
    BrawlSwap,
    SwapOutcome,
    capture_identification_swap,
    capture_swap,
    identify_swapped_hero,
)
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)
from tasks.brawl.withdraw import (  # noqa: E402
    BrawlWithdraw,
    WithdrawOutcome,
    capture_withdraw,
    read_withdraw_control,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402


class WithdrawTests(unittest.TestCase):
    """Real three-frame withdrawal; the complete pre-scan is explicit simulation."""

    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.before = frame("withdraw-before")
        self.grid = frame("withdraw-grid")
        self.after = frame("withdraw-after")
        observation = BrawlVision(self.before).observe()
        # The screenshots show the three cards but do not record an automatic
        # top-to-bottom scan; this input must not be called a real scanner run.
        self.scan = BenchScanResult((BenchHero("利纳柯", Imprint.B),
                                     BenchHero("最后的骑士克劳乌", Imprint.A),
                                     BenchHero("维多丽卡", Imprint.C)),
                                    observation.panel, observation.game_mode, ScanOutcome.COMPLETE)
        self.request = capture_withdraw(self.before, 6, 3, self.scan)

    def replay(self, images, seconds=4):
        replay = BrawlReplay(BrawlWithdraw, images, self.clock, seconds=seconds)
        self.frame = replay.image
        return replay

    def test_red_minus_pairs_with_green_source_on_new_and_independent_old_grids(self):
        for sample, source, area in ((self.grid, (6, 3), (750, 545, 767, 562)),
                                     (frame("move-source"), (3, 3), (688, 377, 705, 394))):
            self.frame = sample
            control = read_withdraw_control(sample)
            self.assertIsNotNone(control)
            self.assertEqual((control.source.screen_row, control.source.screen_column), source)
            self.assertEqual(control.button_area, area)
        for sample in (self.before, self.after, frame("full-swap-grid"), frame("transmit-selected")):
            self.frame = sample
            self.assertIsNone(read_withdraw_control(sample))

    def test_missing_duplicate_or_unpaired_controls_and_dim_button_stay_unknown(self):
        for area in ((740, 536, 778, 573), (802, 529, 851, 579)):
            self.frame = self.grid.copy()
            x1, y1, x2, y2 = area
            self.frame[y1:y2, x1:x2] = 0
            self.assertIsNone(read_withdraw_control(self.frame))
        # A missing fixed edge is not itself missing geometry: the other edge
        # or a complete full-field control set can locate this same screenshot.
        # Remove both fixed anchors and one blue control to withhold every
        # valid localization proof while leaving the source/minus pair intact.
        self.frame = self.grid.copy()
        for asset in (FORMATION_GRID_CHECK, SWAP_GRID_ANCHOR):
            for marker in asset.match_multi_template(self.grid, similarity=.9):
                x1, y1, x2, y2 = marker.area
                self.frame[y1:y2, x1:x2] = 0
        marker = FORMATION_SOURCE_SELECTED.match_multi_template(self.grid, similarity=.9)[0]
        x1, y1, x2, y2 = marker.area
        self.frame[y1:y2, x1:x2] = 0
        self.assertIsNone(read_formation_grid(self.frame))
        self.assertIsNone(read_withdraw_control(self.frame))
        for variant in ("duplicate", "moved", "dark"):
            self.frame = self.grid.copy()
            if variant != "dark":
                self.frame[536:573, 580:618] = self.grid[536:573, 740:778]
            if variant == "moved":
                self.frame[536:573, 740:778] = 0
            if variant == "dark":
                self.frame[536:573, 740:778] //= 2
            self.assertIsNone(read_withdraw_control(self.frame))

    def test_reported_round13_withdraw_uses_complete_grid_when_fixed_anchors_are_missing(self):
        self.frame = frame("resume-round13-withdraw")
        vision = BrawlVision(self.frame)
        observation = replace(vision.observe(read_panel=False), panel=vision.preparation_panel())
        self.assertEqual(observation.panel, PreparationPanel(13, 7, 8, 8, 160, 0))
        for asset in (FORMATION_GRID_CHECK, SWAP_GRID_ANCHOR):
            self.assertEqual(asset.match_multi_template(self.frame, similarity=.9), [])
        grid = read_formation_grid(self.frame, observation=observation, expected_source=(0, 3))
        self.assertIsNotNone(grid)
        self.assertEqual(grid.selected_source.center, (794, 241))
        self.assertEqual({(cell.screen_row, cell.screen_column) for cell in grid.occupied},
                         {(0, 3), (1, 3), (2, 0), (2, 3), (3, 0), (3, 3), (4, 3), (5, 3)})
        control = read_withdraw_control(self.frame, observation=observation)
        self.assertIsNotNone(control)
        self.assertEqual(control.source, grid.selected_source)
        self.assertEqual(control.button_area, (750, 209, 767, 226))
        self.assertEqual(control.panel, observation.panel)

    def test_anchorless_withdraw_rejects_incomplete_or_ambiguous_current_controls(self):
        original = frame("resume-round13-withdraw")
        vision = BrawlVision(original)
        observation = replace(vision.observe(read_panel=False), panel=vision.preparation_panel())
        for area in ((740, 200, 778, 237), (802, 193, 851, 243)):
            left, top, right, bottom = area
            for corrupt in ("missing", "duplicate", "shifted", "dark"):
                with self.subTest(area=area, corrupt=corrupt):
                    self.frame = original.copy()
                    if corrupt == "duplicate":
                        self.frame[top:bottom, left - 249:right - 249] = original[top:bottom, left:right]
                    elif corrupt == "dark":
                        self.frame[top:bottom, left:right] //= 2
                    else:
                        self.frame[top:bottom, left:right] = 0
                        if corrupt == "shifted":
                            self.frame[top:bottom, left + 16:right + 16] = original[top:bottom, left:right]
                    self.assertIsNone(read_withdraw_control(self.frame, observation=observation))
        self.frame = original.copy()
        self.frame[250:298, 706:756] = 0
        self.assertIsNone(read_withdraw_control(self.frame, observation=observation))
        # Counter contradictions are explicit synthetic observations, not
        # additional captured states. A lone paired source cannot anchor them.
        for panel in (replace(observation.panel, capacity=9), replace(observation.panel, deployed=7)):
            with self.subTest(panel=panel):
                self.assertIsNone(read_withdraw_control(original, observation=replace(observation, panel=panel)))
        self.frame = original
        self.assertIsNotNone(read_withdraw_control(self.frame, observation=observation))

    def test_partial_full_invalid_and_stale_bench_cannot_authorize_withdrawal(self):
        from tasks.brawl.rules import HEROES

        full = tuple(BenchHero(name, Imprint.NONE) for name in tuple(HEROES)[:10])
        for scan in (replace(self.scan, outcome=ScanOutcome.TIMEOUT),
                     replace(self.scan, heroes=full),
                     replace(self.scan, heroes=self.scan.heroes + self.scan.heroes[:1]),
                     replace(self.scan, heroes=(BenchHero("未知英雄", Imprint.NONE),)),
                     replace(self.scan, panel=replace(self.scan.panel, coins=91)),
                     replace(self.scan, heroes=(BenchHero("利纳柯", Imprint.A),) + self.scan.heroes[1:])):
            with self.assertRaises(ObservationError):
                capture_withdraw(self.before, 6, 3, scan)
        for row, col in ((-1, 3), (7, 0), (0, 4), (True, 0)):
            with self.assertRaises(ValueError):
                capture_withdraw(self.before, row, col, self.scan)
        with self.assertRaises(ObservationError):
            capture_withdraw(self.grid, 6, 2, self.scan)

    def test_empty_bench_requires_positive_empty_pixels_in_preparation_and_grid(self):
        from tasks.brawl.bench import BenchViewport

        # Deliberate composite: reuse only the photographed empty queue region.
        # This verifies the overlay gate, not an actual empty-bench action run.
        empty = frame("queue-empty")
        images = []
        for original in (self.before, self.grid):
            edited = original.copy()
            edited[175:550, 1000:1280] = empty[175:550, 1000:1280]
            images.append(edited)
        scan = replace(self.scan, heroes=())
        with patch("tasks.brawl.withdraw.read_bench", return_value=BenchViewport((), panel=scan.panel)):
            request = capture_withdraw(images[0], 6, 3, scan)
            self.assertTrue(capture_withdraw(images[1], 6, 3, scan).selected)
            replay = self.replay(images + [images[1]] * 5)
            self.assertEqual(replay.task.withdraw(request).outcome, WithdrawOutcome.TIMEOUT)
            self.assertEqual([action[1] for action in replay.actions],
                             ["BrawlWithdrawSelect", "BrawlWithdrawMinus"])
            erased = images[0].copy()
            erased[175:550, 1000:1280] = 0
            with self.assertRaises(ObservationError):
                capture_withdraw(erased, 6, 3, scan)

    def test_unknown_idle_pose_can_be_selected_then_named_by_new_bench_card(self):
        self.assertEqual(read_field(self.before).heroes, ())
        replay = self.replay([self.before] + [self.grid] * 8 + [self.after], seconds=.5)
        result = replay.task.withdraw(self.request)
        self.assertEqual(result.outcome, WithdrawOutcome.CONFIRMED)
        self.assertEqual(result.hero, BenchHero("小春", Imprint.B))
        self.assertEqual(read_cursor(self.after).selected.name, "利纳柯")
        self.assertEqual(replay.actions, [(0, "BrawlWithdrawSelect", (794, 577, 795, 578)),
                                          (1, "BrawlWithdrawMinus", (750, 545, 767, 562))])

    def test_already_selected_source_does_not_click_the_hero_again(self):
        request = capture_withdraw(self.grid, 6, 3, self.scan)
        replay = self.replay([self.grid, self.grid, self.after])
        result = replay.task.withdraw(request)
        self.assertEqual(result.hero, BenchHero("小春", Imprint.B))
        self.assertEqual(replay.actions, [(0, "BrawlWithdrawMinus", (750, 545, 767, 562))])

    def test_click_without_selection_or_without_minus_never_retries(self):
        erased = self.grid.copy()
        erased[536:573, 740:778] = 0
        for images in ([self.before] * 5, [self.before] + [erased] * 5):
            replay = self.replay(images)
            self.assertEqual(replay.task.withdraw(self.request).outcome, WithdrawOutcome.TIMEOUT)
            self.assertEqual([action[1] for action in replay.actions], ["BrawlWithdrawSelect"])

    def test_deployed_count_or_card_without_the_other_does_not_confirm(self):
        no_name = self.after.copy()
        no_name[437:500, 1000:1280] = 0
        old_count = self.after.copy()
        old_count[675:711, 307:406] = self.before[675:711, 307:406]
        for pending in (no_name, old_count):
            replay = self.replay([self.before, self.grid] + [pending] * 5)
            result = replay.task.withdraw(self.request)
            self.assertEqual(result.outcome, WithdrawOutcome.TIMEOUT)
            self.assertIsNone(result.hero)
            self.assertEqual([action[1] for action in replay.actions],
                             ["BrawlWithdrawSelect", "BrawlWithdrawMinus"])
        no_imprint = self.after.copy()
        no_imprint[465:488, 1069:1093] = 0
        replay = self.replay([self.before, self.grid, no_imprint, self.after])
        self.assertEqual(replay.task.withdraw(self.request).hero, BenchHero("小春", Imprint.B))
        self.assertEqual(len(replay.actions), 2)

    def test_stale_bench_before_click_and_changed_context_after_click_abort(self):
        replay = self.replay([self.before])
        changed = replace(self.request, bench_before=replace(
            self.scan, heroes=(BenchHero("利纳柯", Imprint.A),) + self.scan.heroes[1:]))
        self.assertEqual(replay.task.withdraw(changed).outcome, WithdrawOutcome.BENCH_CHANGED)
        self.assertEqual(replay.actions, [])
        real = BrawlVision.preparation_panel

        def read_panel(vision):
            panel = real(vision)
            return replace(panel, coins=91) if vision.image is self.after else panel

        replay = self.replay([self.before, self.grid, self.after])
        with patch("tasks.brawl.withdraw.BrawlVision.preparation_panel", autospec=True, side_effect=read_panel):
            result = replay.task.withdraw(self.request)
        self.assertEqual(result.outcome, WithdrawOutcome.CONTEXT_CHANGED)
        self.assertIsNone(result.hero)
        self.assertEqual(len(replay.actions), 2)

    def test_changed_source_and_grid_reentry_cannot_remove_another_hero(self):
        control = read_withdraw_control(self.grid)
        wrong = replace(control, source=replace(control.source, screen_row=5, center=(731, 521)))
        replay = self.replay([self.before, self.grid])
        with patch("tasks.brawl.withdraw.read_withdraw_control", return_value=wrong):
            self.assertEqual(replay.task.withdraw(self.request).outcome, WithdrawOutcome.SOURCE_CHANGED)
        self.assertEqual(len(replay.actions), 1)
        unread = self.after.copy()
        unread[66:114, 1010:1264] = 0
        no_minus = self.grid.copy()
        no_minus[536:573, 740:778] = 0
        for images in ([self.before, no_minus, self.before],
                       [self.before] + [self.grid] * 7 + [unread, self.grid]):
            replay = self.replay(images, seconds=.5)
            self.assertEqual(replay.task.withdraw(self.request).outcome, WithdrawOutcome.SCENE_CHANGED)
            self.assertEqual(len(replay.actions), 1 if len(images) == 3 else 2)

    def test_extra_new_or_changed_old_cards_do_not_confirm_a_single_withdrawal(self):
        actual = read_bench(self.after)
        for entries in (actual.entries + (replace(actual.entries[-1], name="格伦"),),
                        (replace(actual.entries[0], imprint=Imprint.A),) + actual.entries[1:]):
            real = read_bench

            def bench(image, **kwargs):
                return replace(actual, entries=entries) if image is self.after else real(image, **kwargs)

            replay = self.replay([self.before, self.grid, self.after])
            with patch("tasks.brawl.withdraw.read_bench", side_effect=bench):
                self.assertEqual(replay.task.withdraw(self.request).outcome, WithdrawOutcome.BENCH_CHANGED)
            self.assertEqual(len(replay.actions), 2)

    def test_old_full_bench_cannot_be_reused_after_a_successful_removal(self):
        replay = self.replay([self.before, self.grid, self.after])
        self.assertEqual(replay.task.withdraw(self.request).outcome, WithdrawOutcome.CONFIRMED)
        self.assertEqual(replay.task.withdraw(self.request).outcome, WithdrawOutcome.CONTEXT_CHANGED)
        self.assertEqual(len(replay.actions), 2)

    def test_action_interval_survives_an_already_selected_reentry_and_late_success(self):
        request = capture_withdraw(self.grid, 6, 3, self.scan)
        replay = self.replay([self.grid] * 8 + [self.after], seconds=.5)
        replay.task.interval_reset(replay.task.ACTION_TIMER, interval=3)
        self.assertEqual(replay.task.withdraw(request).outcome, WithdrawOutcome.CONFIRMED)
        # A confirmed selected source is a new actionable state, regardless of
        # a preceding independent interaction's timer. The minus is still once.
        self.assertEqual([action[0] for action in replay.actions], [0])
        replay = self.replay([self.before, self.grid, self.after])
        original = replay.screenshot

        def screenshot():
            if replay.index == 1:
                replay.seconds = 13
            original()

        with patch.object(replay, "screenshot", side_effect=screenshot):
            self.assertEqual(replay.task.withdraw(self.request).outcome, WithdrawOutcome.CONFIRMED)
        self.assertEqual(len(replay.actions), 2)


class FullSwapReadingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_full_field_grid_has_ten_occupied_targets_and_no_empty_claim(self):
        self.frame = frame("full-swap-grid")
        self.assertIsNone(read_board(self.frame))
        targets = read_swap_targets(self.frame)
        self.assertIsNotNone(targets)
        self.assertEqual(targets.panel.deployed, 10)
        self.assertEqual([(cell.screen_row, cell.screen_column) for cell in targets.occupied],
                         [(0, 3), (1, 3), (2, 3), (3, 2), (3, 3),
                          (4, 1), (4, 3), (5, 2), (5, 3), (6, 3)])
        self.assertEqual(targets.occupied[-1].center, (794, 577))
        report = analyze(self.frame)
        self.assertIsNone(report["board"])
        self.assertEqual(len(report["swap_targets"]["occupied"]), 10)

    def test_missing_anchor_or_occupied_control_keeps_the_set_unknown(self):
        original = frame("full-swap-grid")
        for area in ((829, 251, 857, 266), (768, 530, 818, 578)):
            self.frame = original.copy()
            x1, y1, x2, y2 = area
            self.frame[y1:y2, x1:x2] = 0
            with self.subTest(area=area):
                self.assertIsNone(read_swap_targets(self.frame))
        for sample in ("full-swap-before", "full-swap-after", "multi-transmit-empty", "move-source"):
            self.frame = frame(sample)
            self.assertIsNone(read_swap_targets(self.frame))

    def test_new_field_identities_have_independent_captured_checks(self):
        # These positives are NOT the source captures used for the two assets.
        for sample, expected in (("full-bench-repeat", "战斗型玛雅"), ("lower-summon-return", "小春")):
            self.frame = frame(sample)
            field = read_field(self.frame)
            heroes = [hero for hero in field.heroes if hero.name == expected]
            self.assertEqual(len(heroes), 1)
            self.assertEqual(heroes[0].foot, (794, 577))
            self.assertIsNone(heroes[0].imprint)
            self.assertFalse(field.complete)

    def test_swap_request_uses_incoming_card_and_separate_outgoing_sprite(self):
        self.frame = frame("full-swap-before")
        request = capture_swap(self.frame, 6, 3)
        self.assertEqual((request.hero.name, request.outgoing_name, request.target),
                         ("小春", "战斗型玛雅", (794, 577)))
        after = frame("full-swap-after")
        self.assertEqual(read_cursor(after).selected.name, "卡琳")
        self.assertEqual([hero.name for hero in read_field(after).heroes], ["小春"])
        for row, column in ((6, 0), (3, 2)):
            with self.assertRaises(ObservationError):
                capture_swap(self.frame, row, column)

    def test_unmatched_idle_pose_remains_unknown_without_lowering_confidence(self):
        self.frame = frame("full-swap-stable")
        field = read_field(self.frame)
        self.assertEqual(field.heroes, ())
        self.assertEqual(field.unknown_count, 10)
        self.assertFalse(field.identity_complete)


class FullSwapReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.before = frame("full-swap-before")
        self.grid = frame("full-swap-grid")
        self.after = frame("full-swap-after")
        self.request = capture_swap(self.before, 6, 3)

    def replay(self, images, seconds=.5):
        replay = BrawlReplay(BrawlSwap, images, self.clock, seconds=seconds)
        self.frame = replay.image
        return replay

    def test_real_full_field_swap_confirms_position_without_using_new_focus(self):
        replay = self.replay([self.before] + [self.grid] * 7 + [self.after])
        replay.task.interval_reset(replay.task.ACTION_TIMER, interval=3)
        self.assertEqual(replay.task.swap_selected(self.request), SwapOutcome.CONFIRMED)
        self.assertEqual(replay.actions, [(0, "BrawlSwapOpen", (943, 268, 977, 303)),
                                          (1, "BrawlSwapCell", (794, 577, 795, 578))])

    def test_unknown_post_swap_pose_waits_for_independent_known_pose(self):
        replay = self.replay([self.before] + [self.grid] * 7
                             + ["full-swap-stable"] * 4 + ["lower-summon-return"])
        # This return is from a later paid summon, not the swap. Its changed
        # resources must stop the request, even though the sprite is readable.
        self.assertEqual(replay.task.swap_selected(self.request), SwapOutcome.CONTEXT_CHANGED)
        self.assertEqual([action[1] for action in replay.actions], ["BrawlSwapOpen", "BrawlSwapCell"])

    def test_unknown_pose_then_real_swap_frame_confirms_without_reclicking(self):
        replay = self.replay([self.before] + [self.grid] * 7 + ["full-swap-stable"] * 4 + [self.after])
        self.assertEqual(replay.task.swap_selected(self.request), SwapOutcome.CONFIRMED)
        self.assertEqual([action[0] for action in replay.actions], [0, 1])

    def test_unchanged_deployed_count_and_missing_sprite_do_not_confirm(self):
        erased = self.after.copy()
        erased[470:529, 777:810] = 0
        replay = self.replay([self.before] + [self.grid] * 7 + [erased] * 28)
        self.assertEqual(replay.task.swap_selected(self.request), SwapOutcome.TIMEOUT)
        self.assertEqual([action[1] for action in replay.actions], ["BrawlSwapOpen", "BrawlSwapCell"])

    def test_source_or_selected_card_changes_abort_before_clicking(self):
        erased = self.before.copy()
        erased[486:543, 781:813] = 0
        for image, expected in ((erased, SwapOutcome.TARGET_UNAVAILABLE),
                                (frame("focus-rin"), SwapOutcome.SELECTION_CHANGED)):
            replay = self.replay([image])
            self.assertEqual(replay.task.swap_selected(self.request), expected)
            self.assertEqual(replay.actions, [])

    def test_closed_grid_and_reappearing_grid_cannot_reuse_an_old_request(self):
        unread = self.after.copy()
        unread[66:114, 1010:1264] = 0
        no_target = self.grid.copy()
        no_target[530:578, 768:818] = 0
        for images in ([self.before, no_target, self.before],
                       [self.before] + [self.grid] * 7 + [unread, self.grid]):
            replay = self.replay(images)
            self.assertEqual(replay.task.swap_selected(self.request), SwapOutcome.SCENE_CHANGED)
            self.assertEqual(sum(action[1] == "BrawlSwapCell" for action in replay.actions),
                             0 if len(images) == 3 else 1)

    def test_changed_header_or_missing_targets_prevent_a_destination_click(self):
        real = BrawlVision.preparation_panel

        def read_panel(vision):
            panel = real(vision)
            return replace(panel, coins=38) if vision.image is self.grid else panel

        replay = self.replay([self.before, self.grid])
        with patch("tasks.brawl.swap.BrawlVision.preparation_panel", autospec=True, side_effect=read_panel):
            self.assertEqual(replay.task.swap_selected(self.request), SwapOutcome.CONTEXT_CHANGED)
        self.assertEqual([action[1] for action in replay.actions], ["BrawlSwapOpen"])
        erased = self.grid.copy()
        erased[530:578, 768:818] = 0
        replay = self.replay([self.before] + [erased] * 28)
        self.assertEqual(replay.task.swap_selected(self.request), SwapOutcome.TIMEOUT)
        self.assertEqual([action[1] for action in replay.actions], ["BrawlSwapOpen"])

    def test_slow_positive_return_wins_before_timeout(self):
        replay = self.replay([self.before, self.grid, self.after], seconds=4)
        original = replay.screenshot

        def screenshot():
            if replay.index == 1:
                replay.seconds = 13
            original()

        with patch.object(replay, "screenshot", side_effect=screenshot):
            self.assertEqual(replay.task.swap_selected(self.request), SwapOutcome.CONFIRMED)
        self.assertEqual([action[0] for action in replay.actions], [0, 1])


class IdentificationSwapTests(unittest.TestCase):
    """Real swap images plus explicitly synthetic complete bench-scan outputs."""

    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.before = frame("full-swap-before")
        self.frame = self.before
        current = BrawlVision(self.before).observe()
        # User-confirmed names/imprints, represented as synthetic complete scans.
        # The supplied focus screenshots are discontinuous and therefore do NOT
        # establish a real top-to-bottom scan or its endpoint transitions.
        self.before_scan = BenchScanResult(tuple(BenchHero(name, imprint) for name, imprint in (
            ("克莉丝缇", Imprint.B), ("格伦", Imprint.C), ("玫勒赛德丝", Imprint.C),
            ("西风处刑者修里", Imprint.C), ("小春", Imprint.B), ("卡琳", Imprint.C),
            ("利纳柯", Imprint.B), ("最后的骑士克劳乌", Imprint.A), ("实验体赛兹", Imprint.B),
            ("指挥型莱伊卡", Imprint.NONE),
        )), current.panel, current.game_mode, ScanOutcome.COMPLETE)
        self.after_scan = replace(self.before_scan, heroes=tuple(
            BenchHero("战斗型玛雅", Imprint.A) if hero.name == "小春" else hero
            for hero in self.before_scan.heroes))

    def request(self, *, image=None, row=6, column=3):
        return capture_identification_swap(
            self.before if image is None else image, row, column, self.before_scan)

    def replay(self, images):
        replay = BrawlReplay(BrawlSwap, images, self.clock)
        self.frame = replay.image
        return replay

    def test_full_bench_identification_uses_scan_difference_not_automatic_focus(self):
        request = self.request()
        self.assertIsNone(request.outgoing_name)
        self.assertEqual(request.bench_before.free_slots, 0)
        replay = self.replay([self.before] + ["full-swap-grid"] * 7 + ["full-swap-after"])
        outcome = replay.task.swap_selected(request)
        self.assertEqual(outcome, SwapOutcome.CONFIRMED)
        self.assertEqual([action[0] for action in replay.actions], [0, 1])
        self.assertEqual(read_cursor(replay.image).selected.name, "卡琳")
        identified = identify_swapped_hero(request, self.after_scan, outcome)
        self.assertEqual(identified, BenchHero("战斗型玛雅", Imprint.A))
        # These later real viewports show Maya's name and imprint. The return
        # capture is independent of the small-A asset's transmit source. Neither
        # viewport is used as a before/after complete bench scan: the whole
        # scan outputs above remain explicitly synthetic inputs to this replay.
        for sample in ("multi-transmit-three", "multi-transmit-return"):
            self.frame = frame(sample)
            visible = read_bench(self.frame)
            self.assertIn((identified.name, identified.imprint),
                          [(entry.name, entry.imprint) for entry in visible.entries])
        self.frame = self.frame.copy()
        self.frame[409:431, 1050:1068] = 0
        visible = read_bench(self.frame)
        self.assertIn((identified.name, None),
                      [(entry.name, entry.imprint) for entry in visible.entries])

    def test_unknown_outgoing_pose_does_not_prevent_explicit_identification_swap(self):
        erased = self.before.copy()
        erased[486:543, 781:813] = 0
        self.assertEqual(read_field(erased).heroes, ())
        with self.assertRaises(ObservationError):
            capture_swap(erased, 6, 3)
        request = self.request(image=erased)
        replay = self.replay([erased] + ["full-swap-grid"] * 7 + ["full-swap-after"])
        self.assertEqual(replay.task.swap_selected(request), SwapOutcome.CONFIRMED)
        self.assertEqual([action[1] for action in replay.actions], ["BrawlSwapOpen", "BrawlSwapCell"])

    def test_identification_rejects_incomplete_invalid_or_stale_bench_before_opening(self):
        original = self.before_scan
        for scan in (
            replace(original, outcome=ScanOutcome.TIMEOUT),
            replace(original, panel=replace(original.panel, coins=38)),
            replace(original, game_mode=BrawlMode.CHALLENGE),
            replace(original, heroes=original.heroes + (original.heroes[0],)),
            replace(original, heroes=tuple(hero for hero in original.heroes if hero.name != "小春")),
            replace(original, heroes=(BenchHero("克莉丝缇", None),) + original.heroes[1:]),
            replace(original, heroes=(BenchHero("不明英雄", Imprint.B),) + original.heroes[1:]),
        ):
            with self.subTest(scan=scan), self.assertRaises(ObservationError):
                capture_identification_swap(self.before, 6, 3, scan)
        with self.assertRaises(ValueError):
            self.request(row=True)

    def test_stale_visible_bench_or_known_field_conflict_forbids_reopening(self):
        request = self.request()
        # A same-context snapshot can still be stale: require every currently
        # readable neighboring card to match it before opening or retrying plus.
        stale = replace(self.before_scan, heroes=tuple(
            replace(hero, imprint=Imprint.A) if hero.name == "利纳柯" else hero
            for hero in self.before_scan.heroes))
        occupied = replace(self.before_scan, heroes=self.before_scan.heroes[:-1]
                           + (BenchHero("战斗型玛雅", Imprint.A),))
        for scan in (stale, occupied):
            replay = self.replay([self.before])
            with self.subTest(scan=scan):
                self.assertEqual(replay.task.swap_selected(replace(request, bench_before=scan)),
                                 SwapOutcome.TARGET_UNAVAILABLE)
                self.assertEqual(replay.actions, [])

    def test_unknown_target_requires_positive_occupied_control(self):
        request = self.request(row=6, column=0)
        replay = self.replay([self.before] + ["full-swap-grid"] * 7)
        self.assertEqual(replay.task.swap_selected(request), SwapOutcome.TARGET_UNAVAILABLE)
        self.assertEqual([action[1] for action in replay.actions], ["BrawlSwapOpen"])
        erased = frame("full-swap-grid")
        erased[530:578, 768:818] = 0
        replay = self.replay([self.before] + [erased] * 28)
        self.assertEqual(replay.task.swap_selected(self.request()), SwapOutcome.TIMEOUT)
        self.assertEqual([action[1] for action in replay.actions], ["BrawlSwapOpen"])

    def test_missing_incoming_pose_never_confirms_or_repeats_the_exchange(self):
        erased = frame("full-swap-after")
        erased[470:529, 777:810] = 0
        replay = self.replay([self.before] + ["full-swap-grid"] * 7 + [erased] * 28)
        request = self.request()
        outcome = replay.task.swap_selected(request)
        self.assertEqual(outcome, SwapOutcome.TIMEOUT)
        self.assertEqual([action[1] for action in replay.actions], ["BrawlSwapOpen", "BrawlSwapCell"])
        self.assertIsNone(identify_swapped_hero(request, self.after_scan, outcome))

    def test_scan_difference_requires_one_exchange_with_unchanged_imprints_and_context(self):
        request = self.request()
        original = self.after_scan
        for scan in (
            self.before_scan,
            replace(original, outcome=ScanOutcome.TIMEOUT),
            replace(original, panel=replace(original.panel, deployed=9)),
            replace(original, panel=replace(original.panel, synergy_tokens=1)),
            replace(original, game_mode=BrawlMode.CHALLENGE),
            replace(original, heroes=original.heroes[:-1]),
            replace(original, heroes=original.heroes[:-1] + (original.heroes[0],)),
            replace(original, heroes=(replace(original.heroes[0], imprint=Imprint.A),)
                    + original.heroes[1:]),
            replace(original, heroes=tuple(
                replace(hero, imprint=None) if hero.name == "战斗型玛雅" else hero
                for hero in original.heroes)),
            replace(original, heroes=tuple(
                BenchHero("小春", Imprint.A) if hero.name == "战斗型玛雅" else hero
                for hero in original.heroes)),
        ):
            with self.subTest(scan=scan):
                self.assertIsNone(identify_swapped_hero(request, scan, SwapOutcome.CONFIRMED))
        # Reordering unchanged cards carries no identity or imprint mutation.
        self.assertEqual(identify_swapped_hero(
            request, replace(original, heroes=tuple(reversed(original.heroes))), SwapOutcome.CONFIRMED),
            BenchHero("战斗型玛雅", Imprint.A))
