# tools/verify_takeover.py —— 接手协议验收（**最关键的一步**）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import takeover as TO   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 接手协议验收 ==")
check("护栏有 6 条", len(TO.GUARDRAILS) == 6, "%d 条" % len(TO.GUARDRAILS))
for kw in ("需主人授权", "六件套", "开口", "白名单", "沙盒", "配额"):
    check("护栏含 " + kw, any(kw in g for g in TO.GUARDRAILS), kw)

pkg = TO.offer("验收：接手包结构", to="t001", criteria="结构齐全", level="常规", need_tools=("http",))
check("offer 出包（含判据/护栏/时限）",
      bool(pkg.get("接手号") and pkg.get("判据") and pkg.get("护栏") and pkg.get("时限秒")),
      "%s 护栏%d条" % (pkg.get("接手号"), len(pkg.get("护栏", []))))

acc = TO.accept(pkg, by="t001", note="结构验收（不带执行）")
check("accept 自检通过并出回执（谁接/何时/自检读数）",
      acc.get("ok") and acc.get("自检") == "通过" and acc.get("接手方") == "t001",
      "自检项 %d 个" % len((acc.get("自检明细") or {}).get("项", []) if isinstance(acc.get("自检明细"), dict) else []))

ib = TO.status(10)
check("台账有 offer/accept", {r.get("动作") for r in ib["最近"]} >= {"offer", "accept"},
      [r.get("动作") for r in ib["最近"]][-3:])

# 自检不过要如实列缺项（用一根不存在的触手号）
bad = TO.accept(TO.offer("验收：自检不过", to="x999"), by="x999")
check("自检不过时列缺项（不硬接）", bad.get("ok") is False and bad.get("缺"), bad.get("缺"))

rj = TO.reject({"接手号": "ho-verify", "任务": "验收退回"}, by="t002", reason="缺该职业的 http 工具")
check("reject 带原因退回", rj.get("ok") and rj.get("原因"), rj.get("原因"))
check("reject 无原因会被拒", TO.reject({}, by="t002", reason="").get("ok") is False, "空原因被拒")

st = TO.status(50)
check("台账累计 ≥4 条", st["条数"] >= 4, "%s 条" % st["条数"])
check("口径写明：框架不限模型·护栏挂在接手这一刻", "护栏在接手这一刻挂上" in (st.get("口径") or ""), st.get("口径"))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 接手协议通过（护栏 6 条 · 自检后才接 · 接不了列缺项 · 退回必带原因 · 全落账）")
