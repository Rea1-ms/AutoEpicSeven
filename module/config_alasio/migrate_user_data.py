"""
One-time migration: old JSON user config -> Alasio SQLite config DB.

Reads an old-generation ./config/{name}.json, cleans values against the new
msgspec models and writes them into {PROJECT_ROOT}/config/{name}.db, so
scheduler states (NextRun etc.) survive the framework switch and daily
freebies are not claimed twice.

Rehearse into a scratch dir first:
    python module/config_alasio/migrate_user_data.py \
        --json ../AutoEpicSeven/config/cn.json \
        --project-root C:/some/scratch/dir --verify

Real run (DB lands in the Alasio project root, default PROJECT_ROOT):
    python module/config_alasio/migrate_user_data.py \
        --json ../AutoEpicSeven/config/cn.json

Needs alasio importable: run with the Alasio venv python, or any python
while the Alasio repo is on PYTHONPATH.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

# Bootstrap: make the AES repo root importable when run as a script
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

_DATETIME_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', required=True, help='Path to old config JSON, e.g. ../AutoEpicSeven/config/cn.json')
    parser.add_argument('--name', default='', help='New DB config name, default to the JSON file stem')
    parser.add_argument('--project-root', default='', help='Override Alasio PROJECT_ROOT (for rehearsal runs)')
    parser.add_argument('--dry-run', action='store_true', help='Print events without writing')
    parser.add_argument('--verify', action='store_true', help='Read back through the adapter after writing')
    return parser.parse_args()


def verify_native_fields(adapter, config_name, events):
    """Verify every migrated value directly against its native SQLite row."""
    from alasio.config.table.config import AlasioConfigTable
    from msgspecerror import load_msgpack_with_default
    from module.config_alasio.adapter import ConfigAdapterError

    refs = {}
    for task_info in adapter._task_index.values():
        for group_name, ref in task_info.group.items():
            refs.setdefault((ref.task, group_name), ref)

    rows = {
        (row.task, row.group): row.value
        for row in AlasioConfigTable(config_name).select()
    }
    decoded = {}
    failures = []
    for event in events:
        key = (event.task, event.group)
        ref = refs.get(key)
        raw = rows.get(key)
        if ref is None:
            failures.append(f'{event.task}.{event.group}: model reference is missing')
            continue
        if raw is None:
            failures.append(f'{event.task}.{event.group}: database row is missing')
            continue

        if key not in decoded:
            model = adapter._mod.get_group_model(file=ref.file, cls=ref.cls)
            value, errors = load_msgpack_with_default(raw, model)
            if errors:
                failures.extend(
                    f'{event.task}.{event.group}: {error}' for error in errors
                )
                continue
            decoded[key] = value

        actual = getattr(decoded[key], event.arg)
        if actual != event.value:
            failures.append(
                f'{event.task}.{event.group}.{event.arg}: '
                f'expected {event.value!r}, got {actual!r}'
            )

    if failures:
        details = '\n'.join(f'  {failure}' for failure in failures)
        raise ConfigAdapterError(
            f'{len(failures)} fields failed strict readback:\n{details}'
        )


def main():
    args = parse_args()

    from alasio.ext import env
    if args.project_root:
        env.set_project_root(args.project_root)
        os.makedirs(os.path.join(args.project_root, 'config'), exist_ok=True)
    elif not env.PROJECT_ROOT:
        env.set_project_root(env.ALASIO_ROOT)

    from module.config_alasio.adapter import (
        AesSqliteAdapter,
        ConfigAdapterError,
        _DASHBOARD_ITEMS,
    )

    name = args.name or os.path.splitext(os.path.basename(args.json))[0]
    with open(args.json, encoding='utf-8') as f:
        old = json.load(f)

    adapter = AesSqliteAdapter()

    # (new task, group) -> {field: default value} from the new model.
    # Defaults drive type-based cleaning: generated datetime fields default to
    # datetime(2020, 1, 1, tz=utc), str fields to '', int literals to ints.
    field_defaults = {}
    for task_info in adapter._task_index.values():
        for group_name, ref in task_info.group.items():
            key = (ref.task, group_name)
            if key in field_defaults:
                continue
            model = adapter._mod.get_group_model(file=ref.file, cls=ref.cls)
            if model is None:
                raise ConfigAdapterError(
                    f'Cannot load config model {ref.file}.{ref.cls} '
                    f'for {ref.task}.{group_name}'
                )
            try:
                default = model()
            except Exception as e:
                raise ConfigAdapterError(
                    f'Cannot construct defaults for {ref.task}.{group_name}: {e}'
                ) from e
            field_defaults[key] = {
                field: getattr(default, field) for field in model.__struct_fields__
            }

    # Flatten old JSON at depth 3 into modified-style paths
    modified = {}
    skipped_paths = []
    unmapped_paths = []
    for task, task_data in old.items():
        if not isinstance(task_data, dict):
            raise ConfigAdapterError(f'Expected object at {task}, got {type(task_data).__name__}')
        for group, group_data in task_data.items():
            if not isinstance(group_data, dict):
                raise ConfigAdapterError(
                    f'Expected object at {task}.{group}, got {type(group_data).__name__}'
                )
            for arg, value in group_data.items():
                path = f'{task}.{group}.{arg}'
                if task == 'DataUpdate' and group == 'Dashboard' and arg in _DASHBOARD_ITEMS:
                    # Dashboard fields moved from one JSON-backed legacy
                    # group to one native Alasio group per item. Keep the
                    # legacy path here; adapter._build_events expands it.
                    modified[path] = value
                    continue
                new_task, new_group, new_arg = adapter._remap_path(task, group, arg)
                valid = adapter._valid_fields.get((new_task, new_group))
                if valid is None or new_arg not in valid:
                    if adapter._is_ignored_legacy_field(task, group, arg):
                        skipped_paths.append(path)
                        continue
                    unmapped_paths.append(path)
                    continue
                # Clean old JSON values against the new model's field type,
                # judged by the default value of the target field
                default = field_defaults.get((new_task, new_group), {}).get(new_arg)
                if (isinstance(value, str) and _DATETIME_RE.match(value)
                        and isinstance(default, datetime)):
                    # naive local datetime string -> datetime object
                    value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
                elif value is None and isinstance(default, str):
                    # old JSON null on a str field
                    value = default
                elif (type(value) is bool and type(default) is int):
                    # old checkbox bool -> new int literal (True -> buy once)
                    value = int(value)
                modified[path] = value

    if unmapped_paths:
        details = '\n'.join(f'  {path}' for path in unmapped_paths)
        raise ConfigAdapterError(
            f'{len(unmapped_paths)} config fields have no Alasio mapping:\n{details}'
        )

    events = adapter._build_events(modified)
    print(f'Old JSON: {args.json}')
    print(f'Target DB: {env.PROJECT_ROOT}/config/{name}.db')
    print(f'{len(events)} fields to migrate, {len(skipped_paths)} legacy fields dropped')
    for path in skipped_paths:
        print(f'  drop: {path}')

    if args.dry_run:
        for e in events:
            print(f'  set: {e.task}.{e.group}.{e.arg} = {e.value!r}')
        return 0

    # The write is atomic: schema or value errors leave the target unchanged.
    # Alasio's normal config log includes raw values. Migration inputs can
    # contain notification credentials, so keep this one batch out of logs.
    from alasio.logger import logger as alasio_logger
    alasio_logger.mute(all=True)
    try:
        adapter._persist(name, modified)
    finally:
        alasio_logger.mute_clear()
    print(f'Batch write OK, {len(events)} fields')

    # Bind the config to the aes mod, so the Alasio GUI scan picks it up
    from alasio.config.table.key import AlasioKeyTable
    from module.config_alasio.const import entry
    AlasioKeyTable(name).mod_set(entry.name)
    print(f'Mod binding written: {name} -> {entry.name}')

    if args.verify:
        print('--- verify: strict field readback ---')
        verify_native_fields(adapter, name, events)
        print(f'Strict readback OK, {len(events)} fields matched')

    return 0


if __name__ == '__main__':
    sys.exit(main())
