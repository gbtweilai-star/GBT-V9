# tools/verify_plug.py —— 万能插执行器验收器（证明"插上并驱动"真的接线了）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import pulse as PL   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 万能插执行器验收 ==")
check("plug_and_run 已导出", "plug_and_run" in PL.__all__, PL.__all__)

r = PL.plug_and_run("py:print(6*7)", kind="process")
check("进程插座真跑出结果", r.get("ok") and r.get("stdout", "").strip() == "42",
      "退出码 %s · stdout %r" % (r.get("退出码"), r.get("stdout")))

rf = PL.plug_and_run("state/panel_current.json", kind="file", action="read")
check("文件插座真读回", rf.get("ok") and "port" in (rf.get("读回") or ""),
      (rf.get("读回") or "")[:40].replace(chr(10), " "))

p = PL.Pulse()
p.plug(PL.Socket(kind="process", target="py:print(1+2)"))
d = p.dispatch("py:print(1+2)", {"action": "run"})
check("dispatch 不再是空壳（真执行）", d.get("ok") and (d.get("结果") or {}).get("stdout", "").strip() == "3",
      "stdout %r" % (d.get("结果") or {}).get("stdout"))
check("dispatch 未插上会如实拒绝", not p.dispatch("no-such-socket", {})["ok"],
      p.dispatch("no-such-socket", {}).get("error"))

# 面板口存在性（静态检查路由被注册）
pp = (ROOT / "panel" / "pulse_page.py").read_text(encoding="utf-8")
for route in ('"/api/pulse/run"', '"/api/pulse/plug"', '"/api/pulse/sockets"'):
    check("面板口 " + route, route in pp, "在 pulse_page.py")

# 台账：驱动要落账（真实路径取自 make_ledger，不靠猜）
from audit.ledger_factory import make_ledger   # noqa: E402
_l = make_ledger()
_p = Path(getattr(_l, "path", ""))
_cands = [_p, ROOT / _p, ROOT / "state" / _p]
_f = next((c for c in _cands if c.is_file()), None)
check("审计库在（驱动落账）", _f is not None,
      "%s · %.1f MB" % ((str(_f.resolve()) if _f else str(_p)), (_f.stat().st_size / 1048576 if _f else 0)))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 万能插执行器通过（plug_and_run/dispatch/面板口 全接通，真执行有读数）")
