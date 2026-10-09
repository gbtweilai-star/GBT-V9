# panel/workflow_page.py —— 工作流独立页面（多智能体协作 · 调研前置 · 验收标准）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：工作流要有**独立页面**；每一段写清谁负责、门禁、验收标准；
#   调研没过闸门不许推进；项目做完要能回答"达不达预期标准"。
# 页面口径：所有数字来自 core.workflows / core.market_research 的真读数，
#   采不到证的写"待采证"，绝不把"没验"写成"达标"。
import json

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse

from core import market_research as MR
from core import workflows as W
from skills.ui_design import Page

router = APIRouter()


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if v is not None else "")


def _stage_state_cls(st: str) -> str:
    if "已通过" in st or "已放行" in st:
        return "ok"
    if "阻塞" in st:
        return "bad"
    return "warn"


def _kpis(st: dict) -> str:
    段总数 = sum(r["阶段数"] for r in st["清单"])
    标准数 = sum(r["验收"]["标准数"] for r in st["清单"])
    采证数 = sum(r["验收"]["已通过"] for r in st["清单"])
    return ('<div class=row>'
            + "".join(
                f'<div class=card style="min-width:150px;margin:0"><div class=muted>{k}</div>'
                f'<div class=kpi>{v}</div><div class=muted style="font-size:12px">{n}</div></div>'
                for k, v, n in (
                    ("工作流", st["工作流数"], "条（含调研/协作）"),
                    ("调研放行", st["放行数"], "条闸门已开"),
                    ("被闸门拦住", st["阻塞数"], "条（不盲推）"),
                    ("流程段", 段总数, "段，每段有负责与验收"),
                    ("验收标准", 标准数, f"条，已采证 {采证数}"),
                ))
            + '</div>')


def _gate_banner(st: dict) -> str:
    rows = []
    for r in st["清单"]:
        g = r["调研闸门"]
        cls = "ok" if g["allowed"] else "bad"
        word = "已放行" if g["allowed"] else "闸门未开"
        rows.append(
            f'<tr><td>{_esc(r["名称"])}</td>'
            f'<td class={cls}><b>{word}</b></td>'
            f'<td class=muted>{_esc(g.get("结论") or "—")}</td>'
            f'<td class=muted>{_esc(g.get("证据等级") or "—")}</td>'
            f'<td class=muted>{_esc(g.get("reason") or "")}</td></tr>')
    return ('<div class=card><h2>调研闸门（推进前置）</h2>'
            '<p class=muted>纪律：没有完成市场调研并过闸门，生产段与验收段一律阻塞。'
            '这就是「经过调研再推进」与「盲目推进」的差别 —— 闸门状态是硬读，不是口号。</p>'
            '<table><tr><th>工作流</th><th>闸门</th><th>结论</th><th>证据等级</th><th>说明</th></tr>'
            + "".join(rows) + '</table></div>')


def _stage_pipeline(r: dict) -> str:
    segs = []
    for s in r["阶段"]:
        cls = _stage_state_cls(s["状态"])
        验收 = "".join(f'<li>{_esc(x)}</li>' for x in s["验收"])
        segs.append(
            f'<div class=card style="min-width:250px;flex:1;margin:0">'
            f'<div class=muted style="font-size:12px">{_esc(s["段"])} · {_esc(s["触手"])}</div>'
            f'<b>{_esc(s["名称"])}</b>'
            f'<div class="{cls}" style="margin:4px 0">{_esc(s["状态"])}</div>'
            f'<div class=muted style="font-size:12px">负责：{_esc("、".join(s["负责"]))}</div>'
            f'<div class=muted style="font-size:12px">入：{_esc(s["输入"])} → 出：{_esc(s["产出"])}</div>'
            f'<div class=muted style="font-size:12px">门禁：{_esc(s["门禁"])} · 证据：{_esc(s["证据"])}</div>'
            f'<div style="margin-top:6px;font-size:12px"><b>验收标准</b><ul style="margin:4px 0 0 16px">'
            f'{验收}</ul></div></div>')
    return "".join(segs)


def _acceptance_table(r: dict) -> str:
    a = r["验收"]
    rows = "".join(
        f'<tr><td>{_esc(x["标准"])}</td>'
        f'<td class="{"ok" if x["通过"] is True else ("bad" if x["通过"] is False else "warn")}">'
        f'{_esc(x["判定"])}</td><td class=muted>{_esc(x["证据"])}</td></tr>'
        for x in a["项"])
    return ('<div class=card><h2>预期标准 · 验收</h2>'
            f'<p class=muted>{_esc(a["口径"])}　（标准 {a["标准数"]} · 已通过 {a["已通过"]} · '
            f'待采证 {a["待采证"]} · 未通过 {a["未通过"]}）</p>'
            '<table><tr><th>预期标准</th><th>判定</th><th>证据来源</th></tr>'
            + rows + '</table></div>')


def _research_panel(topic: str) -> str:
    p = MR.plan(topic)
    ev = MR.local_evidence(topic)
    dims = "".join(
        f'<div class=card style="min-width:260px;flex:1;margin:0"><b>{_esc(d["名称"])}</b>'
        f'<div class=muted style="font-size:12px">{_esc(d["来源类型"])} · 需 {_esc(d["需要的样本量"])}</div>'
        f'<ul style="margin:6px 0 0 16px;font-size:12px">'
        + "".join(f'<li>{_esc(q)}</li>' for q in d["要回答的问题"]) + '</ul>'
        f'<div class=muted style="font-size:12px;margin-top:4px">过线：{_esc(d["判定线"])}</div></div>'
        for d in p["维度"])
    observed = "".join(
        f'<tr><td>{_esc(i["项"])}</td><td>{_esc(i["值"])}</td>'
        f'<td class=muted>{_esc(i["来源"])}</td>'
        f'<td class="{"ok" if i["级别"] == "observed" else "warn"}">{_esc(i["级别"])}</td></tr>'
        for i in ev["本机可观测"])
    pending = "".join(
        f'<tr><td>{_esc(x["维度"])}</td><td class=muted>{_esc(x["要补什么"])}</td>'
        f'<td class=warn>{_esc(x["从哪来"])}</td></tr>' for x in ev["本机观不到"])
    return ('<div class=card><h2>市场调研工作台</h2>'
            f'<p class=muted>题材：<b>{_esc(topic)}</b>　'
            '调研方案先立题（7 维度 · 判定线 · 样本量），再收证；'
            '收集不到的维度如实标「需联网/需人补」，不替它编数字。</p>'
            f'<div class=row>{dims}</div>'
            '<h2>本机真读数（观测得到的）</h2>'
            '<table><tr><th>项</th><th>值</th><th>来源</th><th>级别</th></tr>'
            + observed + '</table>'
            '<h2>本机观不到的（必须外部补）</h2>'
            '<table><tr><th>维度</th><th>要补什么</th><th>从哪来</th></tr>'
            + (pending or '<tr><td colspan=3 class=muted>无缺口</td></tr>') + '</table>'
            '<h2>提交调研结论</h2>'
            '<p class=muted>每条必须带来源；维度不齐或无来源会被**拒收**（宁可不收，也不收拍脑袋）。</p>'
            '<textarea id=rfindings style="width:100%;min-height:120px;background:rgba(4,7,13,.6);'
            'color:var(--text);border:1px solid var(--border);border-radius:var(--r-sm);padding:8px" '
            'placeholder=\'[{"维度":"需求","结论":"...","证据":"...","来源":"5 份真实访谈","过线":true}]\'></textarea>'
            '<div style="margin-top:8px"><button class="btn primary" onclick="rsubmit()">提交调研</button>'
            '<button class=btn onclick="rload()">查看闸门</button>'
            '<span id=rmsg class=muted></span></div></div>')


def _squads_table(st: dict) -> str:
    rows = "".join(f'<tr><td>{_esc(s["班"])}</td><td>{_esc(s["从"])}–{_esc(s["到"])}</td>'
                   f'<td class=muted>{_esc(s["职责"])}</td></tr>' for s in st["触手班"])
    return ('<div class=card><h2>100 根触手的分工班</h2>'
            '<p class=muted>四个班区间不重叠、合起来正好覆盖 t001–t100；每段工作流都指派了班。</p>'
            '<table><tr><th>班</th><th>区间</th><th>职责</th></tr>' + rows + '</table></div>')


CLIPS_JS = """
async function wload(){ const r=await fetch('/api/workflows/status'); const d=await r.json();
  return d; }
async function wgraph(){
  const sel=document.getElementById('flowsel').value;
  const d=await (await fetch('/api/workflows/graph?id='+encodeURIComponent(sel))).json();
  document.getElementById('wgraphbox').innerHTML = d.svg || '<span class=muted>无图</span>';
}
async function wpick(){ await wgraph(); }
async function rload(){
  const t=document.getElementById('rtopic').value;
  const d=await (await fetch('/api/workflows/research?topic='+encodeURIComponent(t))).json();
  document.getElementById('rmsg').innerHTML = d.allowed
    ? '<span class=ok>闸门已开：允许推进</span>' : ('<span class=bad>闸门未开：</span>'+(d.reason||''));
}
async function rsubmit(){
  const t=document.getElementById('rtopic').value;
  let arr; try{ arr=JSON.parse(document.getElementById('rfindings').value||'[]'); }
  catch(e){ document.getElementById('rmsg').innerHTML='<span class=bad>JSON 解析失败</span>'; return; }
  const r=await fetch('/api/workflows/research/submit',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:t,findings:arr,by:'面板'})});
  const d=await r.json();
  document.getElementById('rmsg').innerHTML = d.ok
    ? ('<span class=ok>已收：'+d['记录']['结论']+'（等级 '+d['记录']['证据等级']+'）</span>')
    : ('<span class=bad>拒收：</span>'+(d.reason||'')+' '+((d['问题']||[]).join('；')));
}
async function wadvance(){
  const sel=document.getElementById('flowsel').value;
  const sid=document.getElementById('stagesel').value;
  const r=await fetch('/api/workflows/advance',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({flow:sel,stage:sid,by:'面板'})});
  const d=await r.json();
  document.getElementById('wmsg').innerHTML = d.ok
    ? ('<span class=ok>已推进 '+d['段']+'</span>') : ('<span class=bad>被拦：</span>'+(d.reason||''));
}
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target&&e.target.id==='rtopic')rload();});
"""


@router.get("/workflow", response_class=HTMLResponse)
async def workflow_page():
    import asyncio as _a
    st = await _a.to_thread(W.status)                 # 带缓存 + 丢线程：冷启动也不堵事件循环
    cat = W.catalog()
    topic = next((r["题材"] for r in st["清单"] if r["id"] == "shortvideo"), "AI 短视频代做")
    opts = "".join(f'<option value="{_esc(k)}">{_esc(v["名称"])}</option>' for k, v in cat.items())
    _sv = W.flow("shortvideo")
    stagesel = "".join(
        f'<option value="{_esc(s.id)}">{_esc(s.名称)}</option>'
        for s in (_sv.阶段 if _sv else ()))
    body = (
        _kpis(st)
        + _gate_banner(st)
        + '<div class=card><h2>编排图（多智能体协作）</h2>'
          '<div class=row style="align-items:center">'
          f'<select id=flowsel onchange="wpick()" style="padding:6px 10px;border-radius:var(--r-sm);'
          f'background:var(--surface);color:var(--text);border:1px solid var(--border)">{opts}</select>'
          '<button class=btn onclick="wgraph()">刷新编排图</button>'
          f'<select id=stagesel style="padding:6px 10px;border-radius:var(--r-sm);'
          f'background:var(--surface);color:var(--text);border:1px solid var(--border)">{stagesel}</select>'
          '<button class="btn primary" onclick="wadvance()">推进选中段</button>'
          '<span id=wmsg class=muted></span></div>'
          '<div id=wgraphbox class=graphbox style="margin-top:8px"></div></div>'
        + "".join('<div class=card><h2>阶段流水线 · ' + _esc(r["名称"])
                  + '</h2><div class=row>' + _stage_pipeline(r) + '</div></div>'
                  for r in st["清单"])
        + "".join(_acceptance_table(r) for r in st["清单"])
        + _research_panel(topic)
        + _squads_table(st)
        + '<div class=card><h2>口径（可核查）</h2><p class=muted>'
          + _esc(st["纪律"]) + '<br>'
          '· 生产段步骤明细引用 core.pipelines 真部署状态；<br>'
          '· 调研闸门引用 core.market_research 真记录（维度不齐/无来源直接拒收）；<br>'
          '· 验收里能自动判的给真读数，判不了的写「待采证」——绝不把没验写成达标；<br>'
          '· 协作口径对齐 core.orchestration.team（分解→派发→复核→终止→人工接管）。'
          '</p></div>')
    return Page(title="工作流 · 多智能体协作 · 调研前置 · 验收标准", body=body,
                current="/workflow", extra_css="""
.graphbox{overflow:auto}
.card ul li{margin:2px 0}
""", extra_js=CLIPS_JS).render()


# ═══════════════ API ═══════════════
@router.get("/api/workflows/status")
async def api_status():
    return W.status()


@router.get("/api/workflows/catalog")
async def api_catalog():
    return W.catalog()


@router.get("/api/workflows/graph")
async def api_graph(id: str = "shortvideo"):
    import asyncio as _a
    from core import loop_graph as LG

    g = await _a.to_thread(W.graph, id)
    if not g.get("ok"):
        return g
    svg = await _a.to_thread(_render_graph_svg, g)
    return {**g, "svg": svg}


def _render_graph_svg(g: dict) -> str:
    """把工作流节点画成内联 SVG（零外链）。失败返回空串，页面显示占位。"""
    nodes = g.get("nodes") or []
    if not nodes:
        return ""
    w = max(520, 150 * len(nodes))
    h = 260
    gap = (w - 100) / max(1, len(nodes) - 1)
    color = {"start": "#3fb950", "end": "#3fb950", "gate": "#d29922", "stage": "#39d0ff"}
    st_color = {"可执行": "#39d0ff", "已通过": "#3fb950", "已放行": "#3fb950", "阻塞": "#f85149"}
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="auto" role="img" aria-label="工作流编排图" '
             'style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif">',
             '<defs><marker id=warw viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
             'orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8b949e"/></marker></defs>']
    pos = {}
    for i, n in enumerate(nodes):
        x = 60 + i * gap
        pos[n["id"]] = (x, 130)
        parts.append(f'<circle cx="{x:.0f}" cy="130" r="26" fill="rgba(57,208,255,.10)" '
                     f'stroke="{color.get(n["kind"], "#39d0ff")}" stroke-width="2"/>')
        parts.append(f'<text x="{x:.0f}" y="135" text-anchor="middle" font-size="11" '
                     f'fill="{st_color.get(n["状态"], "#dbe7f5")}">{("●" if n["状态"] != "可执行" else "○")}'
                     f'</text>')
        parts.append(f'<text x="{x:.0f}" y="176" text-anchor="middle" font-size="11" fill="#dbe7f5">'
                     f'{n["标题"][:9]}</text>')
        parts.append(f'<text x="{x:.0f}" y="192" text-anchor="middle" font-size="9" fill="#8b949e">'
                     f'{n["副标题"][:14]}</text>')
    for e in g.get("edges") or []:
        if e["from"] in pos and e["to"] in pos:
            x1, y1 = pos[e["from"]]
            x2, y2 = pos[e["to"]]
            parts.append(f'<line x1="{x1 + 26:.0f}" y1="{y1}" x2="{x2 - 30:.0f}" y2="{y2}" '
                         'stroke="#8b949e" stroke-width="1.4" marker-end="url(#warw)"/>')
    allowed = (g.get("闸门") or {}).get("allowed")
    parts.append(f'<text x="12" y="24" font-size="12" fill="{"#3fb950" if allowed else "#f85149"}">'
                 f'调研闸门：{"已放行" if allowed else "未开（生产/验收段阻塞）"}</text>')
    parts.append('</svg>')
    return "".join(parts)


@router.get("/api/workflows/acceptance")
async def api_acceptance(id: str = "shortvideo"):
    return W.acceptance(id)


@router.get("/api/workflows/research")
async def api_research(topic: str = "", plan: int = 0):
    t = topic or "AI 短视频代做"
    if plan:
        return MR.plan(t)
    return MR.gate(t)


@router.get("/api/workflows/research/plan")
async def api_research_plan(topic: str = "AI 短视频代做"):
    return MR.plan(topic)


@router.get("/api/workflows/research/local")
async def api_research_local(topic: str = "AI 短视频代做"):
    return MR.local_evidence(topic)


@router.post("/api/workflows/research/submit")
async def api_research_submit(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    if p.get("dry_run"):
        return MR.submit(str(p.get("topic") or ""), p.get("findings") or [],
                         sources=p.get("sources") or [], by=str(p.get("by") or "面板"), dry_run=True)
    return MR.submit(str(p.get("topic") or ""), p.get("findings") or [],
                     sources=p.get("sources") or [], by=str(p.get("by") or "面板"))


@router.get("/api/workflows/research/history")
async def api_research_history(limit: int = 20):
    return MR.history(limit)


@router.post("/api/workflows/advance")
async def api_advance(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return W.advance(str(p.get("flow") or ""), str(p.get("stage") or ""),
                     by=str(p.get("by") or "面板"), note=str(p.get("note") or ""))


@router.get("/api/workflows/rows")
async def api_rows():
    return {"rows": W.deploy_rows(), "squads": W.squads()}


@router.post("/api/workflows/register")
async def api_register(payload: dict = Body(default_factory=dict)):
    return W.register(note=str((payload or {}).get("note") or ""))


@router.post("/api/workflows/scan")
async def api_scan():
    return W.scan()


__all__ = ["router"]
