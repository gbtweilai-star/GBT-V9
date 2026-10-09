# tools/sandbox_cli.py —— 无头驱动动手能力（对应 MuseWork 的 `muse serve`：能被脚本/服务调）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用法：
#   python -m tools.sandbox_cli status   --target <目录>
#   python -m tools.sandbox_cli plan     --target <目录> --cmd "python -c print(1+1)"   # 只读干跑
#   python -m tools.sandbox_cli run      --target <目录> --cmd "..." [--readonly|--direct]
#   python -m tools.sandbox_cli apply    --target <目录> --file app.py --text "新内容"     # 写副本
#   python -m tools.sandbox_cli diff     --target <目录>
#   python -m tools.sandbox_cli rollback --target <目录>
#   echo '{"target":"...","steps":[...],"mode":"cow"}' | python -m tools.sandbox_cli loop
import argparse
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.sandbox_exec import (CowWorkspace, SandboxPolicy, run_loop,  # noqa: E402
                              run_plan)


def _show(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def _argv(text: str) -> list:
    """命令串 → 参数列表（无 shell 解析；元字符交给引擎拒）。"""
    return shlex.split(text, posix=(os.name != "nt"))


def _policy(args) -> SandboxPolicy:
    mode = "readonly" if args.readonly else ("direct" if args.direct else "cow")
    return SandboxPolicy(mode=mode)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GBT小土豆V9 动手能力（受限执行 + 写时复制 + 回滚）")
    ap.add_argument("action", choices=["plan", "run", "apply", "diff", "rollback",
                                       "status", "loop"])
    ap.add_argument("--target", help="目标目录")
    ap.add_argument("--root", default=None, help="影子根（默认 ~/.v9-sandbox）")
    ap.add_argument("--cmd", default="", help="要执行的命令（无 shell；含元字符会被拒）")
    ap.add_argument("--file", default="", help="apply 时写的相对路径")
    ap.add_argument("--text", default="", help="apply 时写的内容")
    ap.add_argument("--readonly", action="store_true", help="只读策略（默认 cow）")
    ap.add_argument("--direct", action="store_true", help="直接写原件（需自担风险）")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)

    if args.action == "loop":
        _show(run_plan(json.loads(sys.stdin.read() or "{}")))
        return 0

    if not args.target:
        print("需要 --target <目录>", file=sys.stderr)
        return 2
    ws = CowWorkspace(args.target, root=args.root)

    if args.action == "status":
        _show(ws.status())
        return 0
    if args.action == "diff":
        _show(ws.diff())
        return 0
    if args.action == "rollback":
        _show(ws.rollback())
        return 0
    if args.action == "apply":
        ws.stage()
        _show(ws.write_text(args.file, args.text, policy=_policy(args)))
        _show(ws.diff())
        return 0
    # plan=只读干跑；run=按所选策略真跑
    pol = SandboxPolicy(mode="readonly") if args.action == "plan" else _policy(args)
    steps = [{"name": args.action, "cmd": _argv(args.cmd), "timeout": args.timeout}]
    out = run_loop(steps, ws, policy=pol)
    _show(out)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
