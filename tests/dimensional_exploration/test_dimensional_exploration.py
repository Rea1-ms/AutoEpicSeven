"""Offline exploration regressions. Run from the worktree being tested."""
import subprocess
import sys
from types import SimpleNamespace
import unittest

import numpy as np
from module.logger import logger
from tests.support.exploration import frame, record_action, make_task as make_exploration_task
from tasks.dimensional_exploration.policy import (
    EventChoice, Offer, RunProgress, choose_event, choose_offer,
    node_priority, parse_counter, parse_number,
)
from tasks.dimensional_exploration.vision import ExplorationVision
from tasks.dimensional_exploration.recruitment import same_hero
from tasks.dimensional_exploration.dimensional_exploration import DimensionalExploration

logger.setLevel('ERROR')

class PolicyTests(unittest.TestCase):
    def test_core_boundary_uses_current_count(self):
        self.assertLess(node_priority('shop', 19), node_priority('event', 19))
        for cores in (20, 21, 999):
            self.assertLess(node_priority('event', cores), node_priority('shop', cores))
        for kind in ('battle', 'elite', 'rest'):
            self.assertGreater(node_priority(kind, 20), node_priority('shop', 20))

    def test_shop_invests_before_other_purchases_and_never_withdraws(self):
        offers = [Offer(0, '未来投资', 50), Offer(4, '资金提取', 1), Offer(2, '战利品', 20)]
        self.assertEqual(choose_offer(offers, 100, 3, 3).name, '未来投资')
        self.assertEqual(choose_offer(offers, 40, 3, 3).name, '战利品')
        self.assertIsNone(choose_offer([offers[1]], 100, 3, 3))
        self.assertIsNone(choose_offer([Offer(0, '未来投资', None, True)], 100, 3, 3))

    def test_event_keeps_last_life_and_reserves_investment(self):
        pool = [EventChoice(0, '消耗1点生命体征，获得1个随机战利品'),
                EventChoice(1, '消耗2点生命体征，获得月亮之书', True)]
        self.assertEqual(choose_event(pool, cores=20, fragments=0, life=3, loot=0).index, 1)
        self.assertEqual(choose_event(pool, cores=20, fragments=0, life=2, loot=0).index, 0)
        self.assertIsNone(choose_event(pool, cores=20, fragments=0, life=1, loot=0))
        coin = [EventChoice(0, '消耗50个次元碎片，获得望远镜镜片', True), EventChoice(1, '直接离开')]
        self.assertEqual(choose_event(coin, cores=19, fragments=86, life=3, loot=0).index, 1)
        self.assertEqual(choose_event(coin, cores=20, fragments=86, life=3, loot=0).index, 0)
        self.assertIsNone(choose_event([EventChoice(0, '消耗未知资源，获得战利品')],
                                     cores=20, fragments=999, life=3, loot=9))

    def test_settlement_latch_survives_repeated_frames_and_restart(self):
        progress = RunProgress()
        self.assertTrue(progress.settle(True))
        self.assertFalse(progress.settle(True))
        restored = RunProgress(progress.completed, progress.settlement_seen)
        self.assertFalse(restored.settle(True))
        self.assertEqual(restored.completed, 1)
        restored.entered_run()
        self.assertTrue(restored.settle(True))
        self.assertEqual(restored.completed, 2)
        self.assertTrue(RunProgress().settle(False))

    def test_ocr_validation(self):
        self.assertIsNone(parse_number(''))
        self.assertIsNone(parse_number('O'))
        self.assertEqual(parse_number('2,216'), 2216)
        self.assertEqual(parse_counter('12/10'), (12, 10))
        self.assertIsNone(parse_counter('3/0'))
        self.assertTrue(same_hero('罪庚的安洁莉卡', '罪戾的安洁莉卡'))
        self.assertFalse(same_hero('拉斯', '起源拉斯'))
        self.assertFalse(same_hero('安洁莉卡', '罪戾的安洁莉卡'))


class ScreenshotTests(unittest.TestCase):
    def test_page_identity_and_modal_precedence(self):
        cases = {
            '20260921-105750-804': 'title', '20260922-081145-848': 'chapter',
            '20260922-081153-551': 'lobby', '20260924-091808-594': 'lobby',
            '20260924-091811-816': 'abandon', '20260922-081226-118': 'start_supply',
            '20260922-081258-807': 'recruitment', '20260922-081357-541': 'recruitment',
            '20260922-081352-852': 'hero', '20260923-233032-963': 'hero',
            '20260923-233036-793': 'hero', '20260922-081413-154': 'map',
            '20260922-081603-149': 'preview', '20260922-081423-319': 'prepare',
            '20260922-081430-868': 'battle', '20260922-081507-280': 'reward',
            '20260922-081517-145': 'victory', '20260922-081528-636': 'event',
            '20260922-081544-299': 'reward', '20260922-081548-987': 'event',
            '20260922-081730-252': 'supply_room', '20260922-081751-608': 'supply_room',
            '20260922-082448-905': 'loot', '20260922-082458-438': 'loot',
            '20260923-232305-766': 'rest', '20260923-232309-289': 'upgrade',
            '20260923-233115-192': 'revive', '20260923-232839-814': 'shop',
            '20260923-232843-964': 'buy', '20260923-232913-867': 'leave_confirm',
            '20260923-233811-030': 'failed', '20260923-233818-836': 'settlement',
            '20260924-091819-235': 'settlement',
        }
        for suffix, expected in cases.items():
            with self.subTest(screenshot=suffix):
                self.assertEqual(ExplorationVision(frame(suffix)).state(), expected)

    def test_accessible_nodes_exclude_completed_and_gray_nodes(self):
        cases = {
            '20260922-081413-154': ['battle'],
            '20260922-081522-090': ['event', 'battle', 'rest'],
            '20260922-081554-067': ['battle', 'battle'],
            '20260922-081721-752': ['supply'],
            '20260922-081757-281': ['elite', 'event'],
            '20260922-082926-197': ['boss'],
            '20260922-083127-685': ['rest'],
            '20260923-233512-883': ['boss'],
        }
        for suffix, expected in cases.items():
            with self.subTest(screenshot=suffix):
                self.assertCountEqual([n.kind for n in ExplorationVision(frame(suffix)).nodes()], expected)

    def test_shop_resources_and_offer_binding(self):
        v = ExplorationVision(frame('20260923-232839-814'))
        resources = v.resources()
        self.assertEqual((resources.cores, resources.fragments, resources.life), (21, 359, 2))
        offers = v.offers()
        self.assertEqual(len(offers), 8)
        self.assertTrue(offers[7].sold)
        self.assertEqual(choose_offer(offers, resources.fragments, resources.life, resources.max_life).index, 0)

    def test_event_buttons_with_journal_marker(self):
        v = ExplorationVision(frame('20260922-081528-636'))
        choices = v.event_choices()
        self.assertEqual(len(choices), 2)
        self.assertTrue(choices[0][0].journal)
        v = ExplorationVision(frame('20260923-233240-604'))
        choices = v.event_choices()
        self.assertEqual(len(choices), 2)
        self.assertTrue(choices[1][0].journal)
        self.assertEqual(len(ExplorationVision(frame('20260923-232331-766')).event_choices()), 3)

    def test_hero_names_costs_and_scrolled_columns(self):
        v = ExplorationVision(frame('20260922-081352-852'))
        heroes = v.heroes()
        self.assertTrue(any(h.name == '罪戾的安洁莉卡' and h.cost == 2 for h in heroes))
        self.assertFalse(any(h.name == '光之瑞儿' for h in heroes))
        heroes = ExplorationVision(frame('20260923-233036-793')).heroes()
        self.assertTrue(any('圣炎的艾庭' in h.name and h.cost == 2 for h in heroes))
        self.assertTrue(all(h.area[0] >= 342 for h in heroes))

    def test_settlement_repeated_frames_and_batch_exit(self):
        images = [frame(suffix) for suffix in (
            '20260923-233811-030', '20260923-233818-836',
            '20260923-233818-836', '20260924-091808-594')]
        class Device:
            def __init__(self):
                self.image = images[0]
                self.index = 0
                self.clicks = []
            def screenshot(self):
                self.index += 1
                if self.index >= len(images):
                    raise AssertionError('Loop failed to exit on lobby')
                self.image = images[self.index]
            def click(self, button):
                self.clicks.append(record_action(str(button)))
            def stuck_record_add(self, button):
                pass
        config = SimpleNamespace()
        device = Device()
        task = DimensionalExploration(config, device)
        task.progress, task.target = RunProgress(), 1
        task.action_ready = lambda: True
        task.explore()
        self.assertEqual(task.progress.completed, 1)
        self.assertEqual(config.DimensionalExplorationRuntime_Session['completed'], 1)
        self.assertEqual(device.index, 3)

    def test_initial_recruitment_is_not_mistaken_for_ready_party(self):
        for suffix, expected in [('20260922-081258-807', 'InitialRecruit'),
                                 ('20260922-081357-541', 'EXPLORE_ENTER')]:
            device = SimpleNamespace(image=frame(suffix))
            task = DimensionalExploration(SimpleNamespace(), device)
            clicked = []
            task.click_action = lambda b: clicked.append(record_action(str(b))) or True
            task.handle_recruitment(ExplorationVision(device.image))
            self.assertEqual(clicked, [expected])

    def test_server_page_registration_and_return_routes(self):
        for lang in ('cn', 'global_cn', 'global_en'):
            code = f'''
import module.config.server as s
s.set_lang({lang!r})
from tasks.base.page import Page, page_main
names = [p.name for p in Page.iter_pages()]
assert ('page_dimensional_exploration_title' in names) == ({lang!r} == 'global_cn')
Page.init_connection(page_main)
for p in Page.iter_pages():
    if 'dimensional_exploration' in p.name:
        assert p.parent is not None
list(Page.iter_check_buttons())
'''
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, encoding='utf-8', errors='replace')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_unknown_or_black_frame_has_no_exploration_state(self):
        self.assertEqual(ExplorationVision(np.zeros((720, 1280, 3), dtype=np.uint8)).state(), 'unknown')

    def make_task(self, suffix):
        return make_exploration_task(suffix)

    def test_starting_supply_selection_requires_positive_gold_state(self):
        for suffix, expected in [('20260922-081207-157', 'SUPPLY_BAGGAGE'),
                                 ('20260922-081226-118', 'SUPPLY_CONFIRM')]:
            task, vision, clicks = self.make_task(suffix)
            self.assertTrue(task.handle_start_supply(vision))
            self.assertEqual(clicks, [expected])

    def test_upgrade_and_revive_confirm_selected_hero(self):
        for suffix in ('20260923-232312-127', '20260923-233117-847'):
            task, vision, clicks = self.make_task(suffix)
            self.assertTrue(task.handle_rest_hero(vision))
            self.assertEqual(clicks, ['RestHeroConfirm'])

    def test_auto_combat_only_toggles_on_positive_manual_marker(self):
        for suffix, expected in [('20260922-081430-868', ['AUTO_COMBAT']),
                                 ('20260922-081440-084', [])]:
            task, vision, clicks = self.make_task(suffix)
            task.handle_battle(vision)
            self.assertEqual(clicks, expected)

    def test_zero_loot_event_can_still_choose_coin_option(self):
        task, vision, clicks = self.make_task('20260922-081528-636')
        self.assertTrue(task.handle_event(vision))
        self.assertEqual(clicks, ['ExplorationEventChoice'])

    def test_purchase_dialog_rechecks_name_price_currency(self):
        from module.exception import RequestHumanTakeover
        cases = [('20260923-232848-721', Offer(0, '未来投资', 50)),
                 ('20260923-232858-477', Offer(5, '恢复生命体征', 44)),
                 ('20260923-232904-541', Offer(6, '召唤仪式盘', 94))]
        for suffix, offer in cases:
            task, vision, clicks = self.make_task(suffix)
            self.assertTrue(task.handle_buy(vision))
            self.assertEqual(clicks, ['BUY_CANCEL'])
            clicks.clear()
            task._pending_offer = offer
            self.assertTrue(task.handle_buy(vision))
            self.assertEqual(clicks, ['BUY_CONFIRM'])
            task._pending_offer = Offer(offer.index, offer.name, offer.price + 1)
            with self.assertRaises(RequestHumanTakeover):
                task.handle_buy(vision)

    def test_node_preview_does_not_enter_a_different_type(self):
        task, vision, clicks = self.make_task('20260922-081805-941')
        task._node_selected = True
        task._node_kind = 'event'
        task.handle_preview(vision)
        self.assertNotIn('NODE_ENTER', clicks)
        task, vision, clicks = self.make_task('20260922-082932-782')
        task._node_selected = True
        task._node_kind = 'boss'
        task.handle_preview(vision)
        self.assertEqual(clicks, ['NODE_ENTER'])

    def test_selected_loot_confirms_instead_of_reselecting(self):
        task, vision, clicks = self.make_task('20260922-082458-438')
        task._loot_index = 2
        self.assertTrue(task.handle_loot(vision))
        self.assertEqual(clicks, ['LOOT_CHECK'])

    def test_empty_settlement_does_not_count_as_a_run(self):
        from module.exception import RequestHumanTakeover
        task, vision, clicks = self.make_task('20260924-091819-235')
        task.progress, task.target = RunProgress(), 1
        with self.assertRaises(RequestHumanTakeover):
            task.handle_settlement(vision)
        self.assertEqual(task.progress.completed, 0)
        self.assertEqual(clicks, [])



    def test_unreadable_accessible_node_does_not_change_priority(self):
        image = frame('20260922-081522-090')
        image[151:242, 488:576] = 0
        self.assertEqual(ExplorationVision(image).nodes(), [])


if __name__ == '__main__':
    unittest.main()
