"""Select an unlocked starting strategy and confirm its own golden border."""

from module.base.utils import area_offset, color_similar, get_color
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    SUPPLY_BAGGAGE, SUPPLY_CONTRACT, SUPPLY_MACHINE, SUPPLY_HAMMER,
    SUPPLY_CONFIRM, SUPPLY_SELECTED,
)


STRATEGIES = {
    "雇佣契约": (SUPPLY_CONTRACT, -560),
    "痛苦机械装置": (SUPPLY_MACHINE, -280),
    "沉重包袱": (SUPPLY_BAGGAGE, 0),
    "基础锻炼铁锤": (SUPPLY_HAMMER, 280),
}


class StartingStrategyMixin:
    def handle_start_supply(self, vision):
        strategy = getattr(self.config, "DimensionalExploration_StartStrategy", "沉重包袱")
        if strategy not in STRATEGIES:
            self.require_human(f"未支持的开局策略：{strategy}。请重新选择开局策略。")
        button, offset = STRATEGIES[strategy]
        if not self.appear(button):
            return False
        # The confirm button is shared by all cards. A golden border on a
        # different card cannot authorize confirming the configured strategy;
        # failed selection clicks must retry that card on the next screenshot.
        border = area_offset(SUPPLY_SELECTED.area, (offset, 0))
        selected = color_similar(get_color(self.device.image, border), SUPPLY_SELECTED.color, threshold=25)
        return self.click_action(SUPPLY_CONFIRM if selected else button)
