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


def main():
    args = parse_args()

    from alasio.ext import env
    if args.project_root:
        env.set_project_root(args.project_root)
        os.makedirs(os.path.join(args.project_root, 'config'), exist_ok=True)
    elif not env.PROJECT_ROOT:
        env.set_project_root(env.ALASIO_ROOT)

    from module.config_alasio.adapter import AesSqliteAdapter, ConfigAdapterError

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
                new_task, new_group, new_arg = adapter._remap_path(task, group, arg)
                valid = adapter._valid_fields.get((new_task, new_group))
                if valid is None or new_arg not in valid:
                    if adapter._is_ignored_legacy_field(task, group, arg):
                        skipped_paths.append(path)
                        continue
                    raise ConfigAdapterError(f'Config field has no Alasio mapping: {path}')
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
    adapter._persist(name, modified)
    print(f'Batch write OK, {len(events)} fields')

    # Bind the config to the aes mod, so the Alasio GUI scan picks it up
    from alasio.config.table.key import AlasioKeyTable
    from module.config_alasio.const import entry
    AlasioKeyTable(name).mod_set(entry.name)
    print(f'Mod binding written: {name} -> {entry.name}')

    if args.verify:
        print('--- verify: read back through adapter ---')
        data = adapter.read_file(name)
        for task, task_data in data.items():
            sched = task_data.get('Scheduler')
            if sched:
                print(f'  {task}.Scheduler: Enable={sched.get("Enable")} NextRun={sched.get("NextRun")}')
        emu = data.get('Alas', {}).get('Emulator', {})
        print(f'  Alas.Emulator: {emu}')
        info = data.get('Alas', {}).get('EmulatorInfo', {})
        print(f'  Alas.EmulatorInfo: {info}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
