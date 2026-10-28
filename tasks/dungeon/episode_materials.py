"""Material destinations confirmed by the user's adventure guide."""

import re

EPISODE_STAMINA_COST = {1: 8, 2: 12, 3: 16, 4: 20, 5: 20}


# Columns are Episodes 1 through 5. None means no destination was supplied,
# not that the material cannot drop anywhere in that episode. Keep special
# chapter suffixes: 8 and 8S (likewise 10 and 10S) are different maps.
CATALYST_STAGES = {
    "CATALYST_EPIC_FIGHTING_SPIRIT": (None, (6, 7), (10, 7), (8, 7), (7, 6)),
    "CATALYST_EPIC_OATH": (None, (5, 12), (8, 6), (6, 8), ("8S", 7)),
    "CATALYST_EPIC_SECRET": (None, (9, 2), (7, 2), (10, 6), ("10S", 4)),
    "CATALYST_EPIC_SNIPER": (None, (10, 3), (8, 7), (7, 10), (10, 5)),
    "CATALYST_EPIC_MYSTERIOUS": (None, (7, 10), (7, 10), (9, 10), (9, 6)),
    "CATALYST_EPIC_BENEVOLENT": (None, (8, 10), (9, 10), (9, 7), (8, 7)),
    "CATALYST_RARE_FIGHTING_SPIRIT": ((1, 5), (3, 10), (6, 9), (3, 10), (6, 7)),
    "CATALYST_RARE_OATH": ((2, 5), (2, 9), (2, 8), (5, 5), (1, 7)),
    "CATALYST_RARE_SECRET": ((3, 5), (5, 6), (4, 7), (2, 10), (2, 8)),
    "CATALYST_RARE_SNIPER": ((4, 4), (1, 10), (3, 10), (4, 8), (5, 7)),
    "CATALYST_RARE_MYSTERIOUS": ((5, 9), (4, 5), (1, 8), (5, 10), (3, 6)),
    "CATALYST_RARE_BENEVOLENT": ((6, 6), (4, 10), (5, 10), (1, 9), (4, 7)),
}

EPISODE_MATERIAL_STAGES = {
    episode: {
        key: stages[episode - 1]
        for key, stages in CATALYST_STAGES.items()
        if stages[episode - 1] is not None
    }
    for episode in range(1, 6)
}
EPISODE_MATERIAL_STAGES[4].update({
    "HEART_OF_THE_WOODS": (4, 10),
    "BREATH_OF_KARMA": (6, 10),
    "FROZEN_SEED": (8, 9),
    "TRACES_OF_BRILLIANCE": (10, 9),
})


def parse_material_chapter(text: str) -> int | str:
    """Read a chapter label without turning a special chapter into its parent."""
    text = text.strip().upper()
    matched = re.search(r"(?<![\dA-Z])(?:第\s*)?(\d{1,2})\s*(S?)\s*章", text)
    if matched is None:
        matched = re.search(r"\bCHAPTER\s*(\d{1,2})\s*(S?)(?!\w)", text)
    if matched is None:
        matched = re.fullmatch(r"(\d{1,2})\s*(S?)", text)
    if matched is None:
        return 0
    number, suffix = matched.groups()
    if not 1 <= int(number) <= 10:
        return 0
    return f"{int(number)}S" if suffix else int(number)


def material_chapter_order(chapter: int | str) -> tuple[int, bool]:
    """Special chapters follow their numbered chapter in the arrow sequence."""
    label = str(chapter)
    return int(label.removesuffix("S")), label.endswith("S")
