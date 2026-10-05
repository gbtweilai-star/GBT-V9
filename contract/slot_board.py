# contract/slot_board.py —— D1~D100 槽位板：认领 / 心跳 / 超时回收
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 语义：槽位 D1..D100 全量预置；触手认领后持有 claimed_at 心跳；
#       超时（默认 90s 未心跳）自动回收为 idle，允许他人再认领（幂等）。
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SLOTS = 100
CLAIM_TIMEOUT_S = float(os.getenv("SLOT_CLAIM_TIMEOUT_S", "90"))


@dataclass
class Slot:
    name: str
    holder: str = ""
    claimed_at: float = 0.0
    state: str = "idle"          # idle | claimed | suspect

    @property
    def expired(self) -> bool:
        return (self.state != "idle"
                and time.time() - self.claimed_at > CLAIM_TIMEOUT_S)


class SlotBoard:
    """SQLite 落地（进程重启不丢槽位）；也支持 :memory:。"""

    def __init__(self, db: str | os.PathLike = "state/slots.db",
                 slots: int = DEFAULT_SLOTS) -> None:
        self.db = str(db)
        if self.db != ":memory:":
            Path(self.db).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db, check_same_thread=False)
        self._init(slots)

    def _init(self, slots: int) -> None:
        with self._lock, self._conn as c:
            c.execute("CREATE TABLE IF NOT EXISTS slots("
                      "name TEXT PRIMARY KEY, holder TEXT, claimed_at REAL, state TEXT)")
            for i in range(1, slots + 1):
                c.execute("INSERT OR IGNORE INTO slots VALUES(?,?,?,?)",
                          (f"D{i}", "", 0.0, "idle"))

    # ── 认领：任一空闲槽位（或指定 name）──
    def claim(self, holder: str, name: str | None = None) -> Slot | None:
        self.reclaim_expired()
        with self._lock, self._conn as c:
            if name:
                row = c.execute("SELECT name,holder,claimed_at,state FROM slots WHERE name=?",
                                (name,)).fetchone()
                rows = [row] if row else []
            else:
                rows = c.execute("SELECT name,holder,claimed_at,state FROM slots "
                                 "WHERE state='idle' ORDER BY name LIMIT 1").fetchall()
            for r in rows:
                if r and r[3] in ("idle", "suspect"):
                    c.execute("UPDATE slots SET holder=?, claimed_at=?, state='claimed' "
                              "WHERE name=?", (holder, time.time(), r[0]))
                    return Slot(r[0], holder, time.time(), "claimed")
        return None

    def heartbeat(self, name: str, holder: str) -> bool:
        with self._lock, self._conn as c:
            cur = c.execute("UPDATE slots SET claimed_at=? WHERE name=? AND holder=? "
                            "AND state='claimed'", (time.time(), name, holder))
            return cur.rowcount == 1

    def release(self, name: str, holder: str) -> bool:
        with self._lock, self._conn as c:
            cur = c.execute("UPDATE slots SET holder='', claimed_at=0, state='idle' "
                            "WHERE name=? AND holder=?", (name, holder))
            return cur.rowcount == 1

    # ── 超时回收：> 超时时间未心跳 → 先标 suspect，再转 idle ──
    def reclaim_expired(self) -> list[str]:
        now = time.time()
        freed = []
        with self._lock, self._conn as c:
            rows = c.execute("SELECT name,holder,claimed_at,state FROM slots "
                             "WHERE state!='idle'").fetchall()
            for name, holder, claimed_at, state in rows:
                age = now - claimed_at
                if state == "claimed" and age > CLAIM_TIMEOUT_S:
                    c.execute("UPDATE slots SET state='suspect' WHERE name=?", (name,))
                elif state == "suspect" and age > 2 * CLAIM_TIMEOUT_S:
                    c.execute("UPDATE slots SET holder='', claimed_at=0, state='idle' "
                              "WHERE name=?", (name,))
                    freed.append(name)
        return freed

    def snapshot(self) -> dict:
        self.reclaim_expired()
        with self._lock, self._conn as c:
            rows = c.execute("SELECT name,holder,claimed_at,state FROM slots").fetchall()
        by_state: dict[str, int] = {}
        holders: dict[str, int] = {}
        for _n, h, _t, st in rows:
            by_state[st] = by_state.get(st, 0) + 1
            if h:
                holders[h] = holders.get(h, 0) + 1
        return {"total": len(rows), "by_state": by_state, "holders": holders}

    def close(self) -> None:
        self._conn.close()


__all__ = ["SlotBoard", "Slot", "CLAIM_TIMEOUT_S"]
