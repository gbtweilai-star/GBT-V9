# tests/test_workflows_and_research.py —— 工作流 / 市场调研闸门 / 逐段验收 / 按键统一 / 面板对齐
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：工作流要有独立页面；每段写清负责与验收；**调研没过闸门不许推进**；
#   做完要能回答"达不达预期标准"；按键全站统一；每一项能力的控制面板逐项对齐。
# 这些要求全部要有测试守着，不然过两周又会长回去。
import json

import pytest


# ─────────── 市场调研：拒收规则 + 闸门 ───────────
def test_research_plan_covers_seven_dimensions_with_decision_lines():
    from core import market_research as MR
    p = MR.plan("测试题材")
    assert p["ok"] and p["维度数"] == 7
    names = {d["名称"] for d in p["维度"]}
    assert names == {"需求", "竞品", "规模与趋势", "定价与成本", "渠道与触达",
                     "风险与合规", "技术可行性"}
    for d in p["维度"]:
        assert d["要回答的问题"], f"{d['名称']} 没有问题清单"
        assert d["需要的样本量"] and d["判定线"], f"{d['名称']} 没写样本量/判定线"
        assert d["来源类型"] in ("本机可观测", "需联网", "需人补")
    assert p["决策规则"]["建议推进"]


def test_submit_refuses_items_without_source():
    """宁可不收，也不收拍脑袋的结论。"""
    from core import market_research as MR
    bad = MR.submit("驳回测试", [{"维度": "需求", "结论": "有人要", "证据": "我觉得"}])
    assert bad["ok"] is False
    assert "来源" in bad["reason"] or any("来源" in x for x in bad.get("问题") or [])
    bad2 = MR.submit("驳回测试", [{"维度": "不存在的维度", "结论": "x", "证据": "y", "来源": "z"}])
    assert bad2["ok"] is False and bad2["问题"]
    assert MR.submit("驳回测试", [])["ok"] is False


def test_gate_blocks_without_record_and_explains_how_to_open():
    from core import market_research as MR
    g = MR.gate("这个题材绝对没有调研记录-xyz")
    assert g["allowed"] is False and g["state"] == "blocked"
    assert g["reason"] and g["怎么开"]


def test_partial_research_keeps_gate_closed_with_specific_gaps():
    from core import market_research as MR
    topic = "部分调研题材-xyz"
    r = MR.submit(topic, [{"维度": "需求", "结论": "有付费意愿", "证据": "5 份真实访谈",
                           "来源": "5 份真实访谈", "过线": True}], by="测试")
    assert r["ok"] is True
    rec = r["记录"]
    assert rec["结论"] in ("建议推进", "建议不推进", "补证据")
    assert rec["结论"] != "建议推进"                     # 只答一维绝不许放行
    assert len(rec["缺维度"]) == 6
    g = MR.gate(topic)
    assert g["allowed"] is False
    assert any("缺维度" in p for p in g["问题"])


def test_local_evidence_is_observed_or_honestly_pending():
    """本机观得到的标 observed；观不到的标 pending —— 不替它编数字。"""
    from core import market_research as MR
    ev = MR.local_evidence("任意")
    assert ev["本机可观测"], "至少要能读到本机真读数"
    for i in ev["本机可观测"]:
        assert i["级别"] in ("observed", "unavailable")
        assert i["来源"]
    for x in ev["本机观不到"]:
        assert x["从哪来"] in ("需联网", "需人补")
        assert x["要补什么"]


# ─────────── 工作流：调研前置 + 验收标准 ───────────
def test_every_workflow_starts_with_research_and_has_acceptance():
    from core import workflows as W
    assert len(W.WORKFLOWS) >= 6
    for f in W.WORKFLOWS:
        assert f.阶段, f"{f.名称} 没有阶段"
        first = f.阶段[0]
        assert first.段 == "调研", f"{f.名称} 的第一段不是调研"
        assert f.预期标准, f"{f.名称} 没有预期标准（做完凭什么说达标）"
        for s in f.阶段:
            assert s.负责, f"{f.名称}/{s.名称} 没写负责"
            assert s.触手, f"{f.名称}/{s.名称} 没指派触手班"
            assert s.验收, f"{f.名称}/{s.名称} 没有验收标准"
            assert s.证据, f"{f.名称}/{s.名称} 没写证据来源"


def test_tentacle_squads_cover_all_hundred_without_overlap():
    from core import workflows as W
    assert len(W.SQUADS) == 4
    ids = []
    for _n, a, b, _d in W.SQUADS:
        assert a.startswith("t") and b.startswith("t")
        lo, hi = int(a[1:]), int(b[1:])
        assert lo <= hi
        ids += list(range(lo, hi + 1))
    assert sorted(ids) == list(range(1, 101)), "触手班必须不重叠且恰好覆盖 t001–t100"
    assert all(d for *_x, d in W.SQUADS)


def test_production_stage_is_blocked_until_research_gate_opens():
    from core import workflows as W
    topic = "闸门拦截题材-xyz"                       # 没有调研记录
    r = W.advance("shortvideo", "sv_p1", by="测试")
    assert r["ok"] is False
    assert "调研闸门" in r["reason"]
    assert r["怎么开"]
    # 调研段本身允许推进（它是去把证据收回来）
    assert W.advance("shortvideo", "r1", by="测试")["ok"] is True


def test_advance_refuses_unknown_stage_and_flow():
    from core import workflows as W
    assert W.advance("不存在的流程", "x")["ok"] is False
    assert W.advance("shortvideo", "不存在的段")["ok"] is False


def test_acceptance_never_reports_unverified_as_passed():
    """『待采证』不是达标 —— 不许把没验写成通过。"""
    from core import workflows as W
    a = W.acceptance("shortvideo")
    assert a["ok"] and a["标准数"] == len(W.flow("shortvideo").预期标准)
    for x in a["项"]:
        assert x["通过"] in (True, False, None)
        if x["通过"] is None:
            assert x["判定"] == "待采证"
        assert x["证据"]
    assert a["已通过"] == sum(1 for x in a["项"] if x["通过"] is True)


def test_status_and_graph_expose_gate_state_per_flow():
    from core import workflows as W
    st = W.status()
    assert st["工作流数"] == len(W.WORKFLOWS)
    assert st["放行数"] + st["阻塞数"] == st["工作流数"]
    assert len(st["触手班"]) == 4
    g = W.graph("film")
    assert g["ok"] and len(g["nodes"]) == len(W.flow("film").阶段) + 2
    assert len(g["edges"]) >= len(g["nodes"]) - 1
    assert "allowed" in g["闸门"]


# ─────────── 按键统一 / 面板对齐 ───────────
def test_unified_button_system_has_one_family_and_no_undefined_tokens():
    import re
    from skills import ui_design as U
    css = U.base_css() + U.skin_css()
    defined = set(re.findall(r"(?:var\()?(--[A-Za-z0-9_\-]+)\s*:", U.css_vars()))
    used = set(re.findall(r"var\((--[A-Za-z0-9_\-]+)", css))
    assert not (used - defined), f"有未定义的 CSS 变量：{sorted(used - defined)}"
    for cls in (".btn.primary", ".btn.ghost", ".btn.danger", ".btn.on", ".btn.sm",
                ".btn.lg", ".btnbar"):
        assert cls in css, f"统一按键家族缺 {cls}"
    # skin 必须把老页面的 button 归一（含旧类名 .prim/.ghost）
    for rule in ("button{", "button.prim", "button.ghost", "nav.top a.navbtn"):
        assert rule in U.skin_css(), f"skin 缺归一规则 {rule}"


def test_all_pages_are_inside_the_unified_system():
    from core import capability_panels as CP
    u = CP.ui_unify()
    assert u["体系外"] == [], f"这些页面在统一体系之外：{u['体系外']}"
    assert u["在体系内"] == u["页面数"] >= 14
    assert u["残留自写 button 规则合计"] == 0, "页面又各自写 button 样式了"


def test_every_capability_has_panel_source_action_evidence_and_acceptance():
    """五查：面板/状态源/操作或验证/证据/验收标准 —— 缺一即点名。"""
    from core import capability_panels as CP
    a = CP.audit()
    assert a["总数"] >= 20
    assert a["缺项"] == [], f"这些能力项缺字段：{a['缺项']}"
    assert a["已对齐"] == a["总数"]
    for r in a["项"]:
        for k in ("面板", "状态源", "操作", "证据", "验收"):
            assert str(r[k]).strip(), f"{r['能力']} 的 {k} 为空"
    assert a["本轮补齐"]


def test_workflow_page_renders_with_all_sections_and_no_external_links():
    import re
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.workflow_page import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.get("/workflow")
    assert r.status_code == 200
    html = r.text
    for marker in ("调研闸门", "预期标准 · 验收", "100 根触手的分工班", "编排图",
                   "市场调研工作台", "推进选中段"):
        assert marker in html, f"工作流页缺 {marker}"
    assert not re.search(r'href="https?://(?!127\.0\.0\.1)', html), "工作流页不许有外链"
    # API 面
    assert c.get("/api/workflows/status").status_code == 200
    assert c.get("/api/workflows/catalog").status_code == 200
    g = c.get("/api/workflows/graph?id=shortvideo").json()
    assert g["ok"] and g.get("svg")
    assert c.get("/api/workflows/acceptance?id=music").status_code == 200
    assert c.post("/api/workflows/advance",
                  json={"flow": "music", "stage": "mu_p1"}).json()["ok"] is False


def test_page_registry_apis_match_their_documented_shape():
    """接口"接上了"不够 —— 返回的东西必须与它自己承诺的形状一致。

    真踩过：页面与接口共用了同一个缓存键，页面存的是 SVG 字符串，接口就被顶成了
    一串 SVG，拿 d['节点数'] 的调用方全拿到 None。这条测试把形状钉住。
    """
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.capability_page import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    d = c.get("/api/loops/graph").json()
    assert isinstance(d, dict), "闭环图接口必须给节点数据，不能是一串 SVG"
    for k in ("graph", "svg", "节点数", "连线数", "就绪度"):
        assert k in d, f"闭环图接口缺 {k}"
    assert d["节点数"] and d["连线数"]
    assert str(d["svg"]).startswith("<svg")
    # 缓存键必须与页面那份（loops_graph_svg）分开，否则会互相顶掉
    from panel import capability_page as CP
    keys = CP._CACHE.info()["keys"]
    assert "loops_graph_data" in keys or "loops_graph_svg" in keys
    assert not ("loops_graph" in keys), "又共用了会撞车的缓存键"


def test_ble_scan_endpoint_calls_scan_with_keywords():
    """面板一按"执行"就 500 的那类 bug：关键字专用参数被按位置传。

    senses.ble.scan 的 filter_name/rf 是 keyword-only；接线时必须保关键字。
    """
    import inspect
    from senses import ble as SB
    sig = inspect.signature(SB.scan)
    assert sig.parameters["filter_name"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["rf"].kind is inspect.Parameter.KEYWORD_ONLY
    src = inspect.getsource(__import__("panel.ble_page", fromlist=["x"]))
    assert "lambda: sb.scan(" in src, "接线处必须用关键字调用，否则一按就 TypeError→500"
    assert "to_thread(sb.scan, duration" not in src, "别再按位置传 scan 的参数"


def test_workflow_page_is_registered_in_nav():
    from core.page_registry import PAGES
    routes = [p.路由 for p in PAGES]
    assert "/workflow" in routes
    wf = next(p for p in PAGES if p.路由 == "/workflow")
    assert wf.分组 == "指挥"
    assert any(a.startswith("/api/workflows/") for a in wf.接口)
