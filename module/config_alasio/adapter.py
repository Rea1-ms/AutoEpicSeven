"""
SQLite persistence adapter for AzurLaneConfig.

Replaces ConfigUpdater (JSON file I/O) and ConfigWatcher (file mtime)
with Alasio's SQLite-backed config system.

Read path:  SQLite rows + msgspec model defaults -> old-format nested dict
Write path: modified paths -> ConfigSetEvent -> Mod.config_batch_set()
"""
import json
import os
from datetime import datetime, timezone

from module.base.decorator import cached_property
from module.config.deep import deep_get, deep_set
from module.config.utils import DEFAULT_TIME
from module.logger import logger

# Old Alas.Emulator fields that belong to new Alas.Game group
_GAME_FIELDS = frozenset({'PackageName', 'GameLanguage', 'GameClient'})
# Old groups under Alas task that map to Device task in new system
_DEVICE_GROUPS = frozenset({'Emulator', 'EmulatorInfo', 'Error', 'Optimization'})
# Framework-only tasks excluded from old self.data
_FRAMEWORK_TASKS = frozenset({'Device', 'RestartDevice', 'RestartGame', '_global_bind'})
# Groups whose fields are JSON-encoded strings in the new model but dicts in old code
_STORED_DICT_FIELDS = {
    'Dashboard': frozenset({
        'Gold', 'Skystone', 'Stamina', 'EquipmentInventory',
        'DailyActivity', 'ArenaRank', 'ArenaFlag',
        'ConquestPoint', 'ShadowCommission', 'TeamBattle',
    }),
    'CombatRuntime': frozenset({'Session'}),
}
# Per-group field renames, old name -> new name (applied after group remap on write)
# Old device code reads/writes EmulatorInfo_name / EmulatorInfo_path (lowercase),
# the new framework model spells them Name / Path
_FIELD_RENAME = {
    'EmulatorInfo': {'name': 'Name', 'path': 'Path'},
}
# new name -> old name, for the read direction
_FIELD_RENAME_READ = {
    group: {new: old for old, new in ren.items()}
    for group, ren in _FIELD_RENAME.items()
}
_VALUE_RENAME = {
    ('Device', 'Optimization', 'WhenTaskQueueEmpty'): {
        'close_game': 'stop_game',
        'close_emulator': 'stop_device',
    },
}
_VALUE_RENAME_READ = {
    key: {new: old for old, new in renames.items()}
    for key, renames in _VALUE_RENAME.items()
}
# Legacy values with no equivalent in Alasio. These are deliberately ignored,
# rather than being mistaken for an accidental schema mismatch.
_IGNORED_LEGACY_FIELDS = frozenset({
    ('Alas', 'Error', 'Restart'),
})


class ConfigAdapterError(RuntimeError):
    """Raised when legacy config cannot be represented or persisted safely."""


def _to_naive_local(dt):
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _to_utc_aware(dt):
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    return dt.astimezone().astimezone(timezone.utc)


def _ensure_env():
    from alasio.ext import env
    if not env.PROJECT_ROOT:
        env.set_project_root(env.ALASIO_ROOT)


class AesSqliteAdapter:
    """Drop-in replacement for ConfigUpdater + ConfigWatcher."""

    config_name = 'alas'
    start_mtime = DEFAULT_TIME
    redirection = []

    @cached_property
    def _mod(self):
        _ensure_env()
        from alasio.config.entry.mod import Mod
        from module.config_alasio.const import entry
        return Mod(entry)

    @cached_property
    def _task_index(self):
        return self._mod.task_index_data()

    @cached_property
    def _valid_fields(self):
        out = {}
        for task_name, task_info in self._task_index.items():
            for group_name, ref in task_info.group.items():
                key = (ref.task, group_name)
                if key in out:
                    continue
                model = self._mod.get_group_model(file=ref.file, cls=ref.cls)
                if model is None:
                    raise ConfigAdapterError(
                        f'Cannot load config model {ref.file}.{ref.cls} '
                        f'for {task_name}.{group_name}'
                    )
                out[key] = frozenset(model.__struct_fields__)
        return out

    @cached_property
    def args(self):
        return {name: {} for name in self._task_index if name not in _FRAMEWORK_TASKS}

    # --- Read ---

    def read_file(self, config_name, is_template=False):
        _ensure_env()
        from alasio.config.table.config import AlasioConfigTable
        from msgspec import NODEFAULT
        from msgspec.structs import asdict
        from msgspecerror import load_msgpack_with_default

        table = AlasioConfigTable(config_name)
        dict_row = {}
        for row in table.select():
            dict_row[(row.task, row.group)] = row.value

        def read_group(task_name, group_name, ref):
            model = self._mod.get_group_model(file=ref.file, cls=ref.cls)
            if model is None:
                raise ConfigAdapterError(
                    f'Cannot load config model {ref.file}.{ref.cls} '
                    f'for {task_name}.{group_name}'
                )

            key = (ref.task, group_name)
            raw = dict_row.get(key, NODEFAULT)
            if raw is NODEFAULT:
                try:
                    data = model()
                except Exception as e:
                    raise ConfigAdapterError(
                        f'Cannot construct defaults for {task_name}.{group_name}: {e}'
                    ) from e
            else:
                data, errors = load_msgpack_with_default(raw, model)
                for error in errors:
                    logger.warning(
                        f'Config data inconsistent at {ref.task}.{group_name}: {error}'
                    )
                if data is NODEFAULT:
                    raise ConfigAdapterError(
                        f'Cannot decode config data for {ref.task}.{group_name}'
                    )

            group_data = asdict(data)
            renames = _FIELD_RENAME_READ.get(group_name)
            if renames:
                for new_name, old_name in renames.items():
                    if new_name in group_data:
                        group_data[old_name] = group_data.pop(new_name)

            for key, value in group_data.items():
                if isinstance(value, datetime):
                    group_data[key] = _to_naive_local(value)
                value_renames = _VALUE_RENAME_READ.get((ref.task, group_name, key))
                if value_renames:
                    group_data[key] = value_renames.get(group_data[key], group_data[key])

            stored_fields = _STORED_DICT_FIELDS.get(group_name)
            if stored_fields:
                for field in stored_fields:
                    value = group_data.get(field)
                    if isinstance(value, str):
                        try:
                            group_data[field] = json.loads(value) if value else {}
                        except json.JSONDecodeError as e:
                            logger.warning(
                                f'Invalid JSON at {ref.task}.{group_name}.{field}: {e}'
                            )
                            group_data[field] = {}
            return group_data

        out = {}
        for task_name, task_info in self._task_index.items():
            if task_name in _FRAMEWORK_TASKS:
                continue

            task_dict = {}
            for group_name, ref in task_info.group.items():
                gd = read_group(task_name, group_name, ref)

                if group_name == 'Scheduler':
                    gd['Command'] = task_name

                task_dict[group_name] = gd
            out[task_name] = task_dict

        device_info = self._task_index.get('Device')
        device_groups = {}
        if device_info:
            for gn, ref in device_info.group.items():
                device_groups[gn] = read_group('Device', gn, ref)

        alas = out.setdefault('Alas', {})

        emu = dict(device_groups.get('Emulator', {}))
        emu.update(alas.pop('Game', {}))
        alas['Emulator'] = emu

        for gn in ('EmulatorInfo', 'Error', 'Optimization'):
            if gn in device_groups:
                alas[gn] = device_groups[gn]

        return out

    # --- Write ---

    @staticmethod
    def _remap_path(task, group, arg):
        """Old 'task.group.arg' namespace -> new (task, group, arg) namespace."""
        if task == 'Alas' and group == 'Emulator':
            if arg in _GAME_FIELDS:
                group = 'Game'
            else:
                task = 'Device'
        elif task == 'Alas' and group in _DEVICE_GROUPS:
            task = 'Device'
        ren = _FIELD_RENAME.get(group)
        if ren:
            arg = ren.get(arg, arg)
        return task, group, arg

    @staticmethod
    def _is_ignored_legacy_field(task, group, arg):
        return (
            (group == 'Scheduler' and arg == 'Command')
            or (task, group, arg) in _IGNORED_LEGACY_FIELDS
        )

    def _build_events(self, modified):
        """
        Translate modified paths into ConfigSetEvents.

        Args:
            modified: dict of 'task.group.arg' -> value

        Returns:
            list[ConfigSetEvent]:
        """
        from alasio.config.entry.model import ConfigSetEvent

        events = []
        for path, value in modified.items():
            parts = path.split('.') if isinstance(path, str) else list(path)
            if len(parts) != 3:
                raise ConfigAdapterError(f'Invalid config path: {path!r}')
            task, group, arg = parts

            if self._is_ignored_legacy_field(task, group, arg):
                continue

            task, group, arg = self._remap_path(task, group, arg)

            valid = self._valid_fields.get((task, group))
            if valid is None or arg not in valid:
                raise ConfigAdapterError(
                    f'Config field has no Alasio mapping: {task}.{group}.{arg}'
                )

            value_renames = _VALUE_RENAME.get((task, group, arg))
            if value_renames:
                value = value_renames.get(value, value)

            if isinstance(value, datetime):
                value = _to_utc_aware(value)

            stored = _STORED_DICT_FIELDS.get(group)
            if stored and arg in stored and isinstance(value, dict):
                value = json.dumps(value, default=str)

            events.append(ConfigSetEvent(task=task, group=group, arg=arg, value=value))
        return events

    def _persist(self, config_name, modified):
        events = self._build_events(modified)
        if not events:
            return

        ok, result = self._mod.config_batch_set(config_name, events)
        if not ok:
            # rollback list also contains valid events dragged down by the batch, they have no error
            errors = []
            for e in result:
                if e.error is not None:
                    message = getattr(e.error, 'msg', str(e.error))
                    errors.append(f'{e.task}.{e.group}.{e.arg}: {message}')
            detail = '; '.join(errors) or 'unknown validation error'
            raise ConfigAdapterError(f'Config write rejected: {detail}')

    @staticmethod
    def write_file(config_name, data, mod_name='alas'):
        modified = {}
        for task, groups in data.items():
            for group, fields in groups.items():
                for arg, value in fields.items():
                    modified[f'{task}.{group}.{arg}'] = value
        adapter = AesSqliteAdapter()
        adapter.config_name = config_name
        adapter._persist(config_name, modified)

    # --- Watcher ---

    @cached_property
    def _watcher(self):
        _ensure_env()
        from alasio.base.scheduler.configwatcher import ConfigWatcher
        return ConfigWatcher(self.config_name)

    def start_watching(self):
        self._watcher.init()
        self.start_mtime = self._watcher.mtime or DEFAULT_TIME

    def get_mtime(self):
        _ensure_env()
        from alasio.config.table.base import AlasioConfigDB
        path = str(AlasioConfigDB.config_file(self.config_name))
        try:
            ts = os.stat(path).st_mtime
            return datetime.fromtimestamp(ts).replace(microsecond=0)
        except (FileNotFoundError, OSError):
            return DEFAULT_TIME

    def should_reload(self):
        return self._watcher.is_modified()

    # --- ConfigUpdater stubs ---

    def config_update(self, old, is_template=False):
        return old

    def config_redirect(self, old, new):
        return new

    @staticmethod
    def update_state(data):
        if deep_get(data, keys='Alas.Emulator.GameClient') == 'cloud_android':
            deep_set(data, keys='Alas.Emulator.PackageName', value='CN-Official')
        return data

    def save_callback(self, key, value):
        if key == 'Alas.Emulator.GameClient' and value == 'cloud_android':
            yield 'Alas.Emulator.PackageName', 'CN-Official'

    def iter_hidden_args(self, data):
        if deep_get(data, 'Knights.KnightsTeamBattle.Reminder', default=False) is False:
            yield 'Knights.KnightsTeamBattle.ReminderLeadMinutes'
        if deep_get(data, 'Knights.Knights.Support', default=True) is False:
            yield 'Knights.Knights.RequestItem'
        if deep_get(data, 'Arena.Arena.NPCCombat', default=False) is False:
            yield 'Arena.Arena.NPCCombatFastBattle'
            yield 'Arena.Arena.NPCCombatCount'
        if deep_get(data, 'SecretShop.SecretShop.OnlyFree', default=True) is True:
            yield 'SecretShop.SecretShop.MaxRefresh'
        for task in ('Combat', 'CombatFarm'):
            p = f'{task}.Combat'
            is_farm = task == 'CombatFarm'
            domain = deep_get(data, f'{p}.Domain', default='Hunt')
            hg = deep_get(data, f'{p}.HuntGrade', default='Hell')
            if domain != 'Episode4':
                yield f'{p}.Episode4Material'
            if is_farm:
                yield f'{p}.FastCombatCount'
                yield f'{p}.RepeatCombatCount'
            elif domain == 'Saint37':
                yield f'{p}.FastCombat'
                yield f'{p}.FastCombatCount'
            elif domain == 'Hunt' and hg == 'Dimensional':
                yield f'{p}.FastCombat'
                yield f'{p}.FastCombatCount'
            elif deep_get(data, f'{p}.FastCombat', default=True) is False:
                yield f'{p}.FastCombatCount'
            if is_farm and (domain == 'Saint37' or (domain == 'Hunt' and hg == 'Dimensional')):
                yield f'{p}.FastCombat'
            if domain != 'Saint37':
                yield f'{p}.Saint37AutoRecycle'
            if domain in ('Saint37', 'Episode4'):
                yield f'{p}.Element'
                yield f'{p}.AltarGrade'
                yield f'{p}.HuntGrade'
            else:
                if domain != 'SpiritAltar':
                    yield f'{p}.AltarGrade'
                if domain != 'Hunt':
                    yield f'{p}.HuntGrade'

    def get_hidden_args(self, data):
        return set(self.iter_hidden_args(data))
