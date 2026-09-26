"""Small contract checks for preflight and deterministic replay."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import run_offline_tests as runner
from tests.support.exploration import ReplayDevice


class RunnerContractTests(unittest.TestCase):
    def test_unknown_case_returns_preparation_status(self):
        args = SimpleNamespace(list=True, suite="dimensional_exploration", case="not.registered")
        self.assertEqual(runner.run(args), 2)

    def test_missing_fixture_is_not_skipped(self):
        missing = {"version": 1, "fixtures": {"20260101-000000-001": {
            "path": "tests/fixtures/dimensional_exploration/MuMu-20260101-000000-001.png",
            "sha256": "0" * 64}}}
        with patch.object(type(runner.FIXTURE_MANIFEST), "read_text", return_value=json.dumps(missing)):
            with self.assertRaises(FileNotFoundError):
                runner.fixture_manifest(verify=True)

    def test_changed_fixture_is_not_silently_accepted(self):
        with patch.object(runner, "sha256", return_value="0" * 64):
            with self.assertRaisesRegex(ValueError, "checksum differs"):
                runner.fixture_manifest(verify=True)

    def test_replay_only_advances_on_explicit_frame_request(self):
        replay = ReplayDevice(["20260925-231015-103", "20260925-231015-103"], ["BUY"])
        self.assertEqual(replay.index, 0)
        replay.click("BUY")
        self.assertEqual(replay.index, 0)
        replay.screenshot()
        self.assertEqual(replay.index, 1)
        replay.assert_actions_complete()
        with self.assertRaises(AssertionError):
            replay.screenshot()


if __name__ == "__main__":
    unittest.main()
