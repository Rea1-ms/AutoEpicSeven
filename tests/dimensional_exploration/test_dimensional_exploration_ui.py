"""Regression coverage for the September 25 screenshots and receipts."""
import ast
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from tests.support.exploration import ROOT, artifact_path, frame, task_for
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import LOOT_BORDER
from tasks.dimensional_exploration.policy import Offer, RunProgress, choose_offer
from tasks.dimensional_exploration.recruitment import HeroCosts
from tasks.dimensional_exploration.vision import ExplorationVision, Resources
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
        vision = SimpleNamespace(resources=Mock(return_value=Resources(22, 211, 3, 3)),
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


if __name__ == '__main__':
    unittest.main()
