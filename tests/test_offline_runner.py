"""Small contract checks for preflight and deterministic replay."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import run_offline_tests as runner
from dev_tools import build_exploration_fixtures as fixture_importer
from dev_tools import build_fixture_contact_sheets as contact_sheets
from tests.support.exploration import ReplayDevice, artifact_path


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


class FixtureMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.root = artifact_path("fixture-maintenance")
        self.fixtures = self.root / "tests" / "fixtures" / "dimensional_exploration"
        self.source = self.root / "source"
        self.fixtures.mkdir(parents=True)
        self.source.mkdir()
        self.suffix = "20260101-000000-001"

    def register(self, suffix, category="shop", content=b"registered screenshot"):
        path = self.fixtures / category / f"MuMu-{suffix}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "sha256": fixture_importer.file_hash(path),
            "server": "global_cn",
            "scene": "buy",
            "used_by": ["reviewed.case"],
        }

    def save_manifest(self, fixtures):
        path = self.fixtures / "manifest.json"
        path.write_text(json.dumps({"version": 1, "fixtures": fixtures}), encoding="utf-8")

    def import_fixtures(self, references, category="unclassified"):
        with (patch.object(fixture_importer, "ROOT", self.root),
              patch.object(fixture_importer, "FIXTURES", self.fixtures),
              patch.object(fixture_importer, "references", return_value=references)):
            fixture_importer.run(self.source, category)
        return json.loads((self.fixtures / "manifest.json").read_text(encoding="utf-8"))["fixtures"]

    def test_import_keeps_registered_paths_and_metadata(self):
        item = self.register(self.suffix)
        item["note"] = "人工核对后的付款画面"
        self.save_manifest({self.suffix: item})
        (self.source / f"MuMu-{self.suffix}.png").write_bytes(b"registered screenshot")
        result = self.import_fixtures({self.suffix: {"new.case"}}, "events")
        self.assertEqual(result[self.suffix], {**item, "used_by": ["new.case", "reviewed.case"]})
        self.assertFalse((self.fixtures / f"MuMu-{self.suffix}.png").exists())
        self.assertFalse((self.fixtures / "events").exists())

    def test_import_preserves_registered_but_unreferenced_images(self):
        other = "20260101-000000-002"
        existing = {self.suffix: self.register(self.suffix), other: self.register(other, "entry")}
        self.save_manifest(existing)
        (self.source / f"MuMu-{self.suffix}.png").write_bytes(b"registered screenshot")
        result = self.import_fixtures({self.suffix: {"reviewed.case"}})
        self.assertEqual(result, existing)

    def test_import_adds_new_images_to_selected_category(self):
        (self.source / f"MuMu-{self.suffix}.png").write_bytes(b"new screenshot")
        result = self.import_fixtures({self.suffix: {"new.case"}}, "events")
        self.assertEqual(result[self.suffix]["scene"], "events")
        self.assertEqual(result[self.suffix]["path"],
                         f"tests/fixtures/dimensional_exploration/events/MuMu-{self.suffix}.png")
        self.assertEqual((self.root / result[self.suffix]["path"]).read_bytes(), b"new screenshot")
        self.assertFalse((self.fixtures / f"MuMu-{self.suffix}.png").exists())

    def test_import_rejects_changed_registered_source(self):
        item = self.register(self.suffix)
        self.save_manifest({self.suffix: item})
        (self.source / f"MuMu-{self.suffix}.png").write_bytes(b"changed screenshot")
        with self.assertRaisesRegex(ValueError, "Registered fixture differs"):
            self.import_fixtures({self.suffix: {"reviewed.case"}})
        self.assertEqual((self.root / item["path"]).read_bytes(), b"registered screenshot")
        self.assertEqual(json.loads((self.fixtures / "manifest.json").read_text(encoding="utf-8"))["fixtures"],
                         {self.suffix: item})

    def test_import_rejects_paths_outside_fixture_directory(self):
        item = self.register(self.suffix)
        item["path"] = f"source/MuMu-{self.suffix}.png"
        self.save_manifest({self.suffix: item})
        with self.assertRaisesRegex(ValueError, "Invalid fixture path"):
            self.import_fixtures({self.suffix: {"reviewed.case"}})

    def test_contact_sheets_follow_manifest_and_allow_any_count(self):
        other = "20260101-000000-002"
        items = {self.suffix: self.register(self.suffix), other: self.register(other, "entry")}
        self.save_manifest(items)
        (self.fixtures / "MuMu-20260101-000000-003.png").write_bytes(b"not registered")
        with (patch.object(contact_sheets, "ROOT", self.root),
              patch.object(contact_sheets, "SOURCE", self.fixtures)):
            groups = contact_sheets.fixture_groups()
        self.assertEqual(groups, {"shop": [(self.root / items[self.suffix]["path"]).resolve()],
                                  "entry": [(self.root / items[other]["path"]).resolve()]})

    def test_contact_sheets_fail_on_missing_registered_image(self):
        missing = {"path": f"tests/fixtures/dimensional_exploration/shop/MuMu-{self.suffix}.png"}
        self.save_manifest({self.suffix: missing})
        with (patch.object(contact_sheets, "ROOT", self.root),
              patch.object(contact_sheets, "SOURCE", self.fixtures)):
            with self.assertRaises(FileNotFoundError):
                contact_sheets.fixture_groups()


if __name__ == "__main__":
    unittest.main()
