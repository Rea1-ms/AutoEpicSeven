import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class SanctuaryMonthly(a.GroupBase):
    RewardTier: t.Literal['A', 'B', 'S', 'MaxMinus1', 'MaxMinus2'] = 'A'
