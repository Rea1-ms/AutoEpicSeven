"""Prove that assertion failures and real-device attempts fail with useful reports."""

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from dev_tools import run_offline_tests as runner
from tests.dimensional_exploration.test_dimensional_exploration import PolicyTests
from tests.dimensional_exploration.test_dimensional_exploration_shop_new import ShopNewRegressions


def check(case_id, cls, method_name, replacement, expected_text, expected_attachments):
    destination = runner.ROOT / "screenshots" / "offline_test_results" / f"negative-{uuid4().hex}"
    args = SimpleNamespace(suite="dimensional_exploration", case=case_id, list=False,
                           coverage=False, out=str(destination))
    with patch.object(cls, method_name, replacement):
        exit_code = runner.run(args)
    report = json.loads((destination / "result.json").read_text(encoding="utf-8"))
    record = report["tests"][0]
    attachments = list((destination / "failures").rglob("*.png")) if (destination / "failures").exists() else []
    if exit_code != 1 or record["status"] != "failed" or expected_text not in "\n".join(record["details"]):
        raise AssertionError(f"Negative check did not fail as expected: {destination}")
    if len(attachments) != expected_attachments:
        raise AssertionError(f"Negative check attached {len(attachments)} screenshots, expected {expected_attachments}")
    print(f"Verified failure status and attachments: {destination}")


def main():
    with patch("importlib.util.find_spec", return_value=None):
        missing_model = runner.run(SimpleNamespace(suite="dimensional_exploration", case=None,
                                                  list=False, coverage=False, out=None))
    if missing_model != 2:
        raise AssertionError(f"Missing OCR model returned {missing_model}, expected 2")
    print("Verified missing OCR model returns preparation status 2")

    def wrong_expectation(self):
        self.assertEqual(1, 2)

    check("tests.dimensional_exploration.test_dimensional_exploration_shop_new.ShopNewRegressions."
          "test_badges_bind_to_exact_items_in_both_rows", ShopNewRegressions,
          "test_badges_bind_to_exact_items_in_both_rows", wrong_expectation, "1 != 2", 2)

    def forbidden_device(self):
        from module.device.device import Device

        Device(None)

    check("tests.dimensional_exploration.test_dimensional_exploration.PolicyTests."
          "test_core_boundary_uses_current_count", PolicyTests,
          "test_core_boundary_uses_current_count", forbidden_device,
          "Offline suite attempted to create a real device", 0)


if __name__ == "__main__":
    main()
