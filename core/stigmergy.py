# core/stigmergy.py —— 信息素场地：只追加的间接通道（存放/读取/衰减/取食）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用途（V9 自主层地基）：多个部件不必互相直接调用，把"痕迹"留在地上，别的部件自己闻着走。
#   · deposit（存放）：追加一条痕迹（kind/tags/strength/证据），**账本只追加，从不改写历史**
#   · read（读取）：按**衰减后的强度**排序返回（衰减是算出来的，不动账本）
#   · decay（衰减）：算出当前有效强度（半衰期），供别人判断"这条还新不新"
#   · forage（取食）：按强度 + 新鲜度取最该处理的若干条（自主整理时用）
# 纪律：只追加；衰减是纯函数；不带凭据、不出网、不删除任何历史。
from core.swallow import swallow as _swallow
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "stigmergy.jsonl"
HALF_LIFE_S = 3600.0          # 默认半衰期：1 小时（可被调用方覆盖）
MAX_LEDGER_LINES = 20000      # 超过就轮转到 archive（不删数据）


def _now() -> float:
    return time.time()


def _append(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 账本太长时轮转（保留历史，不丢）
    try:
        if LEDGER.stat().st_size > 4_000_000:
            arch = LEDGER.with_name("stigmergy_archive.jsonl")
            with LEDGER.open("r", encoding="utf-8") as src, arch.open("a", encoding="utf-8") as dst:
                for i, line in enumerate(src):
                    if i < 15000:
                        dst.write(line)
            keep = LEDGER.read_text(encoding="utf-8").splitlines()[15000:]
            LEDGER.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    except Exception as e:
        _swallow(__file__, e)


def _rows() -> list:
    if not LEDGER.is_file():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:                                      # noqa: BLE001 as _e_swallow
            _swallow(__file__, _e_swallow)
            continue
    return out


def effective(rec: dict, *, half_life_s: float = HALF_LIFE_S, now: float | None = None) -> float:
    """一条痕迹**当前**的有效强度（纯函数：不改账本）。"""
    t = float(rec.get("at") or 0.0)
    s = float(rec.get("strength") or 1.0)
    age = max(0.0, (now if now is not None else _now()) - t)
    if half_life_s <= 0:
        return s
    return s * math.pow(0.5, age / half_life_s)


def deposit(kind: str, payload: dict | str, *, tags: tuple = (), strength: float = 1.0,
            by: str = "main", key: str = "", evidence: str = "") -> dict:
    """存放一条痕迹。key 非空时同 key 只留最新一条有效（去重靠 key，不删旧账）。"""
    rec = {"at": _now(), "kind": str(kind), "key": str(key), "by": str(by),
           "tags": list(tags), "strength": float(max(0.0, min(1.0, strength))),
           "evidence": str(evidence)[:400],
           "payload": payload if isinstance(payload, dict) else {"说": str(payload)[:400]}}
    _append(rec)
    return {"ok": True, "痕迹": rec["kind"], "键": rec["key"], "强度": rec["strength"]}


def read(*, kind: str = "", limit: int = 50, half_life_s: float = HALF_LIFE_S,
         min_strength: float = 0.05, by: str = "") -> list:
    """按**衰减后强度**排序返回痕迹（同 key 只留最新那条）。"""
    rows = _rows()
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if by:
        rows = [r for r in rows if r.get("by") == by]
    latest: dict = {}
    plain = []
    for r in rows:
        k = r.get("key") or ""
        if k:
            cur = latest.get(k)
            if cur is None or float(r.get("at") or 0) >= float(cur.get("at") or 0):
                # 同刻的两条：后写的赢（账本顺序即时间顺序）
                latest[k] = r
        else:
            plain.append(r)
    out = []
    for r in list(latest.values()) + plain:
        eff = effective(r, half_life_s=half_life_s)
        if eff >= min_strength:
            out.append({**r, "有效强度": round(eff, 4)})
    out.sort(key=lambda x: (-x["有效强度"], -float(x.get("at") or 0)))
    return out[:max(1, int(limit))]


def decay(*, half_life_s: float = HALF_LIFE_S, floor: float = 0.02) -> dict:
    """算一遍衰减：返回"还剩多少条有效"与"已经淡掉的条数"（不改账本）。"""
    rows = _rows()
    alive = fade = 0
    for r in rows:
        if effective(r, half_life_s=half_life_s) >= floor:
            alive += 1
        else:
            fade += 1
    return {"痕迹总数": len(rows), "仍在有效": alive, "已淡出": fade,
            "半衰期s": half_life_s, "口径": "只算不删：淡出的痕迹仍留在账本里，可回放"}


def forage(*, kind: str = "", want: int = 5, half_life_s: float = HALF_LIFE_S) -> list:
    """取食：挑出当前最该处理的痕迹（强度高 + 新鲜）。"""
    got = read(kind=kind, limit=max(want * 4, 12), half_life_s=half_life_s)
    now = _now()
    got.sort(key=lambda r: (-(r["有效强度"] * (1.0 + 1.0 / (1.0 + (now - float(r.get("at") or 0)) / 600.0)))))
    return got[:max(1, int(want))]


def status() -> dict:
    rows = _rows()
    kinds: dict = {}
    for r in rows:
        kinds[r.get("kind") or "?"] = kinds.get(r.get("kind") or "?", 0) + 1
    return {"账本": str(LEDGER), "痕迹总数": len(rows), "分类": kinds,
            "半衰期s": HALF_LIFE_S, "纪律": "只追加、不删除；衰减是算出来的"}


__all__ = ["deposit", "read", "decay", "forage", "effective", "status", "LEDGER"]
