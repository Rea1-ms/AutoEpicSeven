"""Run the versioned offline regression suite from any working directory."""

import argparse
import contextlib
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
EXPLORATION_MODULES = (
    "tests.dimensional_exploration.test_dimensional_exploration",
    "tests.dimensional_exploration.test_dimensional_exploration_events",
    "tests.dimensional_exploration.test_dimensional_exploration_sampling",
    "tests.dimensional_exploration.test_dimensional_exploration_shop_new",
    "tests.dimensional_exploration.test_dimensional_exploration_ui",
    "tests.dimensional_exploration.test_dimensional_exploration_runtime",
)
RUNNER_MODULES = ("tests.test_offline_runner",)
SANCTUARY_MODULES = (
    "tests.sanctuary.test_smart_custody",
    "tests.sanctuary.test_monthly_scheduling",
    "tests.sanctuary.test_monthly",
    "tests.sanctuary.test_monthly_deposit",
    "tests.sanctuary.test_monthly_reminder",
    "tests.sanctuary.test_captured_sanctuary",
)
UPSTREAM_MODULES = (
    "tests.upstream.test_device",
    "tests.upstream.test_deploy",
    "tests.upstream.test_vision",
    "tests.upstream.test_upstream_emulator_sync",
)
CORE_MODULES = (
    "tests.core.test_data_update",
    "tests.core.test_page_route",
    "tests.core.test_community_aio",
    "tests.core.test_mission_reward_callbacks",
    "tests.core.test_mail",
    "tests.core.test_login_domain_popup",
    "tests.game_info.test_game_info",
    "tests.game_info.test_game_info_seasons",
)
ACTIVITY_MODULES = (
    "tests.activity.test_reward_scheduling",
    "tests.activity.test_special_activity_config",
    "tests.activity.test_activity_task_split",
    "tests.activity.test_activity_batch",
    "tests.activity.test_free_gacha_20",
)
COMBAT_MODULES = (
    "tests.combat.test_combat_entry_search",
    "tests.combat.test_repeat_running_detail",
    "tests.combat.test_episode",
)
STORE_MODULES = ()
SECRET_SHOP_MODULES = ("tests.secret_shop.test_payment", "tests.secret_shop.test_captures")
INTERACTION_MODULES = ("tests.core.test_missed_clicks",)
HISTORICAL_REPLAY_MODULES = (
    "tests.activity.test_cn_september_update",
    "tests.core.test_september_runtime_fixes",
    "tests.combat.test_repeat_settlement_return",
    "tests.combat.test_repeat_progress",
    "tests.combat.test_fast_combat_counter",
    "tests.combat.test_urgent_tasks_first_clear",
    "tests.combat.test_urgent_tasks_hint",
    "tests.activity.test_e7wc_battle_gate",
    "tests.activity.test_koharu_raffle",
    "tests.activity.test_common_activity",
    "tests.activity.test_huche_shop",
    "tests.activity.test_huche_discount_batch",
    "tests.activity.test_huche_drag_recovery",
    "tests.activity.test_huche_overlap_names",
    "tests.combat.test_rune_balance",
    "tests.core.test_main_navigation",
    "tests.gacha.test_gacha_result",
)
HISTORICAL_CAPTURE_MODULES = (
    "tests.captured.test_e7wc_battle_gate",
    "tests.captured.test_koharu_raffle",
    "tests.captured.test_huche_shop",
    "tests.captured.test_huche_discount_batch",
    "tests.captured.test_huche_drag_recovery",
    "tests.captured.test_common_activity",
    "tests.captured.test_fast_combat_counter",
    "tests.captured.test_urgent_tasks_first_clear",
    "tests.captured.test_urgent_tasks_hint",
    "tests.captured.test_main_navigation",
    "tests.captured.test_knights_crest_ocr",
    "tests.captured.test_store_inheritance_charms",
    "tests.captured.test_store_inheritance_cooldown",
    "tests.captured.test_activity_selection",
    "tests.captured.test_asset_crop",
    "tests.captured.test_urgent_tasks",
    "tests.captured.test_navigation_recognition",
    "tests.captured.test_repeat_settlement_return",
)
HISTORICAL_MANUAL_MODULES = (
    "tests.combat.test_fast_combat_stamina",
    "tests.combat.test_burnout",
    "tests.combat.test_background_repeat_wait",
    "tests.core.test_knights_team_battle_status_legacy",
    "tests.combat.test_repeat_combat_v2",
)
SUITES = {
    "all": EXPLORATION_MODULES + SANCTUARY_MODULES + RUNNER_MODULES + UPSTREAM_MODULES
           + CORE_MODULES + ACTIVITY_MODULES + COMBAT_MODULES + STORE_MODULES
           + HISTORICAL_REPLAY_MODULES + HISTORICAL_CAPTURE_MODULES + HISTORICAL_MANUAL_MODULES
           + SECRET_SHOP_MODULES + INTERACTION_MODULES,
    "dimensional_exploration": EXPLORATION_MODULES,
    "sanctuary": SANCTUARY_MODULES,
    "runner": RUNNER_MODULES,
    "upstream": UPSTREAM_MODULES,
    "core": CORE_MODULES,
    "activity": ACTIVITY_MODULES,
    "combat": COMBAT_MODULES,
    "secret_shop": SECRET_SHOP_MODULES,
    "interaction": INTERACTION_MODULES,
    "store": ("tests.captured.test_store_inheritance_charms", "tests.captured.test_store_inheritance_cooldown"),
    "legacy_rules": CORE_MODULES + ACTIVITY_MODULES + COMBAT_MODULES + STORE_MODULES
                    + ("tests.upstream.test_upstream_emulator_sync",),
    "legacy_replay": HISTORICAL_REPLAY_MODULES,
    "legacy_captures": HISTORICAL_CAPTURE_MODULES,
    "legacy_manual": HISTORICAL_MANUAL_MODULES,
    "legacy_tail": ("tests.captured.test_urgent_tasks", "tests.captured.test_navigation_recognition",
                    "tests.captured.test_repeat_settlement_return"),
}
FIXTURE_MANIFEST = ROOT / "tests" / "fixtures" / "dimensional_exploration" / "manifest.json"
SANCTUARY_FIXTURE_MANIFEST = ROOT / "tests" / "fixtures" / "sanctuary" / "manifest.json"
HISTORICAL_FIXTURE_MANIFEST = ROOT / "tests" / "fixtures" / "historical" / "manifest.json"
SECRET_SHOP_FIXTURE_MANIFEST = ROOT / "tests" / "fixtures" / "secret_shop" / "manifest.json"


def iter_cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def tests_for(suite_name):
    suite = unittest.TestSuite()
    for name in SUITES[suite_name]:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
    cases = list(iter_cases(suite))
    ids = [case.id() for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate offline test IDs")
    return cases


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixture_manifest(verify):
    fixtures = {}
    paths = set()
    # Explicit manifests keep historical/manual screenshots outside the test
    # corpus. IDs share one report namespace, so collisions must fail before
    # tests run rather than silently replacing another business's evidence.
    for manifest in (FIXTURE_MANIFEST, SANCTUARY_FIXTURE_MANIFEST, HISTORICAL_FIXTURE_MANIFEST,
                     SECRET_SHOP_FIXTURE_MANIFEST):
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        entries = raw.get("fixtures")
        if raw.get("version") != 1 or not isinstance(entries, dict) or not entries:
            raise ValueError("Fixture manifest is empty or has an unsupported version")
        aliases = raw.get("aliases", {})
        if not isinstance(aliases, dict):
            raise ValueError("Invalid fixture aliases")
        for alias, target in aliases.items():
            relative_alias = Path(alias)
            if (relative_alias.is_absolute() or '..' in relative_alias.parts
                    or target not in entries):
                raise ValueError(f"Invalid fixture alias: {alias}")
        for key, item in entries.items():
            if key in fixtures:
                raise ValueError(f"Duplicate fixture ID: {key}")
            relative = Path(item["path"])
            path = ROOT / relative
            if (relative.is_absolute() or ".." in relative.parts
                    or key != relative.stem.removeprefix("MuMu-")
                    or not path.resolve().is_relative_to(manifest.parent.resolve())):
                raise ValueError(f"Invalid fixture path for {key}")
            if relative.as_posix() in paths:
                raise ValueError(f"Duplicate fixture path for {key}")
            paths.add(relative.as_posix())
            if verify:
                if not path.is_file():
                    raise FileNotFoundError(f"Fixture missing: {relative.as_posix()}")
                if sha256(path) != item["sha256"]:
                    raise ValueError(f"Fixture checksum differs: {relative.as_posix()}")
                with Image.open(path) as image:
                    if image.size != (1280, 720):
                        raise ValueError(f"Fixture dimensions differ: {relative.as_posix()}")
            fixtures[key] = item
    if verify:
        import importlib.util

        spec = importlib.util.find_spec("pponnxcr")
        if spec is None or spec.origin is None:
            raise FileNotFoundError("Chinese OCR package is unavailable")
        model = Path(spec.origin).parent / "model"
        for name in ("ch_PP-OCRv3_det_infer.onnx", "ch_PP-OCRv3_rec_infer.onnx",
                     "ch_ppocr_mobile_v2.0_cls_infer.onnx", "ppocr_keys_v1.txt"):
            if not (model / name).is_file():
                raise FileNotFoundError(f"Chinese OCR model is missing: {name}")
    return fixtures


class Result(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.records = {}
        self.started = {}

    def startTest(self, test):
        from tests.support.offline import set_active_test

        set_active_test(test.id())
        self.started[test.id()] = time.monotonic()
        self.records[test.id()] = {"id": test.id(), "status": "passed", "details": []}
        super().startTest(test)

    def stopTest(self, test):
        from tests.support.offline import finish_test

        self.records[test.id()]["diagnostics"] = finish_test(test.id())
        self.records[test.id()]["duration_seconds"] = round(time.monotonic() - self.started[test.id()], 3)
        super().stopTest(test)

    def addFailure(self, test, err):
        self.records[test.id()]["status"] = "failed"
        self.records[test.id()]["details"].append("".join(traceback.format_exception(*err)))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.records[test.id()]["status"] = "error"
        self.records[test.id()]["details"].append("".join(traceback.format_exception(*err)))
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.records[test.id()]["status"] = "skipped"
        self.records[test.id()]["details"].append(reason)
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            record = self.records[test.id()]
            record["status"] = "failed" if issubclass(err[0], AssertionError) else "error"
            record["details"].append(str(subtest) + "\n" + "".join(traceback.format_exception(*err)))
        super().addSubTest(test, subtest, err)


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, value):
        for stream in self.streams:
            stream.write(value)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def run(args):
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    try:
        fixtures = fixture_manifest(verify=not args.list)
    except (FileNotFoundError, ImportError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"测试准备失败：{exc}", file=sys.stderr)
        return 2

    if args.list:
        try:
            cases = tests_for(args.suite)
            if args.case:
                cases = [case for case in cases if case.id() == args.case]
            if not cases:
                raise ValueError(f"No registered offline test matched: {args.case!r}")
        except (ImportError, KeyError, ValueError) as exc:
            print(f"测试准备失败：{exc}", file=sys.stderr)
            return 2
        inventory = json.loads((ROOT / "tests" / "inventory.json").read_text(encoding="utf-8"))
        categories = {case["id"]: case["category"] for item in inventory["files"] for case in item["cases"]}
        for case in cases:
            related = sorted(key for key, item in fixtures.items() if case.id() in item.get("used_by", []))
            capture_servers = sorted({fixtures[key]['server'] for key in related})
            capture_label = ','.join(capture_servers) or '无截图'
            print(f"{case.id()}  [{categories.get(case.id(), '未分类')}] [样本来源={capture_label}]  screenshots={','.join(related) or '-'}")
        print(f"列出 {len(cases)} 项；未执行测试")
        return 0

    destination = Path(args.out) if args.out else ROOT / "screenshots" / "offline_test_results" / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
    if not destination.is_absolute():
        destination = ROOT / destination
    if destination.exists():
        print(f"测试准备失败：产物目录已存在：{destination}", file=sys.stderr)
        return 2
    destination.mkdir(parents=True)
    os.environ["AES_TEST_ARTIFACT_DIR"] = str(destination / "artifacts")

    cov = None
    if args.coverage:
        try:
            import coverage
        except ImportError:
            print("测试准备失败：缺少 coverage 测试依赖", file=sys.stderr)
            return 2
        cov = coverage.Coverage(source=["tasks.dimensional_exploration"], branch=True,
                                omit=["*/assets/*"], data_file=str(destination / ".coverage"))
        cov.start()

    try:
        cases = tests_for(args.suite)
        if args.case:
            cases = [case for case in cases if case.id() == args.case]
        if not cases:
            raise ValueError(f"No registered offline test matched: {args.case!r}")
    except (ImportError, KeyError, ValueError) as exc:
        if cov:
            cov.stop()
        print(f"测试准备失败：{exc}", file=sys.stderr)
        return 2

    # A real Device constructor connects to ADB. Offline tests use FakeDevice;
    # failing here makes a mistaken real-device path visible before any click.
    import uiautomator2
    from adbutils import AdbClient
    from module.device.connection import Connection
    from module.device.device import Device

    def reject_device(*_args, **_kwargs):
        raise AssertionError("Offline suite attempted to create a real device")

    started = time.monotonic()
    with (destination / "output.log").open("w", encoding="utf-8", newline="\n") as logfile:
        output = Tee(sys.stdout, logfile)
        with contextlib.ExitStack() as guards:
            guards.enter_context(patch.object(Device, "__init__", reject_device))
            guards.enter_context(patch.object(Connection, "__init__", reject_device))
            guards.enter_context(patch.object(AdbClient, "connect", reject_device))
            guards.enter_context(patch.object(AdbClient, "device", reject_device))
            guards.enter_context(patch.object(uiautomator2, "connect", reject_device))
            runner = unittest.TextTestRunner(stream=output, verbosity=2, resultclass=Result)
            result = runner.run(unittest.TestSuite(cases))
    elapsed = round(time.monotonic() - started, 3)
    coverage_totals = None
    if cov:
        cov.stop()
        cov.save()
        cov.json_report(outfile=str(destination / "coverage.json"), pretty_print=True)
        coverage_totals = json.loads((destination / "coverage.json").read_text(encoding="utf-8"))["totals"]

    records = list(result.records.values())
    for record in records:
        related = [(key, item) for key, item in fixtures.items() if record["id"] in item.get("used_by", [])]
        record["fixtures"] = [item["path"] for _, item in related]
        record["fixture_servers"] = {key: item['server'] for key, item in related}
        if record["status"] in ("failed", "error") and related:
            failure_dir = destination / "failures" / re.sub(r"[^A-Za-z0-9_.-]", "_", record["id"])
            failure_dir.mkdir(parents=True)
            for key, item in related:
                shutil.copyfile(ROOT / item["path"], failure_dir / f"{key}.png")
    counts = {status: sum(record["status"] == status for record in records)
              for status in ("passed", "failed", "error", "skipped")}
    report = {
        "schema_version": 1,
        "suite": args.suite,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": sys.version.split()[0],
        "packages": {name: importlib.metadata.version(name) for name in ("numpy", "opencv-python", "Pillow", "pponnxcr", "onnxruntime")},
        "fixture_count": len(fixtures),
        "duration_seconds": elapsed,
        "counts": counts,
        "coverage": coverage_totals,
        "tests": records,
    }
    (destination / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
    print(f"离线测试：{counts}；耗时 {elapsed:.1f} 秒；报告：{destination}")
    return 0 if result.wasSuccessful() and counts["skipped"] == 0 and len(records) == len(cases) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=sorted(SUITES), default="all")
    parser.add_argument("--case", help="Full unittest case ID")
    parser.add_argument("--list", action="store_true", help="List selected cases without running them")
    parser.add_argument("--coverage", action="store_true", help="Measure exploration line and branch coverage")
    parser.add_argument("--out", help="New report directory; existing directories are never overwritten")
    parser.add_argument("--timeout-seconds", type=int, help="Fail the test process after this many seconds")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.timeout_seconds is not None and args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    if args.timeout_seconds and not args.child and not args.list:
        command = [sys.executable, "-B", "-m", "dev_tools.run_offline_tests", "--suite", args.suite, "--child"]
        if args.case:
            command.extend(("--case", args.case))
        if args.coverage:
            command.append("--coverage")
        destination = args.out or str(ROOT / "screenshots" / "offline_test_results" /
                                      (datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"))
        command.extend(("--out", destination))
        try:
            return subprocess.run(command, cwd=ROOT, timeout=args.timeout_seconds, check=False).returncode
        except subprocess.TimeoutExpired:
            location = Path(destination)
            location.mkdir(parents=True, exist_ok=True)
            (location / "timeout.txt").write_text(f"Offline suite exceeded {args.timeout_seconds} seconds\n",
                                                  encoding="utf-8")
            print(f"离线测试超时：{args.timeout_seconds} 秒；报告：{location}", file=sys.stderr)
            return 1
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
