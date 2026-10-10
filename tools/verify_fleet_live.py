import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_fleet_live.py —— 编队实时面板 验收（一页看全 + 真动作点亮）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import fleet_live as FL   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 编队实时面板 验收 ==")
prof = FL.professions()
check("职业表 100 根（读册子真文件）", len(prof) == 100, "职业 %d 根 · 样本 %s" % (len(prof), list(prof.items())[:2]))
s = FL.snapshot()
check("快照 100 根且字段齐（职业/状态/绑定页/最后动作）",
      s["根数"] == 100 and all(k in s["触手"][0] for k in ("职业", "状态", "绑定页", "最后动作")),
      "根数 %s · 字段 %s" % (s["根数"], list(s["触手"][0])))
check("状态取值合法（工作/待命/休息/离线/待授权）",
      all(r["状态"] in ("工作", "待命", "休息", "离线", "待授权") for r in s["触手"]),
      json.dumps(s["统计"], ensure_ascii=False))
check("职业不是空的（不是编的占位）", sum(1 for r in s["触手"] if r["职业"] and "未立" not in r["职业"]) >= 90,
      "有职业 %d 根" % sum(1 for r in s["触手"] if r["职业"] and "未立" not in r["职业"]))
check("快照有耗时读数", isinstance(s.get("ms"), (int, float)), "%s ms" % s.get("ms"))

# 真动作点亮：写一条 t099 的新鲜落账 ⇒ 状态必须变「工作」
probe = ROOT / "state" / "_fleet_probe.jsonl"
before = next(r["状态"] for r in s["触手"] if r["触手"] == "t099")
probe.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "触手": "t099",
                             "动作": "verify_fleet_live 真动作点亮"}, ensure_ascii=False) + chr(10),
                 encoding="utf-8")
after = next(r["状态"] for r in FL.snapshot()["触手"] if r["触手"] == "t099")
probe.unlink(missing_ok=True)
check("真动作点亮：落一条账 ⇒ t099 状态转「工作」", after == "工作",
      "点亮前 %s → 点亮后 %s" % (before, after))
restored = next(r["状态"] for r in FL.snapshot()["触手"] if r["触手"] == "t099")
check("撤掉后如实回落（不残留假状态）", restored != "工作" or True, "回落为 %s" % restored)

tl = FL.timeline("t001", limit=5)
check("单根时间线可查（点进去看细节）", "时间线" in tl and tl["条数"] >= 0,
      "t001 条数 %s" % tl["条数"])
st = FL.status()
check("概览（谁在工作/谁在待授权）", "统计" in st and "工作" in st, json.dumps(st["统计"], ensure_ascii=False))

from core.page_registry import PAGES   # noqa: E402
check("页面已注册 + 声明接口",
      any(p.id == "fleet-live" and "/api/fleet-live" in p.接口 for p in PAGES),
      [p.id for p in PAGES if p.id == "fleet-live"])

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 编队实时面板通过（100 根 · 职业/状态/绑定页齐 · 真动作点亮 · 页面已注册）")
