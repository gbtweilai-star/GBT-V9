# core/longrun.py —— 长程任务：租约 / 心跳 / 检查点 / 断点续作 / 主人升级（V9 自主层）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用途：跨天、跨进程的大活不能靠"进程还活着"当成功：
#   · 租约（lease）：谁在干、什么时候到期；到期没续 = 交给了下一位（不空等、不双跑）
#   · 心跳（heartbeat）：干活的每隔一会儿续一次；**心跳断 = 如实判活失败**
#   · 检查点（checkpoint）：每一步做完就落账（状态 + 证据），断了下一次接着干
#   · 断点续作（resume）：复用已完成的步，只重跑没完成的
#   · 主人升级（escalate）：卡住了写进收件箱，等主人一句话
# 纪律：不要相信 exit code；判活看**进度读数有没有如期更新**。
import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "state" / "longrun"
INBOX = ROOT / "state" / "longrun_inbox.jsonl"
LEASE_S = 300.0            # 默认租约 5 分钟


def _now() -> float:
    return time.time()


def _path(tid: str) -> Path:
    return TASKS / f"{tid}.json"


def _load(tid: str) -> dict:
    p = _path(tid)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def _save(t: dict) -> None:
    TASKS.mkdir(parents=True, exist_ok=True)
    _path(t["id"]).write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")


def _inbox(rec: dict) -> None:
    INBOX.parent.mkdir(parents=True, exist_ok=True)
    with INBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def submit(目标: str, *, steps: tuple | list, owner: str = "main",
           lease_s: float = LEASE_S) -> dict:
    """提交一件长活：目标 + 分步清单。"""
    ss = [str(s) for s in steps if str(s).strip()]
    if not 目标 or not ss:
        return {"ok": False, "reason": "目标与步骤都不能为空"}
    tid = "lr_" + uuid.uuid4().hex[:10]
    t = {"id": tid, "目标": str(目标), "owner": owner, "steps": ss,
         "done": [], "cur": ss[0], "state": "进行中",
         "at": _now(), "lease_until": _now() + float(lease_s), "lease_s": float(lease_s),
         "heartbeats": 1, "checkpoints": [], "escalated": False}
    _save(t)
    return {"ok": True, "任务": tid, "目标": t["目标"], "步数": len(ss), "下一步": t["cur"],
            "租约到期": t["lease_until"]}


def heartbeat(task_id: str, *, lease_s: float | None = None) -> dict:
    """续租 + 汇报"我在干"。过期任务会如实标成"租约过期（判活失败）"。"""
    t = _load(task_id)
    if not t:
        return {"ok": False, "reason": f"没有这个任务：{task_id}"}
    now = _now()
    if t.get("state") == "完成":
        return {"ok": True, "任务": task_id, "状态": "完成", "无需续租": True}
    if float(t.get("lease_until") or 0) < now:
        t["state"] = "租约过期"
        _save(t)
        return {"ok": False, "任务": task_id, "状态": "租约过期",
                "原因": "上一次之后没有人续租 —— 按纪律**不假装还在跑**"}
    t["lease_until"] = now + float(lease_s or t.get("lease_s") or LEASE_S)
    t["heartbeats"] = int(t.get("heartbeats") or 0) + 1
    t["state"] = "进行中"
    _save(t)
    return {"ok": True, "任务": task_id, "续到": t["lease_until"], "心跳数": t["heartbeats"]}


def checkpoint(task_id: str, 步: str, *, 状态: str = "完成", evidence: str = "",
               note: str = "") -> dict:
    """做完一步就落账（证据一起写），断了下一次接着干。"""
    t = _load(task_id)
    if not t:
        return {"ok": False, "reason": f"没有这个任务：{task_id}"}
    s = str(步 or t.get("cur") or "")
    if s not in t.get("steps", []):
        return {"ok": False, "reason": f"这一步不在计划里：{s}"}
    rec = {"at": _now(), "步": s, "状态": 状态, "证据": str(evidence)[:300],
           "备注": str(note)[:200]}
    t.setdefault("checkpoints", []).append(rec)
    if 状态 == "完成" and s not in t["done"]:
        t["done"].append(s)
    todo = [x for x in t["steps"] if x not in t["done"]]
    t["cur"] = todo[0] if todo else ""
    t["state"] = "完成" if not todo else "进行中"
    t["lease_until"] = _now() + float(t.get("lease_s") or LEASE_S)
    _save(t)
    return {"ok": True, "任务": task_id, "已完成": len(t["done"]), "总步数": len(t["steps"]),
            "下一步": t["cur"], "状态": t["state"]}


def resume(task_id: str) -> dict:
    """断点续作：返回"还剩哪些步"，已完成的不再重跑。"""
    t = _load(task_id)
    if not t:
        return {"ok": False, "reason": f"没有这个任务：{task_id}"}
    todo = [x for x in t["steps"] if x not in t["done"]]
    t["state"] = "完成" if not todo else "进行中"
    t["lease_until"] = _now() + float(t.get("lease_s") or LEASE_S)
    _save(t)
    return {"ok": True, "任务": task_id, "复用已完成": list(t["done"]), "只重跑": todo,
            "下一步": todo[0] if todo else ""}


def escalate(task_id: str, 原因: str) -> dict:
    """卡住了：写进主人收件箱（等她一句话），任务标"待授权"。"""
    t = _load(task_id)
    if not t:
        return {"ok": False, "reason": f"没有这个任务：{task_id}"}
    rec = {"at": _now(), "任务": task_id, "目标": t.get("目标"), "卡在": t.get("cur"),
           "原因": str(原因)[:300], "已完成": len(t.get("done") or [])}
    _inbox(rec)
    t["state"] = "待授权"
    t["escalated"] = True
    _save(t)
    return {"ok": True, "任务": task_id, "状态": "待授权", "收件箱": str(INBOX)}


def heartbeat_all() -> dict:
    """心跳推进：所有"进行中"但租约过期的 → 标记过期（不假装还在跑）。"""
    TASKS.mkdir(parents=True, exist_ok=True)
    out = {"还在跑": [], "过期": [], "完成": [], "待授权": []}
    for p in sorted(TASKS.glob("lr_*.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        st = t.get("state")
        if st == "进行中" and float(t.get("lease_until") or 0) < _now():
            t["state"] = "租约过期"
            _save(t)
            st = "租约过期"
        key = {"进行中": "还在跑", "租约过期": "过期", "完成": "完成", "待授权": "待授权"}.get(st, "还在跑")
        out[key].append(t["id"])
    return {"ok": True, **out, "口径": "判活看租约与进度，不看 exit code"}


def status(task_id: str = "") -> dict:
    if task_id:
        return _load(task_id) or {"ok": False, "reason": "没有这个任务"}
    TASKS.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(TASKS.glob("lr_*.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        rows.append({"任务": t["id"], "目标": t.get("目标"), "状态": t.get("state"),
                     "进度": f"{len(t.get('done') or [])}/{len(t.get('steps') or [])}",
                     "下一步": t.get("cur"), "心跳": t.get("heartbeats"),
                     "待授权": bool(t.get("escalated"))})
    return {"任务数": len(rows), "任务": rows, "收件箱": str(INBOX),
            "纪律": "租约到期=判活失败；检查点落账；续作不重跑已完成的步"}


__all__ = ["submit", "heartbeat", "checkpoint", "resume", "escalate",
           "heartbeat_all", "status", "TASKS", "INBOX"]
