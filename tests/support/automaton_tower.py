"""Tower capture replay with real recognition and production action intervals."""

import numpy as np

from module.base.base import ModuleBase
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ROOT, fixture_image, record_frame

MANIFEST = ROOT / "tests/fixtures/automaton_tower/manifest.json"
RESUME_CAPTURES = (
    "2026-11-15_14-42-58-668558", "2026-11-15_14-43-02-635705",
    "2026-11-15_14-43-06-134378", "2026-11-15_14-43-11-056885",
    "2026-11-15_14-43-15-991275", "2026-11-15_14-43-21-857733",
    "2026-11-15_14-43-24-984899", "2026-11-15_14-43-29-271926",
    "2026-11-15_14-43-32-175894", "2026-11-15_14-43-39-909021",
)
HOME, COMBAT, SEASON, TOWER, PENDING, SELECTED, FLOOR, PREPARE, PREPARE_REPEAT, BATTLE = RESUME_CAPTURES
CLEAR = "2026-11-15_00-21-39-738751"
BATTLE_PENDING = "2026-11-15_15-19-29-075489"
BATTLE_SELECTED = "2026-11-15_00-21-36-380571"
POST_CLEAR_OFFER = "2026-11-18_21-17-55-551"


def capture(fixture=None, seconds=0.5):
    return scene(fixture=fixture, seconds=seconds)


class TowerReplay(InteractionReplay):
    def __init__(self, frames, clock):
        from tasks.automaton_tower.automaton_tower import AutomatonTower

        self.images = {}
        self.click_areas = []
        super().__init__(AutomatonTower, frames, clock)
        # Keep all tower/navigation recognition and timers. Only unrelated
        # startup, network and generic popup handling is outside this replay.
        for name in ("appear", "match_template_color", "match_template_luma"):
            setattr(self.task, name, getattr(ModuleBase, name).__get__(self.task))
        self.task.ui_page_appear = AutomatonTower.ui_page_appear.__get__(self.task)
        # Keep tower-specific modal handling in offline navigation. Generic
        # startup/network dialogs remain outside this replay's scope.
        self.task.ui_additional = self.task._handle_tower_entry
        self.task.handle_ui_recovery = lambda: False
        self.task.handle_network_error = lambda: False
        self.task.handle_popup_confirm = lambda **kwargs: False

    def screenshot(self):
        super().screenshot()
        fixture = self.frame["fixture"]
        if fixture is None:
            self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
            record_frame("synthetic:tower-unknown-transition")
        else:
            if fixture not in self.images:
                self.images[fixture] = fixture_image(MANIFEST, fixture)
            self.image = self.images[fixture].copy()
            record_frame(fixture)

    def click(self, button):
        # The actual Device.click also requires a named button with a rectangle;
        # accepting an arbitrary tuple here would hide a real-device crash.
        assert hasattr(button, "button") and len(button.button) == 4, button
        super().click(button)
        self.click_areas.append((self.index, button.name, tuple(button.button)))
