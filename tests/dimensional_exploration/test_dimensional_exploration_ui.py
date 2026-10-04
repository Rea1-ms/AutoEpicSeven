"""Regression coverage for the September 25 screenshots and receipts."""
import ast
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from tests.support.exploration import ROOT, ControlledClock, artifact_path, frame, task_for
from tasks.dimensional_exploration.dimensional_exploration import DimensionalExploration
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import LOOT_BORDER
from tasks.dimensional_exploration.policy import Offer, RunProgress, choose_offer
from tasks.dimensional_exploration.recruitment import HeroCosts
from tasks.dimensional_exploration.vision import ExplorationVision, Hero, Resources
class ScreenshotRegressions(unittest.TestCase):
    def test_rest_preview_enters_both_new_screenshots(self):
        for suffix in ('20260925-113034-953', '20260925-113935-872'):
            task, vision, clicks = task_for(suffix)
            self.assertEqual(vision.state(), 'preview')
            task._node_selected, task._node_kind = True, 'rest'
            self.assertTrue(task.handle_preview(vision))
            self.assertEqual(clicks, ['NODE_ENTER'])

    def test_restart_rest_preview_reselects_then_enters(self):
        task, vision, clicks = task_for('20260925-113935-872')
        task.handle_preview(vision)
        task.handle_preview(vision)
        self.assertEqual(clicks, ['ExplorationNode_rest', 'NODE_ENTER'])

    def test_white_selected_loot_border_confirms_after_restart(self):
        task, vision, clicks = task_for('20260925-113419-624')
        self.assertEqual(vision.state(), 'loot')
        self.assertEqual([vision.gold_border(b) for b in LOOT_BORDER.iter_buttons()], [True, False, False])
        task.handle_loot(vision)
        self.assertEqual(clicks, ['LOOT_CHECK'])

    def test_unselected_loot_still_selects_a_card(self):
        task, _, clicks = task_for()
        vision = ExplorationVision(frame('20260922-082448-905'))
        task.handle_loot(vision)
        self.assertEqual(clicks, ['ExplorationLoot'])

    def test_restart_reward_overlay_claims_loot_and_can_continue(self):
        task, vision, clicks = task_for('20260925-113540-999')
        self.assertEqual(vision.state(), 'resume_rewards')
        task.handle_victory(vision)
        self.assertEqual(clicks, ['ClaimExplorationBattleReward'])
        vision.tokens = Mock(return_value=[])
        task.handle_victory(vision)
        self.assertEqual(clicks[-1], 'RESUME_REWARDS_CONTINUE')

    def test_full_class_ticket_uses_first_affordable_visible_hero(self):
        task, vision, clicks = task_for('20260925-114714-801')
        self.assertEqual(vision.quota(), (13, 20))
        task.config.DimensionalExploration_HealerPriority = '塔玛林尔'
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertEqual(task._hero_selected, '塔玛林尔')
        self.assertEqual(clicks, ['SelectExplorationHero'])
        task.device.swipe.assert_not_called()
        self.assertEqual(task.hero_costs.values['塔玛林尔'], 3)

    def test_unknown_animation_does_not_reset_selected_hero(self):
        task, _, _ = task_for()
        task.observe_state('hero')
        task._hero_selected = '拉兹'
        task.observe_state('unknown')
        task.observe_state('hero')
        self.assertEqual(task._hero_selected, '拉兹')

    def test_known_unaffordable_target_skips_without_scrolling(self):
        task, vision, clicks = task_for()
        task.config.DimensionalExploration_HealerPriority = '光之瑞儿'
        task.hero_costs.values['光之瑞儿'] = 4
        # With no quota, nobody can be recruited; a merely unaffordable
        # priority now permits affordable unlisted heroes instead of Back.
        vision.quota = Mock(return_value=(17, 17))
        vision.heroes = Mock(side_effect=AssertionError('must not scan list'))
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, ['BACK'])
        self.assertTrue(task._recruit_skipped)
        task.device.swipe.assert_not_called()

    def test_initial_party_keeps_affordable_fallback(self):
        task, vision, clicks = task_for()
        task.config.DimensionalExploration_HealerPriority = '光之瑞儿'
        task.hero_costs.values['光之瑞儿'] = 4
        task._initial_recruitment = True
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertIsNotNone(task._hero_best)
        self.assertNotIn('BACK', clicks)


class ReceiptRegressions(unittest.TestCase):
    def test_new_loot_wins_after_investment_within_budget(self):
        offers = [Offer(0, '未来投资', 50), Offer(2, '旧战利品', 120), Offer(3, '新战利品', 158, new=True)]
        self.assertEqual(choose_offer(offers, 211, 3, 3).index, 0)
        self.assertEqual(choose_offer(offers[1:], 161, 3, 3).index, 3)
        self.assertEqual(choose_offer(offers[1:], 150, 3, 3).index, 2)

    def test_shop_keeps_snapshot_through_toast_and_waits_for_debit(self):
        task, _, clicks = task_for()
        offers = [Offer(0, '未来投资', 50), Offer(2, '旧战利品', 120), Offer(3, '新战利品', 158, new=True)]
        vision = SimpleNamespace(resources=Mock(return_value=Resources(20, 211, 3, 3)),
                                 offers=Mock(return_value=offers))
        self.assertFalse(task.handle_shop(vision))
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual(task._pending_offer.index, 0)
        task._purchase_confirmed = True
        vision.offers.side_effect = AssertionError('toast must not trigger full inventory OCR')
        self.assertFalse(task.handle_shop(vision))
        task.device.click_record_clear.assert_not_called()
        vision.resources.return_value = Resources(23, 161, 3, 3)
        self.assertFalse(task.handle_shop(vision))
        self.assertTrue(task.handle_shop(vision))
        self.assertEqual(task._pending_offer.index, 3)
        task.device.click_record_clear.assert_called_once()
        self.assertEqual(vision.offers.call_count, 2)
        self.assertEqual(clicks, ['ExplorationShopOffer', 'ExplorationShopOffer'])

    def test_click_history_only_clears_on_confirmed_forward_transition(self):
        task, _, _ = task_for()
        for state in ('map', 'map', 'preview', 'preview', 'map', 'map', 'preview', 'preview'):
            task.observe_progress(state)
        task.device.click_record_clear.assert_not_called()
        task.observe_progress('rest')
        task.observe_progress('unknown')
        task.observe_progress('rest')
        task.device.click_record_clear.assert_not_called()
        task.observe_progress('rest')
        task.device.click_record_clear.assert_called_once()
        for _ in range(20):
            task.observe_progress('rest')
        task.device.click_record_clear.assert_called_once()

    def test_costs_persist_and_unknown_cost_does_not_skip(self):
        path = artifact_path('costs.json')
        costs = HeroCosts(path)
        self.assertFalse(costs.cannot_afford(('英雄甲',), 2))
        costs.learn([('英雄甲', 4, 0, 0), ('英雄乙', 3, 0, 0)])
        restored = HeroCosts(path)
        self.assertTrue(restored.cannot_afford(('英雄甲', '英雄乙'), 2))
        self.assertFalse(restored.cannot_afford(('英雄甲', '英雄丙'), 2))
        self.assertFalse(restored.cannot_afford(('英雄甲', '英雄乙'), 3))

    def test_recognized_page_does_not_call_global_ad_close_handler(self):
        task, _, _ = task_for('20260925-113419-624')
        task.progress, task.target = RunProgress(), 1
        task.handle_loot = Mock(return_value=False)
        task.handle_ui_recovery = Mock(return_value=False)
        task.ui_additional = Mock(side_effect=AssertionError('unrelated ad close'))
        task.device.screenshot = Mock(side_effect=RuntimeError('test end'))
        with self.assertRaisesRegex(RuntimeError, 'test end'):
            task.explore()
        task.ui_additional.assert_not_called()
        task.handle_ui_recovery.assert_called_once()

    def test_task_ocr_does_not_define_literal_rectangles(self):
        for path in (ROOT / 'tasks' / 'dimensional_exploration').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
                    if node.func.attr in ('text', 'tokens', 'number', 'bright_text', 'gold_border'):
                        area = node.args[0]
                        self.assertFalse(isinstance(area, ast.Tuple) and all(
                            isinstance(v, ast.Constant) for v in area.elts), (str(path), node.lineno))


class FullClassRecruitmentRegressions(unittest.TestCase):
    def picker(self):
        task, _, clicks = task_for('20260922-081352-852')
        for key in task.CLASS_OPTIONS.values():
            setattr(task.config, f'DimensionalExploration_{key}', '')
        task.config.DimensionalExploration_KnightPriority = '名单英雄甲'
        task.config.DimensionalExploration_MagePriority = '名单英雄乙'
        vision = SimpleNamespace(
            quota=Mock(return_value=(13, 28)), text=lambda _: '全职业招募',
            selected_hero=Mock(return_value=''), bright_text=Mock(return_value=True),
            hero_signature=[], rows_read=[], rows=[],
        )

        def heroes(stop_when=None):
            vision.hero_signature = []
            vision.rows_read = []
            found = []
            for hero in vision.rows:
                vision.hero_signature.append((hero.name, hero.cost, *hero.area[:2]))
                vision.rows_read.append(hero.name)
                found.append(hero)
                if stop_when and stop_when(hero):
                    break
            return found

        vision.heroes = Mock(side_effect=heroes)
        return task, vision, clicks

    def row(self, name, cost=2, y=90):
        return Hero(name, cost, (400, y, 550, y + 50))

    def test_first_encountered_member_of_any_class_list_precedes_other_heroes(self):
        task, vision, clicks = self.picker()
        vision.rows = [self.row('亚敏'), self.row('名单英雄乙', 3, 195), self.row('名单英雄甲', 2, 300)]
        self.assertFalse(task.handle_hero(vision))
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '名单英雄乙')
        self.assertEqual(vision.rows_read, ['亚敏', '名单英雄乙'])
        self.assertEqual(clicks, ['SelectExplorationHero'])
        task.device.swipe.assert_not_called()

    def test_one_column_swipe_waits_for_interval_and_stable_next_view(self):
        with ControlledClock() as clock:
            task, vision, clicks = self.picker()
            task.action_ready = DimensionalExploration.action_ready.__get__(task)
            vision.rows = [self.row('亚敏')]
            self.assertFalse(task.handle_hero(vision))
            self.assertTrue(task.handle_hero(vision))
            task.device.swipe.assert_called_once_with(
                (1000, 355), (672, 355), duration=(0.3, 0.4), name='ExplorationHeroList')
            vision.rows = [self.row('名单英雄乙', 3)]
            reads = vision.heroes.call_count
            self.assertFalse(task.handle_hero(vision))
            self.assertEqual(vision.heroes.call_count, reads)
            clock.advance(2.1)
            self.assertFalse(task.handle_hero(vision))
            self.assertEqual(clicks, [])
            self.assertTrue(task.handle_hero(vision))
            self.assertEqual(task._hero_selected, '名单英雄乙')
            self.assertEqual(clicks, ['SelectExplorationHero'])

    def test_recruited_and_unaffordable_members_do_not_stop_scanning(self):
        task, vision, clicks = self.picker()
        task.config.DimensionalExploration_MagePriority = '名单英雄乙 > 名单英雄丙'
        task._recruited_heroes = ['名单英雄甲']
        vision.quota.return_value = (25, 28)
        vision.rows = [self.row('名单英雄甲'), self.row('名单英雄乙', 4, 195),
                       self.row('亚敏', 2, 300), self.row('名单英雄丙', 3, 405)]
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '名单英雄丙')
        self.assertEqual(task._hero_selected_cost, 3)
        self.assertEqual(clicks, ['SelectExplorationHero'])

    def test_missing_member_selects_affordable_fallback_at_end(self):
        for initial in (False, True):
            with self.subTest(initial=initial):
                task, vision, clicks = self.picker()
                task._initial_recruitment = initial
                vision.rows = [self.row('亚敏')]
                for _ in range(6):
                    task.handle_hero(vision)
                self.assertEqual(task.device.swipe.call_count, 2)
                self.assertEqual(clicks, ['SelectExplorationHero'])
                self.assertFalse(task._recruit_skipped)
                self.assertEqual(task._hero_selected, '亚敏')

    def test_empty_completed_or_unaffordable_preferences_keep_affordable_fallback(self):
        for reason in ('empty', 'completed', 'unaffordable'):
            with self.subTest(reason=reason):
                task, vision, clicks = self.picker()
                if reason == 'empty':
                    task.config.DimensionalExploration_KnightPriority = ''
                    task.config.DimensionalExploration_MagePriority = ''
                elif reason == 'completed':
                    task._recruited_heroes = ['名单英雄甲', '名单英雄乙']
                else:
                    vision.quota.return_value = (26, 28)
                    task.hero_costs.values = {'名单英雄甲': 3, '名单英雄乙': 4}
                vision.rows = [self.row('亚敏')]
                if reason == 'unaffordable':
                    self.assertFalse(task.handle_hero(vision))
                    self.assertTrue(task.handle_hero(vision))
                    self.assertEqual(clicks, ['SelectExplorationHero'])
                    self.assertEqual(task._hero_selected, '亚敏')
                    task.device.swipe.assert_not_called()
                    continue
                for _ in range(5):
                    task.handle_hero(vision)
                self.assertEqual(clicks, [])
                self.assertTrue(task.handle_hero(vision))
                self.assertEqual(clicks, ['SelectExplorationHero'])
                self.assertEqual(task._hero_selected, '亚敏')

    def test_unaffordable_final_view_returns_to_remembered_affordable_fallback(self):
        task, vision, clicks = self.picker()
        vision.quota.return_value = (26, 28)
        vision.rows = [self.row('亚敏')]
        task.handle_hero(vision)
        task.handle_hero(vision)
        vision.rows = [self.row('无额度的英雄', 3)]
        for _ in range(6):
            task.handle_hero(vision)
        self.assertTrue(task._hero_returning)
        self.assertEqual(clicks, [])
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task.device.swipe.call_args.args, ((470, 355), (798, 355)))
        vision.rows = [self.row('亚敏')]
        self.assertFalse(task.handle_hero(vision))
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '亚敏')
        self.assertEqual(clicks, ['SelectExplorationHero'])

    def test_no_affordable_hero_leaves_without_spending_beyond_quota(self):
        task, vision, clicks = self.picker()
        vision.quota.return_value = (27, 28)
        vision.rows = [self.row('亚敏', 2), self.row('名单英雄乙', 3, 195)]
        for _ in range(6):
            task.handle_hero(vision)
        self.assertEqual(clicks, ['BACK'])
        self.assertIsNone(task._hero_selected)
        self.assertIsNone(task._recruit_pending)

    def test_missed_selection_retries_same_member_without_confirming_or_swiping(self):
        task, vision, clicks = self.picker()
        vision.rows = [self.row('亚敏'), self.row('名单英雄乙', 3, 195)]
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, ['SelectExplorationHero'] * 2)
        self.assertIsNone(task._recruit_pending)
        self.assertEqual(task._recruited_heroes, [])
        task.device.swipe.assert_not_called()

    def test_restart_pending_receipt_keeps_target_even_outside_current_lists(self):
        task, vision, clicks = self.picker()
        task._recruit_pending = {'name': '亚敏', 'cost': 2, 'quota': [13, 28]}
        vision.rows = [self.row('名单英雄乙'), self.row('亚敏', 2, 195)]
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '亚敏')
        self.assertEqual(vision.rows_read, ['名单英雄乙', '亚敏'])
        self.assertEqual(clicks, ['SelectExplorationHero'])
        vision.selected_hero.return_value = '亚敏'
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks[-1], 'HERO_CONFIRM')
        self.assertEqual(task._recruit_pending['quota'], [13, 28])
        vision.quota.return_value = (15, 28)
        task.observe_recruitment('hero', vision)
        self.assertEqual(task._recruited_heroes, [])
        task.observe_recruitment('hero', vision)
        self.assertEqual(task._recruited_heroes, ['亚敏'])
        self.assertIsNone(task._recruit_pending)


if __name__ == '__main__':
    unittest.main()
