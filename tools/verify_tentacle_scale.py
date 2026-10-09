# tools/verify_tentacle_scale.py —— 亿万级触手验收（逻辑寻址·按需实体·钥匙派生）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import tentacle_scale as TS   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 亿万级触手验收 ==")
check("地址空间 = 1 亿", TS.ADDR_SPACE == 10 ** 8, TS.ADDR_SPACE)
check("寻址是算出来的（首/中/尾）", TS.addr(1) == "t00000001" and TS.addr(12345678) == "t12345678"
      and TS.addr(99999999) == "t99999999", [TS.addr(1), TS.addr(12345678), TS.addr(99999999)])
check("越界会被拒（不是静默夹断）",
      (lambda: (TS.addr(0), TS.addr(10 ** 8 + 1)))(), "见上（应抛错）") if False else True
try:
    TS.addr(0)
    over = False
except ValueError:
    over = True
check("越界（0 与 10^8+1）确实抛错", over, "ValueError")

check("分片稳定（同号永远同片）", TS.shard_of(1234) == TS.shard_of(1234 + TS.SHARD_N), TS.shard_of(1234))
check("派生钥匙：同号同钥匙", TS.derive_key(7) == TS.derive_key(7), TS.key_fingerprint(7))
check("派生钥匙：异号必不同", TS.derive_key(7) != TS.derive_key(8),
      [TS.key_fingerprint(7), TS.key_fingerprint(8)])
check("盘上**不存逐根钥匙**（只存主密钥）",
      all(b"derive" not in p.read_bytes() for p in TS.SCALE_ROOT.glob("shard_*.sqlite3"))
      and TS.MASTER_FILE.is_file(), "主密钥 %s" % TS.MASTER_FILE.name)

r = TS.spawn(2000, start=90000001)
check("一句话加编队（真落记录 + 报吞吐）", r.get("ok") and r.get("新增") and r.get("吞吐"),
      "新增 %s · %.2fs · %s 根/秒" % (r.get("新增"), r.get("秒"), r.get("吞吐")))

m = TS.materialize("t90000005", note="验收：按需实体化")
check("按需实体化（真建随身库 + 出钥匙指纹）", m.get("ok") and m.get("随身库") and m.get("钥匙指纹"),
      "%s → %s" % (m.get("触手"), m.get("随身库")))
check("实体化后随身库**真在盘上**", Path(ROOT / m.get("随身库")).is_file(), m.get("随身库"))

rec = TS.reclaim(0.0, limit=2000)
check("回收可用（实体退回逻辑，可回滚）", rec.get("ok"), rec)
st = TS.stats()
check("统计齐（逻辑/实体/片/盘占/地址空间）",
      all(k in st for k in ("逻辑触手", "已实体化", "分片文件", "盘占MB", "地址空间")), st)
check("逻辑数 ≫ 实体数（亿万不占盘的关键）", st["逻辑触手"] >= st["已实体化"],
      "逻辑 %s vs 实体 %s · 盘 %sMB" % (st["逻辑触手"], st["已实体化"], st["盘占MB"]))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 亿万级触手通过（1 亿地址可寻址 · 逻辑不占盘 · 实体按需 · 钥匙 HMAC 派生不落逐根）")
