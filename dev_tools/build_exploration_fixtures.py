"""Import referenced exploration screenshots into the versioned fixture set.

This is an explicit maintenance command, not part of the test run. Existing
fixtures are never overwritten; a changed source image needs human review.
"""

import argparse
import ast
import hashlib
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests" / "dimensional_exploration"
FIXTURES = ROOT / "tests" / "fixtures" / "dimensional_exploration"
SUFFIX = re.compile(r"2026\d{4}-\d{6}-\d{3}")


def references():
    result = {}
    for source in sorted(TESTS.glob("test_dimensional_exploration*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        module = f"tests.dimensional_exploration.{source.stem}"
        for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            setup = set()
            for method in cls.body:
                if isinstance(method, ast.FunctionDef) and method.name == "setUpClass":
                    setup = set(SUFFIX.findall(ast.get_source_segment(source.read_text(encoding="utf-8"), method)))
            for method in cls.body:
                if not isinstance(method, ast.FunctionDef) or not method.name.startswith("test_"):
                    continue
                test_id = f"{module}.{cls.name}.{method.name}"
                text = ast.get_source_segment(source.read_text(encoding="utf-8"), method)
                for suffix in sorted(set(SUFFIX.findall(text)) | setup):
                    result.setdefault(suffix, set()).add(test_id)
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(source_root):
    refs = references()
    if not refs:
        raise ValueError("No screenshot references found")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for suffix, users in sorted(refs.items()):
        matches = list(source_root.rglob(f"MuMu-{suffix}.png"))
        if not matches:
            raise FileNotFoundError(f"Missing screenshot: {suffix}")
        fingerprints = {file_hash(path) for path in matches}
        if len(fingerprints) != 1:
            raise ValueError(f"Different screenshots share the same ID: {suffix}")
        target = FIXTURES / f"MuMu-{suffix}.png"
        expected = next(iter(fingerprints))
        if target.exists():
            if file_hash(target) != expected:
                raise ValueError(f"Existing fixture differs from source: {suffix}")
        else:
            shutil.copyfile(matches[0], target)
        manifest[suffix] = {
            "path": target.relative_to(ROOT).as_posix(),
            "sha256": expected,
            "server": "global_cn",
            "scene": "dimensional_exploration",
            "used_by": sorted(users),
        }
    destination = FIXTURES / "manifest.json"
    destination.write_text(json.dumps({"version": 1, "fixtures": manifest}, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    print(f"Imported {len(manifest)} unique screenshots into {FIXTURES}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Directory containing original MuMu screenshots")
    args = parser.parse_args()
    run(args.source)
