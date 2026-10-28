"""Episode observations with real pixels or explicitly synthetic states."""

from tasks.dungeon.episode_push import Episode
from tests.support.interaction import InteractionReplay
from tests.support.offline import ROOT, fixture_image

MANIFEST = ROOT / 'tests/fixtures/historical/manifest.json'


class EpisodeReplay(InteractionReplay):
    def __init__(self, frames, clock):
        self.images = {}
        self.positions = []
        super().__init__(Episode, frames, clock)
        self.task.completed_stages = 0
        self.task.config.Episode_Number = 0
        self.task.config.Episode_MaxStages = 1
        self.task._map_ready = self.map_ready
        self.task._map_identity = self.map_identity
        self.task._read_stages = self.stages
        self.task._prepare_stamina = self.stamina
        self.task._last_star_condition = self.last_star_condition
        self.task._selector_episode = self.selector
        self.task._is_episode_selector = self.selector_ready
        self.task._chapter_entries = self.chapter_entries
        self.task.is_in_main = lambda **kw: self.frame.get('main', False)
        self.task.match_template_color = self.color
        self.task.match_color = self.fixed_color
        self.task.match_template_luma = self.luma
        self.task.ui_goto = self.route

    def screenshot(self):
        super().screenshot()
        if self.frame.get('fixture'):
            key = self.frame['fixture']
            if key not in self.images:
                self.images[key] = fixture_image(MANIFEST, key)
            self.image = self.images[key].copy()

    def appear(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return Episode.appear(self.task, button, interval=interval, **kwargs)
        return super().appear(button, interval=interval, **kwargs)

    def color(self, button, **kwargs):
        if self.frame.get('fixture'):
            return Episode.match_template_color(self.task, button, **kwargs)
        return self.appear(button, **kwargs)

    def fixed_color(self, button, **kwargs):
        if self.frame.get('fixture'):
            return Episode.match_color(self.task, button, **kwargs)
        return self.appear(button, **kwargs)

    def luma(self, button, **kwargs):
        # Real screenshots must use the production luminance matcher. The
        # parent replay replaces it with ordinary RGB matching for synthetic
        # scenes, which otherwise hides differences in supporter detection.
        if self.frame.get('fixture'):
            return Episode.match_template_luma(self.task, button, **kwargs)
        return self.appear(button, **kwargs)

    def map_ready(self):
        if self.frame.get('fixture'):
            return Episode._map_ready(self.task)
        return self.frame.get('map_ready', False)

    def map_identity(self):
        if self.frame.get('fixture'):
            return Episode._map_identity(self.task)
        return self.frame.get('identity', (6, 1)), self.frame.get('selected', 3)

    def stages(self):
        if self.frame.get('fixture'):
            return Episode._read_stages(self.task)
        return self.frame.get('rows', [])

    def stamina(self):
        if self.frame.get('fixture'):
            return Episode._prepare_stamina(self.task)
        return self.frame.get('stamina')

    def last_star_condition(self):
        if self.frame.get('fixture'):
            return Episode._last_star_condition(self.task)
        # Existing synthetic prepare scenes explicitly model ordinary combat
        # conditions. Real fixtures always use production OCR, without a default.
        return self.frame.get('star_condition', '配置1名战士职业英雄到队伍中')

    def selector(self):
        if self.frame.get('fixture'):
            return Episode._selector_episode(self.task)
        return self.frame.get('selector')

    def selector_ready(self):
        if self.frame.get('fixture'):
            return Episode._is_episode_selector(self.task)
        return 'selector' in self.frame

    def chapter_entries(self):
        if self.frame.get('fixture'):
            return Episode._chapter_entries(self.task)
        return self.frame.get('chapter_entries', [])

    def route(self, page, **kwargs):
        self.actions.append((self.index, f'route:{page.name}'))
        self.screenshot()

    def click(self, button):
        super().click(button)
        self.positions.append((self.index, button.name, button.button))

    def click_record_remove(self, button):
        self.click_record = type(self.click_record)(
            (item for item in self.click_record if item != button.name), maxlen=30,
        )


class Episode4Replay(EpisodeReplay):
    """Run the farming navigator with real entry frames and explicit map states."""

    def __init__(self, frames, clock, *, episode=4, material='BREATH_OF_KARMA'):
        super().__init__(frames, clock)
        self.task.config.Combat_EpisodeNumber = episode
        self.task.config.Combat_Episode4Material = material
        self.task._is_prepare_page = lambda: self.frame.get('prepare', False)
        self.task._is_episode_choose_page = self.farming_map
        self.task._is_episode_stage_page = self.farming_map
        self.task._ocr_episode_number = self.episode_number
        self.task._ocr_episode_chapter_index = self.chapter_index
        self.task._ocr_episode_current_stage_index = lambda: self.frame.get('stage', 0)
        self.task._episode_has_target_material = lambda plan: self.frame.get('material', False)
        self.task._handle_dungeon_additional = lambda: False

    def farming_map(self):
        if self.frame.get('fixture'):
            return Episode._is_episode_choose_page(self.task) or Episode._is_episode_stage_page(self.task)
        return self.frame.get('map_ready', False)

    def episode_number(self):
        if self.frame.get('fixture'):
            return Episode._ocr_episode_number(self.task)
        return self.frame.get('episode')

    def chapter_index(self):
        if self.frame.get('fixture'):
            return Episode._ocr_episode_chapter_index(self.task)
        return self.frame.get('chapter', 0)
