# core/proactive.py —— 主动汇报：四类发现 + 四维打分 + 不打扰门（V9 自主层）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用途：她**不用等被问**，自己把该说的事说出来；但"能说"不等于"该说"，所以每一条都要过门：
#   · 四类：新奇发现 / 主人需要 / 记忆命中 / 赚钱机会
#   · 四维打分：相关 / 紧急 / 可信 / 价值（0..1）→ 综合分
#   · 不打扰门：静默时段（默认 01:00–07:00）· 预算（每小时最多几条）· 去重（同主题只留最新）· 及格线
# 纪律：过门才落账；被门挡下的要**如实记"为什么没说"**，不许假装没事（否则"主动"会变成骚扰或静默）。
from core.swallow import swallow as _swallow
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEED = ROOT / "state" / "proactive.jsonl"
QUIET = (1, 7)                # 静默时段：01:00–07:00（本地时间）
MIN_SCORE = 0.55              # 及格线
HOURLY_BUDGET = 3             # 每小时最多打扰几次
KINDS = ("新奇发现", "主人需要", "记忆命中", "赚钱机会")


def _now() -> float:
    return time.time()


def _append(rec: dict) -> None:
    FEED.parent.mkdir(parents=True, exist_ok=True)
    with FEED.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _rows() -> list:
    if not FEED.is_file():
        return []
    out = []
    for line in FEED.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:                                      # noqa: BLE001
            continue
    return out


def score(*, 相关: float, 紧急: float, 可信: float, 价值: float) -> float:
    """四维打分 → 综合分（紧急与价值权重更高：能说的事要"值得打断"）。"""
    r = max(0.0, min(1.0, float(相关)))
    u = max(0.0, min(1.0, float(紧急)))
    c = max(0.0, min(1.0, float(可信)))
    v = max(0.0, min(1.0, float(价值)))
    return round(0.20 * r + 0.30 * u + 0.20 * c + 0.30 * v, 4)


def gate(*, 综合分: float, kind: str, key: str = "", now: float | None = None,
         quiet: tuple = QUIET, budget: int = HOURLY_BUDGET,
         min_score: float = MIN_SCORE) -> dict:
    """不打扰门：静默时段 / 预算 / 及格线 / 去重。返回能不能说 + 为什么。"""
    t = now if now is not None else _now()
    lt = time.localtime(t)
    if kind not in KINDS:
        return {"过门": False, "拦下": f"不属于四类之一：{kind}", "四类": list(KINDS)}
    if 综合分 < float(min_score):
        return {"过门": False, "拦下": f"没到及格线（{综合分} < {min_score}）"}
    if quiet and quiet[0] <= lt.tm_hour < quiet[1]:
        return {"过门": False, "拦下": f"静默时段 {quiet[0]:02d}:00–{quiet[1]:02d}:00"}
    rows = _rows()
    hour_ago = t - 3600
    said = [r for r in rows if float(r.get("at") or 0) >= hour_ago and r.get("说了")]
    if len(said) >= int(budget):
        return {"过门": False, "拦下": f"这一小时已经打扰 {len(said)} 次（上限 {budget}）"}
    if key:
        seen = [r for r in rows if r.get("key") == key and r.get("说了")]
        if seen and float(seen[-1].get("at") or 0) >= t - 6 * 3600:
            return {"过门": False, "拦下": f"同主题 6 小时内已经说过了（key={key}）"}
    return {"过门": True, "拦下": ""}


def consider(说: str, *, kind: str, 相关: float = 0.6, 紧急: float = 0.5,
             可信: float = 0.8, 价值: float = 0.6, key: str = "",
             evidence: str = "", by: str = "main") -> dict:
    """考虑要不要说：打分 → 过门 → 落账（被拦下也如实记，注明原因）。"""
    s = score(相关=相关, 紧急=紧急, 可信=可信, 价值=价值)
    g = gate(综合分=s, kind=kind, key=key)
    rec = {"at": _now(), "kind": kind, "说": str(说)[:400], "key": str(key),
           "综合分": s, "四维": {"相关": 相关, "紧急": 紧急, "可信": 可信, "价值": 价值},
           "证据": str(evidence)[:300], "by": by,
           "说了": bool(g["过门"]), "拦下": g.get("拦下", "")}
    _append(rec)
    if g["过门"]:
        try:                                    # 说出去的同时在地上留个痕迹（去重靠它）
            from core import stigmergy as S
            S.deposit("汇报", {"说": rec["说"], "类别": kind}, tags=(kind,),
                      strength=min(1.0, s + 0.2), by=by, key=key or "", evidence=evidence)
        except Exception as e:
            _swallow(__file__, e)
    return {"说": g["过门"], "综合分": s, "拦下": g.get("拦下", ""), "类别": kind}


def feed(*, limit: int = 20, only_said: bool = True) -> list:
    rows = _rows()
    if only_said:
        rows = [r for r in rows if r.get("说了")]
    rows.sort(key=lambda r: -float(r.get("at") or 0))
    return rows[:max(1, int(limit))]


def scan_once() -> dict:
    """扫一遍该主动说的事（心跳调用）：从**真读数**里挑，不编。

    现在的三条真信号：① 待办/承诺里快到期的；② 闭环验器发现断线的；③ 云上额度快见底。
    """
    said, blocked = [], []
    # ① 记忆里的待办/承诺（原生大脑统一库）
    try:
        from core.memory import recall as R
        got = R.search("待办 承诺 明天 交", limit=3)
        for hit in (got or [])[:2]:
            r = consider(f"记忆里有一件快到期的：{str(hit)[:80]}", kind="记忆命中",
                         相关=0.8, 紧急=0.6, 可信=0.7, 价值=0.6,
                         key="记忆待办", evidence=str(hit)[:120])
            (said if r["说"] else blocked).append(r)
    except Exception as e:
        _swallow(__file__, e)
    # ② 闭环断线
    try:
        from core import loop_verifier as LV
        lv = LV.status() if hasattr(LV, "status") else {}
        bad = lv.get("断线") or lv.get("broken") or []
        if bad:
            r = consider(f"闭环有 {len(bad)} 处断线，需要看一眼", kind="新奇发现",
                         相关=0.9, 紧急=0.7, 可信=0.9, 价值=0.7,
                         key="闭环断线", evidence=str(bad)[:200])
            (said if r["说"] else blocked).append(r)
    except Exception as e:
        _swallow(__file__, e)
    # ③ 云上额度
    try:
        from core import tripo as T
        st = T.status()
        bal = st.get("余额")
        if isinstance(bal, (int, float)) and bal < 100:
            r = consider(f"云上额度只剩 {bal}，四视图重建要 65，需要充值",
                         kind="主人需要", 相关=1.0, 紧急=0.8, 可信=1.0, 价值=0.8,
                         key="云额度", evidence=f"余额={bal}")
            (said if r["说"] else blocked).append(r)
    except Exception as e:
        _swallow(__file__, e)
    return {"主动说了": said, "被门拦下": blocked,
            "口径": "四类汇报 + 四维打分 + 不打扰门；被拦下也留痕（不假装没事）"}


def status() -> dict:
    rows = _rows()
    said = [r for r in rows if r.get("说了")]
    return {"账本": str(FEED), "总数": len(rows), "说过": len(said),
            "四类": list(KINDS), "静默时段": f"{QUIET[0]:02d}:00–{QUIET[1]:02d}:00",
            "每小时上限": HOURLY_BUDGET, "及格线": MIN_SCORE,
            "最近": feed(limit=5)}


__all__ = ["KINDS", "score", "gate", "consider", "feed", "scan_once", "status"]
