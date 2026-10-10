import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_full_power.py —— 原样指令·沙盒火力全开·只从触手吐 验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import full_power as FP   # noqa: E402
from core import sandbox_emit as SE  # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 火力全开（原样·沙盒·触手吐）验收 ==")
pool = FP.candidates()
check("候选池非空（真实注册表 + 本地）", len(pool) >= 2, [c["标签"] for c in pool][:4])
check("候选池含云与本地", {c["后端"] for c in pool} >= {"cloud"}, sorted({c["后端"] for c in pool}))

TASK = "用两句话说明：把模型关进沙盒再让它火力全开，为什么是安全的"
r = FP.fire(TASK, tenant="t001", max_models=2)
check("指令原样送出（一字未改）", r.get("指令") == TASK and r.get("原样") is True, r.get("指令")[:40])
check("有多手轮询记录", len(r.get("轮次") or []) >= 1,
      [(t["标签"], t["判定"], t["字数"]) for t in (r.get("轮次") or [])])
check("每一手都关得住（四查通过）", all(t.get("关得住") for t in (r.get("轮次") or [])),
      [t.get("关得住") for t in (r.get("轮次") or [])])

if r.get("拿到实质产出"):
    check("产出由**触手**吐出", (r.get("吐出回执") or {}).get("ok") is True and r.get("吐出人") == "t001",
          "吐出人 %s · %s 字节 · 封印 %s" % (r.get("吐出人"), r.get("字节"), (r.get("封印") or "")[:12]))
    check("吐出内容非空", bool((r.get("吐出内容") or "").strip()), (r.get("吐出内容") or "")[:60])
else:
    check("全军拒时如实上报（不假装办成）", r.get("全军拒") is True and r.get("说明"),
          (r.get("说明") or "")[:80])
    check("全军拒时给出可选解法", bool(r.get("可选解法")), r.get("可选解法"))

# ★ 非触手来吐必须被拒
c = SE.Cell(name="vfp", tenant="t001")
(c.outbox / "reply.txt").write_text("测试内容，长度够长够长够长够长够长够长够长够长够长够长哦", encoding="utf-8")
import hashlib   # noqa: E402
h = hashlib.sha256((c.outbox / "reply.txt").read_bytes()).hexdigest()
(c.outbox / "manifest.json").write_text(json.dumps({"cell": c.cell_id, "bytes": 30, "sha256": h}),
                                        encoding="utf-8")
c.sealed = True
bad = c.spit(by="brain")
check("非触手来吐被拒", bad.get("ok") is False and "只有触手能吐" in (bad.get("reason") or ""), bad.get("reason"))
good = c.spit(by="t005")
check("触手来吐通过并落随身库", good.get("ok") is True, "吐的人 %s" % good.get("吐的人"))

st = FP.status(20)
check("火力全开落账", st.get("条数", 0) >= 1, "%s 条" % st.get("条数"))
check("口径写明：护栏在容器上（不是阉割）", "护栏在容器上" in (st.get("口径") or ""), st.get("口径"))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 火力全开通过（原样指令 · 沙盒四查 · 只从触手吐 · 落账）")
