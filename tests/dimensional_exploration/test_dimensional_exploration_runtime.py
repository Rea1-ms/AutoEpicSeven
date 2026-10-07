"""Regressions from the September 26 on-device logs, without a real device."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tests.support.exploration import ControlledClock, ReplayDevice, artifact_path, frame, task_for
from module.config.config_manual import ManualConfig
from module.device.screenshot import Screenshot
from module.exception import RequestHumanTakeover
from tasks.dimensional_exploration.heroes.recruitment import HeroCosts
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import REWARD_CLOSE
from tasks.dimensional_exploration.recognition.vision import ExplorationVision, Hero
from tasks.dimensional_exploration.events.event import EventMemory, decide_event, event_branch
from tasks.dimensional_exploration.policy import EventChoice, RunProgress, choose_offer
from tasks.dimensional_exploration.events.sampling import choose_sample
from tasks.dimensional_exploration.recognition.hero_names import HeroNameOcr


class BatchResumeRegressions(unittest.TestCase):
    def saved(self):
        return dict(target=5, completed=5, settlement_seen=True, finished=False,
                    starlight_total=401, last_starlight=201,
                    recruitment={'heroes': ['赛珂兰特'], 'pending': None})

    def task(self, session, target=5):
        task, _, _ = task_for('20260924-091808-594')
        task.config.Emulator_PackageName = 'com.stove.epic7.google'
        task.config.DimensionalExploration_RunCount = target
        task.config.DimensionalExplorationRuntime_Session = deepcopy(session)
        task.load_recruitment()
        task.device.screenshot = Mock()
        task.ui_goto_main = Mock()
        return task

    def startup(self, session, target=5):
        task = self.task(session, target)
        # Stop at the real loop boundary, after run() has persisted its startup
        # decision. This does not pretend a mocked explore() completed a batch.
        task.explore = Mock(side_effect=RuntimeError('startup captured'))
        with self.assertRaisesRegex(RuntimeError, 'startup captured'):
            task.run()
        task.ui_goto_main.assert_not_called()
        return task

    def replay(self, session, suffixes, expected, clock, target=5):
        task = self.task(session, target)
        device = ReplayDevice(suffixes, expected_clicks=expected)
        task.device = device
        task.click_action = task.__class__.click_action.__get__(task)
        task.action_ready = task.__class__.action_ready.__get__(task)
        screenshot = device.screenshot

        def next_frame():
            clock.advance(2.1)
            screenshot()

        device.screenshot = next_frame
        return task, device

    def test_completed_unfinished_batch_starts_on_first_click_and_retries_lobby(self):
        suffixes = ['20260924-091808-594'] * 3 + ['20260922-081207-157']
        with ControlledClock() as clock:
            task, device = self.replay(self.saved(), suffixes, ['LOBBY_CONTINUE'] * 2, clock)
            task.handle_start_supply = Mock(side_effect=RuntimeError('new run entered'))
            with self.assertRaisesRegex(RuntimeError, 'new run entered'):
                task.run()
            device.assert_actions_complete()
            task.ui_goto_main.assert_not_called()
            task.handle_start_supply.assert_called_once()
            saved = task.config.DimensionalExplorationRuntime_Session
            self.assertEqual((saved['target'], saved['completed'], saved['starlight_total']), (5, 0, 0))
            self.assertIsNone(saved['last_starlight'])
            self.assertFalse(saved['settlement_seen'])
            self.assertFalse(saved['finished'])

    def test_completed_reset_clears_old_recruitment_and_keeps_settlement_guard(self):
        session = self.saved()
        session['recruitment']['pending'] = {'name': '亚敏', 'cost': 2, 'quota': [13, 19]}
        task = self.startup(session)
        self.assertEqual((task.progress.completed, task.progress.starlight_total), (0, 0))
        self.assertIsNone(task.progress.last_starlight)
        self.assertTrue(task.progress.settlement_seen)
        self.assertEqual(task._recruited_heroes, [])
        self.assertIsNone(task._recruit_pending)
        self.assertEqual(task.config.DimensionalExplorationRuntime_Session['recruitment'],
                         {'heroes': [], 'pending': None})

    def test_unfinished_batch_preserves_partial_progress_and_pending_recruitment(self):
        session = self.saved()
        session['completed'] = 2
        session['recruitment']['pending'] = {'name': '亚敏', 'cost': 2, 'quota': [13, 19]}
        for target in (5, 7):
            with self.subTest(target=target):
                task = self.startup(session, target)
                self.assertEqual((task.progress.completed, task.progress.starlight_total,
                                  task.progress.last_starlight), (2, 401, 201))
                self.assertTrue(task.progress.settlement_seen)
                self.assertEqual(task._recruited_heroes, ['赛珂兰特'])
                self.assertEqual(task._recruit_pending, session['recruitment']['pending'])

    def test_completed_batch_resets_after_target_changes_or_missing_old_target(self):
        for previous, requested, completed in ((5, 7, 5), (7, 3, 3), (None, 5, 5), (5, 5, 6)):
            with self.subTest(previous=previous, requested=requested, completed=completed):
                session = self.saved()
                session['completed'] = completed
                if previous is None:
                    session.pop('target')
                else:
                    session['target'] = previous
                task = self.startup(session, requested)
                self.assertEqual((task.target, task.progress.completed, task.progress.starlight_total),
                                 (requested, 0, 0))
                self.assertTrue(task.progress.settlement_seen)

    def test_finished_batch_also_retains_old_settlement_deduplication(self):
        session = self.saved()
        session['finished'] = True
        task = self.startup(session)
        self.assertEqual((task.progress.completed, task.progress.starlight_total), (0, 0))
        self.assertTrue(task.progress.settlement_seen)
        self.assertFalse(task.config.DimensionalExplorationRuntime_Session['finished'])

    def test_restart_while_closing_old_result_never_recounts_it(self):
        task = self.startup(self.saved())
        # An interruption after resetting totals may leave the previous result
        # visible again. Its guard must survive a second restart, not only the
        # startup that first detected the completed-but-unfinished batch.
        task = self.startup(task.config.DimensionalExplorationRuntime_Session)
        task.device.image = frame('20260923-233818-836')
        vision = ExplorationVision(task.device.image)
        self.assertTrue(task.handle_settlement(vision))
        self.assertTrue(task.handle_settlement(vision))
        self.assertEqual((task.progress.completed, task.progress.starlight_total), (0, 0))
        self.assertTrue(task.progress.settlement_seen)
        task.progress.entered_run()
        self.assertTrue(task.handle_settlement(vision))
        self.assertEqual((task.progress.completed, task.progress.starlight_total), (1, 443))

    def test_interruption_after_last_settlement_recovers_without_second_start(self):
        session = self.saved()
        session.update(completed=4, settlement_seen=False, starlight_total=200, last_starlight=200)
        task = self.task(session)
        vision = ExplorationVision(frame('20260923-233818-836'))
        task.explore = Mock(side_effect=lambda: task.handle_settlement(vision))
        task.ui_goto_main = Mock(side_effect=RuntimeError('interrupted before main'))
        with self.assertRaisesRegex(RuntimeError, 'interrupted before main'):
            task.run()
        saved = task.config.DimensionalExplorationRuntime_Session
        self.assertEqual((saved['completed'], saved['starlight_total']), (5, 643))
        self.assertFalse(saved['finished'])
        restored = self.startup(saved)
        self.assertEqual((restored.progress.completed, restored.progress.starlight_total), (0, 0))
        self.assertTrue(restored.progress.settlement_seen)

    def test_old_result_is_closed_then_new_run_counts_once_in_real_loop(self):
        suffixes = ['20260923-233818-836'] * 3 + ['20260922-081153-551',
                    '20260922-081207-157', '20260923-233811-030',
                    '20260923-233818-836', '20260923-233818-836', '20260924-091808-594']
        expected = ['SETTLEMENT_CLOSE'] * 2 + ['LOBBY_START', 'SUPPLY_BAGGAGE', 'FAILED_CHECK'] + [
            'SETTLEMENT_CLOSE'] * 2
        with ControlledClock() as clock:
            task, device = self.replay(self.saved(), suffixes, expected, clock, target=1)
            task.config.DimensionalExploration_StartStrategy = '沉重包袱'
            task.run()
            device.assert_actions_complete()
            task.ui_goto_main.assert_called_once()
            saved = task.config.DimensionalExplorationRuntime_Session
            self.assertEqual((saved['completed'], saved['starlight_total'], saved['last_starlight']), (1, 443, 443))
            self.assertTrue(saved['finished'])
            self.assertTrue(saved['settlement_seen'])
            self.assertEqual(saved['recruitment'], {'heroes': [], 'pending': None})


class RuntimeRegressions(unittest.TestCase):
    def test_exact_name_cache_reuses_pixels_and_moves_click_box(self):
        from pponnxcr.predict_system import BoxedResult
        reader = HeroNameOcr()
        image = np.zeros((40, 80, 3), dtype=np.uint8)
        original = BoxedResult((2, 3, 16, 9), None, '起源拉斯', .98)
        with patch('tasks.dimensional_exploration.recognition.hero_names.Ocr') as ocr:
            ocr.return_value.detect_and_ocr.return_value = [original]
            first = reader.read(image, (0, 0, 20, 10), 'HeroName')
            moved = reader.read(image.copy(), (30, 20, 50, 30), 'UpgradeName')
            self.assertEqual(ocr.return_value.detect_and_ocr.call_count, 1)
            self.assertEqual((first[0].ocr_text, moved[0].ocr_text), ('起源拉斯', '起源拉斯'))
            self.assertEqual(moved[0].box, (32, 23, 46, 29))
            moved[0].ocr_text = 'changed'
            self.assertEqual(reader.read(image, (0, 0, 20, 10), 'HeroName')[0].ocr_text, '起源拉斯')

    def test_name_cache_invalidates_on_any_pixel_or_shape_change_and_is_bounded(self):
        from pponnxcr.predict_system import BoxedResult
        reader = HeroNameOcr(capacity=1)
        image = np.zeros((20, 40, 3), dtype=np.uint8)
        with patch('tasks.dimensional_exploration.recognition.hero_names.Ocr') as ocr:
            ocr.return_value.detect_and_ocr.return_value = [BoxedResult((1, 1, 8, 7), None, '起源拉斯', .98)]
            reader.read(image, (0, 0, 20, 10), 'HeroName')
            changed = image.copy()
            changed[9, 19, 0] = 1
            reader.read(changed, (0, 0, 20, 10), 'HeroName')
            reader.read(image, (0, 0, 20, 10), 'HeroName')
            reader.read(image, (0, 0, 10, 20), 'HeroName')
            self.assertEqual(ocr.return_value.detect_and_ocr.call_count, 4)
            self.assertEqual(len(reader._entries), 1)

    def test_failed_and_ambiguous_name_reads_are_not_cached(self):
        from pponnxcr.predict_system import BoxedResult
        reader = HeroNameOcr()
        image = np.zeros((10, 20, 3), dtype=np.uint8)
        with patch('tasks.dimensional_exploration.recognition.hero_names.Ocr') as ocr:
            ocr.return_value.detect_and_ocr.side_effect = [[], [
                BoxedResult((0, 0, 5, 5), None, '起源', .8),
                BoxedResult((5, 0, 10, 5), None, '拉斯', .8),
            ], []]
            for _ in range(3):
                reader.read(image, (0, 0, 20, 10), 'HeroName')
            self.assertEqual(ocr.return_value.detect_and_ocr.call_count, 3)
            self.assertEqual(len(reader._entries), 0)

    def test_cached_real_names_do_not_reuse_missing_rank_or_hp(self):
        from module.base.utils import area_offset
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
            OCR_UPGRADE_RANK, ROSTER_HP_BAR,
        )
        reader = HeroNameOcr()
        image = frame('20261004-151606-257')
        first = ExplorationVision(image, hero_name_ocr=reader).upgrade_roster()
        self.assertEqual([h.name for h in first[0]], ['赛珂兰特', '罪戾的安洁莉卡', '起源拉斯', '保健室老师律荷'])
        self.assertEqual(ExplorationVision(image.copy(), hero_name_ocr=reader).upgrade_roster(), first)
        invalid = image.copy()
        x1, y1, x2, y2 = area_offset(OCR_UPGRADE_RANK.area, (0, 0))
        invalid[y1:y2, x1:x2] = 0
        self.assertIsNone(ExplorationVision(invalid, hero_name_ocr=reader).upgrade_roster())
        hp_image = frame('20261004-134045-652')
        self.assertIsNotNone(ExplorationVision(hp_image, hero_name_ocr=reader).roster_health())
        x1, y1, x2, y2 = ROSTER_HP_BAR.area
        hp_image[y1:y2, x1:x2] = 0
        self.assertIsNone(ExplorationVision(hp_image, hero_name_ocr=reader).roster_health())

    def test_program_log_messages_and_takeover_reasons_use_english(self):
        import ast
        from tests.support.exploration import ROOT
        for path in (ROOT / 'tasks/dimensional_exploration').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Attribute):
                    relevant = node.func.attr in ('require_human', 'record_progress') or (
                        isinstance(node.func.value, ast.Name) and node.func.value.id == 'logger')
                else:
                    relevant = isinstance(node.func, ast.Name) and node.func.id in ('ValueError', 'RequestHumanTakeover')
                if relevant:
                    for arg in node.args:
                        # Embedded OCR values may be Chinese. Only the static
                        # program text is constrained, not read game content.
                        literals = arg.values if isinstance(arg, ast.JoinedStr) else [arg]
                        for literal in literals:
                            if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                                self.assertFalse(any('\u4e00' <= char <= '\u9fff' for char in literal.value), (path, node.lineno))

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
                    'tasks.dimensional_exploration.recognition.vision.multi_match', return_value=[rectangle]):
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


class StartingAndStarlightRegressions(unittest.TestCase):
    def test_missing_start_strategy_defaults_to_contract_without_ocr(self):
        task, vision, clicks = task_for('20260922-081207-157')
        self.assertFalse(hasattr(task.config, 'DimensionalExploration_StartStrategy'))
        vision.text = Mock(side_effect=AssertionError('Starting strategy must use templates'))
        task.handle_start_supply(vision)
        self.assertEqual(clicks, ['SUPPLY_CONTRACT'])

    def test_dropdown_source_matches_four_unlocked_cards(self):
        from module.config.config_updater import ConfigGenerator
        from tasks.dimensional_exploration.rooms.starting_strategy import STRATEGIES
        option = ConfigGenerator().args['DimensionalExploration']['DimensionalExploration']['StartStrategy']
        self.assertEqual(option['type'], 'select')
        self.assertEqual(option['value'], '雇佣契约')
        self.assertEqual(option['option'], list(STRATEGIES))

    def test_all_four_start_strategies_select_their_visible_card(self):
        for name, expected in (('雇佣契约', 'SUPPLY_CONTRACT'), ('痛苦机械装置', 'SUPPLY_MACHINE'),
                               ('沉重包袱', 'SUPPLY_BAGGAGE'), ('基础锻炼铁锤', 'SUPPLY_HAMMER')):
            task, vision, clicks = task_for('20260922-081207-157')
            task.config.DimensionalExploration_StartStrategy = name
            self.assertTrue(task.handle_start_supply(vision))
            self.assertEqual(clicks, [expected])

    def test_wrong_selected_card_retries_without_confirming(self):
        task, vision, clicks = task_for('20260922-081226-118')
        task.config.DimensionalExploration_StartStrategy = '雇佣契约'
        self.assertTrue(task.handle_start_supply(vision))
        self.assertTrue(task.handle_start_supply(vision))
        self.assertEqual(clicks, ['SUPPLY_CONTRACT', 'SUPPLY_CONTRACT'])
        task.config.DimensionalExploration_StartStrategy = '沉重包袱'
        self.assertTrue(task.handle_start_supply(vision))
        self.assertEqual(clicks[-1], 'SUPPLY_CONFIRM')

    def test_each_selected_border_uses_its_own_position(self):
        from tasks.dimensional_exploration.rooms.starting_strategy import STRATEGIES
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import SUPPLY_SELECTED
        # Synthetic border states verify geometry. Only the baggage gold state
        # is a captured screenshot; this does not claim on-device validation
        # of the other three selected-card layouts.
        selected = frame('20260922-081226-118')
        x1, y1, x2, y2 = SUPPLY_SELECTED.area
        for name, (_, offset) in STRATEGIES.items():
            task, _, clicks = task_for('20260922-081207-157')
            task.config.DimensionalExploration_StartStrategy = name
            image = task.device.image.copy()
            image[y1:y2, x1+offset:x2+offset] = selected[y1:y2, x1:x2]
            task.device.image = image
            self.assertTrue(task.handle_start_supply(ExplorationVision(image)))
            self.assertEqual(clicks, ['SUPPLY_CONFIRM'])

    def test_unknown_or_invisible_strategy_never_confirms_another(self):
        task, vision, clicks = task_for('20260922-081226-118')
        task.config.DimensionalExploration_StartStrategy = '未登记'
        with self.assertRaisesRegex(RequestHumanTakeover, 'Unsupported starting strategy'):
            task.handle_start_supply(vision)
        self.assertEqual(clicks, [])
        task.config.DimensionalExploration_StartStrategy = '雇佣契约'
        task.appear = Mock(return_value=False)
        self.assertFalse(task.handle_start_supply(vision))
        self.assertEqual(clicks, [])

    def test_starlight_reads_right_hand_reward_and_counts_once_across_restart(self):
        task, vision, clicks = task_for('20260923-233818-836')
        task.progress, task.target = RunProgress(), 2
        self.assertTrue(task.handle_settlement(vision))
        self.assertTrue(task.handle_settlement(vision))
        saved = task.config.DimensionalExplorationRuntime_Session
        self.assertEqual((saved['completed'], saved['starlight_total'], saved['last_starlight']), (1, 443, 443))
        self.assertEqual(clicks, ['SETTLEMENT_CLOSE', 'SETTLEMENT_CLOSE'])
        task.progress = RunProgress(saved['completed'], saved['settlement_seen'],
                                    saved['starlight_total'], saved['last_starlight'])
        task.handle_settlement(vision)
        self.assertEqual(task.progress.starlight_total, 443)
        task.progress.entered_run()
        task.handle_settlement(vision)
        self.assertEqual((task.progress.completed, task.progress.starlight_total), (2, 886))

    def test_unreadable_starlight_waits_without_using_the_score(self):
        task, vision, clicks = task_for('20260923-233818-836')
        task.progress, task.target = RunProgress(), 1
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import OCR_SETTLEMENT_STARLIGHT
        image = task.device.image.copy()
        x1, y1, x2, y2 = OCR_SETTLEMENT_STARLIGHT.area
        image[y1:y2, x1:x2] = 0
        task.device.image = image
        self.assertFalse(task.handle_settlement(ExplorationVision(image)))
        self.assertEqual((task.progress.completed, task.progress.starlight_total), (0, 0))
        self.assertEqual(clicks, [])

    def test_zero_starlight_is_a_reading_not_missing_ocr(self):
        task, vision, clicks = task_for('20260923-233818-836')
        task.progress, task.target = RunProgress(), 1
        vision.number = Mock(return_value=0)
        self.assertTrue(task.handle_settlement(vision))
        self.assertEqual((task.progress.completed, task.progress.starlight_total, task.progress.last_starlight), (1, 0, 0))
        self.assertEqual(clicks, ['SETTLEMENT_CLOSE'])

    def test_empty_settlement_does_not_record_starlight(self):
        task, vision, clicks = task_for('20260924-091819-235')
        task.progress, task.target = RunProgress(), 1
        with self.assertRaisesRegex(RequestHumanTakeover, 'did not enter exploration'):
            task.handle_settlement(vision)
        self.assertEqual((task.progress.completed, task.progress.starlight_total), (0, 0))
        self.assertEqual(clicks, [])

    def test_frozen_costs_work_without_cache_and_leave_unknowns_unknown(self):
        costs = HeroCosts()
        self.assertEqual(len(costs.values), 22)
        for name, cost in (('亚敏', 2), ('赛珂兰特', 3), ('一轮孤月维尔萝娜', 4),
                           ('塔拉诺尔禁卫队员', 2), ('都市暗影小泡芙', 4), ('护卫队长克劳乌', 3)):
            self.assertEqual(costs.values[name], cost)
        self.assertTrue(costs.cannot_afford(('黑暗的科尔布思',), 3))
        self.assertFalse(costs.cannot_afford(('尚未采集',), 3))
        self.assertFalse(costs.cannot_afford(('黑暗的科尔布思', '尚未采集'), 3))

    def test_local_costs_override_frozen_costs_and_verified_aliases_are_canonical(self):
        path = artifact_path('costs.json')
        path.parent.mkdir(parents=True)
        path.write_text('{"亚敏": 3, "都市暗影小泡装": 4, "新英雄": 2}', encoding='utf-8')
        costs = HeroCosts(path)
        self.assertEqual(costs.values['亚敏'], 3)
        self.assertEqual(costs.values['都市暗影小泡芙'], 4)
        self.assertNotIn('都市暗影小泡装', costs.values)
        self.assertEqual(costs.values['新英雄'], 2)
        costs.learn([('护卫队长克劳鸟', 4), ('另一新英雄', 3)])
        restored = HeroCosts(path)
        self.assertEqual(restored.values['护卫队长克劳乌'], 4)
        self.assertEqual(restored.values['另一新英雄'], 3)
        self.assertTrue(restored.cannot_afford(('护卫队长克劳鸟',), 3))


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


class EventRecruitmentLimitRegressions(unittest.TestCase):
    WARNING = '20261006-214829-360'
    TICKET = '20261006-214929-415'
    LEAVE = '20261006-214941-942'
    NARRATION = '20261006-214945-513'
    MAP = '20261006-215003-689'

    def test_quota_warning_precedes_dimmed_event_and_other_modals_remain_distinct(self):
        for suffix, expected in ((self.WARNING, 'event_recruitment_limit'),
                                 (self.TICKET, 'resume_rewards'), (self.LEAVE, 'reward_leave'),
                                 (self.NARRATION, 'event'), (self.MAP, 'map'),
                                 ('20260929-230255-086', 'event'), ('20260929-230307-455', 'hero')):
            with self.subTest(suffix=suffix):
                self.assertEqual(ExplorationVision(frame(suffix)).state(), expected)

    def test_confirmed_limit_skips_only_after_click_without_recording_a_recruit(self):
        task, vision, clicks = task_for(self.WARNING)
        task.event_memory.begin(event_branch('narrow_passage.ranger'))
        task.save_event_history()
        with patch.object(task, 'click_action', return_value=False):
            self.assertFalse(task.handle_event_recruitment_limit(vision))
        self.assertFalse(task._recruit_skipped)
        self.assertFalse(task._reward_leave_authorized)
        for _ in range(2):
            self.assertTrue(task.handle_event_recruitment_limit(vision))
            task.observe_event('event_recruitment_limit', vision)
        self.assertEqual(clicks, ['EVENT_RECRUITMENT_LIMIT_CONFIRM'] * 2)
        self.assertTrue(task._recruit_skipped)
        self.assertFalse(task._reward_leave_authorized)
        self.assertEqual(task.event_memory.pending, 'narrow_passage.ranger')
        self.assertFalse(task.event_memory.visited)
        self.assertFalse(task._recruited_heroes)
        self.assertIsNone(task._recruit_pending)

    def test_restart_without_event_receipt_cancels_then_reconstructs_the_choice(self):
        task, vision, clicks = task_for(self.WARNING)
        self.assertTrue(task.handle_event_recruitment_limit(vision))
        self.assertEqual(clicks, ['EVENT_RECRUITMENT_LIMIT_CANCEL'])
        self.assertFalse(task._recruit_skipped)
        self.assertIsNone(task.event_memory.pending)
        task.device.image = frame('20260929-230255-086')
        self.assertTrue(task.handle_event(ExplorationVision(task.device.image)))
        saved = task.config.DimensionalExplorationRuntime_EventHistory
        restarted, modal, restarted_clicks = task_for(self.WARNING)
        restarted.event_memory = EventMemory.from_saved(saved)
        self.assertTrue(restarted.handle_event_recruitment_limit(modal))
        self.assertEqual(restarted_clicks, ['EVENT_RECRUITMENT_LIMIT_CONFIRM'])
        self.assertTrue(restarted._recruit_skipped)
        self.assertEqual(restarted.event_memory.pending, 'narrow_passage.ranger')

    def test_other_rewards_cannot_use_the_recruitment_skip_permission(self):
        task, vision, clicks = task_for(self.WARNING)
        task.event_memory.begin(event_branch('backpack.supplies'))
        with self.assertRaises(RequestHumanTakeover):
            task.handle_event_recruitment_limit(vision)
        self.assertEqual(clicks, [])
        self.assertFalse(task._recruit_skipped)
        self.assertFalse(task._reward_leave_authorized)
        reward = ExplorationVision(frame(self.TICKET))
        task._recruit_skipped = True
        self.assertTrue(task.handle_victory(reward))
        self.assertFalse(task._reward_leave_authorized)
        self.assertTrue(task.handle_reward_leave(ExplorationVision(frame(self.LEAVE))))
        self.assertEqual(clicks, ['RESUME_REWARDS_CONTINUE', 'REWARD_LEAVE_CANCEL'])

    def test_main_loop_retries_quota_warning_then_discards_ticket_and_returns_to_map(self):
        # The popup, ticket, leave warning, narration and map are supplied
        # screenshots. The final lobby only terminates this local replay;
        # it does not stand in for a complete run or a settlement receipt.
        suffixes = [self.WARNING] * 3 + [self.TICKET] * 3 + [self.LEAVE] * 3 + [self.NARRATION] * 3 + [
            self.MAP, self.MAP, '20260922-081153-551',
        ]
        expected = ['EVENT_RECRUITMENT_LIMIT_CONFIRM'] * 2 + ['RESUME_REWARDS_CONTINUE'] + [
            'REWARD_LEAVE_CONFIRM', 'REWARD_LEAVE_CONFIRM', 'EVENT_NEXT',
        ]
        with ControlledClock() as clock:
            task, _, _ = task_for(self.WARNING)
            task.event_memory.begin(event_branch('narrow_passage.ranger'))
            task.save_event_history()
            device = ReplayDevice(suffixes, expected_clicks=expected)
            task.device = device
            task.action_ready = task.__class__.action_ready.__get__(task)
            task.click_action = task.__class__.click_action.__get__(task)
            task.progress, task.target = RunProgress(completed=1), 1
            task.handle_map = Mock(return_value=False)
            task.handle_ui_recovery = Mock(return_value=False)
            screenshot = device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            task.explore()
            device.assert_actions_complete()
        self.assertEqual(task._last_state, 'map')
        self.assertIsNone(task.event_memory.pending)
        self.assertEqual(task.event_memory.visited, {'narrow_passage.ranger'})
        self.assertFalse(task.event_memory.obtained)
        self.assertFalse(task._recruited_heroes)
        self.assertIsNone(task._recruit_pending)
        self.assertFalse(task._recruit_skipped)
        self.assertFalse(task._reward_leave_authorized)
        self.assertTrue(all(ExplorationVision(frame(suffix)).quota() == (19, 20)
                            for suffix in (self.TICKET, self.NARRATION, self.MAP)))


class RestHealthRegressions(unittest.TestCase):
    @staticmethod
    def rest(available=(0, 3), hp=None):
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import REST_ACTION
        task, vision, _ = task_for('20260923-232305-766')
        buttons = list(REST_ACTION.iter_buttons())
        # The captured room has healing disabled. Availability is explicitly
        # simulated to exercise both choices, not claimed as a new live frame.
        vision.bright_text = Mock(side_effect=lambda button, **kwargs: buttons.index(button) in available)
        task._rest_hero_hp = hp
        actions = []
        task.click_action = Mock(side_effect=lambda button: actions.append((str(button), button.area)) or True)
        return task, vision, actions, buttons

    def test_healthy_team_upgrades_even_when_healing_is_available(self):
        task, vision, actions, buttons = self.rest(hp=(90, 90))
        self.assertTrue(task.handle_rest(vision))
        self.assertEqual(actions, [('ExplorationRestAction', buttons[0].area)])

    def test_critical_average_revives_before_life_recovery_and_healing(self):
        task, vision, actions, buttons = self.rest(available=(0, 1, 2, 3), hp=(20, 29))
        self.assertTrue(task.handle_rest(vision))
        self.assertEqual(actions, [('ExplorationRestAction', buttons[1].area)])
        self.assertEqual(task._rest_action, 1)

    def test_critical_average_heals_before_life_recovery_when_revive_unavailable(self):
        task, vision, actions, buttons = self.rest(available=(0, 2, 3), hp=(20, 29))
        self.assertTrue(task.handle_rest(vision))
        self.assertEqual(actions, [('ExplorationRestAction', buttons[3].area)])

    def test_critical_order_uses_first_available_action(self):
        for available, row in (((0, 1, 2, 3), 1), ((0, 2, 3), 3), ((0, 2), 2),
                               ((0,), 0), ((3,), 3), ((0, 1, 3), 1)):
            with self.subTest(available=available):
                task, vision, actions, buttons = self.rest(available=available, hp=(20, 29))
                self.assertTrue(task.handle_rest(vision))
                self.assertEqual(actions, [('ExplorationRestAction', buttons[row].area)])

    def test_average_thirty_boundary_preserves_normal_priority(self):
        for hp, row in (((0, 59), 1), ((0, 60), 2), ((29, 31), 2), ((29, 30), 1), ((30, 30), 2)):
            with self.subTest(hp=hp):
                task, vision, actions, buttons = self.rest(available=(0, 1, 2, 3), hp=hp)
                self.assertTrue(task.handle_rest(vision))
                self.assertEqual(actions, [('ExplorationRestAction', buttons[row].area)])

    def test_competing_life_recovery_requires_hp_before_selecting_any_action(self):
        for available in ((0, 1, 2, 3), (0, 1, 2), (0, 2, 3)):
            with self.subTest(available=available):
                task, vision, actions, _ = self.rest(available=available)
                self.assertTrue(task.handle_rest(vision))
                self.assertTrue(task.handle_rest(vision))
                self.assertEqual([name for name, _ in actions], ['REST_HERO_ENTRY'] * 2)
                self.assertTrue(task._rest_health_requested)
                self.assertIsNone(task._rest_action)
                self.assertIsNone(task._rest_hero_hp)
                task.appear = Mock(return_value=False)
                self.assertFalse(task.handle_rest(vision))
                self.assertEqual(len(actions), 2)

    def test_critical_selection_keeps_pending_revive_until_room_completion(self):
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import ROOM_LEAVE
        task, vision, actions, buttons = self.rest(available=(0, 1, 2, 3), hp=(20, 29))
        task.handle_rest(vision)
        task._rest_hero_hp = (90, 90)
        task.handle_rest(vision)
        self.assertEqual(actions, [('ExplorationRestAction', buttons[1].area)] * 2)
        with patch('tasks.dimensional_exploration.rooms.rest.match_in', return_value=(1, (0, 0, 1, 1))):
            self.assertTrue(task.handle_rest(vision))
        self.assertEqual(actions[-1], ('ROOM_LEAVE', ROOM_LEAVE.area))
        self.assertIsNone(task._rest_action)
        self.assertIsNone(task._rest_hero_hp)

    def test_lowest_or_average_hp_uses_strict_thresholds(self):
        from tasks.dimensional_exploration.rooms.rest import needs_rest_healing
        for hp, expected in (((29, 100), True), ((30, 90), False), ((30, 89), True),
                             ((59, 60), True), ((60, 60), False), ((100, 100), False),
                             ((0, 100), True), ((30, 30, 100, 100), False)):
            with self.subTest(hp=hp):
                self.assertEqual(needs_rest_healing(hp), expected)
        self.assertIsNone(needs_rest_healing(None))
        self.assertIsNone(needs_rest_healing(()))

    def test_low_hp_chooses_healing_and_boundaries_choose_upgrade(self):
        for hp, row in (((29, 100), 3), ((30, 89), 3), ((30, 90), 0), ((60, 60), 0)):
            task, vision, actions, buttons = self.rest(hp=hp)
            self.assertTrue(task.handle_rest(vision))
            self.assertEqual(actions, [('ExplorationRestAction', buttons[row].area)])

    def test_life_recovery_and_revive_keep_priority_without_hp_inspection(self):
        # These choices cannot be reordered by HP. When life competes with
        # revive/healing, the new critical-average cases require a roster.
        for available, row in (((0, 2), 2), ((0, 1, 3), 1), ((0,), 0)):
            task, vision, actions, buttons = self.rest(available=available)
            self.assertTrue(task.handle_rest(vision))
            self.assertEqual(actions, [('ExplorationRestAction', buttons[row].area)])
            self.assertFalse(task._rest_health_requested)

    def test_healthy_team_leaves_when_upgrade_is_disabled_without_healing(self):
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import ROOM_LEAVE
        task, vision, actions, _ = self.rest(available=(3,), hp=(90, 90))
        self.assertTrue(task.handle_rest(vision))
        self.assertEqual(actions, [('ROOM_LEAVE', ROOM_LEAVE.area)])

    def test_healing_option_opens_owned_roster_and_does_not_assume_click_success(self):
        task, vision, actions, _ = self.rest()
        self.assertTrue(task.handle_rest(vision))
        self.assertTrue(task.handle_rest(vision))
        self.assertEqual([name for name, _ in actions], ['REST_HERO_ENTRY', 'REST_HERO_ENTRY'])
        self.assertIsNone(task._rest_hero_hp)
        self.assertTrue(task._rest_health_requested)
        self.assertIsNone(task._rest_action)

    def test_two_complete_real_roster_frames_return_and_healthy_team_upgrades(self):
        task, rest, actions, buttons = self.rest()
        self.assertTrue(task.handle_rest(rest))
        roster = ExplorationVision(frame('20261001-133151-873'))
        task.device.image = roster.image
        self.assertFalse(task.handle_roster(roster))
        self.assertTrue(task.handle_roster(roster))
        self.assertEqual(len(task._rest_hero_hp), 6)
        self.assertIsNone(task._event_hero_hp)
        task.device.image = rest.image
        self.assertTrue(task.handle_rest(rest))
        self.assertEqual([name for name, _ in actions], ['REST_HERO_ENTRY', 'BACK', 'ExplorationRestAction'])
        self.assertEqual(actions[-1][1], buttons[0].area)

    def test_incomplete_or_changing_roster_waits_for_two_consistent_frames(self):
        from tasks.dimensional_exploration.recognition.vision import HeroHealth
        task, rest, actions, _ = self.rest()
        task.handle_rest(rest)
        roster = ExplorationVision(frame('20261001-133151-873'))
        low = (HeroHealth('英雄甲', 20), HeroHealth('英雄乙', 90))
        healthy = (HeroHealth('英雄甲', 90), HeroHealth('英雄乙', 90))
        roster.roster_health = Mock(side_effect=[None, low, healthy, healthy])
        task.device.image = roster.image
        for _ in range(3):
            self.assertFalse(task.handle_roster(roster))
        self.assertEqual([name for name, _ in actions], ['REST_HERO_ENTRY'])
        self.assertIsNone(task._rest_hero_hp)
        self.assertTrue(task.handle_roster(roster))
        self.assertEqual(task._rest_hero_hp, (90, 90))
        self.assertEqual(actions[-1][0], 'BACK')

    def test_failed_entry_and_back_clicks_retry_without_authorizing_healing(self):
        task, rest, actions, _ = self.rest()
        task.click_action = Mock(return_value=False)
        self.assertFalse(task.handle_rest(rest))
        self.assertFalse(task._rest_health_requested)
        self.assertIsNone(task._rest_action)
        task.click_action = Mock(side_effect=lambda button: actions.append((str(button), button.area)) or True)
        task.handle_rest(rest)
        roster = ExplorationVision(frame('20261001-133151-873'))
        task.device.image = roster.image
        self.assertFalse(task.handle_roster(roster))
        task.click_action.return_value = False
        task.click_action.side_effect = None
        self.assertFalse(task.handle_roster(roster))
        self.assertTrue(task._rest_health_requested)
        self.assertIsNotNone(task._rest_hero_hp)
        task.click_action.side_effect = lambda button: actions.append((str(button), button.area)) or True
        self.assertTrue(task.handle_roster(roster))
        self.assertEqual(actions[-1][0], 'BACK')

    def test_unknown_frames_keep_hp_but_positive_room_exit_clears_it(self):
        task, _, _, _ = self.rest(hp=(29, 100))
        task._rest_health_requested = True
        task._rest_action = 3
        for state in ('unknown', 'roster', 'rest'):
            task.observe_rest_health(state)
            self.assertEqual(task._rest_hero_hp, (29, 100))
            self.assertEqual(task._rest_action, 3)
        for state in ('map', 'upgrade', 'revive', 'event', 'settlement', 'lobby'):
            task._rest_hero_hp = (29, 100)
            task._rest_health_requested = True
            task._rest_action = 3
            task.observe_rest_health(state)
            self.assertIsNone(task._rest_hero_hp)
            self.assertIsNone(task._rest_action)
            self.assertFalse(task._rest_health_requested)

    def test_selected_action_is_pinned_until_room_done_and_clears_hp(self):
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import ROOM_LEAVE
        task, vision, actions, buttons = self.rest(hp=(29, 100))
        task.handle_rest(vision)
        task._rest_hero_hp = (90, 90)
        task.handle_rest(vision)
        self.assertEqual(actions, [('ExplorationRestAction', buttons[3].area)] * 2)
        with patch('tasks.dimensional_exploration.rooms.rest.match_in', return_value=(1, (0, 0, 1, 1))):
            self.assertTrue(task.handle_rest(vision))
        self.assertEqual(actions[-1], ('ROOM_LEAVE', ROOM_LEAVE.area))
        self.assertIsNone(task._rest_hero_hp)
        self.assertIsNone(task._rest_action)

    def test_real_rest_with_disabled_healing_upgrades_without_opening_roster(self):
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import REST_ACTION
        task, vision, _ = task_for('20260923-232305-766')
        actions = []
        task.click_action = lambda button: actions.append((str(button), button.area)) or True
        self.assertTrue(task.handle_rest(vision))
        self.assertEqual(actions, [('ExplorationRestAction', list(REST_ACTION.iter_buttons())[0].area)])
        self.assertFalse(task._rest_health_requested)

    def test_missing_hero_entry_waits_without_healing_or_upgrade(self):
        task, vision, actions, _ = self.rest()
        task.appear = Mock(return_value=False)
        self.assertFalse(task.handle_rest(vision))
        self.assertEqual(actions, [])
        self.assertIsNone(task._rest_hero_hp)


    def test_rest_inspection_does_not_reuse_event_hp(self):
        task, vision, actions, _ = self.rest()
        task._event_hero_hp = (1, 1)
        self.assertTrue(task.handle_rest(vision))
        self.assertEqual([name for name, _ in actions], ['REST_HERO_ENTRY'])
        self.assertIsNone(task._rest_hero_hp)
        self.assertIsNone(task._rest_action)

    def test_main_loop_inspects_roster_retries_then_reaches_upgrade(self):
        from tests.support.exploration import ReplayDevice
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import REST_ACTION
        suffixes = ['20260923-232305-766'] * 2 + ['20261001-133151-873'] * 3 + [
            '20260923-232305-766', '20260923-232305-766', '20260923-232309-289']
        expected = ['REST_HERO_ENTRY', 'BACK', 'ExplorationRestAction']
        buttons = list(REST_ACTION.iter_buttons())
        original_bright = ExplorationVision.bright_text

        def available(vision, button, **kwargs):
            if any(button is b for b in buttons):
                return buttons.index(button) in (0, 3)
            return original_bright(vision, button, **kwargs)

        with ControlledClock() as clock, patch.object(ExplorationVision, 'bright_text', available):
            task, _, _ = task_for(suffixes[0])
            device = ReplayDevice(suffixes, expected_clicks=expected)
            task.device = device
            task.click_action = task.__class__.click_action.__get__(task)
            task.action_ready = task.__class__.action_ready.__get__(task)
            task.progress, task.target = RunProgress(), 5
            task.handle_ui_recovery = Mock(return_value=False)
            task.handle_rest_hero = Mock(side_effect=RuntimeError('upgrade reached'))
            screenshot = device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            with self.assertRaisesRegex(RuntimeError, 'upgrade reached'):
                task.explore()
            device.assert_actions_complete()
            task.handle_rest_hero.assert_called_once()
            self.assertIsNone(task._rest_hero_hp)
            self.assertIsNone(task._rest_action)
            self.assertFalse(task._rest_health_requested)
            self.assertEqual(task.progress.completed, 0)

    def test_main_loop_reads_critical_roster_retries_then_reaches_revive(self):
        from tests.support.exploration import ReplayDevice
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import REST_ACTION
        from tasks.dimensional_exploration.recognition.vision import HeroHealth
        suffixes = ['20260923-232305-766'] * 2 + ['20261001-133151-873'] * 3 + [
            '20260923-232305-766'] * 3 + ['20260923-233115-192']
        expected = ['REST_HERO_ENTRY', 'BACK', 'ExplorationRestAction', 'ExplorationRestAction']
        buttons = list(REST_ACTION.iter_buttons())
        original_bright = ExplorationVision.bright_text

        def available(vision, button, **kwargs):
            if any(button is b for b in buttons):
                return True
            return original_bright(vision, button, **kwargs)

        # The real roster is a page/background fixture. Critical HP and all
        # four enabled actions are explicit simulations, not captured states.
        critical = tuple(HeroHealth(f'模拟英雄{index}', 25) for index in range(6))
        with ControlledClock() as clock, patch.object(ExplorationVision, 'bright_text', available), patch.object(
                ExplorationVision, 'roster_health', return_value=critical):
            task, _, _ = task_for(suffixes[0])
            device = ReplayDevice(suffixes, expected_clicks=expected)
            task.device = device
            task.click_action = task.__class__.click_action.__get__(task)
            task.action_ready = task.__class__.action_ready.__get__(task)
            task.progress, task.target = RunProgress(), 5
            task.handle_ui_recovery = Mock(return_value=False)
            task.handle_rest_hero = Mock(side_effect=RuntimeError('revive reached'))
            click = device.click
            actions = []

            def clicked(button):
                actions.append((str(button), button.area))
                click(button)

            device.click = clicked
            screenshot = device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            with self.assertRaisesRegex(RuntimeError, 'revive reached'):
                task.explore()
            device.assert_actions_complete()
            task.handle_rest_hero.assert_called_once()
            self.assertEqual(actions[-2:], [('ExplorationRestAction', buttons[1].area)] * 2)
            self.assertIsNone(task._rest_hero_hp)
            self.assertIsNone(task._rest_action)
            self.assertFalse(task._rest_health_requested)
            self.assertEqual(task.progress.completed, 0)
