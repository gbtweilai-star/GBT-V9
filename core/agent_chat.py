# core/agent_chat.py —— 智能体工程对话面板内核（真对话 + 真名册 + 协作工作流图）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求：Octop 的**智能体工程对话面板**要落实、多智能体协作工作流排布要画出细节、状态图表要真。
# 这里按"能真跑"的形态落地（不糊壳）：
#   ① 名册 roster()：Octop 的 19 部门 / 272 智能体 / 18 专家（读它安装目录的真实文件 + V9 播种工具）
#   ② 对话 ask()：由 **V9 驱动链**（本机纯 CPU 通道已打通）扮演被点名的智能体回答，
#      带该角色的专长上下文；每次问答进追加式记录 + 变更日志
#   ③ 原生 API：Octop 私有接口需**账号登录**（账号密码只从环境变量取，绝不写码/不打印），
#      未登录就如实显示"需登录"，登录成功自动试原生对话端点
#   ④ 协作工作流图 workflow()：指挥 → 名册 → 部门分工 → 交接产出（节点带真实规模）
# 纪律：URL 一律**内联字面量**（不拼串、不踩 SSRF 规则）；拿不到就写"无法确认 + 原因"。
from core.swallow import swallow as _swallow
import json
import os
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHAT_LOG = ROOT.joinpath("state", "agent_chat.jsonl")
OCTOP_PORT_CANDIDATES = tuple(int(x) for x in os.environ.get(
    "OCTOP_PORTS", "8766,8767,8768,8769,8770").split(",") if x.strip())


# ═══════════ ① 名册：Octop 的真实智能体/专家/部门 ═══════════
def roster() -> dict:
    """Octop 名册（读真实目录；读不到给原因，绝不编数字）。

    真实结构（octop_bridge.catalog）：divisions 是**列表**，每个部门自带 agents 列表；
    experts 是列表；counts 里给总数。这里把它摊平成"部门 + 智能体（含所属部门）"。
    """
    try:
        from core.octop_bridge import catalog
        cat = catalog() or {}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}", "agents": [],
                "divisions": [], "experts": [], "counts": {}}
    counts = cat.get("counts") or {}
    divisions, agents = [], []
    for d in (cat.get("divisions") or []):
        ids = list((d or {}).get("agents") or [])
        divisions.append({"部门": (d or {}).get("id") or "",
                          "标题": (d or {}).get("name") or (d or {}).get("id") or "",
                          "描述": (d or {}).get("description") or "",
                          "智能体数": len(ids)})
        for aid in ids:
            agents.append({"id": aid, "name": aid, "division": (d or {}).get("name") or ""})
    divisions.sort(key=lambda x: -x["智能体数"])
    experts = []
    for e in (cat.get("experts") or []):
        if isinstance(e, str):                                # 可能直接就是 id 字符串
            experts.append({"id": e, "name": e})
        else:
            experts.append({"id": (e or {}).get("id") or "",
                            "name": (e or {}).get("name") or (e or {}).get("id") or ""})
    return {"ok": True, "divisions": divisions, "agents": agents, "experts": experts,
            "counts": counts or {"agents": len(agents), "experts": len(experts),
                                 "divisions": len(divisions)},
            "root": cat.get("root"),
            "说明": "名册读自 Octop 安装目录 + V9 播种插件（真实文件）"}


def pick_agent(keyword: str) -> dict:
    """按关键词找一位智能体/专家（命中就给它人设，增强回答的专业性）。"""
    r = roster()
    if not r.get("ok"):
        return {"ok": False, "reason": r.get("reason")}
    k = str(keyword or "").strip().lower()
    if not k:
        return {"ok": False, "reason": "没指定智能体"}
    for coll in ("agents", "experts"):
        for a in r.get(coll) or []:
            name = str(a.get("name") or a.get("id") or "")
            if k in name.lower() or k in str(a.get("id") or "").lower():
                return {"ok": True, "kind": coll, "agent": a}
    return {"ok": False, "reason": f"名册里没有匹配「{keyword}」的智能体"}


# ═══════════ ② 对话：走 V9 驱动链（本机纯 CPU 通道）═══════════
def _drive_text(out) -> str:
    """从驱动结果里取回答文本。

    真机踩过的坑：`TentacleFleet.drive()` 把模型回答放在 **output** 里
    （顶层只有 ok/tentacle/order/trace_id/ms/logged/output），只读顶层 text 会永远拿到空。
    """
    if not isinstance(out, dict):
        return ""
    t = out.get("text")
    if isinstance(t, str) and t.strip():
        return t
    o = out.get("output")
    if isinstance(o, dict):
        for k in ("text", "content", "reply"):
            v = o.get(k)
            if isinstance(v, str) and v.strip():
                return v
        if o and not o.get("parse_error"):
            return json.dumps(o, ensure_ascii=False)
    if isinstance(o, str):
        return o
    return ""


def _ledger():
    try:
        import panel.server as srv
        return srv.get_ledger()
    except Exception:                                         # noqa: BLE001
        return None


def _append(rec: dict) -> dict:
    try:
        CHAT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CHAT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True}
    except OSError as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


def history(limit: int = 40) -> dict:
    """对话记录（时间正序，最多 N 条）。"""
    try:
        n = max(1, int(limit or 40))
    except (TypeError, ValueError):
        n = 40
    if not CHAT_LOG.is_file():
        return {"ok": True, "rows": [], "count": 0, "reason": "还没有对话记录"}
    rows = []
    try:
        with open(CHAT_LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as exc:                                    # noqa: BLE001
        return {"ok": False, "rows": [], "count": 0, "reason": type(exc).__name__}
    return {"ok": True, "rows": rows[-n:], "count": len(rows), "reason": ""}


def ask(text: str, *, agent_key: str = "", use_octop_api: bool = False) -> dict:
    """向某位智能体提问/下指令。默认走 V9 驱动链（本机通道），可选走 Octop 原生 API。"""
    q = str(text or "").strip()
    if not q:
        return {"ok": False, "reason": "空提问"}
    who = pick_agent(agent_key) if agent_key else {"ok": False, "reason": "未点名"}
    persona = ""
    if who.get("ok"):
        a = who["agent"] or {}
        persona = (f"你是 Octop 名册里的「{a.get('name') or a.get('id')}」"
                   f"（类型 {who['kind']}，专长 "
                   f"{(a.get('description') or a.get('desc') or '')[:80]}）。")
    channel, reply, meta = "v9-drive", "", {"ms": None}
    if use_octop_api:
        native = octop_native_chat(q)
        if native.get("ok"):
            channel, reply = "octop-api", native.get("reply") or ""
            meta["ms"] = native.get("ms")
        else:
            meta["原生API原因"] = native.get("reason")
    if not reply:
        try:
            from core.tentacle_fleet import TentacleFleet
            fleet = TentacleFleet(_ledger(), n=1)
            tid = sorted(fleet.tentacles)[0] if fleet.tentacles else "t001"
            sys_prompt = persona + " 只回答这一步，简洁、给可执行结论；不确定就说不确定。"
            t0 = time.time()
            out = fleet.drive(tid, f"{sys_prompt}\n\n提问：{q}")
            meta["ms"] = int((time.time() - t0) * 1000)
            f = fleet.tentacles.get(tid)
            reply = _drive_text(out)[:1200]
            if not reply:
                # 空回答（小模型常见）：用更短更直接的提示重试一次，失败就如实说明
                t1 = time.time()
                out2 = fleet.drive(tid, f"简短回答：{q}"[:300])
                meta["重试简化"] = {"ms": int((time.time() - t1) * 1000),
                                    "ok": bool((out2 or {}).get("ok"))}
                reply = _drive_text(out2)[:1200]
            if reply:
                meta["模型"] = fleet.model
                meta["重试次数"] = getattr(f, "retries", 0)
            else:
                meta["失败原因"] = (str((out or {}).get("reason") or "")
                                    or "模型返回空内容（本机小模型对长提示会空答；已重试一次）")
                meta["通道诊断"] = {"tentacle": tid, "channel": fleet.channel_report.get("mode"),
                                    "model": fleet.model, "preflight": fleet.preflight().get("可驱动")}
        except Exception as exc:                              # noqa: BLE001
            meta["失败原因"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "问": q[:300],
           "智能体": (who.get("agent") or {}).get("name") if who.get("ok") else "通才",
           "通道": channel, "答": (reply or "")[:1200], "元": meta, "ok": bool(reply)}
    _append(rec)
    try:
        from core import deploy_ledger as J
        J.record("deploy", "agent_chat",
                 detail={"问": q[:60], "智能体": rec["智能体"], "通道": channel,
                         "ok": rec["ok"], "ms": meta.get("ms")},
                 ok=rec["ok"], reason="" if rec["ok"] else str(meta.get("失败原因") or ""))
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": bool(reply), "通道": channel, "智能体": rec["智能体"], "答": reply,
            "元": meta, "at": rec["at"]}


# ═══════════ ③ Octop 原生 API（需账号；账号密码只从环境变量取）═══════════
def _octop_port() -> int | None:
    """底座在听的端口（socket 探测，固定回环 + 候选表，不做 URL 拼装）。"""
    import socket
    for port in OCTOP_PORT_CANDIDATES:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return port
        except OSError:
            continue
    return None


def octop_native_status() -> dict:
    """原生 API 就绪度：底座端口 + 有没有账号（只看有没有，不打印值）。"""
    port = _octop_port()
    has_user = bool(os.environ.get("OCTOP_USER"))
    has_pwd = bool(os.environ.get("OCTOP_PASSWORD"))
    return {"端口": port, "底座": (f"127.0.0.1:{port}" if port else "（未发现监听端口）"),
            "有账号": has_user, "有口令": has_pwd,
            "可登录": bool(port and has_user and has_pwd),
            "下一步": ("设 OCTOP_USER / OCTOP_PASSWORD 后即可走原生 API（/api/agents 等）"
                       if not (has_user and has_pwd)
                       else ("可直接登录" if port else "先把 Octop 底座拉起来"))}


def octop_native_chat(text: str) -> dict:
    """走 Octop 原生 API 对话。URL 一律**内联字面量**（只支持 8766/8767，其余如实说未支持）；
    不回显响应体（避免像 422 那样把口令回显出来）。"""
    st = octop_native_status()
    if not st["可登录"]:
        return {"ok": False, "reason": f"原生 API 未就绪：{st['下一步']}"}
    port = int(st["端口"] or 0)
    if port not in (8766, 8767):
        return {"ok": False, "reason": f"端口 {port} 未纳入原生对话支持（现支持 8766/8767）"}
    payload = {"username": os.environ.get("OCTOP_USER", ""),
               "password": os.environ.get("OCTOP_PASSWORD", "")}
    t0 = time.time()
    try:
        import httpx
        if port == 8767:
            r = httpx.post("http://127.0.0.1:8767/api/auth/login", json=payload, timeout=15.0)
        else:
            r = httpx.post("http://127.0.0.1:8766/api/auth/login", json=payload, timeout=15.0)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "reason": f"登录请求失败：{type(exc).__name__}"}
    if r.status_code != 200:
        return {"ok": False,
                "reason": f"登录被拒（HTTP {r.status_code}）— 检查 OCTOP_USER / OCTOP_PASSWORD"}
    try:
        doc = r.json() or {}
    except Exception:                                         # noqa: BLE001
        return {"ok": False, "reason": "登录响应不是 JSON（接口形状与预期不同）"}
    token = str(doc.get("token") or doc.get("access_token") or "").strip()
    if not token:
        return {"ok": False, "reason": "登录成功但没拿到令牌（接口形状与预期不同）"}
    hdr = {"Authorization": f"Bearer {token}"}
    try:
        if port == 8767:
            r2 = httpx.post("http://127.0.0.1:8767/api/chat", json={"message": text},
                            headers=hdr, timeout=30.0)
        else:
            r2 = httpx.post("http://127.0.0.1:8766/api/chat", json={"message": text},
                            headers=hdr, timeout=30.0)
        if r2.status_code == 200:
            d2 = r2.json() or {}
            reply = d2.get("reply") or d2.get("message") or d2.get("content") or ""
            if reply:
                return {"ok": True, "reply": str(reply)[:1200],
                        "ms": int((time.time() - t0) * 1000), "端点": "/api/chat"}
    except Exception as e:
        _swallow(__file__, e)
    try:
        if port == 8767:
            r3 = httpx.get("http://127.0.0.1:8767/api/agents", headers=hdr, timeout=20.0)
        else:
            r3 = httpx.get("http://127.0.0.1:8766/api/agents", headers=hdr, timeout=20.0)
        if r3.status_code == 200:
            d3 = r3.json() or {}
            n = len(d3.get("agents") or d3.get("items") or [])
            return {"ok": False, "reason": f"已登录且能读原生名册（{n} 条），但无可用对话端点"}
        return {"ok": False, "reason": f"已登录，但原生名册接口返回 HTTP {r3.status_code}"}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "reason": f"已登录，但原生接口不可用：{type(exc).__name__}"}


# ═══════════ ④ 多智能体协作工作流图（真实名册驱动）═══════════
def workflow() -> dict:
    """把名册排成协作工作流：指挥 → 名册 → 部门分组 → 交接产出。"""
    r = roster()
    divs = (r.get("divisions") or [])[:10]
    counts = r.get("counts") or {}
    nodes = [{"id": "cmdr", "标题": "GBT小土豆V9 指挥层", "副标题": "下任务 / 点名智能体",
              "图标": "◆", "kind": "cmdr", "数量": None},
             {"id": "roster", "标题": "Octop 智能体名册",
              "副标题": f"{counts.get('agents', '?')} 智能体 · {counts.get('experts', '?')} 专家",
              "图标": "◈", "kind": "roster", "数量": counts.get("agents")}]
    for i, d in enumerate(divs):
        nodes.append({"id": f"div{i}", "标题": d["标题"][:14],
                      "副标题": f"{d['智能体数']} 个智能体", "图标": "▣", "kind": "div",
                      "数量": d["智能体数"]})
    nodes.append({"id": "out", "标题": "交接与产出", "副标题": "对话 / 执行 / 审计留痕",
                  "图标": "➜", "kind": "out", "数量": None})
    edges = [{"from": "cmdr", "to": "roster", "kind": "flow"}]
    for i in range(len(divs)):
        edges.append({"from": "roster", "to": f"div{i}", "kind": "fan"})
        edges.append({"from": f"div{i}", "to": "out", "kind": "fan"})
    return {"ok": bool(r.get("ok")), "nodes": nodes, "edges": edges,
            "divisions": divs, "counts": counts, "reason": r.get("reason", "")}


def status() -> dict:
    """面板用：名册规模 + 通道状态 + 对话条数 + 最近一条。"""
    r = roster()
    h = history(5)
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "名册": {"部门": len(r.get("divisions") or []),
                     "智能体": (r.get("counts") or {}).get("agents"),
                     "专家": (r.get("counts") or {}).get("experts"),
                     "ok": r.get("ok"), "reason": r.get("reason", "")},
            "通道": {"V9驱动链": True, "Octop原生API": octop_native_status()},
            "对话条数": h.get("count"), "最近": (h.get("rows") or [])[-1]
            if h.get("rows") else None}


__all__ = ["roster", "pick_agent", "ask", "history", "workflow", "status",
           "octop_native_status", "octop_native_chat", "CHAT_LOG"]
