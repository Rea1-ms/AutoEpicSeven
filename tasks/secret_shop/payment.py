"""Evidence for one serialized secret-shop payment, independent of screenshots."""
from dataclasses import dataclass


ITEM_GOLD_COST = {'covenant': 184000, 'mystic': 280000}
REFRESH_SKYSTONE_COST = 3


@dataclass
class ShopPayment:
    kind: str
    before: tuple[int | None, int | None]
    submitted: bool = False
    confirmations: int = 0
    last_balance: tuple[int, int] | None = None

    @property
    def expected(self) -> tuple[int | None, int | None]:
        gold, skystone = self.before
        if self.kind == 'refresh':
            return gold, skystone - REFRESH_SKYSTONE_COST if skystone is not None else None
        return gold - ITEM_GOLD_COST[self.kind] if gold is not None else None, skystone

    def observe(self, balance: tuple[int, int] | None) -> bool:
        # Retry visible controls within this payment without replacing its
        # baseline. Clicks, a missing dialog and unchanged balances are not
        # settlement evidence; stuck recovery belongs to the scheduler.
        # Production reads only the currency spent by this transaction, after
        # its small image region settles. A fully specified pair remains useful
        # for offline evidence checks and must match both values exactly.
        self.last_balance = balance
        expected = self.expected
        spent_index = 1 if self.kind == 'refresh' else 0
        if self.submitted and expected[spent_index] is not None and balance is not None and all(
                expected is None or actual == expected for actual, expected in zip(balance, self.expected)):
            self.confirmations += 1
        else:
            self.confirmations = 0
        return self.confirmations >= 1
