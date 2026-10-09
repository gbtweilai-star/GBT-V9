# tools/desilence_v2.py —— 去静默 v2（AST 精确：只改「except X: pass」这一种形态）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
# v1 用正则会插错位置（35 个文件被安全回滚）。v2 用语法树定位：只认 ExceptHandler 且 body 恰为单个 pass；
# 替换只动那两行；import 插在 __future__/docstring 之后、第一段 import 之前；每文件改完编译，不过就整份回滚。
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRS = ("core", "tools", "panel", "body", "senses", "audit", "media", "skills",
        "actuator", "alert", "parity", "scan")
IMPORT = "from core.swallow import swallow as _swallow"
SKIP = ("core/swallow.py", "tools/desilence.py", "tools/desilence_v2.py", "core/no_begging.py")


def _bare_pass_handlers(tree) -> list:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body = [s for s in node.body
                    if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant)
                            and isinstance(s.value.value, str))]
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                out.append(node)
    return out


def _insert_import(lines: list) -> list:
    if any(IMPORT in l for l in lines):
        return lines
    at = 0
    for i, l in enumerate(lines[:120]):
        if l.startswith("from __future__"):
            at = i + 1
        elif l.startswith(("import ", "from ")) and at == 0:
            at = i
            break
    lines.insert(at, IMPORT)
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
            rel = str(p.relative_to(ROOT)).replace(chr(92), "/")
            if rel in SKIP:
                continue
            src = p.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            handlers = _bare_pass_handlers(tree)
            if not handlers:
                continue
            lines = src.splitlines()
            for h in sorted(handlers, key=lambda n: -n.lineno):
                indent = " " * h.col_offset
                etype = (ast.unparse(h.type) if h.type else "Exception").replace(" as e", "")
                end = h.body[-1].end_lineno or h.body[-1].lineno
                lines[h.lineno - 1:end] = ["%sexcept %s as e:" % (indent, etype),
                                           "%s    _swallow(__file__, e)" % indent]
            out = chr(10).join(_insert_import(lines)) + chr(10)
            p.write_text(out, encoding="utf-8")
            ck = ROOT / "state" / "_c.k"
            r = subprocess.run([sys.executable, "-c",
                                "import py_compile; py_compile.compile(r'%s', doraise=True, cfile=r'%s')" % (p, ck)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                p.write_text(src, encoding="utf-8")
                rolled.append(rel)
            else:
                sites += len(handlers)
                changed.append("%s(%d)" % (rel, len(handlers)))
    print("AST 去静默：%d 个文件 / %d 处" % (len(changed), sites))
    print("  明细:", ", ".join(changed[:14]), ("… 共 %d" % len(changed)) if len(changed) > 14 else "")
    print("编译不过已回滚:", (rolled[:8] + (["…共 %d" % len(rolled)] if len(rolled) > 8 else [])) or "无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
