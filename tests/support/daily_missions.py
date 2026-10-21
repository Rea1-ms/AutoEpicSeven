"""Reviewed CN captures and explicit observations for daily-list replay."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import fixture_image, record_frame

MANIFEST = Path(__file__).resolve().parents[1] / 'fixtures/historical/manifest.json'
HOME = '20261015-195327-585'
CAPTURES = (
    '20261015-195330-291',
    '20261015-195332-961',
    '20261015-195334-507',
    '20261015-195336-031',
    '20261015-195337-685',
    '20261015-195339-394',
)
VISIBLE_KEYS = (
    ('stamina_50', 'core_1', 'arena_5'),
    ('arena_5', 'gacha', 'flower'),
    ('flower', 'hero', 'equipment'),
    ('stamina_100', 'stamina_150', 'core_2'),
    ('stamina_150', 'core_2', 'core_3'),
    ('core_3', 'arena_10', 'login'),
)
# These expectations were transcribed from the supplied captures. They do not
# read production rules, so a changed target or reward remains detectable.
EXPECTED = {
    'stamina_50': (0, 50, 20),
    'stamina_100': (0, 100, 20),
    'stamina_150': (0, 150, 20),
    'core_1': (0, 1, 20),
    'core_2': (0, 2, 20),
    'core_3': (0, 3, 20),
    'arena_5': (0, 5, 30),
    'arena_10': (0, 10, 30),
    'gacha': (0, 1, 15),
    'flower': (0, 1, 15),
    'hero': (0, 1, 15),
    'equipment': (0, 1, 10),
    'login': (1, 1, 25),
}


def task_type():
    # Callers select and restore CN inside each case, before this lazy import.
    from tasks.mission_reward.daily_reward import DailyMissionReward

    return DailyMissionReward


def capture_probe(fixture):
    task = object.__new__(task_type())
    task.config = SimpleNamespace(
        Emulator_PackageName='com.zlongame.cn.epicseven',
        Emulator_GameLanguage='cn', Scheduler_ServerUpdate='03:00',
    )
    task.device = SimpleNamespace(image=fixture_image(MANIFEST, fixture), stuck_record_add=Mock())
    task.interval_timer = {}
    return task


def mission_scene(keys=(), *, readable=True, page=True, dimmed=False, seconds=0.5,
                  after_swipes=0, expected=EXPECTED, points=25):
    """Synthetic observations, deliberately separate from screenshot evidence."""
    from tasks.mission_reward.daily import DailyMission

    rows = tuple(DailyMission(key, *expected[key]) for key in keys) if readable else None
    buttons = ['MISSION_REWARD_DAILY_ENTRY_CHECK' if page else 'OTHER_PAGE']
    if dimmed:
        buttons.append('TOUCH_TO_CLOSE')
    return scene(*buttons, missions=rows, seconds=seconds, dimmed=dimmed,
                 after_swipes=after_swipes, points=points)


def scan_frames(panels=VISIBLE_KEYS, *, expected=EXPECTED, points=25):
    """Explicit half-second observations with delayed, independent scroll results."""
    # Read while moving down, without a preliminary boundary probe or reverse
    # pass. Two old frames after the first swipe model a delayed response;
    # every new panel requires its own preceding swipe and real action timer.
    frames = [mission_scene(panels[0], expected=expected, points=points)] * 4
    for index, keys in enumerate(panels[1:], start=1):
        frames.extend([mission_scene(keys, after_swipes=index, expected=expected, points=points)] * 5)
    return frames


class DailyScanReplay(InteractionReplay):
    """Clicks and swipes never advance the observation or bypass real timers."""

    def __init__(self, frames, clock):
        self.swipes = []
        self.reads = []
        self.images = {}
        super().__init__(task_type(), frames, clock)
        self.task.config.Emulator_PackageName = 'com.zlongame.cn.epicseven'
        self.task.config.Emulator_GameLanguage = 'cn'
        self.task.config.Scheduler_ServerUpdate = '03:00'
        self.task._read_daily_missions = self.read_missions
        self.task._read_daily_points = lambda: self.frame['points']
        self.task.match_template_color = self.match_template_color

    def screenshot(self):
        super().screenshot()
        if len(self.swipes) < self.frame.get('after_swipes', 0):
            raise AssertionError('A scroll result arrived before its required swipe')
        # Production stability checks still compare real pixel arrays. Each
        # explicit observation has a repeatable raster; the metadata remains in
        # self.frame, and neither clicking nor swiping advances the observation.
        signature = (self.frame['missions'], self.frame['buttons'], self.frame['dimmed'])
        if signature not in self.images:
            self.images[signature] = np.full((720, 1280, 3), 20 + 15 * len(self.images), dtype=np.uint8)
        self.image = self.images[signature]

    def match_template_color(self, button, **kwargs):
        return self.appear(button, **kwargs) and not self.frame['dimmed']

    def read_missions(self):
        self.reads.append(self.index)
        record_frame(f'synthetic:daily-list-observation:{self.index}')
        return self.frame['missions']

    def swipe(self, start, end, **kwargs):
        super().swipe(start, end, **kwargs)
        self.swipes.append((self.index, start, end, self.clock.now))

    def assert_swipe_intervals(self, testcase):
        for earlier, later in zip(self.swipes, self.swipes[1:]):
            testcase.assertGreaterEqual(later[3] - earlier[3], 2, self.swipes)
