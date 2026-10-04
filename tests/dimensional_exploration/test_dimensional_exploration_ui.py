"""Regression coverage for the September 25 screenshots and receipts."""
import ast
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from tests.support.exploration import ROOT, ControlledClock, ReplayDevice, artifact_path, frame, task_for
from tasks.dimensional_exploration.dimensional_exploration import DimensionalExploration
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import LOOT_BORDER
from tasks.dimensional_exploration.policy import Offer, RunProgress, choose_offer
from tasks.dimensional_exploration.heroes.recruitment import HeroCosts
from tasks.dimensional_exploration.recognition.vision import ExplorationVision, Hero, Resources
from tasks.dimensional_exploration.heroes.upgrade import RankedHero, UpgradeTarget, choose_upgrade, parse_upgrade_priority
from module.exception import RequestHumanTakeover
class ScreenshotRegressions(unittest.TestCase):
    def test_captured_preview_types_use_templates_without_title_ocr(self):
        for suffix, kind in (
                ('20260922-081603-149', 'battle'), ('20260922-081805-941', 'elite'),
                ('20260922-082932-782', 'boss'), ('20260923-233512-883', 'boss'),
                ('20260925-113034-953', 'rest'), ('20260925-113935-872', 'rest'),
                ('20260922-081725-462', 'supply'), ('20260922-081803-041', 'event')):
            with self.subTest(suffix=suffix):
                task, vision, clicks = task_for(suffix)
                vision.text = Mock(side_effect=AssertionError('Captured preview titles must use templates'))
                self.assertEqual(vision.preview_kind(), kind)
                task._node_selected, task._node_kind = True, kind
                self.assertTrue(task.handle_preview(vision))
                self.assertEqual(clicks, ['NODE_ENTER'])

    def test_missing_wrong_or_ambiguous_preview_cannot_enter(self):
        for suffix in ('20260922-081413-154', '20260922-081805-941'):
            task, vision, clicks = task_for(suffix)
            task._node_selected, task._node_kind = True, 'event'
            vision.text = Mock(side_effect=AssertionError('No OCR fallback for covered types'))
            task.handle_map = Mock(return_value=False)
            self.assertFalse(task.handle_preview(vision))
            self.assertEqual(clicks, [])
            task.handle_map.assert_called_once_with(vision)
        task, vision, clicks = task_for('20260922-081413-154')
        task._node_selected, task._node_kind = True, None
        task.handle_map = Mock(return_value=False)
        self.assertFalse(task.handle_preview(vision))
        self.assertEqual(clicks, [])
        task, vision, clicks = task_for('20260922-081603-149')
        task._node_selected, task._node_kind = True, 'battle'
        with patch.dict(vision.PREVIEW_ASSETS, {'other': vision.PREVIEW_ASSETS['battle']}):
            self.assertIsNone(vision.preview_kind())
            task.handle_map = Mock(return_value=False)
            self.assertFalse(task.handle_preview(vision))
        self.assertEqual(clicks, [])

    def test_shop_preview_ocr_is_only_used_when_no_known_template_matches(self):
        task, vision, clicks = task_for('20260922-081413-154')
        task._node_selected, task._node_kind = True, 'shop'
        task.handle_map = Mock(return_value=False)
        vision.text = Mock(return_value='商店')
        self.assertTrue(task.handle_preview(vision))
        self.assertEqual(clicks, ['NODE_ENTER'])
        # This is a simulated missing shop capture, not an asset positive.
        clicks.clear()
        vision.text.return_value = '未知'
        self.assertFalse(task.handle_preview(vision))
        self.assertEqual(clicks, [])
        task, vision, clicks = task_for('20260922-081805-941')
        task._node_selected, task._node_kind = True, 'shop'
        vision.text = Mock(return_value='商店')
        task.handle_map = Mock(return_value=False)
        self.assertFalse(task.handle_preview(vision))
        vision.text.assert_not_called()
        self.assertEqual(clicks, [])

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
        for path in (ROOT / 'tasks' / 'dimensional_exploration').rglob('*.py'):
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


class UpgradePriorityRegressions(unittest.TestCase):
    @staticmethod
    def hero(name, rank=1, x=398, y=205):
        return RankedHero(name, rank, (x, y, x + 80, y + 23))

    @classmethod
    def picker(cls, heroes=None, count=None):
        task, vision, clicks = task_for('20260923-232309-289')
        task.config.DimensionalExploration_UpgradePriority = '起源拉斯=3\n罪戾的安洁莉卡=5'
        heroes = tuple(heroes or [cls.hero('起源拉斯'), cls.hero('罪戾的安洁莉卡', y=98)])
        vision.upgrade_roster = Mock(return_value=(heroes, count or len(heroes)))
        vision.selected_hero = Mock(return_value='')
        vision.bright_text = Mock(return_value=True)
        task.appear = Mock(return_value=True)
        return task, vision, clicks

    def test_parser_preserves_order_and_rejects_invalid_names_ranks_and_duplicates(self):
        self.assertEqual(parse_upgrade_priority(' 起源拉斯 = 3 > 罪戾的安洁莉卡：5 ',
                                               ('起源拉斯', '罪戾的安洁莉卡')),
                         (UpgradeTarget('起源拉斯', 3), UpgradeTarget('罪戾的安洁莉卡', 5)))
        self.assertEqual(parse_upgrade_priority('', ()), ())
        for value in (0, False, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_upgrade_priority(value, ())
        for text in ('拉斯=3', '起源拉斯=0', '起源拉斯=6', '起源拉斯=-1', '起源拉斯=3.0',
                     '起源拉斯', '=3', '起源拉斯=3\n起源拉斯=5', '罪戾的安洁莉加=3'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_upgrade_priority(text, ('起源拉斯', '罪戾的安洁莉卡'))

    def test_preflight_fails_before_any_screenshot_or_click(self):
        task, _, clicks = self.picker()
        task.config.Emulator_PackageName = 'com.stove.epic7.google'
        task.config.DimensionalExploration_UpgradePriority = '亚敏=5'
        task.device.screenshot = Mock(side_effect=AssertionError('Preflight must happen before device work'))
        task.device.save_screenshot = Mock(side_effect=AssertionError('Preflight must not save a device frame'))
        with self.assertRaisesRegex(RequestHumanTakeover, '亚敏.*not in the hero priority roster'):
            task.run()
        self.assertEqual(clicks, [])
        task.device.screenshot.assert_not_called()
        task.device.save_screenshot.assert_not_called()

    def test_goals_finish_in_order_before_filling_priority_roster_to_five(self):
        targets = (UpgradeTarget('起源拉斯', 3), UpgradeTarget('罪戾的安洁莉卡', 5))
        preferred = ('起源拉斯', '罪戾的安洁莉卡')
        for first, second, expected in ((1, 1, '起源拉斯'), (2, 4, '起源拉斯'),
                                        (3, 1, '罪戾的安洁莉卡'), (3, 5, '起源拉斯'),
                                        (5, 5, '名单外英雄')):
            heroes = (self.hero('名单外英雄'), self.hero('罪戾的安洁莉卡', second),
                      self.hero('起源拉斯', first))
            self.assertEqual(choose_upgrade(heroes, targets, preferred).name, expected)

    def test_absent_goals_fill_listed_heroes_before_other_heroes_and_skip_maxed(self):
        targets = (UpgradeTarget('起源拉斯', 3),)
        heroes = (self.hero('名单外英雄'), self.hero('罪戾的安洁莉卡', 4))
        self.assertEqual(choose_upgrade(heroes, targets, ('罪戾的安洁莉卡',)).name, '罪戾的安洁莉卡')
        maxed = (self.hero('罪戾的安洁莉卡', 5),)
        self.assertIsNone(choose_upgrade(maxed, targets, ('罪戾的安洁莉卡',)))
        self.assertEqual(choose_upgrade(heroes, (), ('罪戾的安洁莉卡',)).name, '罪戾的安洁莉卡')

    def test_real_unselected_and_selected_pickers_read_names_and_rank_fields(self):
        for suffix in ('20260923-232309-289', '20260923-232312-127'):
            vision = ExplorationVision(frame(suffix))
            heroes, count = vision.upgrade_roster()
            self.assertEqual(count, 5)
            self.assertEqual([h.rank for h in heroes], [1] * 5)
            self.assertEqual([h.name for h in heroes], ['罪戾的安洁莉卡', '起源拉斯', '转学生艾庭',
                                                     '智武', '保健室老师律荷'])
            self.assertEqual(heroes[1].area[:2], (398, 205))
        self.assertIsNone(ExplorationVision(frame('20260923-233117-847')).upgrade_roster())

    def test_supplied_mixed_rank_roster_reads_first_one_and_origin_two(self):
        vision = ExplorationVision(frame('20261004-151606-257'))
        heroes, count = vision.upgrade_roster()
        self.assertEqual(count, 4)
        self.assertEqual([(h.name, h.rank) for h in heroes], [
            ('赛珂兰特', 1), ('罪戾的安洁莉卡', 1), ('起源拉斯', 2), ('保健室老师律荷', 1)])
        chosen = choose_upgrade(heroes, (UpgradeTarget('起源拉斯', 3),), ('罪戾的安洁莉卡', '起源拉斯'))
        self.assertEqual(chosen.name, '起源拉斯')

    def test_missing_rank_pixels_do_not_default_to_one_or_choose_a_hero(self):
        from module.base.utils import area_offset
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import OCR_UPGRADE_RANK
        image = frame('20261004-151606-257')
        # Occlude only the complete numeric field, keeping its rank anchor and
        # the entire real roster intact. A blank must never be guessed as 1.
        for offset in ((0, 0), (0, 210)):
            obscured = image.copy()
            x1, y1, x2, y2 = area_offset(OCR_UPGRADE_RANK.area, offset)
            obscured[y1:y2, x1:x2] = 0
            task, _, clicks = task_for('20261004-151606-257')
            task.device.image = obscured
            vision = ExplorationVision(obscured)
            self.assertEqual(vision.state(), 'upgrade')
            self.assertIsNone(vision.upgrade_roster())
            self.assertFalse(task.handle_rest_hero(vision))
            self.assertEqual(clicks, [])
            task.device.swipe.assert_not_called()

    def test_mixed_rank_main_loop_retries_goal_hero_without_scrolling(self):
        suffixes = ['20261004-151606-257'] * 3 + ['20260922-081554-067', '20260922-081153-551']
        with ControlledClock() as clock:
            task, vision, _ = task_for(suffixes[0])
            task.config.Emulator_PackageName = 'com.stove.epic7.google'
            task.config.DimensionalExploration_UpgradePriority = '起源拉斯=3\n罪戾的安洁莉卡=5'
            heroes, _ = vision.upgrade_roster()
            expected_area = next(h.area for h in heroes if h.name == '起源拉斯')
            device = ReplayDevice(suffixes, expected_clicks=['RestHeroSelect'] * 2)
            device.click = Mock(wraps=device.click)
            task.device = device
            task.action_ready = DimensionalExploration.action_ready.__get__(task)
            task.click_action = DimensionalExploration.click_action.__get__(task)
            task.progress, task.target = RunProgress(completed=1), 1
            task.handle_map = Mock(return_value=False)
            task.handle_ui_recovery = Mock(return_value=False)
            screenshot = device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            # This capture proves roster recognition and repeated selection.
            # No selected detail/result was supplied: the old map and lobby
            # explicitly simulate a positive exit, not a completed upgrade.
            task.explore()
            device.assert_actions_complete()
            self.assertEqual([call.args[0].area for call in device.click.call_args_list], [expected_area] * 2)
            device.swipe.assert_not_called()
            self.assertEqual(task._upgrade_inventory, [])
            self.assertIsNone(task._upgrade_pending)

    def test_unreadable_rank_or_name_cannot_authorize_selection(self):
        vision = ExplorationVision(frame('20260923-232309-289'))
        number = vision.number
        for bad in (None, 0, 6):
            vision.number = lambda area, maximum=99999, bad=bad: (
                number(area, maximum) if hasattr(area, 'name') and area.name == 'OCR_ROSTER_COUNT' else bad)
            self.assertIsNone(vision.upgrade_roster())
        task, mocked, clicks = self.picker()
        mocked.upgrade_roster.return_value = None
        self.assertFalse(task.handle_rest_hero(mocked))
        self.assertEqual(clicks, [])
        task.device.swipe.assert_not_called()

    def test_clipped_name_field_cannot_count_as_a_complete_hero(self):
        vision = ExplorationVision(frame('20260923-232309-289'))
        # The right-edge row is deliberately simulated: its full name field
        # lies outside the list, even if OCR returns a plausible clipped name.
        vision.number = Mock(side_effect=(2, 1, 1))
        vision.tokens = Mock(side_effect=(
            [SimpleNamespace(ocr_text='起源拉斯', box=(395, 94, 475, 125))],
            [SimpleNamespace(ocr_text='罪戾的安', box=(1235, 94, 1278, 125))],
        ))
        with patch('tasks.dimensional_exploration.recognition.vision.multi_match', return_value=[
                (360, 136, 380, 155), (1200, 136, 1220, 155)]):
            heroes, count = vision.upgrade_roster()
        self.assertEqual(count, 2)
        self.assertEqual([h.name for h in heroes], ['起源拉斯'])
        self.assertEqual(vision.tokens.call_count, 1)

    def test_target_selection_and_confirmation_retry_without_switching_hero(self):
        task, vision, clicks = self.picker()
        task.handle_rest_hero(vision)
        task.handle_rest_hero(vision)
        self.assertEqual(clicks, ['RestHeroSelect'] * 2)
        vision.selected_hero.return_value = '起源拉斯'
        task.handle_rest_hero(vision)
        task.handle_rest_hero(vision)
        self.assertEqual(clicks[-2:], ['RestHeroConfirm'] * 2)
        self.assertEqual(task._upgrade_pending, ('起源拉斯', 1))
        vision.upgrade_roster.return_value = ((self.hero('起源拉斯', 2), self.hero('罪戾的安洁莉卡')), 2)
        self.assertFalse(task.handle_rest_hero(vision))
        self.assertEqual(len(clicks), 4)

    def test_restart_selected_wrong_hero_selects_configured_goal_first(self):
        task, vision, clicks = self.picker()
        vision.selected_hero.return_value = '罪戾的安洁莉卡'
        task.handle_rest_hero(vision)
        self.assertEqual(clicks, ['RestHeroSelect'])
        self.assertEqual(task._upgrade_choice, '起源拉斯')

    def test_partial_roster_scans_before_fallback_and_returns_to_best_hero(self):
        fallback = self.hero('罪戾的安洁莉卡')
        task, vision, clicks = self.picker([fallback], count=3)
        task.config.DimensionalExploration_UpgradePriority = '起源拉斯=3'
        self.assertFalse(task.handle_rest_hero(vision))
        self.assertTrue(task.handle_rest_hero(vision))
        self.assertEqual(clicks, [])
        vision.upgrade_roster.return_value = ((self.hero('名单外甲'), self.hero('名单外乙', y=308)), 3)
        self.assertFalse(task.handle_rest_hero(vision))
        self.assertTrue(task.handle_rest_hero(vision))
        self.assertEqual(task._upgrade_choice, '罪戾的安洁莉卡')
        self.assertEqual(clicks, [])
        vision.upgrade_roster.return_value = ((fallback,), 3)
        self.assertFalse(task.handle_rest_hero(vision))
        self.assertTrue(task.handle_rest_hero(vision))
        self.assertEqual(clicks, ['RestHeroSelect'])
        self.assertEqual(task.device.swipe.call_count, 2)

    def test_goal_on_later_page_wins_over_visible_fallback(self):
        task, vision, clicks = self.picker([self.hero('罪戾的安洁莉卡')], count=2)
        task.handle_rest_hero(vision)
        task.handle_rest_hero(vision)
        vision.upgrade_roster.return_value = ((self.hero('起源拉斯'),), 2)
        task.handle_rest_hero(vision)
        task.handle_rest_hero(vision)
        self.assertEqual(clicks, ['RestHeroSelect'])
        self.assertEqual(task._upgrade_choice, '起源拉斯')

    def test_incomplete_boundary_and_search_limit_stop_without_fallback(self):
        task, vision, clicks = self.picker([self.hero('罪戾的安洁莉卡')], count=2)
        with self.assertRaisesRegex(RequestHumanTakeover, 'missing heroes'):
            for _ in range(7):
                task.handle_rest_hero(vision)
        self.assertEqual(clicks, [])
        task, vision, clicks = self.picker([self.hero('罪戾的安洁莉卡')], count=2)
        task._upgrade_count = 2
        task._upgrade_swipes = 30
        task.handle_rest_hero(vision)
        with self.assertRaisesRegex(RequestHumanTakeover, 'scan limit reached'):
            task.handle_rest_hero(vision)
        self.assertEqual(clicks, [])

    def test_all_maxed_returns_and_leaves_rest_then_new_room_clears_cache(self):
        task, vision, clicks = self.picker([self.hero('起源拉斯', 5)])
        self.assertTrue(task.handle_rest_hero(vision))
        self.assertTrue(task._upgrade_exhausted)
        self.assertEqual(clicks, ['BACK'])
        rest = ExplorationVision(frame('20260923-232305-766'))
        task.device.image = rest.image
        self.assertTrue(task.handle_rest(rest))
        self.assertEqual(clicks[-1], 'ROOM_LEAVE')
        task.observe_upgrade('unknown')
        self.assertTrue(task._upgrade_exhausted)
        task.observe_upgrade('map')
        self.assertFalse(task._upgrade_exhausted)
        self.assertEqual(task._upgrade_inventory, [])

    def test_gui_source_exposes_goal_input_and_all_locales_have_useful_help(self):
        import json
        from module.config.config_updater import ConfigGenerator
        from module.config.config_generated import GeneratedConfig
        settings = ConfigGenerator().args['DimensionalExploration']['DimensionalExploration']
        self.assertEqual(settings['UpgradePriority']['type'], 'textarea')
        self.assertEqual(settings['UpgradePriority']['value'], '')
        generated = json.loads((ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        self.assertEqual(generated['DimensionalExploration']['DimensionalExploration']['UpgradePriority'],
                         settings['UpgradePriority'])
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        default = template['DimensionalExploration']['DimensionalExploration']['UpgradePriority']
        self.assertEqual(default, GeneratedConfig.DimensionalExploration_UpgradePriority)
        # The framework stores a blank input as None. Both that generated
        # default and the GUI's raw empty string must enable list-only goals.
        for value in (default, ''):
            self.assertEqual(parse_upgrade_priority(value, ('起源拉斯',)), ())
            device = Mock()
            task = DimensionalExploration(GeneratedConfig(), device)
            task.config.DimensionalExploration_UpgradePriority = value
            self.assertEqual(task.validate_upgrade_priority(), ())
            self.assertEqual(device.mock_calls, [])
        for lang in ('zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'es-ES'):
            data = json.loads((ROOT / f'module/config/i18n/{lang}.json').read_text(encoding='utf-8'))
            label = data['DimensionalExploration']['UpgradePriority']
            self.assertTrue(label['name'])
            self.assertIn('=', label['help'])
            self.assertIn('5', label['help'])
            self.assertNotIn('DimensionalExploration.', label['help'])

    def test_main_loop_retries_rank_goal_selection_then_confirms_and_exits(self):
        suffixes = ['20260923-232309-289'] * 3 + ['20260923-232312-127'] * 3 + [
            '20260922-081554-067', '20260922-081153-551']
        with ControlledClock() as clock:
            task, _, _ = task_for(suffixes[0])
            task.config.Emulator_PackageName = 'com.stove.epic7.google'
            task.config.DimensionalExploration_UpgradePriority = '起源拉斯=3\n罪戾的安洁莉卡=5'
            device = ReplayDevice(suffixes, expected_clicks=['RestHeroSelect'] * 2 + ['RestHeroConfirm'])
            task.device = device
            task.action_ready = DimensionalExploration.action_ready.__get__(task)
            task.click_action = DimensionalExploration.click_action.__get__(task)
            task.progress, task.target = RunProgress(completed=1), 1
            task.handle_map = Mock(return_value=False)
            task.handle_ui_recovery = Mock(return_value=False)
            screenshot = device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            # No new rank-change/result capture was supplied. The old map
            # explicitly models the positive exit, without claiming a newly
            # captured upgrade. Missed clicks are represented by repeated frames.
            task.explore()
            device.assert_actions_complete()
            self.assertEqual(task._upgrade_inventory, [])
            self.assertIsNone(task._upgrade_pending)


if __name__ == '__main__':
    unittest.main()
