"""Shared offline harnesses; callers initialize server state before importing."""

from tests.support.history_september_runtime_fixes import Frames
from tasks.dungeon.prepare import CombatPrepare


class CountPrepare(Frames, CombatPrepare):
    def __init__(self, frames):
        super().__init__(frames)
        self.actions = []
        self.device.multi_click = self.multi_click

    def _ocr_lang(self):
        return "cn"

    def _handle_dungeon_additional(self):
        return self.frame.get("popup", False)

    def appear(self, button):
        return self.frame.get("buttons", True)

    def multi_click(self, button, n, interval):
        self.actions.append((self.index, button.name, n))
