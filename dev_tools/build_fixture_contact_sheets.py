"""Make grouped, ignored contact sheets from the registered screenshot fixtures."""

from datetime import datetime
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "dimensional_exploration"
OUTPUT = ROOT / "screenshots" / "fixture_review"


def fixture_groups():
    data = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("fixtures"), dict) or not data["fixtures"]:
        raise ValueError("Unsupported or empty exploration fixture manifest")
    groups = {}
    for suffix, item in sorted(data["fixtures"].items()):
        path = (ROOT / item["path"]).resolve()
        relative = path.relative_to(SOURCE.resolve())
        if path.name != f"MuMu-{suffix}.png":
            raise ValueError(f"Invalid fixture path for {suffix}")
        if not path.is_file():
            raise FileNotFoundError(f"Missing registered screenshot: {suffix}")
        category = relative.parts[0] if len(relative.parts) > 1 else "unclassified"
        groups.setdefault(category, []).append(path)
    return groups


def main():
    groups = fixture_groups()
    review = OUTPUT / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for category, paths in sorted(groups.items()):
        folder = review / category
        folder.mkdir(parents=True, exist_ok=False)
        for group_start in range(0, len(paths), 16):
            canvas = Image.new("RGB", (2048, 1220), "white")
            draw = ImageDraw.Draw(canvas)
            for offset, path in enumerate(paths[group_start:group_start + 16]):
                column = (offset % 4) * 512
                row = (offset // 4) * 305
                with Image.open(path) as source:
                    thumb = ImageOps.contain(source.convert("RGB"), (500, 280))
                canvas.paste(thumb, (column, row))
                draw.text((column + 4, row + 282), path.stem.replace("MuMu-", ""), fill="black")
            destination = folder / f"contact-{group_start // 16 + 1}.jpg"
            canvas.save(destination, quality=90)
            print(destination)
    return review


if __name__ == "__main__":
    main()
