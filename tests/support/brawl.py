"""Brawl fixtures use the shared recorder and never construct a device."""

from tests.support.offline import ROOT, fixture_image
from tests.support.offline import record_action
from types import SimpleNamespace


MANIFEST = ROOT / "tests/fixtures/brawl/manifest.json"


def frame(name):
    return fixture_image(MANIFEST, "brawl-" + name)


class BrawlReplay:
    """Advance only on screenshots and preserve production action intervals."""

    def __init__(self, task_type, frames, clock, seconds=0.5):
        self.frames = [frame(item) if isinstance(item, str) else item for item in frames]
        self.index = 0
        self.image = self.frames[0]
        self.clock = clock
        self.seconds = seconds
        self.actions = []
        self.task = object.__new__(task_type)
        self.task.config = SimpleNamespace()
        self.task.device = self
        self.task.interval_timer = {}

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError("Replay exhausted before confirmed completion or bounded exit")
        self.clock.advance(self.seconds)
        self.image = self.frames[self.index]

    def click(self, button):
        action = (self.index, str(button), tuple(button.button))
        self.actions.append(action)
        record_action(action)
