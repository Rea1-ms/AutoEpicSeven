"""Regressions from the September 26 on-device logs, without a real device."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tests.support.exploration import ControlledClock, artifact_path, frame, task_for
from module.config.config_manual import ManualConfig
from module.device.screenshot import Screenshot
from module.exception import RequestHumanTakeover
from tasks.dimensional_exploration.recruitment import HeroCosts
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import REWARD_CLOSE
from tasks.dimensional_exploration.vision import ExplorationVision, Hero
from tasks.dimensional_exploration.event import EventMemory, decide_event, event_branch
from tasks.dimensional_exploration.policy import EventChoice, RunProgress, choose_offer
from tasks.dimensional_exploration.sampling import choose_sample


class RuntimeRegressions(unittest.TestCase):
    def test_hero_ocr_stops_after_first_priority_without_reading_later_rows(self):
        full = ExplorationVision(frame('20260922-081352-852'))
        full.tokens = Mock(wraps=full.tokens)
        full.heroes()
        partial = ExplorationVision(frame('20260922-081352-852'))
        partial.tokens = Mock(wraps=partial.tokens)
        heroes = partial.heroes(stop_when=lambda hero: hero.name == '罪戾的安洁莉卡')
        self.assertEqual(heroes[-1].name, '罪戾的安洁莉卡')
        self.assertLess(partial.tokens.call_count, full.tokens.call_count)

    def test_shared_costs_migrate_without_losing_originals(self):
        root = artifact_path()
        config = root / 'config'
        config.mkdir(parents=True)
        original = '{"英雄甲": 4, "英雄乙": 2}\n'
        (config / 'dimensional_exploration_hero_costs_one.json').write_text(original, encoding='utf-8')
        (config / 'account.json').write_text('{}', encoding='utf-8')
        costs = HeroCosts.from_runtime(root)
        self.assertEqual(costs.values, {**HeroCosts().values, '英雄甲': 4, '英雄乙': 2})
        self.assertEqual(costs.path.parent, root / 'data' / 'dimensional_exploration')
        self.assertEqual([p.name for p in config.glob('*.json')], ['account.json'])
        archives = list((costs.path.parent / 'legacy').glob('*.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_text(encoding='utf-8'), original)
        self.assertEqual(HeroCosts.from_runtime(root).values, costs.values)

    def test_failure_screenshot_preserves_takeover_reason(self):
        task, _, _ = task_for()
        folder = artifact_path('nested/screenshots')
        config = ManualConfig()
        config.SCREEN_SHOT_SAVE_FOLDER = str(folder)
        saved = []
        device = SimpleNamespace(config=config, _last_save_time={}, image_save=saved.append)
        task.device.save_screenshot = lambda **kwargs: Screenshot.save_screenshot(device, **kwargs)
        with self.assertRaisesRegex(RequestHumanTakeover, '原始停止原因'):
            task.require_human('原始停止原因')
        self.assertEqual(len(saved), 1)
        self.assertTrue((folder / 'dimensional_exploration').is_dir())

    def test_selected_hero_confirms_without_rescanning_list(self):
        task, vision, clicks = task_for('20260926-181057-614')
        task._hero_budget = 5
        task._hero_selected = '智武'
        task._hero_selected_cost = 4
        vision.heroes = Mock(side_effect=AssertionError('selected pane must not rescan the list'))
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, ['HERO_CONFIRM'])
        task.device.swipe.assert_not_called()

    def test_top_map_arrows_choose_supply_and_event(self):
        for suffix, kind in [('20260926-125324-000', 'supply'), ('20260926-200142-567', 'event')]:
            task, vision, clicks = task_for(suffix)
            self.assertEqual(vision.state(), 'map')
            nodes = vision.nodes()
            self.assertEqual([node.kind for node in nodes], [kind])
            self.assertTrue(task.handle_map(vision))
            self.assertEqual(clicks, [f'ExplorationNode_{kind}'])

    def test_discount_uses_payable_price_not_crossed_out_price(self):
        task, vision, clicks = task_for('20260926-192103-665')
        offers = vision.offers()
        self.assertEqual([offer.price for offer in offers], [50, 85, 183, 180, 1, 57, 143, 108])
        task.handle_shop(vision)
        task.handle_shop(vision)
        self.assertEqual(task._pending_offer.index, 0)
        self.assertEqual(clicks, ['ExplorationShopOffer'])

    def test_event_disabled_option_is_not_available(self):
        vision = ExplorationVision(frame('20260926-201447-325'))
        self.assertEqual(vision.state(), 'event')
        choices = [choice for choice, _ in vision.event_choices()]
        self.assertEqual(len(choices), 3)
        self.assertEqual([choice.available for choice in choices], [True, True, False])

    def test_unclaimed_reward_modal_has_priority_over_victory(self):
        vision = ExplorationVision(frame('20260926-202057-530'))
        self.assertEqual(vision.state(), 'reward_leave')

    def test_quota_change_invalidates_selected_hero(self):
        task, vision, clicks = task_for('20260926-181057-614')
        task._hero_budget, task._hero_selected, task._hero_selected_cost = 5, '智武', 4
        task.config.DimensionalExploration_MagePriority = '智武'
        task.hero_costs.values['智武'] = 4
        vision.quota = Mock(return_value=(18, 20))
        vision.heroes = Mock(return_value=[Hero('其他英雄', 2, (400, 90, 550, 140))])
        vision.hero_signature = [('其他英雄', 2, 400, 90)]
        self.assertFalse(task.handle_hero(vision))
        self.assertIsNone(task._hero_selected)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, ['SelectExplorationHero'])
        self.assertEqual(task._hero_selected, '其他英雄')
        self.assertEqual(task._hero_selected_cost, 2)
        self.assertIsNone(task._recruit_pending)

    def test_modal_restart_cancels_then_confirmed_skip_can_leave(self):
        task, modal, clicks = task_for('20260926-202057-530')
        task.handle_reward_leave(modal)
        self.assertEqual(clicks, ['REWARD_LEAVE_CANCEL'])
        task._recruit_skipped = True
        reward = SimpleNamespace(state=lambda: 'victory', bright_text=lambda _: True,
                                 tokens=lambda _: [SimpleNamespace(ocr_text='招募英雄', box=(0, 0, 1, 1))])
        task.appear = Mock(return_value=True)
        task.handle_victory(reward)
        task.handle_reward_leave(modal)
        task.handle_reward_leave(modal)
        self.assertEqual(clicks, ['REWARD_LEAVE_CANCEL', 'VICTORY_CONTINUE',
                                  'REWARD_LEAVE_CONFIRM', 'REWARD_LEAVE_CONFIRM'])
        task.device.click_record_clear.assert_not_called()
        task.observe_state('map')
        self.assertFalse(task._reward_leave_authorized)

    def test_unreadable_reward_text_cannot_authorize_discard(self):
        task, _, clicks = task_for('20260925-113540-999')
        reward = SimpleNamespace(state=lambda: 'resume_rewards', bright_text=lambda _: True,
                                 tokens=lambda _: [])
        task.handle_victory(reward)
        task.handle_reward_leave(ExplorationVision(frame('20260926-202057-530')))
        self.assertEqual(clicks, ['RESUME_REWARDS_CONTINUE', 'REWARD_LEAVE_CANCEL'])

    def test_battle_hud_and_animation_keep_context_without_toggling_auto(self):
        task, manual, clicks = task_for('20260922-081430-868')
        blank = ExplorationVision(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertEqual(task.resolve_state(blank), 'unknown')
        self.assertEqual(manual.state(), 'battle')
        self.assertEqual(task.resolve_state(manual), 'battle')
        task.handle_battle(manual)
        task.device.image = blank.image
        for _ in range(4):
            self.assertEqual(task.resolve_state(blank), 'battle')
            task.handle_battle(blank)
        automatic = ExplorationVision(frame('20260922-081440-084'))
        task.device.image = automatic.image
        self.assertEqual(automatic.state(), 'battle')
        task.handle_battle(automatic)
        self.assertEqual(clicks, ['AUTO_COMBAT'])
        self.assertEqual(task.resolve_state(ExplorationVision(frame('20260922-081517-145'))), 'victory')
        self.assertEqual(task.resolve_state(blank), 'unknown')

    def test_prepare_click_retains_battle_during_loading(self):
        task, vision, clicks = task_for('20260922-081423-319')
        task.handle_prepare(vision)
        self.assertEqual(clicks, ['BATTLE_START'])
        self.assertEqual(task.resolve_state(ExplorationVision(np.zeros((720, 1280, 3), dtype=np.uint8))), 'battle')

    def test_repeated_frames_limit_expensive_work_but_new_state_is_immediate(self):
        with ControlledClock() as clock:
            task, _, _ = task_for('20260922-081517-145')
            task.progress, task.target = RunProgress(), 1
            task.handle_victory = Mock(return_value=False)
            task.handle_map = Mock(return_value=False)
            task.handle_ui_recovery = Mock(return_value=False)
            images = iter([task.device.image] * 9 + [frame('20260926-200142-567')])
            def screenshot():
                clock.advance(0.1)
                try:
                    task.device.image = next(images)
                except StopIteration:
                    raise RuntimeError('end of replay') from None
            task.device.screenshot = screenshot
            with self.assertRaisesRegex(RuntimeError, 'end of replay'):
                task.explore()
            self.assertEqual(task.handle_victory.call_count, 1)
            self.assertEqual(task.handle_map.call_count, 1)

    def test_entry_follows_template_offset_without_ocr(self):
        for offset in (0, 200):
            task, vision, _ = task_for('20260921-105748-548')
            if offset:
                image = np.zeros_like(vision.image)
                image[:, offset:] = vision.image[:, :-offset]
                task.device.image = image
            task.ui_page_appear = Mock(return_value=True)
            vision.tokens = Mock(side_effect=AssertionError('entry must not use OCR'))
            boxes = []
            task.click_action = lambda button: boxes.append(button.button) or True
            self.assertTrue(task.handle_entry(vision))
            self.assertEqual(len(boxes), 1)
            center = (boxes[0][0] + boxes[0][2]) / 2
            self.assertTrue(43 + offset <= center < 170 + offset)
            task.device.swipe.assert_not_called()

    def test_disabled_catalog_and_sampling_options_are_never_selected(self):
        vision = ExplorationVision(frame('20260922-081528-636'))
        choices = [choice for choice, _ in vision.event_choices()]
        balances = dict(cores=20, fragments=220, life=3, loot=2, dice=2)
        first = decide_event(choices, **balances)
        disabled = [replace(c, available=False) if c.index == first.choice.index else c for c in choices]
        self.assertNotEqual(decide_event(disabled, **balances).choice.index, first.choice.index)
        memory = EventMemory()
        memory.begin(first.branch)
        self.assertIsNone(decide_event(disabled, memory=memory, **balances))
        sample = choose_sample([EventChoice(0, '获得50个次元碎片', available=False), EventChoice(1, '径直离开')], balances)
        self.assertEqual(sample.choice.index, 1)

    def test_discounted_item_still_obeys_exact_budget(self):
        vision = ExplorationVision(frame('20260926-192103-665'))
        offer = vision.offers()[7]
        self.assertIsNone(choose_offer([offer], 107, 3, 3))
        self.assertEqual(choose_offer([offer], 108, 3, 3).index, 7)


class RecruitmentPriorityRegressions(unittest.TestCase):
    def picker(self):
        task, _, clicks = task_for('20260922-081352-852')
        task.config.DimensionalExploration_HealerPriority = '英雄甲 > 英雄乙 > 英雄丙'
        rows = [Hero('英雄甲', 3, (350, 150, 460, 180)),
                Hero('英雄乙', 2, (350, 250, 460, 280)),
                Hero('英雄丙', 1, (350, 350, 460, 380))]
        vision = SimpleNamespace(quota=Mock(return_value=(0, 10)), text=lambda _: '精灵师',
                                 selected_hero=lambda: task._hero_selected, bright_text=lambda _: True,
                                 hero_signature=[], rows_read=[])

        def heroes(stop_when=None):
            selected = []
            vision.hero_signature = []
            vision.rows_read = []
            for hero in rows:
                vision.rows_read.append(hero.name)
                vision.hero_signature.append((hero.name, hero.cost, *hero.area[:2]))
                selected.append(hero)
                if stop_when and stop_when(hero):
                    break
            return selected

        vision.heroes = Mock(side_effect=heroes)
        return task, vision, clicks

    def test_recruited_first_priority_promotes_next_without_reading_later_rows(self):
        task, vision, clicks = self.picker()
        task._recruited_heroes = ['英雄甲']
        self.assertFalse(task.handle_hero(vision))
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '英雄乙')
        self.assertEqual(vision.rows_read, ['英雄甲', '英雄乙'])
        self.assertEqual(clicks, ['SelectExplorationHero'])
        task.device.swipe.assert_not_called()

    def test_known_unaffordable_first_priority_promotes_affordable_second(self):
        task, vision, _ = self.picker()
        task.hero_costs.values['英雄甲'] = 3
        vision.quota.return_value = (8, 10)
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertEqual(task._hero_selected, '英雄乙')
        self.assertEqual(vision.rows_read, ['英雄甲', '英雄乙'])
        task.device.swipe.assert_not_called()

    def test_confirmation_requires_exact_stable_quota_and_survives_restart(self):
        task, vision, clicks = self.picker()
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks[-1], 'HERO_CONFIRM')
        self.assertEqual(task._recruited_heroes, [])
        self.assertIsNotNone(task.config.DimensionalExplorationRuntime_Session['recruitment']['pending'])
        restored = type(task)(task.config, task.device)
        for state, quota in [('hero', (0, 10)), ('map', (3, 11)), ('unknown', (3, 10)), ('map', None)]:
            vision.quota.return_value = quota
            restored.observe_recruitment(state, vision)
            self.assertEqual(restored._recruited_heroes, [])
        vision.quota.return_value = (3, 10)
        restored.observe_recruitment('recruitment', vision)
        self.assertEqual(restored._recruited_heroes, [])
        restored.observe_recruitment('recruitment', vision)
        self.assertEqual(restored._recruited_heroes, ['英雄甲'])
        restored.observe_recruitment('recruitment', vision)
        self.assertEqual(restored._recruited_heroes, ['英雄甲'])
        self.assertIsNone(restored._recruit_pending)
        again = type(task)(task.config, task.device)
        self.assertEqual(again._recruited_heroes, ['英雄甲'])

    def test_unsent_confirmation_and_pending_receipt_cannot_advance_priorities(self):
        task, vision, _ = self.picker()
        task.handle_hero(vision)
        task.handle_hero(vision)
        task.click_action = Mock(return_value=False)
        self.assertFalse(task.handle_hero(vision))
        self.assertIsNone(task._recruit_pending)
        task.click_action.return_value = True
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertEqual(task._recruited_heroes, [])
        vision.quota.return_value = (3, 10)
        vision.heroes.side_effect = AssertionError('unconfirmed receipt must not choose another hero')
        self.assertFalse(task.handle_hero(vision))
        self.assertEqual(task._recruit_pending['name'], '英雄甲')

    def test_completed_list_skips_optional_recruitment_but_initial_can_fill(self):
        task, vision, clicks = self.picker()
        task._recruited_heroes = ['英雄甲', '英雄乙', '英雄丙']
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, ['BACK'])
        vision.heroes.assert_not_called()
        task._initial_recruitment = True
        task._recruited_heroes = ['英雄甲', '英雄乙']
        task.config.DimensionalExploration_HealerPriority = '英雄甲 > 英雄乙'
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertEqual(task._hero_best[1], '英雄丙')

    def test_batch_progress_keeps_roster_but_new_run_clears_it(self):
        task, _, _ = self.picker()
        task._recruited_heroes = ['英雄甲']
        task.save_recruitment()
        task.progress, task.target = RunProgress(), 2
        task.save_progress()
        self.assertEqual(task.config.DimensionalExplorationRuntime_Session['recruitment']['heroes'], ['英雄甲'])
        task.observe_state('map')
        self.assertEqual(task._recruited_heroes, ['英雄甲'])
        task.observe_state('start_supply')
        self.assertEqual(task._recruited_heroes, [])
        self.assertIsNone(task._recruit_pending)
        self.assertEqual(task.config.DimensionalExplorationRuntime_Session['recruitment']['heroes'], [])
        # A restart can miss the entire setup: the persisted settlement latch
        # plus a recognized map must also clear the previous run's roster.
        task._recruited_heroes = ['英雄甲']
        task.progress.settlement_seen = True
        task.device.image = frame('20260922-081522-090')
        task.handle_map = Mock(side_effect=RuntimeError('stop after new-run observation'))
        with self.assertRaisesRegex(RuntimeError, 'stop after new-run observation'):
            task.explore()
        self.assertEqual(task._recruited_heroes, [])
        self.assertFalse(task.progress.settlement_seen)


class RecruitmentViewportRegressions(unittest.TestCase):
    def test_scrolled_left_column_keeps_complete_names_and_costs(self):
        vision = ExplorationVision(frame('20261003-112531-836'))
        self.assertEqual(vision.state(), 'hero')
        heroes = vision.heroes()
        self.assertEqual(len(heroes), 11)
        values = {h.name: h.cost for h in heroes}
        for name in ('赛珂兰特', '洁若米亚', '和英', '红丸', '莱娅'):
            self.assertEqual(values[name], 3)
        self.assertTrue(all(h.area[0] >= 322 for h in heroes))

    def test_right_column_with_clipped_costs_is_not_recruited(self):
        vision = ExplorationVision(frame('20261003-112536-124'))
        heroes = vision.heroes()
        self.assertEqual(len(heroes), 10)
        self.assertNotIn('赛珂兰特', {h.name for h in heroes})
        self.assertEqual(next(h.cost for h in heroes if h.name == '狮心王洁若米亚'), 4)

    def test_name_and_cost_fields_must_fit_before_ocr_clamping(self):
        vision = ExplorationVision(frame('20261003-112531-836'))
        vision.tokens = Mock(side_effect=AssertionError('clipped fields must not be read'))
        vision.number = Mock(side_effect=AssertionError('clipped costs must not be guessed'))
        for rectangle in ((470, 133, 490, 159), (1230, 133, 1250, 159)):
            with self.subTest(rectangle=rectangle), patch(
                    'tasks.dimensional_exploration.vision.multi_match', return_value=[rectangle]):
                self.assertEqual(vision.heroes(), [])
        vision.tokens.assert_not_called()
        vision.number.assert_not_called()

    def test_visible_left_priority_retries_selection_without_swiping(self):
        task, vision, clicks = task_for('20261003-112531-836')
        task.config.DimensionalExploration_WarriorPriority = '洁若米亚'
        self.assertFalse(task.handle_hero(vision))
        self.assertTrue(task.handle_hero(vision))
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '洁若米亚')
        self.assertEqual(task._hero_selected_cost, 3)
        self.assertEqual(clicks, ['SelectExplorationHero'] * 2)
        self.assertIsNone(task._recruit_pending)
        self.assertEqual(task._recruited_heroes, [])
        task.device.swipe.assert_not_called()

    def test_repeated_boundary_frames_end_missing_priority_search(self):
        task, vision, clicks = task_for('20261003-112531-836')
        task.config.DimensionalExploration_WarriorPriority = '未出现的目标英雄'
        for _ in range(6):
            task.handle_hero(vision)
        self.assertEqual(task.device.swipe.call_count, 2)
        self.assertEqual(clicks, ['SelectExplorationHero'])
        self.assertEqual(task._hero_selected, '赛珂兰特')
        self.assertEqual(task._hero_selected_cost, 3)
        self.assertEqual(task._recruited_heroes, [])
        self.assertIsNone(task._recruit_pending)


if __name__ == '__main__':
    unittest.main()




class RecruitmentBudgetRegressions(unittest.TestCase):
    def ranger(self):
        task, vision, clicks = task_for('20261004-120409-871')
        task.config.DimensionalExploration_RangerPriority = '海边的维尔萝娜 > 伊赛丽亚'
        task.hero_costs.values.update({'海边的维尔萝娜': 3, '伊赛丽亚': 3})
        return task, vision, clicks

    def test_event_ticket_opens_recruitment_instead_of_discarding_reward(self):
        task, vision, clicks = task_for('20261004-120256-056')
        self.assertEqual(vision.state(), 'resume_rewards')
        self.assertTrue(task.handle_victory(vision))
        self.assertEqual(clicks, ['ClaimExplorationBattleReward'])
        self.assertFalse(task._reward_leave_authorized)

    def test_ranger_rows_exclude_disabled_prices_and_keep_two_point_choices(self):
        vision = ExplorationVision(frame('20261004-120409-871'))
        heroes = vision.heroes()
        self.assertEqual([(h.name, h.cost) for h in heroes], [
            ('尤贝乌斯', 2), ('贝洛妮卡', 2), ('莉珈', 2), ('尤贝乌斯', 2)])
        prices = {name: cost for name, cost, *_ in vision.hero_signature}
        self.assertEqual(prices['伊赛丽亚'], 3)
        self.assertEqual(prices['兰蒂'], 3)
        # The last column's cost crop is clipped. Preserve the same strict
        # viewport rule instead of pretending the visible red digit is a
        # complete OCR field or weakening recognition for this repair.
        self.assertNotIn('海边的维尔萝娜', prices)
        self.assertNotIn('海边的维尔萝娜', {h.name for h in heroes})

    def test_two_points_choose_visible_ranger_outside_unaffordable_list(self):
        task, vision, clicks = self.ranger()
        self.assertEqual(vision.state(), 'hero')
        self.assertEqual(vision.quota(), (13, 15))
        self.assertFalse(task.handle_hero(vision))
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '尤贝乌斯')
        self.assertEqual(task._hero_selected_cost, 2)
        self.assertEqual(clicks, ['SelectExplorationHero'])
        self.assertFalse(task._recruit_skipped)
        self.assertIsNone(task._recruit_pending)
        task.device.swipe.assert_not_called()

    def test_fallback_selection_retries_and_needs_exact_quota_receipt(self):
        task, vision, clicks = self.ranger()
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, ['SelectExplorationHero'] * 2)
        self.assertIsNone(task._recruit_pending)
        vision.selected_hero = Mock(return_value='尤贝乌斯')
        vision.bright_text = Mock(return_value=True)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks[-1], 'HERO_CONFIRM')
        self.assertEqual(task._recruit_pending, {'name': '尤贝乌斯', 'cost': 2, 'quota': [13, 15]})
        self.assertEqual(task._recruited_heroes, [])
        task.observe_recruitment('hero', vision)
        self.assertEqual(task._recruited_heroes, [])
        completed = ExplorationVision(frame('20261004-120450-584'))
        self.assertEqual(completed.state(), 'reward')
        self.assertEqual(completed.quota(), (15, 15))
        # The result popup alone is not an expense receipt. The two later
        # event frames explicitly show the same exact two-point increase.
        task.observe_recruitment('reward', completed)
        self.assertEqual(task._recruited_heroes, [])
        self.assertTrue(task.click_action(REWARD_CLOSE))
        narration = ExplorationVision(frame('20261004-120454-897'))
        self.assertEqual(narration.state(), 'event')
        task.observe_recruitment('event', narration)
        self.assertEqual(task._recruited_heroes, [])
        task.observe_recruitment('event', narration)
        task.observe_recruitment('event', narration)
        self.assertEqual(task._recruited_heroes, ['尤贝乌斯'])
        self.assertIsNone(task._recruit_pending)
        self.assertEqual(clicks[-1], 'REWARD_CLOSE')

    def test_newly_read_unaffordable_prices_keep_current_affordable_candidate(self):
        task, vision, clicks = self.ranger()
        task.config.DimensionalExploration_RangerPriority = '伊赛丽亚 > 兰蒂'
        for name in ('伊赛丽亚', '兰蒂'):
            task.hero_costs.values.pop(name, None)
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '尤贝乌斯')
        self.assertEqual(task.hero_costs.values['伊赛丽亚'], 3)
        self.assertEqual(task.hero_costs.values['兰蒂'], 3)
        self.assertEqual(clicks, ['SelectExplorationHero'])
        task.device.swipe.assert_not_called()

    def test_affordable_priority_still_precedes_fallback(self):
        task, vision, clicks = self.ranger()
        task.config.DimensionalExploration_RangerPriority = '莉珈'
        task.hero_costs.values['莉珈'] = 2
        task.handle_hero(vision)
        task.handle_hero(vision)
        self.assertEqual(task._hero_selected, '莉珈')
        self.assertEqual(clicks, ['SelectExplorationHero'])
        task.device.swipe.assert_not_called()

    def test_recruited_fallback_is_excluded_and_failed_click_cannot_confirm(self):
        task, vision, clicks = self.ranger()
        task._recruited_heroes = ['尤贝乌斯']
        task.handle_hero(vision)
        task.click_action = Mock(return_value=False)
        self.assertFalse(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '贝洛妮卡')
        self.assertIsNone(task._recruit_pending)
        task.click_action.assert_called_once()
        self.assertEqual(clicks, [])
        task.click_action = lambda button: clicks.append(str(button)) or True
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, ['SelectExplorationHero'])
        self.assertIsNone(task._recruit_pending)
        task.device.swipe.assert_not_called()

    def test_late_expensive_price_returns_to_previously_seen_affordable_hero(self):
        task, _, clicks = self.ranger()
        task.config.DimensionalExploration_RangerPriority = '未知目标英雄'
        vision = SimpleNamespace(quota=lambda: (13, 15), text=lambda _: '射手招募券',
                                 selected_hero=lambda: '', bright_text=lambda _: True,
                                 hero_signature=[('其他英雄', 2, 400, 90)],
                                 heroes=Mock(return_value=[Hero('其他英雄', 2, (400, 90, 550, 140))]))
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, [])
        self.assertEqual(task.device.swipe.call_count, 1)
        vision.hero_signature = [('未知目标英雄', 3, 400, 90)]
        vision.heroes.return_value = []
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertTrue(task._hero_returning)
        self.assertEqual(task.device.swipe.call_args.args, ((470, 355), (798, 355)))
        vision.hero_signature = [('其他英雄', 2, 400, 90)]
        vision.heroes.return_value = [Hero('其他英雄', 2, (400, 90, 550, 140))]
        task.handle_hero(vision)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '其他英雄')
        self.assertEqual(clicks, ['SelectExplorationHero'])

    def test_unknown_missing_target_keeps_affordable_fallback_at_search_boundary(self):
        task, _, clicks = self.ranger()
        task.config.DimensionalExploration_RangerPriority = '未知目标英雄'
        vision = SimpleNamespace(quota=lambda: (13, 15), text=lambda _: '射手招募券',
                                 selected_hero=lambda: '', bright_text=lambda _: True,
                                 hero_signature=[('其他英雄', 2, 400, 90)],
                                 heroes=Mock(return_value=[Hero('其他英雄', 2, (400, 90, 550, 140))]))
        for _ in range(5):
            task.handle_hero(vision)
        self.assertEqual(clicks, [])
        self.assertEqual(task.device.swipe.call_count, 2)
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(clicks, ['SelectExplorationHero'])
        self.assertEqual(task._hero_selected, '其他英雄')
        self.assertEqual(task._hero_selected_cost, 2)
        self.assertIsNone(task._recruit_pending)

    def test_empty_final_view_returns_to_unlisted_fallback_even_with_unknown_target_cost(self):
        task, _, clicks = self.ranger()
        task.config.DimensionalExploration_RangerPriority = '未知目标英雄'
        vision = SimpleNamespace(quota=lambda: (13, 15), text=lambda _: '射手招募券',
                                 selected_hero=lambda: '', bright_text=lambda _: True,
                                 hero_signature=[('其他英雄', 2, 400, 90)],
                                 heroes=Mock(return_value=[Hero('其他英雄', 2, (400, 90, 550, 140))]))
        task.handle_hero(vision)
        task.handle_hero(vision)
        vision.hero_signature = [('其他昂贵英雄', 3, 400, 90)]
        vision.heroes.return_value = []
        for _ in range(6):
            task.handle_hero(vision)
        self.assertTrue(task._hero_returning)
        self.assertEqual(clicks, [])
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task.device.swipe.call_args.args, ((470, 355), (798, 355)))
        vision.hero_signature = [('其他英雄', 2, 400, 90)]
        vision.heroes.return_value = [Hero('其他英雄', 2, (400, 90, 550, 140))]
        self.assertFalse(task.handle_hero(vision))
        self.assertTrue(task.handle_hero(vision))
        self.assertEqual(task._hero_selected, '其他英雄')
        self.assertEqual(clicks, ['SelectExplorationHero'])
        self.assertIsNone(task._recruit_pending)

    def test_zero_quota_skips_known_hero_only_event_without_continue_cancel_loop(self):
        task, picker, clicks = self.ranger()
        picker.quota = Mock(return_value=(15, 15))
        task.event_memory.begin(event_branch('narrow_passage.ranger'))
        self.assertTrue(task.handle_hero(picker))
        self.assertEqual(clicks, ['BACK'])
        reward = ExplorationVision(frame('20261004-120256-056'))
        self.assertTrue(task.handle_victory(reward))
        self.assertTrue(task._reward_leave_authorized)
        modal = ExplorationVision(frame('20260926-202057-530'))
        task.handle_reward_leave(modal)
        task.handle_reward_leave(modal)
        self.assertEqual(clicks, ['BACK', 'RESUME_REWARDS_CONTINUE', 'REWARD_LEAVE_CONFIRM',
                                  'REWARD_LEAVE_CONFIRM'])
        task.observe_state('map')
        self.assertFalse(task._recruit_skipped)
        self.assertFalse(task._reward_leave_authorized)

    def test_story_text_and_skip_without_known_hero_only_branch_cannot_discard(self):
        task, reward, clicks = task_for('20261004-120256-056')
        task._recruit_skipped = True
        self.assertTrue(task.handle_victory(reward))
        self.assertFalse(task._reward_leave_authorized)
        task.handle_reward_leave(ExplorationVision(frame('20260926-202057-530')))
        self.assertEqual(clicks, ['RESUME_REWARDS_CONTINUE', 'REWARD_LEAVE_CANCEL'])
