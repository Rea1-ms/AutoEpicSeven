"""Coordinate observed resources with waits owned by their consumers."""

import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta

from module.config import server
from module.config.deep import deep_get
from module.logger import logger
from module.ocr.language import resolve_ocr_lang

RESOURCE_STATE_PATH = "DataUpdate.ResourceRuntime.State"
RESOURCE_RECOVERY_SECONDS = {"arena_flag": 3600, "stamina": 240}
RESOURCE_OBSERVATION_SECONDS = 300
RESOURCE_REFRESH_RETRY_MINUTES = 30


def local_now():
    """Use the scheduler's local naive timestamps with an explicit local zone."""
    return datetime.now().astimezone().replace(tzinfo=None)


def config_value(config, path, default=None):
    """Include unflushed writes inside the config's existing transaction."""
    for key, value in getattr(config, "modified", {}).items():
        if path == key:
            return value
        if path.startswith(key + "."):
            return deep_get(value, path[len(key) + 1:], default)
    reader = getattr(config, "cross_get", None)
    return reader(path, default=default) if callable(reader) else default


def _date(value):
    if isinstance(value, datetime):
        return value if value.tzinfo is None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is None else None
        except ValueError:
            pass
    return None


def _batch(config):
    method = getattr(config, "multi_set", None)
    return method() if callable(method) else nullcontext()


class ResourceCoordinator:
    def __init__(self, config):
        self.config = config
        # Recognition-only consumers may have no writable task configuration.
        # They can still render counters, but cannot create scheduling evidence.
        self.available = callable(getattr(config, "cross_set", None))
        self.server = server.normalize_server(getattr(config, "Emulator_PackageName", ""))
        self.language = resolve_ocr_lang(config)

    def _load(self):
        root = deepcopy(config_value(self.config, RESOURCE_STATE_PATH, {}))
        if not isinstance(root, dict) or root.get("version", 1) != 1:
            raise ValueError("Unknown resource runtime schema")
        root.setdefault("version", 1)
        bucket = root.setdefault("servers", {}).setdefault(self.server, {})
        if bucket.get("language") != self.language:
            # A recognition-language change invalidates observations and wait
            # signatures, never daily allowances or background-run ownership.
            bucket["language"] = self.language
            bucket["observations"] = {}
            bucket["waits"] = {}
        for name in ("observations", "pending", "waits"):
            bucket.setdefault(name, {})
        if self.available and root != config_value(self.config, RESOURCE_STATE_PATH, {}):
            self._save(root)
        return root, bucket

    def _save(self, root):
        if self.available:
            self.config.cross_set(RESOURCE_STATE_PATH, root)

    def _signature(self, task):
        groups = ("Arena",) if task == "Arena" else ("Combat", "UrgentTasks")
        return json.dumps({name: config_value(self.config, f"{task}.{name}", {})
                           for name in groups}, sort_keys=True, default=str)

    @property
    def pending(self):
        return set(self._load()[1]["pending"]) if self.available else set()

    def observation(self, key, *, now=None):
        if not self.available:
            return None
        _, bucket = self._load()
        item = bucket["observations"].get(key)
        if not item or not item.get("valid") or key in bucket["pending"]:
            return None
        at = _date(item.get("at"))
        age = ((now or local_now()) - at).total_seconds() if at else -1
        return deepcopy(item) if 0 <= age <= RESOURCE_OBSERVATION_SECONDS else None

    def invalidate(self, *keys):
        if not self.available:
            return
        root, bucket = self._load()
        for key in keys:
            if key in bucket["observations"]:
                bucket["observations"][key]["valid"] = False
        self._save(root)

    def request(self, source, keys=("arena_flag", "stamina", "skystone")):
        """Persist before a potentially consuming/claiming click, not after it."""
        if not self.available:
            return
        root, bucket = self._load()
        now = local_now().replace(microsecond=0)
        with _batch(self.config):
            for key in keys:
                bucket["pending"].setdefault(key, {"source": source, "at": now.isoformat()})
            if config_value(self.config, "DataUpdate.Scheduler.Enable", False):
                current = _date(config_value(self.config, "DataUpdate.Scheduler.NextRun"))
                # Keep an existing refresh failure cooldown. Repeated claims
                # coalesce into that request instead of moving it to now again.
                queued = _date(bucket.get("refresh_run"))
                if queued != current or queued is None:
                    bucket["full_due"] = (current or now).isoformat()
                    target = min(current or now, now)
                    bucket["refresh_run"] = target.isoformat()
                    self.config.cross_set("DataUpdate.Scheduler.NextRun", target)
            self._save(root)

    def observe(self, parsed, source):
        if not self.available or not parsed:
            return
        root, bucket = self._load()
        now = local_now().replace(microsecond=0)
        with _batch(self.config):
            for key, value in parsed.items():
                previous = bucket["observations"].get(key, {})
                changed = (previous.get("value") != value.value
                           or previous.get("total") != value.total
                           or not previous.get("valid") or key in bucket["pending"])
                bucket["observations"][key] = {
                    "value": value.value, "total": value.total, "at": now.isoformat(),
                    "source": source, "valid": True,
                    "revision": previous.get("revision", 0) + int(changed),
                }
                # Repeat menus report only the owned amount, not the natural
                # recovery cap. Keep that frame's total=0 truthful, while
                # retaining separately the last cap actually read in this
                # server/language bucket. Consumption invalidates the amount,
                # not the cap; language changes clear both in _load(). Never
                # borrow a cap from the unpartitioned dashboard on migration.
                if key in RESOURCE_RECOVERY_SECONDS:
                    bucket["observations"][key]["capacity"] = (
                        value.total or previous.get("capacity", previous.get("total", 0))
                    )
                bucket["pending"].pop(key, None)
            self._reconcile(bucket, parsed, now)
            self._restore_full_schedule(bucket)
            self._save(root)

    def _restore_full_schedule(self, bucket):
        if bucket["pending"]:
            return
        current = _date(config_value(self.config, "DataUpdate.Scheduler.NextRun"))
        queued = _date(bucket.pop("refresh_run", None))
        full = _date(bucket.pop("full_due", None))
        if full and queued == current:
            self.config.cross_set("DataUpdate.Scheduler.NextRun", full)

    def begin(self, task):
        if not self.available:
            return
        root, bucket = self._load()
        bucket["waits"].pop(task, None)
        self._save(root)

    def wait(self, task, resource, need):
        """Record only a known resource wait, after its scheduler time is set."""
        if not self.available:
            return
        target = _date(config_value(self.config, f"{task}.Scheduler.NextRun"))
        if not target or need <= 0:
            return
        root, bucket = self._load()
        bucket["waits"][task] = {
            "resource": resource, "need": need, "next_run": target.isoformat(),
            "signature": self._signature(task),
        }
        self._save(root)

    def _blocked(self, task):
        if task == "Combat" and any(config_value(
            self.config, f"{name}.CombatRuntime.Session.active", False
        ) for name in ("Combat", "CombatFarm")):
            return True
        budgets = config_value(self.config, "MissionReward.MissionReward.DailyBudget", {})
        for bucket in budgets.get("contexts", {}).values():
            if (bucket.get("server") == self.server
                    and bucket.get("executors", {}).get(task, {}).get("pending") is not None):
                return True
        return False

    def _reconcile(self, bucket, parsed, now):
        for task, wait in list(bucket["waits"].items()):
            current = _date(config_value(self.config, f"{task}.Scheduler.NextRun"))
            if (not config_value(self.config, f"{task}.Scheduler.Enable", False)
                    or current != _date(wait["next_run"])
                    or wait["signature"] != self._signature(task)):
                bucket["waits"].pop(task)
                continue
            key = wait["resource"]
            if key not in parsed or current <= now or self._blocked(task):
                continue
            value = parsed[key]
            need = wait["need"]
            if value.value >= need:
                target = now
            elif (bucket["observations"][key].get("capacity", value.total) >= need
                  and key in RESOURCE_RECOVERY_SECONDS):
                # The HUD does not expose the partial recovery countdown. A
                # whole interval per missing point is a conservative deadline.
                target = now + timedelta(seconds=(need - value.value) * RESOURCE_RECOVERY_SECONDS[key])
            else:
                continue
            if target < current:
                logger.info(f"Resource sync: {task} {key}={value.value}, next run {current} -> {target}")
                self.config.cross_set(f"{task}.Scheduler.NextRun", target)
                wait["next_run"] = target.isoformat()

    def lightweight_refresh(self):
        _, bucket = self._load()
        full = _date(bucket.get("full_due"))
        current = _date(config_value(self.config, "DataUpdate.Scheduler.NextRun"))
        return bool(bucket["pending"] and full and full > local_now()
                    and current == _date(bucket.get("refresh_run")))

    def finish_refresh(self, *, full=False):
        if not self.available:
            return
        root, bucket = self._load()
        if full:
            due = _date(config_value(self.config, "DataUpdate.Scheduler.NextRun"))
            bucket["full_due"] = due.isoformat() if due else None
        if bucket["pending"]:
            target = local_now().replace(microsecond=0) + timedelta(minutes=RESOURCE_REFRESH_RETRY_MINUTES)
            due = _date(bucket.get("full_due"))
            if due and due > local_now():
                target = min(target, due)
            bucket["refresh_run"] = target.isoformat()
            self.config.cross_set("DataUpdate.Scheduler.NextRun", target)
        else:
            self._restore_full_schedule(bucket)
        self._save(root)
