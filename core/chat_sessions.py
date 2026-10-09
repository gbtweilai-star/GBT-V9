# core/chat_sessions.py —— 对话会话存储（APP 独立多功能对话面板的底座）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径：
#   · 一个会话一个 JSONL 文件（state/chat/<id>.jsonl），**追加式**，消息只增不改；
#   · 会话清单（state/chat/index.json）记录标题/模式/时间/条数，供左栏列表用；
#   · 品牌与归属统一：会话属于 GBT小土豆V9，不叫别人的名字；
#   · 幂等：同 id 重复创建不会覆盖已有内容。
#
# 为什么不用数据库：对话是"流水"，追加式文件最抗崩（写一半也不破坏已写的行），
# 而且与仓库里既有的 agent_chat.jsonl / ble_ops.jsonl 是同一种做法，不引新依赖。
import json
import os
import re
import time
import uuid
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHAT_DIR = ROOT.joinpath("state", "chat")
INDEX = CHAT_DIR.joinpath("index.json")

MODES = ("日常对话", "大脑记忆", "指挥读数", "工作流", "工具")
BRAND = "GBT小土豆V9"


def _safe_id(sid: str) -> str:
    """会话 id 白名单化：只留字母数字与短横，杜绝路径穿越。"""
    return re.sub(r"[^A-Za-z0-9\-]", "", str(sid or ""))[:40]


def _path(sid: str) -> Path:
    return CHAT_DIR.joinpath(_safe_id(sid) + ".jsonl")


def _read_index() -> dict:
    if not INDEX.is_file():
        return {}
    try:
        return json.loads(INDEX.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _write_index(idx: dict) -> None:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(INDEX)


def new(*, title: str = "", mode: str = "日常对话", agent: str = "",
        owner: str = "main") -> dict:
    sid = "s" + uuid.uuid4().hex[:10]
    now = time.time()
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"id": sid, "title": (str(title).strip() or "新会话")[:60],
           "mode": mode if mode in MODES else MODES[0], "agent": str(agent or "")[:40],
           "owner": str(owner or "main"), "brand": BRAND,
           "created": now, "updated": now, "count": 0}
    idx = _read_index()
    idx[sid] = rec
    _write_index(idx)
    _path(sid).touch()
    return {"ok": True, "session": rec}


def sessions(*, limit: int = 50) -> list:
    idx = _read_index()
    rows = sorted(idx.values(), key=lambda r: -(r.get("updated") or 0))
    return rows[: max(1, int(limit))]


def get(sid: str) -> dict | None:
    return _read_index().get(_safe_id(sid))


def append(sid: str, role: str, text: str, *, meta: dict | None = None) -> dict:
    """追加一条消息（只增不改）。返回这条消息与更新后的会话摘要。"""
    s = _safe_id(sid)
    idx = _read_index()
    if s not in idx:
        return {"ok": False, "reason": "会话不存在"}
    msg = {"ts": time.time(), "role": str(role or "user")[:16],
           "text": str(text or "")[:8000], "meta": meta or {}}
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    with _path(s).open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    idx[s]["updated"] = msg["ts"]
    idx[s]["count"] = int(idx[s].get("count") or 0) + 1
    if idx[s].get("title") in ("", "新会话") and role == "user":
        idx[s]["title"] = str(text)[:24]                   # 首句当标题
    _write_index(idx)
    return {"ok": True, "message": msg, "session": idx[s]}


def history(sid: str, *, limit: int = 200) -> list:
    p = _path(sid)
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out[-max(1, int(limit)):]


def rename(sid: str, title: str) -> dict:
    s = _safe_id(sid)
    idx = _read_index()
    if s not in idx:
        return {"ok": False, "reason": "会话不存在"}
    idx[s]["title"] = str(title).strip()[:60] or idx[s]["title"]
    idx[s]["updated"] = time.time()
    _write_index(idx)
    return {"ok": True, "session": idx[s]}


def set_mode(sid: str, mode: str = "", agent: str = "") -> dict:
    s = _safe_id(sid)
    idx = _read_index()
    if s not in idx:
        return {"ok": False, "reason": "会话不存在"}
    if mode:
        idx[s]["mode"] = mode if mode in MODES else idx[s].get("mode")
    if agent != "":
        idx[s]["agent"] = str(agent)[:40]
    idx[s]["updated"] = time.time()
    _write_index(idx)
    return {"ok": True, "session": idx[s]}


def drop(sid: str) -> dict:
    """移除会话：从索引里摘掉，消息文件改名加 .deleted 后缀（不立即粉碎，留个底）。"""
    s = _safe_id(sid)
    idx = _read_index()
    if s not in idx:
        return {"ok": False, "reason": "会话不存在"}
    rec = idx.pop(s)
    _write_index(idx)
    p = _path(s)
    if p.is_file():
        try:
            p.rename(p.with_suffix(".jsonl.deleted"))
        except OSError as e:
            _swallow(__file__, e)

    return {"ok": True, "session": rec, "口径": "文件改名留底，不是立即删除"}


def status() -> dict:
    idx = _read_index()
    modes: dict = {}
    for r in idx.values():
        modes[r.get("mode") or "—"] = modes.get(r.get("mode") or "—", 0) + 1
    return {"品牌": BRAND, "会话数": len(idx), "模式分布": modes, "模式可选": list(MODES),
            "目录": str(CHAT_DIR),
            "最近": sessions(limit=5)}


__all__ = ["MODES", "BRAND", "new", "sessions", "get", "append", "history", "rename",
           "set_mode", "drop", "status", "CHAT_DIR"]
