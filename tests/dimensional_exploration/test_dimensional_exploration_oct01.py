"""October 1 event outcomes, hero HP and multi-stage fragment budgets."""
import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import numpy as np

from tests.support.exploration import ControlledClock, ReplayDevice, artifact_path, frame, task_for
from tasks.dimensional_exploration.dimensional_exploration import DimensionalExploration
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import ROSTER_HP_BAR
from tasks.dimensional_exploration.event import EventCost, EventMemory, decide_event, event_branch, match_event
from tasks.dimensional_exploration.policy import EventChoice, RunProgress
from tasks.dimensional_exploration.sampling import EventSampler, text_key
from tasks.dimensional_exploration.vision import ExplorationVision
from tasks.base.assets.assets_base_popup import NETWORK_ERROR_ABNORMAL, TOUCH_TO_CLOSE
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import EVENT_NEXT


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
        self.assertTrue(EventCost(all_hp_percent=30).unavailable(**balances))
        self.assertFalse(EventCost(all_hp_percent=30).unavailable(**{**balances, 'hero_hp': (75, 55, 60)}))
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


class FirelightHealthRegressions(unittest.TestCase):
    @staticmethod
    def options():
        vision = ExplorationVision(frame('20261004-134120-334'))
        return vision, vision.event_choices(), vision.event_story()

    def test_supplied_firelight_and_complete_roster_read_lowest_hp(self):
        vision, choices, story = self.options()
        self.assertEqual(vision.state(), 'event')
        self.assertEqual(len(choices), 3)
        self.assertEqual(match_event([c for c, _ in choices], story)[0].event_id, 'faint_fire')
        roster = ExplorationVision(frame('20261004-134045-652'))
        self.assertEqual(roster.state(), 'roster')
        heroes = roster.roster_health()
        self.assertIsNotNone(heroes)
        self.assertEqual(len(heroes), 6)
        self.assertEqual(min(h.percent for h in heroes), 41)

    def test_all_and_random_damage_leave_at_least_twenty_five_percent(self):
        balances = dict(cores=20, fragments=500, life=4, loot=5)
        for damage in (1, 25, 30, 40, 50):
            for cost in (EventCost(all_hp_percent=damage), EventCost(random_hp_percent=damage)):
                with self.subTest(cost=cost):
                    self.assertTrue(cost.unavailable(**balances, hero_hp=(98, damage + 24)))
                    self.assertFalse(cost.unavailable(**balances, hero_hp=(98, damage + 25)))
                    self.assertTrue(cost.unavailable(**balances, hero_hp=(0, 98)))
                    self.assertTrue(cost.unavailable(**balances, hero_hp=()))

    def test_firelight_prefers_loot_only_above_the_cost_adjusted_floor(self):
        _, choices, story = self.options()
        balances = dict(cores=52, fragments=378, life=4, loot=5, dice=3, story=story)
        for hp, expected in ((41, 'rest'), (54, 'rest'), (55, 'loot'), (98, 'loot')):
            with self.subTest(hp=hp):
                decision = decide_event([c for c, _ in choices], **balances, hero_hp=(98, hp))
                self.assertEqual(decision.branch.key, 'faint_fire.' + expected)

    def test_dice_route_is_disabled_even_when_other_choices_are_unavailable(self):
        _, choices, story = self.options()
        choices = [replace(c, available=c.index == 1) for c, _ in choices]
        self.assertFalse(event_branch('faint_fire.dice').enabled)
        self.assertIsNone(decide_event(choices, cores=52, fragments=378, life=4, loot=5,
                                      dice=3, hero_hp=(98, 98), story=story))

    def test_known_ocr_variants_keep_roster_result_without_reopening(self):
        task, event, clicks = task_for('20261004-134120-334')
        choices, story = event.event_choices(), event.event_story()
        self.assertTrue(task.handle_event(event))
        roster = ExplorationVision(frame('20261004-134045-652'))
        task.device.image = roster.image
        self.assertFalse(task.handle_roster(roster))
        self.assertTrue(task.handle_roster(roster))
        task.device.image = event.image
        hp = task._event_hero_hp
        for spelling in ('般子', '殷', '骰子'):
            event.event_choices = lambda spelling=spelling: [
                (replace(c, text=c.text.replace('次元殷', '次元' + spelling)), area) for c, area in choices]
            event.event_story = lambda: story.replace('摇电', '摇曳')
            self.assertTrue(task.handle_event(event))
            self.assertEqual(task._event_hero_hp, hp)
            self.assertEqual(task.event_memory.pending, 'faint_fire.rest')
        self.assertEqual(clicks, ['EVENT_HERO_ENTRY', 'BACK'] + ['ExplorationEventChoice'] * 3)

    def test_reordered_known_options_keep_confirmed_hp(self):
        task, event, clicks = task_for('20261004-134120-334')
        choices, story = event.event_choices(), event.event_story()
        self.assertEqual(task.inspect_event_health(event, [c for c, _ in choices], story), (False, True))
        task._event_hero_hp = (98, 41, 98, 98, 98, 98)
        self.assertEqual(task.inspect_event_health(event, [c for c, _ in reversed(choices)], story), (True, False))
        self.assertEqual(clicks, ['EVENT_HERO_ENTRY'])

    def test_new_known_event_requires_new_roster_and_partial_rows_wait(self):
        task, event, clicks = task_for('20261004-134120-334')
        choices, story = event.event_choices(), event.event_story()
        task.inspect_event_health(event, [c for c, _ in choices], story)
        task._event_hero_hp = (98, 41, 98, 98, 98, 98)
        event.event_choices = lambda: choices[:2]
        self.assertFalse(task.handle_event(event))
        self.assertEqual(task._event_hero_hp[1], 41)
        other = ExplorationVision(frame('20261001-132524-411'))
        task.device.image = other.image
        self.assertTrue(task.handle_event(other))
        self.assertIsNone(task._event_hero_hp)
        self.assertEqual(clicks, ['EVENT_HERO_ENTRY', 'EVENT_HERO_ENTRY'])

    def test_sampling_pending_damage_also_obeys_confirmed_hp_floor(self):
        from tasks.dimensional_exploration.sampling import choose_sample
        choice = EventChoice(0, '所有英雄生命值降低30%，获得战利品')
        balances = dict(cores=20, fragments=500, life=4, loot=5, dice=3, hero_hp=(98, 54))
        self.assertIsNone(choose_sample([choice], balances, pending_text=choice.text))
        balances['hero_hp'] = (98, 55)
        self.assertEqual(choose_sample([choice], balances, pending_text=choice.text).choice.index, 0)

    def test_main_loop_reads_roster_once_and_retries_rest_despite_ocr_changes(self):
        # Only the event and roster were supplied. The existing map/lobby
        # fixtures explicitly model the eventual exit; no healing outcome
        # or change in hero HP is fabricated as a captured result.
        suffixes = ['20261004-134120-334'] * 2 + ['20261004-134045-652'] * 3 + [
            '20261004-134120-334', '20261004-134120-334', '20261004-134120-334',
            '20260922-081554-067', '20260922-081153-551',
        ]
        event, choices, story = self.options()
        expected = ['EVENT_HERO_ENTRY', 'BACK', 'ExplorationEventChoice', 'ExplorationEventChoice']
        with ControlledClock() as clock:
            task, _, _ = task_for(suffixes[0])
            device = ReplayDevice(suffixes, expected_clicks=expected)
            task.device = device
            task.action_ready = DimensionalExploration.action_ready.__get__(task)
            task.click_action = DimensionalExploration.click_action.__get__(task)
            task.progress, task.target = RunProgress(completed=1), 1
            task.handle_map = Mock(return_value=False)
            task.handle_ui_recovery = Mock(return_value=False)
            original_choices = ExplorationVision.event_choices

            def read_choices(vision):
                if np.array_equal(vision.image, event.image):
                    spelling = '般子' if device.index < 5 else '殷'
                    return [(replace(c, text=c.text.replace('次元殷', '次元' + spelling)), area)
                            for c, area in choices]
                return original_choices(vision)

            screenshot = device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            with patch.object(ExplorationVision, 'event_choices', read_choices), patch.object(
                    ExplorationVision, 'event_story', return_value=story):
                task.explore()
            device.assert_actions_complete()
            self.assertEqual(task.event_memory.visited, {'faint_fire.rest'})
            self.assertFalse(task.event_memory.obtained)
            self.assertIsNone(task.event_memory.pending)
            self.assertIsNone(task._event_hero_hp)
            task.handle_map.assert_called_once()


class FirelightOutcomeRegressions(unittest.TestCase):
    def test_real_rest_result_and_roster_confirm_fifteen_percent_healing(self):
        stories = []
        for suffix in ('20261004-140207-882', '20261004-140210-503'):
            task, vision, _ = task_for(suffix)
            self.assertEqual(vision.state(), 'event')
            self.assertTrue(task.appear(EVENT_NEXT))
            self.assertEqual(vision.event_choices(), [])
            story = vision.event_story()
            self.assertIn('抚慰了伤口', story)
            stories.append(story)
        self.assertEqual(stories[0], stories[1])
        before = ExplorationVision(frame('20261004-134045-652')).roster_health()
        after = ExplorationVision(frame('20261004-140215-765')).roster_health()
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertEqual([h.name for h in before], [h.name for h in after])
        self.assertEqual(len(after), 6)
        self.assertEqual((before[1].percent, after[1].percent), (41, 56))
        self.assertEqual(after[1].percent - before[1].percent, 15)
        self.assertEqual([h.percent for h in after if h.name != before[1].name], [98] * 5)
        returned = ExplorationVision(frame('20261004-140228-740'))
        self.assertEqual(returned.state(), 'map')
        self.assertEqual((returned.resources().cores, returned.resources().fragments), (52, 378))

    def test_network_markers_distinguish_countdown_retry_and_clear_pages(self):
        for suffix, overlay, retry in (
            ('20261004-140222-304', True, False), ('20261004-140225-272', True, True),
            ('20261004-140207-882', False, False), ('20261004-140210-503', False, False),
            ('20261004-140215-765', False, False), ('20261004-140228-740', False, False),
        ):
            with self.subTest(suffix=suffix):
                task, vision, _ = task_for(suffix)
                self.assertEqual(task.appear(NETWORK_ERROR_ABNORMAL), overlay)
                self.assertEqual(task.appear(TOUCH_TO_CLOSE), retry)
                if overlay:
                    # The dimmed background still matches the event and its
                    # continuation marker: page identity alone is not a guard.
                    self.assertEqual(vision.state(), 'event')
                    self.assertTrue(task.appear(EVENT_NEXT))

    def test_overlay_waits_then_retries_visible_button_at_production_interval(self):
        with ControlledClock() as clock:
            task, _, _ = task_for('20261004-140222-304')
            task.action_ready = DimensionalExploration.action_ready.__get__(task)
            task.click_action = DimensionalExploration.click_action.__get__(task)
            clicks = []
            task.device.click = lambda button: clicks.append((str(button), tuple(button.button)))
            task.device.sleep = Mock(side_effect=AssertionError('Recovery must inspect a fresh screenshot'))
            for delay in (0, 45):
                clock.advance(delay)
                self.assertTrue(task.handle_network_overlay())
                self.assertFalse(task._unreadable.reached())
                self.assertEqual(clicks, [])
            task.device.image = frame('20261004-140225-272')
            self.assertTrue(task.handle_network_overlay())
            clock.advance(1)
            self.assertTrue(task.handle_network_overlay())
            self.assertEqual(len(clicks), 1)
            clock.advance(1.1)
            self.assertTrue(task.handle_network_overlay())
            self.assertEqual([name for name, _ in clicks], ['TOUCH_TO_CLOSE'] * 2)
            for _, (x1, y1, x2, y2) in clicks:
                self.assertTrue(622 <= x1 < x2 <= 770)
                self.assertTrue(457 <= y1 < y2 < 600)
            task.device.image = frame('20261004-140210-503')
            self.assertFalse(task.handle_network_overlay())
            self.assertEqual(len(clicks), 2)
            task.device.sleep.assert_not_called()

    def test_main_loop_freezes_pending_event_until_network_overlay_clears(self):
        # Repeated captures explicitly model missed retry clicks. The clear
        # narration is reused to model recovery before the captured map; it
        # is not claimed to be an extra screenshot taken after retry.
        suffixes = ['20261004-140222-304'] * 2 + ['20261004-140225-272'] * 3 + [
            '20261004-140210-503', '20261004-140210-503',
            '20261004-140228-740', '20260922-081153-551',
        ]
        expected = ['TOUCH_TO_CLOSE', 'TOUCH_TO_CLOSE', 'EVENT_NEXT']
        with ControlledClock() as clock:
            task, _, _ = task_for(suffixes[0])
            task.config.Emulator_PackageName = 'com.stove.epic7.google'
            device = ReplayDevice(suffixes, expected_clicks=expected)
            task.device = device
            task.action_ready = DimensionalExploration.action_ready.__get__(task)
            task.click_action = DimensionalExploration.click_action.__get__(task)
            task.progress, task.target = RunProgress(completed=1), 1
            task.handle_map = Mock(return_value=False)
            task.device.sleep = Mock(side_effect=AssertionError('Recovery must not sleep'))
            task._event_hero_hp = (98, 41, 98, 98, 98, 98)
            before = ExplorationVision(frame('20261004-134120-334'))
            choices = [c for c, _ in before.event_choices()]
            story = before.event_story()
            task.sampler = EventSampler(artifact_path())
            task.sampler.before(before.image, story, choices, {}, choices[0], 'catalog', 'faint_fire.rest')
            task.sampler.clicked()
            folder = task.sampler.active['folder']
            task.event_memory.begin(event_branch('faint_fire.rest'))
            task.observe_event = Mock(wraps=task.observe_event)
            screenshot = device.screenshot

            def next_frame():
                if device.index < 5:
                    self.assertEqual(task.event_memory.pending, 'faint_fire.rest')
                    self.assertFalse(task.event_memory.advanced)
                    self.assertEqual(task._event_hero_hp[1], 41)
                    self.assertEqual(task.sampler.active['frames'], [])
                    task.observe_event.assert_not_called()
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            task.explore()
            device.assert_actions_complete()
            saved = json.loads((task.sampler.root / folder / 'record.json').read_text(encoding='utf-8'))
            self.assertEqual([f['state'] for f in saved['frames']], ['event', 'map'])
            self.assertEqual(saved['finished'], 'map')
            self.assertEqual(task.sampler.visits(story)[text_key(choices[0].text)], 1)
            self.assertEqual(task.event_memory.visited, {'faint_fire.rest'})
            self.assertIsNone(task.event_memory.pending)
            self.assertIsNone(task._event_hero_hp)
            task.handle_map.assert_called_once()
            task.device.sleep.assert_not_called()

    def test_captured_result_roster_network_and_map_do_not_repeat_rest(self):
        # The user opened the healed roster manually. Replay that external
        # navigation, rather than adding a new automatic post-event visit.
        # All six supplied scenes retain their captured order; extra frames
        # model observing the same page and a retry click that did not land.
        suffixes = ['20261004-140207-882', '20261004-140210-503'] + [
            '20261004-140215-765'] * 2 + ['20261004-140222-304'] * 2 + [
            '20261004-140225-272'] * 3 + ['20261004-140228-740', '20260922-081153-551']
        expected = ['EVENT_NEXT', 'BACK', 'TOUCH_TO_CLOSE', 'TOUCH_TO_CLOSE']
        with ControlledClock() as clock:
            task, _, _ = task_for(suffixes[0])
            task.config.Emulator_PackageName = 'com.stove.epic7.google'
            device = ReplayDevice(suffixes, expected_clicks=expected)
            task.device = device
            task.action_ready = DimensionalExploration.action_ready.__get__(task)
            task.click_action = DimensionalExploration.click_action.__get__(task)
            task.progress, task.target = RunProgress(completed=1), 1
            task.handle_map = Mock(return_value=False)
            before = ExplorationVision(frame('20261004-134120-334'))
            choices = [c for c, _ in before.event_choices()]
            story = before.event_story()
            task.sampler = EventSampler(artifact_path())
            task.sampler.before(before.image, story, choices, {}, choices[0], 'catalog', 'faint_fire.rest')
            task.sampler.clicked()
            folder = task.sampler.active['folder']
            task.event_memory.begin(event_branch('faint_fire.rest'))
            screenshot = device.screenshot

            def next_frame():
                clock.advance(1.1)
                screenshot()

            device.screenshot = next_frame
            task.explore()
            device.assert_actions_complete()
            saved = json.loads((task.sampler.root / folder / 'record.json').read_text(encoding='utf-8'))
            self.assertEqual([f['state'] for f in saved['frames']], ['event', 'map'])
            self.assertEqual(saved['selected_index'], 0)
            self.assertEqual(task.sampler.visits(story)[text_key(choices[0].text)], 1)
            self.assertEqual(task.event_memory.visited, {'faint_fire.rest'})
            self.assertFalse(task.event_memory.obtained)
            self.assertIsNone(task.event_memory.pending)
            self.assertIsNone(task._event_hero_hp)
            task.handle_map.assert_called_once()


if __name__ == '__main__':
    unittest.main()
