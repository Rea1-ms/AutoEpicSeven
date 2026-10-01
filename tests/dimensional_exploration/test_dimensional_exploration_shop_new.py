"""Offline shop badge regressions from the September 25 user screenshots."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.support.exploration import frame, record_action, task_for
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    FRAGMENT_ICON, OCR_FRAGMENT, SHOP_OFFER,
)
from tasks.dimensional_exploration.policy import Offer, choose_offer, parse_number
from tasks.dimensional_exploration.vision import ExplorationVision, Resources, match_in


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


class ShopCoreRegressions(unittest.TestCase):
    def test_core_icon_margin_does_not_block_shop_resources(self):
        task, vision, clicks = task_for('20260928-214032-646')
        self.assertEqual(vision.resources(), Resources(40, 171, 3, 3))
        self.assertIsNone(parse_number(': 40'))
        self.assertFalse(task.handle_shop(vision))
        self.assertEqual(clicks, [])
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual((task._pending_offer.index, task._pending_offer.price), (3, 85))

    def test_new_loot_precedes_investment_only_above_twenty_cores(self):
        offers = [Offer(0, '未来投资', 50), Offer(5, '恢复生命体征', 35),
                  Offer(2, '未收录战利品', 121, new=True), Offer(3, '已有战利品', 85)]
        for cores, index in ((19, 0), (20, 0), (21, 2), (40, 2)):
            with self.subTest(cores=cores):
                self.assertEqual(choose_offer(offers, 121, 1, 3, cores=cores).index, index)
        task, _, clicks = task_for('20260925-231033-887')
        vision = SimpleNamespace(resources=lambda: Resources(21, 121, 1, 3), offers=lambda: offers)
        self.assertFalse(task.handle_shop(vision))
        self.assertEqual(clicks, [])
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual(task._pending_offer.index, 2)

    def test_investment_continues_without_affordable_unsold_new_loot(self):
        investment = Offer(0, '未来投资', 50)
        for new_offer in (Offer(2, '太贵', 122, new=True),
                          Offer(2, '已买', 121, sold=True, new=True),
                          Offer(2, '价格未知', None, new=True),
                          Offer(1, '招募券', 50, new=True)):
            with self.subTest(offer=new_offer):
                self.assertEqual(choose_offer([investment, new_offer], 121, 1, 3, cores=40), investment)
        offers = [investment, Offer(2, '未收录战利品', 121, new=True)]
        first = choose_offer(offers, 171, 3, 3, cores=40)
        remaining = [replace(o, sold=True) if o.index == first.index else o for o in offers]
        self.assertEqual(choose_offer(remaining, 171 - first.price, 3, 3, cores=40), investment)

    def test_transient_map_keeps_purchase_until_debit_is_confirmed(self):
        task, vision, clicks = task_for('20260928-214032-646')
        investment = Offer(0, '未来投资', 50)
        task._shop_offers = [investment, Offer(3, '大祭司未完记录', 85)]
        task._pending_offer = investment
        task._purchase_balance = 221
        task._purchase_confirmed = True
        task._last_state = 'shop'
        task.observe_state('map')
        self.assertEqual(task._pending_offer, investment)
        self.assertFalse(task.handle_map(vision))
        self.assertEqual(clicks, [])
        task.observe_state('shop')
        vision.offers = Mock(side_effect=AssertionError('Receipt must keep the original shop snapshot'))
        self.assertFalse(task.handle_shop(vision))
        self.assertEqual(clicks, [])
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual(task._pending_offer.index, 3)
        self.assertTrue(task._shop_offers[0].sold)
        task.device.click_record_clear.assert_called_once()
        vision.offers.assert_not_called()

    def test_next_map_node_clears_previous_shop_snapshot(self):
        task, vision, clicks = task_for('20260922-081522-090')
        task._shop_offers = [Offer(0, '未来投资', 50)]
        task._shop_candidate = task._shop_offers
        task._pending_offer = task._shop_offers[0]
        task._purchase_confirmed = True
        task._last_state = 'shop'
        task.observe_state('map')
        self.assertTrue(task.handle_map(vision))
        self.assertEqual(len(clicks), 1)
        self.assertIsNone(task._shop_offers)
        self.assertIsNone(task._shop_candidate)
        self.assertIsNone(task._pending_offer)
        self.assertFalse(task._purchase_confirmed)


class ShopZeroBalanceRegressions(unittest.TestCase):
    def test_zero_fragment_balance_is_read_from_shop_screenshot(self):
        vision = ExplorationVision(frame('20261001-011604-278'))
        self.assertEqual(vision.state(), 'shop')
        self.assertEqual(vision.resources(), Resources(43, 0, 3, 3))

    def test_resume_at_zero_balance_leaves_without_buying(self):
        task, vision, clicks = task_for('20261001-011604-278')
        self.assertFalse(task.handle_shop(vision))
        self.assertEqual(clicks, [])
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual(clicks, ['ROOM_LEAVE'])
        self.assertIsNone(task._pending_offer)
        self.assertFalse(task._purchase_confirmed)

    def test_confirmed_purchase_waits_for_two_zero_balance_frames(self):
        task, vision, clicks = task_for('20261001-011604-278')
        # The confirmed receipt is simulated; the supplied screenshot only
        # proves its final zero balance, not the item's original purchase price.
        offer = Offer(2, '空虚眼瞳', 121, new=True)
        task._shop_offers = [offer, Offer(3, '已有战利品', 85)]
        task._pending_offer = offer
        task._purchase_balance = offer.price
        task._purchase_confirmed = True
        vision.offers = Mock(side_effect=AssertionError('Keep the confirmed shop inventory'))
        delayed = SimpleNamespace(resources=lambda: Resources(43, offer.price, 3, 3))
        for _ in range(2):
            self.assertFalse(task.handle_shop(delayed))
            self.assertEqual(clicks, [])
            self.assertEqual(task._pending_offer, offer)
            self.assertFalse(task._shop_offers[0].sold)
        self.assertFalse(task.handle_shop(vision))
        self.assertEqual(clicks, [])
        self.assertTrue(task._purchase_confirmed)
        self.assertFalse(task._shop_offers[0].sold)
        task.device.click_record_clear.assert_not_called()
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual(clicks, ['ROOM_LEAVE'])
        self.assertTrue(task._shop_offers[0].sold)
        self.assertIsNone(task._pending_offer)
        self.assertFalse(task._purchase_confirmed)
        task.device.click_record_clear.assert_called_once()
        vision.offers.assert_not_called()

    def test_unreadable_fragment_region_is_not_treated_as_zero(self):
        for background in (0, 255):
            with self.subTest(background=background):
                task, vision, clicks = task_for('20261001-011604-278')
                fragment = match_in(vision.image, FRAGMENT_ICON, FRAGMENT_ICON.search)
                self.assertIsNotNone(fragment)
                x1, y1, x2, y2 = (fragment[1][2], OCR_FRAGMENT.area[1],
                                  OCR_FRAGMENT.area[2], OCR_FRAGMENT.area[3])
                vision.image = vision.image.copy()
                vision.image[y1:y2, x1:x2] = background
                self.assertIsNotNone(match_in(vision.image, FRAGMENT_ICON, FRAGMENT_ICON.search))
                self.assertIsNone(vision.resources())
                self.assertFalse(task.handle_shop(vision))
                self.assertEqual(clicks, [])
        for text in ('', '。', '.', '?', 'unknown'):
            with self.subTest(text=text):
                self.assertIsNone(parse_number(text))


if __name__ == '__main__':
    unittest.main()
