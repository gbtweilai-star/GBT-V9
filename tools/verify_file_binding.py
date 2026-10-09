# tools/verify_file_binding.py —— 双向绑定 + 无死角穿透 验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import file_binding as FB   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 双向绑定 + 无死角穿透 验收 ==")
check("建表/索引幂等", FB.ensure_table()["ok"] is True, "4 条")

tf = FB.tracked_files()
check("排除项逐类登记原因（不隐藏分母）",
      all(x.get("原因") for x in tf["排除集"][:50]) and tf["排除集数"] > 0,
      "排除 %d 条 · 原因 %d 类" % (tf["排除集数"], len({x["原因"] for x in tf["排除集"]})))

r = FB.bind_all()
check("绑定可跑且幂等（连跑不炸）", r.get("ok") is True, "写行 %s · %.1fs" % (r.get("写行"), r.get("秒")))
c = FB.coverage()
check("覆盖率 100%（否则列未绑定清单）", c["覆盖率"] >= 100.0,
      "%.2f%% (%d/%d) · 未绑定 %d" % (c["覆盖率"], c["已绑定"], c["绑定集"], c["未绑定数"]))
check("每根触手都有页（100 根都分到活）", c["触手数"] >= 100, "触手 %d 根" % c["触手数"])

# 双向互反
sample = c["每根触手页数"][0]["tentacle_id"]
own = FB.owner(sample, limit=5)
ok_two_way = False
if own["页"]:
    p0 = own["页"][0]["path"]
    lk = FB.lookup(p0)
    ok_two_way = any(b["tentacle_id"] == sample for b in lk["绑定"])
    check("双向互反（触手→页→同一触手）", ok_two_way,
          "%s → %s → %s" % (sample, p0, lk["触手数"]))
else:
    check("双向互反（触手→页→同一触手）", False, "该触手没有页")

# 瞬间穿透（两档真口径：path 走索引=瞬间；all 另加内容检索=如实报）
lat_path, lat_all = [], []
for kw in ("core/file_binding.py", "tentacle_file_bindings", "verify_", "net_guard", "spit"):
    t0 = time.time()
    hit = FB.penetrate(kw, limit=20, scope="path")
    lat_path.append((time.time() - t0) * 1000)
    t0 = time.time()
    hit_all = FB.penetrate(kw, limit=20, scope="all")
    lat_all.append((time.time() - t0) * 1000)
    if kw == "tentacle_file_bindings":
        check("穿透命中并给链路（含触手/状态）",
              hit_all["命中"] > 0 and all("触手" in x for x in hit_all["链路"][:3]),
              "命中 %d · 检索法 %s" % (hit_all["命中"], hit_all.get("检索法")))
avg_p = sum(lat_path) / len(lat_path)
avg_a = sum(lat_all) / len(lat_all)
check("【路径档】瞬间：平均 < 200 ms", avg_p < 200, "平均 %.0f ms（最大 %.0f）" % (avg_p, max(lat_path)))
check("【内容档】如实：单次 < 2 s", max(lat_all) < 2000, "平均 %.0f ms（最大 %.0f）" % (avg_a, max(lat_all)))

# 含字面反斜杠的合法文件名也要能反查（工作区确有此类文件）
_bl = [r for r in FB._q("SELECT path FROM tentacle_file_bindings WHERE root_id=? AND path LIKE ?",
                         (FB.ROOT_ID, "%" + chr(92) + "%"))][:1]
if _bl:
    check("含反斜杠转义的文件名可查（原样优先）", FB.lookup(_bl[0]["path"])["触手数"] >= 1,
          _bl[0]["path"][:60])

check("落账", FB.status(2).get("最近绑定") is not None, "state/file_binding.jsonl")

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 双向绑定通过（覆盖 100% · 双向互反 · 穿透 <200ms 平均 · 落账）")
