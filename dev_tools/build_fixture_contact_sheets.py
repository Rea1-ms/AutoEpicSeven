"""Make ignored contact sheets for a human privacy review of screenshot fixtures."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "dimensional_exploration"
OUTPUT = ROOT / "screenshots" / "fixture_review"


def main():
    paths = sorted(SOURCE.glob("MuMu-*.png"))
    if len(paths) != 62:
        raise ValueError(f"Expected 62 fixtures, found {len(paths)}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for group_start in range(0, len(paths), 16):
        canvas = Image.new("RGB", (2048, 1220), "white")
        draw = ImageDraw.Draw(canvas)
        for offset, path in enumerate(paths[group_start:group_start + 16]):
            x, y = (offset % 4) * 512, (offset // 4) * 305
            with Image.open(path) as source:
                thumb = ImageOps.contain(source.convert("RGB"), (500, 280))
            canvas.paste(thumb, (x, y))
            draw.text((x + 4, y + 282), path.stem.replace("MuMu-", ""), fill="black")
        destination = OUTPUT / f"contact-{group_start // 16 + 1}.jpg"
        canvas.save(destination, quality=90)
        print(destination)


if __name__ == "__main__":
    main()
