"""Preview and crop the three workshop prices from registered real samples."""

import argparse
import json
from pathlib import Path

from dev_tools.asset_crop import CropItem, CropRecipe, create_output_plan, write_output_plan
from module.base.utils import load_image


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    plans = []
    for sample, suffix in (("initial", "UNLOCKED"), ("one_lock", "ONE_LOCK"), ("two_locks", "TWO_LOCKS")):
        source = ROOT / "tests" / "fixtures" / "equipment_reroll" / f"{sample}.png"
        recipe = CropRecipe(
            source=str(source), suite="equipment_reroll", case=f"cost_{suffix.lower()}",
            items=(CropItem(f"EQUIPMENT_REROLL_COST_{suffix}", "base", 1, "share", "equipment_reroll",
                            (326, 627, 369, 660)),),
        )
        plan = create_output_plan(load_image(source), recipe, source_path=source,
                                  worktree=ROOT, original_repo=ROOT)
        print(json.dumps(plan.result(dry_run=True), ensure_ascii=False))
        if plan.conflicts:
            raise ValueError(f"Output conflicts: {plan.conflicts}")
        plans.append(plan)
    if args.write:
        for plan in plans:
            write_output_plan(plan)
        from dev_tools.button_extract import generate_code

        print(json.dumps({"generated": generate_code(modules=["equipment_reroll"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
