"""Inspect and validate local game facts without starting the game or scheduler."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from module.game_info.catalog import INFO_TIMEZONE, SERVER_FAMILIES, load_info
from module.game_info.timeline import render_timeline


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="校验或查看共享游戏资料")
    parser.add_argument("command", choices=("validate", "current", "timeline"))
    parser.add_argument("--path", type=Path, help="指定待校验资料文件")
    parser.add_argument("--server", choices=SERVER_FAMILIES, default="CN")
    parser.add_argument("--at", help="查询时刻，必须包含时区")
    args = parser.parse_args()
    try:
        info = load_info(args.path)
        now = datetime.fromisoformat(args.at) if args.at else datetime.now(INFO_TIMEZONE)
        if now.tzinfo is None:
            raise ValueError("--at must include a timezone")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.command == "validate":
        print(f"资料校验通过：{len(info.periods)}个服务器时段，{len(info.defaults)}类默认规则。")
    elif args.command == "timeline":
        print(render_timeline(info), end="")
    else:
        kinds = sorted(set(info.defaults) | {p.kind for p in info.periods})
        current = [info.current(kind, args.server, now) for kind in kinds]
        output = {
            "server": args.server,
            "at": now.isoformat(),
            "active": [{"id": p.event_id, "kind": p.kind, "name": p.name,
                        "start": p.start.isoformat(), "end": p.end.isoformat(),
                        "source": p.source, "inferred": p.inferred}
                       for p in current if p is not None],
            "values": {kind: dict(info.values(kind, args.server, now)) for kind in kinds},
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
