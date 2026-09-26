"""Shared fixtures for exploration tests; importing this module never connects a device."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
from PIL import Image

from module.config import server

# Assets bind to the active server during import. Keep this before task imports.
server.set_lang("global_cn")

from tasks.dimensional_exploration.dimensional_exploration import DimensionalExploration  # noqa: E402
from tasks.dimensional_exploration.vision import ExplorationVision  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "dimensional_exploration"
MANIFEST = FIXTURES / "manifest.json"
_active_test = None
_diagnostics = {}


def set_active_test(test_id):
    global _active_test
    _active_test = test_id
    _diagnostics[test_id] = {"frames_read": [], "actions": []}


def finish_test(test_id):
    global _active_test
    _active_test = None
    return _diagnostics.pop(test_id, {"frames_read": [], "actions": []})


def record_action(action):
    if _active_test is not None:
        _diagnostics[_active_test]["actions"].append(str(action))
    return action


class ClickLog(list):
    def append(self, action):
        super().append(record_action(action))


def load_manifest():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("fixtures"), dict):
        raise ValueError("Unsupported exploration fixture manifest")
    return data["fixtures"]


def frame(suffix):
    manifest = load_manifest()
    if suffix not in manifest:
        raise ValueError(f"Unregistered exploration screenshot: {suffix}")
    if _active_test is not None:
        _diagnostics[_active_test]["frames_read"].append(suffix)
    path = ROOT / manifest[suffix]["path"]
    with Image.open(path) as source:
        if source.size != (1280, 720):
            raise ValueError(f"Unexpected exploration screenshot size: {suffix}")
        return np.array(source.convert("RGB"))


def artifact_path(filename=""):
    base = Path(os.environ.get("AES_TEST_ARTIFACT_DIR", ROOT / "screenshots" / "offline_test_results"))
    path = base / uuid4().hex
    return path / filename if filename else path


class FakeDevice:
    """Only methods used by the offline task tests are provided."""

    def __init__(self, image):
        self.image = image
        self.clicks = ClickLog()
        self.click_record_clear = Mock()
        self.swipe = Mock()

    def stuck_record_add(self, *args):
        pass

    def stuck_record_clear(self):
        pass

    def save_screenshot(self, **kwargs):
        pass


class ReplayDevice(FakeDevice):
    """Explicit frames and action expectations prevent a click from advancing by itself."""

    def __init__(self, suffixes, expected_clicks=()):
        images = [frame(suffix) for suffix in suffixes]
        if not images:
            raise ValueError("Replay needs at least one frame")
        super().__init__(images[0])
        self.images = images
        self.index = 0
        self.expected_clicks = list(expected_clicks)

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.images):
            raise AssertionError("Replay exhausted before the state loop exited")
        self.image = self.images[self.index]

    def click(self, button):
        actual = str(button)
        if len(self.clicks) >= len(self.expected_clicks):
            raise AssertionError(f"Unexpected replay click: {actual}")
        expected = self.expected_clicks[len(self.clicks)]
        if actual != expected:
            raise AssertionError(f"Expected {expected!r}, clicked {actual!r}")
        self.clicks.append(actual)

    def assert_actions_complete(self):
        if self.clicks != self.expected_clicks:
            raise AssertionError(f"Replay actions differ: {self.clicks!r} != {self.expected_clicks!r}")


def make_task(suffix):
    device = FakeDevice(frame(suffix))
    values = {"DimensionalExploration_" + key: "起源拉斯 > 罪戾的安洁莉卡 > 智武"
              for key in DimensionalExploration.CLASS_OPTIONS.values()}
    task = DimensionalExploration(SimpleNamespace(**values), device)
    clicks = ClickLog()
    task.click_action = lambda button: clicks.append(str(button)) or True
    return task, ExplorationVision(device.image), clicks


def task_for(suffix=None):
    task, _, clicks = make_task(suffix or "20260922-081352-852")
    task.action_ready = lambda: True
    return task, ExplorationVision(task.device.image), clicks
