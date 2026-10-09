# tools/verify_senses_gate.py —— 四觉闭环 + 视觉钉死 验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import senses_gate as SG   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 四觉闭环 + 视觉钉死 验收 ==")
bs = SG.blind_spots()
check("瞎子缝 = 0（所有动手口都过闸）", bs["缝隙数"] == 0,
      "缝: %s" % (bs["瞎子缝"] or "无"))
check("动手口清单齐（5 个手都点名）", len(bs["明细"]) >= 5,
      " · ".join("%s(%d点)" % (d["文件"].split("/")[-1], d["动作点数"]) for d in bs["明细"]))

# 无脑 → 拒动（缺脑那一步）
r1 = SG.act(lambda: {"ok": True}, decision={})
check("缺『脑』（无决策）⇒ 拒动", r1.get("拒动") is True and r1.get("在哪一步") == "②脑",
      "%s · %s" % (r1.get("在哪一步"), str(r1.get("读数"))[:50]))

# 有眼有脑 → 放行（用自家浏览器真动作，验走回执口径）
from core import browser_plug as BP   # noqa: E402
r2 = SG.act(lambda: BP.pilot("t001", "open", url="https://example.com"),
            decision={"目标": "打开示例页", "理由": "验收四步闭环", "复核等待ms": 300},
            verify="return")
check("眼+脑+手+验 四步全绿 ⇒ 放行", r2.get("拒动") is False and all((r2.get("步骤") or {}).values()),
      "步骤 %s · 验口径 %s" % (r2.get("步骤"), r2.get("验的口径")))
check("四步都有读数（不是空判）", all(k in r2 for k in ("眼", "复核帧", "真响应ms", "动作回执")),
      "真响应 %s ms · 复核帧 %s" % (r2.get("真响应ms"), r2.get("复核帧")))

# 耳/嘴：如实
e = SG.ear()
check("耳：如实报能否听（不许假装）", "可听" in e and "判" in e, "%s · %s" % (e.get("可听"), e.get("判")))
m = SG.mouth("GBT小土豆V9", speak=False)
check("嘴：登记文本（未发声时不谎称说了）", m.get("说了") is False and m.get("文本") == "GBT小土豆V9",
      "说了=%s" % m.get("说了"))
check("落账", SG.status(3).get("最近") is not None, "state/senses_gate.jsonl")

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 四觉闭环通过（瞎子缝 0 · 缺脑拒动 · 四步全绿放行 · 耳嘴如实 · 落账）")
