"""Crop the full-roll and normal-roll markers from a verified workshop image."""

import argparse
import json
from pathlib import Path

from dev_tools.asset_crop import CropItem, CropRecipe, create_output_plan, write_output_plan
from module.base.utils import load_image


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    recipe = CropRecipe(
        source=str(args.image), suite="equipment_reroll", case="gold_markers",
        items=(
            CropItem("EQUIPMENT_REROLL_ROLL_FULL", "base", 1, "share", "equipment_reroll", (524, 244, 545, 267)),
            CropItem("EQUIPMENT_REROLL_ROLL_NORMAL", "base", 1, "share", "equipment_reroll", (524, 197, 545, 220)),
        ),
    )
    plan = create_output_plan(load_image(args.image), recipe, source_path=args.image,
                              worktree=ROOT, original_repo=ROOT)
    print(json.dumps(plan.result(dry_run=True), ensure_ascii=False))
    if plan.conflicts:
        raise ValueError(f"输出冲突：{plan.conflicts}")
    if args.write:
        write_output_plan(plan)
        from dev_tools.button_extract import generate_code

        print(json.dumps({"generated": generate_code(modules=["equipment_reroll"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
