"""Offline shop badge regressions from the September 25 user screenshots."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.support.exploration import frame, record_action, task_for
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import SHOP_OFFER
from tasks.dimensional_exploration.policy import choose_offer
from tasks.dimensional_exploration.vision import ExplorationVision


class ShopNewRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = {}
        for suffix in ('20260925-231015-103', '20260925-231033-887'):
            vision = ExplorationVision(frame(suffix))
            cls.snapshots[suffix] = (vision.offers(), vision.resources())

    def test_badges_bind_to_exact_items_in_both_rows(self):
        for suffix, (offers, _) in self.snapshots.items():
            with self.subTest(screenshot=suffix):
                self.assertEqual(len(offers), 8)
                self.assertEqual([(o.index, o.name, o.price) for o in offers if o.new],
                                 [(2, '神殿灯盏', 153), (7, '大祭司戒指', 126)])

    def test_investment_and_healing_still_precede_new_loot(self):
        offers, resources = self.snapshots['20260925-231015-103']
        self.assertEqual((resources.fragments, resources.life, resources.max_life), (495, 2, 3))
        balance, life = resources.fragments, resources.life
        expected = [(0, '未来投资'), (5, '恢复生命体征'), (7, '大祭司戒指'),
                    (2, '神殿灯盏'), (3, '发光石头')]
        for index, name in expected:
            selected = choose_offer(offers, balance, life, resources.max_life)
            self.assertEqual((selected.index, selected.name), (index, name))
            balance -= selected.price
            if selected.name == '恢复生命体征':
                life = resources.max_life
            offers = [replace(o, sold=True) if o.index == selected.index else o for o in offers]
        self.assertEqual(balance, 13)
        self.assertIsNone(choose_offer(offers, balance, life, resources.max_life))

    def test_affordability_and_sold_flags_still_filter_new_loot(self):
        offers, resources = self.snapshots['20260925-231033-887']
        self.assertEqual((resources.fragments, resources.life), (400, 3))
        self.assertEqual([o.index for o in offers if o.sold], [0, 5])
        self.assertEqual(choose_offer(offers, 400, 3, 3).index, 7)
        sold_ring = [replace(o, sold=True) if o.index == 7 else o for o in offers]
        self.assertEqual(choose_offer(sold_ring, 400, 3, 3).index, 2)
        self.assertEqual(choose_offer(offers, 125, 3, 3).index, 3)
        self.assertIsNone(choose_offer(offers, 107, 3, 3))

    def test_stable_shop_clicks_new_item_and_keeps_it_through_payment(self):
        task, _, _ = task_for('20260925-231033-887')
        offers, resources = self.snapshots['20260925-231033-887']
        vision = SimpleNamespace(offers=Mock(return_value=offers),
                                 resources=Mock(return_value=resources))
        clicked = []
        task.click_action = lambda button: clicked.append(record_action(button.area)) or True
        self.assertFalse(task.handle_shop(vision))
        self.assertEqual(clicked, [])
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual(task._pending_offer.name, '大祭司戒指')
        self.assertEqual(clicked, [list(SHOP_OFFER.iter_buttons())[7].area])
        task._purchase_confirmed = True
        vision.offers.side_effect = AssertionError('Do not rescan a shop obscured by the receipt toast')
        self.assertFalse(task.handle_shop(vision))
        vision.resources.return_value = replace(resources, fragments=274)
        self.assertFalse(task.handle_shop(vision))
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual(task._pending_offer.name, '神殿灯盏')
        self.assertTrue(task._pending_offer.new)
        self.assertEqual(clicked[-1], list(SHOP_OFFER.iter_buttons())[2].area)
        self.assertEqual(vision.offers.call_count, 2)


if __name__ == '__main__':
    unittest.main()
