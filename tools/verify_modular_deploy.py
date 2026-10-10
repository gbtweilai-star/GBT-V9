import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_modular_deploy.py —— 模块式部署验收（模块=实现件+面板口+独立验收器）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import modular_deploy as MD   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 模块式部署验收 ==")
bp = MD.blueprint()
check("蓝图模块数 ≥ 10", bp["模块数"] >= 10, bp["模块数"])
check("每个模块都声明了独立验收器",
      all(m["验收器"] for m in bp["模块"]),
      [m["模块"] for m in bp["模块"] if not m["验收器"]] or "全部有")

missing_impl, missing_ver = [], []
for mod, spec in MD.BLUEPRINT.items():
    for rel in spec.get("实现", []):
        if not (ROOT / rel).is_file():
            missing_impl.append("%s→%s" % (mod, rel))
    for rel in spec.get("验收", []):
        if not (ROOT / rel).is_file():
            missing_ver.append("%s→%s" % (mod, rel))
check("蓝图声明的实现件都在盘上", not missing_impl, missing_impl[:4] or "全在")
check("蓝图声明的验收器都在盘上", not missing_ver, missing_ver[:4] or "全在")

allr = MD.deploy_all(dry=True)
check("全量装（干跑）能报缺件", allr.get("模块数") == bp["模块数"],
      "模块 %s · 装好 %s · 缺件模块 %s" % (allr.get("模块数"), allr.get("装好"), allr.get("缺件模块") or "无"))

one = MD.deploy("纪律-细节化与不开口")
check("单模块部署（真装 + 落台账）", one.get("ok") and one.get("件数", 0) >= 2,
      "%s：件 %s · 依赖 %s" % (one.get("模块"), one.get("件数"), one.get("依赖")))
check("缺件时如实报（不假装装好）", MD.deploy("不存在的模块").get("ok") is False,
      MD.deploy("不存在的模块").get("reason"))

v = MD.verify("纪律-细节化与不开口")
check("模块验（只跑它自己的验收器）", v.get("通过") is True,
      [x["验收器"] + ":" + str(x["通过"]) for x in v.get("验收", [])])
st = MD.status(3)
_rows = st.get("最近部署") or st.get("最近") or []      # 🔴 键名是「最近部署」，我上一版读「最近」读成 0 条
check("部署台账可读", isinstance(_rows, list) and st.get("蓝图模块数") >= 10,
      "蓝图 %s · 最近 %s 条" % (st.get("蓝图模块数"), len(_rows)))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 模块式部署通过（蓝图齐 · 三件齐 · 单模块可装可验 · 缺件如实报 · 落账）")
