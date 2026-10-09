# core/console_hub.py —— 总控台唯一数据源：页面 / 能力图 / 部件读数 / 布局 / 盲区扫描
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用途：所有面板页**只从这里取数**，不再各自去凑（避免"这页有那页没有、数字对不上"）：
#   · pages()    页面登记（分组/路径/标题/是否在册）
#   · graph()    能力图（域 → 能力节点 → 绑定弧；脉冲取**真账本尾**，不是动画）
#   · widgets()  部件读数（固化最近、台账尾、云上3D、渲染引擎、话筒、调度、长任务、镜像、汇报、信息素）
#   · layout()   总控台布局记忆（拖拽后的排布落盘，重新打开还在）
#   · scan()     盲区扫描：页面可达性 / 能力在册 / 闭环断线 / 该收的账
# 纪律：每个部件读数都带"来源"与"是不是真读数"；取不到就写"取不到 + 原因"，不编数字。
from core.swallow import swallow as _swallow
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAYOUT = ROOT / "state" / "console_layout.json"

# ★能力图是 20~40 秒级的重活（域环→节点→弧），页面不该等它。
#   注意：面板默认多 worker，**内存缓存每个 worker 一份**，请求落到没算过的那个 worker 就又要重算
#   → 所以落盘缓存：谁算过都写 state/hub_cache.json，其余 worker 直接读盘（实测 23s → 0.00s）。
_CACHE_FILE = ROOT / "state" / "hub_cache.json"
_TTL = {"graph": 900.0, "scan": 300.0}


def _disk_get(key: str):
    try:
        doc = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        item = doc.get(key) or {}
        if time.time() - float(item.get("at") or 0) <= _TTL.get(key, 300.0):
            return item.get("值")
    except Exception as e:
        _swallow(__file__, e)
    return None


def _disk_put(key: str, val) -> None:
    try:
        doc = {}
        if _CACHE_FILE.is_file():
            doc = json.loads(_CACHE_FILE.read_text(encoding="utf-8")) or {}
        doc[key] = {"at": time.time(), "值": val}
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        _swallow(__file__, e)


DEFAULT_LAYOUT = {
    "版本": 1,
    "行": [
        {"高": "4fr", "列": [
            {"格": "页面登记", "宽": "3fr"}, {"格": "能力图", "宽": "6fr"}, {"格": "部件读数", "宽": "3fr"}]},
        {"高": "5fr", "列": [
            {"格": "主动汇报", "宽": "4fr"}, {"格": "长任务", "宽": "4fr"}, {"格": "镜像排练", "宽": "4fr"}]},
    ],
    "口径": "Bento 式排布：行高 + 每行列宽；拖拽结果落 state/console_layout.json",
}


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return default


def pages() -> dict:
    """页面登记：谁在册、在哪一组、路径是什么。"""
    try:
        from core import page_registry as PR
        # catalog() 的形状：{页面id: {id/标题/路由/分组/一句说明/接口...}}
        cat = PR.catalog() if hasattr(PR, "catalog") else {}
        rows = []
        if isinstance(cat, dict):
            for pid, it in cat.items():
                if not isinstance(it, dict):
                    rows.append({"id": pid, "组": "", "标题": str(it), "路径": ""})
                    continue
                rows.append({"id": it.get("id") or pid, "组": it.get("分组") or "",
                             "标题": it.get("标题") or pid, "路径": it.get("路由") or "",
                             "图标": it.get("图标") or "",
                             "说明": (it.get("一句说明") or "")[:70],
                             "接口数": len(it.get("接口") or [])})
        rows.sort(key=lambda r: (r.get("组") or "", r.get("id") or ""))
        groups: dict = {}
        for r in rows:
            groups[r["组"]] = groups.get(r["组"], 0) + 1
        return {"页面数": len(rows), "分组": groups, "页面": rows,
                "来源": "core/page_registry.catalog()（在册页面唯一登记）"}
    except Exception as exc:                                   # noqa: BLE001
        return {"页面数": 0, "页面": [], "来源": "取不到", "原因": f"{type(exc).__name__}: {exc}"}


def graph() -> dict:
    """能力图：域环 → 能力节点 → 绑定弧；脉冲 = 真台账尾（不是动画）。

    重活走 TTL 缓存（900s）：页面永远秒回，后台心跳负责预热。
    """
    hit = _disk_get("graph")
    if hit is not None:
        return hit
    val = _graph_build()
    _disk_put("graph", val)
    return val


def _graph_build() -> dict:
    out = {"来源": "core/loop_graph.graph()"}
    try:
        from core import loop_graph as LG
        g = LG.graph() if hasattr(LG, "graph") else {}
        out.update(g if isinstance(g, dict) else {"图": g})
    except Exception as exc:                                   # noqa: BLE001
        out.update({"取不到": f"{type(exc).__name__}: {exc}"})
    try:                                                       # 脉冲：台账最后一条的时间
        from core import deploy_ledger as DL
        p = Path(DL.journal_path())
        last = ""
        if p.is_file():
            tail = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
            if tail:
                try:
                    last = json.loads(tail[0]).get("at") or ""
                except Exception:                              # noqa: BLE001
                    last = tail[0][:40]
        out["脉冲"] = last or "（台账没有记录）"
    except Exception as exc:                                   # noqa: BLE001
        out["脉冲"] = f"取不到（{type(exc).__name__}）"
    return out


def widgets() -> dict:
    """部件读数：一格一个真读数，取不到就写原因。"""
    w: dict = {}

    def put(name: str, fn):
        try:
            w[name] = {"读数": fn(), "来源": getattr(fn, "__module__", "") or "core"}
        except Exception as exc:                               # noqa: BLE001
            w[name] = {"读数": None, "来源": "取不到", "原因": f"{type(exc).__name__}: {exc}"[:120]}

    def _solid():
        from core import solidify as S
        names = S.snapshot_names() if hasattr(S, "snapshot_names") else []
        return {"固化组": len(names), "最近": names[-3:] if names else []}

    def _tripo():
        from core import tripo as T
        st = T.status()
        return {"可用": st.get("可用"), "余额": st.get("余额")}

    def _blender():
        from core import blender as B
        st = B.status()
        return {"可用": st.get("可用"), "版本": st.get("版本")}

    def _mic():
        from core import mic_io as M
        st = M.status()
        return {"已部署": st.get("已部署"), "结论": st.get("结论")}

    def _sched():
        from core import sched as SC
        st = SC.status()
        return {"任务数": st.get("任务数"), "到期": len(st.get("到期") or [])}

    def _longrun():
        from core import longrun as L
        st = L.status()
        return {"任务数": st.get("任务数")}

    def _mirror():
        from core import mirror as M
        st = M.status()
        return {"空间数": st.get("空间数")}

    def _proactive():
        from core import proactive as P
        st = P.status()
        return {"说过": st.get("说过"), "总数": st.get("总数")}

    def _stig():
        from core import stigmergy as S
        st = S.status()
        return {"痕迹总数": st.get("痕迹总数"), "分类": st.get("分类")}

    put("固化", _solid)
    put("云上3D", _tripo)
    put("渲染引擎", _blender)
    put("本机话筒", _mic)
    put("心跳调度", _sched)
    put("长任务", _longrun)
    put("镜像排练", _mirror)
    put("主动汇报", _proactive)
    put("信息素", _stig)
    return {"部件数": len(w), "部件": w, "口径": "每格都是真读数；取不到写原因，不编"}


def layout() -> dict:
    """总控台布局（拖拽后落盘，重新打开还在）。"""
    if not LAYOUT.is_file():
        LAYOUT.parent.mkdir(parents=True, exist_ok=True)
        LAYOUT.write_text(json.dumps(DEFAULT_LAYOUT, ensure_ascii=False, indent=2), encoding="utf-8")
    return _read_json(LAYOUT, DEFAULT_LAYOUT)


def save_layout(doc: dict) -> dict:
    """保存布局（只认 行/列 结构；空结构拒收）。"""
    if not isinstance(doc, dict) or not doc.get("行"):
        return {"ok": False, "reason": "布局结构不合法（需要 行+列）"}
    doc = dict(doc)
    doc["版本"] = int(doc.get("版本") or 1)
    doc["保存时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
    LAYOUT.parent.mkdir(parents=True, exist_ok=True)
    LAYOUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "行数": len(doc.get("行") or []), "路径": str(LAYOUT)}


def scan() -> dict:
    """盲区扫描：页面 / 能力 / 闭环 / 该收的账，一次给全（只读，TTL 缓存 300s）。"""
    hit = _disk_get("scan")
    if hit is not None:
        return hit
    val = _scan_build()
    _disk_put("scan", val)
    return val


def _scan_build() -> dict:
    out: dict = {"at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        from core import page_registry as PR
        out["页面审计"] = PR.audit() if hasattr(PR, "audit") else {}
    except Exception as exc:                                   # noqa: BLE001
        out["页面审计"] = {"取不到": f"{type(exc).__name__}: {exc}"}
    try:
        from core import capability_map as CM
        out["能力总计"] = CM._totals()
    except Exception as exc:                                   # noqa: BLE001
        out["能力总计"] = {"取不到": f"{type(exc).__name__}: {exc}"}
    try:
        from core import loop_verifier as LV
        out["闭环"] = LV.status() if hasattr(LV, "status") else {}
    except Exception as exc:                                   # noqa: BLE001
        out["闭环"] = {"取不到": f"{type(exc).__name__}: {exc}"}
    gaps = []
    if isinstance(out.get("页面审计"), dict):
        for k in ("缺失", "missing", "断链", "broken"):
            if out["页面审计"].get(k):
                gaps.append(f"页面：{k}={out['页面审计'][k]}")
    if isinstance(out.get("闭环"), dict):
        for k in ("断线", "broken", "fail"):
            if out["闭环"].get(k):
                gaps.append(f"闭环：{k}={out['闭环'][k]}")
    out["盲区"] = gaps or ["没扫到明显盲区"]
    out["口径"] = "只读扫描：页面/能力/闭环/盲区；有盲区就列出来，不粉饰"
    return out


def snapshot() -> dict:
    """一次性打包（页面 + 图 + 部件 + 布局 + 盲区）。面板各页都从这一份取数。"""
    return {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "pages": pages(), "graph": graph(),
            "widgets": widgets(), "layout": layout(), "scan": scan(),
            "口径": "总控台唯一数据源：页面只读这一份，数字不会有第二份"}


def warm() -> dict:
    """后台预热：把重的两样（能力图 / 盲区扫描）先算好，页面就永远秒回。"""
    t0 = time.time()
    g = graph()
    s1 = scan()
    return {"ok": True, "能力图": bool(g), "盲区条数": len(s1.get("盲区") or []),
            "耗时s": round(time.time() - t0, 1)}


def deploy() -> dict:
    """总装：补齐默认心跳 → 推进一次 → 固化登记。走防偷懒钩子。"""
    from core import hooks as H
    from core import sched as SCH
    g = H.Guard("GBT小土豆V9·自主层总装", must_steps=("心跳在册", "推进一次", "固化登记"))
    with g.step("心跳在册", expect="默认调度齐（信息素衰减/长任务心跳/主动汇报/镜像摘要）") as s:
        made = SCH.ensure_defaults()
        st = SCH.status()
        if st.get("任务数", 0) < 4:
            raise H.HookError(f"默认心跳不齐：只有 {st.get('任务数')} 条")
        s.evidence(新增=made.get("新增"), 任务数=st.get("任务数"),
                   fingerprint=H.fingerprint(st.get("任务数")))
    with g.step("推进一次", expect="tick 真调用处理器（不是空转）") as s:
        got = SCH.tick(limit=4)
        s.evidence(跑了=[x["任务"] for x in got.get("跑了")], 失败=got.get("失败"),
                   fingerprint=H.fingerprint(len(got.get("跑了") or [])))
    with g.step("固化登记", expect="页面/部件/布局都读得到") as s:
        w = widgets()
        p_ = pages()
        if w.get("部件数", 0) < 8 or p_.get("页面数", 0) < 19:
            raise H.HookError("中枢读数不齐：部件或页面少了")
        s.evidence(部件=w["部件数"], 页面=p_["页面数"],
                   fingerprint=H.fingerprint(w["部件数"], p_["页面数"]))
    a = g.finish()
    out = {"ok": True, "部件数": w["部件数"], "页面数": p_["页面数"],
           "心跳": SCH.status().get("任务数"), "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "自主层六件事（主动汇报/心跳/长任务/镜像/信息素/中枢）都在本体内，可读可查"}
    try:
        from core import solidify as S
        S.solidify("v9_organs", {"部件数": w["部件数"], "页面数": p_["页面数"],
                                 "心跳任务": [j["任务"] for j in SCH.status().get("任务") or []]},
                   note="V9 自主层总装（主动汇报·心跳调度·长任务·镜像排练·信息素·数据中枢）")
    except Exception as e:
        _swallow(__file__, e)
    return out


__all__ = ["pages", "graph", "widgets", "layout", "save_layout", "scan", "snapshot",
           "deploy", "warm", "LAYOUT"]
