"""Event decisions against actual screenshots and restart/receipt regressions."""
from dataclasses import replace
import unittest

from tests.support.exploration import frame, make_task
from tasks.dimensional_exploration.event import (
    EventCost, EventMemory, decide_event, event_branch, match_event,
)
from tasks.dimensional_exploration.policy import RunProgress
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
            ('20260924-193808-685', 'sword.pay', 'sword.touch'),
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
        self.assertEqual(decide_event(choices, memory=restored, **params).branch.key, 'sword.touch')

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
        self.assertEqual(decide_event(choices, memory=restored, **params).branch.key, 'sword.touch')
        # A known acquisition suppresses both payment and combat first bonuses,
        # even if the observed branch list was imported without those visits.
        restored.visited.clear()
        self.assertEqual(decide_event(choices, memory=restored, **params).branch.key, 'sword.touch')

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


if __name__ == '__main__':
    unittest.main()
