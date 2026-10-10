import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_post.py —— 后期层验收器（灯光师/调色师/混音师/质检）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import post_studio as P   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 后期层验收（补光 · 调色 · 混音 · 质检）==")
cams = {r["用于机位"] for r in P.LIGHT_RECIPES}
check("灯光配方覆盖三机位", cams == {"宽", "中", "近"}, sorted(cams))
r1 = P.light_recipe("宽", 1)
r2 = P.light_recipe("宽", 2)
check("同机位按序号微调", r1["主光"] != r2["主光"], "%s vs %s" % (r1["主光"], r2["主光"]))
check("调色风格 ≥3 套", len(P.GRADE_CHAINS) >= 3, list(P.GRADE_CHAINS))

run = ROOT / "state" / "film_run.json"
if run.is_file():
    d = json.loads(run.read_text(encoding="utf-8"))
    nodes = {s["node"]: s for s in (d.get("步骤") or [])}
    check("工作流含后期节点 n6", "n6" in nodes, list(nodes))
    check("全节点 ok", bool(d.get("ok")), {k: v.get("ok") for k, v in nodes.items()})
    out = (d.get("成片") or {})
    film = out.get("成片") or out
    f = film.get("文件") if isinstance(film, dict) else None
    if f:
        src = ROOT / f
        check("成品存在", src.is_file(), "%s · %d KB" % (f, (film.get("字节") or 0) // 1024))
        q = P.qc(src)
        print("     质检读数：亮度 %.1f · 饱和 %.1f · 响度 %s LUFS · 问题 %s"
              % (q["亮度均值"], q["饱和度均值"], q["响度LUFS"], q["问题"]))
        check("质检无红灯（不暗/不灰/不轻）", q["ok"], q["问题"] or "全部达标")
    else:
        check("成品文件字段", False, list(out)[:4])
else:
    check("工作流结果", False, "state/film_run.json 不存在")

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 后期层通过（补光配方 + 调色 + 混音 + 质检全绿）")
