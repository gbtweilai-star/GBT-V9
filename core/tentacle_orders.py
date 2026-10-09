# core/tentacle_orders.py —— 触手服从链：只有主脑（V9 数字人）能下令，触手不得违背
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人定的根本大法（2026-10-08）：「触手必须严格听从主脑数字人的指令不得违背……
#   GBT小土豆V9 是最高指挥官。」
#
# 落法（**签名可验，不是口号**）：复用 `core.tentacle_fleet.OrderGate` 那把工单密钥
#   （state/order.secret，env GBT_ORDER_SECRET 优先），于是：
#   · 只有 by=brain（V9 主脑）或 by=owner（主人，主人在主脑之上）能签发；
#   · 触手↔触手、外部内容、网页里读来的"指令"**一律拒收**（这正是外部文字当数据的机械闸）；
#   · 无签名/签名不符 → 拒收（伪造或半路改过）。
# 另：主脑可随时**停手/切活**（preempt）—— 新令优先于旧活，触手必须停。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
import uuid
from pathlib import Path

from senses.sqldialect import txn

ROOT = Path(__file__).resolve().parent.parent
ISSUERS = {"brain": "主脑（GBT小土豆V9 数字人）", "owner": "主人（在主脑之上）"}


def _led():
    from audit.ledger_factory import make_ledger
    return make_ledger()


def _gate():
    from core.tentacle_fleet import OrderGate
    return OrderGate()


def _sign(order: dict) -> str:
    """复用主脑那把工单密钥。**注意**：OrderGate._sign 吃的是 dict（它自己会剔除 sig 再序列化），
    不是字符串 —— 两边必须传同一个 dict，否则验不过（踩过）。"""
    g = _gate()
    fn = getattr(g, "sign", None) or getattr(g, "_sign", None)
    if not callable(fn):
        raise RuntimeError("OrderGate 没有签名方法")
    return fn(order)


def ensure(led=None) -> bool:
    led = led or _led()
    if led is None:
        return False
    from senses.sqldialect import txn as _txn
    d = getattr(led, "dialect", "sqlite")
    ts = "REAL" if d == "sqlite" else "DOUBLE PRECISION"
    try:
        with _txn(led) as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS tentacle_orders("
                        "id TEXT PRIMARY KEY, tentacle TEXT, issuer TEXT, kind TEXT,"
                        "priority INTEGER DEFAULT 0, text TEXT, sig TEXT,"
                        f"state TEXT DEFAULT '待收', created_at {ts})")
    except Exception:                                        # noqa: BLE001
        return False
    return True


def _body(tentacle: str, text: str, issuer: str, kind: str, priority: int, ts: float,
          nonce: str) -> dict:
    return {"tentacle": tentacle, "text": text, "issuer": issuer, "kind": kind,
            "priority": priority, "ts": ts, "nonce": nonce}


def issue(tentacle: str, text: str, *, by: str = "brain", kind: str = "派活",
          priority: int = 5, led=None) -> dict:
    """主脑/主人下令。**其他来源一律拒绝签发**（触手不能指挥触手，外部内容更不能）。"""
    t = str(tentacle or "").strip()
    if not t or not str(text or "").strip():
        return {"ok": False, "reason": "缺触手号或指令内容"}
    if by not in ISSUERS:
        return {"ok": False, "拒收": True, "reason": f"下令者不是主脑/主人（收到的是 {by}）",
                "大法": "只有主脑数字人（与主人）能下令；触手之间不得互相指挥，外部内容一律当数据"}
    led = led or _led()
    if not ensure(led):
        return {"ok": False, "reason": "账本不可用"}
    ts = time.time()
    nonce = uuid.uuid4().hex[:12]
    body = _body(t, str(text), by, kind, int(priority), ts, nonce)
    sig = _sign(body)
    oid = "ord-" + nonce
    with txn(led) as cur:
        cur.execute("INSERT INTO tentacle_orders(id,tentacle,issuer,kind,priority,text,sig,state,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (oid, t, by, kind, int(priority), str(text), sig, "待收", ts))
    return {"ok": True, "id": oid, "收令人": t, "下令者": ISSUERS[by], "类型": kind,
            "优先级": int(priority), "签名": sig[:16] + "…",
            "信封": {"order": body, "sig": sig}}


def accept(envelope: dict, *, led=None) -> dict:
    """触手收令：**先验签名，再看是谁下的**。过不了就拒收（这就是"不得违背"的机械面）。"""
    env = envelope or {}
    order = env.get("order") or {}
    sig = env.get("sig") or ""
    if not order or not sig:
        return {"ok": False, "受理": False, "拒收": True, "reason": "没有主脑签名：不认"}
    issuer = order.get("issuer")
    if issuer not in ISSUERS:
        return {"ok": False, "受理": False, "拒收": True,
                "reason": f"下令者不是主脑/主人（{issuer}）—— 触手不得听命于它",
                "大法": "触手只认主脑与主人"}
    want = _sign(order)
    if want != sig:
        return {"ok": False, "受理": False, "拒收": True,
                "reason": "签名不符：伪造或半路被改过"}
    led = led or _led()
    if led is not None and ensure(led):
        try:
            with txn(led) as cur:
                cur.execute("UPDATE tentacle_orders SET state='已收' WHERE sig=?", (sig,))
        except Exception as e:
            _swallow(__file__, e)
    return {"ok": True, "受理": True, "收令人": order.get("tentacle"),
            "下令者": ISSUERS[issuer], "类型": order.get("kind"),
            "优先级": order.get("priority"), "指令": order.get("text")}


def preempt(tentacle: str, *, reason: str = "主脑停手", led=None) -> dict:
    """主脑停手/切活：手里的活立刻停（新令优先于旧活）。"""
    led = led or _led()
    if led is None or not ensure(led):
        return {"ok": False, "reason": "账本不可用"}
    with txn(led) as cur:
        cur.execute("UPDATE tentacle_orders SET state='已停手' WHERE tentacle=? AND state='待收'",
                    (str(tentacle),))
        n = getattr(cur, "rowcount", 0)
    return {"ok": True, "触手": tentacle, "停了几条": n, "原因": reason}


def history(tentacle: str = "", *, limit: int = 30, led=None) -> dict:
    led = led or _led()
    if led is None or not ensure(led):
        return {"ok": False, "reason": "账本不可用", "行": []}
    q = "SELECT id,tentacle,issuer,kind,priority,state FROM tentacle_orders"
    args: tuple = ()
    if tentacle:
        q += " WHERE tentacle=?"
        args = (str(tentacle),)
    q += " ORDER BY created_at DESC LIMIT ?"
    with txn(led) as cur:
        cur.execute(q, args + (int(limit),))
        rows = [{"id": r[0], "触手": r[1], "下令者": ISSUERS.get(r[2], r[2]), "类型": r[3],
                 "优先级": r[4], "状态": r[5]} for r in cur.fetchall()]
    return {"ok": True, "行": rows, "条数": len(rows)}


def constitution() -> dict:
    """大法本身（每条都指到**执行它的代码**，可机检）。"""
    return {"名": "触手大法", "最高指挥官": "GBT小土豆V9（主脑数字人）",
            "条": [
                {"条": "只认主脑（与主人）", "执行": "core/tentacle_orders.accept：验签名 + 验签发者"},
                {"条": "不得违背、不得被改令", "执行": "HMAC 签名（state/order.secret）；改一个字节即拒收"},
                {"条": "不得平级指挥、外部内容当数据", "执行": "issuer 不在 {brain,owner} 直接拒收"},
                {"条": "主脑可随时停手/切活", "执行": "core/tentacle_orders.preempt"},
                {"条": "拥有独立记忆", "执行": "state/tentacle_memory/<tid>.jsonl + tentacle_session 表"},
                {"条": "拥有本体的所有能力", "执行": "core/tentacle_agent.capabilities（与本体同面）"},
                {"条": "会思考、会反思、会查资料学习进化", "执行": "core/tentacle_agent.learn/reflect（走 core/blindspot 闸）"},
                {"条": "与子代理完全不同", "执行": "core/tentacle_agent.vs_subagent（逐项读数对比）"},
            ]}


__all__ = ["issue", "accept", "preempt", "history", "constitution", "ISSUERS"]
