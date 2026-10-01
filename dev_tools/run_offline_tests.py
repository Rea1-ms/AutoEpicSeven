"""Whitelisted offline tests for this worktree; never load account configs."""

import argparse
import importlib
import json
import subprocess
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SUITES = {
    "equipment_reroll": (
        "tests.equipment_reroll.test_rules",
        "tests.equipment_reroll.test_recognition",
        "tests.equipment_reroll.test_replay",
        "tests.equipment_reroll.test_infrastructure",
    ),
}


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def forbid_real_device(*args, **kwargs):
    raise AssertionError("离线验证禁止创建真实设备或读取账号配置")


def load_tests(suite_name, case=None):
    modules = SUITES["equipment_reroll"] if suite_name == "all" else SUITES[suite_name]
    loader = unittest.TestLoader()
    loaded = unittest.TestSuite(loader.loadTestsFromModule(importlib.import_module(name)) for name in modules)
    if loader.errors:
        raise ValueError("测试模块准备失败：" + "\n".join(loader.errors))
    tests = list(flatten(loaded))
    if case:
        tests = [test for test in tests if test.id() == case]
    if not tests:
        raise ValueError(f"没有找到用例：{case or suite_name}")
    return unittest.TestSuite(tests)


class OfflineResult(unittest.TextTestResult):
    artifact_dir = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def _record(self, test, status, detail=None):
        device = getattr(test, "device", None)
        sample_id = getattr(test, "sample_id", None)
        frame = getattr(device, "image", None)
        image_path = None
        if status not in ("passed", "skipped") and hasattr(frame, "shape"):
            from module.base.utils import save_image

            image_path = self.artifact_dir / f"failure-{len(self.records)}.png"
            save_image(frame, image_path)
        self.records.append({"id": test.id(), "status": status, "detail": detail,
                             "sample_id": sample_id,
                             "image": str(image_path) if image_path else None,
                             "last_frame": repr(frame) if frame is not None and not hasattr(frame, "shape") else None,
                             "actions": getattr(device, "actions", [])})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "skipped", reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._record(test, "subtest_error", self._exc_info_to_string(err, test))


def worker(args):
    with ExitStack() as stack:
        # Importing these classes does not construct them. Patch before loading
        # any business/test modules so accidental real-device calls fail closed.
        stack.enter_context(patch("module.device.device.Device.__init__", forbid_real_device))
        stack.enter_context(patch("module.config.config.AzurLaneConfig.__init__", forbid_real_device))
        tests = load_tests(args.suite, args.case)
        if args.list:
            print(json.dumps([test.id() for test in flatten(tests)], ensure_ascii=False, indent=2))
            return 0
        from tests.support.equipment_reroll import FIXTURES, load_sample

        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        for sample in manifest["samples"]:
            load_sample(sample["id"])
        from module.ocr.models import OCR_MODEL

        for lang in ("cn", "en"):
            OCR_MODEL.get_by_lang(lang)
        OfflineResult.artifact_dir = args.out
        result = unittest.TextTestRunner(verbosity=2, resultclass=OfflineResult).run(tests)
        status = 0 if result.wasSuccessful() and not result.skipped else 1
        report = {
            "exit_code": status, "python": sys.version, "root": str(ROOT),
            "tests_run": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped),
            "cases": result.records,
        }
        (args.out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8", newline="\n")
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["all", *SUITES], default="all")
    parser.add_argument("--case")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker or args.list:
        try:
            return worker(args)
        except (ImportError, OSError, ValueError) as exc:
            print(f"离线验证准备失败：{exc}", file=sys.stderr)
            return 2
    if args.timeout_seconds <= 0:
        parser.error("timeout-seconds 必须大于零")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    output = args.out or ROOT / "screenshots" / "offline_test_results" / timestamp
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"测试产物目录必须是新目录：{exc}", file=sys.stderr)
        return 2
    command = [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--worker",
               "--suite", args.suite, "--out", str(output.resolve())]
    if args.case:
        command.extend(["--case", args.case])
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                   encoding="utf-8", timeout=args.timeout_seconds)
        log = completed.stdout + completed.stderr
        code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        log = (exc.stdout or b"").decode("utf-8", errors="replace")
        log += "\n离线验证超时\n"
        code = 1
    (output / "output.log").write_text(log, encoding="utf-8", newline="\n")
    if not (output / "result.json").exists():
        (output / "result.json").write_text(
            json.dumps({"exit_code": code, "preparation_or_timeout_failure": True,
                        "detail": log}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n",
        )
    print(log, end="")
    print(f"离线验证报告：{output}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
