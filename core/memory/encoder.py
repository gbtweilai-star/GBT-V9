# core/memory/encoder.py —— 理解（编码器）：把一段话读成"什么东西、多重要、关于谁"
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 定位：捕捉是**即时**的（写完就完了），理解放在这里**稍后发生**（后台 worker 调它）。
# 纪律：
#   · 规则优先、确定性、可测 —— 不为了"看起来聪明"去编模型输出；
#   · 分类只给 8 类（事实/事件/待办/感受/做法/知识/里程碑/教训），并落到 scope（记忆域）；
#   · 向量走**我们自己的算力路由**（compute_router 的 embed.text → 云插件主管道）；
#     云不可用时退回**本地哈希向量**，并如实标注是"哈希，不是语义"；
#   · private=1 的记忆**绝不外发**（不调云、不落外部），只做本地处理。
import hashlib
import math
import os
import re
import struct
import time

from core.memory import store as S

# ── 分类规则（先关键词，再看句式；顺序影响优先级） ──
_TODO = re.compile(r"(记得|待办|要(去|做|买|写|发|问)|别忘|明天|下周|月底|deadline|todo)",
                   re.I)
_EVENT = re.compile(r"(今天|昨天|前天|上周|上个月|刚才|刚刚|那天|生日|开完会|见面|聊过|发生)")
_FEEL = re.compile(r"(开心|难过|累|烦|焦虑|兴奋|生气|委屈|感动|害怕|爽|郁闷|开心死)")
_HOWTO = re.compile(r"(步骤|怎么|如何|方法|流程|配方|命令|配置|部署|修复|先.*再)")
_LESSON = re.compile(r"(教训|踩坑|下次别|不该|失误|翻车|亏|错在|以后要)")
_FACT = re.compile(r"(是|为|等于|位于|成立于|属于|=|：)")
_DEFAULT_KIND = "事实"

# 记忆域：把分类映射到我们自己的 scope 词表（store.SCOPES）
_KIND_TO_SCOPE = {"事实": "主脑记忆", "事件": "主脑记忆", "待办": "项目",
                  "感受": "主脑记忆", "做法": "技能", "知识": "洞见",
                  "里程碑": "项目", "教训": "错误"}

_STOPWORD = {"的", "了", "是", "在", "我", "你", "他", "她", "它", "和", "与", "就",
             "都", "也", "很", "有", "没有", "这个", "那个", "一个", "什么", "怎么",
             "记得", "要", "会", "把", "被", "给", "对", "从", "到", "后", "前"}


def classify(text: str) -> dict:
    """规则分类：返回 kind（8 类之一）+ 命中的依据（可核查，不黑箱）。"""
    t = str(text or "")
    for kind, rx in (("待办", _TODO), ("教训", _LESSON), ("做法", _HOWTO),
                     ("感受", _FEEL), ("事件", _EVENT)):
        m = rx.search(t)
        if m:
            return {"kind": kind, "依据": f"命中「{m.group(0)}」", "scope": _KIND_TO_SCOPE[kind]}
    m = _FACT.search(t)
    return {"kind": _DEFAULT_KIND,
            "依据": (f"含判断词「{m.group(0)}」" if m else "无明确特征 → 归事实"),
            "scope": _KIND_TO_SCOPE[_DEFAULT_KIND]}


def importance_of(text: str, *, kind: str = "") -> float:
    """重要度（0~1）：待办/教训天然高一点；含数字/日期/专名的再加一点。基线 0.5。"""
    t = str(text or "")
    v = 0.5
    if kind in ("待办", "教训", "里程碑"):
        v += 0.15
    if re.search(r"\d", t):
        v += 0.06
    if re.search(r"(\d{1,2}月\d{1,2}[日号]|周[一二三四五六日天]|明天|下周|月底)", t):
        v += 0.08
    if len(t) >= 40:
        v += 0.05
    if re.search(r"(必须|一定|重要|要紧|关键)", t):
        v += 0.08
    return round(min(1.0, v), 3)


_CUT = set("的了是在和与就都也很把被给对从到后前个这那你我他她它们吗呢吧啊着过上下里外")


def extract_entities(text: str, *, limit: int = 10) -> list:
    """实体抽取（规则式，可核查 —— 不是 NER 模型，抽的是候选实体，用来连边做联想）。

    上一版的毛病：对整段中文穷举 2/3/4 字窗口，结果被"会定了/开会定/开会定了/张总开…"
    这类重叠碎片灌满，`limit` 一截，**真正有用的实体（预算、Q3）反而被丢掉** ——
    联想于是永远连不上。现在改成：
      ① 先按常用虚词/代词把中文串切成"实词块"；
      ② 每块只取长度 2~4 的窗口（块本来就短，不会爆炸），长的优先；
      ③ ASCII 词 ≥2 字也算实体（Q3 / t007 / GMT+8 / V9 这些代号很重要）；
      ④ #标签 / @提及 当强实体。
    """
    t = str(text or "")
    out: list = []
    seen = set()

    def put(name: str, etype: str, w: float = 1.0):
        nm = str(name).strip()
        if len(nm) < 2 or nm in seen or nm in _STOPWORD:
            return
        seen.add(nm)
        out.append((nm, etype, w))

    for tag in re.findall(r"[#@][A-Za-z0-9_\u4e00-\u9fff]{2,20}", t):
        put(tag[1:], "标签", 1.3)
    for w in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9_.+\-]{1,}\b", t):
        put(w, "专名" if len(w) <= 6 else "词", 1.2)
    chunks = []
    for run in re.findall(r"[\u4e00-\u9fff]{2,20}", t):
        buf = ""
        for ch in run:
            if ch in _CUT:
                if buf:
                    chunks.append(buf)
                buf = ""
            else:
                buf += ch
        if buf:
            chunks.append(buf)
    cands = []
    for ck in chunks:
        if len(ck) <= 4:
            cands.append(ck)
            continue
        for n in (4, 3, 2):                       # 长块取窗口，长的优先
            for i in range(0, len(ck) - n + 1):
                cands.append(ck[i:i + n])
    for nm in cands:
        put(nm, "实体", 1.0 if len(nm) >= 3 else 0.7)
    out.sort(key=lambda x: -len(x[0]))            # 更具体的排前面，截断时才不会被碎片挤掉
    return out[: max(1, int(limit))]


# ── 向量：走我们自己的算力路由；云可用就**真调**，不可用如实降本地哈希 ──
DIM = 128
EMBED_MODEL = os.environ.get("V9_EMBED_MODEL", "text-embedding-3-small")


def _hashed_vec(text: str, *, dim: int = DIM) -> bytes:
    """本地哈希向量（bag-of-tokens 投影）。**不是语义模型** —— 只做"像不像"的粗兜底。"""
    vec = [0.0] * dim
    for tk in S.tokens(text):
        h = int.from_bytes(hashlib.sha256(tk.encode("utf-8")).digest()[:4], "big")
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    vec = [v / norm for v in vec]
    return struct.pack(f"<{dim}f", *vec)


def _vec_bytes(vec: list) -> bytes:
    return struct.pack(f"<{len(vec)}f", *[float(x) for x in vec])


def _cloud_vec(text: str) -> dict:
    """真调网关的 embeddings（复用主脑那把钥匙与客户端，不自己拼 URL）。"""
    from core.brain import API_KEY, BASE_URL                      # noqa: PLC0415
    from openai import OpenAI
    cli = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    r = cli.embeddings.create(model=EMBED_MODEL, input=text[:2000])
    vec = list(r.data[0].embedding or [])
    if not vec:
        raise ValueError("网关返回空向量")
    return {"ok": True, "vec": _vec_bytes(vec),
            "model": f"云 embedding（{EMBED_MODEL} · {len(vec)} 维）"}


def embed(text: str, *, private: bool = False) -> dict:
    """给一段话取向量。

    私密记忆**绝不外发**（直接本地哈希）。非私密时：算力路由说云可走就**真调一次**
    网关 embeddings；调用失败或路由不可用，如实降本地哈希并写清原因（不假装有语义向量）。
    """
    if private:
        return {"ok": True, "state": "local", "model": "本地哈希（私密记忆不外发）",
                "vec": _hashed_vec(text), "why": "private=1：只在本机处理，绝不外发"}
    route = {}
    try:
        from core import compute_router as CR
        route = CR.route("embed.text")
    except Exception as exc:                                   # noqa: BLE001
        route = {"ok": False, "reason": type(exc).__name__}
    if not route.get("ok") or route.get("channel") != "cloud":
        return {"ok": True, "state": "local", "model": "本地哈希（云不可用，非语义）",
                "vec": _hashed_vec(text),
                "why": str(route.get("why") or route.get("reason") or "云主管道不可用")}
    try:
        got = _cloud_vec(text)
        return {"ok": True, "state": "cloud", "model": got["model"], "vec": got["vec"],
                "why": f"云主管道真调成功（插件 {route.get('plugin')} 同族）"}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": True, "state": "local", "model": "本地哈希（云调用失败，非语义）",
                "vec": _hashed_vec(text),
                "why": f"云 embedding 调用失败：{type(exc).__name__}: {str(exc)[:80]}"}


def encode(memory_id: str, *, st=None) -> dict:
    """把一条记忆编码：分类 + 重要度 + 实体 + 向量，写回存储（原文不动）。

    注意用 row_of（按 id 直取）：编码是**后台按 id 干活**，不该受 owner 过滤影响 ——
    否则触手的记忆永远编码不了、一直卡在"未分类"（真踩过）。
    """
    st = st or S.store()
    m = st.row_of(memory_id)
    if m is None:
        return {"ok": False, "reason": "记忆不存在"}
    if m.get("merged_into"):
        return {"ok": True, "skipped": "已被合并进别的记忆", "id": memory_id}
    text = m["raw"]
    cls = classify(text)
    imp = importance_of(text, kind=cls["kind"])
    ents = extract_entities(text)
    emb = embed(text, private=bool(m.get("private")))
    # 记忆域的归属优先看**主体**：触手写的永远是"触手记忆"，不能被内容分类覆盖掉
    owner = str(m.get("owner") or "")
    scope = "触手记忆" if owner.startswith("t") else (
        cls.get("scope") or m.get("scope") or "主脑记忆")
    st.enrich(memory_id, kind=cls["kind"], importance=imp, entities=ents,
              category=cls["kind"], scope=scope,
              embed_state=emb["state"], embed_model=emb["model"], embed_vec=emb["vec"],
              note=cls["依据"])
    st.event("encode", f"{memory_id} → {cls['kind']}（{cls['依据']}）",
             owner=str(m.get("owner") or S.OWNER_DEFAULT))
    return {"ok": True, "id": memory_id, "分类": cls["kind"], "依据": cls["依据"],
            "重要度": imp, "实体": [e[0] for e in ents], "向量通道": emb["state"],
            "向量说明": emb["why"]}


def encode_pending(*, limit: int = 50, st=None) -> dict:
    """把还没编码的（含刚导入的）都编码一遍。返回处理条数与通道分布。"""
    st = st or S.store()
    todo = [m for m in st.list(all_owners=True, limit=100000)
            if m.get("encode_state") == "pending"]
    done, channels = 0, {}
    for m in todo[: max(1, int(limit))]:
        r = encode(m["id"], st=st)
        if r.get("ok"):
            done += 1
            channels[r["向量通道"]] = channels.get(r["向量通道"], 0) + 1
    return {"待编码": len(todo), "本次编码": done, "向量通道": channels}


def reembed(*, limit: int = 500, st=None) -> dict:
    """重编码：把已编码但向量通道是**陈旧标签**的行按现在的口径重算一遍。

    为什么要它：向量通道的口径变过（早期只写"待调用"占位，现在是"真调云 / 如实降本地"）。
    老行若带着旧标签，面板上就看不到真实分布 —— 那是把"没接上"藏起来，不行。
    """
    st = st or S.store()
    stale = [m for m in st.list(all_owners=True, limit=100000)
             if m.get("encode_state") == "done"
             and str(m.get("embed_state") or "") in ("pending_cloud", "none")]
    done, channels = 0, {}
    for m in stale[: max(1, int(limit))]:
        r = encode(m["id"], st=st)
        if r.get("ok"):
            done += 1
            channels[r["向量通道"]] = channels.get(r["向量通道"], 0) + 1
    return {"陈旧标签行": len(stale), "本次重算": done, "向量通道": channels,
            "重算后分布": st.by_embed()}


def pending_count(*, st=None) -> int:
    st = st or S.store()
    return int(st.counts(all_owners=True).get("待编码") or 0)


def explain(text: str) -> dict:
    """给面板/接口看：这段话会被读成什么（不落库），让人能对着规则较真。"""
    cls = classify(text)
    return {"分类": cls["kind"], "依据": cls["依据"], "记忆域": cls["scope"],
            "重要度": importance_of(text, kind=cls["kind"]),
            "实体": [{"名": e[0], "类": e[1]} for e in extract_entities(text)],
            "向量": embed(text)["model"]}


__all__ = ["classify", "importance_of", "extract_entities", "embed", "encode",
           "encode_pending", "reembed", "pending_count", "explain", "DIM", "EMBED_MODEL"]
