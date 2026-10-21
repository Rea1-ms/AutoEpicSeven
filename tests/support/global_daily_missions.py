"""Reviewed overseas Chinese captures and time-faithful daily replays."""

from types import SimpleNamespace
from unittest.mock import Mock

from tests.support.daily_missions import MANIFEST, DailyScanReplay
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import fixture_image, record_frame

HOME = '20261017-004551-316'
MENU = '20261017-004552-856'
POPUP = '20261017-004606-299'
CLAIMED = '20261017-004607-640'
HOME_AFTER = '20261017-004609-828'
CAPTURES = (
    '20261017-004555-650', '20261017-004557-957', '20261017-004559-319',
    '20261017-004600-561', '20261017-004601-643', '20261017-004602-994',
)
VISIBLE_KEYS = (
    ('core_1', 'flower', 'hero'),
    ('hero', 'equipment', 'core_2'),
    ('core_3', 'login', 'stamina_50'),
    ('stamina_50', 'arena_5', 'gacha'),
    ('gacha', 'stamina_100', 'stamina_150'),
    ('stamina_100', 'stamina_150', 'arena_10'),
)
# Transcribed from the reviewed captures, independently of production rules.
# Completed missions move below unfinished missions in this 160-point list.
EXPECTED = {
    'core_1': (0, 1, 20), 'core_2': (0, 2, 20), 'core_3': (0, 3, 20),
    'flower': (0, 1, 15), 'hero': (0, 1, 15), 'equipment': (0, 1, 10),
    'login': (1, 1, 25),
    'stamina_50': (50, 50, 20), 'stamina_100': (100, 100, 20),
    'stamina_150': (150, 150, 20),
    'arena_5': (5, 5, 30), 'arena_10': (10, 10, 30), 'gacha': (1, 1, 15),
}


def task_type():
    from tasks.mission_reward.daily_reward import DailyMissionReward

    return DailyMissionReward


def capture_probe(fixture):
    task = object.__new__(task_type())
    task.config = SimpleNamespace(
        Emulator_PackageName='com.stove.epic7.google',
        Emulator_GameLanguage='cn', Scheduler_ServerUpdate='02:00',
    )
    task.device = SimpleNamespace(image=fixture_image(MANIFEST, fixture), stuck_record_add=Mock())
    task.interval_timer = {}
    return task


def captured(fixture, *, seconds=0.5):
    return scene(fixture=fixture, seconds=seconds)


class GlobalDailyCaptureReplay(InteractionReplay):
    """Use actual captures for all page, reward, popup and interval decisions."""

    def __init__(self, frames, clock):
        self.images = {}
        self.action_times = []
        self.swipes = []
        super().__init__(task_type(), frames, clock)
        self.task.match_template_color = self.match_template_color
        self.task.match_template_luma = self.match_template_luma

    def screenshot(self):
        super().screenshot()
        fixture = self.frame['fixture']
        if fixture not in self.images:
            self.images[fixture] = fixture_image(MANIFEST, fixture)
        self.image = self.images[fixture].copy()
        record_frame(fixture)

    def appear(self, button, interval=0, **kwargs):
        return task_type().appear(self.task, button, interval=interval, **kwargs)

    def match_template_color(self, button, interval=0, **kwargs):
        return task_type().match_template_color(self.task, button, interval=interval, **kwargs)

    def match_template_luma(self, button, interval=0, **kwargs):
        return task_type().match_template_luma(self.task, button, interval=interval, **kwargs)

    def click(self, button):
        super().click(button)
        self.action_times.append((self.index, button.name, self.clock.now))

    def swipe(self, start, end, **kwargs):
        super().swipe(start, end, **kwargs)
        self.swipes.append((self.index, start, end, self.clock.now))


class GlobalDailyScanReplay(DailyScanReplay):
    """Reuse shared synthetic list observations with the overseas configuration."""

    def __init__(self, frames, clock):
        self.swipes = []
        self.reads = []
        self.images = {}
        InteractionReplay.__init__(self, task_type(), frames, clock)
        self.task._read_daily_missions = self.read_missions
        self.task._read_daily_points = lambda: self.frame['points']
        self.task.match_template_color = self.match_template_color
