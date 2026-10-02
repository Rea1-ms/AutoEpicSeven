"""Evidence for one serialized secret-shop payment, independent of screenshots."""
from dataclasses import dataclass


ITEM_GOLD_COST = {'covenant': 184000, 'mystic': 280000}
REFRESH_SKYSTONE_COST = 3


@dataclass
class ShopPayment:
    kind: str
    before: tuple[int, int]
    submitted: bool = False
    confirmations: int = 0
    last_balance: tuple[int, int] | None = None

    @property
    def expected(self) -> tuple[int, int]:
        gold, skystone = self.before
        if self.kind == 'refresh':
            return gold, skystone - REFRESH_SKYSTONE_COST
        return gold - ITEM_GOLD_COST[self.kind], skystone

    def observe(self, balance: tuple[int, int] | None) -> bool:
        # Retry visible controls within this payment without replacing its
        # baseline. Clicks, a missing dialog and unchanged balances are not
        # settlement evidence; stuck recovery belongs to the scheduler.
        # Require exactly the expected debit and an unchanged other currency
        # on two consecutive usable frames. An unreadable/occluded/intervening
        # frame resets the evidence rather than combining unrelated readings.
        self.last_balance = balance
        if self.submitted and balance == self.expected:
            self.confirmations += 1
        else:
            self.confirmations = 0
        return self.confirmations >= 2
