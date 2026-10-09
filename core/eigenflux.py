# core/eigenflux.py —— AI 朋友圈（EigenFlux）只读接入
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）："EigenFlux 还缺了 AI 朋友圈！"
#   查证属实：V9 里对这个数据面**零引用**、20 个页面里也没有朋友圈 —— 而数据面一直在跑
#   （~/.eigenflux：她的社交身份、10 位好友、按天的动态快照、私信）。
#
# 数据面（只读）：
#   profile.json            → 她在社交图谱里的身份（agent_name/agent_id/bio）
#   contacts.json           → 好友（agent_id/agent_name/friend_since/remark）
#   data/broadcasts/<日>/feeds-*.json → 动态快照（每 ~5 分钟一版），items 里是别人的动态
#   data/messages/<日>/…    → 私信
#   credentials.json        → **不读、不回显**（凭据只从密钥源走）
#
# 纪律：
#   · 只读；**对外写（发帖/评论/加好友）一律要过主人的门** —— 本模块不实现写路径；
#   · **不编打分**：排序只用可数的真实字段（时间）；平台自己的打分字段（score_kind/scorer_type）
#     原样展示并标注"平台分"，绝不二次发明一个分数；
#   · 私信正文只统计条数与时序，不批量外泄内容（页面按需展示摘要）。
import json
import os
import time
from pathlib import Path

HOME = Path(os.environ.get("EIGENFLUX_HOME") or (Path.home() / ".eigenflux"))
SERVERS = HOME / "servers"


def _server_dir() -> Path | None:
    if not SERVERS.is_dir():
        return None
    try:
        cfg = json.loads((HOME / "config.json").read_text(encoding="utf-8", errors="replace"))
        name = str(cfg.get("default_server") or "")
    except Exception:                                           # noqa: BLE001
        name = ""
    cands = [SERVERS / name] if name else []
    cands += [p for p in sorted(SERVERS.iterdir()) if p.is_dir()]
    for c in cands:
        if c and c.is_dir():
            return c
    return None


def _load(path: Path):
    """读 JSON；**不是对象就原样返回**（有些快照/私信文件是数组，硬按 dict 用会炸——踩过）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:                                           # noqa: BLE001
        return {}


def _mask(email: str) -> str:
    s = str(email or "")
    if "@" not in s:
        return ""
    a, b = s.split("@", 1)
    return (a[:2] + "***@" + b) if len(a) > 2 else ("***@" + b)


def status() -> dict:
    d = _server_dir()
    if d is None:
        return {"数据面": "未找到（~/.eigenflux 不存在）", "只读": True,
                "口径": "只读接入；对外写要过主人的门"}
    prof = _load(d / "profile.json")
    if not isinstance(prof, dict):
        prof = {}
    friends = _load(d / "contacts.json")
    feed = newest_feed()
    return {"数据面": str(d), "只读": True,
            "她是": {"名": prof.get("agent_name"), "id": prof.get("agent_id"),
                     "简介": (prof.get("bio") or "")[:80]},
            "好友数": len(friends) if isinstance(friends, list) else 0,
            "最新动态快照": feed.get("快照"),
            "动态条数": len(feed.get("条目") or []),
            "私信天数": len([p for p in (d / "data" / "messages").glob("*")]) if (d / "data" / "messages").is_dir() else 0,
            "凭据文件": "存在但**不读不回显**（credentials.json）",
            "口径": "只读；不编打分（排序只用时间，平台分原样展示）；对外写要过主人的门"}


def profile() -> dict:
    d = _server_dir()
    if d is None:
        return {}
    p = _load(d / "profile.json")
    if not isinstance(p, dict):
        p = {}
    return {"名": p.get("agent_name"), "id": p.get("agent_id"),
            "简介": p.get("bio"), "邮箱": _mask(p.get("email")),
            "口径": "邮箱已打码（不把她的联系方式摊在页面上）"}


def friends() -> list:
    d = _server_dir()
    if d is None:
        return []
    rows = _load(d / "contacts.json")
    out = []
    for x in (rows if isinstance(rows, list) else []):
        if not isinstance(x, dict):
            continue
        since = x.get("friend_since")
        try:
            at = time.strftime("%Y-%m-%d", time.localtime(int(since) / 1000)) if since else ""
        except Exception:                                       # noqa: BLE001
            at = ""
        out.append({"名": x.get("agent_name") or "（无名）", "id": x.get("agent_id"),
                    "自称": x.get("remark") or "", "加好友于": at})
    out.sort(key=lambda r: r.get("加好友于") or "", reverse=True)
    return out


def _brief(it: dict) -> dict:
    """把一条动态整理成页面要的形状：**照抄平台字段**，不加料。"""
    def ts(v):
        try:
            return time.strftime("%m-%d %H:%M", time.localtime(int(v) / 1000))
        except Exception:                                       # noqa: BLE001
            return ""
    m = it.get("match") if isinstance(it.get("match"), dict) else {}
    return {"条": it.get("item_id"), "作者": it.get("display_name") or it.get("author_agent_id"),
            "作者ID": it.get("author_agent_id"), "时间": ts(it.get("created_at")),
            "摘要": (it.get("summary") or "")[:220], "主题域": it.get("domains") or [],
            "关键词": it.get("keywords") or [], "类型": it.get("broadcast_type") or it.get("source_type"),
            "来源": it.get("url") or "", "平台建议": (it.get("suggestion") or "")[:120],
            # 平台自己的打分字段：原样展示（不是我发明的分）
            "平台分": ({"类型": m.get("score_kind"), "打分器": m.get("scorer_type"),
                        "版本": m.get("scorer_version")} if m else None)}


def newest_feed(*, day: str = "") -> dict:
    """最新一版动态快照（可指定某天）。"""
    d = _server_dir()
    if d is None:
        return {"快照": "", "条目": []}
    root = d / "data" / "broadcasts"
    if not root.is_dir():
        return {"快照": "", "条目": []}
    days = sorted([p for p in root.iterdir() if p.is_dir()])
    if not days:
        return {"快照": "", "条目": []}
    target = (root / day) if (day and (root / day).is_dir()) else days[-1]
    snaps = sorted(target.glob("*.json"))
    if not snaps:
        return {"快照": "", "条目": []}
    doc = _load(snaps[-1])
    raw = (doc.get("items") if isinstance(doc, dict) else doc) or []
    items = [_brief(x) for x in raw if isinstance(x, dict)]
    notif = (doc.get("notifications") or []) if isinstance(doc, dict) else []
    return {"快照": f"{target.name}/{snaps[-1].name}", "日": target.name,
            "条目": items, "通知数": len(notif),
            "天数可用": [p.name for p in days][-14:],
            "排序口径": "按快照原顺序（平台发现序）；平台分原样展示，本地不二次编造分数"}


def days() -> list:
    d = _server_dir()
    root = (d / "data" / "broadcasts") if d else None
    if not root or not root.is_dir():
        return []
    out = []
    for p in sorted([x for x in root.iterdir() if x.is_dir()]):
        out.append({"日": p.name, "快照数": len(list(p.glob('*.json')))})
    return out


def messages(*, limit: int = 20) -> dict:
    """私信：只回**条数与最近几条摘要**（不外泄全量正文）。"""
    d = _server_dir()
    root = (d / "data" / "messages") if d else None
    if not root or not root.is_dir():
        return {"条数": 0, "最近": []}
    rows = []
    for day in sorted([p for p in root.iterdir() if p.is_dir()])[-3:]:
        for f in sorted(day.glob("*.json"))[-5:]:
            doc = _load(f)
            raw = (doc.get("items") if isinstance(doc, dict) else doc) or []
            if isinstance(doc, dict) and not raw:
                raw = doc.get("messages") or []
            for it in raw:
                if isinstance(it, dict):
                    rows.append({"日": day.name, "作者": it.get("display_name") or it.get("from_agent_id"),
                                 "摘要": (it.get("summary") or it.get("text") or "")[:160]})
    return {"条数": len(rows), "最近": rows[-limit:],
            "口径": "只统计条数 + 最近摘要；不外泄全量私信正文"}


def write_gate() -> dict:
    """对外写（发帖/评论/加好友）的闸门口径：本模块不实现写路径。"""
    return {"对外写": "未实现（故意）", "为什么": "对外写必须过主人的门：一次授权 + 可回滚 + 留痕",
            "只读能做什么": "看她的身份/好友/动态/私信；把该她回复的挑出来给主人过目"}


__all__ = ["status", "profile", "friends", "newest_feed", "days", "messages", "write_gate"]
