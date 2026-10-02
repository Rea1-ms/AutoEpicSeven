# ruff: noqa: E402
import unittest
from module.config import server
server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

from pathlib import Path
WORKTREE=Path(__file__).resolve().parents[2]

"""Offline checks for the global-CN limited-time Urgent Tasks flow."""


from pathlib import Path

from types import SimpleNamespace

SCREENSHOTS = input_root("urgent_tasks")

import module.config.server as server


from module.ocr.ocr import DigitCounter

from tasks.base.page import Page, page_combat_common, page_combat_urgent

from tasks.dungeon.assets.assets_dungeon_configs_urgent_tasks import (
    OCR_URGENT_COMBAT_TIMES_REMAINING,
)

from tasks.dungeon.assets.assets_dungeon_action import (
    COMBAT_RESULT_CONFIRM,
    COMBAT_RESULT_LEAVE,
)

from tasks.dungeon.assets.assets_dungeon_state import (
    COMBAT_PREPARE_PET_EMPTY,
    COMBAT_RESULT_CLEAR,
)

from tasks.dungeon.dungeon import Combat

class OfflineDevice:
    def __init__(self, image):
        self.image = image

    @staticmethod
    def stuck_record_add(*args, **kwargs):
        pass

class OfflineConfig(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cross_values = {}

    def cross_get(self, path, default=None):
        return self.cross_values.get(path, default)

    def cross_set(self, path, value):
        self.cross_values[path] = value

class ExchangeDevice:
    def __init__(self, currency: int):
        self.currency = currency
        self.batches = []
        self.screenshots = 0
        self.click_record_clears = 0

    def screenshot(self):
        self.screenshots += 1

    def multi_click(self, button, n, interval):
        self.batches.append((button.name, n, interval))
        self.currency = max(self.currency - n * 100, 0)

    def click_record_clear(self):
        self.click_record_clears += 1

def build_combat(image_name: str, difficulty: str) -> Combat:
    combat = Combat.__new__(Combat)
    combat.config = OfflineConfig(
        Combat_UrgentTasksDifficulty=difficulty,
        Emulator_GameLanguage="cn",
        Scheduler_ServerUpdate="02:00",
        task=SimpleNamespace(command="Combat"),
    )
    combat.device = OfflineDevice(load_image(SCREENSHOTS / image_name))
    combat._dungeon_domain = lambda: "UrgentTasks"
    return combat

def main() -> None:
    Page.init_connection(page_combat_urgent)
    assert page_combat_common.parent == page_combat_urgent

    detail = build_combat("superior-detail.png", "Superior")
    assert detail._is_urgent_tasks_detail_page()
    assert detail._urgent_tasks_remaining() == 5
    assert detail._combat_stage_stamina_cost() == 30
    assert detail._combat_burnout_enabled() is False
    assert detail._inspect_urgent_tasks_resource("currency").value == 0
    detail_stamina = detail._inspect_urgent_tasks_resource("stamina")
    assert (detail_stamina.value, detail_stamina.total) == (6, 360)

    superior = build_combat("superior-prepare.png", "Superior")
    assert superior._is_urgent_tasks_prepare_page()
    assert superior._is_urgent_tasks_target_prepare_page()
    superior.config.Combat_UrgentTasksDifficulty = "Normal"
    assert superior._is_urgent_tasks_target_prepare_page()

    normal = build_combat("normal-prepare.png", "Normal")
    assert normal._is_urgent_tasks_target_prepare_page()
    assert normal._combat_stage_stamina_cost() == 15

    superior_large = build_combat("superior-prepare-large.png", "Superior")
    assert superior_large._is_urgent_tasks_target_prepare_page()
    normal_large = build_combat("normal-prepare-large.png", "Normal")
    assert normal_large._is_urgent_tasks_target_prepare_page()

    fast_on = build_combat("superior-fast-on.png", "Superior")
    assert fast_on._is_fast_combat_on()
    assert not fast_on._is_fast_combat_off()
    fast_off = build_combat("superior-fast-off.png", "Superior")
    assert not fast_off._is_fast_combat_on()
    assert fast_off._is_fast_combat_off()

    auto_on = build_combat("battle-auto-on.png", "Superior")
    auto_off = build_combat("battle-manual-target.png", "Superior")
    assert auto_on._detect_auto_combat_state() is True
    assert auto_off._detect_auto_combat_state() is False
    assert COMBAT_RESULT_CLEAR.match_template_luma(
        load_image(SCREENSHOTS / "battle-clear.png")
    )
    assert COMBAT_RESULT_CONFIRM.match_template_luma(
        load_image(SCREENSHOTS / "battle-result.png")
    )
    assert COMBAT_RESULT_LEAVE.match_template_luma(
        load_image(SCREENSHOTS / "battle-leave.png")
    )

    hint = build_combat("prepare-times-hint.png", "Superior")
    current, _, total = DigitCounter(
        OCR_URGENT_COMBAT_TIMES_REMAINING,
        lang="cn",
        name="UrgentCombatTimesHint",
    ).ocr_single_line(hint.device.image)
    assert (current, total) == (3, 5)
    assert hint.appear(COMBAT_PREPARE_PET_EMPTY)
    assert hint._inspect_urgent_tasks_resource("currency").value == 250
    stamina = hint._inspect_urgent_tasks_resource("stamina")
    assert (stamina.value, stamina.total) == (475, 360)

    active_entry = build_combat("superior-detail.png", "Superior")
    active_entry.device.image = load_image(
        WORKTREE / "assets/global_cn/dungeon/configs/combat/entry/URGENT_ENTRY.png"
    )
    assert active_entry._is_urgent_tasks_entry_available()
    assert not active_entry._is_urgent_tasks_entry_locked()

    locked_entry = build_combat("superior-detail.png", "Superior")
    locked_entry.device.image = load_image(
        WORKTREE / "assets/share/dungeon/configs/combat/entry/URGENT_LOCKED.png"
    )
    assert locked_entry._is_urgent_tasks_entry_locked()
    assert not locked_entry._is_urgent_tasks_entry_available()

    active_entry.config.UrgentTasks_Enable = False
    assert not active_entry._urgent_tasks_enabled()
    active_entry.config.UrgentTasks_Enable = True
    assert not active_entry._urgent_tasks_checked_today()
    active_entry._mark_urgent_tasks_checked()
    assert active_entry._urgent_tasks_checked_today()
    active_entry.config.task.command = "CombatFarm"
    assert not active_entry._urgent_tasks_enabled()
    active_entry.config.task.command = "Combat"
    active_entry.config.Combat_Domain = "UrgentTasks"
    assert active_entry._configured_dungeon_domain() == "Hunt"

    exchange = build_combat("superior-detail.png", "Superior")
    exchange.device = ExchangeDevice(currency=1690)
    exchange._is_urgent_tasks_detail_page = lambda: True
    exchange._handle_dungeon_additional = lambda: False
    exchange.appear = lambda *args, **kwargs: True
    exchange_reads = 0

    def inspect_exchange_currency(_key):
        nonlocal exchange_reads
        exchange_reads += 1
        return SimpleNamespace(value=exchange.device.currency)

    exchange._inspect_urgent_tasks_resource = inspect_exchange_currency
    exchange.URGENT_TASKS_EXCHANGE_POST_CLICK_SECONDS = 0
    assert exchange._exchange_urgent_tasks_rewards()
    assert exchange.device.batches == [
        ("URGENT_TASKS_REWARD_EXCHANGE", 16, (0.2, 0.3))
    ]
    assert exchange.device.currency == 90
    assert exchange.device.click_record_clears == 1
    assert exchange_reads == 2
    print("urgent tasks offline checks passed")

class ManualChecks(unittest.TestCase):
    def tearDown(self):
        Page.clear_connection()

    def test_urgent_task_recognition_and_exchange(self):
        main()
