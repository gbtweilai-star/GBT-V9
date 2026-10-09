from core.swallow import swallow as _swallow
# core/blueprint.py —— 项目 3D 蓝图数据（上帝视角，无死角）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："每个项目要有一个 3D 蓝图显示页面…直接看项目整体和上帝视角
#   看项目无死角，才能做出超越的作品。"
#
# 这里只做**数据**（页面上那层 3D 由 panel/blueprint_page.py 用纯 CSS/SVG 画，零外链）。
# 蓝图的每一层都来自真注册表，数字不对就是图纸不对 —— 所以每层都带来源与占比：
#   层 0 指挥层   页面（按键）× 闭环 × 工作流
#   层 1 能力层   能力项（五查）× 云插件槽 × 库槽 × Octop 原生页
#   层 2 编队层   100 触手 × 身份位（邮箱/云插件/数据库/开源仓库）
#   层 3 底层层   固化组 × 台账 × 记忆
# 另有"关卡层"：调研闸门 / 推进闸门 / 发布门禁 / 钩子闸门 —— 每个都是真状态。
def _pages() -> list:
    try:
        from core.page_registry import PAGES
        return [{"id": p.id, "标题": p.标题, "路由": p.路由, "分组": p.分组, "图标": p.图标}
                for p in PAGES]
    except Exception:                                          # noqa: BLE001
        return []


def _loops() -> list:
    try:
        from core import loop_graph as LG
        g = LG.graph()
        return {"节点": len(g.get("nodes") or []), "边": len(g.get("edges") or []),
                "就绪度": g.get("就绪度")}
    except Exception as exc:                                   # noqa: BLE001
        return {"错误": type(exc).__name__}


def _flows() -> list:
    try:
        from core import workflows as W
        return [{"id": f.id, "名称": f.名称, "阶段": len(f.阶段),
                 "闸门": W.research_gate(f.id).get("allowed")} for f in W.WORKFLOWS]
    except Exception:                                          # noqa: BLE001
        return []


def _caps() -> dict:
    try:
        from core import capability_panels as CP
        a = CP.audit()
        return {"总数": a["总数"], "已对齐": a["已对齐"], "缺项": len(a["缺项"])}
    except Exception:                                          # noqa: BLE001
        return {}


def _slots() -> dict:
    out = {}
    try:
        from core import cloud_plugins as CP
        out["云插件槽"] = len(list(getattr(CP, "PLUGIN_IDS", ()) or ()))
    except Exception:                                          # noqa: BLE001
        out["云插件槽"] = None
    try:
        from core import db_fleet as DF
        out["库槽"] = len(DF.SLOT_IDS)
    except Exception:                                          # noqa: BLE001
        out["库槽"] = None
    try:
        from core import octop_fusion as OF
        out["Octop原生页"] = len(OF.PAGES)
    except Exception:                                          # noqa: BLE001
        out["Octop原生页"] = None
    return out


def _fleet() -> dict:
    out = {}
    # 触手数走 capability_map 的**已缓存**清单（新建 TentacleFleet 会真探通道，冷启十几秒）
    try:
        from core import capability_map as cm
        out["触手"] = (cm._totals() or {}).get("触手")
    except Exception:                                          # noqa: BLE001
        try:
            from core import tentacle_fleet as TF
            out["触手"] = len(getattr(TF.TentacleFleet(), "tentacles", []) or [])
        except Exception:                                      # noqa: BLE001
            out["触手"] = None
    try:
        from core import tentacle_identity as TI
        s = TI.summary()
        out["身份位就绪"] = s["就绪"]
        out["身份位总数"] = s["身份位总数"]
        out["身份待办"] = s["待办数"]
    except Exception:                                          # noqa: BLE001
        out["身份位就绪"] = None
    return out


def _base() -> dict:
    out = {}
    try:
        from core import solidify as S
        out["固化组"] = len(S.status().get("names") or [])
    except Exception:                                          # noqa: BLE001
        out["固化组"] = None
    try:
        from core import deploy_ledger as D
        out["台账条"] = sum((D.summary().get("by_kind") or {}).values())
    except Exception:                                          # noqa: BLE001
        out["台账条"] = None
    try:
        from core.memory import store as MS
        c = MS.store().counts(all_owners=True)
        out["记忆"] = c.get("记忆")
        out["生平"] = c.get("生平条目")
    except Exception:                                          # noqa: BLE001
        out["记忆"] = None
    return out


def _gates() -> list:
    gate_list = []
    try:
        from core import workflows as W
        st = W.status()
        gate_list.append({"关卡": "调研闸门", "开": st["放行数"], "关": st["阻塞数"],
                          "口径": "调研未过 → 生产/验收段不许推进"})
    except Exception as e:
        _swallow(__file__, e)
    try:
        from core import production_gate as PG
        s = PG.summary().get("统计") or {}
        gate_list.append({"关卡": "生产就绪", "已修复": s.get("已修复"),
                          "阻塞": s.get("阻塞（非代码）"), "按设计": s.get("按设计"),
                          "口径": "非生产项必须写明根因与修法"})
    except Exception as e:
        _swallow(__file__, e)
    try:
        from core import capability_panels as CP
        u = CP.ui_unify()
        gate_list.append({"关卡": "按键统一", "体系内": u["在体系内"], "页面数": u["页面数"],
                          "残留自写规则": u["残留自写 button 规则合计"],
                          "口径": "每页都在同一套按键体系里"})
    except Exception as e:
        _swallow(__file__, e)
    try:
        from core import hooks as H
        gate_list.append({"关卡": "钩子闸门", "口径": H.Guard("蓝图自检").audit()["口径"]})
    except Exception as e:
        _swallow(__file__, e)
    return gate_list


_CACHE = {"t": 0.0, "v": None}
_TTL = 60.0


def build(*, fresh: bool = False) -> dict:
    """蓝图的全部数据。层序 = 从指挥到地基，上帝视角一层层往下看。

    带 60 秒缓存：一次 build 要读编队/能力/固化/台账/记忆，实测冷启 30 秒以上 ——
    画图不该等这么久（页面轮询更不行）。
    """
    import time as _t
    if not fresh and _CACHE["v"] is not None and (_t.time() - _CACHE["t"]) < _TTL:
        return _CACHE["v"]
    got = _build()
    _CACHE.update({"t": _t.time(), "v": got})
    return got


def _build() -> dict:
    """真正的组装（无缓存）。"""
    pages = _pages()
    by_group: dict = {}
    for p in pages:
        by_group.setdefault(p["分组"], []).append(p)
    layers = [
        {"层": 0, "名称": "指挥层", "说明": "按键 → 页面 → 闭环 / 工作流", "来源": "page_registry",
         "节点": [{"标题": p["标题"], "副": p["路由"], "组": p["分组"]} for p in pages],
         "统计": {"页面": len(pages), "分组": len(by_group)},
         "分组": {k: len(v) for k, v in by_group.items()},
         "闭环": _loops(), "工作流": _flows()},
        {"层": 1, "名称": "能力层", "说明": "能力项（五查）× 云插件槽 × 库槽 × Octop 原生页",
         "来源": "capability_panels / cloud_plugins / db_fleet / octop_fusion",
         "统计": {**_caps(), **_slots()}},
        {"层": 2, "名称": "编队层", "说明": "100 触手 × 身份位（邮箱/云插件/数据库/开源仓库）",
         "来源": "tentacle_fleet / tentacle_identity", "统计": _fleet()},
        {"层": 3, "名称": "地基", "说明": "固化 / 台账 / 记忆 —— 可回滚的底",
         "来源": "solidify / deploy_ledger / core.memory", "统计": _base()},
    ]
    return {"品牌": "GBT小土豆V9", "层": layers, "关卡": _gates(),
            "上帝视角": {"口径": "每层都标了来源：数字不对就是图纸不对，先修数据再改图",
                         "无死角检查": [
                             {"面": "按键/页面", "有条目": len(pages) > 0},
                             {"面": "能力五查", "有条目": bool(_caps().get("总数"))},
                             {"面": "编队", "有条目": bool((_fleet() or {}).get("触手"))},
                             {"面": "槽位", "有条目": all(v for v in _slots().values())},
                             {"面": "关卡", "有条目": len(_gates()) >= 3},
                             {"面": "地基", "有条目": bool((_base() or {}).get("固化组"))},
                         ]}}


def status() -> dict:
    b = build()
    ok = all(x["有条目"] for x in b["上帝视角"]["无死角检查"])
    return {"层数": len(b["层"]), "关卡数": len(b["关卡"]), "无死角": ok,
            "缺面": [x["面"] for x in b["上帝视角"]["无死角检查"] if not x["有条目"]]}


def register(*, note: str = "") -> dict:
    from core import solidify as S
    r = S.solidify("blueprint", build(), note=note or "项目 3D 蓝图（上帝视角）")
    try:
        from core import deploy_ledger as DL
        st = status()
        DL.record("scan", "blueprint",
                  detail=f"层 {st['层数']} 关卡 {st['关卡数']} 无死角 {st['无死角']}",
                  before="", after=str(r.get("rev") or r.get("version")), ok=bool(st["无死角"]))
    except Exception as e:
        _swallow(__file__, e)
    return r


__all__ = ["build", "status", "register"]
