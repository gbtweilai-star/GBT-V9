import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_tentacle_vaults.py —— 触手册子（独立金库）与自配装备验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import tentacle_equip as TE        # noqa: E402
from core import tentacle_profession as TP   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 触手册子 + 自配装备验收 ==")
VAULT = ROOT / "vaults" / "agency_tentacles"
files = sorted(VAULT.glob("*.md")) if VAULT.is_dir() else []
check("册子目录在", VAULT.is_dir(), str(VAULT.relative_to(ROOT)))
check("册子 100 篇", len(files) >= 100, "%d 篇" % len(files))

if files:
    head = files[0].read_text(encoding="utf-8")
    for seg in ("## 身份与专业", "## 需求与装备计划", "## 本次配置结果", "## 纪律"):
        check("册子含章节 " + seg, seg in head, files[0].name)
    # 抽 3 篇看有没有"未立"
    bad = [f.name for f in files[:100] if "职业（专业）：未立" in f.read_text(encoding="utf-8")]
    check("册子无『未立专业』条目", not bad, ("有 %d 篇未立" % len(bad)) if bad else "全已立")

rep = TE.report(n=100)
check("账号已齐 100", rep.get("账号已齐") == 100, rep.get("账号已齐"))
check("装备已齐 100", rep.get("装备已齐") == 100, rep.get("装备已齐"))
check("无缺口触手", not [r for r in rep.get("行", []) if r.get("装备缺口")],
      [r["tentacle"] for r in rep.get("行", []) if r.get("装备缺口")][:5] or "0 根")

ros = TP.roster(n=100)
check("已立专业 100", ros.get("已立") == 100, "已立=%s 未立=%s" % (ros.get("已立"), ros.get("未立")))

con = sqlite3.connect(ROOT / "tentacle_ledger.db")
try:
    auto = con.execute("SELECT count(*) FROM tool_bay_audit WHERE grant_id='whitelist:auto' AND ok=1").fetchone()[0]
    nogr = con.execute("SELECT count(*) FROM tool_bay_audit WHERE ok=0 AND detail LIKE '%no_grant%'").fetchone()[0]
    check("装备坞有白名单自主放行记录", auto > 0, "%d 条 ok=1" % auto)
    print("      （历史 no_grant 拒绝 %d 条 = 白名单外的旧记录，说明门确实拦过）" % nogr)
except Exception as e:  # noqa: BLE001
    check("装备坞审计可读", False, "%s: %s" % (type(e).__name__, e))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 触手册子与自配装备通过（100 篇册子 · 专业/账号/装备 全 100 · 装备坞留痕）")
