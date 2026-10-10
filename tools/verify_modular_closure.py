import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_modular_closure.py —— 模块闭环硬规定 验收
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


print("== 模块闭环硬规定 验收 ==")
# 开工闸：缺验收器即拒
check("开工闸：模块清单缺验收器 ⇒ 直接拒",
      MD.plan_project([{"模块": "新模块", "实现": ["x.py"]}])["放行"] is False,
      MD.plan_project([{"模块": "新模块", "实现": ["x.py"]}])["缺验收器"])
check("开工闸：清单带验收器 ⇒ 放行",
      MD.plan_project([{"模块": "合规模块", "实现": ["x.py"], "验收": ["tools/verify_no_ask.py"]}])["放行"] is True,
      "合规模块")
# 布置闸：缺验收器即装不成
check("布置闸：模块缺验收器 ⇒ 装不成", MD.deploy("不存在模块X").get("ok") is False,
      MD.deploy("不存在模块X").get("reason") or MD.deploy("不存在模块X").get("口径"))
check("布置闸：正常模块可装", MD.deploy("纪律-细节化与不开口").get("ok") is True,
      "件 %s" % MD.deploy("纪律-细节化与不开口").get("件数"))
# 闭环率（真跑每个模块自己的验收器）
c = MD.closure_rate()
check("闭环率可算（模块数/已闭环/未闭环/清单）",
      all(k in c for k in ("模块数", "已闭环", "闭环率", "未闭环清单")),
      "模块 %s · 已闭环 %s · 闭环率 %s%%" % (c["模块数"], c["已闭环"], c["闭环率"]))
print("     未闭环清单:", c["未闭环清单"] or "无")
# 未闭环拦停 + 绑定盲区
a = MD.assert_modules_closed()
check("未闭环模块 ⇒ 拦停（不许交付）", a["放行"] == (not a["未闭环"] and not a["盲区"]),
      "放行=%s · 未闭环 %d · 盲区 %d" % (a["放行"], len(a["未闭环"]), a["盲区数"]))
# 停机闸接入
from core import stop_policy as SP   # noqa: E402
gaps = [g.get("缺口", "") for g in SP.completeness()["缺口"]]
check("停机闸已接入模块闭环 + 绑定覆盖",
      any("模块闭环率" in g for g in gaps) or any("绑定盲区" in g for g in gaps)
      or SP.completeness()["整体完善"],
      "缺口样本: %s" % (gaps[:3] or "无（整体完善）"))

print()
if c["未闭环"]:
    print("结论：❌ 有 %d 个模块未闭环 —— %s" % (c["未闭环"], "、".join(c["未闭环清单"][:6])))
    raise SystemExit(1)
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 模块闭环硬规定通过（开工闸/布置闸/闭环率 100%/拦停/停机闸接入）")
