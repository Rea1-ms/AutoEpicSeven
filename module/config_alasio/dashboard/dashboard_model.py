import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class Gold(a.DashboardAmount):
    pass


class Skystone(a.DashboardAmount):
    pass


class Stamina(a.DashboardDynamicTotal):
    pass


class EquipmentInventory(a.DashboardDynamicTotal):
    pass


class DailyActivity(a.DashboardDynamicTotal):
    Total: int = 100


class ArenaRank(a.DashboardTotal):
    Value: e.Annotated[int, m.Meta(ge=0, le=38)] = 0


class ArenaFlag(a.DashboardDynamicTotal):
    pass


class ConquestPoint(a.DashboardAmount):
    pass


class ShadowCommission(a.DashboardTotal):
    Value: e.Annotated[int, m.Meta(ge=0, le=30)] = 0


class TeamBattle(a.DashboardText):
    pass
