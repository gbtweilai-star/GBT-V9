import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_local_models.py —— 本地模型可跑性探针验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import local_model_probe as L   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 本地模型可跑性探针验收 ==")
r = L.judge()
m = r["家底"]
check("量到总内存", isinstance(m.get("内存总GB"), (int, float)), "%s GB" % m.get("内存总GB"))
check("量到可用内存", isinstance(m.get("内存可用GB"), (int, float)), "%s GB" % m.get("内存可用GB"))
check("查了显存（无独显也要如实报）", bool(m.get("显存GB")), m.get("显存GB"))
check("列了本机 ollama 模型", isinstance(m.get("已装模型"), list), [x["名"] for x in m.get("已装模型", [])])
check("MiniCPM 在表里", any("MiniCPM" in x["名"] for x in L.MODELS),
      [x["名"] for x in L.MODELS if "MiniCPM" in x["名"]][:3])
rows = r["逐档"]
check("逐档都有判定与理由", all(x.get("判定") and x.get("理由") for x in rows), "%d 档" % len(rows))
check("判定只有三态", {x["判定"] for x in rows} <= {"能跑", "勉强", "跑不动"}, {x["判定"] for x in rows})
check("给了结论", bool(r.get("结论")), r.get("结论"))
check("口径写明是估算", "估" in (r.get("口径") or ""), (r.get("口径") or "")[:44])

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 本地模型探针通过（家底真量 · 逐档三态 · 有理由有口径）")
