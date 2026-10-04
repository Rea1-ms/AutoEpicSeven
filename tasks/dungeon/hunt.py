"""Language-independent hunt targets and card selection checks."""

from module.base.button import ButtonWrapper
from module.base.utils import color_similar, get_color
from tasks.dungeon.assets.assets_dungeon_configs_combat_hunt_boss import (
    AZIMANAK,
    BANSHEE,
    CAIDES,
    GOLEM,
    HUNT_CARD_SEARCH,
    HUNT_CARD_SELECTED,
    OGRE,
    WYVERN,
)


HUNT_BOSSES = {
    "Wyvern": WYVERN,
    "Golem": GOLEM,
    "Banshee": BANSHEE,
    "Azimanak": AZIMANAK,
    "Caides": CAIDES,
    "Ogre": OGRE,
}
HUNT_BOSS_ELEMENTS = {
    "Wyvern": "Fire",
    "Golem": "Nature",
    "Banshee": "Water",
    "Azimanak": "Dark",
    "Caides": "Light",
    "Ogre": "Dark",
}


class HuntNavigateMixin:
    def _hunt_element(self) -> str:
        return HUNT_BOSS_ELEMENTS[self._hunt_boss()]

    def _hunt_boss(self) -> str:
        return getattr(self.config, "Combat_HuntBoss", "Wyvern")

    def _hunt_boss_button(self) -> ButtonWrapper:
        button = HUNT_BOSSES[self._hunt_boss()]
        button.load_search(HUNT_CARD_SEARCH.area)
        return button

    def _is_selected_hunt_boss(self, button: ButtonWrapper) -> bool:
        if not self.match_template_luma(
            button, similarity=self.COMBAT_CHECK_SIMILARITY
        ):
            return False

        # A shared dark icon can identify both Azimanak and Ogre. The portrait
        # must match this fresh frame before using its offset. Sample the
        # selection border on this exact card, never another visible card or
        # an offset retained from a previous successful match.
        x_offset, y_offset = button.button_offset
        area = (
            HUNT_CARD_SELECTED.area[0] + x_offset,
            button.area[1] + y_offset,
            HUNT_CARD_SELECTED.area[2] + x_offset,
            button.area[3] + y_offset,
        )
        # Portrait searches stop before the border. Validate the border using
        # its own narrow search strip, and anchor its vertical span to the
        # matched portrait rather than an independently editable click box.
        left, top, right, bottom = HUNT_CARD_SELECTED.search
        if not (left <= area[0] < area[2] <= right and top <= area[1] < area[3] <= bottom):
            return False
        return color_similar(
            get_color(self.device.image, area),
            HUNT_CARD_SELECTED.color,
            threshold=self.COMBAT_STATE_COLOR_THRESHOLD,
        )

    def _scroll_hunt_boss_list(self) -> None:
        bosses = list(HUNT_BOSSES)
        target = bosses.index(self._hunt_boss())
        visible = []
        for index, button in enumerate(HUNT_BOSSES.values()):
            button.load_search(HUNT_CARD_SEARCH.area)
            if self.match_template_luma(button, similarity=self.COMBAT_CHECK_SIMILARITY):
                visible.append(index)
        scroll_up = target < min(visible) if visible else target < len(bosses) // 2
        start_y, end_y = self.COMBAT_SCROLL_START_Y, self.COMBAT_SCROLL_END_Y
        if scroll_up:
            start_y, end_y = end_y, start_y
        self.device.swipe(
            (self.COMBAT_SCROLL_X, start_y),
            (self.COMBAT_SCROLL_X, end_y),
            duration=(0.2, 0.3),
        )
