"""Render the same facts consumed by tasks, without importing any task code."""

import re

from module.game_info.catalog import GameInfo


def render_timeline(info: GameInfo) -> str:
    lines = [
        "时间表由共享游戏资料生成，时间均为北京时间。国服自动顺延会单独标注；手动推算的依据见共享资料的来源说明。",
        "",
        "```mermaid",
        "gantt",
        "    title 游戏活动与赛季时间表",
        "    dateFormat YYYY-MM-DD HH:mm",
        "    axisFormat %m/%d",
    ]
    for family, label in (("OVERSEA", "国际服"), ("CN", "国服")):
        periods = [p for p in info.periods if p.server_family == family]
        if not periods:
            continue
        lines.append(f"    section {label}")
        for period in sorted(periods, key=lambda p: (p.start, p.kind)):
            name = re.sub(r"[^\w ()/-]", " ", period.name)
            if period.inferred:
                name += "（推算）"
            lines.append(f"    {name} :{family}_{period.event_id}, "
                         f"{period.start:%Y-%m-%d %H:%M}, {period.end:%Y-%m-%d %H:%M}")
    lines.extend(["```", "", "| 项目 | 服务器 | 开始 | 结束 | 日期依据 |",
                  "| --- | --- | --- | --- | --- |"])
    for period in info.periods:
        family = "国服" if period.server_family == "CN" else "国际服"
        name = period.name.replace("|", "&#124;")
        basis = "按类别顺延推算" if period.inferred else "已填写；依据见资料来源"
        lines.append(f"| {name} | {family} | {period.start:%Y-%m-%d %H:%M} | "
                     f"{period.end:%Y-%m-%d %H:%M} | {basis} |")
    lines.append("\n尚未填写日期的类别只提供默认规则，不显示为已确认赛季。")
    return "\n".join(lines) + "\n"
