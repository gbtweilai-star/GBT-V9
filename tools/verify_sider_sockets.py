# tools/verify_sider_sockets.py —— 她五面插座验收（chat/wisebase/hand 真跑 + 危险命令拒动 + 落账）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import sider_sockets as SS   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 她五面插座 验收 ==")
check("五个插座都在", set(SS.status()["插座"]) == {"chat", "code", "hand", "create", "wisebase"}, SS.status()["插座"])
c = SS.chat("用一句话说明你已就位")
check("chat 真答（云脑或本地）", bool(c.get("ok")) and bool(c.get("字")),
      "谁答的=%s 秒=%s 字=%s" % (c.get("谁答的"), c.get("秒"), (c.get("字") or "")[:40]))
w = SS.wisebase("视觉钉死是什么")
check("wisebase 先查记忆再答（有依据）", w.get("命中", 0) > 0,
      "命中=%s 依据=%s" % (w.get("命中"), str(w.get("依据"))[:50]))
h = SS.hand("python --version")
check("hand 真跑命令（退码如实）", h.get("ok") is True and h.get("码") == 0,
      "码=%s 出字=%s" % (h.get("码"), (h.get("出字") or "").strip()[:20]))
h2 = SS.hand("whoami")
check("越界命令拒动（准入名单）", h2.get("拒动") is True, str(h2.get("原因"))[:60])
led = ROOT / "state" / "sider_sockets.jsonl"
check("落账", led.is_file() and led.stat().st_size > 0, "%s %s B" % (led.name, led.stat().st_size if led.is_file() else 0))
print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 她五面插座通过（chat 真答 · wisebase 有依据 · hand 真跑 · 越界拒动 · 落账）")
