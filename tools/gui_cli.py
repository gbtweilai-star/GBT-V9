# tools/gui_cli.py —— 桌面操控命令行：看屏幕 / 出计划 / 授权后真操作
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用法：
#   python tools/gui_cli.py look                       # 纯视觉看屏：元素清单（不碰无障碍树）
#   python tools/gui_cli.py plan "打开记事本写一句话"     # 只规划（dry-run，不碰鼠标键盘）
#   python tools/gui_cli.py grant --ttl 600             # 主人签发授权令牌（打印 token）
#   python tools/gui_cli.py run "打开记事本写一句话" --grant <token>   # 带授权真执行
#   python tools/gui_cli.py run "..." --grant-file .gui_grant.json
#
# 纪律：无授权时高风险动作会在执行前停下（needs_confirm）；授权是唯一闸门；
#       截图永不出设备（GUI_LOCAL_ONLY=1），规划走统一密钥网关（与主脑同一把）。
from __future__ import annotations
from core.swallow import swallow as _swallow

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gui_grant import issue as issue_grant          # noqa: E402
from core.gui_perception import perceive                 # noqa: E402


def _show(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GBT小土豆V9 桌面操控（纯视觉 + 授权）")
    ap.add_argument("cmd", choices=["look", "plan", "run", "grant"])
    ap.add_argument("task", nargs="?", help="任务文本")
    ap.add_argument("--grant", help="授权令牌字符串")
    ap.add_argument("--grant-file", help="从文件读授权令牌")
    ap.add_argument("--ttl", type=int, default=1800, help="授权有效期（秒）")
    ap.add_argument("--scope", default="*", help="授权原语（逗号分隔，* = 全部）")
    ap.add_argument("--note", default="主人授权", help="授权备注（记入审计）")
    ap.add_argument("--steps", type=int, default=None, help="最大步数")
    ap.add_argument("--max-elements", type=int, default=60)
    ap.add_argument("--vision-only", action="store_true", default=None,
                    help="纯视觉：不读无障碍树")
    ap.add_argument("--no-vision-only", dest="vision_only", action="store_false")
    args = ap.parse_args(argv)

    if args.cmd == "look":
        out = perceive(max_elements=args.max_elements, want_marks=False,
                       vision_only=True if args.vision_only is None else args.vision_only)
        out.pop("marks_png", None)
        _show(out)
        return 0

    if args.cmd == "grant":
        tok = issue_grant(scope=args.scope.split(","), ttl=args.ttl, note=args.note)
        pri = {"scope": args.scope, "ttl": args.ttl, "note": args.note}
        _show({"token": tok, "hint": "把它交给 run --grant；过期即失效", **pri})
        return 0

    if not args.task:
        print("缺少任务文本", file=sys.stderr)
        return 2
    grant = args.grant
    if not grant and args.grant_file:
        grant = Path(args.grant_file).read_text(encoding="utf-8").strip()

    from core.gui_agent import GuiAgent
    ledger = None
    try:
        from audit.ledger_factory import make_ledger
        ledger = make_ledger()
    except Exception:                                     # noqa: BLE001
        print("[warn] 账本不可用：本次不落轨迹行", file=sys.stderr)
    try:
        act = None
        if args.cmd == "run":
            from core.actuator import Actuator
            act = Actuator(ledger=ledger, brain=None,
                           auto_confirm=os.environ.get("AUTO_CONFIRM") == "1")
        ag = GuiAgent(ledger=ledger, actuator=act,
                      dry_run=(args.cmd == "plan"), max_steps=args.steps)
        res = ag.run(args.task, grant=grant, max_steps=args.steps)
        _show(res)
        return 0 if res.get("ok") else 1
    finally:
        if ledger is not None:
            try:
                ledger.close()
            except Exception as e:
                _swallow(__file__, e)


if __name__ == "__main__":
    raise SystemExit(main())
