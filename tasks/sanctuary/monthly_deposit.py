"""Positive occupancy checks for the five monthly deposit slots."""

from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import (
    DEPOSIT_REWARD_TIER_A,
    DEPOSIT_REWARD_TIER_B,
    DEPOSIT_REWARD_TIER_S,
)


DEPOSIT_SLOT_COUNT = 5


def deposit_tiers_increased(before: tuple[str | None, ...], after: tuple[str | None, ...]) -> bool:
    """Confirm one new known reward without losing or replacing existing slots."""
    if len(before) != DEPOSIT_SLOT_COUNT or len(after) != DEPOSIT_SLOT_COUNT:
        return False
    known_tiers = ('S', 'A', 'B')
    before_count = sum(tier in known_tiers for tier in before)
    after_count = sum(tier in known_tiers for tier in after)
    # Unknown slots are not assumed empty. This is only a post-click change
    # check, after the ordinary free-slot marker authorized custody. Preserve
    # every previously recognized slot so missing/reclassified old rewards do
    # not combine with an unrelated match to create a false storage receipt.
    return after_count == before_count + 1 and all(
        previous is None or previous == current for previous, current in zip(before, after)
    )


def match_deposit_tiers(image) -> tuple[str | None, ...]:
    """Recognize one known tier per physical slot, not a raw template hit count."""
    left, _, right, _ = DEPOSIT_REWARD_TIER_S.search
    slot_width = (right - left) / DEPOSIT_SLOT_COUNT
    slots = [set() for _ in range(DEPOSIT_SLOT_COUNT)]
    for tier, asset in (
        ("S", DEPOSIT_REWARD_TIER_S),
        ("A", DEPOSIT_REWARD_TIER_A),
        ("B", DEPOSIT_REWARD_TIER_B),
    ):
        for match in asset.match_multi_template(image, similarity=0.85, threshold=20):
            center_x = (match.area[0] + match.area[2]) / 2
            slot = int((center_x - left) // slot_width)
            if not 0 <= slot < DEPOSIT_SLOT_COUNT:
                continue
            expected_center = left + (slot + 0.5) * slot_width
            if abs(center_x - expected_center) <= slot_width / 4:
                slots[slot].add(tier)

    # All five different slots must have positive evidence in this screenshot.
    # Repeated matches in one slot never fill another slot. Wide templates
    # include the space beside the letter so an uncollected SS/SSS label is not
    # intentionally treated as several S labels. Unknown or conflicting tiers
    # remain unknown; absence of a known tier never proves a free slot.
    return tuple(next(iter(tiers)) if len(tiers) == 1 else None for tiers in slots)
