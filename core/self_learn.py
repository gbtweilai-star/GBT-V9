# core/self_learn.py —— 卡点自学习：遇到不会的就自己上网找核心资料，学完再继续
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）：
#   "给她注入：不管做什么，只要遇到卡点或者自身知识没有的，就自己全网查找核心资料
#    并学习再继续，别跟个傻逼一样无脑操作。"
#
# 口径（不装、不糊）：
#   · 学习 = **真去取公开资料**（搜索引擎结果页 → 抓正文段落）→ 蒸馏成一条可复核的笔记，
#     落 state/knowledge/learned/ 并入原生大脑（可召回），全程进账本；
#   · 断网/取不到 → 如实说"没学到 + 为什么"，**绝不编一条假知识**；
#   · 出行安全：所有外发请求先过 body.net_guard（只允许 http/https，拒回环/私有/保留地址），
#     不跟随跨域重定向，带超时与体积上限（沿用本项目既有纪律）。
from core.swallow import swallow as _swallow
import hashlib
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "state" / "knowledge" / "learned"
FEED = ROOT / "state" / "self_learn.jsonl"
UA = "Mozilla/5.0 (compatible; GBT-V9-SelfLearn/1.0)"

# 这条规矩要注入到"她的行为准则"里（人格/指令面），不只是躺着当代码
RULE = ("遇到卡点或自己知识里没有的：先查（公开资料/官方文档）→ 蒸馏成可复核笔记 → "
        "再继续干；查不到就如实说没查到，不许编、不许无脑硬试。")


def _hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe(url: str) -> str:
    """出行安全闸门：只放行 http/https 且非本机/私网/保留地址（用项目既有 net_guard）。"""
    from body.net_guard import assert_safe_outbound_url
    assert_safe_outbound_url(url)          # 不合规会抛异常，调用方如实处理
    return url


def _http(url: str, *, timeout: float = 12.0, limit: int = 400_000) -> dict:
    """取一个页面（带护栏）。返回 {ok, text, status}。"""
    try:
        _safe(url)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": f"网址被安全闸门拒绝：{type(exc).__name__}"}
    try:
        import httpx
        with httpx.Client(timeout=timeout, follow_redirects=False,
                          headers={"User-Agent": UA}) as c:
            r = c.get(url)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location") or ""
            if loc.startswith("http") and loc.split("/")[2] != url.split("/")[2]:
                return {"ok": False, "reason": "跨域重定向未跟随"}
            if loc.startswith("http"):
                return _http(loc, timeout=timeout, limit=limit)
        return {"ok": r.status_code == 200, "status": r.status_code,
                "text": (r.text or "")[:limit]}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}"}


def _text_of(html: str) -> str:
    """粗抽正文：去脚本/样式/标签，压空白。够蒸馏用，不做花活。"""
    h = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html or "")
    h = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"'))
    h = re.sub(r"[ \t\r\f\v]+", " ", h)
    return re.sub(r"\n{2,}", "\n", h).strip()


def search(query: str, *, limit: int = 5) -> dict:
    """公开搜索（DuckDuckGo HTML 版；纯 GET，不带凭据）。返回 [{标题, 网址}]。"""
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "reason": "空查询"}
    from urllib.parse import quote, unquote
    url = "https://html.duckduckgo.com/html/?q=" + quote(q)
    r = _http(url)
    if not r.get("ok"):
        return {"ok": False, "reason": r.get("reason") or f"HTTP {r.get('status')}"}
    html = r["text"] or ""
    # DDG 会把结果包成 //duckduckgo.com/l/?uddg=<urlencoded>，所以先解 uddg，再兜底收裸链接。
    cands = [unquote(m.group(1)) for m in re.finditer(r'uddg=([^&"\'<>]+)', html)]
    cands += [m.group(1) for m in re.finditer(r'href="(https?://[^"]+)"', html)]
    skip = ("duckduckgo.com", "bing.com", "baidu.com", "google.com", "w3.org")
    seen, out = set(), []
    for u in cands:
        if not u.startswith("http") or any(s in u for s in skip) or u in seen:
            continue
        seen.add(u)
        out.append({"网址": u})
        if len(out) >= limit:
            break
    return {"ok": bool(out), "查询": q, "结果": out,
            "reason": "" if out else "搜索页没解析出结果（可能被挡）"}


def learn(topic: str, *, gap: str = "", max_pages: int = 3, record: bool = True) -> dict:
    """自学习主流程：搜索 → 取正文 → 蒸馏笔记 → 落盘 + 入脑 + 记账。

    gap 写清"卡在哪"（没有它就不算复盘，只是搜索）。取不到任何资料时如实失败。
    """
    t0 = time.time()
    topic = str(topic or "").strip()
    if not topic:
        return {"ok": False, "reason": "空主题"}
    s = search(topic)
    if not s.get("ok"):
        out = {"ok": False, "主题": topic, "阶段": "搜索", "reason": s.get("reason"),
               "口径": "查不到就如实说，不编"}
        _append({"at": _now(), **out})
        return out
    notes, used = [], []
    for item in s["结果"][:max_pages]:
        page = _http(item["网址"])
        if not page.get("ok"):
            continue
        body = _text_of(page.get("text") or "")
        if len(body) < 300:
            continue
        # 蒸馏：取与主题词最相关的前若干段（词命中优先，不去重造语义）
        keys = [w for w in re.split(r"[\s，,。·/]+", topic) if len(w) >= 2][:6]
        paras = [p.strip() for p in body.split("\n") if len(p.strip()) >= 40]
        scored = sorted(paras, key=lambda p: -sum(p.count(k) for k in keys))
        keep = [p[:400] for p in scored[:6]]
        notes.append({"网址": item["网址"], "段落": keep, "字数": len(body)})
        used.append(item["网址"])
    return write_note(topic, gap, notes, record=record, extra={"ms": int((time.time() - t0) * 1000)})


def write_note(topic: str, gap: str, notes: list, *, record: bool = True,
               extra: dict | None = None) -> dict:
    """把**已取到的页面**写成可复核笔记 + 入脑 + 记账。

    ★2026-10-08 抽出来的：`learn()`（自己搜自己抓）与 `core/blindspot.py`（外部喂已取到的资料）
    必须走**同一条落盘路径** —— 否则两条路会各写一份格式不同的笔记，读的人不知道信哪份。
    """
    ok = bool(notes)
    used = [n["网址"] for n in notes]      # 抽函数时漏了这个（原在 learn() 里）；补上
    slug = _hash(topic)[:12]
    path = OUT / f"{slug}.md"
    if ok:
        OUT.mkdir(parents=True, exist_ok=True)
        lines = [f"# 自学习笔记：{topic}", "",
                 f"- 学于：{_now()}",
                 f"- 卡点：{gap or '（未写明）'}",
                 f"- 来源：{'；'.join(used)}", ""]
        for n in notes:
            lines.append(f"## {n['网址']}")
            for p in n["段落"]:
                lines.append(f"- {p}")
            lines.append("")
        lines += ["## 来源等级", "公开网页 + 本项目蒸馏；**非权威原文摘录**，引用前请回源核对。", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
    res = {"ok": ok, "主题": topic, "卡点": gap, "取到页数": len(notes),
           "来源": [n["网址"] for n in notes], "笔记": str(path) if ok else "",
           "口径": "真抓公开资料并蒸馏；查不到就说查不到"}
    if extra:
        res.update(extra)
    if ok:
        try:                                     # 入原生大脑（可召回；入口是 core.memory.brain.remember）
            from core import memory as MEM
            r = MEM.brain.remember(
                f"自学习：{topic}\n卡点：{gap or '未写明'}\n来源：{'；'.join(used)}\n"
                + "\n".join(l for n in notes for l in n["段落"])[:1500],
                origin="self_learn", category="系统",
                meta={"主题": topic, "卡点": gap, "来源": used, "笔记": str(path)})
            res["入脑"] = bool(r.get("ok"))
        except Exception as exc:                                    # noqa: BLE001
            res["入脑"] = f"未入脑（{type(exc).__name__}）"
    if record:
        try:
            from core import deploy_ledger as J
            J.record("add", "self_learn", detail={"主题": topic, "卡点": gap,
                                                  "取到页数": len(notes), "笔记": res["笔记"]})
        except Exception as e:
            _swallow(__file__, e)
    _append({"at": _now(), **{k: res[k] for k in ("ok", "主题", "卡点", "取到页数", "笔记")}})
    return res


def _append(rec: dict) -> None:
    try:
        FEED.parent.mkdir(parents=True, exist_ok=True)
        with FEED.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        _swallow(__file__, e)


def recent(limit: int = 10) -> list:
    if not FEED.is_file():
        return []
    rows = []
    for line in FEED.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:                                       # noqa: BLE001 as _e_swallow
            _swallow(__file__, _e_swallow)
            continue
    return rows


def notes() -> list:
    if not OUT.is_dir():
        return []
    return [{"名": p.name, "字节": p.stat().st_size, "改动": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime))}
        for p in sorted(OUT.glob("*.md"))]


def status() -> dict:
    return {"规矩": RULE, "笔记目录": str(OUT), "笔记数": len(notes()),
            "最近": recent(5), "口径": "遇到卡点先学再干；查不到如实说，绝不编"}


__all__ = ["RULE", "search", "learn", "recent", "notes", "status"]
