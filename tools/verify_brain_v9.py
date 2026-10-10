import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_brain_v9.py —— 她的脑子验收（Agnes 云脑优先 + 本地兜底如实回落 + key 不入仓）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import os, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import brain_v9 as B   # noqa: E402

FAIL = []


# 排除验收器自身：它内部含有"要找的密钥前缀"作判据，自匹配不是泄露
SELF = __file__


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 她的脑子 验收 ==")
st = B.status()
check("脑子状态可读（配了/没配都如实）", "Agnes" in st, st["Agnes"])
r = B.think("回两个字：在")
check("能说话（云脑或本地兜底至少一条通）", bool(r.get("ok")),
      "谁答的=%s 后端=%s 秒=%s" % (r.get("谁答的"), r.get("后端"), r.get("秒")))
old = os.environ.get("AGNES_API_KEY")
os.environ["AGNES_API_KEY"] = "bad-key-for-test"
r2 = B.think("测试回落")
if old is None:
    os.environ.pop("AGNES_API_KEY", None)
else:
    os.environ["AGNES_API_KEY"] = old
check("坏 key 时如实回落本地（不空手、不假装）", bool(r2.get("ok")) and r2.get("谁答的") == "local",
      "谁答的=%s" % r2.get("谁答的"))
keys = ROOT / "state" / "keys.env"
import subprocess
# 搜**真实密钥前缀**（不是变量名；变量名会命中验收器自己）
hit = subprocess.run("git grep -l sk-6IjZl", shell=True, capture_output=True, text=True,
                     encoding="utf-8", errors="replace", cwd=str(ROOT)).stdout.strip()
check("密钥不入仓（已跟踪文件里搜不到）", hit == "", "命中文件: %s" % (hit or "无"))
led = ROOT / "state" / "brain_v9.jsonl"
check("落账（谁答的写进账）", led.is_file() and led.stat().st_size > 0,
      "%s %s B" % (led.name, led.stat().st_size if led.is_file() else 0))
print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 她的脑子通过（云脑或本地真答 · 坏 key 如实回落 · key 不入仓 · 落账）")
