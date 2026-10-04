"""October 1 event outcomes, hero HP and multi-stage fragment budgets."""
import json
import unittest
from dataclasses import replace
from unittest.mock import Mock

import numpy as np

from tests.support.exploration import ControlledClock, ReplayDevice, artifact_path, frame, task_for
from tasks.dimensional_exploration.dimensional_exploration import DimensionalExploration
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import ROSTER_HP_BAR
from tasks.dimensional_exploration.event import EventCost, EventMemory, decide_event, event_branch, match_event
from tasks.dimensional_exploration.policy import EventChoice, RunProgress
from tasks.dimensional_exploration.sampling import EventSampler, text_key
from tasks.dimensional_exploration.vision import ExplorationVision


class Oct01Screenshots(unittest.TestCase):
    def test_new_page_identities_and_roster_precedence(self):
        for suffix, state in (
            ('20261001-132524-411', 'event'), ('20261001-133151-873', 'roster'),
            ('20261001-133603-790', 'event'), ('20261001-133740-628', 'event'),
            ('20261001-140630-704', 'event'), ('20261001-140638-153', 'event'),
            ('20261001-140648-336', 'event'), ('20261001-143340-283', 'resume_rewards'),
            ('20261001-143349-317', 'event'),
        ):
            with self.subTest(suffix=suffix):
                self.assertEqual(ExplorationVision(frame(suffix)).state(), state)

    def test_roster_marker_does_not_match_recruitment_upgrade_or_map(self):
        for suffix in ('20260922-081352-852', '20260923-232309-289',
                       '20260922-081522-090', '20261001-132524-411'):
            self.assertNotEqual(ExplorationVision(frame(suffix)).state(), 'roster')

    def test_roster_reads_all_six_names_and_native_hp_bars(self):
        vision = ExplorationVision(frame('20261001-133151-873'))
        heroes = vision.roster_health()
        self.assertIsNotNone(heroes)
        self.assertEqual(len(heroes), 6)
        values = {h.name: h.percent for h in heroes}
        for name, expected in (('亚敏', 75), ('雅咪德', 75), ('罪戾的安洁莉卡', 40),
                               ('起源拉斯', 75), ('午时戏水芙兰', 45), ('保健室老师律荷', 75)):
            self.assertAlmostEqual(values[name], expected, delta=3)

    def test_partial_roster_or_corrupt_bars_cannot_prove_hp(self):
        vision = ExplorationVision(frame('20261001-133151-873'))
        vision.number = Mock(return_value=7)
        self.assertIsNone(vision.roster_health())
        corrupt = frame('20261001-133151-873')
        x1, y1, x2, y2 = ROSTER_HP_BAR.area
        corrupt[y1:y2, x1:x2] = 0
        self.assertIsNone(ExplorationVision(corrupt).roster_health())

    def test_observatory_followup_exact_costs_and_disabled_final_option(self):
        for suffix, key, cost in (('20261001-140630-704', 'observatory_retry', 70),
                                  ('20261001-140638-153', 'observatory_final', 100)):
            vision = ExplorationVision(frame(suffix))
            choices = [c for c, _ in vision.event_choices()]
            matched = match_event(choices, vision.event_story())
            self.assertIsNotNone(matched)
            self.assertEqual(matched[0].event_id, key)
            self.assertEqual(matched[1][0][1].cost.fragments, cost)
            self.assertEqual(choices[0].available, cost == 70)
            self.assertEqual(len(choices), 2)


class Oct01Rules(unittest.TestCase):
    def choices(self, event_id):
        if event_id == 'observatory':
            vision = ExplorationVision(frame('20260922-081528-636'))
        else:
            suffix = '20261001-140630-704' if event_id == 'observatory_retry' else '20261001-140638-153'
            vision = ExplorationVision(frame(suffix))
        return [replace(c, available=True) for c, _ in vision.event_choices()]

    def test_fragment_budget_covers_all_remaining_stages_and_investment(self):
        for event_id, minimum in (('observatory', 220), ('observatory_retry', 170),
                                   ('observatory_final', 100)):
            choices = self.choices(event_id)
            for cores, reserve in ((20, 0), (19, 50)):
                params = dict(cores=cores, fragments=minimum + reserve, life=3, loot=1)
                self.assertEqual(decide_event(choices, **params).branch.branch_id, 'coin')
                params['fragments'] -= 1
                self.assertEqual(decide_event(choices, **params).branch.branch_id, 'leave')

    def test_collection_chain_spends_at_most_220_and_preserves_investment(self):
        memory = EventMemory()
        fragments = 270
        for event_id, cost in (('observatory', 50), ('observatory_retry', 70), ('observatory_final', 100)):
            decision = decide_event(self.choices(event_id), cores=19, fragments=fragments,
                                    life=3, loot=1, memory=memory)
            self.assertEqual(decision.branch.cost.fragments, cost)
            memory.begin(decision.branch)
            memory.observe('event', narration=True)
            fragments -= cost
        self.assertEqual(fragments, 50)
        memory.observe('reward', reward_name='观测站望远镜镜片')
        memory.observe('map')
        self.assertEqual(memory.obtained, {'观测站望远镜镜片'})
        self.assertEqual(decide_event(self.choices('observatory'), cores=19, fragments=270,
                                     life=3, loot=1, memory=memory).branch.key, 'observatory.leave')

    def test_damage_costs_keep_every_possible_target_alive(self):
        balances = dict(cores=20, fragments=500, life=3, loot=1, hero_hp=(75, 40, 45))
        for cost in (EventCost(all_hp_percent=40), EventCost(random_hp_percent=40)):
            self.assertTrue(cost.unavailable(**balances))
        self.assertFalse(EventCost(all_hp_percent=30).unavailable(**balances))
        self.assertTrue(EventCost(random_hp_percent=1).unavailable(**{**balances, 'hero_hp': (0, 75)}))
        self.assertTrue(EventCost(all_hp_percent=20).unavailable(**{**balances, 'hero_hp': ()}))

    def test_low_hp_uses_existing_free_fallback_without_spending_life(self):
        vision = ExplorationVision(frame('20260923-233133-331'))
        choices = [c for c, _ in vision.event_choices()]
        decision = decide_event(choices, cores=20, fragments=500, life=3, loot=1,
                                hero_hp=(24, 75), story=vision.event_story())
        self.assertEqual(decision.branch.key, 'backpack.remains')


class Oct01Flows(unittest.TestCase):
    def test_main_loop_inspects_hp_and_saves_garden_outcomes_with_missed_click_frames(self):
        suffixes = ['20261001-132524-411'] * 2 + ['20261001-133151-873'] * 3 + [
            '20261001-132524-411', '20261001-132524-411', '20261001-133603-790',
            '20261001-133603-790', '20261001-133740-628', '20261001-133740-628',
            '20260922-081554-067', '20260922-081153-551',
        ]
        expected = ['EVENT_HERO_ENTRY', 'BACK', 'ExplorationEventChoice', 'EVENT_NEXT', 'EVENT_NEXT']
        with ControlledClock() as clock:
            task, _, _ = task_for(suffixes[0])
            device = ReplayDevice(suffixes, expected_clicks=expected)
            task.device = device
            task.action_ready = DimensionalExploration.action_ready.__get__(task)
            task.click_action = DimensionalExploration.click_action.__get__(task)
            task.progress, task.target = RunProgress(completed=1), 1
            task.sampler = EventSampler(artifact_path())
            task.handle_map = Mock(return_value=False)
            task.handle_ui_recovery = Mock(return_value=False)
            screenshot = device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            task.explore()
            device.assert_actions_complete()
            self.assertEqual(task.event_memory.visited, {'garden.heal'})
            self.assertIsNone(task.sampler.active)
            self.assertIsNone(task._event_hero_hp)
            self.assertEqual(sum(c['completed'] for e in task.sampler.index['events'].values()
                                 for c in e['choices'].values()), 1)

    def test_rank_reward_claim_retries_without_continuing(self):
        task, vision, clicks = task_for('20261001-143340-283')
        self.assertEqual(vision.state(), 'resume_rewards')
        for _ in range(2):
            self.assertTrue(task.handle_victory(vision))
        self.assertEqual(clicks, ['ClaimExplorationBattleReward'] * 2)
        self.assertFalse(task._reward_leave_authorized)

    def test_rank_reward_is_captured_and_marks_branch_visited_once(self):
        task, reward, _ = task_for('20261001-143340-283')
        task.event_memory.begin(event_branch('blue_flower.rank'))
        task.sampler = EventSampler(artifact_path())
        choice = EventChoice(0, '将手伸向花朵')
        task.sampler.before(reward.image, '蓝色花朵', [choice], {}, choice, 'catalog', 'blue_flower.rank')
        task.sampler.clicked()
        for _ in range(2):
            task.observe_event('resume_rewards', reward)
        self.assertEqual(task.event_memory.visited, {'blue_flower.rank'})
        self.assertEqual([f['state'] for f in task.sampler.active['frames']], ['resume_rewards'])
        self.assertIn('英雄升阶', task.sampler.active['frames'][0]['text'])
        outcome = ExplorationVision(frame('20261001-143349-317'))
        task.device.image = outcome.image
        task.observe_event('event', outcome)
        self.assertEqual([f['state'] for f in task.sampler.active['frames']], ['resume_rewards', 'event'])

    def test_garden_saves_two_outcome_pages_and_deduplicates_repeated_frames(self):
        task, before, clicks = task_for('20261001-132524-411')
        task.sampler = EventSampler(artifact_path())
        # HP is read from the supplied roster through the same production handler.
        task.handle_event(before)
        roster = ExplorationVision(frame('20261001-133151-873'))
        task.device.image = roster.image
        task.handle_roster(roster)
        task.handle_roster(roster)
        task.device.image = before.image
        self.assertTrue(task.handle_event(before))
        folder = task.sampler.active['folder']
        for suffix in ('20261001-133603-790', '20261001-133740-628'):
            outcome = ExplorationVision(frame(suffix))
            task.device.image = outcome.image
            for _ in range(2):
                task.observe_event('event', outcome)
        map_vision = ExplorationVision(frame('20260922-081554-067'))
        task.device.image = map_vision.image
        task.observe_event('map', map_vision)
        saved = json.loads((task.sampler.root / folder / 'record.json').read_text(encoding='utf-8'))
        self.assertEqual([f['state'] for f in saved['frames']], ['event', 'event', 'map'])
        self.assertEqual(saved['selected_index'], 2)
        self.assertEqual(clicks, ['EVENT_HERO_ENTRY', 'BACK', 'ExplorationEventChoice'])
        self.assertEqual(task.sampler.visits(before.event_story())[text_key(saved['selected_text'])], 1)

    def test_event_checks_stable_complete_roster_before_hp_choice(self):
        task, event, clicks = task_for('20261001-132524-411')
        self.assertTrue(task.handle_event(event))
        self.assertEqual(clicks, ['EVENT_HERO_ENTRY'])
        roster = ExplorationVision(frame('20261001-133151-873'))
        task.device.image = roster.image
        self.assertFalse(task.handle_roster(roster))
        self.assertTrue(task.handle_roster(roster))
        task.device.image = event.image
        self.assertTrue(task.handle_event(event))
        self.assertTrue(task.handle_event(event))
        self.assertEqual(clicks, ['EVENT_HERO_ENTRY', 'BACK', 'ExplorationEventChoice', 'ExplorationEventChoice'])
        self.assertEqual(len(task._event_hero_hp), 6)
        task.device.swipe.assert_not_called()

    def test_partial_roster_waits_without_selecting_or_returning(self):
        task, event, clicks = task_for('20261001-132524-411')
        task.handle_event(event)
        roster = ExplorationVision(frame('20261001-133151-873'))
        roster.roster_health = Mock(return_value=None)
        task.device.image = roster.image
        self.assertFalse(task.handle_roster(roster))
        self.assertFalse(task.handle_roster(roster))
        self.assertEqual(clicks, ['EVENT_HERO_ENTRY'])
        self.assertIsNone(task._event_hero_hp)

    def test_restart_on_roster_returns_without_recruitment_actions(self):
        task, roster, clicks = task_for('20261001-133151-873')
        self.assertTrue(task.handle_roster(roster))
        self.assertEqual(clicks, ['BACK'])
        self.assertIsNone(task._event_hero_hp)

    def test_positive_outcome_clears_hp_but_unknown_frame_keeps_it(self):
        task, event, _ = task_for('20261001-132524-411')
        task.handle_event(event)
        roster = ExplorationVision(frame('20261001-133151-873'))
        task.device.image = roster.image
        task.handle_roster(roster)
        task.handle_roster(roster)
        hp = task._event_hero_hp
        task.observe_event('unknown', ExplorationVision(np.zeros_like(event.image)))
        self.assertEqual(task._event_hero_hp, hp)
        outcome = ExplorationVision(frame('20261001-133603-790'))
        task.device.image = outcome.image
        task.observe_event('event', outcome)
        self.assertIsNone(task._event_hero_hp)

    def test_roster_inspection_does_not_complete_event_sampling(self):
        task, roster, _ = task_for('20261001-133151-873')
        task.sampler = EventSampler(artifact_path())
        choice = EventChoice(0, '未确认的选项')
        task.sampler.before(roster.image, '事件', [choice], {}, choice, 'catalog', 'test')
        task.sampler.clicked()
        task.observe_event('roster', roster)
        self.assertFalse(task.sampler.active['advanced'])
        self.assertEqual(task.sampler.active['frames'], [])

    def test_followup_options_complete_prior_sampling_before_next_choice(self):
        task, stage, clicks = task_for('20261001-140630-704')
        task.sampler = EventSampler(artifact_path())
        choice = EventChoice(0, '尝试向投币口投入硬币')
        task.sampler.before(stage.image, '观测站投币口', [choice], {}, choice, 'catalog', 'observatory.coin')
        task.sampler.clicked()
        task.event_memory.begin(event_branch('observatory.coin'))
        folder = task.sampler.active['folder']
        self.assertTrue(task.handle_event(stage))
        saved = json.loads((task.sampler.root / folder / 'record.json').read_text(encoding='utf-8'))
        self.assertEqual([f['state'] for f in saved['frames']], ['event_options'])
        self.assertIn('70个次元碎片', saved['frames'][0]['text'])
        self.assertEqual(saved['finished'], 'next_options')
        self.assertEqual(task.event_memory.visited, {'observatory.coin'})
        # This real frame holds only 111, less than the remaining 70 + 100.
        self.assertEqual(task.event_memory.pending, 'observatory_retry.leave')
        self.assertEqual(clicks, ['ExplorationEventChoice'])

    def test_roster_full_and_empty_bars_keep_zero_distinct_from_missing_pixels(self):
        image = frame('20261001-133151-873')
        x1, y1, x2, y2 = ROSTER_HP_BAR.area
        for color, expected in (((139, 196, 25), 98), ((31, 35, 43), 0)):
            image[y1:y2, x1:x2] = color
            heroes = ExplorationVision(image).roster_health()
            self.assertIsNotNone(heroes)
            self.assertEqual(heroes[0].percent, expected)


if __name__ == '__main__':
    unittest.main()
