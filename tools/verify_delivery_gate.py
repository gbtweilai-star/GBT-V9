# tools/verify_delivery_gate.py —— 交付闸验收（每个能力必须单独闭环）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import delivery_gate as DG   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 交付闸验收 ==")
n = len(DG.CAPABILITIES)
check("能力登记 ≥ 20 个", n >= 20, n)
have = [c for c in DG.CAPABILITIES if (ROOT / c[2]).is_file()]
check("每个登记能力都有**独立验收器**（缺=没闭环）", len(have) == n, "%d/%d" % (len(have), n))
check("验收器文件都是 verify_*.py", all(Path(c[2]).name.startswith("verify_") for c in DG.CAPABILITIES),
      [c[2] for c in DG.CAPABILITIES if not Path(c[2]).name.startswith("verify_")] or "全部合规")

r1 = DG.run_one("tools/verify_no_ask.py")
check("单个能力能跑出结论（含退出码）", r1.get("通过") and r1.get("退出码") == 0,
      "%s · %s" % (r1.get("退出码"), (r1.get("结论") or "")[:60]))
r2 = DG.run_one("tools/verify_not_exist_xyz.py")
check("缺验收器如实判『没闭环』（不放过）", r2.get("通过") is False and "不存在" in (r2.get("结论") or ""),
      r2.get("结论"))
g = DG.gate("不存在的能力")
check("未登记能力不放行", g.get("放行") is False, g.get("原因"))
g2 = DG.gate("不开口闸")
check("已闭环能力放行", g2.get("放行") is True, (g2.get("读数") or {}).get("结论", "")[:60])
st = DG.status(3)
check("交付闸落账", st.get("登记能力数", 0) >= 20, "登记 %s · 台账 %s 次" % (st.get("登记能力数"), len(st.get("最近") or [])))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 交付闸通过（能力都有独立验收器 · 缺验收器判没闭环 · 未登记不放行 · 落账）")
