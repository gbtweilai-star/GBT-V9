# core/memory/inform.py —— 通知（轻推，不是垃圾）：只在该打扰的时候打扰
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 蒸馏自 TheBrain 的 §07，用我们自己的口径重写：
#   · 每条候选提醒先**打分**：重要度 × 时间紧迫 × 可执行性；分不够就压着不出声；
#   · **静默时段**（默认 22:00–08:00）一律不打扰；攒到早上出**晨报**；
#   · **背压**：待发提醒超过上限就不再堆（宁可丢分低的，也不刷屏）；
#   · **忽略学习**：同一类提醒老被忽略 → 退避（越推越少），如实记录在案。
import os
import time

from core.memory import store as S

QUIET_FROM = int(os.environ.get("V9_BRAIN_QUIET_FROM", "22"))     # 静默开始（小时）
QUIET_TO = int(os.environ.get("V9_BRAIN_QUIET_TO", "8"))          # 静默结束（小时）
INTERRUPT_MIN = float(os.environ.get("V9_BRAIN_INTERRUPT_MIN", "0.62"))
MAX_PENDING = int(os.environ.get("V9_BRAIN_MAX_PENDING", "20"))
DISMISS_BACKOFF = int(os.environ.get("V9_BRAIN_DISMISS_BACKOFF", "3"))


def in_quiet_hours(*, now: float | None = None) -> bool:
    h = time.localtime(time.time() if now is None else now).tm_hour
    return (h >= QUIET_FROM or h < QUIET_TO) if QUIET_FROM > QUIET_TO else \
        (QUIET_FROM <= h < QUIET_TO)


def score(mem: dict, *, now: float | None = None) -> float:
    """给一条记忆算"该不该现在提醒"。待办/有日期/重要度高 → 分高；飘远的 → 分低。"""
    now = time.time() if now is None else now
    if (mem.get("tier") or "") == "recycled":
        return 0.0
    s = 0.35 * float(mem.get("importance") or 0.5)
    if mem.get("category") == "待办":
        s += 0.30
    raw = str(mem.get("raw") or "")
    if any(k in raw for k in ("记得", "别忘", "待办", "明天", "下周", "截止", "deadline")):
        s += 0.20
    te = float(mem.get("t_event") or 0)
    if te:
        days = (te - now) / 86400.0
        if -1 <= days <= 3:                    # 就在眼前的事
            s += 0.25
        elif days > 3:
            s -= 0.10                          # 还早，不必现在说
    s += 0.10 * min(1.0, float(mem.get("heat") or 0.0))
    if int(mem.get("hits") or 0) > 0:
        s -= 0.05                              # 已经用过的就别再提醒
    return round(max(0.0, min(1.0, s)), 3)


def _dismissed_kinds(st, *, owner: str, days: float = 30.0) -> dict:
    rows = st._c.execute(
        "SELECT kind, COUNT(*) AS n FROM brain_dismissals WHERE owner=? AND ts>=?",
        (owner, time.time() - days * 86400.0)).fetchall()
    return {r["kind"]: r["n"] for r in rows}


def due(mem: dict, *, now: float | None = None, st=None, owner: str = S.OWNER_DEFAULT) -> dict:
    """这条记忆现在该不该提醒（把静默/忽略学习/背压都算进去）。"""
    st = st or S.store()
    now = time.time() if now is None else now
    s = score(mem, now=now)
    kind = str(mem.get("category") or "其他")
    dis = _dismissed_kinds(st, owner=owner).get(kind, 0)
    if dis >= DISMISS_BACKOFF:                 # 同类老被忽略 → 退避（越推越少）
        s -= min(0.5, 0.12 * (dis - DISMISS_BACKOFF + 1))
    if in_quiet_hours(now=now):
        return {"提醒": False, "分": round(s, 3), "原因": "静默时段（攒到早上出晨报）",
                "kind": kind}
    if s < INTERRUPT_MIN:
        return {"提醒": False, "分": round(s, 3),
                "原因": f"分不够（{s} < {INTERRUPT_MIN}）：压着，等更合适的时候",
                "kind": kind, "被忽略过": dis}
    pending = int(st._c.execute(
        "SELECT COUNT(*) FROM brain_nudges WHERE owner=? AND state='pending'",
        (owner,)).fetchone()[0])
    if pending >= MAX_PENDING:                 # 背压：宁可不推，也不刷屏
        return {"提醒": False, "分": round(s, 3),
                "原因": f"待发提醒已 {pending} 条（上限 {MAX_PENDING}）→ 背压，先不推",
                "kind": kind}
    return {"提醒": True, "分": round(s, 3), "kind": kind, "被忽略过": dis}


def emit(*, st=None, owner: str = S.OWNER_DEFAULT, limit: int = 50) -> dict:
    """按打分产出提醒（只在该推的时候建档；静默时段只入晨报）。"""
    st = st or S.store()
    rows = st.list(owner=owner, all_owners=False, limit=100000)
    out = {"已推": [], "压着": [], "晨报": []}
    for m in rows[: max(1, int(limit))]:
        d = due(m, st=st, owner=owner)
        if not d["提醒"]:
            if d["原因"].startswith("静默"):
                out["晨报"].append({"id": m["id"], "分": d["分"],
                                    "原文": (m.get("raw") or "")[:60]})
            else:
                out["压着"].append({"id": m["id"], "分": d["分"], "为什么": d["原因"]})
            continue
        nid = "n" + S.digest(m["id"] + str(d["分"]))
        with st._c:
            st._c.execute(
                "INSERT OR REPLACE INTO brain_nudges(nudge_id,ts,owner,kind,score,text,"
                "memory_id,state,why) VALUES(?,?,?,?,?,?,?,?,?)",
                (nid, time.time(), owner, d["kind"], d["分"], (m.get("raw") or "")[:200],
                 m["id"], "pending", f"打分 {d['分']}（{'被忽略过 ' + str(d['被忽略过']) if d.get('被忽略过') else '没被忽略过'}）"))
        out["已推"].append({"nudge_id": nid, "id": m["id"], "分": d["分"],
                            "原文": (m.get("raw") or "")[:60]})
        st.event("nudge", f"{nid} 分={d['分']}", owner=owner)
    with st._c:
        st._c.execute(
            "INSERT OR REPLACE INTO brain_nudges(nudge_id,ts,owner,kind,score,text,"
            "memory_id,state,why) VALUES(?,?,?,?,?,?,?,?,?)",
            ("digest-" + str(int(time.time() // 86400)), time.time(), owner, "晨报",
             0.5, f"晨报：静默期间攒了 {len(out['晨报'])} 条", "", "pending",
             "静默时段的提醒合并成晨报"))
    return {"ok": True, "已推": out["已推"], "压着": out["压着"][:10],
            "晨报条数": len(out["晨报"]), "静默中": in_quiet_hours(),
            "口径": f"打断线 {INTERRUPT_MIN}；静默 {QUIET_FROM}:00–{QUIET_TO}:00；"
                    f"同类被忽略 {DISMISS_BACKOFF} 次起退避；待发上限 {MAX_PENDING}"}


def act(nudge_id: str, *, action: str = "done", owner: str = S.OWNER_DEFAULT,
        st=None) -> dict:
    """对提醒表态：done（处理了）/ dismiss（不需要）/ snooze（稍后）。

    dismiss 会累加"忽略学习"，同类提醒之后会自动退避 —— 这就是"它会学"。
    """
    st = st or S.store()
    r = st._c.execute("SELECT * FROM brain_nudges WHERE nudge_id=? AND owner=?",
                      (nudge_id, owner)).fetchone()
    if r is None:
        return {"ok": False, "reason": "没有这条提醒"}
    state = {"done": "done", "dismiss": "dismissed", "snooze": "snoozed"}.get(
        str(action), "done")
    with st._c:
        st._c.execute("UPDATE brain_nudges SET state=?,acted_at=? WHERE nudge_id=?",
                      (state, time.time(), nudge_id))
        if state == "dismissed":
            st._c.execute("INSERT INTO brain_dismissals(ts,owner,kind) VALUES(?,?,?)",
                          (time.time(), owner, str(r["kind"])))
    if state == "done" and r["memory_id"]:
        st.touch(str(r["memory_id"]), owner=owner)
    st.event("nudge_action", f"{nudge_id} → {state}", owner=owner)
    dis = _dismissed_kinds(st, owner=owner).get(str(r["kind"]), 0)
    return {"ok": True, "nudge_id": nudge_id, "状态": state,
            "同类被忽略次数": dis,
            "退避": ("已开始退避（该类提醒会变少）" if dis >= DISMISS_BACKOFF else "还没到退避线")}


def inbox(*, st=None, owner: str = S.OWNER_DEFAULT, limit: int = 30) -> dict:
    st = st or S.store()
    rows = st._c.execute(
        "SELECT * FROM brain_nudges WHERE owner=? AND state='pending' ORDER BY score DESC",
        (owner,)).fetchall()
    items = [dict(r) for r in rows[: max(1, int(limit))]]
    return {"待处理": len(items), "静默中": in_quiet_hours(),
            "打断线": INTERRUPT_MIN, "项": items,
            "被忽略过的类": _dismissed_kinds(st, owner=owner)}


def status(*, st=None) -> dict:
    st = st or S.store()
    pend = int(st._c.execute(
        "SELECT COUNT(*) FROM brain_nudges WHERE state='pending'").fetchone()[0])
    return {"静默时段": f"{QUIET_FROM}:00–{QUIET_TO}:00", "现在静默": in_quiet_hours(),
            "打断线": INTERRUPT_MIN, "待处理": pend, "待发上限": MAX_PENDING,
            "忽略学习": f"同类被忽略 ≥{DISMISS_BACKOFF} 次起退避",
            "口径": "只在该打扰的时候打扰；宁可压着，也不刷屏"}


__all__ = ["in_quiet_hours", "score", "due", "emit", "act", "inbox", "status",
           "QUIET_FROM", "QUIET_TO", "INTERRUPT_MIN", "MAX_PENDING", "DISMISS_BACKOFF"]
