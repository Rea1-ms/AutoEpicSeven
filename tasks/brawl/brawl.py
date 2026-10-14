"""Inspect a saved brawl screenshot without constructing a game device."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from tasks.brawl.rules import BENCH_CAPACITY, summon_cost, summon_odds, upgrade_cost
from tasks.brawl.strategy import HeroState, recommend_lineup


SCENE_NAMES = {
    "preparation": "战斗准备",
    "level_up": "升级确认",
    "hero_result": "英雄获得结果",
    "hero_unlock": "英雄解锁说明",
    "rank_up": "局外总等级提升",
    "combat": "战斗中",
    "round_clear": "单轮胜利",
    "round_failed": "单轮失败",
    "formation_selection": "布阵选格",
    "run_victory": "整局冠军结算",
    "run_defeat": "整局失败结算",
    "transmit_selection": "传送英雄选择",
    "probability": "召唤概率说明",
    "give_up_confirm": "放弃挑战确认",
    "lobby": "大乱斗大厅",
    "unknown": "无法确认的画面",
}


def parse_roster(data, panel):
    """Require an explicit, current full roster; visible queue rows are incomplete."""
    required = {"complete", "round_number", "level", "heroes", "class_bonuses"}
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError("阵容资料必须包含完整标记、回合、等级、英雄和职业天赋，且不能有未知字段")
    if data["complete"] is not True:
        raise ValueError("必须明确提供场上与等待队列的完整英雄名单")
    for field in ("round_number", "level"):
        if type(data[field]) is not int or data[field] != getattr(panel, field):
            raise ValueError("阵容资料的回合和等级必须与当前截图一致")
    if not isinstance(data["heroes"], list) or not data["heroes"]:
        raise ValueError("完整英雄名单不能为空")
    if not isinstance(data["class_bonuses"], dict):
        raise ValueError("职业天赋必须明确填写；没有加成时填写空字典")
    states = []
    for hero in data["heroes"]:
        if not isinstance(hero, dict) or set(hero) != {"name", "imprint"}:
            raise ValueError("每名英雄必须同时填写名称和刻印，不能省略刻印或添加未知字段")
        if not isinstance(hero["name"], str) or not isinstance(hero["imprint"], str):
            raise ValueError("英雄名称和刻印必须为文字")
        states.append(HeroState(**hero))
    if len(states) < panel.deployed:
        raise ValueError("完整名单的英雄数少于截图中的上场人数")
    # Empty field slots do not increase bench capacity. Use the observed number
    # deployed now, not the level's maximum field size or a proposed lineup size.
    if len(states) > panel.deployed + BENCH_CAPACITY:
        raise ValueError("完整名单超出当前上场人数加10名候补的上限")
    return states, data["class_bonuses"]


def unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"阵容资料有重复字段：{name}")
        result[name] = value
    return result


def analyze(image, roster=None):
    """Return observations and optional manual-roster advice; never click or spend.

    The supplied roster is an explicit human observation, not screenshot-derived
    identity evidence. A new round can grant heroes even after a loss, so a roster
    from another round must not silently be reused. Same-round manual edits still
    require the caller to provide a fresh roster; this offline tool cannot prove
    that sprites or queue entries correspond to that list.
    """
    from module.config import server

    if server.lang != "global_cn":
        raise ValueError("当前截图分析仅支持国际服中文")

    from tasks.brawl.vision import BrawlScreen, BrawlVision, ObservationError

    observation = BrawlVision(image, lang="cn").observe()
    report = {
        "server": "global_cn",
        "mode": "offline_analysis",
        "observation": asdict(observation),
        "scene_description": SCENE_NAMES[observation.state.value],
        "run_complete": None,
        "run_result": None,
        "automatic_transition": observation.state in (BrawlScreen.ROUND_CLEAR, BrawlScreen.ROUND_FAILED),
        "lineup": None,
        "hero_result": None,
        "bench": None,
        "bench_cursor": None,
        "board": None,
        "swap_targets": None,
        "withdraw": None,
        "field": None,
        "field_traits": None,
        "transmit": None,
        "upgrade": None,
        "economy": None,
        "notes": [
            "仅分析已有截图，不连接设备或执行操作。",
            "仅明确的整局胜负结算能证明完成；单轮胜利、总等级提升和英雄解锁不能代替。",
            "候补只报告当前可读条目；场上身份与队列两端未核实，阵容建议仍需完整名单。",
        ],
    }
    if observation.state in (BrawlScreen.RUN_VICTORY, BrawlScreen.RUN_DEFEAT):
        report["run_complete"] = True
        report["run_result"] = "victory" if observation.state == BrawlScreen.RUN_VICTORY else "defeat"
    if report["automatic_transition"]:
        report["notes"].append("单轮胜负自动过渡，无需确认；不能按闪现次数或回合号推断战斗次数。")
    if observation.state == BrawlScreen.HERO_RESULT:
        from tasks.brawl.heroes import read_hero_result

        hero = read_hero_result(image)
        if hero is not None:
            report["hero_result"] = asdict(hero)
            report["hero_result"]["imprint"] = hero.imprint.name
            report["hero_result"]["previous_imprint"] = (
                None if hero.previous_imprint is None else hero.previous_imprint.name)
        report["notes"].append("英雄结果仅表示本页单名英雄，不代表完整阵容，也不用于跨帧累计获得次数。")
    if observation.state == BrawlScreen.LEVEL_UP:
        from tasks.brawl.upgrade import read_upgrade_dialog

        try:
            upgrade = read_upgrade_dialog(image)
        except ObservationError as exc:
            report["notes"].append(f"升级报价暂时无法完整读取：{exc}")
        else:
            report["upgrade"] = asdict(upgrade)
            report["upgrade"]["affordable"] = upgrade.header.coins >= upgrade.cost
            report["upgrade"]["confirmed"] = False
        report["notes"].append("升级页只报告当前等级、人数上限和报价；需返回准备页核对新等级与实际扣款。")
    if observation.state in (BrawlScreen.PREPARATION, BrawlScreen.FORMATION_SELECTION,
                             BrawlScreen.TRANSMIT_SELECTION):
        from tasks.brawl.bench import read_bench

        bench = read_bench(image, observation=observation)
        report["bench"] = asdict(bench)
        for entry in report["bench"]["entries"]:
            entry["imprint"] = None if entry["imprint"] is None else entry["imprint"].name
        report["notes"].append("候补刻印读不清时保留未知；当前条目和加号位置不能沿用于滚动后的画面。")
    if observation.state == BrawlScreen.PREPARATION and observation.panel is not None:
        from tasks.brawl.cursor import read_cursor
        from tasks.brawl.field import read_field
        from tasks.brawl.traits import read_field_traits

        field = read_field(image, observation=observation)
        report["field"] = asdict(field)
        report["field"]["complete"] = field.complete
        traits = read_field_traits(image, observation=observation)
        report["field_traits"] = asdict(traits)
        for header, row in zip(traits.headers, report["field_traits"]["headers"]):
            row["name"] = header.name
        report["notes"].append("头顶阵营与职业共同确定候选，同组合英雄保持歧义；头标位置不代替脚下格位，刻印未知。")
        report["notes"].append("五名英雄的已验证外观仍用于动作位置确认；未知身份可换到候补后比对完整名单，不能用自动焦点代替。")
        cursor = read_cursor(image, observation=observation)
        if cursor is not None:
            report["bench_cursor"] = {
                "selected": None if cursor.selected is None else {
                    "name": cursor.selected.name,
                    "imprint": None if cursor.selected.imprint is None else cursor.selected.imprint.name,
                },
                "top": cursor.top,
                "bottom": cursor.bottom,
                "empty": cursor.empty,
                "next_area": cursor.next_area,
            }
        report["notes"].append("单张候补端点图不能证明已读完整；需要从顶部逐名确认到末尾。")
    if observation.state == BrawlScreen.FORMATION_SELECTION:
        from tasks.brawl.board import read_board, read_swap_targets
        from tasks.brawl.withdraw import read_withdraw_control

        board = read_board(image, observation=observation)
        if board is not None:
            report["board"] = asdict(board)
        targets = read_swap_targets(image, observation=observation)
        if targets is not None:
            report["swap_targets"] = asdict(targets)
        withdraw = read_withdraw_control(image, observation=observation)
        if withdraw is not None:
            report["withdraw"] = asdict(withdraw)
            report["withdraw"]["confirmed"] = False
            report["withdraw"]["bench_space_verified"] = False
            report["notes"].append("红色减号将选中人物移回候补；操作前须完整扫描确认空位，本图不证明下场完成。")
        report["notes"].append("蓝色双箭头表示已有英雄的换人目标；只有绿色选中标记才确定移动来源，不据此猜人物身份。")
    if observation.state == BrawlScreen.TRANSMIT_SELECTION:
        from tasks.brawl.transmit import read_transmit_panel

        try:
            transmit = read_transmit_panel(image)
        except ObservationError as exc:
            report["notes"].append(f"传送栏暂时无法完整读取：{exc}")
        else:
            report["transmit"] = {
                "header": asdict(transmit.header),
                "quote": asdict(transmit.quote),
                "selected": transmit.selected,
                "confirmed": False,
            }
        report["notes"].append("英雄移入左侧选择栏时便会从候补消失；本页报价不代表传送已完成。")
    panel = observation.panel
    if observation.state != BrawlScreen.PREPARATION or panel is None:
        if roster is not None:
            raise ValueError("仅能为资源和人数均确认的准备画面生成阵容建议")
        return report

    # Affordability is a resource fact, not authority to repeat summons or sell
    # heroes. In particular, maximum-imprint duplicates can refund both currencies;
    # neither an expected debit nor a result animation proves a complete roster.
    cost = upgrade_cost(panel.level) if panel.level < 9 else None
    report["economy"] = {
        "upgrade_cost": cost,
        "can_afford_upgrade": cost is not None and panel.coins >= cost,
        "summons": {},
    }
    for kind in ("normal", "lower", "upper"):
        price = summon_cost(kind)
        report["economy"]["summons"][kind] = {
            "cost": asdict(price),
            "affordable": panel.coins >= price.coins and panel.synergy_tokens >= price.tokens,
            "odds": dict(summon_odds(kind, panel.level)) if kind != "normal" or panel.level >= 8 else None,
        }
    if panel.level < 8:
        report["notes"].append("当前等级的一般召唤概率尚未提供，不推算期望收益。")
    if roster is not None:
        states, bonuses = parse_roster(roster, panel)
        report["lineup"] = asdict(recommend_lineup(states, panel.level, class_bonuses=bonuses))
        for group in ("team", "bench"):
            for hero in report["lineup"][group]:
                hero["imprint"] = hero["imprint"].name
        report["notes"].append("阵容评分属于启发式建议；格子为前排起算的逻辑位置，尚未映射屏幕坐标。")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线分析国际服中文大乱斗截图，不连接设备")
    parser.add_argument("--image", type=Path, required=True, help="已有的1280×720截图")
    parser.add_argument("--roster", type=Path, help="可选的完整阵容资料")
    parser.add_argument("--out", type=Path, help="可选的新报告文件，已有文件不会覆盖")
    args = parser.parse_args(argv)
    try:
        from module.base.utils import load_image
        from module.config import server

        server.set_lang("global_cn")
        roster = None if args.roster is None else json.loads(
            args.roster.read_text(encoding="utf-8"), object_pairs_hook=unique_fields,
        )
        report = analyze(load_image(args.image), roster)
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.out is None:
            print(output, end="")
        else:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8", newline="\n") as target:
                target.write(output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"无法分析：{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
