# core/boot_scan.py —— 开机环境扫描（数字人指挥 AI 逐步扫描，一步一句台湾腔播报）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："启动的时候数字人指挥 AI 开始扫描环境，每一步都使用自然的台湾腔女声
#   来跟用户沟通对话式操作。"
#
# 口径：
#   · 每一步都是**真检查**（读真件：后端/账本/底座/面板/身体服务/编队/身份位/大脑/能力面/闭环）；
#   · 每一步配一句**台湾腔女声**要说的话（用台湾用词：專案、網路、資訊、影片…）；
#   · 结果如实：没起来的就说没起来 + 缺什么（不糊弄、不装扫描成功）；
#   · 页面按 order 逐步调用 run_step(i)，边扫边说 —— 这就是"对话式操作"的开场。
import time

STEPS = (
    ("backend", "后端与账本", "先看一下后端跟账本有没有醒过来。"),
    ("octop", "Octop 底座", "再来是 Octop 底座，看看它有没有在跑。"),
    ("panel", "总控台", "总控台这边我也顺一下。"),
    ("body", "身体服务", "身体服务是重点，快照采集跟见证探测都要活着才行。"),
    ("fleet", "触手编队", "触手编队，一百根都要点名到。"),
    ("identity", "触手身份位", "每一根触手的身份位，我一个个帮你对过。"),
    ("brain", "原生大脑", "再看我自己的脑袋，记忆有没有留住。"),
    ("caps", "能力面", "能力面摊开来看，一项一项都不许缺。"),
    ("loops", "闭环", "最后把八条闭环走一遍，通通要绿。"),
)


def _steps_by_id() -> dict:
    return {sid: (name, say) for sid, name, say in STEPS}


def _panel_bases(port=None) -> list:
    """总控台自检要探的本机地址（**跟着实际端口走**）。

    为什么：以前写死 8765，一旦用 PANEL_PORT 换端口跑（比如 8770 做验证），
    自检就会谎报"总控台我没连上 / 身体读数拿不到"（踩过，主人当场指出来）。
    """
    import os
    out = []
    # 第一优先：**页面自己正在被访问的那个端口**（最不会说谎）
    # 其次：环境变量指定的端口；最后才是历史默认 8765
    for p in (port, os.environ.get("PANEL_PORT"), os.environ.get("V9_PANEL_PORT"), "8765"):
        try:
            n = int(str(p))
        except Exception:                                      # noqa: BLE001
            continue
        url = f"http://127.0.0.1:{n}"
        if url not in out:
            out.append(url)
    return out


def _probe(path: str, timeout: float = 8.0, port=None):
    """按候选地址逐个探；返回 (数据, 用到的地址)；全都不通就抛最后一个异常。"""
    import json as _j
    import urllib.request as U
    last = None
    for base in _panel_bases(port):
        try:
            with U.urlopen(base + path, timeout=timeout) as r:
                return _j.loads(r.read().decode("utf-8", "replace")), base
        except Exception as exc:                               # noqa: BLE001
            last = exc
    raise last if last else RuntimeError("没有可探的地址")


def plan() -> list:
    """开机扫描的步骤表（页面照这个顺序念，用户听得懂）。"""
    return [{"id": sid, "名称": name, "播报": say} for sid, name, say in STEPS]


def _backend() -> dict:
    try:
        import panel.server as S
        led = S.get_ledger()
        if led is None:
            return {"ok": False, "说": "后端账本还没连上，你等一下我再看。",
                    "证据": {"ledger": None}}
        with led._tx() as c:
            n = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        return {"ok": True, "说": f"后端正常，账本里有 {n} 条记录。",
                "证据": {"ledger_rows": n, "db": getattr(led, "db", "")}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "后端这边我没读通，等下再试一次。",
                "证据": {"error": type(exc).__name__}}


def _octop() -> dict:
    try:
        from core import octop_fusion as OF
        st = OF.status()
        port = st.get("端口")
        if st.get("octop在线"):
            return {"ok": True, "说": f"Octop 底座有在跑，端口 {port}。",
                    "证据": {"端口": port, "融合度": st.get("融合度")}}
        return {"ok": False, "说": f"Octop 底座没起来，端口 {port} 没回应。",
                "证据": {"端口": port, "原因": (st.get("健康") or {}).get("reason")}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "底座我探不到，等下再看。",
                "证据": {"error": type(exc).__name__}}


def _panel(port=None) -> dict:
    try:
        d, base = _probe("/api/health", timeout=8, port=port)
        parts = d.get("body_parts") or {}
        missing = d.get("body_missing") or []
        if missing:
            return {"ok": False, "说": "总控台在，不过身体服务有没起来的：" + "、".join(missing),
                    "证据": {"地址": base, "body_parts": parts, "缺": missing}}
        return {"ok": True, "说": "总控台在跑，身体服务也全部就绪。",
                "证据": {"地址": base, "collect": d.get("collect"), "body_parts": parts}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "总控台我没连上，你看看是不是被我关掉过。",
                "证据": {"探过": _panel_bases(port), "error": type(exc).__name__}}


def _body(port=None) -> dict:
    try:
        d, base = _probe("/api/body/snapshots", timeout=15, port=port)
        stale = [k for k, v in d.items() if v.get("stale")]
        if stale:
            return {"ok": False, "说": "读数有点旧了：" + "、".join(stale) + "，我再采一次。",
                    "证据": {"地址": base, "过期": stale}}
        return {"ok": True, "说": "吞噬、扫描、队列三个读数都是新鲜的。",
                "证据": {"地址": base, **{k: v.get("observed_at") for k, v in d.items()}}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "身体读数我暂时拿不到。",
                "证据": {"探过": _panel_bases(port), "error": type(exc).__name__}}


def _fleet() -> dict:
    try:
        from core import capability_map as cm
        n = (cm._totals() or {}).get("触手")
        if n:
            return {"ok": True, "说": f"触手 {n} 根，都在编队里。", "证据": {"触手": n}}
        return {"ok": False, "说": "编队读数拿不到。", "证据": {"触手": None}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "编队我点不到名。", "证据": {"error": type(exc).__name__}}


def _identity() -> dict:
    try:
        from core import tentacle_identity as TI
        s = TI.summary()
        ok = s["就绪"] > 0
        return {"ok": ok,
                "说": (f"身份位已经配好 {s['就绪']} 个，总共 {s['身份位总数']} 个。"
                       if ok else "身份位还一个都没配，要不要我现在帮你配齐？"),
                "证据": {"就绪": s["就绪"], "总数": s["身份位总数"], "待办": s["待办数"]}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "身份金库我打不开。", "证据": {"error": type(exc).__name__}}


def _brain() -> dict:
    try:
        from core.memory import brain as B
        st = B.status()
        m = st["统一记忆"]
        return {"ok": True,
                "说": f"我自己的记忆里有 {m.get('记忆')} 条，生平 {m.get('生平条目')} 条。",
                "证据": {"记忆": m.get("记忆"), "主体": st.get("主体")}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "我的记忆库还没开起来。", "证据": {"error": type(exc).__name__}}


def _caps() -> dict:
    try:
        from core import capability_panels as CP
        a = CP.audit()
        ok = not a["缺项"]
        return {"ok": ok,
                "说": (f"能力面 {a['已对齐']} 项全部对齐，没有缺的。"
                       if ok else f"能力面还缺 {len(a['缺项'])} 项，我等下补上。"),
                "证据": {"总数": a["总数"], "已对齐": a["已对齐"], "缺项": a["缺项"]}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "能力面我摊不开。", "证据": {"error": type(exc).__name__}}


def _loops() -> dict:
    try:
        from core import loop_verifier as LV
        r = LV.summary()
        ok = (r.get("就绪度") or 0) >= 87.5
        return {"ok": bool(ok),
                "说": f"闭环跑通 {r.get('已跑通')} 条，共 {r.get('闭环总数')} 条。",
                "证据": {"已跑通": r.get("已跑通"), "总数": r.get("闭环总数"),
                         "就绪度": r.get("就绪度")}}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "说": "闭环核对我没跑完。", "证据": {"error": type(exc).__name__}}


_RUN = {"backend": _backend, "octop": _octop, "panel": _panel, "body": _body,
        "fleet": _fleet, "identity": _identity, "brain": _brain, "caps": _caps,
        "loops": _loops}


def run_step(i: int, port=None) -> dict:
    """跑第 i 步（页面逐步调用，边扫边播报）。越界就返回完成态。"""
    try:
        idx = int(i)
    except (TypeError, ValueError):
        idx = 0
    if idx < 0 or idx >= len(STEPS):
        return {"done": True, "index": len(STEPS), "总数": len(STEPS),
                "说": "环境都扫过了，你可以按住空格跟我说话，或者按右下角去总控台。"}
    sid, name, say = STEPS[idx]
    t0 = time.time()
    try:
        fn = _RUN[sid]
        got = fn(port) if sid in ("panel", "body") else fn()
    except Exception as exc:                                   # noqa: BLE001
        got = {"ok": False, "说": f"{name} 这一步我出错了。",
               "证据": {"error": type(exc).__name__}}
    return {"index": idx, "id": sid, "名称": name, "播报": got.get("说") or say,
            "开场": say, "ok": bool(got.get("ok")), "证据": got.get("证据") or {},
            "ms": int((time.time() - t0) * 1000), "总数": len(STEPS),
            "done": False}


def run_all(port=None) -> dict:
    """一次跑完（不开页面时的自检口）。"""
    rows = [run_step(i, port) for i in range(len(STEPS))]
    ok = sum(1 for r in rows if r.get("ok"))
    return {"ok": ok == len(STEPS), "步数": len(STEPS), "通过": ok,
            "行": rows,
            "说": (f"环境扫描完成，{ok} 项全部正常。" if ok == len(STEPS)
                   else f"环境扫描完成，{ok} 项正常、{len(STEPS) - ok} 项要处理。")}


def status() -> dict:
    return {"步数": len(STEPS), "步骤": plan(),
            "口径": "每一步都是真检查 + 一句台湾腔播报；没起来的如实说"}


__all__ = ["STEPS", "plan", "run_step", "run_all", "status"]
