"""Render the same facts consumed by tasks, without importing any task code."""

import re
from datetime import datetime

from module.game_info.catalog import GameInfo, aware_time


def render_timeline(info: GameInfo, at: datetime | None = None) -> str:
    at = aware_time(at)
    current = tuple(p for p in info.periods if p.end > at)
    expired = tuple(p for p in info.periods if p.end <= at)
    lines = [
        "时间表由共享游戏资料生成，时间均为北京时间。两服共用日期按服展示，过渡期差异单独保留；手动推算的依据见共享资料的来源说明。",
        "正式开放时刻未知的活动，以首次确认开放时刻作为资料起点，详见来源。旧资料的国服自动顺延仍会单独标注。",
        "",
        f"更新时点：{at:%Y-%m-%d %H:%M}。主图只显示尚未结束的已知时段；已结束时段保存在下方历史记录。",
        "",
    ]
    if current:
        lines.extend(_chart(current))
        lines.extend(["", *_table(current, at)])
    else:
        lines.append("当前没有已填写且尚未结束的时段。")
    unknown = []
    for family, label in (("OVERSEA", "国际服"), ("CN", "国服")):
        for kind, name in (("arena_pass", "竞技场通行证"), ("shadow_commission", "暗影情报委托")):
            if kind in info.defaults and info.current(kind, family, at) is None:
                unknown.append(f"{label}{name}")
    if unknown:
        lines.extend(["", f"{'、'.join(unknown)}：当前期日期尚未确认，未自动续期；任务沿用默认规则。"])
    if expired:
        lines.extend(["", "<details>", "<summary>已结束活动与赛季（历史记录）</summary>", "",
                      *_table(expired, at), "", "</details>"])
    lines.append("\n尚未填写日期的类别只提供默认规则，不显示为已确认赛季。")
    return "\n".join(lines) + "\n"


def _chart(periods) -> list[str]:
    lines = [
        "```mermaid",
        "gantt",
        "    title 游戏活动与赛季时间表",
        "    dateFormat YYYY-MM-DD HH:mm",
        "    axisFormat %m/%d",
    ]
    for family, label in (("OVERSEA", "国际服"), ("CN", "国服")):
        family_periods = [p for p in periods if p.server_family == family]
        if not family_periods:
            continue
        lines.append(f"    section {label}")
        for period in sorted(family_periods, key=lambda p: (p.start, p.kind)):
            name = re.sub(r"[^\w ()/-]", " ", period.name)
            if period.inferred:
                name += "（推算）"
            lines.append(f"    {name} :{family}_{period.event_id}, "
                         f"{period.start:%Y-%m-%d %H:%M}, {period.end:%Y-%m-%d %H:%M}")
    lines.append("```")
    return lines


def _table(periods, at: datetime) -> list[str]:
    lines = ["| 项目 | 服务器 | 起点 | 结束 | 状态 | 日期依据 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for period in periods:
        family = "国服" if period.server_family == "CN" else "国际服"
        name = period.name.replace("|", "&#124;")
        basis = "按类别顺延推算" if period.inferred else "已填写；依据见资料来源"
        status = "已结束" if period.end <= at else "尚未开始" if period.start > at else "进行中"
        lines.append(f"| {name} | {family} | {period.start:%Y-%m-%d %H:%M} | "
                     f"{period.end:%Y-%m-%d %H:%M} | {status} | {basis} |")
    return lines
