# tools/verify_manual.py —— 使用说明机检：文里点到的路径必须真实存在
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 为什么要有它：说明书最容易烂在「写了不存在的文件/命令」。这里把路径逐条核对，红了就是文档漂移。
# 用法：python tools/verify_manual.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FAILS: list = []


def main() -> int:
    doc = ROOT / "docs" / "使用说明.md"
    print("== 使用说明机检 @", ROOT, "==")
    if not doc.is_file():
        print("  ❌ 说明书不存在"); return 1
    text = doc.read_text(encoding="utf-8")
    print("  ✅ 说明书存在 —— %d 行 / %d 字符" % (text.count(chr(10)) + 1, len(text)))

    # ① 反引号里的仓内路径：存在才算数
    cand = set(re.findall(r"`([A-Za-z0-9_./\u4e00-\u9fff-]+\.(?:py|md|json|jsonl|env|cfg|ini|yaml|yml|cmd|png|webm|jpg|db|sqlite3))`", text))
    missing = []
    for rel in sorted(cand):
        if rel.startswith(("state/", "data/", "tools/_bay")) and not (ROOT / rel).exists():
            missing.append(rel + "（运行时目录，允许暂不存在）")
        elif not (ROOT / rel).exists() and "/" in rel:
            missing.append(rel)
    hard = [m for m in missing if "允许暂不存在" not in m]
    print("  %s 仓内路径核对 —— 提到 %d 条，缺 %d 条" % ("✅" if not hard else "❌", len(cand), len(hard)))
    for m in hard[:12]:
        print("     -", m)
    if hard:
        FAILS.append("仓内路径" + str(hard[:3]))

    # ② 提到的 tools/verify_*.py 必须都在（说明书点名的验收器不许缺）
    vers = sorted(set(re.findall(r"tools/(verify_[a-z_]+\.py)", text)))
    want = sorted(p.name for p in (ROOT / "tools").glob("verify_*.py"))
    lacking = [v for v in vers if not (ROOT / "tools" / v).is_file()]
    print("  %s 说明书点名的验收器都在 —— 点名 %d / 盘上 %d" % ("✅" if not lacking else "❌", len(vers), len(want)))
    for v in lacking:
        print("     -", v)
    if lacking:
        FAILS.append("验收器缺失")

    # ③ 说明书提到的命令前缀必须真能跑（只查文件在不在，不真跑）
    cmds = sorted(set(re.findall(r"python (tools/[a-z_]+\.py)", text)))
    bad = [c for c in cmds if not (ROOT / c).is_file()]
    print("  %s 命令里的脚本都在 —— %d 条" % ("✅" if not bad else "❌", len(cmds)))
    for b in bad:
        print("     -", b)
    if bad:
        FAILS.append("命令脚本缺失")

    # ④ 面板端点与页面的说法要有对应实现（抽查关键几条）
    must_have = {"/api/panel/capabilities": "panel/panel_api.py",
                 "/workflows/chain": "panel/workflows_api.py",
                 "/api/setup/status": "panel/dh_companion.py",
                 "/api/voice/ptt": "panel/voice_page.py",
                 "/digital-human/console": "panel/dh_console.py"}
    for route, f in must_have.items():
        p = ROOT / f
        ok = p.is_file() and route.split("/")[-1] in p.read_text(encoding="utf-8")
        if not ok:
            FAILS.append(route)
        print("  %s %s → %s" % ("✅" if ok else "❌", route, f))

    print("\n结论：" + ("✅ 说明书与实现一致" if not FAILS else "❌ 漂移：" + "、".join(FAILS[:4])))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
