"""Captured navigation anchors with explicit synthetic waits and missing controls."""

import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from module.exception import GamePageUnknownError  # noqa: E402
from tasks.base.page import Page  # noqa: E402
from tasks.brawl.assets.assets_brawl_entry import (  # noqa: E402
    BRAWL_ENTRY,
    LOBBY_CONTINUE,
    LOBBY_ENTER,
)
from tasks.brawl.entry import BrawlEntry  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import (  # noqa: E402
    ROOT,
    ControlledClock,
)


class EntryTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.addCleanup(Page.clear_connection)

    def replay(self, frames, seconds=0.5):
        replay = BrawlReplay(BrawlEntry, frames, self.clock, seconds=seconds)
        replay.config = replay.task.config
        replay.config.Emulator_GameLanguage = "cn"
        replay.config.Emulator_PackageName = "OVERSEA-Play"
        replay.stuck_record_add = Mock()
        # Only unrelated recovery handlers are disabled. Page recognition,
        # route matching, main-page confirmation and action timers stay real.
        replay.task.handle_ui_recovery = Mock(return_value=False)
        replay.task.ui_additional = Mock(return_value=False)
        replay.task.handle_popup_confirm = Mock(return_value=False)
        self.frame = replay.image
        return replay

    def test_existing_preparation_returns_without_global_navigation_or_clicks(self):
        replay = self.replay(["resume-preparation"])
        with patch.object(replay.task, "ui_ensure") as navigate:
            result = replay.task.enter()
        navigate.assert_not_called()
        self.assertTrue(result.resumed)
        self.assertEqual(result.observation.state, BrawlScreen.PREPARATION)
        self.assertEqual(result.observation.panel.deployed, 2)
        self.assertEqual(replay.index, 0)
        self.assertEqual(replay.actions, [])

    def test_home_routes_through_season_and_only_continues_the_existing_save(self):
        # These four captures show the supplied route. Repeated frames and
        # their one-second timing are simulated independently from clicks.
        replay = self.replay(["navigation-main"] * 2 + ["navigation-season"]
                             + ["lobby-saved-opening"] * 6 + ["resume-preparation"], seconds=1)
        result = replay.task.enter()
        self.assertTrue(result.resumed)
        self.assertEqual(result.observation.game_mode, BrawlMode.NORMAL)
        self.assertEqual(result.observation.panel.deployed, 2)
        self.assertEqual([(item[0], item[1]) for item in replay.actions],
                         [(1, "MAIN_GOTO_COMBAT"), (2, "BRAWL_ENTRY"), (7, "LOBBY_CONTINUE")])
        self.assertTrue(all(page.parent is None for page in Page.iter_pages()))

    def test_repeated_saved_lobby_frames_never_repeat_entry_or_choose_other_controls(self):
        replay = self.replay(["lobby-saved-opening"] * 12 + ["resume-preparation"])
        result = replay.task.enter()
        self.assertTrue(result.resumed)
        self.assertEqual([(item[0], item[1]) for item in replay.actions], [(0, "LOBBY_CONTINUE")])

    def test_lobby_entry_preserves_the_shared_action_interval(self):
        replay = self.replay(["lobby-saved-opening"] * 9 + ["resume-preparation"])
        replay.task.interval_reset("BrawlBenchScan", interval=3)
        result = replay.task.enter()
        self.assertTrue(result.resumed)
        self.assertEqual([(item[0], item[1]) for item in replay.actions], [(7, "LOBBY_CONTINUE")])

    def test_free_new_run_keeps_each_lobby_mode_and_requires_first_empty_preparation(self):
        for lobby, preparation, mode in (
                ("normal-lobby-empty", "normal-opening", BrawlMode.NORMAL),
                ("challenge-lobby-empty", "challenge-opening", BrawlMode.CHALLENGE)):
            with self.subTest(mode=mode):
                replay = self.replay([lobby] * 10 + [preparation])
                result = replay.task.enter()
                self.assertFalse(result.resumed)
                self.assertEqual(result.observation.game_mode, mode)
                self.assertEqual(result.observation.panel.round_number, 1)
                self.assertEqual(result.observation.panel.deployed, 0)
                self.assertEqual([(item[0], item[1]) for item in replay.actions], [(0, "LOBBY_ENTER")])

    def test_missing_free_evidence_never_authorizes_paid_entry(self):
        image = frame("normal-lobby-empty").copy()
        # Explicit negative variant: erase only the complete free-price search
        # region, retaining the captured entry button and empty-save marker.
        image[630:695, 962:1047] = 0
        replay = self.replay([image])
        with self.assertRaisesRegex(ObservationError, "Free entry is not confirmed"):
            replay.task.enter()
        self.assertEqual(replay.actions, [])

    def test_disabled_free_only_still_uses_a_verified_free_entry(self):
        replay = self.replay(["normal-lobby-empty"] * 10 + ["normal-opening"])
        result = replay.task.enter(only_free=False)
        self.assertFalse(result.resumed)
        self.assertEqual(result.observation.game_mode, BrawlMode.NORMAL)
        self.assertEqual([item[1] for item in replay.actions], ["LOBBY_ENTER"])

    def test_disabled_free_only_requires_a_real_paid_quote_before_any_entry(self):
        image = frame("normal-lobby-empty").copy()
        image[630:695, 962:1047] = 0
        replay = self.replay([image])
        with self.assertRaisesRegex(ObservationError, "verified price and available resources"):
            replay.task.enter(only_free=False)
        self.assertEqual(replay.actions, [])

    def test_invalid_free_only_setting_stops_before_navigation_or_clicks(self):
        for value in (None, 0, 1, "false", "true"):
            with self.subTest(value=value):
                replay = self.replay(["navigation-main"])
                with patch.object(replay.task, "ui_ensure") as navigate, \
                        self.assertRaisesRegex(ValueError, "must be a boolean"):
                    replay.task.enter(only_free=value)
                navigate.assert_not_called()
                self.assertEqual(replay.index, 0)
                self.assertEqual(replay.actions, [])

    def test_conflicting_saved_and_new_controls_stop_without_any_click(self):
        replay = self.replay(["lobby-saved-opening"])
        actual_matches = BrawlVision.matches

        def matches(vision, button, **kwargs):
            # Deliberately inject one impossible positive control; all actual
            # page and mode anchors still come from the saved-lobby capture.
            return True if button is LOBBY_ENTER else actual_matches(vision, button, **kwargs)

        with patch("tasks.brawl.entry.BrawlVision.matches", autospec=True, side_effect=matches), \
                self.assertRaisesRegex(ObservationError, "contradictory"):
            replay.task.enter()
        self.assertEqual(replay.actions, [])

    def test_real_give_up_overlay_keeps_the_save_and_never_confirms_or_continues(self):
        replay = self.replay(["give-up"])
        self.assertTrue(LOBBY_CONTINUE.match_template(replay.image, similarity=0.9))
        self.assertFalse(LOBBY_CONTINUE.match_template_color(replay.image, similarity=0.9, threshold=20))
        with self.assertRaisesRegex(ObservationError, "give_up_confirm"):
            replay.task.enter()
        self.assertEqual(replay.actions, [])
        replay.task.handle_popup_confirm.assert_not_called()

    def test_mode_change_after_continue_is_not_followed_by_another_entry(self):
        for returned in ("challenge-lobby-empty", "challenge-prepared"):
            with self.subTest(returned=returned):
                replay = self.replay(["lobby-saved-opening", returned])
                with self.assertRaisesRegex(ObservationError, "mode"):
                    replay.task.enter()
                self.assertEqual([item[1] for item in replay.actions], ["LOBBY_CONTINUE"])

    def test_new_entry_cannot_be_confirmed_by_a_later_or_deployed_preparation(self):
        for returned in ("normal-round2", "normal-deployed"):
            with self.subTest(returned=returned):
                replay = self.replay(["normal-lobby-empty", returned])
                with self.assertRaisesRegex(ObservationError, "empty first-round"):
                    replay.task.enter()
                self.assertEqual([item[1] for item in replay.actions], ["LOBBY_ENTER"])

    def test_unknown_transition_times_out_without_repeating_entry(self):
        unknown = np.zeros((720, 1280, 3), np.uint8)
        replay = self.replay(["lobby-saved-opening"] + [unknown] * 45)
        with self.assertRaisesRegex(ObservationError, "verified preparation"):
            replay.task.enter()
        self.assertEqual([item[1] for item in replay.actions], ["LOBBY_CONTINUE"])

    def test_fresh_first_frame_discards_a_stale_lobby_without_clicking(self):
        replay = self.replay(["lobby-saved-opening", "resume-preparation"])
        result = replay.task.enter(skip_first_screenshot=False)
        self.assertTrue(result.resumed)
        self.assertEqual(replay.index, 1)
        self.assertEqual(replay.actions, [])

    def test_absent_season_entry_never_reuses_a_previous_match_coordinate(self):
        source = frame("navigation-season")
        self.assertTrue(BRAWL_ENTRY.match_template(source, similarity=0.9))
        missing = source.copy()
        missing[174:225, 320:1230] = 0
        replay = self.replay([missing] * 25)
        with self.assertRaisesRegex(GamePageUnknownError, "UI route entry not found"):
            replay.task.enter()
        self.assertEqual(replay.actions, [])
        self.assertTrue(all(page.parent is None for page in Page.iter_pages()))

    def test_page_registration_and_forward_return_routes_are_isolated_per_server(self):
        code = """
import module.config.server as server
server.server = {package!r}
server.set_lang({lang!r})
from tasks.base.ui import UI
from tasks.base import page
from tasks.base.page import Page
checks = tuple(Page.iter_check_buttons())
assert len(checks) == len(tuple(Page.iter_pages()))
assert all(check is not None for check in checks)
if {lang!r} == 'global_cn':
    lobby = page.page_brawl_lobby
    assert lobby in Page.all_pages.values()
    assert lobby in page.page_combat_season.links_need_match
    Page.init_connection(lobby)
    assert page.page_combat_season.parent is lobby
    assert page.page_main.parent is page.page_combat_season
    assert str(lobby.links[page.page_combat_season]) == 'BACK'
    Page.init_connection(page.page_combat_season)
    assert lobby.parent is page.page_combat_season
    Page.init_connection(page.page_main)
    current = lobby
    visited = set()
    while current is not page.page_main:
        assert current not in visited
        visited.add(current)
        assert current.parent in current.links
        current = current.parent
else:
    assert not hasattr(page, 'page_brawl_lobby')
    assert not any('brawl' in item.name for item in Page.iter_pages())
    Page.init_connection(page.page_combat_season)
    assert page.page_main.parent is page.page_combat_season
Page.clear_connection()
assert all(item.parent is None for item in Page.iter_pages())
"""
        for package, language in (("CN-Official", "cn"), ("OVERSEA-Play", "global_cn"),
                                  ("OVERSEA-Play", "global_en")):
            with self.subTest(language=language):
                result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c",
                                         code.format(package=package, lang=language)], cwd=ROOT,
                                        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
