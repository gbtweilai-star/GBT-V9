import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_capability_loop.py —— 「每一项能力都调用跑通闭环」验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LEDGER = ROOT / "state" / "capability_loop.jsonl"
FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 能力调用闭环验收 ==")
from skills import INFRA_CAPABILITIES, NATIVE_CAPABILITIES   # noqa: E402
allcaps = list(NATIVE_CAPABILITIES) + list(INFRA_CAPABILITIES)
check("权威清单读得出", len(allcaps) == 9, "%d 项：%s" % (len(allcaps), allcaps))

check("闭环台账在", LEDGER.is_file(), str(LEDGER.relative_to(ROOT)))
rec = json.loads(LEDGER.read_text(encoding="utf-8").splitlines()[-1]) if LEDGER.is_file() else {}
rows = {r["能力"]: r for r in rec.get("明细", [])}
check("台账覆盖全部权威能力", all(c in rows for c in allcaps),
      "缺：%s" % [c for c in allcaps if c not in rows] or "齐")
check("每一项都有结论", all(r.get("结论") for r in rows.values()), "%d 项" % len(rows))
nope = [r["能力"] for r in rows.values() if r.get("结论") != "通" and not r.get("卡点")]
check("非『通』的项都写了具体卡点（不许空着/不许『不影响』）", not nope, nope or "0 项")
ban = [r["能力"] for r in rows.values() if "不影响" in json.dumps(r, ensure_ascii=False)]
check("台账无『不影响』禁句", not ban, ban or "无")

n_ok = sum(1 for r in rows.values() if r.get("结论") == "通")
n_warn = sum(1 for r in rows.values() if r.get("结论") == "黄警")
n_bad = sum(1 for r in rows.values() if r.get("结论") == "未通")
print("     读数：总 %d · 通 %d · 黄警 %d · 未通 %d" % (len(rows), n_ok, n_warn, n_bad))
check("台账数字与明细一致", rec.get("通") == n_ok and rec.get("未通") == n_bad,
      "台账(%s/%s) vs 明细(%s/%s)" % (rec.get("通"), rec.get("未通"), n_ok, n_bad))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 能力调用闭环通过（9 权威能力全覆盖 · 每项有结论与卡点 · 无禁句）")
