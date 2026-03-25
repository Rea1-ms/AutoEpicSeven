from module.base.button import ButtonWrapper
from tasks.store.assets.assets_store_items import ARTIFACT_ENHANCEMENT_STONE, MOBILITY_40
from tasks.store.purchase import (
    ItemPurchasePlan,
    PurchaseCounterPreset,
    normalize_config_purchase_quantity,
)


def build_friendship_store_items(
    config,
    *,
    buy_counter_area: tuple[int, int, int, int],
    remaining_buy_times_area: tuple[int, int, int, int],
    arena_flag_asset: ButtonWrapper,
) -> tuple[ItemPurchasePlan, ...]:
    """
    Build purchase plans for the Friendship Points store.

    The weekly artifact enhancement stone shares the same store page and
    purchase popup flow as the daily friendship items, so it stays in the same
    sub-store plan and reuses the shared weekly remaining-times OCR.
    """
    return (
        ItemPurchasePlan(
            name='friendship_mobility_40',
            asset=MOBILITY_40,
            desired_quantity=1 if config.StoreDaily_BuyFriendshipMobility40 else 0,
            quantity_strategy='once',
            counter_preset=PurchaseCounterPreset(
                name='StoreFriendshipMobility40Counter',
                area=buy_counter_area,
            ),
        ),
        ItemPurchasePlan(
            name='friendship_artifact_enhancement_stone',
            asset=ARTIFACT_ENHANCEMENT_STONE,
            desired_quantity=normalize_config_purchase_quantity(
                getattr(config, 'StoreWeekly_BuyFriendshipArtifactEnhancementStone', 0),
                maximum=3,
            ),
            quantity_strategy='target',
            counter_preset=PurchaseCounterPreset(
                name='StoreFriendshipArtifactEnhancementStoneCounter',
                area=buy_counter_area,
            ),
            purchase_limit=3,
            remaining_counter_preset=PurchaseCounterPreset(
                name='StoreFriendshipArtifactEnhancementStoneRemainingTimes',
                area=remaining_buy_times_area,
            ),
        ),
        ItemPurchasePlan(
            name='friendship_arena_flag',
            asset=arena_flag_asset,
            desired_quantity=1 if config.StoreDaily_BuyFriendshipArenaFlag else 0,
            quantity_strategy='once',
            counter_preset=PurchaseCounterPreset(
                name='StoreFriendshipArenaFlagCounter',
                area=buy_counter_area,
            ),
        ),
    )
