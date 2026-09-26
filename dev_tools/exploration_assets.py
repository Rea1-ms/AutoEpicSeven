"""Reproduce the dimensional-exploration crops from the supplied screenshots.

Only this module's source images are written. Generate wrappers afterwards with
``python -m dev_tools.button_extract --module dimensional_exploration``.
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


# name, screenshot suffix, crop, optional search, optional click rectangle
CROPS = [
    ("TITLE_CHECK", "20260921-105750-804", (52, 119, 299, 216)),
    ("TITLE_ENTER", "20260921-105750-804", (1044, 649, 1134, 673)),
    ("CHAPTER_CHECK", "20260922-081145-848", (156, 299, 258, 360)),
    ("LOBBY_CHECK", "20260922-081153-551", (1043, 649, 1134, 673)),
    ("LOBBY_CHECK.2", "20260924-091808-594", (1062, 649, 1115, 673)),
    ("LOBBY_START", "20260922-081153-551", (1043, 649, 1134, 673)),
    ("LOBBY_CONTINUE", "20260924-091808-594", (1062, 649, 1115, 673)),
    ("SUPPLY_CHECK", "20260922-081226-118", (54, 315, 133, 336)),
    ("SUPPLY_BAGGAGE", "20260922-081226-118", (675, 258, 770, 285)),
    ("SUPPLY_SELECTED", "20260922-081226-118", (590, 135, 599, 248)),
    ("SUPPLY_CONFIRM", "20260922-081226-118", (1064, 649, 1116, 673)),
    ("RECRUITMENT_CHECK", "20260922-081357-541", (773, 649, 821, 673)),
    ("EXPLORE_ENTER", "20260922-081357-541", (1044, 649, 1133, 673)),
    ("HERO_PICKER_CHECK", "20260922-081352-852", (698, 23, 762, 45)),
    ("HERO_PICKER_CHECK.2", "20260923-233032-963", (698, 23, 762, 45)),
    ("HERO_COST_ICON", "20260922-081352-852", (582, 133, 602, 159), (348, 85, 1258, 592)),
    ("HERO_COST_ICON.2", "20260923-233036-793", (634, 133, 656, 159)),
    ("HERO_CONFIRM", "20260922-081352-852", (1042, 649, 1133, 673)),
    ("MAP_CHECK", "20260922-081522-090", (135, 642, 257, 664)),
    ("NODE_AVAILABLE", "20260922-081522-090", (522, 133, 542, 151), (0, 85, 1230, 565)),
    ("NODE_EVENT", "20260922-081522-090", (518, 154, 560, 221)),
    ("NODE_BATTLE", "20260922-081522-090", (513, 264, 564, 325)),
    ("NODE_REST", "20260922-081522-090", (511, 360, 565, 423)),
    ("NODE_ELITE", "20260922-082932-782", (320, 258, 378, 326)),
    ("NODE_BOSS", "20260922-082932-782", (618, 305, 667, 371)),
    ("NODE_SHOP", "20260923-233512-883", (315, 415, 377, 470)),
    ("NODE_SUPPLY", "20260923-233512-883", (17, 211, 72, 266)),
    ("PREVIEW_CHECK", "20260922-082932-782", (945, 102, 1023, 124)),
    ("PREVIEW_CHECK.2", "20260922-081603-149", (945, 102, 1023, 124)),
    ("PREVIEW_CHECK.3", "20260922-081803-041", (945, 102, 1023, 124)),
    ("PREVIEW_CHECK.4", "20260922-081725-462", (945, 102, 1000, 124)),
    ("NODE_ENTER", "20260922-082932-782", (1064, 649, 1115, 673)),
    ("EVENT_CHECK", "20260923-232331-766", (120, 102, 156, 131)),
    ("EVENT_OPTION", "20260923-232331-766", (75, 660, 98, 689), (30, 646, 1210, 698)),
    ("EVENT_NEXT", "20260922-081548-987", (631, 693, 648, 704)),
    ("EVENT_JOURNAL", "20260922-081528-636", (272, 560, 297, 585)),
    ("ROOM_SUPPLY_CHECK", "20260922-081730-252", (930, 295, 1049, 320)),
    ("REST_CHECK", "20260923-232305-766", (932, 408, 1053, 427)),
    ("ROOM_DONE", "20260923-232319-917", (1174, 175, 1214, 216), (1165, 155, 1220, 553)),
    ("ROOM_LEAVE", "20260923-232305-766", (1065, 649, 1115, 673)),
    ("UPGRADE_CHECK", "20260923-232309-289", (70, 20, 170, 47)),
    ("REVIVE_CHECK", "20260923-233115-192", (70, 20, 170, 47)),
    ("SHOP_CHECK", "20260923-232839-814", (439, 115, 529, 140)),
    ("SHOP_NEW", "20260925-231015-103", (767, 90, 794, 117), (384, 84, 1145, 594)),
    ("SHOP_SOLD", "20260923-232839-814", (1015, 544, 1091, 567), (393, 280, 1140, 580)),
    ("BUY_CHECK", "20260923-232843-964", (579, 141, 703, 171)),
    ("BUY_CURRENCY", "20260923-232843-964", (628, 485, 668, 525)),
    ("BUY_CONFIRM", "20260923-232843-964", (795, 495, 846, 519)),
    ("BUY_CANCEL", "20260923-232843-964", (440, 495, 490, 519)),
    ("LEAVE_CONFIRM_CHECK", "20260923-232913-867", (550, 301, 733, 343)),
    ("LOOT_CHECK", "20260922-082448-905", (594, 649, 698, 673)),
    ("LOOT_SELECTED", "20260922-082458-438", (839, 99, 848, 223), (190, 91, 1090, 232)),
    ("REWARD_CLOSE", "20260922-081544-299", (597, 685, 684, 703)),
    ("REWARD_CLOSE.2", "20260922-081507-280", (600, 629, 684, 651)),
    ("PREPARE_CHECK", "20260922-081423-319", (112, 135, 253, 163)),
    ("BATTLE_START", "20260922-081423-319", (1043, 649, 1133, 673)),
    ("MANUAL_TARGET", "20260922-081430-868", (915, 341, 948, 373), (700, 120, 1250, 605)),
    ("VICTORY_CHECK", "20260922-081517-145", (510, 25, 791, 85)),
    ("VICTORY_CONTINUE", "20260922-081517-145", (594, 649, 688, 673)),
    ("FAILED_CHECK", "20260923-233811-030", (501, 284, 788, 325)),
    ("SETTLEMENT_CHECK", "20260923-233818-836", (48, 512, 138, 542)),
    ("SETTLEMENT_EMPTY", "20260924-091819-235", (434, 607, 847, 635)),
    ("OCR_RESOURCES", "20260923-232839-814", (782, 13, 1234, 55)),
    ("OCR_HEROES", "20260922-081352-852", (345, 80, 1278, 600)),
    ("OCR_OPTIONS", "20260923-232331-766", (40, 546, 1210, 693)),
    ("CORE_ICON", "20260923-232839-814", (828, 18, 852, 47), (790, 10, 906, 54)),
    ("FRAGMENT_ICON", "20260923-232839-814", (893, 21, 917, 46), (866, 10, 950, 54)),
    ("ABANDON_CHECK", "20260924-091811-816", (514, 310, 771, 336)),
]

# The generator obtains regions directly from the non-black source bounds.
# Attribute images are exceptions, not a second copy of every source crop.
REGIONS = [
    ("OCR_ENTRY", "20260921-105748-548", (22, 90, 1248, 609)),
    ("OCR_PREVIEW_TITLE", "20260925-113034-953", (942, 94, 1190, 132)),
    ("OCR_LIFE", "20260923-232839-814", (1043, 17, 1092, 51)),
    ("OCR_QUOTA", "20260923-232839-814", (1160, 17, 1227, 51)),
    ("OCR_CORE", "20260923-232839-814", (852, 17, 891, 51)),
    ("OCR_FRAGMENT", "20260923-232839-814", (917, 17, 974, 51)),
    ("OCR_DICE", "20260923-232839-814", (800, 17, 825, 51)),
    ("OCR_EVENT_LOOT", "20260922-081528-636", (39, 195, 88, 224)),
    ("OCR_EVENT_STORY", "20260923-232331-766", (245, 400, 1192, 545)),
    ("OCR_EVENT_REWARD", "20260922-081544-299", (510, 288, 776, 326)),
    ("OCR_EVENT_OPTION", "20260923-232331-766", (75, 554, 441, 689)),
    ("EVENT_OPTION_CLICK", "20260923-232331-766", (120, 602, 400, 678)),
    ("EVENT_DETAIL_AREA", "20260923-232331-766", (75, 554, 120, 602)),
    ("OCR_HERO_TITLE", "20260922-081352-852", (69, 17, 345, 51)),
    ("OCR_SELECTED_HERO", "20260922-081352-852", (50, 78, 306, 112)),
    ("OCR_HERO_NAME", "20260922-081352-852", (394, 87, 622, 125)),
    ("OCR_HERO_COST", "20260922-081352-852", (601, 131, 638, 160)),
    ("HERO_COST_ACTIVE", "20260922-081352-852", (602, 133, 630, 159)),
    ("HERO_ROW_CLICK", "20260922-081352-852", (406, 93, 552, 154)),
    ("OCR_REST_HEROES", "20260923-232309-289", (390, 86, 965, 589)),
    ("OCR_INITIAL_RECRUITMENT", "20260922-081258-807", (35, 413, 1218, 604)),
    ("HERO_CONFIRM_ACTIVE", "20260922-081357-541", (1040, 646, 1136, 677)),
    ("REST_HERO_CONFIRM", "20260923-232312-127", (1030, 648, 1140, 677)),
    ("OCR_BUY_NAME", "20260923-232843-964", (640, 342, 955, 373)),
    ("OCR_BUY_PRICE", "20260923-232843-964", (676, 488, 740, 528)),
    ("OCR_BATTLE_REWARDS", "20260922-081517-145", (187, 470, 1100, 564)),
    ("OCR_RESUME_REWARDS", "20260925-113540-999", (380, 450, 900, 509)),
    ("VICTORY_CONTINUE_ACTIVE", "20260922-081517-145", (590, 647, 692, 675)),
    ("OCR_SETTLEMENT_SCORE", "20260923-233818-836", (170, 590, 472, 670)),
    ("SUPPLY_LOOT", "20260922-081730-252", (932, 399, 1061, 432)),
    ("SUPPLY_DONE_AREA", "20260922-081751-608", (1165, 265, 1220, 455)),
    ("NODE_TYPE_AREA", "20260922-081522-090", (488, 151, 576, 242)),
    ("NODE_CLICK", "20260922-081522-090", (514, 173, 550, 203)),
    ("CHAPTER_SELECT", "20260922-081145-848", (141, 170, 252, 261)),
    ("LEAVE_SHOP_CONFIRM", "20260923-232913-867", (692, 433, 810, 484)),
    ("CANCEL_ABANDON", "20260924-091811-816", (472, 442, 584, 481)),
    ("SETTLEMENT_CLOSE", "20260923-233818-836", (600, 683, 699, 710)),
]

# This counter is empty in the source screenshot, leaving only a tiny mark.
# Its OCR region must also accommodate the digits displayed in later frames.
AREA_OVERRIDES = {"OCR_EVENT_LOOT"}
for index in range(8):
    x, y = 394 + (index % 4) * 191, 93 + (index // 4) * 251
    for name, area in (
        ("OCR_SHOP_NAME", (x + 8, y + 14, x + 173, y + 55)),
        ("OCR_SHOP_PRICE", (x + 40, y + 193, x + 163, y + 232)),
        ("OCR_SHOP_NEW", (x, y, x + 178, y + 193)),
        ("SHOP_OFFER", (x + 13, y + 191, x + 162, y + 231)),
    ):
        REGIONS.append((f"{name}.{index + 1}", "20260923-232839-814", area))
for index in range(3):
    x = 195 + 320 * index
    for name, area in (
        ("OCR_LOOT_EFFECT", (x + 20, 254, x + 236, 407)),
        ("LOOT_CARD", (x + 65, 241, x + 200, 406)),
        # Selected card edges turn white/gold. Unselected purple borders have
        # much less green; this covers the real edge in both supplied styles.
        ("LOOT_BORDER", (x - 5, 105, x + 10, 215)),
    ):
        REGIONS.append((f"{name}.{index + 1}", "20260925-113419-624", area))
for index in range(4):
    y = 179 + 109 * index
    REGIONS.append((f"REST_ACTION.{index + 1}", "20260923-232305-766", (930, y, 1173, y + 32)))

CROPS.extend([
    ("RESUME_REWARDS_CHECK", "20260925-113540-999", (600, 638, 688, 661)),
    ("RESUME_REWARDS_CONTINUE", "20260925-113540-999", (600, 638, 688, 661)),
])


def extract(screenshots: Path, output: Path, extra_screenshots: Path | None = None, overwrite=False, only=None):
    # Validate the entire recipe before writing any files. Missing screenshots
    # must never leave a half-generated asset set that looks supported.
    sources = {}
    crops = [row for row in CROPS if not only or row[0] in only]
    regions = [row for row in REGIONS if not only or row[0] in only]
    if only and set(only) - {row[0] for row in crops + regions}:
        raise ValueError("Unknown crop name")
    for _, suffix, *_ in crops + regions:
        if suffix in sources:
            continue
        matches = list(screenshots.rglob(f"MuMu-{suffix}.png"))
        if not matches and extra_screenshots is not None:
            matches = list(extra_screenshots.glob(f"MuMu-{suffix}.png"))
        if len(matches) != 1:
            raise ValueError(f"Expected one source screenshot for {suffix}, found {len(matches)}")
        sources[suffix] = matches[0]
    for path in sources.values():
        with Image.open(path) as image:
            if image.size != (1280, 720):
                raise ValueError(f"Unexpected screenshot size: {path}")
    output.mkdir(parents=True, exist_ok=True)
    def save(canvas, name):
        path = output / f"{name}.png"
        # Once cropped, source PNGs are authoritative. Re-running the bootstrap
        # must preserve later adjustments made with the interactive crop tool.
        if overwrite or not path.exists():
            canvas.save(path)

    for name, suffix, area, *attributes in crops + regions:
        with Image.open(sources[suffix]) as source:
            canvas = Image.new("RGB", (1280, 720))
            canvas.paste(source.crop(area).convert("RGB"), area)
            save(canvas, name)
        for attr, rectangle in zip(("SEARCH", "BUTTON"), attributes):
            canvas = Image.new("RGB", (1280, 720))
            x1, y1, x2, y2 = rectangle
            ImageDraw.Draw(canvas).rectangle((x1, y1, x2 - 1, y2 - 1), fill="white")
            save(canvas, f"{name}.{attr}")
    for name, _, (x1, y1, x2, y2) in regions:
        if name not in AREA_OVERRIDES:
            continue
        canvas = Image.new("RGB", (1280, 720))
        ImageDraw.Draw(canvas).rectangle((x1, y1, x2 - 1, y2 - 1), fill="white")
        save(canvas, f"{name}.AREA")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshots", type=Path)
    parser.add_argument("--extra-screenshots", type=Path)
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace existing source crops")
    parser.add_argument("--only", nargs="+", help="Generate only the named source crops")
    args = parser.parse_args()
    extract(args.screenshots, Path("assets/global_cn/dimensional_exploration"),
            args.extra_screenshots, args.overwrite, args.only)
