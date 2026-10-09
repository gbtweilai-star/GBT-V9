# tools/verify_eye_hand.py —— 眼手同步（射击级）独立验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import eye_hand as EH   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 眼手同步（射击级）验收 ==")
fps = EH.eye_fps(1.0)
check("眼够快（≥40fps）", (fps.get("fps") or 0) >= 40, "%s fps" % fps.get("fps"))
r = EH.sync_benchmark(50)
check("闭环 p50 ≤50ms（射击级）", (r["闭环ms"]["p50"] or 999) <= 50, json.dumps(r["闭环ms"], ensure_ascii=False))
check("驱动亚毫秒级（p50 ≤2ms）", (r["驱动ms"]["p50"] or 999) <= 2, json.dumps(r["驱动ms"], ensure_ascii=False))
check("到位率 ≥98%（手真到位）", (r["到位率"] or 0) >= 98, "%s%%" % r["到位率"])
check("偏差 ≤2px", (r["偏差px"]["max"] or 999) <= 2, json.dumps(r["偏差px"], ensure_ascii=False))
led = ROOT / "state" / "eye_hand.jsonl"
check("落账", led.is_file() and led.stat().st_size > 0, "%s %s B" % (led.name, led.stat().st_size if led.is_file() else 0))
print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 眼手同步通过（眼≥40fps · 闭环 p50≤50ms · 驱动≤2ms · 到位率≥98% · 偏差≤2px · 落账）")
