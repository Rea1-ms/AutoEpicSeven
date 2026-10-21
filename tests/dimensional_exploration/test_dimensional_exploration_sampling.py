"""Offline sampler checks; retained samples stay under the ignored screenshots folder."""
import json
import unittest
from unittest.mock import patch

import numpy as np
from tests.support.exploration import artifact_path, frame, make_task
from module.exception import RequestHumanTakeover
from tasks.dimensional_exploration.policy import EventChoice
from tasks.dimensional_exploration.sampling import EventSampler, choose_sample, inspect_option, text_key
from tasks.dimensional_exploration.vision import ExplorationVision


class SamplingTests(unittest.TestCase):
    def root(self):
        return artifact_path()

    def test_unknown_events_choose_cheap_trials_and_rotate_equal_cost_routes(self):
        choices = [EventChoice(0, '轻轻敲响钟声，获得2个次元骰子'),
                   EventChoice(1, '打开旁边的抽屉，获得50个次元碎片'), EventChoice(2, '径直离开')]
        balances = dict(cores=20, fragments=100, life=3, loot=2, dice=2)
        self.assertEqual(choose_sample(choices, balances).choice.index, 0)
        visits = {text_key(choices[0].text): 1}
        self.assertEqual(choose_sample(choices, balances, visits).choice.index, 1)
        paid = [EventChoice(0, '消耗50个次元碎片，获得战利品'), EventChoice(1, '径直离开')]
        self.assertEqual(choose_sample(paid, balances).choice.index, 0)
        self.assertEqual(choose_sample(paid, balances, {text_key(paid[0].text): 1}).choice.index, 1)

    def test_costs_are_all_parsed_and_life_and_investment_guards_hold(self):
        balances = dict(cores=19, fragments=90, life=2, loot=None, dice=None)
        for text in ('消耗50个次元碎片，获得1个战利品', '消耗2点生命体征，获得战利品',
                     '消耗1个次元骰子，获得次元碎片', '消耗未知代价，获得战利品',
                     '所有英雄生命值降低100%，获得战利品',
                     '消耗10个次元碎片，并失去1点生命体征，获得战利品',
                     '生命体征-1，获得次元碎片'):
            with self.subTest(text=text):
                self.assertIsNone(choose_sample([EventChoice(0, text)], balances))
        text = '消耗10个次元碎片，消耗1点生命体征，获得2个次元骰子'
        option = choose_sample([EventChoice(0, text)], balances)
        self.assertEqual((option.cost.fragments, option.cost.life), (10, 1))
        recovery = inspect_option(EventChoice(0, '所有英雄生命值恢复20%'))
        self.assertEqual(recovery.tier, 0)

    def test_exit_beats_hp_sacrifice_but_pending_click_cannot_switch(self):
        choices = [EventChoice(0, '所有英雄生命值降低25%，获得战利品'), EventChoice(1, '径直离开')]
        balances = dict(cores=20, fragments=100, life=3, loot=2, dice=2)
        self.assertEqual(choose_sample(choices, balances).choice.index, 1)
        self.assertEqual(choose_sample(choices, balances, pending_text=choices[0].text).choice.index, 0)
        self.assertIsNone(choose_sample(choices, balances, pending_text='上一次尚未确认的选项'))

    def test_receipt_waits_for_positive_progress_and_restart_deduplicates(self):
        root = self.root()
        sampler = EventSampler(root)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        choice = EventChoice(0, '免费获得2个次元骰子')
        resources = dict(cores=20, fragments=100, life=3, loot=2, dice=0)
        self.assertFalse(sampler.stable('新事件叙述', [choice], resources))
        self.assertTrue(sampler.stable('新事件叙述', [choice], resources))
        sampler.before(image, '新事件叙述', [choice], resources, choice, 'sampling', '无显示代价')
        folder = sampler.active['folder']
        sampler.before(image, '新事件叙述', [choice], resources, choice, 'sampling', '重复帧')
        self.assertEqual(sampler.active['folder'], folder)
        sampler.observe('map', image, resources=resources)
        self.assertFalse(sampler.visits('新事件叙述'))
        sampler.clicked()
        sampler.observe('unknown', image)
        sampler.observe('event', image)
        self.assertFalse(sampler.active['advanced'])
        restarted = EventSampler(root)
        self.assertEqual(restarted.pending_text('文字识别有细微变化'), choice.text)
        restarted.observe('event', image, text='获得了骰子', narration=True,
                          resources={**resources, 'dice': 2})
        restarted.observe('event', image, text='获得了骰子', narration=True,
                          resources={**resources, 'dice': 2})
        self.assertEqual(len(restarted.active['frames']), 1)
        self.assertEqual(restarted.active['frames'][0]['delta']['dice'], 2)
        restarted.observe('map', image, resources={**resources, 'dice': 2})
        self.assertIsNone(restarted.active)
        self.assertFalse(restarted.stable('新事件叙述', [choice], resources))
        self.assertEqual(restarted.visits('新事件叙述')[text_key(choice.text)], 1)
        record = json.loads((root / folder / 'record.json').read_text(encoding='utf-8'))
        (root / 'active.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        replay = EventSampler(root)
        self.assertIsNone(replay.active)
        self.assertEqual(replay.visits('新事件叙述')[text_key(choice.text)], 1)
        self.assertEqual(len(list((root / folder).glob('*.png'))), 3)

    def test_outcome_capture_is_bounded_without_losing_completion(self):
        sampler = EventSampler(self.root())
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        choice = EventChoice(0, '查看神秘装置')
        sampler.before(image, '神秘装置事件', [choice], {}, choice, 'sampling', '记录')
        sampler.clicked()
        for number in range(20):
            sampler.observe('reward', image, text=f'奖励{number}')
        self.assertEqual(len(sampler.active['frames']), 12)
        sampler.observe('map', image)
        self.assertIsNone(sampler.active)
        self.assertEqual(sampler.visits('神秘装置事件')[text_key(choice.text)], 1)

    def test_unlisted_screenshot_is_sampled_after_two_reads_and_outcome_is_saved(self):
        task, vision, clicks = make_task('20260922-081528-636')
        task.sampler = EventSampler(self.root())
        task.action_ready = lambda: True
        with patch('tasks.dimensional_exploration.dimensional_exploration.match_event', return_value=None):
            self.assertFalse(task.handle_event(vision))
            self.assertTrue(task.handle_event(vision))
        self.assertEqual(clicks, ['ExplorationEventSample'])
        self.assertIsNone(task.event_memory.pending)
        root, folder = task.sampler.root, task.sampler.active['folder']
        task.observe_event('reward', ExplorationVision(frame('20260922-081544-299')))
        task.observe_event('map', ExplorationVision(frame('20260922-081554-067')))
        self.assertIsNone(task.sampler.active)
        record = json.loads((root / folder / 'record.json').read_text(encoding='utf-8'))
        self.assertEqual(record['source'], 'sampling')
        self.assertEqual([frame['state'] for frame in record['frames']], ['reward', 'map'])
        self.assertEqual(record['frames'][0]['text'], '观测站望远镜镜片')
        self.assertFalse(task.event_memory.obtained)

    def test_sampling_disabled_still_stops_on_unlisted_event(self):
        task, vision, clicks = make_task('20260922-081528-636')
        self.assertIsNone(task.sampler)
        with patch('tasks.dimensional_exploration.dimensional_exploration.match_event', return_value=None):
            with self.assertRaises(RequestHumanTakeover):
                task.handle_event(vision)
        self.assertFalse(clicks)


if __name__ == '__main__':
    unittest.main()
