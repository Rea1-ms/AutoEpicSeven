"""Record the ignored historical test tree without importing any of its scripts."""

import argparse
import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "tests" / "inventory.json"
MIGRATED = {"test_dimensional_exploration.py", "test_dimensional_exploration_events.py",
            "test_dimensional_exploration_sampling.py", "test_dimensional_exploration_shop_new.py",
            "test_dimensional_exploration_ui.py"}


def category(name, source):
    lowered = name.lower()
    if any(word in lowered for word in ("replay", "restart", "receipt", "pending", "click", "transition", "payment")):
        return "流程回放"
    if any(word in lowered for word in ("asset", "import", "registration", "route", "resource")):
        return "资源基础检查"
    if any(word in lowered for word in ("ocr", "screenshot", "badge", "page", "image", "template", "frame")):
        return "截图识别"
    if any(word in source.lower() for word in ("adb", "device(", "screenshot(", "tap(", "swipe(")):
        return "实机调试工具"
    return "规则测试"


def build(source_root):
    manifest = json.loads((ROOT / "tests" / "fixtures" / "dimensional_exploration" / "manifest.json").read_text(encoding="utf-8"))["fixtures"]
    records = []
    for source in sorted(source_root.glob("test_*.py")):
        raw = source.read_text(encoding="utf-8-sig")
        tree = ast.parse(raw, filename=str(source))
        migrated = source.name in MIGRATED
        business = "肉鸽" if "dimensional_exploration" in source.name else (
            "Alasio 分支" if "alasio" in source.name.lower() else "其他业务或框架")
        tests = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                parent = next((cls.name for cls in tree.body if isinstance(cls, ast.ClassDef) and node in cls.body), None)
                test_id = ".".join(filter(None, ["tests" if migrated else "test", "dimensional_exploration" if migrated else "", source.stem, parent, node.name]))
                body = ast.get_source_segment(raw, node) or ""
                device = any(term in body.lower() for term in ("adb", "device(", "screenshot(", "swipe("))
                tests.append({
                    "id": test_id,
                    "category": category(node.name, body),
                    "requires_device": False if migrated else device,
                    "has_automatic_assertion": any(isinstance(n, ast.Assert) or
                                                   isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                                   and n.func.attr.startswith("assert") for n in ast.walk(node)),
                    "fixtures": sorted(key for key, item in manifest.items() if test_id in item["used_by"]) if migrated else [],
                    "status": "已迁移" if migrated else "未纳入",
                    "reason": "" if migrated else "首轮只迁移肉鸽；需核对依赖、样本与断言后再纳入",
                })
        records.append({
            "source": "test/" + source.name,
            "business": business,
            "status": "已迁移" if migrated else "未纳入",
            "reason": "" if migrated else "首轮只迁移肉鸽；不自动导入历史脚本",
            "cases": tests,
            "file_category": category(source.name, raw) if not tests else None,
        })
    if len(records) != 100:
        raise ValueError(f"Expected 100 source scripts, found {len(records)}")
    if sum(len(item["cases"]) for item in records if item["status"] == "已迁移") != 59:
        raise ValueError("Migrated case count is not 59")
    contract_source = ROOT / "tests" / "test_offline_runner.py"
    contract_tree = ast.parse(contract_source.read_text(encoding="utf-8"))
    contract_cases = [{
        "id": f"tests.test_offline_runner.{cls.name}.{method.name}",
        "category": "资源基础检查",
        "requires_device": False,
        "has_automatic_assertion": True,
        "fixtures": [],
        "status": "新增",
        "reason": "",
    } for cls in contract_tree.body if isinstance(cls, ast.ClassDef)
        for method in cls.body if isinstance(method, ast.FunctionDef) and method.name.startswith("test_")]
    records.append({"source": "tests/test_offline_runner.py", "business": "测试基础设施",
                    "status": "新增", "reason": "", "cases": contract_cases, "file_category": None})
    DESTINATION.write_text(json.dumps({"schema_version": 1, "source_root": "ignored test/ in main checkout",
                                       "files": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded 100 historical scripts, 59 migrated cases and {len(contract_cases)} new cases")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    build(parser.parse_args().source)
