# core/memory/unify.py —— 统一入口：把散落各处的记忆收进主脑，并且分类好
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："把主脑记忆全部统一…把触手的记忆也存放进去分类好。"
#
# 收拢的对象（都是仓库里**真实存在**的记忆源，不是设想）：
#   ① Obsidian 触手记忆库（body/obsidian.py：触手/<id>.md，每个触手一份追加式笔记）
#      → 归到 owner=t0xx、scope=触手记忆、category=事件
#   ② 进程内触手记忆（core/isolated_bus.MemoryStore：命名空间隔离，重启即失）
#      → 归到 owner=<tid>、scope=触手记忆（落库后才真的"活过重启"）
#   ③ 主脑自己的零散记录（state/agent_chat.jsonl 里的对话、固化备注）
#      → 归到 owner=main、scope=主脑记忆
# 纪律：
#   · **幂等**：靠 (origin, origin_ref, 文本指纹) 去重，重复导入不会翻倍；
#   · 原文照抄进 raw（source of truth 不变），导入只加"从哪来"的标记；
#   · 导入后统一编码（分类/实体/向量），所以触手记忆也带分类进大脑。
import hashlib
import json
import time
from pathlib import Path

from core.memory import store as S

ROOT = Path(__file__).resolve().parent.parent.parent
STATE = ROOT.joinpath("state")


def _fp(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:20]


def _exists(st, *, origin: str, ref: str, text: str) -> bool:
    rows = st.list(all_owners=True, limit=100000, include_merged=True)
    fp = _fp(text)
    for m in rows:
        if m.get("origin") == origin and str(m.get("origin_ref") or "") == str(ref) and \
                _fp(m.get("raw") or "") == fp:
            return True
    return False


def import_obsidian(*, st=None, limit: int = 2000) -> dict:
    """把 Obsidian 触手记忆库逐条收进主脑（每行一条；触手归属从文件名/小节推断）。"""
    st = st or S.store()
    try:
        from body import obsidian as OB
        vault = OB.pick_vault()
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "来源": "obsidian", "reason": f"{type(exc).__name__}: {exc}"}
    if not vault:
        return {"ok": True, "来源": "obsidian", "导入": 0,
                "reason": "没找到记忆库（未装 Obsidian 或没建「GBT小土豆V9-触手记忆」库）"}
    got = 0
    scanned = 0
    try:
        files = vault.list_files() if hasattr(vault, "list_files") else []
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "来源": "obsidian", "reason": type(exc).__name__}
    for rel in files[:limit]:
        if not str(rel).endswith(".md"):
            continue
        scanned += 1
        try:
            text = vault.read(rel)
        except Exception:                                      # noqa: BLE001
            continue
        tid = "main"
        parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
        for p in parts:
            stem = p.rsplit(".", 1)[0]
            if stem.startswith("t") and stem[1:].isdigit():
                tid = stem
                break
        for line in str(text or "").splitlines():
            ln = line.strip().lstrip("-*").strip()
            if len(ln) < 4:
                continue
            if _exists(st, origin="obsidian", ref=f"{rel}", text=ln):
                continue
            scope = "触手记忆" if tid.startswith("t") else "主脑记忆"
            st.capture(ln, owner=tid, origin="obsidian", origin_ref=str(rel)[:120],
                       scope=scope, category="事件",
                       meta={"导入": "obsidian", "文件": str(rel)})
            got += 1
    return {"ok": True, "来源": "obsidian", "扫描文件": scanned, "导入": got,
            "记忆库": str(getattr(vault, "root", "") or "")}


def import_isolated_bus(*, st=None) -> dict:
    """把进程内触手记忆（MemoryStore）收进主脑 —— 落库后才真的活过重启。"""
    st = st or S.store()
    try:
        from core.isolated_bus import MemoryStore
        snap = MemoryStore().inspect_all()
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "来源": "isolated_bus", "reason": type(exc).__name__}
    got = 0
    for tid, ns in (snap or {}).items():
        for key, rec in (ns or {}).items():
            text = f"{key}: {(rec or {}).get('value')}"
            if _exists(st, origin="isolated_bus", ref=f"{tid}/{key}", text=text):
                continue
            st.capture(text, owner=str(tid), origin="isolated_bus",
                       origin_ref=f"{tid}/{key}", scope="触手记忆", category="事实",
                       meta={"导入": "isolated_bus"})
            got += 1
    return {"ok": True, "来源": "isolated_bus", "导入": got,
            "说明": "进程内记忆原本重启即失；导入后进入统一记忆库"}


def import_agent_chat(*, st=None, limit: int = 500) -> dict:
    """把主脑与智能体的对话记录收进主脑（提问与回答都留，作为"我说过什么"）。"""
    st = st or S.store()
    p = STATE.joinpath("agent_chat.jsonl")
    if not p.is_file():
        return {"ok": True, "来源": "agent_chat", "导入": 0, "reason": "还没有对话记录"}
    got = 0
    lines = p.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
    for i, ln in enumerate(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        text = str(d.get("text") or d.get("question") or "").strip()
        ans = str(d.get("answer") or d.get("reply") or "").strip()
        blob = (text + ("\n→ " + ans if ans else "")).strip()
        if len(blob) < 4:
            continue
        ref = f"agent_chat/{i}"
        if _exists(st, origin="agent_chat", ref=ref, text=blob):
            continue
        st.capture(blob, owner="main", origin="agent_chat", origin_ref=ref,
                   scope="主脑记忆", category="事件" if ans else "事实",
                   meta={"导入": "agent_chat", "at": d.get("at")})
        got += 1
    return {"ok": True, "来源": "agent_chat", "导入": got}


def import_life(*, st=None) -> dict:
    """主脑自己的生平（固化/记账/整理）也归档进生命起源存档。"""
    from core.memory import life as LIFE
    return LIFE.onboard(st=st or S.store())


def unify_all(*, st=None, encode: bool = True, limit: int = 500) -> dict:
    """一键统一：三个来源 + 生平归档 + 编码。返回每步结果（可重复跑，不会翻倍）。"""
    st = st or S.store()
    from core.memory import encoder as E
    from core.memory import life as LIFE
    LIFE.born(st=st)
    steps = [import_obsidian(st=st), import_isolated_bus(st=st), import_agent_chat(st=st)]
    enc = E.encode_pending(limit=limit, st=st) if encode else {"跳过": True}
    lf = LIFE.onboard(st=st)
    total = {**st.counts(all_owners=True), "按主体": st.by_owner_kind(),
             "按记忆域": st.by_scope(), "按分类": st.by_category()}
    return {"ok": True, "步骤": steps, "编码": enc, "生平": {"条数": lf.get("新增或已存在")},
            "统一后": total,
            "口径": "幂等导入：靠 (来源, 位置, 文本指纹) 去重，重复跑不会翻倍"}


def status(*, st=None) -> dict:
    st = st or S.store()
    return {"库位置": str(st.path), "主体维度": st.by_owner_kind(),
            "记忆域": st.by_scope(), "分类": st.by_category(), "分层": st.by_tier(),
            "主体数": len(st.owners()), "主体列表": st.owners()[:12],
            "口径": "主脑 / 100 触手 / 用户 / 系统 的记忆同库，靠 owner 维度分类与隔离"}


__all__ = ["import_obsidian", "import_isolated_bus", "import_agent_chat", "import_life",
           "unify_all", "status"]
