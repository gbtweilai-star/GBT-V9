# panel/db_page.py —— 数据库编队中枢（独立窗口 /db）：10 族 × 10 + 连接可视化 + 双向绑定 + 真建库
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter()

GROUP_COLOR = {
    "core-rdb": "#39d0ff", "timeseries": "#7ee787", "vector": "#ffa657",
    "document": "#d2a8ff", "kv-cache": "#79c0ff", "fulltext": "#ff9bce",
    "graph": "#f2cc60", "queue": "#56d364", "memory": "#ff7b72", "audit": "#8b949e",
}


def _fleet():
    try:
        from panel.deps import db
        from core.db_fleet import DbFleet
        return DbFleet(db)
    except Exception:                                          # noqa: BLE001
        return None


@router.get("/api/db/registry")
async def db_registry():
    from core.db_fleet import registry, validate
    f = _fleet()
    em = (await f.aenabled_map()) if f else {}
    return {"registry": registry(enabled_map=em), "validation": validate()}


@router.get("/api/db/mesh")
async def db_mesh():
    from core.db_fleet import mesh_view, reachable
    from core.db_fleet import SLOT_IDS
    v = mesh_view()
    v["any_to_any_check"] = [
        {"a": SLOT_IDS[0], "b": SLOT_IDS[-1], "reachable": reachable(SLOT_IDS[0], SLOT_IDS[-1])},
        {"a": "t001", "b": SLOT_IDS[0], "reachable": reachable("t001", SLOT_IDS[0])}]
    return v


@router.get("/api/db/state")
async def db_state():
    from core.db_fleet import SLOT_IDS
    f = _fleet()
    st = (await f.astate()) if f else {"reason": "body_ledger_unavailable"}
    return {**st, "slots": len(SLOT_IDS)}


class BindBody(BaseModel):
    tentacle: str
    slot: str
    resource: str = "default"


class ToggleBody(BaseModel):
    slot: str
    enabled: bool | None = None


class BindAllBody(BaseModel):
    tentacles: int = 100


@router.post("/api/db/bind")
async def db_bind(body: BindBody):
    f = _fleet()
    if f is None:
        raise HTTPException(503, "body_ledger_unavailable")
    r = await f.abind(body.tentacle, body.slot, resource=body.resource)
    if not r.get("ok"):
        raise HTTPException(400, r.get("reason") or "bind_failed")
    return r


@router.post("/api/db/unbind")
async def db_unbind(body: BindBody):
    f = _fleet()
    if f is None:
        raise HTTPException(503, "body_ledger_unavailable")
    return await f.aunbind(body.tentacle, body.slot)


@router.post("/api/db/bind_all")
async def db_bind_all(body: BindAllBody):
    f = _fleet()
    if f is None:
        raise HTTPException(503, "body_ledger_unavailable")
    return await f.abind_all(tentacles=body.tentacles)


@router.post("/api/db/toggle")
async def db_toggle(body: ToggleBody):
    f = _fleet()
    if f is None:
        raise HTTPException(503, "body_ledger_unavailable")
    r = await f.atoggle(body.slot, body.enabled)
    if not r.get("ok"):
        raise HTTPException(400, r.get("reason") or "toggle_failed")
    return r


@router.post("/api/db/ensure_all")
async def db_ensure_all():
    """把 100 个库**真的建出来**（幂等）。"""
    from core.db_fleet import ensure_all
    return ensure_all()


@router.get("/api/db/ping/{family}/{slug}")
async def db_ping(family: str, slug: str):
    """真读一次该库（参数绑定；读不到就说读不到）。"""
    from core.db_fleet import ping_slot
    return ping_slot(family, slug)


@router.get("/db", response_class=HTMLResponse)
async def db_page() -> str:
    from core.db_fleet import GROUPS, GROUP_CN, mesh_size, registry
    from skills.ui_design import Page
    reg = registry()
    by_group: dict = {g: [] for g in GROUPS}
    for s in reg["plugins"]:
        by_group[s["group"]].append(s)
    cards = []
    for g in GROUPS:
        color = GROUP_COLOR[g]
        cells = []
        for s in by_group[g]:
            cells.append(
                f'<div class=plug data-key="{s["key"]}" style="border-left:3px solid {color}">'
                f'<div class="pn">{s["slot"]}. {s["slug"]}</div>'
                f'<div class=muted style="font-size:11px">{s["cn"]} · {s["engine"]}</div>'
                f'<div class=muted style="font-size:10px">{s["memory_mb"]}MB · '
                f'{"已建" if s["created"] else "未建"}</div>'
                f'<div style="display:flex;align-items:center;gap:6px;margin-top:4px">'
                f'<span class="sw" id="sw-{g}-{s["slot"]}" data-on="{int(bool(s["enabled"]))}" '
                f'onclick="tog(\'{s["key"]}\', this)"></span>'
                f'<span class=muted style="font-size:10px" id="b-{g}-{s["slot"]}">触手 0</span>'
                f'</div></div>')
        cards.append(
            f'<div class="card" style="margin:8px 0"><div class=muted>'
            f'<b style="color:{color}">{GROUP_CN[g]}</b> · 第 {GROUPS.index(g)+1} 族 · 10 槽</div>'
            f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:8px">'
            f'{"".join(cells)}</div></div>')
    body = f"""
<div class=row>
  <div style="flex:3;min-width:660px">
    <h2>数据库编队 · 100 个（10 族 × 10）</h2>
    <div class=muted>真实槽 {reg["slots_real"]} · 已建库 <b>{reg["created"]}</b> · 库根
      <code>{reg["db_root"]}</code> · 服务端引擎槽 {reg["server_engine_slots"]}（连接串只从环境变量读）</div>
    <div class=row style="margin:8px 0">
      <button class="btn primary" onclick="ensureAll()">一键建齐 100 个库</button>
      <span id=dbmsg class=muted></span>
    </div>
    {"".join(cards)}
  </div>
  <div style="flex:2;min-width:380px">
    <h2>连接可视化面板</h2>
    <div class=card>
      <div class=muted>库全互通网格（任意库 ↔ 任意库）</div>
      <svg id=viz width="100%" height="330" viewBox="0 0 380 330"></svg>
      <div id=vizstat class=muted style="margin-top:6px">加载中…</div>
    </div>
    <div class=card>
      <b>绑定（互相双向）</b>
      <div class=row style="margin-top:8px">
        <input id=bt placeholder="触手 t001" style="width:86px;background:var(--bg);
          border:1px solid var(--border);color:var(--text);border-radius:var(--r-sm);padding:6px">
        <input id=bs placeholder="槽 core-rdb:ledger#1" style="flex:1;background:var(--bg);
          border:1px solid var(--border);color:var(--text);border-radius:var(--r-sm);padding:6px">
        <button class="btn primary" onclick="doBind()">双向绑定</button>
      </div>
      <div class=row style="margin-top:6px">
        <button class=btn onclick="bindAll(1)">把 t001 绑全部 100</button>
        <button class=btn onclick="bindAll(100)">一键全绑 100×100</button>
        <span id=bindmsg class=muted></span>
      </div>
      <div class=muted style="margin-top:8px;font-size:12px">
        绑定即"互相双向"：t→d 与 d→t 各记一行（面板校验对称性）；开关状态同样落库。
      </div>
    </div>
  </div>
</div>
"""
    js = f"""
var GROUPS = {json.dumps(list(GROUPS))};
var GCOLOR = {json.dumps(GROUP_COLOR)};
async function loadViz(){{
  var reg = await (await fetch('/api/db/registry')).json();
  var mesh = await (await fetch('/api/db/mesh')).json();
  var st  = await (await fetch('/api/db/state')).json();
  var svg=document.getElementById('viz'), CX=190, CY=150, R=104, out='', nodes={{}};
  GROUPS.forEach(function(g,gi){{
    var ang=(gi/GROUPS.length)*Math.PI*2-Math.PI/2, gx=CX+Math.cos(ang)*R, gy=CY+Math.sin(ang)*R;
    var items=reg.registry.plugins.filter(function(p){{return p.group===g;}});
    items.forEach(function(p,pi){{
      var a2=(pi/items.length)*Math.PI*2-Math.PI/2;
      nodes[p.key]=[gx+Math.cos(a2)*18, gy+Math.sin(a2)*18];}});
    out+='<text x="'+gx+'" y="'+(gy-24)+'" fill="'+GCOLOR[g]+'" font-size="9" text-anchor="middle">'+reg.registry.groups[g]+'</text>';
    for(var i=0;i<items.length;i++) for(var j=i+1;j<items.length;j++){{
      var a=nodes[items[i].key], b=nodes[items[j].key];
      out+='<line x1="'+a[0]+'" y1="'+a[1]+'" x2="'+b[0]+'" y2="'+b[1]+'" stroke="'+GCOLOR[g]+'" stroke-width="0.4" opacity="0.5"/>';}}
  }});
  var C=GROUPS.map(function(g,gi){{var ang=(gi/GROUPS.length)*Math.PI*2-Math.PI/2;
    return [CX+Math.cos(ang)*R, CY+Math.sin(ang)*R];}});
  for(var i=0;i<C.length;i++) for(var j=i+1;j<C.length;j++){{
    out+='<line x1="'+C[i][0]+'" y1="'+C[i][1]+'" x2="'+C[j][0]+'" y2="'+C[j][1]+'" stroke="#30363d" stroke-width="0.6"/>';}}
  reg.registry.plugins.forEach(function(p){{
    var xy=nodes[p.key], n=st.by_slot[p.key]||0;
    out+='<circle cx="'+xy[0]+'" cy="'+xy[1]+'" r="'+(n?3.6:2.4)+'" fill="'+(n?GCOLOR[p.group]:(p.created?'#3d4652':'#2a3038'))+'"><title>'+p.key+' · 触手 '+n+' · '+(p.created?'已建':'未建')+'</title></circle>';
    var el=document.getElementById('b-'+p.group+'-'+p.slot); if(el) el.textContent='触手 '+n;}});
  svg.innerHTML=out;
  document.getElementById('vizstat').innerHTML=
    '库槽 <b>'+reg.registry.count+'</b> · 族 <b>'+GROUPS.length+'</b> · 已建 <b>'+reg.registry.created+
    '</b> · 全互通边 <b>'+mesh.edge_total+'</b>（组内 '+mesh.intra_group_edges+' / 跨组 '+mesh.cross_group_edges+
    '）<br>已绑 <b>'+(st.bindings||0)+'</b> 对 · 覆盖库 <b>'+(st.slots_bound||0)+
    '</b> · 覆盖触手 <b>'+(st.tentacles_bound||0)+'</b> · 双向对称 <b>'+(st.symmetric?'✅':'❌')+'</b>';
}}
async function tog(key, el){{
  var on=el.getAttribute('data-on')==='1';
  var r=await fetch('/api/db/toggle',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{slot:key, enabled:!on}})}}); var d=await r.json();
  if(d.ok) el.setAttribute('data-on', d.enabled?'1':'0');
}}
async function ensureAll(){{
  var m=document.getElementById('dbmsg'); m.textContent='建库中…';
  var d=await (await fetch('/api/db/ensure_all',{{method:'POST'}})).json();
  m.innerHTML='<span class=ok>新建 '+(d.created||0)+' · 已存在 '+(d.already||0)+' · 失败 '+((d.failed||[]).length)+'</span>';
  loadViz();
}}
async function doBind(){{
  var t=document.getElementById('bt').value.trim(), s=document.getElementById('bs').value.trim();
  var m=document.getElementById('bindmsg'); m.textContent='…';
  var r=await fetch('/api/db/bind',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{tentacle:t,slot:s}})}}); var d=await r.json();
  m.innerHTML = d.ok ? '<span class=ok>已双向绑定 '+t+' ↔ '+s+'</span>' : '<span class=bad>'+(d.detail||'失败')+'</span>';
  loadViz();
}}
async function bindAll(n){{
  var m=document.getElementById('bindmsg'); m.textContent='绑定中…';
  var d=await (await fetch('/api/db/bind_all',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{tentacles:n}})}})).json();
  m.innerHTML='<span class=ok>已绑 '+(d.bound_pairs||0)+' 对（'+((d.bound_pairs||0)*2)+' 行）</span>';
  loadViz();
}}
loadViz(); setInterval(loadViz, 20000);
"""
    return Page(title="数据库编队中枢 · 100 个库", body=body, current="/db",
                extra_css="""
.plug{background:var(--surface_2);border-radius:var(--r-sm);padding:6px 8px}
.plug .pn{font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sw{display:inline-block;width:34px;height:18px;border-radius:999px;background:#30363d;
  position:relative;cursor:pointer;transition:background .15s}
.sw:after{content:'';position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;
  background:#8b949e;transition:all .15s}
.sw[data-on="1"]{background:#1f6feb}.sw[data-on="1"]:after{left:18px;background:#fff}
""", extra_js=js, dock=True).render()
