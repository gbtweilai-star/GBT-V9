# tools/verify_tentacle_store.py —— 触手随身库验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import tentacle_store as TS   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 触手随身库验收 ==")
r = TS.provision_fleet(100)
check("100 根各配一个随身库", r.get("ok") is True and r.get("配备") == 100, r)
# 🔴 2026-10-09 追根因：tentacle_scale 按需实体化会额外建 t12345678 这类库 ⇒ 总数 > 100。
#    原来写死 ==100 太脆（是我判据的错，不是能力的错）。改成：**基础 100 根必须全在** + 允许动态实体化。
_base = [TS.path("t%03d" % i) for i in range(1, 101)]
_missing = [p.name for p in _base if not p.is_file()]
st = TS.status()
check("基础 100 根随身库全在", not _missing, "缺 %s" % (_missing[:5] or "无"))
check("库文件数 ≥100（含按需实体化的）", (st.get("配备了") or 0) >= 100,
      "%s 个 · %s 字节" % (st.get("配备了"), st.get("总字节")))

k1 = TS.stash("t001", "verify/stash", {"验证": True})
check("随手丢可用", k1.get("ok") is True, k1.get("键"))
rp = TS.report("t001", "验收写入一条汇报", "来自 verify_tentacle_store", level="常规")
check("汇报落库（标未读）", rp.get("ok") is True and rp.get("汇报号"), "号 %s 级别 %s" % (rp.get("汇报号"), rp.get("级别")))
bad = TS.report("t001", "级别乱填", "应被归为常规", level="宇宙级")
check("非法级别归常规（不崩）", bad.get("级别") == "常规", bad.get("级别"))

ib = TS.inbox()
check("主脑批量取未读", ib.get("未读条数", 0) >= 1, "%s 条" % ib.get("未读条数"))
_rank = {"紧急": 0, "重要": 1, "常规": 2, "备忘": 3}
_lv = [_rank.get(x.get("级别"), 9) for x in ib["汇报"]]
check("未读按级别排序（紧急>重要>常规>备忘）",
      bool(ib["汇报"]) and all(isinstance(x, dict) and x.get("级别") and x.get("标题")
                              and x.get("at") for x in ib["汇报"]) and _lv == sorted(_lv),
      [x["级别"] for x in ib["汇报"][:4]])
n = TS.mark_read(ib["汇报"])
check("读完能标已读", n.get("标已读", 0) >= 1, n.get("标已读"))
check("标完未读归零", TS.inbox()["未读条数"] == 0, TS.inbox()["未读条数"])

dr = TS.drop("t001", "verify.txt", "deadbeef", 12, "验收登记")
check("产物登记可用", dr.get("ok") is True, dr.get("登记"))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 触手随身库通过（100 个独立库 · 随手丢 · 汇报·未读·批量慢看·标已读）")
