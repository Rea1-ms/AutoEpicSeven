"""Crop the verified speed label from a registered comparison screenshot."""

import argparse
import json
from pathlib import Path

from dev_tools.asset_crop import CropItem, CropRecipe, create_output_plan, write_output_plan
from module.base.utils import load_image


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--frame", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    sample = "speed_five_two_locks" if args.frame == 1 else "initial"
    source = ROOT / "tests" / "fixtures" / "equipment_reroll" / f"{sample}.png"
    case = "speed_name" if args.frame == 1 else "speed_name_bright"
    box = (570, 291, 610, 315) if args.frame == 1 else (208, 338, 250, 362)
    recipe = CropRecipe(
        source=str(source), suite="equipment_reroll", case=case,
        items=(CropItem("EQUIPMENT_REROLL_STAT_SPEED", "base", args.frame, "share", "equipment_reroll", box),),
    )
    plan = create_output_plan(load_image(source), recipe, source_path=source,
                              worktree=ROOT, original_repo=ROOT)
    print(json.dumps(plan.result(dry_run=True), ensure_ascii=False))
    if plan.conflicts:
        raise ValueError(f"Output conflicts: {plan.conflicts}")
    if args.write:
        write_output_plan(plan)
        from dev_tools.button_extract import generate_code

        print(json.dumps({"generated": generate_code(modules=["equipment_reroll"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
