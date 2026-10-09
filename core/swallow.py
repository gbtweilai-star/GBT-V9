# core/swallow.py —— 静默吞异常登记处（不许无声吞）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「把红色黄色代码全清干净，别看起来跟小白部署一样。」
# 本仓铁律反面就是 except: pass —— 出了事没人知道。本件给它们一个**有名有因**的出口：
#   swallow(位置, 异常, 备注) → 记 state/silent_swallow_audit.jsonl（文件:行 + 类型 + 消息 + 时间）
#   仍**不抛**（保持原语义：这里是容错点），但**留下痕迹**，可日报可统计。
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "silent_swallow_audit.jsonl"
_count = 0
_last = ""


def swallow(where: str, exc: BaseException | str, note: str = "") -> dict:
    global _count, _last
    _count += 1
    msg = exc if isinstance(exc, str) else "%s: %s" % (type(exc).__name__, str(exc)[:200])
    _last = "%s @ %s" % (where, msg[:80])
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "位置": where, "异常": msg, "备注": note[:120]}
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception as e:  # 登记失败：绝不静默，落 stderr（本模块内不自引用）
        import sys as _sys
        print('[swallow] 登记失败 %s: %s' % (type(e).__name__, e), file=_sys.stderr)
    return rec


def stats(limit: int = 5) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    total = 0
    if LEDGER.is_file():
        total = sum(1 for _ in LEDGER.open(encoding="utf-8"))
    return {"本次进程吞了": _count, "累计": total, "最近": rows, "最后一条": _last,
            "口径": "容错点仍不抛，但每条都留痕（可日报可统计），不再无声"}


__all__ = ["swallow", "stats", "LEDGER"]
