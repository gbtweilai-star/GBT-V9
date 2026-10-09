# tools/verify_cross_scan.py —— 编队交叉扫描验收（触手监督触手 · 无盲区是读数）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import cross_scan_fleet as CS   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 编队交叉扫描验收 ==")
check("检查器无噪音（已剔除裸双引号那条）", "裸双引号嵌套" not in CS.CHECKS, list(CS.CHECKS))
check("分片可复现（同文件永远同片）", CS.shards(CS._files(20), 10) == CS.shards(CS._files(20), 10), "两次一致")

tmp1 = ROOT / "state" / "_cs_clean.py"
tmp2 = ROOT / "state" / "_cs_hit.py"
tmp1.write_text("x = 1  # clean\n", encoding="utf-8")
tmp2.write_text("# 不影响\n", encoding="utf-8")
c0 = CS.cross_check({"文件": "state/_cs_clean.py", "行": 1, "检查": "禁忌词(不影响)"}, "禁忌词(不影响)")
c2 = CS.cross_check({"文件": "state/_cs_hit.py", "行": 1, "检查": "禁忌词(不影响)"}, "禁忌词(不影响)")
check("干净行：交叉复核 0 认同（不冤判）", c0["认同数"] == 0, c0)
check("真命中：交叉复核 2 认同", c2["认同数"] == 2 and c2["结论"] == "真命中", c2)
check("复核者是**别的触手号**", all(v["复核触手"].startswith("t") for v in c2["复核触手"]),
      [v["复核触手"] for v in c2["复核触手"]])
tmp1.unlink(missing_ok=True)
tmp2.unlink(missing_ok=True)

r = CS.run(100, verbose=False)
check("跑通并出读数", all(k in r for k in ("文件数", "分片", "命中总数", "交叉复核通过", "盲区")),
      "文件 %s · 命中 %s · 复核 %s · 孤证 %s" % (r["文件数"], r["命中总数"], r["交叉复核通过"],
                                          r["盲区"]["孤证数"]))
check("账对得上：命中 = 复核通过 + 孤证", r["命中总数"] == r["交叉复核通过"] + r["盲区"]["孤证数"],
      "%s = %s + %s" % (r["命中总数"], r["交叉复核通过"], r["盲区"]["孤证数"]))
check("命中量级合理（噪音剔除后应远小于 1000）", r["命中总数"] < 1000, r["命中总数"])
check("盲区清单有口径", bool((r.get("盲区") or {}).get("口径")), (r["盲区"].get("口径") or "")[:40])
check("落账", CS.status(5).get("条数", 0) >= 1, "%s 条" % CS.status(5).get("条数"))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 编队交叉扫描通过（无噪音检查器 · 命中须 2 根复核 · 孤证不算 · 盲区有清单 · 落账）")
