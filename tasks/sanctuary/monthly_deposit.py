"""Positive occupancy checks for the five monthly deposit slots."""

from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import (
    DEPOSIT_REWARD_TIER_A,
    DEPOSIT_REWARD_TIER_B,
    DEPOSIT_REWARD_TIER_S,
)


DEPOSIT_SLOT_COUNT = 5


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
