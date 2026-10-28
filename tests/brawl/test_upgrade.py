import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.brawl import analyze  # noqa: E402
from tasks.brawl.observation_cache import (  # noqa: E402
    invalidate_panel_observation_cache,
    panel_observation_cache,
)
from tasks.brawl.upgrade import (  # noqa: E402
    BrawlUpgrade,
    UpgradeDialog,
    UpgradeOutcome,
    UpgradeRequest,
    capture_upgrade,
    parse_upgrade_fields,
    read_upgrade_dialog,
)
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlObservation,
    BrawlScreen,
    ObservationError,
    PreparationPanel,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402

PANEL = PreparationPanel(8, 4, 5, 5, 205, 0)
REQUEST = UpgradeRequest(PANEL, BrawlMode.NORMAL)
DIALOG = UpgradeDialog(PANEL, BrawlMode.NORMAL, 5, 6, 100, True)
AFTER = replace(PANEL, level=5, capacity=6, coins=105)


def step(state=BrawlScreen.PREPARATION, *, panel=PANEL, mode=BrawlMode.NORMAL,
         dialog=DIALOG, entry=True):
    return SimpleNamespace(observation=BrawlObservation(state, panel, game_mode=mode),
                           dialog=dialog, entry=entry)


READY = step()
QUOTE = step(BrawlScreen.LEVEL_UP, panel=None)
DONE = step(panel=AFTER)
UNKNOWN = step(BrawlScreen.UNKNOWN, panel=None, mode=None)


class UpgradeReadingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_quote_checks_price_capacity_header_and_button(self):
        self.frame = frame("upgrade-dialog")
        self.assertEqual(read_upgrade_dialog(self.frame), DIALOG)
        self.frame = frame("upgrade")
        self.assertEqual(read_upgrade_dialog(self.frame), replace(DIALOG, header=replace(PANEL, round_number=7, coins=145)))

    def test_capture_enforces_reserve_funds_maximum_and_preparation(self):
        self.frame = frame("upgrade-before")
        self.assertEqual(capture_upgrade(self.frame, 105), replace(REQUEST, reserve_coins=105))
        with self.assertRaises(ObservationError):
            capture_upgrade(self.frame, 106)
        for reserve in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                capture_upgrade(self.frame, reserve)
        for name in ("prepare-max", "prepare-level8", "upgrade-dialog", "combat", "upgrade-animation"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                with self.assertRaises(ObservationError):
                    capture_upgrade(self.frame)

    def test_hidden_price_or_wrong_scene_cannot_create_a_quote(self):
        for name in ("upgrade-before", "prepare-level5", "hero-result", "give-up"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                with self.assertRaises(ObservationError):
                    read_upgrade_dialog(self.frame)
        self.frame = frame("upgrade-dialog").copy()
        self.frame[504:533, 677:730] = 0
        with self.assertRaises(ObservationError):
            read_upgrade_dialog(self.frame)

    def test_fields_validate_all_levels_prices_capacities_and_confidence(self):
        valid = [("Lv.4", .99), ("Lv.5", .99), ("5", .99), ("6", .99), ("100", .99)]
        self.assertEqual(parse_upgrade_fields(valid), (4, 5, 5, 6, 100))
        self.assertEqual(parse_upgrade_fields([(text, .99) for text in ("Lv.8", "Lv.MAX", "9", "10", "250")]),
                         (8, 9, 9, 10, 250))
        for index, text in ((0, "Lv.MAX"), (1, "Lv.6"), (2, "4"), (3, "5"), (4, "10"), (4, "100x")):
            values = valid.copy()
            values[index] = (text, .99)
            with self.subTest(index=index, text=text), self.assertRaises(ObservationError):
                parse_upgrade_fields(values)
        for score in (.79, float("nan"), float("inf")):
            values = valid.copy()
            values[4] = ("100", score)
            with self.assertRaises(ObservationError):
                parse_upgrade_fields(values)
        with self.assertRaises(ObservationError):
            parse_upgrade_fields(valid[:-1])

    def test_wrong_header_price_and_model_failures_are_not_hidden(self):
        self.frame = frame("upgrade-dialog")
        with patch("tasks.brawl.upgrade.BrawlVision.preparation_panel", return_value=replace(PANEL, level=3, capacity=4)), \
                self.assertRaises(ObservationError):
            read_upgrade_dialog(self.frame)
        with patch("tasks.brawl.upgrade.Ocr.ocr_multi_lines", side_effect=RuntimeError("model failed")), \
                self.assertRaisesRegex(RuntimeError, "model failed"):
            read_upgrade_dialog(self.frame)

    def test_analysis_reports_quote_without_claiming_payment(self):
        self.frame = frame("upgrade-dialog")
        report = analyze(self.frame)
        self.assertEqual(report["upgrade"]["cost"], 100)
        self.assertEqual(report["upgrade"]["next_level"], 5)
        self.assertTrue(report["upgrade"]["affordable"])
        self.assertFalse(report["upgrade"]["confirmed"])
        self.assertIsNone(report["economy"])


class UpgradeReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, steps, *, seconds=.5):
        images = []
        for index in range(len(steps)):
            image = np.zeros((720, 1280, 3), np.uint8)
            image[0, 0, 0] = index
            images.append(image)
        replay = BrawlReplay(BrawlUpgrade, images, self.clock, seconds)

        def current(image):
            return steps[int(image[0, 0, 0])]

        def read_dialog(image):
            dialog = current(image).dialog
            if isinstance(dialog, Exception):
                raise dialog
            return dialog

        patches = (
            patch("tasks.brawl.upgrade.BrawlVision", side_effect=lambda image: SimpleNamespace(
                observe=lambda **kwargs: current(image).observation,
                matches=lambda button: current(image).entry)),
            patch("tasks.brawl.upgrade.read_upgrade_dialog", side_effect=read_dialog),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.frame = replay.image
        return replay

    def assert_actions(self, replay, names):
        self.assertEqual([action[1] for action in replay.actions], names)

    def test_real_sequence_confirms_level_capacity_and_exact_debit_once(self):
        self.frame = frame("upgrade-before")
        request = capture_upgrade(self.frame)
        self.assertEqual(request, REQUEST)
        replay = BrawlReplay(BrawlUpgrade,
                             ["upgrade-before"] * 2 + ["upgrade-dialog"] * 7
                             + ["upgrade-animation"] * 2 + ["prepare-level5"], self.clock)
        with patch("tasks.brawl.upgrade.read_upgrade_dialog", wraps=read_upgrade_dialog) as reader:
            self.assertEqual(replay.task.upgrade_once(request), UpgradeOutcome.CONFIRMED)
        reader.assert_called_once()
        self.assertEqual(replay.actions, [(0, "UPGRADE_ENTRY", (248, 649, 292, 695)),
                                          (2, "UPGRADE_CONFIRM", (767, 494, 873, 545))])

    def test_cached_upgrade_refreshes_before_request_and_after_payment_without_repaying_old_frames(self):
        from module.ocr.ocr import Ocr

        # All pixels are captured samples; only their repetition/order models a
        # delayed response. A click never advances the frame or supplies a debit.
        replay = BrawlReplay(BrawlUpgrade,
                             ["upgrade-before"] * 2 + ["upgrade-dialog"] * 11
                             + ["upgrade-before"] * 3 + ["prepare-level5"], self.clock)
        self.frame = replay.image
        reads, invalidations = [], []
        real_ocr = Ocr.ocr_multi_lines

        def counted_read(reader, images):
            reads.append((replay.index, reader.name))
            return real_ocr(reader, images)

        def invalidate():
            invalidations.append((replay.index, tuple(action[1] for action in replay.actions)))
            invalidate_panel_observation_cache()

        with panel_observation_cache(), \
                patch.object(Ocr, "ocr_multi_lines", autospec=True, side_effect=counted_read), \
                patch("tasks.brawl.upgrade.invalidate_panel_observation_cache", side_effect=invalidate):
            # Prime a valid header in the same scope. Starting the request must
            # still refresh affordability even when those exact pixels persist.
            request = capture_upgrade(replay.image)
            self.assertEqual(request, REQUEST)
            self.assertEqual(replay.task.upgrade_once(request), UpgradeOutcome.CONFIRMED)
        self.assertEqual(invalidations, [
            (0, ()), (2, ("UPGRADE_ENTRY", "UPGRADE_CONFIRM"))])
        self.assertEqual([index for index, name in reads if name == "BRAWL_PANEL"], [0, 0, 2, 13, 16])
        self.assertEqual([index for index, name in reads if name == "BRAWL_UPGRADE_QUOTE"], [2])
        self.assertEqual(replay.index, 16)
        self.assertEqual(replay.actions, [(0, "UPGRADE_ENTRY", (248, 649, 292, 695)),
                                          (2, "UPGRADE_CONFIRM", (767, 494, 873, 545))])

    def test_cache_invalidation_does_not_repeat_on_unconfirmed_opening_or_payment(self):
        cases = (
            ([READY] * 27, ["UPGRADE_ENTRY"] * 4, [(0, ())]),
            ([READY] + [QUOTE] * 42, ["UPGRADE_ENTRY", "UPGRADE_CONFIRM"],
             [(0, ()), (1, ("UPGRADE_ENTRY", "UPGRADE_CONFIRM"))]),
        )
        for frames, actions, expected_invalidations in cases:
            with self.subTest(expected_actions=actions):
                replay = self.replay(frames)
                invalidations = []

                def invalidate(replay=replay, invalidations=invalidations):
                    invalidations.append((replay.index, tuple(action[1] for action in replay.actions)))
                    invalidate_panel_observation_cache()

                # Explicit simulated states retain the production timer and
                # payment latch. Cache invalidation follows owned transactions,
                # not opening retries, duplicate quotes or a timeout itself.
                with panel_observation_cache(), \
                        patch("tasks.brawl.upgrade.invalidate_panel_observation_cache", side_effect=invalidate):
                    self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.TIMEOUT)
                self.assert_actions(replay, actions)
                self.assertEqual(invalidations, expected_invalidations)

    def test_delayed_digits_and_unknown_frames_never_repeat_payment(self):
        unread = step(panel=None)
        old_balance = step(panel=replace(AFTER, coins=PANEL.coins))
        replay = self.replay([READY] + [QUOTE] * 7 + [UNKNOWN, unread, old_balance, old_balance, DONE])
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.CONFIRMED)
        self.assert_actions(replay, ["UPGRADE_ENTRY", "UPGRADE_CONFIRM"])

    def test_repeated_dialog_without_a_result_times_out_after_one_payment(self):
        replay = self.replay([READY] + [QUOTE] * 42)
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.TIMEOUT)
        self.assert_actions(replay, ["UPGRADE_ENTRY", "UPGRADE_CONFIRM"])

    def test_no_response_retries_only_opening_at_three_second_intervals(self):
        replay = self.replay([READY] * 27)
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.TIMEOUT)
        self.assertEqual([action[0] for action in replay.actions], [0, 7, 14, 21])
        self.assert_actions(replay, ["UPGRADE_ENTRY"] * 4)

    def test_verified_dialog_pays_immediately_despite_a_previous_action_cooldown(self):
        replay = self.replay([READY, QUOTE, QUOTE, QUOTE, DONE], seconds=.2)
        replay.task.interval_reset(replay.task.ACTION_TIMER, interval=3)
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.CONFIRMED)
        self.assertEqual([action[0] for action in replay.actions], [0, 1])
        self.assert_actions(replay, ["UPGRADE_ENTRY", "UPGRADE_CONFIRM"])
        self.assertEqual(replay.index, 4)

    def test_changed_request_or_dialog_context_prevents_payment(self):
        for panel in (replace(PANEL, round_number=9), replace(PANEL, coins=204), replace(PANEL, deployed=4),
                      replace(PANEL, synergy_tokens=1)):
            with self.subTest(panel=panel):
                replay = self.replay([step(panel=panel)])
                self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.CONTEXT_CHANGED)
                self.assert_actions(replay, [])
                replay = self.replay([READY] + [step(BrawlScreen.LEVEL_UP, panel=None,
                                                    dialog=replace(DIALOG, header=panel))] * 7)
                self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.CONTEXT_CHANGED)
                self.assert_actions(replay, ["UPGRADE_ENTRY"])

    def test_disabled_or_unread_quote_never_authorizes_confirmation(self):
        for dialog in (replace(DIALOG, ready=False), ObservationError("Unrecognized price")):
            with self.subTest(dialog=dialog):
                replay = self.replay([READY] + [step(BrawlScreen.LEVEL_UP, panel=None, dialog=dialog)] * 27)
                self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.TIMEOUT)
                self.assert_actions(replay, ["UPGRADE_ENTRY"])

    def test_unexpected_balance_or_level_cannot_confirm_an_upgrade(self):
        for panel, outcome in ((replace(AFTER, coins=104), UpgradeOutcome.BALANCE_MISMATCH),
                               (replace(AFTER, coins=206), UpgradeOutcome.BALANCE_MISMATCH),
                               (replace(AFTER, level=6, capacity=7), UpgradeOutcome.CONTEXT_CHANGED),
                               (replace(AFTER, synergy_tokens=1), UpgradeOutcome.CONTEXT_CHANGED),
                               (replace(AFTER, round_number=9), UpgradeOutcome.CONTEXT_CHANGED),
                               (replace(AFTER, deployed=6), UpgradeOutcome.CONTEXT_CHANGED)):
            with self.subTest(panel=panel):
                replay = self.replay([READY] + [QUOTE] * 7 + [step(panel=panel)])
                self.assertEqual(replay.task.upgrade_once(REQUEST), outcome)
                self.assert_actions(replay, ["UPGRADE_ENTRY", "UPGRADE_CONFIRM"])

    def test_existing_or_reopened_dialog_is_not_another_purchase(self):
        replay = self.replay([QUOTE])
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.SCENE_CHANGED)
        self.assert_actions(replay, [])
        replay = self.replay([READY] + [QUOTE] * 7 + [step(panel=None), QUOTE])
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.SCENE_CHANGED)
        self.assert_actions(replay, ["UPGRADE_ENTRY", "UPGRADE_CONFIRM"])
        # An unread quote cannot authorize payment even when preparation
        # returns before three seconds; the old cooldown is not the guard.
        unread_quote = step(BrawlScreen.LEVEL_UP, panel=None, dialog=ObservationError("unread quote"))
        replay = self.replay([READY, unread_quote, step(panel=None)])
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.SCENE_CHANGED)
        self.assert_actions(replay, ["UPGRADE_ENTRY"])

    def test_missing_opening_control_and_unknown_scenes_never_click(self):
        for value in (step(entry=False), UNKNOWN):
            with self.subTest(scene=value.observation):
                replay = self.replay([value] * 27)
                self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.TIMEOUT)
                self.assert_actions(replay, [])

    def test_slow_dialog_cannot_reset_an_expired_opening_deadline(self):
        replay = self.replay([READY, QUOTE], seconds=13)
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.TIMEOUT)
        self.assert_actions(replay, ["UPGRADE_ENTRY"])

    def test_slow_verified_result_takes_priority_over_local_timeout(self):
        replay = self.replay([READY, QUOTE, QUOTE, DONE], seconds=6)
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.CONFIRMED)
        self.assert_actions(replay, ["UPGRADE_ENTRY", "UPGRADE_CONFIRM"])

    def test_mode_change_interruption_and_fresh_first_frame_prevent_spending(self):
        replay = self.replay([step(mode=BrawlMode.CHALLENGE)])
        self.assertEqual(replay.task.upgrade_once(REQUEST), UpgradeOutcome.CONTEXT_CHANGED)
        self.assert_actions(replay, [])
        replay = self.replay([READY, step(BrawlScreen.COMBAT, panel=None)])
        self.assertEqual(replay.task.upgrade_once(REQUEST, skip_first_screenshot=False), UpgradeOutcome.SCENE_CHANGED)
        self.assert_actions(replay, [])
        with self.assertRaises(ValueError):
            replay.task.upgrade_once(replace(REQUEST, reserve_coins=106))
