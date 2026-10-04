"""Event decisions against actual screenshots and restart/receipt regressions."""
from dataclasses import replace
import json
import re
import unittest
from unittest.mock import Mock, patch

from tests.support.exploration import ControlledClock, ROOT, ReplayDevice, frame, make_task, task_for
from module.exception import RequestHumanTakeover
from tasks.dimensional_exploration.event import (
    EventCondition, EventCost, EventMemory, decide_event, event_branch, event_catalog, match_event, pending_event,
)
from tasks.dimensional_exploration.policy import EventChoice, RunProgress, normalize
from tasks.dimensional_exploration.vision import ExplorationVision


class EventTests(unittest.TestCase):
    @staticmethod
    def observation(suffix):
        vision = ExplorationVision(frame(suffix))
        resources = vision.resources()
        choices = [choice for choice, _ in vision.event_choices()]
        params = dict(cores=resources.cores, fragments=resources.fragments, life=resources.life,
                      loot=vision.number((39, 195, 88, 224)), dice=vision.event_dice(), story=vision.event_story())
        return vision, choices, params

    def test_all_supplied_events_have_first_and_repeat_routes(self):
        cases = [
            ('20260922-081528-636', 'observatory.leave', 'observatory.leave'),
            ('20260923-232331-766', 'candlestick.repair', 'candlestick.repair'),
            ('20260923-232358-161', 'garden.heal', 'garden.heal'),
            ('20260923-233133-331', 'backpack.supplies', 'backpack.remains'),
            ('20260923-233146-181', 'mimic.leave', 'mimic.leave'),
            ('20260923-233240-604', 'pool.deep', 'pool.shallow'),
            ('20260924-193808-685', 'sword.fight', 'sword.fight'),
        ]
        for suffix, first, repeat in cases:
            with self.subTest(suffix=suffix):
                _, choices, params = self.observation(suffix)
                memory = EventMemory()
                decision = decide_event(choices, memory=memory, **params)
                self.assertEqual(decision.branch.key, first)
                memory.begin(decision.branch)
                memory.observe('map')
                self.assertEqual(decide_event(choices, memory=memory, **params).branch.key, repeat)
                self.assertEqual(memory.obtained, set())

    def test_magnifier_is_not_a_collection_flag_and_click_avoids_preview(self):
        vision, choices, params = self.observation('20260923-233133-331')
        ordinary = decide_event(choices, **params)
        inverted = [replace(choice, journal=not choice.journal) for choice in choices]
        self.assertEqual(decide_event(inverted, **params).branch.key, ordinary.branch.key)
        self.assertTrue(all(area[1] > 585 for _, area in vision.event_choices()))
        memory = EventMemory()
        self.assertFalse(memory.observe('reward', reward_name='探险家指南针'))
        self.assertFalse(memory.obtained)

    def test_life_floor_and_distinct_cost_types(self):
        _, choices, params = self.observation('20260923-233240-604')
        self.assertEqual(decide_event(choices, **{**params, 'life': 2}).branch.key, 'pool.shallow')
        self.assertIsNone(decide_event(choices, **{**params, 'life': 1}))
        balances = dict(cores=19, fragments=109, life=1, loot=None, dice=None)
        self.assertTrue(EventCost(fragments=60).unavailable(**balances))
        self.assertFalse(EventCost(fragments=60).unavailable(**{**balances, 'fragments': 110}))
        self.assertFalse(EventCost(fragments=60).unavailable(**{**balances, 'cores': 20}))
        self.assertTrue(EventCost(loot=1).unavailable(**balances))
        self.assertTrue(EventCost(dice=1).unavailable(**balances))
        self.assertFalse(EventCost(dice=1).unavailable(**{**balances, 'dice': 1}))
        self.assertFalse(EventCost(random_hp_percent=40).unavailable(**balances))
        self.assertFalse(EventCost(all_hp_percent=25).unavailable(**balances))
        self.assertEqual(event_branch('sword.touch').rewards, ({'kind': 'dice', 'amount': 2},))

    def test_changed_cost_or_extra_condition_never_uses_old_table(self):
        _, choices, params = self.observation('20260924-193808-685')
        for changed in ('600个次元碎片', '60个次元碎片并失去1点生命体征'):
            broken = [replace(c, text=c.text.replace('60个次元碎片', changed)) for c in choices]
            self.assertIsNone(decide_event(broken, **params))
        self.assertIsNone(match_event(choices, '这是另一个完全不同的事件'))

    def test_pending_choice_is_pinned_until_positive_progress_and_survives_restart(self):
        _, choices, params = self.observation('20260924-193808-685')
        params['fragments'] = 2000
        memory = EventMemory()
        memory.begin(decide_event(choices, **params).branch)
        self.assertFalse(memory.observe('unknown'))
        self.assertFalse(memory.observe('event'))
        restored = EventMemory.from_saved(memory.as_dict())
        self.assertEqual(restored.pending, 'sword.pay')
        self.assertFalse(restored.visited)
        reordered = [replace(c, index=2-c.index) for c in reversed(choices)]
        self.assertEqual(decide_event(reordered, memory=restored, **params).branch.key, 'sword.pay')
        self.assertIsNone(decide_event(choices, memory=restored, **{**params, 'fragments': 0}))
        self.assertTrue(restored.observe('event', narration=True))
        self.assertFalse(restored.observe('event', narration=True))
        self.assertEqual(restored.visited, {'sword.pay'})
        self.assertFalse(restored.obtained)
        self.assertEqual(decide_event(choices, memory=restored, **{**params, 'fragments': 1940}).branch.key, 'sword.fight')

    def test_named_reward_requires_matching_card_and_is_not_repaid_via_another_branch(self):
        _, choices, params = self.observation('20260924-193808-685')
        memory = EventMemory()
        memory.begin(event_branch('sword.pay'))
        memory.observe('reward', reward_name='其他物品')
        self.assertFalse(memory.obtained)
        memory.observe('reward', reward_name='门卫之剑碎片')
        memory.observe('map')
        self.assertEqual(memory.obtained, {'门卫之剑碎片'})
        self.assertIsNone(memory.pending)
        restored = EventMemory.from_saved(memory.as_dict())
        repeated = decide_event(choices, memory=restored, **params)
        self.assertEqual(repeated.branch.key, 'sword.fight')
        self.assertFalse(repeated.first_collectible)
        # The reviewed repeat route still fights, but a known acquisition must
        # suppress the first-collection bonus, even without a visited branch.
        restored.visited.clear()
        repeated = decide_event(choices, memory=restored, **params)
        self.assertEqual(repeated.branch.key, 'sword.fight')
        self.assertFalse(repeated.first_collectible)

    def test_task_click_then_reward_and_batch_reset_preserves_history(self):
        task, vision, clicks = make_task('20260922-081528-636')
        # The old screenshot holds only 86 fragments. Seed the explicit budget
        # needed for this receipt test; the real 86-fragment route exits above.
        vision.resources = Mock(return_value=replace(vision.resources(), fragments=220))
        task.action_ready = lambda: True
        self.assertTrue(task.handle_event(vision))
        self.assertFalse(task.event_memory.visited)
        self.assertEqual(task.config.DimensionalExplorationRuntime_EventHistory['pending'], 'observatory.coin')
        self.assertTrue(task.handle_event(vision))
        self.assertEqual(len(clicks), 2)
        self.assertFalse(task.event_memory.visited)
        reward = ExplorationVision(frame('20260922-081544-299'))
        task.observe_event('reward', reward)
        self.assertEqual(task.event_memory.obtained, {'观测站望远镜镜片'})
        task.observe_event('map', vision)
        saved = task.config.DimensionalExplorationRuntime_EventHistory
        task.progress, task.target = RunProgress(), 1
        task.save_progress(finished=True)
        self.assertEqual(task.config.DimensionalExplorationRuntime_EventHistory, saved)
        other = EventMemory()
        self.assertFalse(other.visited)
        self.assertFalse(other.obtained)

    def test_unsent_click_is_not_persisted(self):
        task, vision, _ = make_task('20260922-081528-636')
        task.action_ready = lambda: True
        task.click_action = lambda button: False
        self.assertFalse(task.handle_event(vision))
        self.assertIsNone(task.event_memory.pending)
        self.assertFalse(task.event_memory.visited)

    def test_dice_crop_does_not_include_die_icon_number(self):
        for suffix in ('20260922-081528-636', '20260924-193808-685'):
            vision = ExplorationVision(frame(suffix))
            self.assertEqual(vision.event_dice(), 2)
        # A zero OCR failure is unknown, never the icon's 20 or 82.
        vision = ExplorationVision(frame('20260923-233240-604'))
        self.assertIn(vision.event_dice(), (0, None))

    def test_malformed_history_does_not_silently_overwrite_observations(self):
        with self.assertRaises(ValueError):
            EventMemory.from_saved({'version': 2, 'visited': [], 'obtained': []})
        with self.assertRaises(ValueError):
            EventMemory.from_saved({'version': 1, 'visited': 'pool.deep', 'obtained': []})


class GiantEyeEventTests(unittest.TestCase):
    REWARD = '绘画：盛极一时的星辰帝国'
    STORY = '那只眼睛仿佛从很久以前就一直注视着你。就像是一个记录过无数探查队的存在。'
    TEXTS = (
        '与巨眼对视。消耗1点生命体征，获得1个随机战利品',
        '用帷幕遮住眼睛。消耗1个战利品，获得1个首领战利品',
        '攻击眼瞳。战斗后，获得绘画：盛极一时的星辰帝国',
    )

    def choices(self):
        return [EventChoice(index, text) for index, text in enumerate(self.TEXTS)]

    def test_supplied_pages_and_animation_readiness(self):
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import EVENT_NEXT
        expected = [('20261004-125347-490', 'event', False), ('20261004-125352-386', 'event', False),
                    ('20261004-125521-108', 'reward', False), ('20261004-125528-432', 'event', True),
                    ('20261004-125532-398', 'event', True)]
        for suffix, state, next_ready in expected:
            with self.subTest(suffix=suffix):
                vision = ExplorationVision(frame(suffix))
                self.assertEqual(vision.state(), state)
                self.assertEqual(EVENT_NEXT.match_template(vision.image), next_ready)
                if state == 'reward':
                    self.assertEqual(vision.event_reward(), self.REWARD)
                elif next_ready:
                    self.assertEqual(vision.event_choices(), [])

    def test_real_options_resources_and_attack_click_avoid_magnifier(self):
        vision = ExplorationVision(frame('20261004-125352-386'))
        choices = vision.event_choices()
        self.assertEqual(len(choices), 3)
        self.assertEqual([choice.available for choice, _ in choices], [True] * 3)
        matched = match_event([choice for choice, _ in choices], vision.event_story())
        self.assertIsNotNone(matched)
        self.assertEqual(matched[0].event_id, 'giant_eye')
        resources = vision.resources()
        self.assertIsNotNone(resources)
        self.assertEqual((resources.cores, resources.fragments, resources.life, resources.max_life), (52, 378, 4, 4))
        self.assertEqual(vision.event_dice(), 3)
        attack, area = choices[2]
        self.assertTrue(attack.journal)
        self.assertGreater(area[0], 835)
        self.assertGreater(area[1], 585)
        self.assertLessEqual(area[2], 1203)

    def test_first_repeat_and_collected_routes_still_attack_without_resource_cost(self):
        memory = EventMemory()
        balances = dict(cores=0, fragments=0, life=1, loot=0, dice=0, story=self.STORY)
        for stage in ('first', 'visited', 'collected'):
            with self.subTest(stage=stage):
                decision = decide_event(self.choices(), memory=memory, **balances)
                self.assertIsNotNone(decision)
                self.assertEqual(decision.branch.key, 'giant_eye.attack')
                self.assertEqual(decision.first_collectible, stage == 'first')
                self.assertEqual(decision.branch.cost, EventCost(battle=1))
                memory.begin(decision.branch)
                if stage == 'visited':
                    memory.observe('reward', reward_name=self.REWARD)
                memory.observe('map')

    def test_exact_costs_partial_rows_and_other_events_do_not_authorize_choice(self):
        balances = dict(cores=52, fragments=378, life=4, loot=4, dice=3, story=self.STORY)
        self.assertIsNone(decide_event(self.choices()[:2], **balances))
        for original, changed in (('消耗1点', '消耗2点'), ('消耗1个', '消耗2个'),
                                  ('战斗后', '消耗100个次元碎片战斗后')):
            choices = [replace(choice, text=choice.text.replace(original, changed)) for choice in self.choices()]
            self.assertIsNone(match_event(choices, self.STORY))
        self.assertIsNone(match_event(self.choices(), '这是一个不同的事件'))
        for suffix in ('20260923-233133-331', '20260924-193808-685'):
            vision = ExplorationVision(frame(suffix))
            matched = match_event([choice for choice, _ in vision.event_choices()], vision.event_story())
            self.assertIsNotNone(matched)
            self.assertNotEqual(matched[0].event_id, 'giant_eye')

    def test_reward_requires_pending_attack_and_matching_card_across_restart(self):
        task, reward, _ = task_for('20261004-125521-108')
        task.observe_event('reward', reward)
        self.assertFalse(task.event_memory.obtained)
        task.event_memory.begin(event_branch('giant_eye.attack'))
        task.observe_event('battle', reward)
        self.assertFalse(task.event_memory.obtained)
        task.event_memory = EventMemory.from_saved(task.event_memory.as_dict())
        task.event_memory.observe('reward', reward_name='其他战利品')
        self.assertFalse(task.event_memory.obtained)
        for _ in range(2):
            task.observe_event('reward', reward)
        self.assertEqual(task.event_memory.obtained, {self.REWARD})
        self.assertEqual(task.event_memory.visited, {'giant_eye.attack'})
        for suffix in ('20261004-125528-432', '20261004-125532-398'):
            task.device.image = frame(suffix)
            task.observe_event('event', ExplorationVision(task.device.image))
            self.assertEqual(task.event_memory.pending, 'giant_eye.attack')
        task.observe_event('map', reward)
        self.assertIsNone(task.event_memory.pending)
        restored = EventMemory.from_saved(task.config.DimensionalExplorationRuntime_EventHistory)
        self.assertEqual(restored.obtained, {self.REWARD})

    def test_main_loop_retries_attack_and_reward_then_advances_both_narrations(self):
        # No fight frames were supplied for this event. The existing prepare
        # and auto-battle fixtures explicitly stand in for that shared flow;
        # the eye options, named receipt and both narrations are real inputs.
        suffixes = ['20261004-125347-490'] + ['20261004-125352-386'] * 3 + [
            '20260922-081423-319', '20260922-081423-319',
            '20260922-081430-868', '20260922-081440-084',
        ] + ['20261004-125521-108'] * 3 + ['20261004-125528-432'] * 2 + [
            '20261004-125532-398', '20261004-125532-398',
            '20260922-081554-067', '20260922-081153-551',
        ]
        expected = ['ExplorationEventChoice'] * 2 + ['BATTLE_START', 'REWARD_CLOSE', 'REWARD_CLOSE',
                                                    'EVENT_NEXT', 'EVENT_NEXT']
        with ControlledClock() as clock:
            task, _, _ = task_for(suffixes[0])
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
            self.assertEqual(task.event_memory.visited, {'giant_eye.attack'})
            self.assertEqual(task.event_memory.obtained, {self.REWARD})
            self.assertIsNone(task.event_memory.pending)
            task.handle_map.assert_called_once()


class CliffCoinEventTests(unittest.TestCase):
    STAGES = (
        ('20261004-143741-000', 'cliff_coin', 30, 110,
         '悬崖挡住了去路，狂风从崖底猛烈吹来。对面的墙壁上刻着一个古老的标记。'),
        ('20261004-145456-631', 'cliff_coin_retry', 50, 80,
         '硬币朝标记飞去。崖底吹来的风变得更加猛烈。'),
    )

    @staticmethod
    def choices(cost):
        return [EventChoice(0, f'朝标记投掷硬币。消耗{cost}个次元碎片，有50%的几率获得2倍'),
                EventChoice(1, '不投硬币，转身离开。')]

    def test_real_stages_and_leave_narration_keep_exact_resources(self):
        for suffix, event_id, cost, fragments, _ in self.STAGES:
            with self.subTest(suffix=suffix):
                vision = ExplorationVision(frame(suffix))
                self.assertEqual(vision.state(), 'event')
                choices = [choice for choice, _ in vision.event_choices()]
                self.assertEqual(len(choices), 2)
                self.assertTrue(all(choice.available for choice in choices))
                matched = match_event(choices, vision.event_story())
                self.assertIsNotNone(matched)
                self.assertEqual(matched[0].event_id, event_id)
                self.assertEqual(matched[1][0][1].cost.fragments, cost)
                resources = vision.resources()
                self.assertEqual((resources.cores, resources.fragments, resources.life, resources.max_life),
                                 (52, fragments, 4, 4))
        result = ExplorationVision(frame('20261004-145530-255'))
        self.assertEqual(result.state(), 'event')
        self.assertEqual(result.event_choices(), [])
        self.assertIn('没有投掷硬币', result.event_story())
        self.assertEqual(result.resources().fragments, 80)

    def test_first_and_repeat_never_gamble_even_with_surplus_or_no_fragments(self):
        for _, event_id, cost, _, story in self.STAGES:
            coin = event_branch(f'{event_id}.coin')
            self.assertIsNotNone(coin)
            self.assertFalse(coin.enabled)
            for fragments in (0, 20, 80, 110, 9999):
                memory = EventMemory()
                for stage in ('first', 'repeat'):
                    with self.subTest(event=event_id, fragments=fragments, stage=stage):
                        decision = decide_event(self.choices(cost), cores=0, fragments=fragments,
                                                life=1, loot=None, dice=0, story=story, memory=memory)
                        self.assertIsNotNone(decision)
                        self.assertEqual(decision.branch.key, f'{event_id}.leave')
                        self.assertEqual(decision.branch.cost, EventCost())
                        self.assertFalse(decision.first_collectible)
                        memory.begin(decision.branch)
                        memory.observe('map')
            blocked = [self.choices(cost)[0], replace(self.choices(cost)[1], available=False)]
            self.assertIsNone(decide_event(blocked, cores=52, fragments=9999, life=4, loot=5, story=story))

    def test_unknown_cost_probability_partial_rows_and_other_stories_wait(self):
        for _, event_id, cost, _, story in self.STAGES:
            params = dict(cores=52, fragments=9999, life=4, loot=1, story=story)
            choices = self.choices(cost)
            self.assertIsNone(decide_event(choices[:1], **params))
            self.assertIsNone(decide_event(choices[1:], **params))
            for original, changed in ((f'消耗{cost}个', '消耗70个'), ('50%', '60%'), ('2倍', '3倍')):
                altered = [replace(choice, text=choice.text.replace(original, changed)) for choice in choices]
                self.assertIsNone(match_event(altered, story), (event_id, altered))
            self.assertIsNone(match_event(choices, '不是悬崖投币的其他事件'))
        # The shared leave text cannot identify which stage is on screen.
        self.assertIsNone(match_event([EventChoice(0, '不投硬币，转身离开。')]))

    def test_live_catalogue_chooses_right_leave_card_without_sampling_or_hp(self):
        for suffix, event_id, _, _, _ in self.STAGES:
            task, vision, clicks = task_for(suffix)
            task.sampler = Mock(active=None)
            task.handle_event_sample = Mock(side_effect=AssertionError('reviewed event must not gamble via sampling'))
            task.inspect_event_health = Mock(side_effect=AssertionError('free leave requires no HP inspection'))
            choices = vision.event_choices()
            self.assertTrue(task.handle_event(vision))
            self.assertEqual(task.event_memory.pending, f'{event_id}.leave')
            self.assertEqual(clicks, ['ExplorationEventChoice'])
            chosen = task.sampler.before.call_args.args[4]
            self.assertEqual(chosen.index, 1)
            self.assertGreater(choices[chosen.index][1][0], choices[0][1][2])
            self.assertFalse(task.event_memory.visited)
            # An unchanged options frame retries the same free card.
            self.assertTrue(task.handle_event(vision))
            self.assertEqual(clicks, ['ExplorationEventChoice'] * 2)
            task.handle_event_sample.assert_not_called()
            task.inspect_event_health.assert_not_called()

    def test_main_loop_retries_leave_preserves_fragments_and_samples_outcome_once(self):
        from tests.support.exploration import artifact_path
        from tasks.dimensional_exploration.sampling import EventSampler, text_key
        # The supplied narration belongs to the 50-fragment stage. The final
        # map/lobby explicitly reuse existing fixtures; no new map was supplied.
        suffixes = ['20261004-145456-631'] * 3 + ['20261004-145530-255'] * 5 + [
            '20260922-081554-067', '20260922-081153-551',
        ]
        expected = ['ExplorationEventChoice'] * 2 + ['EVENT_NEXT'] * 2
        with ControlledClock() as clock:
            task, initial, _ = task_for(suffixes[0])
            task.sampler = EventSampler(artifact_path('cliff_coin'))
            task.progress, task.target = RunProgress(completed=1), 1
            task.handle_map = Mock(return_value=False)
            task.handle_ui_recovery = Mock(return_value=False)
            task.handle_event_sample = Mock(side_effect=AssertionError('catalogue must bypass gambling sampler'))
            task.device = ReplayDevice(suffixes, expected_clicks=expected)
            task.action_ready = task.__class__.action_ready.__get__(task)
            task.click_action = task.__class__.click_action.__get__(task)
            screenshot = task.device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            task.device.screenshot = next_frame
            task.explore()
            task.device.assert_actions_complete()
            self.assertEqual(task.event_memory.visited, {'cliff_coin_retry.leave'})
            self.assertIsNone(task.event_memory.pending)
            self.assertFalse(task.event_memory.obtained)
            self.assertIsNone(task.sampler.active)
            records = list(task.sampler.root.glob('*/*/record.json'))
            self.assertEqual(len(records), 1)
            saved = json.loads(records[0].read_text(encoding='utf-8'))
            self.assertEqual(saved['source'], 'catalog')
            self.assertEqual(saved['reason'], 'cliff_coin_retry.leave')
            self.assertEqual(saved['selected_index'], 1)
            narrations = [item for item in saved['frames'] if item['state'] == 'event']
            self.assertEqual(len(narrations), 1)
            self.assertEqual(narrations[0]['resources']['fragments'], 80)
            self.assertEqual(narrations[0]['delta']['fragments'], 0)
            self.assertEqual(task.sampler.visits(initial.event_story()), {text_key('不投硬币，转身离开。'): 1})
            task.handle_event_sample.assert_not_called()


class CollectedEventTests(unittest.TestCase):
    @staticmethod
    def observations():
        path = ROOT / 'tests/fixtures/dimensional_exploration/event_observations.json'
        return json.loads(path.read_text(encoding='utf-8'))['observations']

    def test_all_collected_option_sets_match_exact_catalogue_stages(self):
        samples = self.observations()
        self.assertEqual(len(samples), 61)
        for sample in samples:
            with self.subTest(source=sample['source']):
                choices = [EventChoice(i, text) for i, text in enumerate(sample['choices'])]
                self.assertIsNotNone(match_event(choices, sample['story']))

    def test_new_named_rewards_use_first_attempt_then_lower_cost_routes(self):
        expected = {'blue_flame': ('deep', 'touch'), 'bloody_refuge': ('lantern', 'leave'),
                    'spirit_gate': ('thread', 'memory'), 'armory': ('elite', 'leave')}
        for spec in event_catalog():
            if spec.event_id not in expected:
                continue
            sample = next(s for s in self.observations()
                          if (m := match_event([EventChoice(i, t) for i, t in enumerate(s['choices'])], s['story']))
                          and m[0].event_id == spec.event_id)
            choices = [EventChoice(i, t) for i, t in enumerate(sample['choices'])]
            params = dict(cores=20, fragments=500, life=3, loot=5, dice=5, story=sample['story'])
            memory = EventMemory()
            first = decide_event(choices, memory=memory, **params)
            self.assertEqual(first.branch.branch_id, expected[spec.event_id][0])
            memory.begin(first.branch)
            memory.observe('map')
            repeat = decide_event(choices, memory=memory, **params)
            self.assertEqual(repeat.branch.branch_id, expected[spec.event_id][1])

    def test_blank_priorities_hold_even_when_sampling_is_enabled(self):
        for sample in self.observations():
            choices = [EventChoice(i, text) for i, text in enumerate(sample['choices'])]
            event = pending_event(sample['story'])
            if event is not None:
                self.assertIsNone(decide_event(choices, cores=20, fragments=999, life=9, loot=9,
                                               dice=9, story=sample['story']))
        task, vision, clicks = make_task('20260926-201447-325')
        task.sampler = Mock(active=None)
        task.handle_event_sample = Mock(side_effect=AssertionError('blank policy must not sample'))
        task.action_ready = lambda: True
        # Keep the blank-policy guard covered after the user fills the real
        # table: simulate leaving one priority blank in a recognized event.
        original = next(e for e in event_catalog() if e.event_id == 'vestment_price')
        deferred = replace(original, branches=(replace(original.branches[0], priority=None), *original.branches[1:]))
        catalogue = tuple(deferred if e == original else e for e in event_catalog())
        with patch('tasks.dimensional_exploration.event.event_catalog', return_value=catalogue):
            with self.assertRaisesRegex(RequestHumanTakeover, '策略留空'):
                task.handle_event(vision)
            self.assertEqual(clicks, [])
            task.sampler.before.assert_called_once()
            # Corrupted option OCR still must not bypass the explicit review hold.
            choices = vision.event_choices()
            vision.event_choices = lambda: [(replace(c, text='未识别选项'), area) for c, area in choices]
            with self.assertRaisesRegex(RequestHumanTakeover, '策略留空'):
                task.handle_event(vision)
            self.assertEqual(clicks, [])

    def test_multistage_costs_and_observed_rewards_are_not_invented(self):
        self.assertEqual(event_branch('torn_map_first.follow').cost.fragments, 20)
        self.assertEqual(event_branch('torn_map_second.follow').cost.fragments, 40)
        self.assertEqual(event_branch('blue_flower.rest').rewards, ({'kind': 'life', 'amount': 1},))
        self.assertEqual(event_branch('bloody_refuge.leave').rewards, ({'kind': 'experience', 'amount': 50},))
        self.assertTrue(event_branch('twisted_study.book').rewards[0]['observed_only'])
        self.assertEqual(event_branch('meteorite.core').cost.life, 1)
        self.assertTrue(event_branch('meteorite.core').cost.unavailable(
            cores=20, fragments=100, life=1, loot=1))
        self.assertFalse(event_branch('fairy_dance.heal').enabled)


class ReviewedEventTests(unittest.TestCase):
    @staticmethod
    def choices(event_id):
        data = json.loads((ROOT / 'tests/fixtures/dimensional_exploration/event_observations.json').read_text(encoding='utf-8'))
        reviewed = json.loads((ROOT / 'tests/fixtures/dimensional_exploration/reviewed_events.json').read_text(encoding='utf-8'))
        for sample in reviewed['observations'] + data['observations']:
            choices = [EventChoice(i, text) for i, text in enumerate(sample['choices'])]
            matched = match_event(choices, sample['story'])
            if matched and matched[0].event_id == event_id and len(choices) == len(matched[0].branches):
                return choices
        raise AssertionError(f'Missing observed choices for {event_id}')

    def decide(self, event_id, **overrides):
        params = dict(cores=20, fragments=500, life=3, max_life=3, loot=5, dice=5)
        params.update(overrides)
        return decide_event(self.choices(event_id), **params)

    def test_reviewed_fixed_preferences_and_last_life_guard(self):
        for event_id, branch in (('flooded_prison', 'fragments'), ('torn_vestment', 'take'),
                                 ('vestment_price', 'life'), ('quarreling_tablet', 'healer'),
                                 ('fairy_dance', 'dice'), ('mushroom_path', 'loot'), ('mimic', 'leave')):
            with self.subTest(event=event_id):
                self.assertEqual(self.decide(event_id).branch.branch_id, branch)
        self.assertEqual(self.decide('vestment_price', life=1, loot=0).branch.branch_id, 'hp')
        self.assertEqual(self.decide('fairy_dance', loot=0).branch.branch_id, 'dice')
        self.assertIsNone(self.decide('mushroom_path', fragments=99))
        self.assertIsNone(self.decide('mushroom_path', cores=19, fragments=149))
        self.assertEqual(self.decide('mushroom_path', cores=19, fragments=150).branch.branch_id, 'loot')

    def test_life_threshold_and_capacity_control_recovery(self):
        for life, branch in ((1, 'life'), (2, 'life'), (3, 'wall'), (4, 'wall')):
            self.assertEqual(self.decide('eternal_torch', life=life, max_life=4).branch.branch_id, branch)
        for life, capacity, branch in ((1, 3, 'rest'), (3, 4, 'rest'), (3, 3, 'rank'), (4, 4, 'rank')):
            self.assertEqual(self.decide('blue_flower', life=life, max_life=capacity).branch.branch_id, branch)
        self.assertIsNone(self.decide('blue_flower', max_life=None))

    def test_mask_first_reward_then_paid_repeat_and_budget_fallback(self):
        memory = EventMemory()
        first = self.decide('broken_mask', memory=memory)
        self.assertEqual(first.branch.branch_id, 'repair')
        self.assertTrue(first.first_collectible)
        memory.begin(first.branch)
        memory.observe('map')
        self.assertFalse(memory.obtained)
        for cores, fragments, branch in ((20, 50, 'spice'), (20, 49, 'repair'),
                                        (19, 100, 'spice'), (19, 99, 'repair')):
            self.assertEqual(self.decide('broken_mask', cores=cores, fragments=fragments,
                                         loot=0, memory=memory).branch.branch_id, branch)
        acquired = EventMemory(obtained={'地下苔藓'})
        self.assertEqual(self.decide('broken_mask', memory=acquired).branch.branch_id, 'spice')

    def test_sword_payment_threshold_applies_before_and_after_collection(self):
        for memory in (EventMemory(), EventMemory(obtained={'门卫之剑碎片'})):
            for fragments, branch in ((0, 'fight'), (60, 'fight'), (1999, 'fight'), (2000, 'pay'), (2060, 'pay')):
                with self.subTest(fragments=fragments, collected=bool(memory.obtained)):
                    self.assertEqual(self.decide('sword', fragments=fragments, memory=memory).branch.branch_id, branch)
        pending = EventMemory(pending='sword.pay')
        self.assertIsNone(self.decide('sword', fragments=1999, memory=pending))
        self.assertFalse(event_branch('sword.touch').enabled)

    def test_conditions_reject_malformed_values(self):
        for condition in ({'life_lte': True}, {'fragments_gte': -1}, {'fragments_gte': '2000'},
                          {'life_below_max': 'true'}):
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                EventCondition(**condition)

    def test_partial_event_options_do_not_advance_or_choose(self):
        task, vision, clicks = task_for('20260929-230509-019')
        task.event_memory.begin(event_branch('sword.pay'))
        complete = vision.event_choices()
        vision.event_choices = lambda: complete[:1]
        choices = [c for c, _ in complete[:1]]
        params = dict(cores=20, fragments=2000, life=3, loot=5)
        self.assertIsNotNone(match_event(choices, vision.event_story()))
        self.assertIsNone(decide_event(choices, **params))
        self.assertIsNone(decide_event(choices, memory=task.event_memory, **params))
        for _ in range(2):
            self.assertFalse(task.handle_event(vision))
        self.assertEqual(clicks, [])
        self.assertEqual(task.event_memory.pending, 'sword.pay')
        self.assertFalse(task.event_memory.advanced)
        self.assertFalse(task.event_memory.visited)

    def test_supplied_screenshots_have_expected_page_states(self):
        states = {
            '20260927-152340-604': 'event', '20260929-230255-086': 'event',
            '20260929-230259-128': 'resume_rewards', '20260929-230307-455': 'hero',
            '20260929-230315-796': 'hero', '20260929-230320-094': 'reward',
            '20260929-230325-818': 'event', '20260929-230509-019': 'event',
            '20260929-230827-172': 'reward', '20260929-230830-517': 'event',
            '20260929-230947-942': 'event', '20260929-230953-691': 'event',
        }
        for suffix, state in states.items():
            with self.subTest(screenshot=suffix):
                self.assertEqual(ExplorationVision(frame(suffix)).state(), state)

    def test_event_choices_and_outcomes_use_real_screenshots(self):
        for suffix, branch in (('20260927-152340-604', 'vestment_price.life'),
                               ('20260929-230509-019', 'sword.fight'),
                               ('20260929-230947-942', 'mimic.leave')):
            task, vision, clicks = task_for(suffix)
            self.assertTrue(task.handle_event(vision))
            self.assertEqual(task.event_memory.pending, branch)
            self.assertEqual(clicks, ['ExplorationEventChoice'])
            self.assertFalse(task.event_memory.visited)
            if branch == 'mimic.leave':
                task.device.image = frame('20260929-230953-691')
                self.assertTrue(task.handle_event(ExplorationVision(task.device.image)))
                self.assertIn(branch, task.event_memory.visited)
                self.assertEqual(clicks[-1], 'EVENT_NEXT')
        # The user supplied a previous paid result. It proves the reward name,
        # independently of the newly chosen 2000-fragment payment threshold.
        task, _, clicks = task_for('20260929-230827-172')
        task.event_memory.begin(event_branch('sword.pay'))
        task.observe_event('reward', ExplorationVision(task.device.image))
        self.assertEqual(task.event_memory.obtained, {'门卫之剑碎片'})
        task.device.image = frame('20260929-230830-517')
        self.assertTrue(task.handle_event(ExplorationVision(task.device.image)))
        self.assertEqual(clicks, ['EVENT_NEXT'])

    def test_narrow_passage_recruits_ranger_and_confirms_quota(self):
        task, vision, clicks = task_for('20260929-230255-086')
        task.config.DimensionalExploration_RangerPriority = '海边的维尔萝娜'
        self.assertTrue(task.handle_event(vision))
        self.assertEqual(task.event_memory.pending, 'narrow_passage.ranger')
        task.device.image = frame('20260929-230259-128')
        self.assertTrue(task.handle_victory(ExplorationVision(task.device.image)))
        task.device.image = frame('20260929-230307-455')
        self.assertFalse(task.handle_hero(ExplorationVision(task.device.image)))
        self.assertTrue(task.handle_hero(ExplorationVision(task.device.image)))
        # The chosen hero is in the clipped third column. Its full cost area
        # is outside the screenshot, so the task scrolls instead of guessing.
        task.device.swipe.assert_called_once()
        self.assertIsNone(task._hero_selected)
        self.assertEqual(clicks, ['ExplorationEventChoice', 'ClaimExplorationBattleReward'])
        # No post-scroll screenshot was supplied. Test the later confirmation
        # from an explicitly seeded, already verified selection receipt, rather
        # than pretending the preceding swipe selected the user's hero.
        task._hero_selected = '海边的维尔萝娜'
        task._hero_selected_cost = 3
        task.device.image = frame('20260929-230315-796')
        self.assertTrue(task.handle_hero(ExplorationVision(task.device.image)))
        self.assertEqual(task._recruit_pending, {'name': '海边的维尔萝娜', 'cost': 3, 'quota': [17, 22]})
        self.assertEqual(task._recruited_heroes, [])
        task.device.image = frame('20260929-230320-094')
        task.observe_event('reward', ExplorationVision(task.device.image))
        self.assertIn('narrow_passage.ranger', task.event_memory.visited)
        self.assertFalse(task.event_memory.obtained)
        task.device.image = frame('20260929-230325-818')
        task.observe_recruitment('event', ExplorationVision(task.device.image))
        self.assertEqual(task._recruited_heroes, [])
        task.observe_recruitment('event', ExplorationVision(task.device.image))
        self.assertEqual(task._recruited_heroes, ['海边的维尔萝娜'])
        self.assertTrue(task.handle_event(ExplorationVision(task.device.image)))
        self.assertEqual(clicks, ['ExplorationEventChoice', 'ClaimExplorationBattleReward',
                                  'HERO_CONFIRM', 'EVENT_NEXT'])


if __name__ == '__main__':
    unittest.main()


class SteleEventTests(unittest.TestCase):
    CHOICES = [EventChoice(0, '查看石碑下方的缝隙。'),
               EventChoice(1, '拓印无名纹样。所有英雄生命值降低25%，获得1个随机战利品')]
    STORY = '刻有古代文字、孤零零伫立着的残破古老石碑映入眼帘。'

    def test_priority_hp_floor_and_pending_choice_use_complete_options(self):
        params = dict(cores=56, fragments=344, life=4, loot=3, story=self.STORY)
        for hp, expected in (((98, 98, 73), 'rubbing'), ((50, 100), 'rubbing'),
                             ((49, 100), 'fragments'), ((0, 98), 'fragments'), ((), 'fragments')):
            for visited in (set(), {'ancient_stele.rubbing'}):
                decision = decide_event(self.CHOICES, **params, hero_hp=hp, memory=EventMemory(visited=visited))
                self.assertEqual(decision.branch.key, 'ancient_stele.' + expected)
        self.assertEqual(decide_event(self.CHOICES, **params).branch.key, 'ancient_stele.rubbing')
        self.assertEqual(decide_event(self.CHOICES, **dict(params, fragments=0, life=1, loot=0),
                                      hero_hp=(49,)).branch.key, 'ancient_stele.fragments')
        self.assertEqual(event_branch('ancient_stele.fragments').cost, EventCost())
        self.assertEqual(event_branch('ancient_stele.fragments').rewards, ({'kind': 'fragments', 'amount': 50},))
        self.assertEqual(event_branch('ancient_stele.rubbing').cost.all_hp_percent, 25)
        self.assertIsNone(event_branch('ancient_stele.rubbing').collectible)
        memory = EventMemory(pending='ancient_stele.fragments')
        self.assertEqual(decide_event(self.CHOICES, **params, hero_hp=(100,), memory=memory).branch.key,
                         'ancient_stele.fragments')
        memory = EventMemory(pending='ancient_stele.rubbing')
        self.assertIsNone(decide_event(self.CHOICES, **params, hero_hp=(49,), memory=memory))
        for broken in (self.CHOICES[:1], [self.CHOICES[0], replace(self.CHOICES[1],
                       text=self.CHOICES[1].text.replace('25%', '20%'))]):
            self.assertIsNone(decide_event(broken, **params, hero_hp=(100,)))

    def test_native_options_reward_rosters_and_two_narrations(self):
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import OCR_EVENT_LOOT
        states = {'175956-091': 'event', '180237-343': 'roster', '180244-044': 'reward',
                  '180247-666': 'event', '180348-232': 'roster', '180354-458': 'event'}
        for suffix, state in states.items():
            self.assertEqual(ExplorationVision(frame('20261004-' + suffix)).state(), state)
        vision = ExplorationVision(frame('20261004-175956-091'))
        choices = vision.event_choices()
        self.assertEqual(len(choices), 2)
        self.assertEqual(match_event([c for c, _ in choices], vision.event_story())[0].event_id, 'ancient_stele')
        self.assertEqual(normalize(choices[1][0].text), normalize(self.CHOICES[1].text))
        self.assertGreater(choices[1][1][0], 640)
        self.assertTrue(all(area[1] > 585 for _, area in choices))
        for suffix, event, count in (('20260927-152340-604', 'vestment_price', 3),
                                     ('20261004-134120-334', 'faint_fire', 3),
                                     ('20261001-132524-411', 'garden', 3),
                                     ('20260929-230947-942', 'mimic', 2)):
            prior = ExplorationVision(frame(suffix))
            prior_choices = [c for c, _ in prior.event_choices()]
            self.assertEqual(len(prior_choices), count)
            self.assertEqual(match_event(prior_choices, prior.event_story())[0].event_id, event)
        before = ExplorationVision(frame('20261004-180237-343')).roster_health()
        after = ExplorationVision(frame('20261004-180348-232')).roster_health()
        self.assertEqual([h.name for h in before], ['罪戾的安洁莉卡', '午时戏水芙兰', '智武'])
        self.assertEqual([h.name for h in after], [h.name for h in before])
        self.assertEqual([h.percent for h in before], [98, 98, 73])
        self.assertEqual([h.percent for h in after], [75, 75, 47])
        # Native bar ratios are approximate; full bars read about 98%, so
        # do not demand an exact 25-point difference from the pixel estimate.
        self.assertTrue(all(20 <= a.percent - b.percent <= 27 for a, b in zip(before, after)))
        reward = ExplorationVision(frame('20261004-180244-044'))
        self.assertEqual(reward.event_reward(), '守望者胸甲')
        self.assertEqual(reward.number(OCR_EVENT_LOOT), 3)
        for suffix in ('20261004-180247-666', '20261004-180354-458'):
            result = ExplorationVision(frame(suffix))
            self.assertEqual(result.number(OCR_EVENT_LOOT), 4)
            self.assertEqual(result.resources(), vision.resources())
            self.assertEqual(result.event_choices(), [])
        # The new option template must not mistake narrative
        # pixels for an actionable option. Missing complete HP still waits.
        blank = frame('20261004-180237-343').copy()
        blank[130:165, 420:490] = 0
        self.assertIsNone(ExplorationVision(blank).roster_health())

    def test_live_selection_waits_for_hp_then_uses_loot_or_free_fragments(self):
        for roster, expected in (('20261004-180237-343', 'rubbing'), ('20261004-180348-232', 'fragments')):
            with self.subTest(roster=roster):
                task, vision, clicks = task_for('20261004-175956-091')
                self.assertTrue(task.handle_event(vision))
                self.assertEqual(clicks, ['EVENT_HERO_ENTRY'])
                self.assertIsNone(task.event_memory.pending)
                task.device.image = frame(roster)
                owned = ExplorationVision(task.device.image)
                with patch.object(owned, 'roster_health', return_value=None):
                    self.assertFalse(task.handle_roster(owned))
                self.assertFalse(task.handle_roster(owned))
                self.assertTrue(task.handle_roster(owned))
                task.device.image = vision.image
                click = task.click_action
                task.click_action = Mock(return_value=False)
                self.assertFalse(task.handle_event(vision))
                self.assertIsNone(task.event_memory.pending)
                task.click_action = click
                self.assertTrue(task.handle_event(vision))
                self.assertTrue(task.handle_event(vision))
                self.assertEqual(task.event_memory.pending, 'ancient_stele.' + expected)
                self.assertEqual(clicks, ['EVENT_HERO_ENTRY', 'BACK', 'ExplorationEventChoice', 'ExplorationEventChoice'])
                self.assertEqual(task.event_memory.visited, set())
                complete = vision.event_choices()
                with patch.object(vision, 'event_choices', return_value=complete[:1]):
                    self.assertFalse(task.handle_event(vision))
                self.assertEqual(len(clicks), 4)

    def test_sampling_records_random_reward_and_both_narrations_once(self):
        from tests.support.exploration import artifact_path
        from tasks.dimensional_exploration.sampling import EventSampler, sampling_balances
        task, vision, _ = task_for('20261004-175956-091')
        choices = [c for c, _ in vision.event_choices()]
        self.assertEqual(len(choices), 2)
        task.event_memory.begin(event_branch('ancient_stele.rubbing'))
        task.sampler = EventSampler(artifact_path())
        task.sampler.before(vision.image, vision.event_story(), choices, sampling_balances(vision),
                            choices[1], 'catalog', 'ancient_stele.rubbing')
        task.sampler.clicked()
        folder = task.sampler.active['folder']
        owned = ExplorationVision(frame('20261004-180237-343'))
        task.device.image = owned.image
        task.observe_event('roster', owned)
        self.assertFalse(task.event_memory.advanced)
        self.assertFalse(task.sampler.active['advanced'])
        for suffix, state in (('20261004-180244-044', 'reward'), ('20261004-180247-666', 'event'),
                              ('20261004-180354-458', 'event'), ('20260922-081554-067', 'map')):
            result = ExplorationVision(frame(suffix))
            task.device.image = result.image
            task.observe_event(state, result)
            task.observe_event(state, result)
        self.assertIsNone(task.sampler.active)
        saved = json.loads((task.sampler.root / folder / 'record.json').read_text(encoding='utf-8'))
        self.assertEqual([f['state'] for f in saved['frames']], ['reward', 'event', 'event', 'map'])
        self.assertEqual(saved['frames'][0]['text'], '守望者胸甲')
        self.assertEqual(saved['frames'][0]['delta']['loot'], 0)
        self.assertEqual(saved['frames'][1]['delta']['loot'], 1)
        self.assertEqual(saved['frames'][1]['delta']['fragments'], 0)
        self.assertNotEqual(saved['frames'][1]['text'], saved['frames'][2]['text'])
        self.assertEqual(task.event_memory.visited, {'ancient_stele.rubbing'})
        # A random named card is an observed result, not an event-exclusive
        # collectible. Seeing this armor must not make future stele runs skip
        # the user's preferred random-loot branch.
        self.assertEqual(task.event_memory.obtained, set())
        self.assertEqual(sum(c['completed'] for c in task.sampler.index['events'][saved['event']]['choices'].values()), 1)

    def test_main_loop_uses_production_intervals_for_both_hp_routes(self):
        for roster, branch in (('20261004-180237-343', 'rubbing'), ('20261004-180348-232', 'fragments')):
            suffixes = sum(([suffix] * 3 for suffix in ('20261004-175956-091', roster, '20261004-175956-091')), [])
            expected = ['EVENT_HERO_ENTRY'] * 2 + ['BACK'] + ['ExplorationEventChoice'] * 2
            if branch == 'rubbing':
                suffixes += sum(([suffix] * 3 for suffix in ('20261004-180244-044', '20261004-180247-666',
                                                           '20261004-180354-458')), [])
                expected += ['REWARD_CLOSE'] + ['EVENT_NEXT'] * 3
            suffixes += ['20260922-081554-067', '20260922-081153-551']
            with self.subTest(branch=branch), ControlledClock() as clock:
                task, _, _ = task_for(suffixes[0])
                device = ReplayDevice(suffixes, expected_clicks=expected)
                task.device = device
                task.action_ready = type(task).action_ready.__get__(task)
                task.click_action = type(task).click_action.__get__(task)
                task.progress, task.target = RunProgress(completed=1), 1
                task.handle_map = Mock(return_value=False)
                task.handle_ui_recovery = Mock(return_value=False)
                screenshot = device.screenshot
                def next_frame():
                    clock.advance(1.1)
                    screenshot()
                device.screenshot = next_frame
                # The high-HP branch has native reward and both narratives.
                # Low HP explicitly reuses the supplied AFTER roster as a
                # simulated future selection context; its result and both
                # terminal map/lobby frames reuse old fixtures, not new proof
                # that the user's capture has already returned to the map.
                task.explore()
                device.assert_actions_complete()
                self.assertEqual(task.event_memory.visited, {'ancient_stele.' + branch})
                self.assertIsNone(task.event_memory.pending)
                self.assertIsNone(task._event_hero_hp)


class ForkSacrificeEventTests(unittest.TestCase):
    CHOICES = [EventChoice(0, '前往左侧岔路。1名随机英雄生命值降低50%，获得1个随机战利品'),
               EventChoice(1, '前往右侧岔路。消耗1个战利品，所有英雄生命值恢复100%')]

    @staticmethod
    def payment(suffix='20261004-161432-079'):
        task, vision, clicks = task_for(suffix)
        task.event_memory.begin(event_branch('forked_path.right_heal'))
        return task, vision, clicks

    def test_real_pages_distinguish_normal_special_hp_and_one_item_payment(self):
        import numpy as np
        from tasks.dimensional_exploration.assets.assets_dimensional_exploration import OCR_EVENT_LOOT
        from tasks.dimensional_exploration.vision import EventLootCountOcr
        states = {'161405-470': 'event', '161414-135': 'roster', '161432-079': 'sacrifice',
                  '161439-143': 'sacrifice', '161443-261': 'event', '161448-012': 'event', '161453-293': 'roster'}
        for suffix, expected in states.items():
            self.assertEqual(ExplorationVision(frame('20261004-' + suffix)).state(), expected)
        before = ExplorationVision(frame('20261004-161414-135')).roster_health()
        after = ExplorationVision(frame('20261004-161453-293')).roster_health()
        self.assertEqual([h.percent for h in before], [0, 25, 98, 83])
        self.assertEqual([h.percent for h in after], [0, 98, 98, 98])
        _, vision, _ = self.payment()
        self.assertEqual(vision.sacrifice_counter(), (0, 1))
        self.assertEqual(vision.sacrifice_loot_count(), 5)
        ordinary, special = vision.sacrifice_items()
        self.assertEqual([a[0] for a in ordinary], [662, 758, 854, 1046])
        self.assertEqual([a[0] for a in special], [950])
        selected = ExplorationVision(frame('20261004-161439-143'))
        self.assertEqual(selected.sacrifice_counter(), (1, 1))
        self.assertEqual(selected.sacrifice_loot_count(), 5)
        self.assertEqual(ExplorationVision(frame('20261004-161443-261')).number((47, 197, 68, 220)), 4)
        self.assertEqual(ExplorationVision(frame('20261004-161405-470')).number(OCR_EVENT_LOOT), 5)
        self.assertEqual(ExplorationVision(frame('20261004-161443-261')).number(OCR_EVENT_LOOT), 4)
        missing = frame('20261004-161405-470').copy()
        x1, y1, x2, y2 = OCR_EVENT_LOOT.area
        missing[y1:y2, x1:x2] = 0
        self.assertIsNone(ExplorationVision(missing).number(OCR_EVENT_LOOT))
        # Simulated separated numeric glyphs must both survive preprocessing.
        # Keeping only the largest component would silently drop one digit.
        multiple = np.zeros((29, 49, 3), dtype=np.uint8)
        multiple[8:21, 8:15] = 220
        multiple[8:21, 31:38] = 220
        prepared = EventLootCountOcr(OCR_EVENT_LOOT, lang='cn').pre_process(multiple)
        self.assertGreater(prepared.shape[1], 30)
        self.assertEqual(np.count_nonzero(prepared.min(axis=2) > 155), 13 * 14)

    def test_hp_floor_exact_costs_and_missing_safe_payment(self):
        params = dict(cores=52, fragments=391, life=3, loot=5, story='眼前出现了两条岔路。')
        for hp, expected in (((75, 100), 'left_loot'), ((74, 100), 'right_heal'),
                             ((0, 25, 98, 83), 'right_heal')):
            decision = decide_event(self.CHOICES, **params, hero_hp=hp)
            self.assertEqual(decision.branch.key, 'forked_path.' + expected)
        self.assertIsNone(decide_event(self.CHOICES, **dict(params, loot=0), hero_hp=(74, 100)))
        for bad in ('降低50%', '消耗1个战利品', '恢复100%'):
            altered = [replace(c, text=c.text.replace(bad, bad.replace('50', '40').replace('1', '2')))
                       for c in self.CHOICES]
            self.assertIsNone(decide_event(altered, **params, hero_hp=(75, 100)))
        self.assertIsNone(decide_event(self.CHOICES[:1], **params, hero_hp=(100,)))
        memory = EventMemory()
        memory.begin(event_branch('forked_path.right_heal'))
        self.assertEqual(decide_event(self.CHOICES, **params, hero_hp=(100,), memory=memory).branch.key,
                         'forked_path.right_heal')

    def test_normal_selection_and_confirm_retry_preserve_unpaid_receipt(self):
        task, vision, clicks = self.payment()
        self.assertTrue(task.handle_sacrifice(vision))
        self.assertEqual(task._sacrifice_selected[0], vision.sacrifice_items()[0][0])
        click = task.click_action
        task.click_action = Mock(return_value=False)
        self.assertFalse(task.handle_sacrifice(vision))
        self.assertEqual(task._sacrifice_selected[0], vision.sacrifice_items()[0][0])
        task.click_action = click
        selected = ExplorationVision(frame('20261004-161439-143'))
        task.device.image = selected.image
        self.assertTrue(task.handle_sacrifice(selected))
        self.assertTrue(task.handle_sacrifice(selected))
        self.assertEqual(clicks, ['SacrificeNormalSelect', 'SACRIFICE_CONFIRM', 'SACRIFICE_CONFIRM'])
        task.observe_event('sacrifice', selected)
        self.assertFalse(task.event_memory.advanced)
        self.assertEqual(task.event_memory.visited, set())
        task.observe_sacrifice('unknown')
        self.assertIsNotNone(task._sacrifice_selected)
        task.observe_sacrifice('event')
        self.assertIsNone(task._sacrifice_selected)
        # Explicitly simulate a different inventory order using native card
        # pixels: the special item now occupies the first slot. Selection must
        # locate the next ordinary border, not assume the first item is safe.
        ordinary, special = vision.sacrifice_items()
        moved = vision.image.copy()
        a, b = ordinary[0], special[0]
        moved[a[1]:a[3], a[0]:a[2]] = vision.image[b[1]:b[3], b[0]:b[2]]
        moved[b[1]:b[3], b[0]:b[2]] = vision.image[a[1]:a[3], a[0]:a[2]]
        task.device.image = moved
        self.assertTrue(task.handle_sacrifice(ExplorationVision(moved)))
        self.assertEqual(task._sacrifice_selected[0], ordinary[1])

    def test_restart_or_changed_selection_clears_before_confirm_and_requires_event(self):
        task, selected, clicks = self.payment('20261004-161439-143')
        self.assertTrue(task.handle_sacrifice(selected))
        self.assertTrue(task.handle_sacrifice(selected))
        self.assertEqual(clicks, ['SACRIFICE_REMOVE'] * 2)
        unselected = ExplorationVision(frame('20261004-161432-079'))
        task.device.image = unselected.image
        self.assertTrue(task.handle_sacrifice(unselected))
        task._sacrifice_selected = (unselected.sacrifice_items()[1][0], 5)
        task.device.image = selected.image
        self.assertTrue(task.handle_sacrifice(selected))
        self.assertEqual(clicks[-1], 'SACRIFICE_REMOVE')
        task.event_memory = EventMemory()
        with self.assertRaises(RequestHumanTakeover):
            task.handle_sacrifice(selected)
        self.assertNotIn('SACRIFICE_CONFIRM', clicks)

    def test_unknown_fields_special_only_and_scan_boundary_never_pay(self):
        task, vision, clicks = self.payment()
        counter = vision.sacrifice_counter
        for value in (None, (0, 2), (2, 2)):
            vision.sacrifice_counter = Mock(return_value=value)
            self.assertFalse(task.handle_sacrifice(vision))
        vision.sacrifice_counter = counter
        vision.sacrifice_loot_count = Mock(return_value=None)
        self.assertFalse(task.handle_sacrifice(vision))
        vision.sacrifice_loot_count = Mock(return_value=1)
        special = vision.sacrifice_items()[1]
        vision.sacrifice_items = Mock(return_value=((), special))
        with self.assertRaisesRegex(RequestHumanTakeover, '都是特殊战利品'):
            task.handle_sacrifice(vision)
        vision.sacrifice_loot_count = Mock(return_value=5)
        self.assertTrue(task.handle_sacrifice(vision))
        self.assertTrue(task.handle_sacrifice(vision))
        with self.assertRaisesRegex(RequestHumanTakeover, '未找到可确认的普通战利品'):
            task.handle_sacrifice(vision)
        self.assertEqual(task.device.swipe.call_count, 2)
        self.assertEqual(clicks, [])

    def test_sampling_picker_is_not_completion_and_records_both_narrations(self):
        from tests.support.exploration import artifact_path
        from tasks.dimensional_exploration.sampling import EventSampler, sampling_balances
        task, vision, _ = task_for('20261004-161405-470')
        choices = [c for c, _ in vision.event_choices()]
        task.event_memory.begin(event_branch('forked_path.right_heal'))
        task.sampler = EventSampler(artifact_path())
        task.sampler.before(vision.image, vision.event_story(), choices, sampling_balances(vision),
                            choices[1], 'catalog', 'forked_path.right_heal')
        task.sampler.clicked()
        folder = task.sampler.active['folder']
        for suffix in ('20261004-161432-079', '20261004-161439-143'):
            payment = ExplorationVision(frame(suffix))
            task.device.image = payment.image
            task.observe_event('sacrifice', payment)
            task.observe_event('sacrifice', payment)
            self.assertFalse(task.event_memory.advanced)
            self.assertFalse(task.sampler.active['advanced'])
            self.assertEqual(task.event_memory.visited, set())
        for suffix in ('20261004-161443-261', '20261004-161448-012'):
            result = ExplorationVision(frame(suffix))
            task.device.image = result.image
            task.observe_event('event', result)
            task.observe_event('event', result)
        result = ExplorationVision(frame('20260922-081554-067'))
        task.device.image = result.image
        task.observe_event('map', result)
        self.assertIsNone(task.sampler.active)
        saved = json.loads((task.sampler.root / folder / 'record.json').read_text(encoding='utf-8'))
        self.assertEqual([f['state'] for f in saved['frames']], ['sacrifice', 'sacrifice', 'event', 'event', 'map'])
        self.assertEqual(saved['frames'][2]['delta']['loot'], -1)
        self.assertEqual(sum(c['completed'] for c in task.sampler.index['events'][saved['event']]['choices'].values()), 1)

    def test_main_loop_reads_hp_pays_normal_retries_and_reaches_both_results(self):
        suffixes = sum(([f'20261004-{suffix}'] * 3 for suffix in (
            '161405-470', '161414-135', '161405-470', '161432-079', '161439-143', '161443-261', '161448-012')), [])
        suffixes += ['20260922-081554-067', '20260922-081153-551']
        expected = ['EVENT_HERO_ENTRY'] * 2 + ['BACK'] + ['ExplorationEventChoice'] * 2 + [
            'SacrificeNormalSelect', 'SACRIFICE_CONFIRM', 'SACRIFICE_CONFIRM'] + ['EVENT_NEXT'] * 3
        with ControlledClock() as clock:
            task, _, _ = task_for(suffixes[0])
            device = ReplayDevice(suffixes, expected_clicks=expected)
            task.device = device
            task.action_ready = type(task).action_ready.__get__(task)
            task.click_action = type(task).click_action.__get__(task)
            task.progress, task.target = RunProgress(completed=1), 1
            task.handle_map = Mock(return_value=False)
            task.handle_ui_recovery = Mock(return_value=False)
            screenshot = device.screenshot
            def next_frame():
                clock.advance(1.1)
                screenshot()
            device.screenshot = next_frame
            # Selection, confirmation and narration are supplied real images.
            # The final map/lobby explicitly reuse old fixtures as a positive
            # exit; no actual return-to-map capture was supplied for this fork.
            task.explore()
            device.assert_actions_complete()
            self.assertIn('forked_path.right_heal', task.event_memory.visited)
            self.assertNotIn('forked_path.left_loot', task.event_memory.visited)
            self.assertIsNone(task.event_memory.pending)
            self.assertIsNone(task._sacrifice_selected)


class Oct03EventReceipts(unittest.TestCase):
    def replay_receipt(self, before_suffix, outcome_suffixes, expected_branch):
        from tests.support.exploration import artifact_path
        from tasks.dimensional_exploration.sampling import EventSampler, sampling_balances, text_key
        task, vision, _ = task_for(before_suffix)
        choices = [choice for choice, _ in vision.event_choices()]
        matched = match_event(choices, vision.event_story())
        self.assertIsNotNone(matched)
        branch = event_branch(expected_branch)
        choice = next(choice for choice in choices if re.fullmatch(branch.pattern, normalize(choice.text)))
        task.event_memory.begin(branch)
        task.sampler = EventSampler(artifact_path())
        task.sampler.before(vision.image, vision.event_story(), choices, sampling_balances(vision),
                            choice, 'catalog', branch.key)
        task.sampler.clicked()
        folder = task.sampler.active['folder']
        for suffix, state in outcome_suffixes:
            outcome = ExplorationVision(frame(suffix))
            task.device.image = outcome.image
            self.assertEqual(outcome.state(), state)
            task.observe_event(state, outcome)
            task.observe_event(state, outcome)
        self.assertIsNone(task.sampler.active)
        saved = json.loads((task.sampler.root / folder / 'record.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['finished'], 'map')
        self.assertEqual(len(saved['frames']), len(outcome_suffixes))
        self.assertIn(expected_branch, task.event_memory.visited)
        self.assertEqual(task.sampler.visits(vision.event_story()),
                         {text_key(choice.text): 1})
        return task, saved

    def test_backpack_loot_increase_does_not_prove_the_named_compass(self):
        task, saved = self.replay_receipt('20261003-112252-000',
            [('20261003-112252-001', 'event'), ('20261003-112252-002', 'map')], 'backpack.supplies')
        self.assertEqual(saved['frames'][0]['delta']['loot'], 1)
        self.assertEqual(saved['frames'][0]['delta']['fragments'], 0)
        self.assertNotIn('探险家指南针', task.event_memory.obtained)

    def test_mask_named_card_and_both_narration_pages_are_preserved(self):
        task, saved = self.replay_receipt('20261003-171834-000',
            [('20261003-171834-001', 'reward'), ('20261003-171834-002', 'event'),
             ('20261003-171834-003', 'event'), ('20261003-171834-004', 'map')], 'broken_mask.repair')
        self.assertIn('地下苔藓', task.event_memory.obtained)
        self.assertEqual(saved['frames'][0]['text'], '地下苔藓')
        self.assertEqual(saved['frames'][1]['delta']['loot'], 1)
        self.assertNotEqual(saved['frames'][1]['text'], saved['frames'][2]['text'])

    def test_prayer_is_free_experience_and_keeps_fragments(self):
        _, saved = self.replay_receipt('20261003-172757-000',
            [('20261003-172757-001', 'event'), ('20261003-172757-002', 'map')], 'prayer_tower.prayer')
        branch = event_branch('prayer_tower.prayer')
        self.assertEqual(branch.cost.fragments, 0)
        self.assertEqual(branch.rewards, ({'kind': 'experience', 'amount': 100},))
        self.assertTrue(all(frame['delta']['fragments'] == 0 for frame in saved['frames']))
