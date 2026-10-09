# tools/desilence.py —— 全仓去静默（except: pass → 有名有因有落账）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令：「把红色黄色代码全清干净，别看起来跟小白部署一样。」
# 做法：把「except X: pass」换成「except X as e: _swallow(__file__, e)」，并在模块顶部补一行
#   from core.swallow import swallow as _swallow
# 安全：每个文件改完**立即编译**；编译不过**整份回滚**（宁可不改，不许改坏）。
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRS = ("core", "tools", "panel", "body", "senses", "audit", "media", "skills", "parity", "scan", "actuator", "alert")
PAT = re.compile(r"(?P<ind>[ \t]*)except(?P<ex>[^\n:]*):[ \t]*\r?\n(?P=ind)[ \t]*pass\b")
IMPORT_LINE = "from core.swallow import swallow as _swallow"


def _insert_import(lines: list) -> list:
    if any(IMPORT_LINE in l for l in lines):
        return lines
    # 插在顶部 import 区之后（前 80 行内最后一个 import/from 行之后）
    last = 0
    for i, l in enumerate(lines[:80]):
        s = l.strip()
        if s.startswith("import ") or s.startswith("from "):
            last = i + 1
    lines.insert(last, IMPORT_LINE)
    return lines


def main() -> int:
    changed, rolled, sites = [], [], 0
    for d in DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            src = p.read_text(encoding="utf-8", errors="replace")
            if not PAT.search(src):
                continue
            rel = str(p.relative_to(ROOT)).replace(chr(92), "/")
            if rel.startswith(("core/swallow.py", "tools/desilence.py")):
                continue

            def rep(m):
                ind, ex = m.group("ind"), (m.group("ex").strip() or "Exception")
                ex = re.sub(r"\s+as\s+\w+$", "", ex)
                return "%sexcept %s as e:\n%s    _swallow(__file__, e)\n" % (ind, ex, ind)
            new, n = PAT.subn(rep, src)
            new = chr(10).join(_insert_import(new.splitlines())) + chr(10)
            p.write_text(new, encoding="utf-8")
            ck = ROOT / "state" / "_c.k"
            r = subprocess.run([sys.executable, "-c",
                                "import py_compile; py_compile.compile(r'%s', doraise=True, cfile=r'%s')" % (p, ck)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                p.write_text(src, encoding="utf-8")
                rolled.append(rel)
            else:
                sites += n
                changed.append("%s(%d)" % (rel, n))
    print("已去静默：%d 个文件 / %d 处" % (len(changed), sites))
    print("  明细:", ", ".join(changed[:12]), ("… 共 %d 个" % len(changed)) if len(changed) > 12 else "")
    print("编译不过已回滚:", rolled or "无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
