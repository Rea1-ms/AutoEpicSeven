# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Offline regression for inheritance-store cooldown viewport handling."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ORIGINAL_REPO = Path(__import__("subprocess").check_output(["git","rev-parse","--git-common-dir"],text=True).strip()).resolve().parent
WORKTREE = Path(__file__).resolve().parents[2]
SCREENSHOTS = input_root('store')

import module.config.server as server  # noqa: E402

server.lang = "global_cn"
server.server = "OVERSEA-Play"

from tasks.store.assets.assets_store_items import (  # noqa: E402
    EQUIPMENT_REFORGING_STONE_SELECTION_CHEST,
    ITEM_IN_CD,
    MOROGORA,
    POTENTIAL_FRAGMENTS,
)
from tasks.store.current import CurrentStore  # noqa: E402
from tasks.store.purchase import ItemPurchasePlan, PurchaseResult  # noqa: E402


class StoreInheritanceCooldownTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.complete_image = load_image(
            SCREENSHOTS / "inheritance-cooldown-20260925.png"
        )
        cls.full_image = load_image(SCREENSHOTS / "inheritance-charms.png")

    def make_store(self, image=None):
        store = CurrentStore.__new__(CurrentStore)
        start_image = self.complete_image if image is None else image
        store.device = SimpleNamespace(image=start_image, screenshot=Mock(), swipe=Mock())
        store._load_shared_item_search()
        store._build_inheritance_store_items = Mock(return_value=[
            ItemPurchasePlan("fragments", POTENTIAL_FRAGMENTS),
            ItemPurchasePlan("chest", EQUIPMENT_REFORGING_STONE_SELECTION_CHEST),
            ItemPurchasePlan("morogora", MOROGORA),
        ])
        store._wait_purchase_cooldown_before_switch = Mock()
        store._goto_inheritance_stone_store = Mock()
        store._reset_inheritance_store_view = Mock(
            side_effect=lambda: setattr(store.device, "image", start_image)
        )
        store._purchase_item = Mock(return_value=PurchaseResult(True, 1))
        store._record_purchase_result = Mock()
        store._record_purchase_time = Mock()
        store._wait_store_ready_after_purchase = Mock(return_value=True)
        store.device.screenshot()
        return store

    def test_user_screenshot_has_four_cooldown_clocks(self):
        store = self.make_store()
        self.assertEqual(
            len(ITEM_IN_CD.match_multi_template(store.device.image, threshold=30)), 4
        )
        self.assertTrue(store._inheritance_store_view_complete())
        store.device.image = self.full_image
        self.assertFalse(store._inheritance_store_view_complete())

    def test_dimmed_popup_is_not_an_available_store_view(self):
        store = self.make_store()
        store.device.image = (self.complete_image * 0.3).astype("uint8")
        self.assertFalse(store._inheritance_store_view_complete())

    def test_sold_out_items_do_not_trigger_purchase_or_tab_reset(self):
        store = self.make_store()
        store._locate_inheritance_item = Mock()
        self.assertTrue(store._run_inheritance_store())
        store._goto_inheritance_stone_store.assert_called_once()
        store._wait_purchase_cooldown_before_switch.assert_called_once()
        store._reset_inheritance_store_view.assert_not_called()
        store._locate_inheritance_item.assert_not_called()
        store._purchase_item.assert_not_called()
        store.device.swipe.assert_not_called()

    def test_available_items_after_missing_item_are_still_bought(self):
        store = self.make_store()
        store._item_ready_for_purchase = Mock(side_effect=[False, True, True])
        store._locate_inheritance_item = Mock()
        self.assertTrue(store._run_inheritance_store())
        self.assertEqual(
            [call.args[0].name for call in store._purchase_item.call_args_list],
            ["chest", "morogora"],
        )
        self.assertEqual(store._wait_store_ready_after_purchase.call_count, 2)
        store._reset_inheritance_store_view.assert_not_called()
        store._locate_inheritance_item.assert_not_called()

    def test_clock_seen_after_search_does_not_skip_reset(self):
        store = self.make_store(self.full_image)

        def locate(item):
            # The search reaches a later viewport containing sold-out cards.
            store.device.image = self.complete_image
            return False

        store._locate_inheritance_item = Mock(side_effect=locate)
        self.assertTrue(store._run_inheritance_store())
        self.assertEqual(store._locate_inheritance_item.call_count, 3)
        self.assertEqual(store._reset_inheritance_store_view.call_count, 2)
        store._purchase_item.assert_not_called()

    def test_next_tab_entry_can_enable_shortcut_after_purchase(self):
        store = self.make_store(self.full_image)
        store._locate_inheritance_item = Mock(return_value=True)
        store._reset_inheritance_store_view.side_effect = lambda: setattr(
            store.device, "image", self.complete_image
        )
        self.assertTrue(store._run_inheritance_store())
        store._purchase_item.assert_called_once()
        store._locate_inheritance_item.assert_called_once()
        store._reset_inheritance_store_view.assert_called_once()

    def test_view_is_rechecked_after_purchase(self):
        store = self.make_store()
        store._item_ready_for_purchase = Mock(return_value=True)
        store._locate_inheritance_item = Mock(return_value=False)

        def buy(item):
            store.device.image = self.full_image
            return PurchaseResult(True, 1)

        store._purchase_item.side_effect = buy
        self.assertTrue(store._run_inheritance_store())
        store._locate_inheritance_item.assert_called_once()
        store._reset_inheritance_store_view.assert_called_once()

    def test_purchase_failure_or_unsettled_store_stops_remaining_items(self):
        for result, settled in (
            (PurchaseResult(False), True),
            (PurchaseResult(True, 1), False),
        ):
            with self.subTest(result=result, settled=settled):
                store = self.make_store()
                store._item_ready_for_purchase = Mock(return_value=True)
                store._purchase_item.return_value = result
                store._wait_store_ready_after_purchase.return_value = settled
                self.assertFalse(store._run_inheritance_store())
                store._purchase_item.assert_called_once()
                store._reset_inheritance_store_view.assert_not_called()

    def test_target_already_reached_does_not_block_next_item(self):
        store = self.make_store()
        store._item_ready_for_purchase = Mock(return_value=True)
        store._purchase_item.return_value = PurchaseResult(
            False, quantity_source="target_reached"
        )
        self.assertTrue(store._run_inheritance_store())
        self.assertEqual(store._purchase_item.call_count, 3)
        store._reset_inheritance_store_view.assert_not_called()

    def test_no_enabled_items_does_not_enter_store(self):
        store = self.make_store()
        store._build_inheritance_store_items.return_value = []
        self.assertTrue(store._run_inheritance_store())
        store._goto_inheritance_stone_store.assert_not_called()
