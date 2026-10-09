# core/memory/heat.py —— 热度与分层：什么留在身边，什么自然飘远
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 蒸馏自 TheBrain 的 §06 分层引擎，但**按我们自己的口径重写**（纯函数、可测、零依赖）：
#   · 热度 = 衰减 × 用过的加成 × 重要度加成；用一次就回暖，久不用就降温；
#   · 分层（hot/warm/cold/frozen）带**迟滞**：升层容易、降层要真掉下去才降，
#     否则一个刚好卡在边界上的记忆会来回抖；
#   · 最关键的一条纪律：**褪色只让它更难被撞见，绝不删除**。删除是另一条路
#     （回收站 + 宽限期），必须由人显式发起。
import time

HALF_LIFE_DAYS = 4.0                  # 半衰期（天）：用过之后下一次能"记住"多久
TIER_EDGES = (("hot", 0.60), ("warm", 0.30), ("cold", 0.10))   # >= 阈值即该层
HYSTERESIS = 0.15                     # 迟滞量：已在某层就别因一点回落掉出去
TIERS = ("hot", "warm", "cold", "frozen")


def decay(dt_days: float, *, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """纯衰减：每过一个半衰期热度减半。dt<=0 视为不过期。"""
    if dt_days <= 0:
        return 1.0
    hl = max(0.05, float(half_life_days or HALF_LIFE_DAYS))
    return float(0.5 ** (dt_days / hl))


def heat_of(*, last_used: float, hits: int = 0, importance: float = 0.5,
            now: float | None = None, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """算一个记忆此刻的热度（0~2）。

    · 基础：从最后一次"被用到"（召回/编辑）算起做衰减；
    · 用到过的次数给一点加成 —— 常用的自然靠前；
    · 重要度只是**乘数**，不能替代使用：重要的东西久不看也会冷下去（这才像真遗忘）。
    """
    now = time.time() if now is None else now
    dt_days = max(0.0, (now - float(last_used or now)) / 86400.0)
    base = decay(dt_days, half_life_days=half_life_days)
    use_boost = 1.0 + min(0.5, 0.05 * max(0, int(hits or 0)))
    imp = 0.6 + 0.8 * min(1.0, max(0.0, float(importance if importance is not None else 0.5)))
    return round(min(2.0, base * use_boost * imp), 4)


def tier_of(h: float, *, prev: str | None = None, hysteresis: float = HYSTERESIS) -> str:
    """热度 → 层；带迟滞（已在某层，要**明显**掉下去才降层）。

    先看"原来那层还守不守得住"：只要 h 还在原层阈值减去迟滞量之上，就留在原层 ——
    否则一个刚好卡在边界上的记忆会来回抖（升一层、降一层、再升…）。
    """
    if prev in TIERS:
        for name, edge in TIER_EDGES:
            if name == prev and h >= edge - hysteresis:
                return name
    for name, edge in TIER_EDGES:
        if h >= edge:
            return name
    return "frozen"


def used(mem: dict, *, hits_delta: int = 1, now: float | None = None) -> dict:
    """"被用到"：更新命中次数与最后使用时间（不动原文）。返回要写回的字段。"""
    now = time.time() if now is None else now
    hits = int(mem.get("hits") or 0) + max(0, int(hits_delta))
    importance = mem.get("importance")
    h = heat_of(last_used=now, hits=hits, importance=importance, now=now)
    return {"hits": hits, "last_used": now, "heat": h,
            "tier": tier_of(h, prev=str(mem.get("tier") or ""))}


def recompute(mem: dict, *, now: float | None = None) -> dict:
    """**只重算**热度/分层（不动 last_used、不动 hits）。

    真踩过：重算如果走 used()，会把 last_used 刷成"现在"，于是再怎么放着不用，
    热度永远回到 1.0 —— 分层引擎就等于没开。重算必须从原有的 last_used 算起。
    """
    now = time.time() if now is None else now
    h = heat_of(last_used=float(mem.get("last_used") or now), hits=mem.get("hits"),
                importance=mem.get("importance"), now=now)
    return {"heat": h, "tier": tier_of(h, prev=str(mem.get("tier") or ""))}


def explain(mem: dict, *, now: float | None = None) -> dict:
    """给面板看的解释：为什么它在前面 / 为什么飘远了 —— 给算式，不写形容词。"""
    now = time.time() if now is None else now
    lu = float(mem.get("last_used") or now)
    dt_days = max(0.0, (now - lu) / 86400.0)
    importance = mem.get("importance")
    h = heat_of(last_used=lu, hits=mem.get("hits"), importance=importance, now=now)
    imp = 0.6 + 0.8 * min(1.0, max(0.0, float(importance if importance is not None else 0.5)))
    return {"热度": h, "层": tier_of(h, prev=str(mem.get("tier") or "")),
            "距上次使用（天）": round(dt_days, 2), "用到次数": int(mem.get("hits") or 0),
            "重要度": float(importance if importance is not None else 0.5),
            "算式": f"衰减 {decay(dt_days):.3f} × 使用加成 "
                    f"{1.0 + min(0.5, 0.05 * int(mem.get('hits') or 0)):.2f} × 重要度 {imp:.2f}",
            "口径": "褪色只让它更难被撞见，**从不删除**"}


__all__ = ["HALF_LIFE_DAYS", "TIERS", "TIER_EDGES", "HYSTERESIS", "decay", "heat_of",
           "tier_of", "used", "recompute", "explain"]
