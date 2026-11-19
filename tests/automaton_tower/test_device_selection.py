"""Real card names and halos, plus retries with independent screenshot frames."""

import json
import unittest
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.config import server

server.set_lang("global_cn")

from module.exception import RequestHumanTakeover
from tasks.automaton_tower.device_debug import DeviceDebug
from tasks.automaton_tower.device_record import DeviceRecord
from tasks.automaton_tower.device_selection import DeviceReader
from tasks.automaton_tower.policy import DecisionUnavailable, parse_profile
from tests.automaton_tower.test_device_profiles import profile_text, workspace
from tests.support.automaton_tower import (
    FLOOR,
    MANIFEST,
    PENDING,
    POST_CLEAR_OFFER,
    PREPARE,
    SELECTED,
    TowerReplay,
    capture,
)
from tests.support.offline import ControlledClock, fixture_image

MIDDLE = "2026-11-15_18-05-42-922214"
LEFT = "2026-11-15_18-05-46-907553"
RIGHT = "2026-11-15_18-05-52-680786"
LEVEL_TWO = "2026-11-15_18-10-02-444204"
ELEMENTS = "2026-11-15_18-11-36-127159"
CLASSES = "2026-11-15_18-18-38-564782"
THIEF_SELECTED = "2026-11-15_18-18-48-486917"


class TowerDeviceRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_offers_read_multiline_attribute_and_profession_names(self):
        reader = DeviceReader("cn")
        expected = {
            MIDDLE: (
                "front_counter",
                "unknown_adventurer_flidica",
                "element_advantage",
            ),
            LEFT: ("front_counter", "unknown_adventurer_flidica", "element_advantage"),
            RIGHT: ("front_counter", "unknown_adventurer_flidica", "element_advantage"),
            LEVEL_TWO: ("critical_chance", "back_critical_damage", "single_attack"),
            ELEMENTS: ("light_dark_critical", "earth_critical", "fire_critical"),
            CLASSES: ("thief_basic_repeat", "warrior_attack", "soul_weaver_action"),
            THIEF_SELECTED: (
                "thief_basic_repeat",
                "warrior_attack",
                "soul_weaver_action",
            ),
        }
        for name, ids in expected.items():
            with self.subTest(name=name):
                candidates = reader.read(fixture_image(MANIFEST, name))
                self.assertEqual(
                    tuple(candidate.device.id for candidate in candidates), ids
                )
                if name in (LEFT, MIDDLE, RIGHT):
                    self.assertEqual(
                        (candidates[0].level, candidates[0].max_level), (1, 3)
                    )
                if name == LEVEL_TWO:
                    self.assertEqual(
                        (candidates[2].level, candidates[2].max_level), (2, 3)
                    )

    def test_post_clear_offer_reads_all_names_and_max_level(self):
        candidates = DeviceReader("cn").read(fixture_image(MANIFEST, POST_CLEAR_OFFER))
        self.assertEqual(
            tuple((candidate.device.name, candidate.level, candidate.max_level) for candidate in candidates),
            (
                ("首次回合产生特殊钉2级装置", 2, 2),
                ("基本技能发动装置", None, None),
                ("效果命中强化装置", None, None),
            ),
        )

    def test_selected_slot_is_unique_and_not_inferred_from_green_confirm(self):
        for name, slot in (
            (MIDDLE, 1),
            (LEFT, 0),
            (RIGHT, 2),
            (THIEF_SELECTED, 0),
            (CLASSES, None),
            (ELEMENTS, None),
            (LEVEL_TWO, None),
            (FLOOR, None),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    DeviceReader.selected_slot(fixture_image(MANIFEST, name)), slot
                )

    def test_truncated_name_and_mismatched_level_cannot_authorize_choice(self):
        image = fixture_image(MANIFEST, MIDDLE)
        truncated = SimpleNamespace(
            ocr_text="未知开拓者佛里蒂丝强化装", score=0.99, box=(510, 232, 769, 255)
        )
        reader = DeviceReader("cn")
        with (
            patch(
                "tasks.automaton_tower.device_selection.Ocr.detect_and_ocr",
                return_value=[truncated],
            ),
            self.assertRaises(DecisionUnavailable),
        ):
            reader.read(image)
        reader = DeviceReader("cn")
        reader.read(image)
        # Cached complete names must not authorize an independently wrong level.
        with (
            patch(
                "tasks.automaton_tower.device_selection.Ocr.ocr_single_line",
                return_value="Lv.2/3",
            ),
            self.assertRaises(DecisionUnavailable),
        ):
            reader.read(image)


class TowerDeviceReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def prepare(self, frames, clock, target="前排产生反抗1级装置"):
        replay = TowerReplay(frames, clock)
        replay.task.device_policy = parse_profile(
            profile_text(
                队伍={
                    "主输出职业": "盗贼",
                    "主输出属性": "火焰",
                    "主输出位置": "前排",
                    "英雄": [{"姓名": "未知开拓者佛里蒂丝"}],
                },
                装置优先顺序=[target],
            )
        )
        replay.task.device_record = DeviceRecord(
            "offline", "global_cn", "测试方案", workspace()
        )
        replay.task.current_floor = 10
        replay.save_screenshot = Mock()
        return replay

    def test_reselects_target_before_confirm_and_retries_only_due_actions(self):
        with ControlledClock() as clock:
            replay = self.prepare(
                [
                    capture(MIDDLE),
                    capture(MIDDLE, 0.5),
                    capture(MIDDLE, 1.6),
                    capture(LEFT, 0.1),
                    capture(LEFT, 0.5),
                    capture(LEFT, 1.6),
                    capture(None, 0.1),
                    capture(FLOOR, 0.1),
                ],
                clock,
            )
            self.assertTrue(replay.task._select_first_device())
            self.assertEqual(
                replay.actions,
                [
                    (0, "DEVICE_LEFT_SELECTED"),
                    (2, "DEVICE_LEFT_SELECTED"),
                    (3, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
                    (5, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
                ],
            )
            replay.assert_retried(self, "DEVICE_LEFT_SELECTED", 2)
            replay.assert_retried(self, "AUTOMATON_TOWER_DEVICE_CONFIRM", 2)
            record = json.loads(
                replay.task.device_record.path.read_text(encoding="utf-8")
            )
            self.assertEqual(len(record["choices"]), 1)
            self.assertEqual(record["choices"][0]["status"], "confirmed")
            self.assertEqual(record["choices"][0]["floor"], 10)

    def test_can_select_middle_or_right_from_another_existing_selection(self):
        for target, selected, button in (
            ("未知开拓者佛里蒂丝强化装置", MIDDLE, "DEVICE_MIDDLE_SELECTED"),
            ("属性相克强化装置", RIGHT, "DEVICE_RIGHT_SELECTED"),
        ):
            with self.subTest(target=target), ControlledClock() as clock:
                replay = self.prepare(
                    [capture(LEFT), capture(selected, 0.1), capture(PREPARE, 0.1)],
                    clock,
                    target,
                )
                self.assertTrue(replay.task._select_first_device())
                self.assertEqual(
                    replay.actions, [(0, button), (1, "AUTOMATON_TOWER_DEVICE_CONFIRM")]
                )

    def test_existing_target_confirms_without_reselecting_and_waits_for_result(self):
        with ControlledClock() as clock:
            replay = self.prepare(
                [capture(LEFT), capture(None, 0.1), capture(FLOOR, 0.1)], clock
            )
            self.assertTrue(replay.task._select_first_device())
            self.assertEqual(replay.actions, [(0, "AUTOMATON_TOWER_DEVICE_CONFIRM")])
            self.assertEqual(replay.index, 2)
            self.assertEqual(
                replay.task.device_record.data["choices"][0]["destination"],
                "floor_list",
            )

    def test_pending_offer_timeout_preserves_unconfirmed_record_and_screenshot(self):
        with ControlledClock() as clock:
            replay = self.prepare(
                [
                    capture(LEVEL_TWO),
                    capture(LEVEL_TWO, 8),
                    capture(LEVEL_TWO, 8),
                    capture(LEVEL_TWO, 9.1),
                ],
                clock,
                "暴击率强化装置",
            )
            with self.assertRaises(RequestHumanTakeover):
                replay.task._select_first_device()
            self.assertEqual(
                replay.actions,
                [
                    (0, "DEVICE_LEFT_SELECTED"),
                    (1, "DEVICE_LEFT_SELECTED"),
                    (2, "DEVICE_LEFT_SELECTED"),
                ],
            )
            self.assertEqual(
                replay.task.device_record.data["choices"][0]["status"],
                "selection_pending",
            )
            replay.save_screenshot.assert_called_once()

    def test_incomplete_reads_retry_then_stop_without_selecting_or_confirming(self):
        with ControlledClock() as clock:
            replay = self.prepare(
                [capture(MIDDLE), capture(MIDDLE, 1.1), capture(MIDDLE, 1.1)], clock
            )
            with (
                patch.object(
                    DeviceReader, "read", side_effect=DecisionUnavailable("未登记装置")
                ) as read,
                self.assertRaises(RequestHumanTakeover),
            ):
                replay.task._select_first_device()
            self.assertEqual(read.call_count, 3)
            self.assertEqual(replay.actions, [])
            self.assertEqual(replay.task.device_record.data["choices"], [])

    def test_next_complete_offer_confirms_previous_round_without_guessing_second_result(
        self,
    ):
        # Distinct captures are deliberately spliced to test the boundary, not
        # presented as footage of the same live game run.
        with ControlledClock() as clock:
            replay = self.prepare(
                [capture(LEFT), capture(LEVEL_TWO, 0.1), capture(LEVEL_TWO, 26)], clock
            )
            with self.assertRaises(RequestHumanTakeover):
                replay.task._select_first_device()
            entries = replay.task.device_record.data["choices"]
            self.assertEqual(
                [entry["status"] for entry in entries],
                ["confirmed", "selection_pending"],
            )
            self.assertEqual(entries[0]["destination"], "next_device_offer")
            self.assertEqual(
                replay.actions,
                [(0, "AUTOMATON_TOWER_DEVICE_CONFIRM"), (1, "DEVICE_LEFT_SELECTED")],
            )

    def test_human_takeover_keeps_current_screen_instead_of_navigating_back(self):
        with ControlledClock() as clock:
            replay = self.prepare([capture(LEFT)], clock)
            task = replay.task
            task.config.config_name = "offline"
            task._ensure_app_running = Mock()
            task._enter_tower = Mock(return_value=True)
            task._enter_floor = Mock(side_effect=RequestHumanTakeover("缺图"))
            task._return_to_main = Mock()
            with (
                patch(
                    "tasks.automaton_tower.profile_store.ProfileStore.active",
                    return_value=task.device_policy,
                ),
                patch(
                    "tasks.automaton_tower.profile_store.ProfileStore.debug_enabled",
                    return_value=False,
                ),
                self.assertRaises(RequestHumanTakeover),
            ):
                task.run()
            task._return_to_main.assert_not_called()
            self.assertEqual(replay.actions, [])

    def test_debug_profile_records_choice_and_preconfirm_frame(self):
        with ControlledClock() as clock:
            replay = self.prepare(
                [capture(MIDDLE), capture(LEFT, 0.1), capture(FLOOR, 0.1)], clock
            )
            debug = DeviceDebug("offline", "global_cn", workspace())
            replay.task.device_debug = debug
            self.assertTrue(replay.task._select_first_device())
            self.assertEqual(
                replay.actions,
                [(0, "DEVICE_LEFT_SELECTED"), (1, "AUTOMATON_TOWER_DEVICE_CONFIRM")],
            )
            entry = json.loads(
                (debug.path / "001/record.json").read_text(encoding="utf-8")
            )
            self.assertEqual(entry["chosen_slot"], 0)
            self.assertEqual(entry["mode"], "profile")
            self.assertEqual(entry["status"], "confirmed")
            self.assertEqual(len(entry["candidates"]), 3)
            self.assertEqual(entry["candidates"][0]["id"], "front_counter")
            self.assertEqual(len(list(debug.path.rglob("*.png"))), 2)

    def test_debug_unknown_offer_is_saved_before_recognition_stops(self):
        with ControlledClock() as clock:
            replay = self.prepare(
                [capture(MIDDLE), capture(MIDDLE, 1.1), capture(MIDDLE, 1.1)], clock
            )
            debug = DeviceDebug("offline", "global_cn", workspace())
            replay.task.device_debug = debug
            with (
                patch.object(
                    DeviceReader, "read", side_effect=DecisionUnavailable("未登记装置")
                ),
                self.assertRaises(RequestHumanTakeover),
            ):
                replay.task._select_first_device()
            entry = json.loads(
                (debug.path / "001/record.json").read_text(encoding="utf-8")
            )
            self.assertEqual(entry["status"], "interrupted")
            self.assertEqual(entry["error"], "未登记装置")
            self.assertIsNone(entry["candidates"])
            self.assertEqual(entry["images"], {"offer": "offer.png"})
            self.assertEqual(replay.actions, [])

    def test_debug_next_complete_offer_uses_a_separate_capture_directory(self):
        with ControlledClock() as clock:
            replay = self.prepare(
                [capture(LEFT), capture(LEVEL_TWO, 0.1), capture(LEVEL_TWO, 26)], clock
            )
            debug = DeviceDebug("offline", "global_cn", workspace())
            replay.task.device_debug = debug
            with self.assertRaises(RequestHumanTakeover):
                replay.task._select_first_device()
            entries = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(debug.path.glob("*/record.json"))
            ]
            self.assertEqual(
                [e["status"] for e in entries], ["confirmed", "interrupted"]
            )
            self.assertEqual(entries[0]["destination"], "next_device_offer")
            self.assertEqual(entries[1]["images"], {"offer": "offer.png"})
            self.assertEqual(len(list(debug.path.rglob("*.png"))), 3)


class TowerLegacyDebugTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_legacy_capture_does_not_require_ocr_and_deduplicates_retries(self):
        with ControlledClock() as clock:
            replay = TowerReplay(
                [
                    capture(PENDING),
                    capture(PENDING, 0.5),
                    capture(SELECTED, 0.1),
                    capture(SELECTED, 0.5),
                    capture(SELECTED, 1.6),
                    capture(FLOOR, 0.1),
                ],
                clock,
            )
            debug = DeviceDebug("offline", "global_cn", workspace())
            replay.task.device_debug = debug
            replay.task.current_floor = 10
            with patch.object(DeviceReader, "read") as reader:
                self.assertTrue(replay.task._select_first_device())
            reader.assert_not_called()
            self.assertEqual(
                replay.actions,
                [
                    (0, "AUTOMATON_TOWER_FIRST_DEVICE"),
                    (2, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
                    (4, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
                ],
            )
            entry = json.loads(
                (debug.path / "001/record.json").read_text(encoding="utf-8")
            )
            self.assertEqual(entry["floor"], 10)
            self.assertEqual(entry["mode"], "first_card")
            self.assertIsNone(entry["candidates"])
            self.assertIsNone(entry["chosen_slot"])
            self.assertEqual(entry["confirmation_requests"], 2)
            self.assertEqual(entry["status"], "confirmed")
            self.assertEqual(len(list(debug.path.rglob("*.png"))), 2)
            for stage, fixture in (("offer", PENDING), ("selected", SELECTED)):
                with Image.open(debug.path / "001" / (stage + ".png")) as image:
                    self.assertEqual(image.size, (1280, 720))
                    np.testing.assert_array_equal(
                        np.asarray(image), fixture_image(MANIFEST, fixture)
                    )

    def test_debug_write_failure_keeps_legacy_actions_and_disables_only_capture(self):
        with ControlledClock() as clock:
            replay = TowerReplay(
                [capture(PENDING), capture(SELECTED), capture(FLOOR)], clock
            )
            debug = DeviceDebug("offline", "global_cn", workspace())
            replay.task.device_debug = debug
            with patch(
                "tasks.automaton_tower.device_debug.save_image",
                side_effect=OSError("disk full"),
            ) as save:
                self.assertTrue(replay.task._select_first_device())
            self.assertFalse(debug.enabled)
            self.assertEqual(save.call_count, 1)
            self.assertEqual(
                replay.actions,
                [
                    (0, "AUTOMATON_TOWER_FIRST_DEVICE"),
                    (1, "AUTOMATON_TOWER_DEVICE_CONFIRM"),
                ],
            )


class TowerProfileUiTests(unittest.TestCase):
    def test_profile_controls_import_export_and_edit_without_overwriting(self):
        from module.webui import automaton_tower as panel
        from tasks.automaton_tower.profile_store import ProfileStore

        store = ProfileStore(workspace())
        content = profile_text(装置优先顺序=["暴击率强化装置"])
        first = store.import_profile(content)
        store.activate("offline", first)
        values = {panel.SELECT: first}
        with ExitStack() as stack:
            stack.enter_context(patch.object(panel, "ProfileStore", return_value=store))
            stack.enter_context(patch.object(panel, "pin", values))
            stack.enter_context(
                patch.object(
                    panel, "use_scope", side_effect=lambda *a, **kw: nullcontext()
                )
            )
            stack.enter_context(
                patch.object(panel, "popup", side_effect=lambda *a, **kw: nullcontext())
            )
            outputs = {
                name: stack.enter_context(patch.object(panel, name))
                for name in (
                    "put_text",
                    "put_warning",
                    "put_select",
                    "put_checkbox",
                    "put_scope",
                    "clear",
                    "put_input",
                    "put_buttons",
                    "put_textarea",
                    "put_file",
                    "pin_on_change",
                    "toast",
                    "close_popup",
                )
            }
            stack.enter_context(
                patch.object(
                    panel,
                    "file_upload",
                    return_value={"content": content.encode("utf-8")},
                )
            )
            gui = SimpleNamespace(alas_name="offline")
            panel.render_profile_controls(gui)
            callbacks = next(
                call.kwargs["onclick"]
                for call in outputs["put_buttons"].call_args_list
                if "使用所选方案" in call.args[0]
            )
            values[panel.SELECT] = panel.LEGACY
            callbacks[0]()
            self.assertIsNone(store.active_id("offline"))
            callbacks[1]()
            second = store.active_id("offline")
            self.assertNotEqual(first, second)
            values[panel.SELECT] = second
            callbacks[2]()
            outputs["put_file"].assert_called_once_with(
                "月塔装置方案.yaml", content.encode("utf-8"), label="下载方案"
            )
            callbacks[3]()
            save_copy = outputs["put_buttons"].call_args.kwargs["onclick"][0]
            values[panel.EDITOR] = profile_text(方案名="修改后的方案")
            save_copy()
            self.assertEqual(store.active("offline").name, "修改后的方案")
            self.assertEqual(store.export(first).decode("utf-8"), content)
            self.assertEqual(len(list(store.user.glob("*.yaml"))), 3)
            all_buttons = {label for call in outputs["put_buttons"].call_args_list for label in call.args[0]}
            self.assertNotIn("保存调试设置", all_buttons)
            self.assertNotIn("保存行动力设置", all_buttons)
            watcher_calls = outputs["pin_on_change"].call_args_list
            watchers = {name: callback for (name, callback), _ in watcher_calls}
            self.assertEqual(set(watchers), {panel.DEBUG, panel.REFILL})
            outputs["put_input"].assert_not_called()
            for call in watcher_calls:
                self.assertEqual(call.kwargs, {"clear": True, "serial_mode": True})

            active = store.active_id("offline")
            gui.alas_name = "another"
            watchers[panel.DEBUG](["enabled"])
            self.assertTrue(store.debug_enabled("offline"))
            self.assertFalse(store.debug_enabled("another"))
            outputs["put_input"].assert_not_called()

            watchers[panel.REFILL](["enabled"])
            self.assertTrue(store.stamina("offline").enabled)
            self.assertEqual(outputs["put_input"].call_args.kwargs["value"], 0)
            reserve_call = outputs["pin_on_change"].call_args
            self.assertEqual(reserve_call.args[0], panel.RESERVE)
            self.assertEqual(reserve_call.kwargs, {"clear": True, "serial_mode": True})
            save_reserve = reserve_call.args[1]
            save_reserve(12)
            self.assertEqual(store.stamina("offline").reserve_leaves, 12)
            for invalid in (None, -1, 1.5, True):
                with self.subTest(invalid=invalid):
                    save_reserve(invalid)
                    self.assertEqual(store.stamina("offline").reserve_leaves, 12)
            reserve_scope = outputs["put_input"].call_args.kwargs["scope"]
            outputs["put_scope"].assert_any_call(reserve_scope)
            outputs["put_input"].reset_mock()
            outputs["clear"].reset_mock()
            watchers[panel.REFILL]([])
            outputs["clear"].assert_called_once_with(reserve_scope)
            outputs["put_input"].assert_not_called()
            self.assertFalse(store.stamina("offline").enabled)
            self.assertEqual(store.stamina("offline").reserve_leaves, 12)
            self.assertEqual(store.active_id("offline"), active)
            self.assertFalse(store.stamina("another").enabled)
            self.assertEqual(store.stamina("another").reserve_leaves, 0)

            gui.alas_name = "offline"
            panel.render_profile_controls(gui)
            outputs["put_input"].assert_not_called()
            refill_call = outputs["pin_on_change"].call_args
            self.assertEqual(refill_call.args[0], panel.REFILL)
            refill_call.args[1](["enabled"])
            self.assertEqual(outputs["put_input"].call_args.kwargs["value"], 12)
            outputs["put_input"].reset_mock()
            panel.render_profile_controls(gui)
            outputs["put_input"].assert_called_once()
            self.assertEqual(outputs["put_input"].call_args.kwargs["value"], 12)
            checkboxes = {call.args[0]: call.kwargs["value"] for call in outputs["put_checkbox"].call_args_list}
            self.assertEqual(checkboxes, {panel.DEBUG: ["enabled"], panel.REFILL: ["enabled"]})
            watchers[panel.DEBUG]([])
            self.assertFalse(store.debug_enabled("offline"))

    def test_task_detail_extension_registers_only_tower_profile_panel(self):
        from module.webui.automaton_tower import render_automaton_tower_panel
        from module.webui.task_extension import (
            TASK_DETAIL_RENDERERS,
            render_task_detail,
        )

        self.assertIs(
            TASK_DETAIL_RENDERERS["AutomatonTower"], render_automaton_tower_panel
        )
        gui = object()
        with patch.dict(TASK_DETAIL_RENDERERS, {"AutomatonTower": Mock()}) as renderers:
            render_task_detail(gui, "AutomatonTower")
            renderers["AutomatonTower"].assert_called_once_with(gui)
