"""Screenshot recognition and server registration for Automaton Tower."""

import json
import subprocess
import sys
import unittest

import yaml

from module.config import server

server.set_lang("global_cn")

from module.config.config_manual import ManualConfig
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

    def test_task_is_in_tool_menu_with_independent_scheduler(self):
        task_data = yaml.safe_load(
            (ROOT / "module" / "config" / "argument" / "task.yaml").read_text(encoding="utf-8")
        )
        menu_data = json.loads(
            (ROOT / "module" / "config" / "argument" / "menu.json").read_text(encoding="utf-8")
        )
        self.assertEqual(task_data["Tool"]["tasks"]["AutomatonTower"], ["Scheduler"])
        self.assertNotIn("AutomatonTower", task_data["Monthly"]["tasks"])
        self.assertIn("AutomatonTower", menu_data["Tool"]["tasks"])
        self.assertNotIn("AutomatonTower", menu_data["Monthly"]["tasks"])
        priority = [item.strip() for item in ManualConfig.SCHEDULER_PRIORITY.split(">")]
        self.assertIn("AutomatonTower", priority)

    def test_page_registration_is_limited_to_overseas_simplified_chinese(self):
        code = """
import module.config.server as server
server.server = {package!r}
server.set_lang({lang!r})
from tasks.base import page

registered = 'page_automaton_tower' in page.Page.all_pages
assert registered == ({package!r} == 'OVERSEA-Play' and {lang!r} == 'global_cn')
if registered:
    assert page.page_combat_season.links[page.page_automaton_tower].name == 'AUTOMATON_TOWER_ENTRY'
    assert page.page_automaton_tower.links[page.page_automaton_tower_floor].name == 'AUTOMATON_TOWER_ENTER'
    assert page.page_automaton_tower_floor.links[page.page_automaton_tower_prepare].name == 'AUTOMATON_TOWER_CHOOSE_TEAM'
    page.Page.init_connection(page.page_automaton_tower_prepare)
    assert page.page_combat_season.parent == page.page_automaton_tower
else:
    assert all(
        getattr(marker, 'name', '') != 'AUTOMATON_TOWER_CHECK'
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
