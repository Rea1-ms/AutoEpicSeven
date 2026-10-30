"""Create this tool's assets with the existing crop API and local sample output."""

import argparse
import hashlib
import json
from pathlib import Path

from dev_tools.asset_crop import CropItem, CropRecipe, create_output_plan, write_output_plan
from module.base.utils import load_image, save_image


ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "initial": "MuMu-20261001-001651-992.png",
    "lock_speed": "MuMu-20261001-004223-239.png",
    "one_lock": "MuMu-20261001-004232-575.png",
    "two_locks": "MuMu-20261001-004237-115.png",
    "two_lock_roll": "MuMu-20261001-004240-137.png",
    "unlocked": "MuMu-20261001-004247-191.png",
    "speed_four": "MuMu-20261001-004252-178.png",
    "replace_confirm": "MuMu-20261001-155433-139.png",
    "replace_applied": "MuMu-20261001-155437-773.png",
    "speed_four_current": "MuMu-20261001-161016-421.png",
    "replace_health_percent_four": "MuMu-20261001-163800-588.png",
    "critical_damage": "MuMu-20261001-174909-267.png",
    "speed_four_percent_health": "MuMu-20261005-175522-657.png",
    "speed_four_unlocked_roll": "MuMu-20261005-175527-407.png",
    "speed_five_two_locks": "MuMu-20261005-180640-096.png",
    "speed_five_locked_roll": "MuMu-20261005-180643-216.png",
    "speed_five_gold_animation": "MuMu-20261005-180647-949.png",
}
BOXES = {
    "EQUIPMENT_REROLL_CHECK": (990, 184, 1116, 216),
    "EQUIPMENT_REROLL_REFRESH": (473, 629, 615, 658),
    "EQUIPMENT_REROLL_KEEP": (220, 454, 404, 491),
    "EQUIPMENT_REROLL_REPLACE": (579, 454, 763, 491),
    "EQUIPMENT_REROLL_UNLOCKED": (165, 197, 188, 221),
    "OCR_EQUIPMENT_REROLL_CURRENT": (201, 194, 466, 363),
    "OCR_EQUIPMENT_REROLL_CANDIDATE": (563, 194, 824, 363),
    "OCR_EQUIPMENT_REROLL_POINTS": (975, 102, 1085, 140),
    "OCR_EQUIPMENT_REROLL_COST": (326, 627, 369, 660),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--case", action="append", choices=list(CASES))
    args = parser.parse_args()
    plans = []
    for case, filename in CASES.items():
        if args.case and case not in args.case:
            continue
        source = args.source / filename
        boxes = BOXES if case == "initial" else {}
        if case == "two_locks":
            boxes = {"EQUIPMENT_REROLL_LOCKED": (165, 197, 188, 221)}
        if case == "replace_confirm":
            boxes = {
                "EQUIPMENT_REROLL_REPLACE_CHECK": (547, 104, 736, 143),
                "EQUIPMENT_REROLL_REPLACE_CONFIRM": (708, 570, 789, 614),
                "OCR_EQUIPMENT_REROLL_REPLACE_STATS": (528, 311, 792, 464),
            }
        if case == "replace_applied":
            boxes = {
                "EQUIPMENT_REROLL_CURRENT_CHECK": (380, 505, 602, 525),
                "EQUIPMENT_REROLL_CURRENT_UNLOCKED": (345, 230, 368, 254),
                "OCR_EQUIPMENT_REROLL_APPLIED": (380, 227, 646, 397),
            }
        if case == "critical_damage":
            boxes = {"EQUIPMENT_REROLL_STAT_CRITICAL_DAMAGE": (570, 338, 642, 361)}
        recipe = CropRecipe(
            source=str(source), suite="equipment_reroll", case=case,
            items=tuple(CropItem(name, "base", 1, "share", "equipment_reroll", box)
                        for name, box in boxes.items()),
        )
        # The crop API explicitly supports the repository output root. Keeping
        # manual samples here avoids writing assets or fixtures to another tree.
        plan = create_output_plan(load_image(source), recipe, source_path=source,
                                  worktree=ROOT, original_repo=ROOT)
        plans.append((case, plan))
    for case, plan in plans:
        print(json.dumps(plan.result(dry_run=True), ensure_ascii=False))
        if plan.conflicts:
            raise ValueError(f"输出冲突：{plan.conflicts}")
    if not args.write:
        return
    fixture_dir = ROOT / "tests" / "fixtures" / "equipment_reroll"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = fixture_dir / "manifest.json"
    samples = (json.loads(manifest_path.read_text(encoding="utf-8"))["samples"]
               if manifest_path.exists() else [])
    for case, plan in plans:
        write_output_plan(plan)
        fixture = fixture_dir / f"{case}.png"
        if fixture.exists():
            raise ValueError(f"正式样本已存在：{fixture}")
        # These workshop screens contain no player name or account identifier;
        # create_output_plan also masks the standard bottom-left account area.
        fixture_image = plan.image.copy()
        if case.startswith(("speed_five_", "speed_four_percent_", "speed_four_unlocked_")):
            # The resource bar is irrelevant to reroll decisions. Keep the
            # workshop balance and all candidate/lock evidence untouched.
            fixture_image[0:66, 440:1160] = 0
        save_image(fixture_image, fixture)
        samples.append({
            "id": case, "path": fixture.name,
            "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            "server": "OVERSEA-Play", "language": "global_cn",
            "scene": case,
            "used_by": ["tests.equipment_reroll.test_recognition.RecognitionTests.test_screenshots"],
        })
    manifest_path.write_text(
        json.dumps({"version": 1, "samples": samples}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    from dev_tools.button_extract import generate_code

    print(json.dumps({"generated": generate_code(modules=["equipment_reroll"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
