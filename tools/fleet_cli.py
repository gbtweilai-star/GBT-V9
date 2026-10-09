# tools/fleet_cli.py —— 触手编队命令行：查看状态 / 由指挥官驱动触手
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用法：
#   python tools/fleet_cli.py status                    # 编队规模 / 统一密钥指纹 / 用量
#   python tools/fleet_cli.py drive t001 "扫描 src 目录并返回 JSON"
#   python tools/fleet_cli.py drive-many scan "检查第 {i} 号分片" --limit 8
#   python tools/fleet_cli.py log 10                    # 最近 10 条驱动审计
#
# 纪律：密钥只从环境变量读（OPENAI_API_KEY / GBT_LLM_API_KEY，与主脑同一把）；
#       输出里只有指纹 key_id，永不回显密钥原文。
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.ledger_factory import make_ledger          # noqa: E402
from core.tentacle_fleet import TentacleFleet         # noqa: E402


def _ledger():
    try:
        return make_ledger()
    except Exception as exc:                          # noqa: BLE001
        print(f"[warn] 账本不可用（{type(exc).__name__}），本次不落审计行", file=sys.stderr)
        return None


def _show(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GBT小土豆V9 触手编队（统一密钥 + 指挥闸门）")
    ap.add_argument("cmd", choices=["status", "drive", "drive-many", "log"])
    ap.add_argument("arg", nargs="?", help="触手 id（drive）/ 任务模板（drive-many）")
    ap.add_argument("task", nargs="?", help="任务文本（drive）")
    ap.add_argument("--n", type=int, default=None, help="编队规模（默认 100）")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--allow-actions", action="store_true",
                    help="保留参数：触手编队只做 LLM 推理，桌面动作请走 core/gui_agent")
    args = ap.parse_args(argv)

    fleet = TentacleFleet(ledger=_ledger(), n=args.n)

    if args.cmd == "status":
        _show({**fleet.status(), "roles": {
            r: sum(1 for t in fleet.tentacles.values() if t.role == r)
            for r in sorted({t.role for t in fleet.tentacles.values()})}})
        return 0

    if args.cmd == "drive":
        if not args.arg or not args.task:
            print("用法：drive <tentacle_id> \"<任务>\"", file=sys.stderr)
            return 2
        _show(fleet.drive(args.arg, args.task))
        return 0

    if args.cmd == "drive-many":
        tpl = args.task or args.arg or "执行分片任务并返回 JSON"
        out = fleet.drive_many(lambda tid: tpl.replace("{i}", tid), limit=args.limit)
        _show({k: v for k, v in out.items() if k != "results"})
        for r in out["results"]:
            print(" ", r.get("tentacle"), "→", "ok" if r.get("ok") else r.get("reason"))
        return 0 if out["failed"] == 0 else 1

    _show(fleet.table(args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
