# tools/verify_capability_wiring.py —— 能力接线验收（非 LLM 判据，可复跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 背景（2026-10-08 真机病因，逐行复核）：/api/panel/capabilities 从来只回 error 信封 ——
#   ① server.py:1384 注入 CapRegistry(53)，panel_api 却读 reg.skills/reg.brain；
#   ② 同一文件顶部把 NATIVE_CAPABILITIES 别名成 NATIVE_POTATO_CAPS，函数里却写原名 ⇒ NameError；
#   ③ 列表只遍历原生 9 条，53 项 Octop 离线能力从未上表；
#   ④ capability.html 轮询的两个接口全仓不存在。
# 本验收器把「修好了」变成可判定读数：跑一次，红了就是没修好。
#
# 用法：python tools/verify_capability_wiring.py
# 退出码：0 = 全过；1 = 有断言不过。
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import asyncio
import json
import py_compile
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, cond: bool, reading: str) -> None:
    print("  %s %s —— %s" % ("✅" if cond else "❌", name, reading))
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("== 能力接线验收 @", ROOT, "==")
    for rel in ("panel/panel_api.py", "panel/capability_page.py"):
        py_compile.compile(str(ROOT / rel), doraise=True, cfile=str(ROOT / rel) + ".chk")
    for j in ROOT.rglob("*.chk"):
        j.unlink(missing_ok=True)
    check("编译", True, "panel_api.py / capability_page.py 语法 OK")

    from audit.ledger_factory import make_ledger
    from skills.caps.adapter import as_skill_registry
    from skills.caps.registry import build_caps_registry
    import panel.panel_api as pa
    import panel.capability_page as cp

    led = make_ledger()
    # 生产接线（server.py:1386-1389）：原始能力表 → 适配器 → 注入 router
    reg = as_skill_registry(build_caps_registry(ledger=led), ledger=led)
    pa.router.ledger = led
    pa.router.registry = reg

    caps = pa.capabilities()
    rows = caps["data"] or []
    octop = [r for r in rows if r.get("source") == "octop-offline"]
    native = [r for r in rows if r.get("source") == "native"]
    check("/api/panel/capabilities 无错误", caps["error"] is None,
          "error=%r" % caps["error"])
    check("Octop 离线能力 53 项全上表", len(octop) == len(reg.caps) >= 50,
          "octop=%d / 注册表=%d" % (len(octop), len(reg.caps)))
    check("原生能力 5 项也在表内", len(native) >= 5, "native=%d" % len(native))

    topo = pa.topology()
    dd = topo["data"] or {}
    n_octop = sum(1 for n in dd.get("nodes", []) if n.get("group") == "octop")
    check("/api/panel/topology 不炸", topo["error"] is None, "error=%r" % topo["error"])
    check("拓扑含 53 个 Octop 节点", n_octop >= 50, "octop 组节点=%d" % n_octop)

    def _calls() -> int:
        import sqlite3
        try:
            return sqlite3.connect(str(ROOT / "tentacle_ledger.db")).execute(
                "SELECT COUNT(*) FROM skill_calls").fetchone()[0]
        except Exception:
            return -1

    before = _calls()
    ids = list(reg.caps)
    pick = next((i for i in ids if ("dry" in i or "fixture" in i)), ids[0])
    r = pa.capability_run(pick, {"probe": True})
    got = r["data"] or {}
    check("invoke 门可用（真跑一条能力）", r["error"] is None and got.get("ok") is True,
          "%s -> ok=%s" % (pick, got.get("ok")))
    after = _calls()
    check("调用落账（skill_calls 增行）", after == before + 1,
          "%d -> %d" % (before, after))

    cp._led = lambda: led
    ov = asyncio.run(cp.api_capability_overview())
    check("/api/capability/overview 可用", ov.get("error") is None,
          "caps_total=%s native=%s health=%d" % (
              ov.get("caps_total"), ov.get("native_total"), len(ov.get("health", {}))))
    cl = asyncio.run(cp.api_capability_calls(5))
    check("/api/capability/calls 可用", isinstance(cl, list) and len(cl) >= 1,
          "行数=%d" % len(cl))

    print("\n结论：" + ("✅ 能力接线全过" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
