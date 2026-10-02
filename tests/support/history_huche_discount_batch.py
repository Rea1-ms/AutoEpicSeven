# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")


"""Rotating Huche batches: screenshot boundaries and continuous scan coverage."""

from tasks.activity.huche_shop import DiscountItem, DiscountView, DiscountBatchScan

TOP = DiscountView(tuple(DiscountItem(y, name, False) for y, name in zip(
    (85, 230, 375, 520), ("圣约书签", "装备重铸精髓", "上级转换石选择箱", "5★英雄召唤券"))), None)
EDGE = DiscountView((DiscountItem(69, "5★英雄召唤券", False),
                     DiscountItem(214, "飞翔的灵药", False),
                     DiscountItem(360, "随机Lv.85特化饰品", False)), (634, 583, 713, 606))


def stable(scan, view):
    scan.observe(view)
    return scan.observe(view)


def at_top():
    scan = DiscountBatchScan()
    assert stable(scan, TOP) == "down"
    return scan

__all__ = ['stable']
