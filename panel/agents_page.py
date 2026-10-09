# panel/agents_page.py —— 智能体工程对话面板（/agents）：名册 · 真对话 · 协作工作流图 · 状态图表
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 内置页（零外链）。数据全部来自 core.agent_chat（真名册目录 + 真问答 + 真记录）：
#   · 左侧：Octop 名册（19 部门 / 272 智能体 / 18 专家），可搜可点，点名后对话带其专长上下文
#   · 中间：对话区（走 V9 驱动链；也可切换"走 Octop 原生 API"）
#   · 右侧：状态图表（名册规模条形 / 通道就绪度 / 最近耗时）
#   · 下方：多智能体协作工作流图（节点图：指挥 → 名册 → 部门分工 → 交接产出）
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends
from fastapi.responses import HTMLResponse
from common.db import get_db

from core import agent_chat as AC
from skills.ui_design import TOKENS, inject

router = APIRouter()


def _c(k: str) -> str:
    return TOKENS["color"].get(k, "#888")


def _workflow_svg(wf: dict) -> str:
    """多智能体协作工作流图（内联 SVG，无外部资源）。"""
    nodes = {n["id"]: n for n in (wf.get("nodes") or [])}
    divs = [n for n in (wf.get("nodes") or []) if n["kind"] == "div"]
    h = 90 + max(1, len(divs)) * 46
    parts = [f'<svg viewBox="0 0 980 {h}" width="100%" height="auto" role="img" '
             f'aria-label="多智能体协作工作流" style="font-family:-apple-system,Segoe UI,'
             f'Microsoft YaHei,sans-serif">',
             '<defs><marker id="aw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
             f'markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" '
             f'fill="{_c("hairline")}"/></marker></defs>']
    # 三列：指挥(40) → 名册(300) → 部门(560) → 产出(830)
    cmdr = nodes.get("cmdr") or {}
    roster = nodes.get("roster") or {}
    out = nodes.get("out") or {}
    box = lambda x, y, w, label, sub, color: (                        # noqa: E731
        f'<rect x="{x}" y="{y}" width="{w}" height="52" rx="10" fill="rgba(13,21,34,.92)" '
        f'stroke="{color}" stroke-width="1.5"/>'
        f'<text x="{x + 14}" y="{y + 22}" font-size="13.5" fill="{_c("text")}">{label}</text>'
        f'<text x="{x + 14}" y="{y + 40}" font-size="11.5" fill="{_c("muted")}">{sub}</text>')
    line = lambda x1, y1, x2, y2: (                                   # noqa: E731
        f'<path d="M {x1} {y1} H {x1 + 20} V {y2} H {x2}" fill="none" '
        f'stroke="{_c("hairline")}" stroke-width="1.5" marker-end="url(#aw)"/>')
    y_mid = h // 2
    parts.append(box(30, y_mid - 26, 200, cmdr.get("标题", "指挥层"), cmdr.get("副标题", ""),
                     _c("accent")))
    parts.append(box(290, y_mid - 26, 220, roster.get("标题", "名册"), roster.get("副标题", ""),
                     _c("accent_2")))
    parts.append(box(810, y_mid - 26, 150, out.get("标题", "交接产出"), out.get("副标题", ""),
                     _c("ok")))
    parts.append(line(230, y_mid, 290, y_mid))
    for i, d in enumerate(divs):
        y = 60 + i * 46
        parts.append(box(560, y, 220, d["标题"], d["副标题"], _c("border")))
        parts.append(line(510, y_mid, 560, y + 26))
        parts.append(f'<path d="M 780 {y + 26} H 795 V {y_mid} H 810" fill="none" '
                     f'stroke="{_c("ok")}" stroke-width="1.2" opacity="0.8"/>')
    parts.append("</svg>")
    return "".join(parts)


def _bars(st: dict) -> str:
    """状态图表：名册规模 + 通道就绪（真数据，纯 CSS 条形）。"""
    ros = st.get("名册", {})
    ch = (st.get("通道") or {}).get("Octop原生API", {})
    rows = [("智能体", ros.get("智能体"), 272), ("专家", ros.get("专家"), 18),
            ("部门", ros.get("部门"), 19)]
    out = []
    for label, val, cap in rows:
        pct = int(min(1.0, (val or 0) / max(1, cap)) * 100)
        out.append(f'<div class=row><div>{label}</div>'
                   f'<div class=bar><i style="width:{pct}%"></i></div>'
                   f'<div class=nums>{val if val is not None else "—"} / {cap}</div></div>')
    ok = "可登录" if ch.get("可登录") else "需账号"
    out.append(f'<p class=muted>V9 驱动链：可用 · Octop 原生 API：{ok}'
               f'（{ch.get("下一步", "")}）· 对话条数：{st.get("对话条数", 0)}</p>')
    return "".join(out)


def _page(st: dict, wf: dict, ros: dict, hist: dict) -> str:
    divs = "".join(
        f'<option value="{d["标题"]}">{d["标题"]}（{d["智能体数"]}）</option>'
        for d in (ros.get("divisions") or []))
    msgs = "".join(
        f'<div class="bubble {"me" if r.get("ok") else ""}">'
        f'<b>{r.get("智能体") or "通才"}</b><span class=muted> · {r.get("通道")} · '
        f'{r.get("at", "")[11:19]}</span><br>{r.get("答") or r.get("问")}</div>'
        for r in (hist.get("rows") or [])[-30:])
    caps = "".join(
        f'<tr><td>{a.get("name") or a.get("id")}</td>'
        f'<td class=muted>{str(a.get("description") or a.get("desc") or "")[:70]}</td></tr>'
        for a in (ros.get("agents") or [])[:60])
    exp = "".join(f'<li>{e.get("name") or e.get("id")}</li>'
                  for e in (ros.get("experts") or [])[:18])
    return f"""
<h1>智能体工程对话面板</h1>
<div class=row>
  <div class=card style="flex:2">
    <b>对话（点名智能体 → 带其专长回答）</b>
    <div class=row style="gap:6px;margin:8px 0">
      <select id=ag style="flex:1">{'<option value="">通才（不点名）</option>'}{divs}</select>
      <label class=muted><input type=checkbox id=useapi> 走 Octop 原生 API</label>
    </div>
    <div id=chat style="max-height:44vh;overflow:auto">{msgs or '<span class=muted>还没有对话</span>'}</div>
    <div class=row style="gap:6px;margin-top:8px">
      <input id=q placeholder="例：把这条需求拆成可执行任务 / 帮我审一下这段代码" style="flex:1">
      <button class=btn onclick="ask()">发送</button>
    </div>
    <p class=muted id=askmsg></p>
  </div>
  <div class=card style="flex:1">
    <b>状态图表</b>
    {_bars(st)}
  </div>
</div>
<h2>多智能体协作工作流</h2>
<div class=card>
  <p class=muted>指挥层下任务 → 名册点名 → 部门分工（{len(wf.get('divisions') or [])} 个部门参与）
     → 交接与产出（对话/执行/审计留痕）。数字来自真实名册。</p>
  <div class=graphbox>{_workflow_svg(wf)}</div>
</div>
<div class=row>
  <div class=card style="flex:2">
    <b>名册 · 智能体（前 60）</b>
    <table class=tbl><thead><tr><th>名称</th><th>专长</th></tr></thead><tbody>{caps}</tbody></table>
  </div>
  <div class=card style="flex:1">
    <b>名册 · 专家（{len(ros.get('experts') or [])}）</b>
    <ul>{exp}</ul>
  </div>
</div>
<style>
.graphbox svg{{border:1px solid rgba(255,255,255,.07);border-radius:12px;
  background:linear-gradient(180deg,rgba(255,255,255,.02),rgba(255,255,255,.005))}}
.tbl{{width:100%;border-collapse:collapse;font-size:13px}}
.tbl th,.tbl td{{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left}}
.row{{display:flex;gap:8px;align-items:center;margin:6px 0}}
.bar{{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:999px;height:10px;overflow:hidden}}
.bar i{{display:block;height:100%;background:var(--accent)}}
.nums{{width:110px;text-align:right;font-variant-numeric:tabular-nums}}
.bubble{{border:1px solid var(--border);border-radius:var(--r-md);padding:8px 10px;margin:6px 0;
  background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.01))}}
.bubble.me{{border-color:rgba(57,208,255,.35)}}
select,input{{background:var(--bg);border:1px solid var(--border);color:var(--text);
  border-radius:var(--r-sm);padding:6px 8px;font:inherit}}
</style>
<script>
async function ask(){{
  var m=document.getElementById('askmsg'); m.textContent='…';
  var body={{text:document.getElementById('q').value,
             agent_key:document.getElementById('ag').value,
             use_octop_api:document.getElementById('useapi').checked}};
  try{{
    var r=await fetch('/api/agents/ask',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify(body)}});
    var d=await r.json();
    m.textContent = d.ok ? ('已回复（'+d['通道']+'）') : ('未成：'+(d.reason||d['元']&&d['元']['失败原因']||''));
    setTimeout(function(){{location.reload()}},600);
  }}catch(e){{ m.textContent='失败：'+e; }}
}}
</script>"""


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(db=Depends(get_db)):
    st = AC.status()
    page = ("<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>GBT小土豆V9 · 智能体工程对话</title></head><body><main id=main>"
            + _page(st, AC.workflow(), AC.roster(), AC.history(30)) +
            "</main></body></html>")
    return inject(page, "/agents")


@router.get("/api/agents/status")
async def api_status():
    return AC.status()


@router.get("/api/agents/roster")
async def api_roster():
    return AC.roster()


@router.get("/api/agents/workflow")
async def api_workflow():
    return AC.workflow()


@router.get("/api/agents/history")
async def api_history(limit: int = 40):
    return AC.history(limit)


@router.post("/api/agents/ask")
async def api_ask(payload: dict = Body(...)):
    return AC.ask(str((payload or {}).get("text") or ""),
                  agent_key=str((payload or {}).get("agent_key") or ""),
                  use_octop_api=bool((payload or {}).get("use_octop_api")))


@router.get("/api/agents/native")
async def api_native():
    return AC.octop_native_status()


@router.post("/api/agents/native/chat")
async def api_native_chat(payload: dict = Body(...)):
    return AC.octop_native_chat(str((payload or {}).get("text") or ""))
