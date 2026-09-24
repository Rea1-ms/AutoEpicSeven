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


def extract(screenshots: Path, output: Path):
    # Validate the entire recipe before writing any files. Missing screenshots
    # must never leave a half-generated asset set that looks supported.
    sources = {suffix: screenshots / f"MuMu-{suffix}.png" for _, suffix, *_ in CROPS}
    for path in sources.values():
        with Image.open(path) as image:
            if image.size != (1280, 720):
                raise ValueError(f"Unexpected screenshot size: {path}")
    output.mkdir(parents=True, exist_ok=True)
    for name, suffix, area, *attributes in CROPS:
        with Image.open(sources[suffix]) as source:
            canvas = Image.new("RGB", (1280, 720))
            canvas.paste(source.crop(area).convert("RGB"), area)
            canvas.save(output / f"{name}.png")
        for attr, rectangle in zip(("SEARCH", "BUTTON"), attributes):
            canvas = Image.new("RGB", (1280, 720))
            x1, y1, x2, y2 = rectangle
            ImageDraw.Draw(canvas).rectangle((x1, y1, x2 - 1, y2 - 1), fill="white")
            canvas.save(output / f"{name}.{attr}.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshots", type=Path)
    args = parser.parse_args()
    extract(args.screenshots, Path("assets/global_cn/dimensional_exploration"))
