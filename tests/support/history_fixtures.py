"""Read reviewed historical inputs by registered aliases, without source folders."""

import json
from pathlib import Path

from module.base.utils import load_image as asset_image
from tests.support.offline import ROOT, fixture_image


MANIFEST = ROOT / 'tests/fixtures/historical/manifest.json'
INPUT_ROOT = ROOT / 'tests/fixtures/historical/input'


def input_root(group=''):
    return INPUT_ROOT / group


def fixture_path(alias):
    """Resolve one declared alias for APIs that require a real input filename."""
    from tests.support.offline import record_frame

    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    fixture_id = data['aliases'][alias]
    item = data['fixtures'][fixture_id]
    relative = Path(item['path'])
    path = (ROOT / relative).resolve()
    if (relative.is_absolute() or '..' in relative.parts
            or not path.is_relative_to(MANIFEST.parent.resolve())
            or item['server'] != 'global_cn'
            or item['capture_type'] != 'emulator_screenshot'):
        raise ValueError(f'Invalid historical capture: {alias}')
    record_frame(fixture_id)
    return path


def read_input(path):
    candidate = Path(path).resolve()
    if candidate.is_relative_to(INPUT_ROOT.resolve()):
        alias = candidate.relative_to(INPUT_ROOT.resolve()).as_posix()
        data = json.loads(MANIFEST.read_text(encoding='utf-8'))
        if alias not in data['aliases']:
            raise ValueError(f'Unregistered historical capture: {alias}')
        fixture_id = data['aliases'][alias]
        item = data['fixtures'][fixture_id]
        if item['server'] != 'global_cn' or item['capture_type'] != 'emulator_screenshot':
            raise ValueError(f'Unexpected capture origin: {fixture_id}')
        return fixture_image(MANIFEST, fixture_id)
    if candidate.is_relative_to((ROOT / 'assets').resolve()):
        return asset_image(candidate)
    raise ValueError(f'Offline input outside registered captures and assets: {candidate.name}')


def build_combat(image_name, difficulty):
    """Build the historical combat probe with a registered, prepared first frame."""
    from types import SimpleNamespace

    from tasks.dungeon.dungeon import Combat

    class Device:
        def __init__(self):
            self.image = read_input(input_root('urgent_tasks') / image_name)

        @staticmethod
        def stuck_record_add(*args, **kwargs):
            pass

    class Config(SimpleNamespace):
        def __init__(self):
            super().__init__(Combat_UrgentTasksDifficulty=difficulty, Emulator_GameLanguage='cn',
                             Scheduler_ServerUpdate='02:00', task=SimpleNamespace(command='Combat'))
            self.cross_values = {}

        def cross_get(self, path, default=None):
            return self.cross_values.get(path, default)

        def cross_set(self, path, value):
            self.cross_values[path] = value

    combat = Combat.__new__(Combat)
    combat.config = Config()
    combat.device = Device()
    combat._dungeon_domain = lambda: 'UrgentTasks'
    return combat
