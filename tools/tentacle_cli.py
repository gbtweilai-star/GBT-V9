# tools/tentacle_cli.py —— 触手自理入口：原生视觉 / 专属邮箱 / 自助工具坞
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用法：
#   python tools/tentacle_cli.py vision --seconds 3 --fps 30        # 逐帧不丢采集（可复核账）
#   python tools/tentacle_cli.py vision --seconds 1 --grab-test      # 用真实屏幕采 1 秒
#   python tools/tentacle_cli.py mail plan                            # GBT-D1…GBT-D100 地址表
#   python tools/tentacle_cli.py mail register                        # 落登记表（含备注）
#   python tools/tentacle_cli.py mail remark --n 7 --text "负责吞噬归档"
#   python tools/tentacle_cli.py mail send --n 7 --to a@b.com --subject hi --body hello
#   python tools/tentacle_cli.py mail fetch --limit 10                 # 拉信并按触手分拣
#   python tools/tentacle_cli.py tools list                            # 白名单工具
#   python tools/tentacle_cli.py tools selfserve --n 7 --need "识别屏幕文字" [--grant TOKEN]
#   python tools/tentacle_cli.py tools run --n 7 --tool sqlite --code "print(1)" --grant TOKEN
#
# 纪律：邮箱与工具坞的凭据只从环境变量读；工具坞与真操作都必须带主人授权令牌。
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.mailbox_fleet import MailboxFleet          # noqa: E402
from core.tool_bay import CATALOG, ToolBay           # noqa: E402
from senses.frame_lock import FrameLock              # noqa: E402


def _ledger():
    try:
        from audit.ledger_factory import make_ledger
        return make_ledger()
    except Exception as exc:                          # noqa: BLE001
        print(f"[warn] 账本不可用（{type(exc).__name__}）：本次不落审计", file=sys.stderr)
        return None


def _show(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GBT小土豆V9 触手自理（视觉/邮箱/工具坞）")
    ap.add_argument("group", choices=["vision", "mail", "tools"])
    ap.add_argument("cmd", nargs="?", default="")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--fps", default="auto",
                    help="目标帧率；auto=先校准可达速率再定节奏（默认）")
    ap.add_argument("--grab-test", action="store_true", help="用真实屏幕采集（默认假源）")
    ap.add_argument("--dir", default="devoured/vision")
    ap.add_argument("--n", type=int, default=None, help="触手编号（GBT-D<n>）")
    ap.add_argument("--domain", default=None)
    ap.add_argument("--to", default="")
    ap.add_argument("--subject", default="")
    ap.add_argument("--body", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--tool", default="")
    ap.add_argument("--need", default="")
    ap.add_argument("--code", default="")
    ap.add_argument("--args", default="")
    ap.add_argument("--grant", default=None)
    args = ap.parse_args(argv)

    # ── 原生视觉：逐帧不丢 ──
    if args.group == "vision":
        # 默认走高速原始像素取帧（复用 mss 实例）；--grab-test 只影响 source 标注
        fps = args.fps if args.fps in ("auto", "") else float(args.fps)
        fl = FrameLock(args.dir, fps=fps,
                       source="screen" if args.grab_test else "player")
        rep = fl.watch(args.seconds)
        if args.grab_test:
            rep["devour"] = fl.merge_into_devour_index()
        _show(rep)
        return 0 if rep["verdict"] == "on_time" else 1

    # ── 专属永久邮箱 ──
    if args.group == "mail":
        f = MailboxFleet(ledger=_ledger() if args.cmd in ("register", "remark", "send",
                                                          "fetch") else None,
                         n=100, domain=args.domain)
        if args.cmd in ("", "plan"):
            st = f.status()
            _show({"status": st,
                   "addresses": [b.address for b in list(f.boxes.values())[:5]] +
                                [f.boxes[max(f.boxes)].address],
                   "remarks": {b.local: b.remark for b in f.boxes.values() if b.remark}})
            return 0 if st["domain"] else 1
        if args.cmd == "register":
            _show(f.register())
            return 0
        if args.cmd == "remark":
            if not args.n:
                print("需要 --n <编号>", file=sys.stderr)
                return 2
            _show(f.remark(args.n, args.text))
            return 0
        if args.cmd == "send":
            _show(f.send(args.n, args.to, args.subject, args.body))
            return 0
        if args.cmd == "fetch":
            _show(f.fetch(limit=args.limit))
            return 0

    # ── 自助工具坞 ──
    if args.group == "tools":
        bay = ToolBay(ledger=_ledger())
        if args.cmd in ("", "list"):
            _show({k: {"kind": v.kind, "target": v.target, "desc": v.desc, "risk": v.risk}
                   for k, v in CATALOG.items()})
            return 0
        if args.cmd == "selfserve":
            _show(bay.self_serve(f"t{(args.n or 1):03d}", args.need, grant=args.grant,
                                 args=args.args.split() if args.args else None,
                                 python_code=args.code or None))
            return 0
        if args.cmd == "install":
            _show(bay.install(f"t{(args.n or 1):03d}", args.tool, grant=args.grant))
            return 0
        if args.cmd == "run":
            _show(bay.run(f"t{(args.n or 1):03d}", args.tool,
                          args=args.args.split() if args.args else None,
                          grant=args.grant, python_code=args.code or None))
            return 0
        if args.cmd == "log":
            _show(bay.list(limit=args.limit))
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
