"""Screenshot recognition and server registration for Automaton Tower."""

import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

import yaml

from module.config import server
from module.exception import RequestHumanTakeover

server.set_lang("global_cn")

from tasks.arena.assets.assets_arena import AUTO_FIGHT_EXIST
from tasks.automaton_tower.assets.assets_automaton_tower import (
    AUTOMATON_TOWER_BATTLE_START,
    AUTOMATON_TOWER_CHECK,
    AUTOMATON_TOWER_CHOOSE_TEAM,
    AUTOMATON_TOWER_CLEAR,
    AUTOMATON_TOWER_DEVICE_CONFIRM,
    AUTOMATON_TOWER_DEVICE_DIALOG,
    AUTOMATON_TOWER_ENTRY,
    AUTOMATON_TOWER_FLOOR_CHECK,
    AUTOMATON_TOWER_NEXT_FLOOR,
    AUTOMATON_TOWER_PREPARE_CHECK,
    AUTOMATON_TOWER_SPEED_1X,
    AUTOMATON_TOWER_SPEED_2X,
    OCR_FLOOR_LIST,
    OCR_FLOOR_PREPARE,
)
from tasks.automaton_tower.assets.assets_automaton_tower_entry import TOWER_SEASON_INFO
from tasks.automaton_tower.automaton_tower import AutomatonTower, AutomatonTowerFloorOcr
from tasks.dungeon.assets.assets_dungeon_action import (
    AUTO_COMBAT,
    AUTO_COMBAT_ENEMY_SELECT,
)
from tasks.dungeon.assets.assets_dungeon_state import (
    AUTO_COMBAT_EXIST,
    AUTO_COMBAT_SKILL_OPENED,
    COMBAT_RESULT_CLEAR,
    ENEMY_NUM_EXIST,
)
from tests.support.automaton_tower import (
    BATTLE_PENDING,
    BATTLE_SELECTED,
    CLEAR,
    FLOOR,
    PENDING,
    POST_CLEAR_OFFER,
    PREPARE,
    RESUME_CAPTURES,
    SELECTED,
    TOWER,
    TowerReplay,
    capture,
)
from tests.support.offline import ROOT, ControlledClock, fixture_image

MANIFEST = ROOT / "tests" / "fixtures" / "automaton_tower" / "manifest.json"


class AutomatonTowerCaptureTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_run_captures_missing_first_frame_and_reuses_existing_frame(self):
        for preloaded in (False, True):
            with self.subTest(preloaded=preloaded), ControlledClock() as clock:
                frames = [capture(TOWER)] if preloaded else [capture(), capture(TOWER)]
                replay = TowerReplay(frames, clock)
                task = replay.task
                task.config.config_name = "offline"
                if not preloaded:
                    del replay.image

                with (
                    patch("tasks.automaton_tower.profile_store.ProfileStore.active", return_value=None),
                    patch("tasks.automaton_tower.profile_store.ProfileStore.debug_enabled", return_value=False),
                    patch.object(task, "_enter_floor", return_value=False) as enter_floor,
                    patch.object(task, "_return_to_main", return_value=True),
                ):
                    self.assertFalse(task.run())

                enter_floor.assert_called_once_with()
                task.config.task_delay.assert_not_called()
                self.assertEqual(replay.index, len(frames) - 1)
                self.assertEqual(replay.actions, [])

    def test_resume_screenshots_identify_pending_device(self):
        from tasks.base.assets.assets_base_main_page import WHITE_STAR
        from tasks.base.page import page_combat, page_combat_season

        with ControlledClock() as clock:
            replay = TowerReplay([capture(name) for name in RESUME_CAPTURES], clock)
            for index in range(len(RESUME_CAPTURES)):
                if index:
                    replay.screenshot()
                with self.subTest(fixture=RESUME_CAPTURES[index]):
                    task = replay.task
                    self.assertEqual(task._is_device_dialog(), index in (4, 5))
                    if index == 0:
                        # Android's visible system bars dim the first capture;
                        # it identifies home but cannot authorize navigation.
                        self.assertTrue(WHITE_STAR.match_template_luma(replay.image))
                        self.assertFalse(task.is_in_main())
                    elif index < 3:
                        self.assertTrue(task.ui_page_appear((page_combat, page_combat_season)[index - 1]))
                    elif index == 3:
                        self.assertTrue(task._is_tower_page())
                    elif index in (4, 5):
                        self.assertEqual(
                            AUTOMATON_TOWER_DEVICE_CONFIRM.match_color(replay.image, threshold=30),
                            index == 5,
                        )
                    elif index == 6:
                        self.assertTrue(task._is_floor_page())
                    elif index in (7, 8):
                        self.assertTrue(task._is_prepare_page())
                    else:
                        self.assertFalse(task._is_prepare_page())
            self.assertEqual(replay.actions, [])

    def test_resume_from_combat_selects_device_before_starting_floor(self):
        with ControlledClock() as clock:
            # The supplied home capture is dimmed by Android's system bars;
            # navigation starts on the next, actionable combat-menu capture.
            # CLEAR is explicitly spliced from the earlier capture sequence:
            # the supplied afternoon sequence stops during the next battle.
            frames = [*map(capture, RESUME_CAPTURES[1:]), capture(CLEAR)]
            replay = TowerReplay(frames, clock)
            self.assertTrue(replay.task._enter_tower())
            self.assertEqual(replay.index, 2)
            self.assertTrue(replay.task._enter_floor())
            self.assertEqual(replay.index, 6)
            self.assertEqual(replay.task.current_floor, 4)
            self.assertEqual(replay.task._run_battle(), "clear")
            self.assertEqual(replay.index, 9)
            self.assertEqual(replay.actions, [
                (0, "SEASON_ENTRY"), (1, "AUTOMATON_TOWER_ENTRY"),
                (2, "AUTOMATON_TOWER_ENTER"), (3, "AUTOMATON_TOWER_FIRST_DEVICE"),
                (4, "AUTOMATON_TOWER_DEVICE_CONFIRM"), (5, "AUTOMATON_TOWER_CHOOSE_TEAM"),
                (6, "AUTOMATON_TOWER_BATTLE_START"),
            ])
            first_card = next(area for _, name, area in replay.click_areas if name == "AUTOMATON_TOWER_FIRST_DEVICE")
            self.assertTrue(100 <= first_card[0] <= first_card[2] <= 435)
            self.assertTrue(80 <= first_card[1] <= first_card[3] <= 550)

    def test_resume_retries_dropped_selection_and_confirmation(self):
        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(TOWER), capture(PENDING), capture(PENDING, 0.5), capture(PENDING, 1.6),
                capture(SELECTED, 0.1), capture(SELECTED, 0.5), capture(SELECTED, 1.6),
                capture(None, 0.1), capture(FLOOR, 0.1), capture(PREPARE, 0.1),
            ], clock)
            self.assertTrue(replay.task._enter_floor())
            self.assertEqual(replay.index, 9)
            self.assertEqual(replay.actions, [
                (0, "AUTOMATON_TOWER_ENTER"), (1, "AUTOMATON_TOWER_FIRST_DEVICE"),
                (3, "AUTOMATON_TOWER_FIRST_DEVICE"), (4, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
                (6, "AUTOMATON_TOWER_DEVICE_CONFIRM"), (8, "AUTOMATON_TOWER_CHOOSE_TEAM"),
            ])
            replay.assert_retried(self, "AUTOMATON_TOWER_FIRST_DEVICE", 2)
            replay.assert_retried(self, "AUTOMATON_TOWER_DEVICE_CONFIRM", 2)

    def test_resume_keeps_existing_selection(self):
        with ControlledClock() as clock:
            replay = TowerReplay([capture(SELECTED), capture(FLOOR, 0.1), capture(PREPARE, 0.1)], clock)
            self.assertTrue(replay.task._enter_floor())
            self.assertEqual(replay.index, 2)
            self.assertEqual(replay.actions, [
                (0, "AUTOMATON_TOWER_DEVICE_CONFIRM"), (1, "AUTOMATON_TOWER_CHOOSE_TEAM"),
            ])

    def test_device_unknown_frames_do_not_confirm_completion(self):
        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(PENDING), capture(SELECTED, 0.1), capture(None, 0.1), capture(FLOOR, 0.1),
            ], clock)
            self.assertTrue(replay.task._select_first_device())
            self.assertEqual(replay.index, 3)
            self.assertEqual(replay.actions, [
                (0, "AUTOMATON_TOWER_FIRST_DEVICE"), (1, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
            ])

    def test_device_retries_do_not_extend_deadline(self):
        for fixture in (PENDING, SELECTED):
            with self.subTest(fixture=fixture), ControlledClock() as clock:
                replay = TowerReplay([capture(fixture) for _ in range(42)], clock)
                started = clock.now
                self.assertFalse(replay.task._select_first_device())
                # Production Timer.reached uses a strict '>' comparison;
                # a 0.5-second sampler observes the 20-second limit at 20.5.
                self.assertAlmostEqual(clock.now - started, 20.5)
                name = "AUTOMATON_TOWER_FIRST_DEVICE" if fixture == PENDING else "AUTOMATON_TOWER_DEVICE_CONFIRM"
                self.assertEqual(replay.actions, [(index, name) for index in range(0, 41, 5)])

    def test_battle_device_selection_returns_on_visible_clear(self):
        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(BATTLE_PENDING), capture(BATTLE_SELECTED, 0.1),
                capture(None, 0.1), capture(CLEAR, 0.1),
            ], clock)
            self.assertEqual(replay.task._run_battle(), "clear")
            self.assertEqual(replay.index, 3)
            self.assertEqual(replay.actions, [
                (0, "AUTOMATON_TOWER_FIRST_DEVICE"), (1, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
            ])

    def test_next_floor_handles_device_overlay_before_background_button(self):
        with ControlledClock() as clock:
            # The pending frame is the real blocked floor 14 result. The
            # selected, uncovered and preparation frames are from earlier runs.
            replay = TowerReplay([
                capture(CLEAR), capture(POST_CLEAR_OFFER), capture(POST_CLEAR_OFFER, 0.3),
                capture(BATTLE_SELECTED, 0.1), capture(CLEAR, 0.1), capture(PREPARE, 0.1),
            ], clock)
            self.assertEqual(replay.task._next_floor(), "next")
            self.assertEqual(replay.index, 5)
            self.assertEqual(replay.task.current_floor, 4)
            self.assertEqual(replay.actions, [
                (1, "AUTOMATON_TOWER_FIRST_DEVICE"),
                (3, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
                (4, "AUTOMATON_TOWER_NEXT_FLOOR"),
            ])

    def test_real_screenshots_cover_activity_states(self):
        expected = {
            "2026-11-15_00-20-10-741272": (AUTOMATON_TOWER_ENTRY,),
            "2026-11-15_00-20-15-516532": (AUTOMATON_TOWER_CHECK,),
            "2026-11-15_00-20-19-772479": (
                AUTOMATON_TOWER_FLOOR_CHECK,
                AUTOMATON_TOWER_CHOOSE_TEAM,
            ),
            "2026-11-15_00-20-23-753144": (
                AUTOMATON_TOWER_PREPARE_CHECK,
                AUTOMATON_TOWER_BATTLE_START,
            ),
            "2026-11-15_00-20-31-060817": (AUTO_FIGHT_EXIST,),
            # This repeat battle frame contains a transient animation over the
            # auto icon; keep it in the fixture sequence without requiring the
            # production threshold to accept that single animation frame.
            "2026-11-15_00-20-34-671936": (),
            "2026-11-15_00-21-33-720501": (AUTOMATON_TOWER_DEVICE_DIALOG,),
            "2026-11-15_00-21-36-380571": (AUTOMATON_TOWER_DEVICE_DIALOG,),
            "2026-11-15_00-21-39-738751": (
                AUTOMATON_TOWER_CLEAR,
                COMBAT_RESULT_CLEAR,
                AUTOMATON_TOWER_NEXT_FLOOR,
            ),
            "2026-11-15_00-21-44-601809": (
                AUTOMATON_TOWER_PREPARE_CHECK,
                AUTOMATON_TOWER_BATTLE_START,
            ),
            "2026-11-15_00-21-53-337939": (AUTO_FIGHT_EXIST,),
            "2026-11-15_15-19-29-075489": (AUTOMATON_TOWER_DEVICE_DIALOG,),
            "2026-11-15_15-22-23-137734": (
                AUTO_COMBAT_EXIST,
                ENEMY_NUM_EXIST,
                AUTOMATON_TOWER_SPEED_2X,
            ),
            "2026-11-15_15-22-26-688830": (
                AUTO_COMBAT_EXIST,
                ENEMY_NUM_EXIST,
                AUTOMATON_TOWER_SPEED_1X,
            ),
            "2026-11-15_15-22-31-914685": (
                AUTO_COMBAT_EXIST,
                AUTO_COMBAT_ENEMY_SELECT,
                AUTO_COMBAT_SKILL_OPENED,
            ),
            "2026-11-15_17-13-16-352244": (
                AUTOMATON_TOWER_CHOOSE_TEAM,
            ),
        }
        for fixture_id, buttons in expected.items():
            with self.subTest(fixture_id=fixture_id):
                image = fixture_image(MANIFEST, fixture_id)
                for button in buttons:
                    self.assertTrue(button.match_template(image), button.name)

        pending = fixture_image(MANIFEST, "2026-11-15_15-19-29-075489")
        self.assertTrue(AUTOMATON_TOWER_DEVICE_CONFIRM.match_template(pending))
        self.assertFalse(AUTOMATON_TOWER_DEVICE_CONFIRM.match_color(pending, threshold=30))
        transition = fixture_image(MANIFEST, "2026-11-15_15-22-35-147595")
        for button in (AUTO_COMBAT_EXIST, AUTO_COMBAT_ENEMY_SELECT,
                       AUTO_COMBAT_SKILL_OPENED, ENEMY_NUM_EXIST,
                       AUTOMATON_TOWER_SPEED_1X, AUTOMATON_TOWER_SPEED_2X):
            self.assertFalse(button.match_template(transition), button.name)

        # The floor label is dynamic and becomes two digits on this page, so
        # the old single-digit label template is not a reliable page identity.
        # The fixed choose-team control remains a positive floor-list marker.
        double_digit = fixture_image(MANIFEST, "2026-11-15_17-13-16-352244")
        self.assertFalse(AUTOMATON_TOWER_FLOOR_CHECK.match_template(double_digit))
        task = object.__new__(AutomatonTower)
        task.appear = lambda button, interval=0: button.match_template(double_digit)
        self.assertTrue(task._is_floor_page())

    def test_device_confirmation_requires_bright_green_button(self):
        pending = fixture_image(MANIFEST, "2026-11-15_00-21-33-720501")
        selected = fixture_image(MANIFEST, "2026-11-15_00-21-36-380571")
        self.assertTrue(AUTOMATON_TOWER_DEVICE_CONFIRM.match_template(pending))
        self.assertFalse(AUTOMATON_TOWER_DEVICE_CONFIRM.match_color(pending, threshold=30))
        self.assertTrue(AUTOMATON_TOWER_DEVICE_CONFIRM.match_template(selected))
        self.assertTrue(AUTOMATON_TOWER_DEVICE_CONFIRM.match_color(selected, threshold=30))

    def test_floor_ocr_reads_list_and_prepare_labels(self):
        cases = (
            ("2026-11-15_00-20-19-772479", OCR_FLOOR_LIST, 1),
            ("2026-11-15_14-43-24-984899", OCR_FLOOR_LIST, 4),
            ("2026-11-15_00-20-23-753144", OCR_FLOOR_PREPARE, 1),
            ("2026-11-15_14-43-32-175894", OCR_FLOOR_PREPARE, 4),
            ("2026-11-15_17-13-16-352244", OCR_FLOOR_LIST, 10),
        )
        for fixture_id, button, expected in cases:
            with self.subTest(fixture_id=fixture_id):
                image = fixture_image(MANIFEST, fixture_id)
                self.assertEqual(
                    AutomatonTowerFloorOcr(button, lang="cn", name="TowerFloor").ocr_single_line(image),
                    expected,
                )

    def test_battle_replay_starts_once_and_keeps_existing_auto_mode(self):
        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(PREPARE),
                capture("2026-11-15_14-43-39-909021", 0.5),
                capture(CLEAR, 0.5),
            ], clock)
            self.assertEqual(replay.task._run_battle(), "clear")
            self.assertEqual(replay.actions, [(0, "AUTOMATON_TOWER_BATTLE_START")])
            self.assertEqual(replay.index, 2)

    def test_battle_replay_enables_dungeon_auto_combat_and_resets_stuck_guard(self):
        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(PREPARE),
                capture("2026-11-15_15-22-23-137734", 0.5),
                capture("2026-11-15_15-22-23-137734", 0.5),
                capture("2026-11-15_15-22-23-137734", 1.6),
                capture("2026-11-15_14-43-39-909021", 0.5),
                capture("2026-11-15_15-22-35-147595", 0.5),
                capture(CLEAR, 0.5),
            ], clock)
            self.assertEqual(replay.task._run_battle(), "clear")
            self.assertEqual(replay.actions, [
                (0, "AUTOMATON_TOWER_BATTLE_START"),
                (1, AUTO_COMBAT.name),
                (3, AUTO_COMBAT.name),
            ])
            self.assertEqual(replay.index, 6)

    def test_battle_replay_toggles_speed_only_from_positive_one_x_state(self):
        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(PREPARE),
                capture("2026-11-15_15-22-31-914685", 0.5),
                capture("2026-11-15_15-22-31-914685", 0.5),
                capture("2026-11-15_15-22-31-914685", 1.6),
                capture("2026-11-15_14-43-39-909021", 0.5),
                capture("2026-11-15_15-22-35-147595", 0.5),
                capture(CLEAR, 0.5),
            ], clock)
            self.assertEqual(replay.task._run_battle(), "clear")
            self.assertEqual(replay.actions, [
                (0, "AUTOMATON_TOWER_BATTLE_START"),
                (1, AUTOMATON_TOWER_SPEED_1X.name),
                (3, AUTOMATON_TOWER_SPEED_1X.name),
            ])
            self.assertEqual(replay.index, 6)

        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(PREPARE),
                capture("2026-11-15_14-43-39-909021", 0.5),
                capture("2026-11-15_15-22-35-147595", 0.5),
                capture(CLEAR, 0.5),
            ], clock)
            self.assertEqual(replay.task._run_battle(), "clear")
            self.assertEqual(replay.actions, [(0, "AUTOMATON_TOWER_BATTLE_START")])
            self.assertEqual(replay.index, 3)

    def test_battle_replay_survives_long_hud_then_resolves_pending_device(self):
        # Keep the production stuck guard active while replaying more than one
        # minute of real battle HUD.  The pending device and selected-device
        # captures are deliberately spliced from separate runs; only their
        # recognition and the resulting actions are under test.
        active_hud = [capture("2026-11-15_14-43-39-909021", 1.0) for _ in range(61)]
        with ControlledClock() as clock:
            started = clock.now
            replay = TowerReplay([
                capture(PREPARE),
                *active_hud,
                capture(BATTLE_PENDING, 0.5),
                capture(BATTLE_SELECTED, 0.5),
                capture(CLEAR, 0.5),
            ], clock)
            self.assertEqual(replay.task._run_battle(), "clear")
            self.assertGreater(clock.now - started, 60.0)
            self.assertEqual(replay.actions, [
                (0, "AUTOMATON_TOWER_BATTLE_START"),
                (62, "AUTOMATON_TOWER_FIRST_DEVICE"),
                (63, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
            ])
            self.assertEqual(replay.index, 64)

    def test_manual_tools_keep_entry_without_scheduler_settings(self):
        from types import SimpleNamespace

        from module.webui.submodule.utils import get_tool_runner
        from tasks.automaton_tower.tool import run_tool

        with (patch('module.webui.config.DeployConfig.read'),
              patch('module.webui.patch.patch_executor'),
              patch('module.webui.patch.patch_mimetype')):
            from module.webui.app import AlasGUI

        task_data = yaml.safe_load(
            (ROOT / "module" / "config" / "argument" / "task.yaml").read_text(encoding="utf-8")
        )
        gui = SimpleNamespace(alas_mod='alas', _init_alas_config_watcher=lambda: None)
        AlasGUI.initial(gui)
        self.assertIn("AutomatonTower", task_data["Tool"]["tasks"])
        self.assertNotIn("AutomatonTower", task_data["Monthly"]["tasks"])
        for task in ('AutomatonTower', 'BuyPenguins', 'CommunityAuth', 'DimensionalExploration',
                     'Episode', 'EquipmentReroll'):
            with self.subTest(task=task):
                self.assertIn(task, gui.ALAS_MENU['Tool']['tasks'])
                self.assertNotIn('Scheduler', gui.ALAS_ARGS[task])
        self.assertIn('BuyPenguins', gui.ALAS_ARGS['BuyPenguins'])
        self.assertIn('Enable', gui.ALAS_ARGS['Arena']['Scheduler'])
        self.assertIn('NextRun', gui.ALAS_ARGS['SanctuaryMonthly']['Scheduler'])
        self.assertIs(get_tool_runner("AutomatonTower"), run_tool)

    def test_season_popup_blocks_navigation_until_close_is_observed(self):
        from tasks.base.page import page_automaton_tower

        with ControlledClock() as clock:
            replay = TowerReplay([
                capture("e7-tower-popup"), capture("e7-tower-popup", 0.1),
                capture("e7-tower-popup", 2.1), capture(None, 0.1), capture(TOWER, 0.1),
            ], clock)
            self.assertFalse(replay.task._is_tower_page())
            self.assertFalse(replay.task._is_floor_page())
            self.assertFalse(replay.task.ui_page_appear(page_automaton_tower))
            replay.task.ui_goto(page_automaton_tower)
            self.assertEqual(replay.index, 4)
            self.assertEqual(replay.actions, [(0, "TOWER_SEASON_INFO"), (2, "TOWER_SEASON_INFO")])
            replay.assert_retried(self, "TOWER_SEASON_INFO", 2)
            for _, _, area in replay.click_areas:
                self.assertTrue(580 <= area[0] < area[2] <= 710)
                self.assertTrue(680 <= area[1] < area[3] <= 720)

    def test_season_popup_has_positive_identity_and_real_negative_scenes(self):
        self.assertTrue(TOWER_SEASON_INFO.match_template(fixture_image(MANIFEST, "e7-tower-popup")))
        for fixture in (TOWER, FLOOR, PREPARE, PENDING, CLEAR):
            with self.subTest(fixture=fixture), ControlledClock() as clock:
                replay = TowerReplay([capture(fixture)], clock)
                self.assertFalse(replay.task._is_entry_overlay())
                self.assertFalse(replay.task._handle_tower_entry())
                self.assertEqual(replay.actions, [])

    def test_page_registration_is_limited_to_overseas_simplified_chinese(self):
        code = """
import module.config.server as server
server.server = {package!r}
server.set_lang({lang!r})
from tasks.base import page

registered = 'page_automaton_tower' in page.Page.all_pages
assert registered == ({lang!r} in ('cn', 'global_cn'))
from tasks.automaton_tower.automaton_tower import AutomatonTower
task = object.__new__(AutomatonTower)
assert task._unsupported() == (not registered)
if registered:
    assert page.page_combat_season.links[page.page_automaton_tower].name == 'AUTOMATON_TOWER_ENTRY'
    assert page.page_automaton_tower.links[page.page_automaton_tower_floor].name == 'AUTOMATON_TOWER_ENTER'
    assert page.page_automaton_tower_floor.links[page.page_automaton_tower_prepare].name == 'AUTOMATON_TOWER_CHOOSE_TEAM'
    page.Page.init_connection(page.page_automaton_tower_prepare)
    assert page.page_combat_season.parent == page.page_automaton_tower
    assert page.page_automaton_tower.check_button.name == 'AUTOMATON_TOWER_LANDING_CHECK'
    assert page.page_automaton_tower_floor.check_button.name == 'AUTOMATON_TOWER_CHOOSE_TEAM'
    assert all(marker.buttons for marker in page.Page.iter_check_buttons() if hasattr(marker, 'buttons'))
else:
    assert all(
        not getattr(marker, 'name', '').startswith('AUTOMATON_TOWER_')
        for check in page.Page.iter_check_buttons()
        for marker in (check if isinstance(check, tuple) else (check,))
    )
page.Page.clear_connection()
"""
        for package, lang in (
            ("CN-Official", "cn"),
            ("OVERSEA-Play", "global_cn"),
            ("OVERSEA-Play", "global_en"),
        ):
            with self.subTest(package=package, lang=lang):
                result = subprocess.run(
                    [sys.executable, "-B", "-X", "utf8", "-c", code.format(package=package, lang=lang)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=45,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class AutomatonTowerCnTests(unittest.TestCase):
    """Initialize CN before imports so page registration stays process-local."""

    def run_cn(self, code):
        prefix = """
import unittest
import module.config.server as server
server.server = 'CN-Official'
server.set_lang('cn')
from tests.support.automaton_tower import MANIFEST, TowerReplay, capture
from tests.support.offline import ROOT, ControlledClock, fixture_image
from tasks.automaton_tower.automaton_tower import AutomatonTower, AutomatonTowerFloorOcr
from tasks.automaton_tower.assets.assets_automaton_tower import (
    AUTOMATON_TOWER_ENTER, AUTOMATON_TOWER_SPEED_1X, AUTOMATON_TOWER_SPEED_2X,
    OCR_FLOOR_LIST, OCR_FLOOR_PREPARE,
)
from tasks.base.page import page_automaton_tower
case = unittest.TestCase()
def cn(suffix, seconds=0.5):
    return capture('20261115-' + suffix, seconds)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "-c", textwrap.dedent(prefix) + textwrap.dedent(code)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cn_navigation_closes_season_modal_before_arrival(self):
        self.run_cn("""
with ControlledClock() as clock:
    replay = TowerReplay([
        cn('221941-499'), cn('221944-879'), cn('221944-879', 0.1),
        cn('221944-879', 2.1), capture(None, 0.1), cn('221946-748', 0.1),
    ], clock)
    replay.task.config.Emulator_PackageName = 'com.zlongame.cn.epicseven'
    replay.task.ui_goto(page_automaton_tower)
    case.assertEqual(replay.index, 5)
    case.assertEqual(replay.actions, [(0, 'AUTOMATON_TOWER_ENTRY'), (1, 'TOWER_SEASON_INFO'), (3, 'TOWER_SEASON_INFO')])
    replay.assert_retried(case, 'TOWER_SEASON_INFO', 2)
with ControlledClock() as clock:
    # An already started season can restore its floor list directly. The
    # production entry must accept it without navigating BACK to difficulty.
    replay = TowerReplay([cn('221941-499'), cn('222909-462'), cn('222903-191')], clock)
    case.assertTrue(replay.task._enter_tower())
    case.assertEqual(replay.index, 1)
    case.assertTrue(replay.task._enter_floor())
    case.assertEqual(replay.task.current_floor, 2)
    case.assertEqual(replay.actions, [(0, 'AUTOMATON_TOWER_ENTRY'), (1, 'AUTOMATON_TOWER_CHOOSE_TEAM')])
""")

    def test_cn_stage_entry_retries_confirm_and_preserves_current_difficulty(self):
        self.run_cn("""
with ControlledClock() as clock:
    replay = TowerReplay([
        cn('221946-748'), cn('222002-635', 0.1), cn('222002-635', 0.1),
        cn('222002-635', 2.1), capture(None, 0.1), cn('222005-252', 0.1), cn('222014-084', 0.1),
    ], clock)
    case.assertTrue(replay.task._enter_floor())
    case.assertEqual(replay.task.current_floor, 1)
    case.assertEqual(replay.actions, [
        (0, 'AUTOMATON_TOWER_ENTER'), (1, 'TOWER_STAGE_CONFIRM'),
        (3, 'TOWER_STAGE_CONFIRM'), (5, 'AUTOMATON_TOWER_CHOOSE_TEAM'),
    ])
    replay.assert_retried(case, 'TOWER_STAGE_CONFIRM', 2)
    for index, name, area in replay.click_areas:
        if name == 'TOWER_STAGE_CONFIRM':
            case.assertTrue(649 <= area[0] < area[2] <= 848)
            case.assertTrue(465 <= area[1] < area[3] <= 526)
for suffix in ('221946-748', '221950-315', '221952-911', '222000-640'):
    image = fixture_image(MANIFEST, '20261115-' + suffix)
    case.assertTrue(AUTOMATON_TOWER_ENTER.match_template_color(image, threshold=30))
for suffix in ('221954-929', '221956-184'):
    image = fixture_image(MANIFEST, '20261115-' + suffix)
    case.assertFalse(AUTOMATON_TOWER_ENTER.match_template_color(image, threshold=30))
""")

    def test_cn_pending_modal_never_uses_generic_confirm_or_background_controls(self):
        self.run_cn("""
from tasks.automaton_tower.entry import AutomatonTowerEntry
for suffix in ('221944-879', '222002-635'):
    with ControlledClock() as clock:
        replay = TowerReplay([cn(suffix), cn(suffix, 0.1)], clock)
        task = replay.task
        case.assertTrue(task._is_entry_overlay())
        case.assertFalse(task._is_tower_page())
        case.assertFalse(task._is_floor_page())
        case.assertFalse(task.ui_page_appear(page_automaton_tower))
        case.assertTrue(task._handle_tower_entry())
        replay.screenshot()
        case.assertFalse(task._handle_tower_entry())
        # Bind production methods: TowerReplay stubs unrelated generic popups.
        case.assertFalse(AutomatonTowerEntry.handle_popup_confirm(task))
        case.assertFalse(AutomatonTowerEntry.handle_popup_cancel(task))
        case.assertEqual(len(replay.actions), 1)
""")

    def test_cn_captures_reject_unrelated_popups_and_read_actual_floors(self):
        self.run_cn("""
for suffix in ('221936-261', '221939-032', '221940-408', '221941-499', '222007-993', '222009-804'):
    with ControlledClock() as clock:
        replay = TowerReplay([cn(suffix)], clock)
        case.assertFalse(replay.task._is_entry_overlay())
        case.assertFalse(replay.task._handle_tower_entry())
        case.assertEqual(replay.actions, [])
for suffix, button, expected in (
    ('222005-252', OCR_FLOOR_LIST, 1), ('222011-239', OCR_FLOOR_LIST, 1),
    ('222909-462', OCR_FLOOR_LIST, 2), ('222014-084', OCR_FLOOR_PREPARE, 1),
    ('222903-191', OCR_FLOOR_PREPARE, 2),
):
    image = fixture_image(MANIFEST, '20261115-' + suffix)
    case.assertEqual(AutomatonTowerFloorOcr(button, lang='cn', name='CnTowerFloor').ocr_single_line(image), expected)
""")

    def test_cn_battle_recovers_speed_and_auto_then_selects_and_advances(self):
        self.run_cn("""
from tasks.dungeon.execute import detect_auto_combat_state
with ControlledClock() as clock:
    replay = TowerReplay([
        cn('222014-084'), cn('222029-545'), cn('222029-545', 0.1),
        cn('222023-418', 0.1), cn('222031-986', 2.2), capture(None, 0.1),
        cn('222856-893', 0.1), cn('222858-926', 0.1), cn('222900-237', 0.1),
        cn('222900-237', 0.1), cn('222903-191', 0.1),
    ], clock)
    case.assertEqual(replay.task._run_battle(), 'clear')
    case.assertEqual(replay.index, 8)
    case.assertEqual(replay.task._next_floor(), 'next')
    case.assertEqual(replay.task.current_floor, 2)
    case.assertEqual(replay.actions, [
        (0, 'AUTOMATON_TOWER_BATTLE_START'), (1, 'AUTOMATON_TOWER_SPEED_1X'),
        (2, 'AUTO_COMBAT'), (4, 'AUTO_COMBAT'), (6, 'AUTOMATON_TOWER_FIRST_DEVICE'),
        (7, 'AUTOMATON_TOWER_DEVICE_CONFIRM'), (9, 'AUTOMATON_TOWER_NEXT_FLOOR'),
    ])
for suffix, speed, auto in (
    ('222020-688', AUTOMATON_TOWER_SPEED_2X, None),
    ('222023-418', AUTOMATON_TOWER_SPEED_1X, True),
    ('222029-545', AUTOMATON_TOWER_SPEED_1X, False),
    ('222031-986', AUTOMATON_TOWER_SPEED_2X, False),
    ('222033-491', AUTOMATON_TOWER_SPEED_2X, False),
):
    with ControlledClock() as clock:
        replay = TowerReplay([cn(suffix)], clock)
        case.assertTrue(speed.match_template_color(replay.image, threshold=30))
        case.assertIs(detect_auto_combat_state(replay.task.appear), auto)
""")

    def test_cn_profile_reads_complete_offer_and_requires_selected_target(self):
        self.run_cn("""
import yaml
from tasks.automaton_tower.device_selection import DeviceReader, selection_button
from tasks.automaton_tower.device_record import DeviceRecord
from tasks.automaton_tower.policy import parse_profile
from uuid import uuid4
root = ROOT / '.cache/automaton_tower_tests' / uuid4().hex
reader = DeviceReader('cn')
for suffix, selected in (('222856-893', None), ('222858-926', 1)):
    image = fixture_image(MANIFEST, '20261115-' + suffix)
    candidates = reader.read(image)
    case.assertEqual([item.device.id for item in candidates], ['back_critical_damage', 'front_counter', 'multiple_target'])
    case.assertEqual((candidates[1].level, candidates[1].max_level), (1, 3))
    case.assertEqual(reader.selected_slot(image), selected)
for slot in range(3):
    area = selection_button(slot).button
    case.assertTrue(100 + slot * 370 <= area[0] < area[2] <= 435 + slot * 370)
with ControlledClock() as clock:
    replay = TowerReplay([
        cn('222856-893'), cn('222856-893', 0.1), cn('222856-893', 2.1),
        cn('222858-926', 0.1), cn('222858-926', 0.1), cn('222858-926', 2.1),
        capture(None, 0.1), cn('222900-237', 0.1),
    ], clock)
    replay.task.device_policy = parse_profile(yaml.safe_dump({
        '版本': 1, '方案名': '国服回放', '队伍': {'主输出位置': '前排'},
        '装置优先顺序': ['前排产生反抗1级装置'],
    }, allow_unicode=True))
    replay.task.device_record = DeviceRecord('offline-cn', 'cn', '国服回放', root=root)
    case.assertTrue(replay.task._select_first_device())
    case.assertEqual(replay.actions, [(0, 'DEVICE_MIDDLE_SELECTED'), (2, 'DEVICE_MIDDLE_SELECTED'), (3, 'AUTOMATON_TOWER_DEVICE_CONFIRM'), (5, 'AUTOMATON_TOWER_DEVICE_CONFIRM')])
    case.assertEqual(replay.index, 7)
""")

    def test_cn_debug_saves_full_offer_and_preconfirm_without_extra_ocr(self):
        self.run_cn("""
import json
import numpy as np
from PIL import Image
from uuid import uuid4
from tasks.automaton_tower.device_debug import DeviceDebug
root = ROOT / '.cache/automaton_tower_tests' / uuid4().hex
with ControlledClock() as clock:
    replay = TowerReplay([
        cn('222858-926'), cn('222858-926', 0.1), cn('222858-926', 2.1), cn('222900-237', 0.1),
    ], clock)
    replay.task.device_debug = DeviceDebug('offline-cn', 'cn', root=root)
    replay.task.current_floor = 1
    case.assertTrue(replay.task._select_first_device())
    folder = replay.task.device_debug.path / '001'
    record = json.loads((folder / 'record.json').read_text(encoding='utf-8'))
    case.assertEqual(record['server'], 'cn')
    case.assertEqual(record['floor'], 1)
    case.assertEqual(record['confirmation_requests'], 2)
    case.assertEqual(record['status'], 'confirmed')
    expected = fixture_image(MANIFEST, '20261115-222858-926')
    for filename in record['images'].values():
        case.assertTrue(np.array_equal(np.array(Image.open(folder / filename).convert('RGB')), expected))
    case.assertEqual(len(record['images']), 2)
    case.assertEqual(replay.actions, [(0, 'AUTOMATON_TOWER_DEVICE_CONFIRM'), (2, 'AUTOMATON_TOWER_DEVICE_CONFIRM')])
""")
