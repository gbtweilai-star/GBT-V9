# core/sched.py —— 心跳调度：定时器的定义 / 到期扫描 / 推进 / 状态（V9 自主层）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用途：让"空闲时自主整理"有节奏。定义一件按秒/按分钟循环的事，tick 只推进**到期的**那些，
#       每件事都落在账本里（上次跑、下次跑、结果），不看"进程还活着"当成功。
# 纪律：① 不新增常驻进程 —— tick 由面板的身体服务/计划任务驱动即可；
#       ② 到点没跑要**如实记迟到**，不许悄悄顺延还不留痕；
#       ③ 处理器名必须是**在册的**（未知处理器拒收，不装作跑了）。
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "state" / "sched.json"
LOG = ROOT / "state" / "sched_log.jsonl"


def _now() -> float:
    return time.time()


def _load() -> dict:
    if not JOBS.is_file():
        return {"jobs": {}}
    try:
        d = json.loads(JOBS.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"jobs": {}}
        d.setdefault("jobs", {})
        return d
    except Exception:                                          # noqa: BLE001
        return {"jobs": {}}


def _save(d: dict) -> None:
    JOBS.parent.mkdir(parents=True, exist_ok=True)
    JOBS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _log(rec: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── 在册处理器：名字 → 真动作（tick 只认这里有的名字）──
def _h_stigmergy_decay() -> dict:
    from core import stigmergy as S
    return S.decay()


def _h_mirror_digest() -> dict:
    from core import mirror as M
    return M.status()


def _h_longrun_heartbeat() -> dict:
    from core import longrun as L
    return L.heartbeat()


def _h_proactive_scan() -> dict:
    from core import proactive as P
    return P.scan_once()


def _h_hub_warm() -> dict:
    from core import console_hub as H
    return H.warm()


HANDLERS = {
    "hub_warm": _h_hub_warm,
    "stigmergy_decay": _h_stigmergy_decay,
    "mirror_digest": _h_mirror_digest,
    "longrun_heartbeat": _h_longrun_heartbeat,
    "proactive_scan": _h_proactive_scan,
}


def define(name: str, every_s: float, *, handler: str, note: str = "",
           first_delay_s: float = 0.0) -> dict:
    """定义/更新一件事。handler 必须是**在册的**（否则拒收，不冒充跑了）。"""
    h = str(handler or "").strip()
    if h not in HANDLERS:
        return {"ok": False, "reason": f"处理器不在册：{h or '（空）'}",
                "在册": sorted(HANDLERS)}
    n = str(name or "").strip()
    if not n:
        return {"ok": False, "reason": "名字不能为空"}
    every = float(every_s)
    if every < 5:
        return {"ok": False, "reason": "间隔太短（≥5 秒）：心跳不是忙等"}
    d = _load()
    now = _now()
    old = d["jobs"].get(n) or {}
    d["jobs"][n] = {"handler": h, "every_s": every, "note": str(note)[:200],
                    "next_at": now + float(first_delay_s or 0),
                    "last_at": old.get("last_at"), "last_result": old.get("last_result"),
                    "runs": int(old.get("runs") or 0), "late": int(old.get("late") or 0)}
    _save(d)
    return {"ok": True, "任务": n, "每s": every, "处理器": h,
            "下次": d["jobs"][n]["next_at"]}


def due(*, now: float | None = None) -> list:
    """列出**到期的**任务（含迟到秒数，迟到要留痕）。"""
    t = now if now is not None else _now()
    out = []
    for name, j in (_load().get("jobs") or {}).items():
        nxt = float(j.get("next_at") or 0)
        if nxt <= t:
            out.append({"任务": name, "处理器": j.get("handler"),
                        "迟到s": round(t - nxt, 1), "每s": j.get("every_s")})
    out.sort(key=lambda x: -x["迟到s"])
    return out


def tick(*, limit: int = 5, now: float | None = None) -> dict:
    """推进到期的任务：真调用处理器，结果落账本；迟到的累计迟到次数（如实记）。"""
    t = now if now is not None else _now()
    d = _load()
    ran, failed = [], []
    for item in due(now=t)[:max(1, int(limit))]:
        name = item["任务"]
        j = d["jobs"].get(name)
        if not j:
            continue
        handler = HANDLERS.get(j.get("handler"))
        if handler is None:
            failed.append({"任务": name, "原因": f"处理器下线：{j.get('handler')}"})
            continue
        t0 = _now()
        try:
            res = handler() or {}
            j["last_result"] = json.dumps(res, ensure_ascii=False)[:300]
            ran.append({"任务": name, "耗时s": round(_now() - t0, 2)})
        except Exception as exc:                               # noqa: BLE001
            j["last_result"] = f"FAIL {type(exc).__name__}: {exc}"[:300]
            failed.append({"任务": name, "原因": type(exc).__name__})
        j["last_at"] = t0
        j["runs"] = int(j.get("runs") or 0) + 1
        if item["迟到s"] > float(j.get("every_s") or 0):
            j["late"] = int(j.get("late") or 0) + 1
        # 下次时间从**本次应跑时间**顺推，避免漂移
        nxt = float(j.get("next_at") or t0)
        every = float(j.get("every_s") or 60)
        steps = 0
        while nxt <= t and steps < 10000:
            nxt += every
            steps += 1
        j["next_at"] = nxt
        _log({"at": t0, "任务": name, "处理器": j.get("handler"),
              "迟到s": item["迟到s"], "结果": j["last_result"]})
    _save(d)
    return {"ok": True, "跑了": ran, "失败": failed, "到期数": len(due(now=t))}


def status() -> dict:
    d = _load().get("jobs") or {}
    return {"任务数": len(d), "在册处理器": sorted(HANDLERS),
            "到期": due(), "账本": str(LOG),
            "任务": [{"任务": k, "处理器": v.get("handler"), "每s": v.get("every_s"),
                      "跑过": v.get("runs"), "迟到次数": v.get("late"),
                      "上次结果": v.get("last_result")} for k, v in d.items()]}


def ensure_defaults() -> dict:
    """把 V9 自主层该有的几条心跳补齐（幂等：已存在就跳过）。"""
    made = []
    for name, every, handler, note in (
            ("信息素衰减", 900, "stigmergy_decay", "算一遍衰减，只算不删"),
            ("长任务心跳", 300, "longrun_heartbeat", "续租 + 推进到期检查点"),
            ("主动汇报扫描", 600, "proactive_scan", "四类汇报：过不打扰门才说"),
            ("镜像摘要", 1800, "mirror_digest", "镜像排练层的状态快照"),
            ("中枢预热", 600, "hub_warm", "把能力图/盲区先算好，页面永远秒回")):
        d = _load()
        if name in (d.get("jobs") or {}):
            continue
        r = define(name, every, handler=handler, note=note, first_delay_s=20)
        if r.get("ok"):
            made.append(name)
    return {"ok": True, "新增": made, "任务数": len((_load().get("jobs") or {}))}


__all__ = ["define", "due", "tick", "status", "ensure_defaults", "HANDLERS", "JOBS"]
