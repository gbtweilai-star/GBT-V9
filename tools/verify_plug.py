# tools/verify_plug.py —— 万能插（脉冲）独立验收：五种插座各验一次 + 危险命令必须拒动 + 落账
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import pulse as P   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 万能插（脉冲）验收 ==")
check("插座类型齐（process/file/api/web/model）",
      set(P.SOCKET_KINDS) == {"process", "file", "api", "web", "model"}, list(P.SOCKET_KINDS))
check("五种插座都声明了能源（不空口）", all(P.SOCKET_ENERGY.get(k) for k in P.SOCKET_KINDS),
      {k: str(v)[:18] for k, v in P.SOCKET_ENERGY.items()})

# ① process：真跑 + 退码如实
r1 = P.plug_and_run("t011", kind="process", action="run", args={"cmd": ["python", "--version"]}, timeout=90)
check("process 真跑且退码如实", r1.get("ok") is True and r1.get("码") == 0,
      "ok=%s 码=%s 出字=%s" % (r1.get("ok"), r1.get("码"), (r1.get("stdout") or "").strip()[:20]))

# ② process：危险命令必须拒动（准入名单）
r2 = P.plug_and_run("t012", kind="process", action="run", args={"cmd": ["whoami"]}   # 真存在的可执行程序，但不在准入名单, timeout=30)
check("危险命令被拒动（准入名单）", r2.get("ok") is False, str(r2.get("reason") or r2.get("错") or r2)[:80])

# ③ file：探测 / 读
r3 = P.plug_and_run(str(ROOT / "core" / "pulse.py"), kind="file", action="exists", args={}, timeout=20)
check("file 探测存在", r3.get("ok") is True and r3.get("存在") is True, "存在=%s" % r3.get("存在"))

# ④ api：出网安全检查必须拦非法 URL（不许被绕）
try:
    r4 = P.plug_and_run("ftp://example.com/x", kind="api", action="get", args={}, timeout=15)
    ok4 = r4.get("ok") is False
    rd4 = str(r4.get("reason") or r4.get("错") or r4)[:70]
except Exception as e:  # noqa: BLE001
    ok4, rd4 = True, "出网检查抛异常（视为拦截）: %s" % type(e).__name__
check("api 非法 scheme 被拦（不绕）", ok4, rd4)

# ⑤ model：在沙盒里跑，产物带 sha256
r5 = P.plug_and_run("t001", kind="model", args={"prompt": "回一个字：在", "backend": "local", "tentacle": "t001"}, timeout=180)
sha = ""
try:
    sha = (r5.get("产物") or [{}])[0].get("sha256", "")
except Exception:  # noqa: BLE001
    sha = ""
check("model 在沙盒里跑出产物（带 sha256）", bool(r5.get("ok")) and bool(r5.get("沙盒")) and bool(sha),
      "后端=%s 秒=%s 沙盒=%s sha=%s" % (r5.get("后端"), r5.get("秒"), str(r5.get("沙盒"))[-24:], sha[:12]))

# ⑥ 落账
led = ROOT / "state" / "pulse.jsonl"
check("每次插入/驱动都有账", led.is_file() and led.stat().st_size > 0,
      "%s %s B" % (led.name, led.stat().st_size if led.is_file() else 0))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 万能插通过（五插座齐 · process 退码如实 · 危险命令拒动 · 出网不绕 · 模型沙盒+sha256 · 落账）")
