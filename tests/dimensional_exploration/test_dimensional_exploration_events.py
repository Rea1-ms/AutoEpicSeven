"""Event decisions against actual screenshots and restart/receipt regressions."""
from dataclasses import replace
import json
import unittest
from unittest.mock import Mock, patch

from tests.support.exploration import ROOT, frame, make_task, task_for
from module.exception import RequestHumanTakeover
from tasks.dimensional_exploration.event import (
    EventCondition, EventCost, EventMemory, decide_event, event_branch, event_catalog, match_event, pending_event,
)
from tasks.dimensional_exploration.policy import EventChoice, RunProgress
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
            ('20260922-081528-636', 'observatory.coin', 'observatory.leave'),
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
