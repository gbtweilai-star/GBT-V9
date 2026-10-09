# core/fleet_live.py —— 一页编队实时面板（每根 AI/触手：身份·职业·在干嘛·最后动作）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-09）：「整个框架所有的编程职业在一页上面做出每个 AI 在执行任务的可视化面板，
#   谁是谁、什么职位、正在休息还是在工作都要看得见。」
# 口径（全部读**既有台账**，不新造状态；没有落账就是休息/离线，不编）：
#   职业 ← vaults/agency_tentacles/tNNN-职业.md（册子真文件）
#   状态 ← state/*.jsonl 里该触手最近一次落账时间：≤10min=工作；≤60min=待命；待授权队列=待授权；无记录=离线
#   当前任务 ← 那条落账的动作/事由
#   附带 ← 随身库未读（tentacle_store）、绑定页数（file_binding 表）、装备/钥匙（tentacle_keys 指纹）
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
VAULTS = ROOT / "vaults" / "agency_tentacles"
WORK_MS = 10 * 60
IDLE_MS = 60 * 60


def _ms_ago(ts: str) -> float | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return (time.time() - time.mktime(time.strptime(ts[:19], fmt))) * 1000
        except Exception:  # noqa: BLE001
            continue
    return None


def professions() -> dict:
    """职业表：t001 → 职业名（从册子文件名读，真文件）。"""
    out = {}
    if VAULTS.is_dir():
        for p in VAULTS.glob("t*-*.md"):
            name = p.stem
            tid, _, prof = name.partition("-")
            if tid.startswith("t"):
                out[tid] = prof.replace("_", " ")
    return out


def _scan_ledgers(limit: int = 400) -> dict:
    """扫 state/*.jsonl：每根触手最近一次落账（时间 + 动作 + 台账名）。"""
    last: dict = {}
    for p in sorted(STATE.glob("*.jsonl")):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            blob = json.dumps(rec, ensure_ascii=False)
            ts = str(rec.get("at") or rec.get("时间") or rec.get("ts") or "")
            who = set()
            for key in ("触手", "tentacle", "谁", "by", "吐出人"):
                v = rec.get(key)
                if isinstance(v, str) and v.startswith("t") and v[1:].isdigit():
                    who.add(v)
            for k, v in rec.items():
                if isinstance(v, str) and v.startswith("t") and v[1:].isdigit():
                    who.add(v)
                if isinstance(v, list):
                    for it in v:
                        if isinstance(it, dict):
                            for kk in ("触手", "tentacle", "复核触手", "吐的人"):
                                vv = it.get(kk)
                                if isinstance(vv, str) and vv.startswith("t") and vv[1:].isdigit():
                                    who.add(vv)
            # 兜底：台账文本里出现的 tNNN
            import re
            who |= set(re.findall(r"\bt\d{3}\b", blob))
            for t in who:
                cur = last.get(t)
                if cur is None or ts >= cur["at"]:
                    act = (rec.get("动作") or rec.get("事") or rec.get("抓") or rec.get("动作位") or "")
                    if not act:
                        act = ", ".join(list(rec.keys())[:4])
                    last[t] = {"at": ts, "动作": str(act)[:40], "台账": p.name}
    return last


def _binding_pages() -> dict:
    try:
        from core import file_binding as FB
        rows = FB._q("SELECT tentacle_id, COUNT(*) AS n FROM tentacle_file_bindings"
                     " WHERE root_id=? GROUP BY tentacle_id", (FB.ROOT_ID,))
        return {r["tentacle_id"]: r["n"] for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def _unread() -> dict:
    try:
        from core import tentacle_store as TS
        ib = TS.inbox()
        return {x.get("触手"): 1 for x in (ib.get("汇报") or [])}
    except Exception:  # noqa: BLE001
        return {}


def snapshot(*, n: int = 100) -> dict:
    t0 = time.time()
    prof = professions()
    last = _scan_ledgers()
    pages = _binding_pages()
    waiting = []
    try:
        from core import longrun
        st = longrun.status() if hasattr(longrun, "status") else {}
        waiting = list((st or {}).get("待授权", []) or [])
    except Exception:  # noqa: BLE001
        waiting = []
    rows = []
    for i in range(1, n + 1):
        t = "t%03d" % i
        rec = last.get(t)
        if rec and rec["at"]:
            age = _ms_ago(rec["at"])
            if age is not None and age <= WORK_MS * 1000:
                status = "工作"
            elif age is not None and age <= IDLE_MS * 1000:
                status = "待命"
            else:
                status = "休息"
        else:
            status = "离线"
        if t in waiting and status != "工作":
            status = "待授权"
        rows.append({"触手": t, "职业": prof.get(t, "（未立专业）"), "状态": status,
                     "最后动作": (rec or {}).get("动作") or "—",
                     "最后时间": (rec or {}).get("at") or "",
                     "台账": (rec or {}).get("台账") or "",
                     "绑定页": pages.get(t, 0)})
    tally = {}
    for r in rows:
        tally[r["状态"]] = tally.get(r["状态"], 0) + 1
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "根数": len(rows), "统计": tally,
            "触手": rows, "ms": round((time.time() - t0) * 1000, 1),
            "口径": "状态只有落了账才算『工作』；无记录=离线；不编状态"}


def timeline(tentacle: str, *, limit: int = 20) -> dict:
    hits = []
    for p in sorted(STATE.glob("*.jsonl")):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-600:]
        except OSError:
            continue
        for line in lines:
            if tentacle in line:
                try:
                    hits.append({"台账": p.name, **json.loads(line)})
                except Exception:  # noqa: BLE001
                    continue
    hits.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return {"触手": tentacle, "条数": len(hits), "时间线": hits[:limit]}


def status() -> dict:
    s = snapshot()
    return {"统计": s["统计"], "口径": s["口径"],
            "工作": [r["触手"] for r in s["触手"] if r["状态"] == "工作"][:10],
            "待授权": [r["触手"] for r in s["触手"] if r["状态"] == "待授权"][:10]}


__all__ = ["WORK_MS", "IDLE_MS", "professions", "snapshot", "timeline", "status"]
