# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Offline checks for inheritance-store weekly charm purchases."""

import unittest
from pathlib import Path
from types import SimpleNamespace


ORIGINAL_REPO = Path(__import__("subprocess").check_output(["git","rev-parse","--git-common-dir"],text=True).strip()).resolve().parent
WORKTREE = Path(__file__).resolve().parents[2]
SCREENSHOT = input_root('store') / 'inheritance-charms.png'

import module.config.server as server  # noqa: E402

server.lang = "global_cn"
server.server = "OVERSEA-Play"

from tasks.store.assets.assets_store_items import (  # noqa: E402
    EQUIPMENT_REFORGING_STONE_SELECTION_CHEST,
    GREATER_ACCESSORY_CHARM,
    GREATER_ARTIFACT_CHARM,
    LESSER_ACCESSORY_CHARM,
    LESSER_ARTIFACT_CHARM,
    POTENTIAL_FRAGMENTS,
    STORE_ITEMS_SEARCH,
)
from tasks.store.current import CurrentStore  # noqa: E402


class StoreInheritanceCharmsTest(unittest.TestCase):
    def test_assets_match_attached_store_screenshot(self):
        image = load_image(SCREENSHOT)
        for asset in (
            POTENTIAL_FRAGMENTS,
            EQUIPMENT_REFORGING_STONE_SELECTION_CHEST,
            LESSER_ARTIFACT_CHARM,
            GREATER_ARTIFACT_CHARM,
            LESSER_ACCESSORY_CHARM,
            GREATER_ACCESSORY_CHARM,
        ):
            asset.load_search(STORE_ITEMS_SEARCH.area)
            self.assertEqual(len(asset.match_multi_template(image, threshold=30)), 1)
            self.assertTrue(asset.match_template_color(image, threshold=30))

    def test_inheritance_purchase_plans_use_weekly_limits(self):
        store = CurrentStore.__new__(CurrentStore)
        store.config = SimpleNamespace(
            StoreWeekly_BuyInheritancePotentialFragments=2,
            StoreWeekly_BuyInheritanceEquipmentReforgingStoneSelectionChest=True,
            StoreWeekly_BuyInheritanceMorogora=0,
            StoreWeekly_BuyInheritanceLesserArtifactCharm=3,
            StoreWeekly_BuyInheritanceGreaterArtifactCharm=True,
            StoreWeekly_BuyInheritanceLesserAccessoryCharm=2,
            StoreWeekly_BuyInheritanceGreaterAccessoryCharm=True,
        )

        plans = {item.name: item for item in store._build_inheritance_store_items()}
        expected = {
            "inheritance_potential_fragments": (2, 2, "target"),
            "inheritance_reforging_stone_selection_chest": (1, 1, "once"),
            "inheritance_lesser_artifact_charm": (3, 3, "target"),
            "inheritance_greater_artifact_charm": (1, 1, "once"),
            "inheritance_lesser_accessory_charm": (2, 3, "target"),
            "inheritance_greater_accessory_charm": (1, 1, "once"),
        }
        for name, values in expected.items():
            plan = plans[name]
            self.assertEqual(
                (plan.desired_quantity, plan.purchase_limit, plan.quantity_strategy),
                values,
            )
