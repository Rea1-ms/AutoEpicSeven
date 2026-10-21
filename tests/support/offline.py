"""Business-independent clocks, action diagnostics, and registered screenshots."""

import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
_active_test = None
_diagnostics = {}


@contextmanager
def retained_directory(label):
    """Keep test-only output under the ignored tree; never delete user files."""
    path = ROOT / 'screenshots' / 'offline_test_results' / f'{label}-{uuid4().hex}'
    path.mkdir(parents=True)
    yield str(path)


class OfflineAssertions:
    """Assertions for non-test replay helpers extracted from historical classes."""

    @staticmethod
    def assertEqual(actual, expected):
        assert actual == expected, f'{actual!r} != {expected!r}'


def set_active_test(test_id):
    global _active_test
    _active_test = test_id
    _diagnostics[test_id] = {'frames_read': [], 'actions': []}


def finish_test(test_id):
    global _active_test
    _active_test = None
    return _diagnostics.pop(test_id, {'frames_read': [], 'actions': []})


def record_action(action):
    if _active_test is not None:
        _diagnostics[_active_test]['actions'].append(str(action))
    return action


def record_frame(fixture_id):
    if _active_test is not None:
        _diagnostics[_active_test]['frames_read'].append(fixture_id)


class ControlledClock:
    """Advance the production Timer clock deterministically, without sleeping."""

    def __init__(self, start=100.0):
        self.now = start

    def __enter__(self):
        self.patch = patch('module.base.timer.time', side_effect=lambda: self.now)
        self.patch.start()
        return self

    def __exit__(self, *args):
        self.patch.stop()

    def advance(self, seconds):
        self.now += seconds


def fixture_image(manifest_path, fixture_id):
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('fixtures'), dict):
        raise ValueError('Unsupported fixture manifest')
    if fixture_id not in data['fixtures']:
        raise ValueError(f'Unregistered screenshot: {fixture_id}')
    relative = Path(data['fixtures'][fixture_id]['path'])
    path = ROOT / relative
    if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(
        manifest_path.parent.resolve()
    ):
        raise ValueError(f'Invalid fixture path: {fixture_id}')
    with Image.open(path) as source:
        if source.size != (1280, 720):
            raise ValueError(f'Unexpected screenshot size: {fixture_id}')
        image = np.array(source.convert('RGB'))
    record_frame(fixture_id)
    return image
