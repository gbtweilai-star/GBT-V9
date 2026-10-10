# tools/verify_cross_process_grant.py —— 跨进程令牌验收（真起子进程签、父进程验）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 真机病因（2026-10-08）：签名密钥原先「没 env 就进程内临时生成」⇒ 面板/触手两个进程各一把，
#   主人签了授权却报「未授权」（假未授权）。本验收用**真跨进程**证明已经共用同一把密钥。
# 判据：① 密钥来源是 env/file（不是 memory）；② 子进程签的授权令牌父进程验得过；
#       ③ 子进程签的工单父进程验得过；④ 篡改过的令牌仍然验不过（闸门没松）。
# 用法：python tools/verify_cross_process_grant.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAILS: list = []


def check(name: str, cond: bool, reading: str) -> None:
    print("  %s %s —— %s" % ("✅" if cond else "❌", name, reading))
    if not cond:
        FAILS.append(name)


def _child(code: str) -> str:
    """真的另起一个进程跑（不是同进程模拟）。"""
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180)
    if r.returncode != 0:
        return "__ERR__" + (r.stderr or "")[-300:]
    return (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""


def main() -> int:
    print("== 跨进程令牌验收 @", ROOT, "==")
    from core.local_secret import local_secret, is_persistent
    from core.gui_grant import Grant, ephemeral

    s1 = local_secret("gui_grant", "GUI_GRANT_SECRET")
    check("授权签名密钥是跨进程可用的（env 或落盘）",
          s1["source"] in ("env", "file") and is_persistent("gui_grant", "GUI_GRANT_SECRET"),
          "source=%s path=%s" % (s1["source"], Path(s1["path"]).name or "（env）"))
    check("gui_grant.ephemeral() 为假（不再进程内临时）", ephemeral() is False,
          "ephemeral=%s" % ephemeral())
    s2 = local_secret("order", "GBT_ORDER_SECRET")
    check("工单签名密钥同样持久", s2["source"] in ("env", "file"),
          "source=%s path=%s" % (s2["source"], Path(s2["path"]).name or "（env）"))

    # ── 真跨进程：子进程签，父进程验 ──
    tok = _child("from core.gui_grant import issue; print(issue(primitives=['gui'], note='跨进程验收'))")
    check("子进程签得出授权令牌", bool(tok) and not tok.startswith("__ERR__"),
          (tok[:36] + "…") if tok and not tok.startswith("__ERR__") else tok[-160:])
    ok, why, _ = Grant.verify(tok if tok else None)
    check("**父进程验得过子进程签的令牌**（原先必失败）", ok, "verify=%s/%s" % (ok, why))
    ok2, why2, _ = Grant.verify_action(tok, primitive="gui")
    check("原语范围校验也过（primitive=gui）", ok2, "%s" % why2)
    bad = (tok[:-2] + ("00" if tok[-2:] != "00" else "11")) if tok and not tok.startswith("__ERR__") else ""
    ok3, why3, _ = Grant.verify(bad)
    check("篡改过的令牌仍然验不过（闸门没松）", ok3 is False, "%s" % why3)

    order_txt = _child("import json; from core.tentacle_fleet import OrderGate; "
                       "print(json.dumps(OrderGate().issue('t001','跨进程工单')))")
    order = None
    try:
        order = json.loads(order_txt)
    except Exception:                                             # noqa: BLE001
        order = None
    check("子进程签得出工单", isinstance(order, dict) and bool(order.get("sig")),
          "order_id=%s" % ((order or {}).get("order_id") or order_txt[-160:]))
    from core.tentacle_fleet import OrderGate
    v_ok, v_why = OrderGate().verify(order, tentacle_id="t001")
    check("**父进程验得过子进程签的工单**", v_ok, "verify=%s/%s" % (v_ok, v_why))
    v_bad = OrderGate().verify({**(order or {}), "sig": "deadbeef"}, tentacle_id="t001")
    check("篡改工单仍然验不过", v_bad[0] is False, "%s" % v_bad[1])

    print("\n说明：密钥落 state/（.gitignore 已挡），env 仍优先；**面板要重启**才会用上新密钥。")
    print("结论：" + ("✅ 跨进程令牌已通（假未授权修掉）" if not FAILS
                     else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
