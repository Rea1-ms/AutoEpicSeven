# Commit Modules

## 1. CombatFarm backend split

Purpose: add an independent repeat-farming task without changing the daily `Combat` task's daily-reset behavior.

Files:

- `aes.py`
- `module/alas.py`
- `tasks/combat/combat.py`
- `tasks/combat/runtime.py`

Suggested commit:

```powershell
git add aes.py module/alas.py tasks/combat/combat.py tasks/combat/runtime.py
git commit -m "Add independent repeat combat farm task"
```

## 2. Config and UI task registration

Purpose: register `CombatFarm` as a separate menu/task, update scheduler priority, and hide daily-only options in the farm UI.

Files:

- `module/config/argument/task.yaml`
- `module/config/config_manual.py`
- `module/config/config_updater.py`

Suggested commit:

```powershell
git add module/config/argument/task.yaml module/config/config_manual.py module/config/config_updater.py
git commit -m "Register repeat farming config task"
```

## 3. Generated config artifacts and translations

Purpose: commit generated menu/argument/template files plus translated labels for the new menu and task.

Files:

- `module/config/argument/args.json`
- `module/config/argument/menu.json`
- `config/template.json`
- `module/config/i18n/en-US.json`
- `module/config/i18n/es-ES.json`
- `module/config/i18n/ja-JP.json`
- `module/config/i18n/zh-CN.json`
- `module/config/i18n/zh-TW.json`

Suggested commit:

```powershell
git add module/config/argument/args.json module/config/argument/menu.json config/template.json module/config/i18n/en-US.json module/config/i18n/es-ES.json module/config/i18n/ja-JP.json module/config/i18n/zh-CN.json module/config/i18n/zh-TW.json
git commit -m "Update generated config for repeat farming"
```

## 4. Documentation

Purpose: document the split between daily combat and repeat farming.

Files:

- `SAINT37_TASK_SUMMARY.md`
- `COMMIT_MODULES.md`

Suggested commit:

```powershell
git add SAINT37_TASK_SUMMARY.md COMMIT_MODULES.md
git commit -m "Document repeat farming task split"
```

## Local user config

`config/cn.json` was updated locally so the current machine uses `CombatFarm` for Saint 3-7 and disables the old daily `Combat`. It is normally ignored/local runtime config, so do not commit it unless you intentionally want to share this exact personal config.
