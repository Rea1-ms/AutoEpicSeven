"""Tower-local modal handling during both navigation and floor entry."""

from module.config import server
from module.logger import logger
from tasks.automaton_tower.assets.assets_automaton_tower_entry import (
    TOWER_SEASON_INFO,
    TOWER_STAGE_CONFIRM,
)
from tasks.base.ui import UI


class AutomatonTowerEntry(UI):
    """Keep tower overlays out of the ordinary page scan."""

    def _is_entry_overlay(self) -> bool:
        if server.lang not in ("cn", "global_cn"):
            return False
        return self.appear(TOWER_SEASON_INFO, interval=0) or (
            server.lang == "cn" and self.appear(TOWER_STAGE_CONFIRM, interval=0)
        )

    def _handle_tower_entry(self) -> bool:
        if server.lang not in ("cn", "global_cn"):
            return False
        if self.appear_then_click(TOWER_SEASON_INFO, interval=2):
            logger.info("Automaton Tower: close season information")
            return True
        # Only the supplied CN dialog proves this confirmation's identity.
        # Its body explicitly starts the currently selected stage. Never use
        # a generic confirmation or click a stage/reset control behind it.
        if server.lang == "cn" and self.appear_then_click(TOWER_STAGE_CONFIRM, interval=2):
            logger.info("Automaton Tower: confirm currently selected stage")
            return True
        return False

    def ui_page_appear(self, page, interval=0):
        # ui_goto checks its destination BEFORE calling ui_additional. A modal
        # can leave page titles visible behind it, including during a dropped
        # close click. Block that scan on every frame, independently of the
        # close action's retry interval; only a fresh usable page ends entry.
        if self._is_entry_overlay():
            return False
        return super().ui_page_appear(page, interval=interval)

    def ui_additional(self) -> bool:
        if self._is_entry_overlay():
            return self._handle_tower_entry()
        return super().ui_additional()

    def handle_popup_confirm(self, interval=2) -> bool:
        if self._is_entry_overlay():
            return False
        return super().handle_popup_confirm(interval=interval)

    def handle_popup_cancel(self, interval=2) -> bool:
        if self._is_entry_overlay():
            return False
        return super().handle_popup_cancel(interval=interval)
