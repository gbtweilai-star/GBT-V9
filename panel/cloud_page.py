# panel/cloud_page.py —— 云插件中枢（独立窗口 /cloud）：CF Workers AI · 10 族×10 槽 + 开关 + 连接可视化
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter()

GROUP_COLOR = {
    "text-generation": "#39d0ff", "embedding": "#7ee787", "image-generation": "#ffa657",
    "vision": "#d2a8ff", "asr": "#79c0ff", "tts": "#ff9bce", "translation": "#f2cc60",
    "classification": "#56d364", "code": "#8b949e", "guard": "#ff7b72",
}


def _hub():
    """绑定/开关写可写身体库（面板只读库写不了）。"""
    try:
        from panel.deps import db
        from core.cloud_plugins import CloudHub
        return CloudHub(db)
    except Exception:                                          # noqa: BLE001
        return None


@router.get("/api/cloud/registry")
async def cloud_registry():
    from core.cloud_plugins import registry, validate
    hub = _hub()
    em = (await hub.aenabled_map()) if hub else {}
    return {"registry": registry(enabled_map=em), "validation": validate()}


@router.get("/api/cloud/mesh")
async def cloud_mesh():
    from core.cloud_plugins import reachable, mesh_view
    v = mesh_view()
    v["any_to_any_check"] = [
        {"a": "text-generation:qwq-32b#8", "b": "guard:llama-guard-3-8b#1",
         "reachable": reachable("text-generation:qwq-32b#8", "guard:llama-guard-3-8b#1")},
        {"a": "t001", "b": "tts:aura-1#1", "reachable": reachable("t001", "tts:aura-1#1")}]
    return v


@router.get("/api/cloud/state")
async def cloud_state():
    from core.cloud_plugins import PLUGIN_IDS
    hub = _hub()
    st = (await hub.astate()) if hub else {"reason": "body_ledger_unavailable"}
    return {**st, "plugin_slots": len(PLUGIN_IDS),
            "tentacle_slots": len(hub.tentacle_ids()) if hub else 100}


class BindBody(BaseModel):
    tentacle: str
    plugin: str
    resource: str = "default"


class ToggleBody(BaseModel):
    plugin: str
    enabled: bool | None = None


class BindAllBody(BaseModel):
    tentacles: int = 100


@router.post("/api/cloud/bind")
async def cloud_bind(body: BindBody):
    hub = _hub()
    if hub is None:
        raise HTTPException(503, "body_ledger_unavailable")
    r = await hub.abind(body.tentacle, body.plugin, resource=body.resource)
    if not r.get("ok"):
        raise HTTPException(400, r.get("reason") or "bind_failed")
    return r


@router.post("/api/cloud/unbind")
async def cloud_unbind(body: BindBody):
    hub = _hub()
    if hub is None:
        raise HTTPException(503, "body_ledger_unavailable")
    return await hub.aunbind(body.tentacle, body.plugin)


@router.post("/api/cloud/bind_all")
async def cloud_bind_all(body: BindAllBody):
    hub = _hub()
    if hub is None:
        raise HTTPException(503, "body_ledger_unavailable")
    return await hub.abind_all(tentacles=body.tentacles)


@router.post("/api/cloud/toggle")
async def cloud_toggle(body: ToggleBody):
    """插件启用开关（对应面板上的 switch）。"""
    hub = _hub()
    if hub is None:
        raise HTTPException(503, "body_ledger_unavailable")
    r = await hub.atoggle(body.plugin, body.enabled)
    if not r.get("ok"):
        raise HTTPException(400, r.get("reason") or "toggle_failed")
    return r


@router.post("/api/cloud/share_all")
async def cloud_share_all():
    """一键内部互绑：全部 C(100,2)=4950 对，每对两个方向各一行。"""
    hub = _hub()
    if hub is None:
        raise HTTPException(503, "body_ledger_unavailable")
    return await hub.ashare_all()


@router.get("/api/cloud/share_state")
async def cloud_share_state():
    hub = _hub()
    if hub is None:
        return {"shared_pairs": 0, "reason": "body_ledger_unavailable"}
    st = await hub.ashare_state()
    from core.cloud_plugins import share_registry_note
    return {**st, **share_registry_note()}


class ShareBody(BaseModel):
    a: str
    b: str
    resource: str = "shared"


@router.post("/api/cloud/share")
async def cloud_share(body: ShareBody):
    hub = _hub()
    if hub is None:
        raise HTTPException(503, "body_ledger_unavailable")
    r = await hub.ashare_bond(body.a, body.b, resource=body.resource)
    if not r.get("ok"):
        raise HTTPException(400, r.get("reason") or "share_failed")
    return r


@router.get("/api/cloud/egress")
async def cloud_egress():
    """每个云插件的独立 IP 出口（公共代理池；凭据只从环境变量读）。"""
    from core.cloud_plugins import egress_report
    return egress_report()


@router.get("/api/cloud/sync")
async def cloud_sync():
    """与你自己导出的官方目录快照核对（本服务不持凭据、不发请求）。"""
    from core.cloud_plugins import sync_from_snapshot
    return sync_from_snapshot()


# ── 云终端（免费算力 · 本地 0 显存）：一排 10 槽 + freebuff 优先 ──
@router.get("/api/cloud/terminal")
async def cloud_terminal_registry():
    from core import cloud_terminal as CT
    return CT.registry()


@router.get("/api/cloud/terminal/signup_order")
async def cloud_terminal_signup_order(slot: int):
    """给触手的一张开户作业单（我不执行，触手执行）。"""
    from core import cloud_terminal as CT
    return CT.signup_order(slot)


@router.post("/api/cloud/terminal/signup_done")
async def cloud_terminal_signup_done(body: TerminalBody, verified: bool = False):
    from core import cloud_terminal as CT
    return CT.signup_done(body.slot, handle=body.tentacle, verified=verified)


# ── 云上大模型拼接（plugin_key → 真 id → 开关 → 绑定 → 出网跑）──
@router.get("/api/cloud/quota")
async def cloud_quota():
    """额度读数（能读就报数；读不到如实说缺什么权限）。"""
    from core import cloud_runner as CR
    return CR.quota()


@router.get("/api/local-llm/readiness")
async def local_llm_readiness():
    """本地大模型前置体检（只读，不装东西）：三条通道的"能不能用"之一。"""
    from core import local_llm_probe as LP
    return LP.verdict()


@router.get("/api/cloud/neurons")
async def cloud_neurons():
    """今日 neuron 记账（真值优先）+ 并发闸读数。"""
    from core import cloud_runner as CR
    return {"今日": CR.neurons_today(), "闸": CR.quota_gate()}


@router.get("/api/cloud/splice")
async def cloud_splice():
    from core import cloud_runner as CR
    return CR.splice_report()


class CloudRunBody(BaseModel):
    plugin: str
    prompt: str = "用一句话说明你在线"
    tentacle: str = ""


@router.post("/api/cloud/run")
async def cloud_run(body: CloudRunBody):
    """按插件键真跑一次（预留槽/关着的/没绑定的会如实拒）。"""
    from core import cloud_runner as CR
    return CR.run_plugin(body.plugin, body.prompt, tentacle=body.tentacle)


@router.post("/api/cloud/terminal/auto")
async def cloud_terminal_auto(body: TerminalBody | None = None):
    from core import cloud_terminal as CT
    return CT.auto()


@router.get("/api/cloud/terminal/check")
async def cloud_terminal_check(slot: int):
    from core import cloud_terminal as CT
    return CT.check(slot)


class TerminalBody(BaseModel):
    slot: int
    tentacle: str = ""
    provider: str = "freebuff"


@router.post("/api/cloud/terminal/assign")
async def cloud_terminal_assign(body: TerminalBody):
    from core import cloud_terminal as CT
    r = CT.assign(body.slot, body.tentacle, provider=body.provider)
    if not r.get("ok"):
        raise HTTPException(400, r.get("reason") or "assign_failed")
    return r


@router.post("/api/cloud/terminal/release")
async def cloud_terminal_release(body: TerminalBody):
    from core import cloud_terminal as CT
    return CT.release(body.slot)


@router.get("/cloud", response_class=HTMLResponse)
async def cloud_page() -> str:
    """云插件页 = 原来的内容 + 末尾独立隔开的「云终端」板块（包装器，不动原函数内部）。"""
    return _with_terminal(await _cloud_page_impl())


def _with_terminal(html: str) -> str:
    from core.cloud_terminal import section
    s = section()
    return html.replace("</body>", s + "</body>", 1) if "</body>" in html else html + s


async def _cloud_page_impl() -> str:
    from core.cloud_plugins import GROUPS, GROUP_CN, mesh_size, registry
    from skills.ui_design import Page
    reg = registry()
    by_group: dict = {g: [] for g in GROUPS}
    for p in reg["plugins"]:
        by_group[p["group"]].append(p)

    cards = []
    for g in GROUPS:
        color = GROUP_COLOR[g]
        cells = []
        for p in by_group[g]:
            badge = ('<span class=badge style="border-color:#d29922">预留</span>'
                     if p["reserved"] else
                     ('' if p["cf_id"] else
                      '<span class=badge style="border-color:#d29922">id待核</span>'))
            cells.append(
                f'<div class=plug data-key="{p["key"]}" '
                f'style="border-left:3px solid {color};opacity:{0.45 if p["reserved"] else 1}">'
                f'<div class="pn">{p["slot"]}. {p["slug"]}</div>'
                f'<div class=muted style="font-size:11px">{p["cn"]} {badge}</div>'
                f'<div class=muted style="font-size:10px" title="{p["cf_id"] or ""}">'
                f'{(p["cf_id"] or "—")[:26]}</div>'
                f'<div style="display:flex;align-items:center;gap:6px;margin-top:4px">'
                f'<span class="sw" id="sw-{g}-{p["slot"]}" data-on="{int(bool(p["enabled"]))}" '
                f'onclick="tog(\'{p["key"]}\', this)" title="启用/停用"></span>'
                f'<span class=muted style="font-size:10px" id="b-{g}-{p["slot"]}">触手 0</span>'
                f'</div></div>')
        cards.append(
            f'<div class="card" style="margin:8px 0"><div class=muted>'
            f'<b style="color:{color}">{GROUP_CN[g]}</b> · 第 {GROUPS.index(g)+1} 族 · 10 槽</div>'
            f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:8px">'
            f'{"".join(cells)}</div></div>')

    extra_css = """
.plug{background:var(--surface_2);border-radius:var(--r-sm);padding:6px 8px}
.plug .pn{font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sw{display:inline-block;width:34px;height:18px;border-radius:999px;background:#30363d;
  position:relative;cursor:pointer;transition:background .15s}
.sw:after{content:'';position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;
  background:#8b949e;transition:all .15s}
.sw[data-on="1"]{background:#1f6feb}.sw[data-on="1"]:after{left:18px;background:#fff}
"""
    body = f"""
<div class=row>
  <div style="flex:3;min-width:660px">
    <h2>Cloudflare Workers AI · 100 个插件（10 族 × 10 槽）</h2>
    <div class=muted>来源：{reg["source"]} · 真实模型 {reg["slots_real"]} 槽
      （id 已确认 {reg["ids_confirmed"]} / 待核 {reg["ids_unverified"]}）· 预留 {reg["slots_reserved"]} 槽</div>
    {"".join(cards)}
  </div>
  <div style="flex:2;min-width:380px">
    <h2>连接可视化面板</h2>
    <div class=card>
      <div class=muted>插件全互通网格（任意插件 ↔ 任意插件）</div>
      <svg id=viz width="100%" height="330" viewBox="0 0 380 330"></svg>
      <div id=vizstat class=muted style="margin-top:6px">加载中…</div>
    </div>
    <div class=card>
      <b>共享资源速度</b> <span class=muted id=spdnote></span>
      <div id=spdstat class=muted style="margin-top:6px">加载中…</div>
      <svg id=spd width="100%" height="86" viewBox="0 0 380 86"></svg>
      <table style="margin-top:6px"><tr><th>热链（触手）</th><th>速率</th><th>均值</th><th>tokens</th><th>成功</th></tr>
        <tbody id=spdtop></tbody></table>
      <div class=muted style="font-size:11px;margin-top:6px" id=spdshared></div>
    </div>
    <div class=card>
      <b>连接状态表</b>
      <div class=row style="margin:6px 0;gap:6px">
        <input id=lq placeholder="搜插件/中文名" style="flex:1;background:var(--bg);
          border:1px solid var(--border);color:var(--text);border-radius:var(--r-sm);padding:5px 8px">
        <select id=lg style="background:var(--bg);border:1px solid var(--border);
          color:var(--text);border-radius:var(--r-sm);padding:5px"></select>
        <label class=muted style="font-size:12px"><input type=checkbox id=lo style="width:auto"> 只看在岗</label>
      </div>
      <div style="max-height:300px;overflow:auto"><table id=linktable>
        <tr><th>槽位/模型</th><th>族</th><th>启用</th><th>IP 出口</th><th>绑定触手</th>
            <th>内部互绑</th><th>模型</th><th>连接状态</th></tr>
        <tbody id=linkrows></tbody></table></div>
      <div class=muted style="font-size:11px;margin-top:4px" id=linkstat></div>
    </div>
    <div class=card>
      <b>绑定（互相双向）</b>
      <div class=row style="margin-top:8px">
        <input id=bt placeholder="触手 t001" style="width:86px;background:var(--bg);
          border:1px solid var(--border);color:var(--text);border-radius:var(--r-sm);padding:6px">
        <input id=bp placeholder="插件 tts:aura-1#1" style="flex:1;background:var(--bg);
          border:1px solid var(--border);color:var(--text);border-radius:var(--r-sm);padding:6px">
        <button class="btn primary" onclick="doBind()">双向绑定</button>
      </div>
      <div class=row style="margin-top:6px">
        <button class=btn onclick="bindAll(1)">把 t001 绑全部 100</button>
        <button class=btn onclick="bindAll(100)">一键全绑 100×100</button>
        <button class=btn onclick="shareAll()">一键内部互绑（全互通双向）</button>
        <span id=bindmsg class=muted></span>
      </div>
      <div class=muted style="margin-top:8px;font-size:12px">
        绑定即"互相双向"：t→p 与 p→t 各记一行（面板校验对称性）；
        开关状态也落库。官方目录核对：GET /api/cloud/sync（用你自己导出的 state/cf_models.json）。
      </div>
    </div>
  </div>
</div>
"""
    js = f"""
var GROUPS = {json.dumps(list(GROUPS))};
var GCOLOR = {json.dumps(GROUP_COLOR)};
async function loadViz(){{
  var reg = await (await fetch('/api/cloud/registry')).json();
  var mesh = await (await fetch('/api/cloud/mesh')).json();
  var st  = await (await fetch('/api/cloud/state')).json();
  var svg=document.getElementById('viz'), W=380,H=330,CX=190,CY=150,R=104;
  var out='', nodes={{}};
  GROUPS.forEach(function(g,gi){{
    var ang=(gi/GROUPS.length)*Math.PI*2-Math.PI/2, gx=CX+Math.cos(ang)*R, gy=CY+Math.sin(ang)*R;
    var items=reg.registry.plugins.filter(function(p){{return p.group===g;}});
    items.forEach(function(p,pi){{
      var a2=(pi/items.length)*Math.PI*2-Math.PI/2;
      nodes[p.key]=[gx+Math.cos(a2)*18, gy+Math.sin(a2)*18];
    }});
    out+='<text x="'+gx+'" y="'+(gy-24)+'" fill="'+GCOLOR[g]+'" font-size="9" text-anchor="middle">'+reg.registry.groups[g]+'</text>';
    for(var i=0;i<items.length;i++) for(var j=i+1;j<items.length;j++){{
      var a=nodes[items[i].key], b=nodes[items[j].key];
      out+='<line x1="'+a[0]+'" y1="'+a[1]+'" x2="'+b[0]+'" y2="'+b[1]+'" stroke="'+GCOLOR[g]+'" stroke-width="0.4" opacity="0.5"/>';
    }}
  }});
  var C=GROUPS.map(function(g,gi){{var ang=(gi/GROUPS.length)*Math.PI*2-Math.PI/2;
    return [CX+Math.cos(ang)*R, CY+Math.sin(ang)*R];}});
  for(var i=0;i<C.length;i++) for(var j=i+1;j<C.length;j++){{
    out+='<line x1="'+C[i][0]+'" y1="'+C[i][1]+'" x2="'+C[j][0]+'" y2="'+C[j][1]+'" stroke="#30363d" stroke-width="0.6"/>';
  }}
  reg.registry.plugins.forEach(function(p){{
    var xy=nodes[p.key], n=st.by_plugin[p.key]||0;
    out+='<circle cx="'+xy[0]+'" cy="'+xy[1]+'" r="'+(n?3.6:2.4)+'" fill="'+(p.reserved?'#30363d':(n?GCOLOR[p.group]:'#3d4652'))+'"><title>'+p.key+' · 触手 '+n+'</title></circle>';
    var el=document.getElementById('b-'+p.group+'-'+p.slot);
    if(el) el.textContent='触手 '+n;
  }});
  try{{ var eg=await (await fetch('/api/cloud/egress')).json();
    reg.registry.plugins.forEach(function(p,idx){{
      var n=String(idx+1).padStart(3,'0');
      var el=document.getElementById('e-'+p.group+'-'+p.slot);
      if(el){{ el.textContent='IP eg-'+n; el.style.borderColor = eg.isolated?'#3fb950':'#d29922'; }}
    }});
    document.getElementById('vizstat').innerHTML +=
      '<br>出口隔离 <b>'+(eg.isolated?'✅ 每插件独立 IP（公共池':'⚠ 未配（直连）池')+eg.pool+'</b>';
    try{{ var sh=await (await fetch('/api/cloud/share_state')).json();
      document.getElementById('vizstat').innerHTML +=
        ' · 内部互绑共享 <b>'+(sh.shared_pairs||0)+'/'+(sh.expected_pairs||4950)+
        '</b> 对（双向 '+(sh.symmetric?'✅':'❌')+'）'; }}catch(e){{}}
  }}catch(e){{}}
  svg.innerHTML=out;
  document.getElementById('vizstat').innerHTML=
    '插件 <b>'+reg.registry.count+'</b> · 族 <b>'+GROUPS.length+'</b> · 全互通边 <b>'+mesh.edge_total+
    '</b>（组内 '+mesh.intra_group_edges+' / 跨组 '+mesh.cross_group_edges+'）<br>已绑 <b>'+(st.bindings||0)+
    '</b> 对 · 覆盖插件 <b>'+(st.plugins_bound||0)+'</b> · 覆盖触手 <b>'+(st.tentacles_bound||0)+
    '</b> · 双向对称 <b>'+(st.symmetric?'✅':'❌')+'</b> · 已启用 <b>'+(st.enabled||0)+'</b>';
}}
async function tog(key, el){{
  var on = el.getAttribute('data-on')==='1';
  var r=await fetch('/api/cloud/toggle',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{plugin:key, enabled:!on}})}});
  var d=await r.json(); if(d.ok) el.setAttribute('data-on', d.enabled?'1':'0');
}}
async function doBind(){{
  var t=document.getElementById('bt').value.trim(), p=document.getElementById('bp').value.trim();
  var m=document.getElementById('bindmsg'); m.textContent='…';
  var r=await fetch('/api/cloud/bind',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{tentacle:t,plugin:p}})}}); var d=await r.json();
  m.innerHTML = d.ok ? '<span class=ok>已双向绑定 '+t+' ↔ '+p+'</span>' : '<span class=bad>'+(d.detail||'失败')+'</span>';
  loadViz();
}}
async function shareAll(){{
  var m=document.getElementById('bindmsg'); m.textContent='内部互绑中…（4950 对）';
  var d=await (await fetch('/api/cloud/share_all',{{method:'POST'}})).json();
  m.innerHTML='<span class=ok>内部互绑 '+(d.shared_pairs||0)+' 对（'+((d.shared_pairs||0)*2)+' 行）</span>';
  loadViz();
}}
async function bindAll(n){{
  var m=document.getElementById('bindmsg'); m.textContent='绑定中…';
  var r=await fetch('/api/cloud/bind_all',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{tentacles:n}})}}); var d=await r.json();
  m.innerHTML='<span class=ok>已绑 '+(d.bound_pairs||0)+' 对（'+((d.bound_pairs||0)*2)+' 行）</span>';
  loadViz();
}}
async function loadSpeed(){{
  try{{
    var d=await (await fetch('/api/cloud/speed?window=900')).json();
    document.getElementById('spdnote').textContent='（窗口 '+(d.window_s||0)+'s · 单位 '+(d.unit||'')+'）';
    var st=document.getElementById('spdstat');
    if(d.no_calls_in_window){{
      st.innerHTML='<span class=warn>窗口内没有驱动记录 → 速度按 0 报</span> <span class=muted>'+((d.note||'').slice(0,60))+'</span>';
    }}else{{
      st.innerHTML='总速率 <b>'+(d.rate_per_min||0)+'</b> 次/分 · 调用 <b>'+(d.calls||0)+
        '</b> 次 · 平均 <b>'+(d.avg_ms||0)+'</b> ms · tokens <b>'+(d.tokens||0)+
        '</b> · 成功率 <b>'+((d.ok_rate==null)?'-':(d.ok_rate*100).toFixed(1)+'%')+'</b>';
    }}
    var s=d.buckets_series||[], mx=Math.max.apply(null,s.concat([1])), out='';
    s.forEach(function(v,i){{
      var h=Math.round((v/mx)*66), x=6+i*31;
      out+='<rect x="'+x+'" y="'+(70-h)+'" width="22" height="'+h+'" fill="'+(v?'#39d0ff':'#30363d')+'" rx="2"><title>桶'+(i+1)+'：'+v+' 次</title></rect>';
      out+='<text x="'+(x+11)+'" y="82" fill="#8b949e" font-size="8" text-anchor="middle">'+v+'</text>';
    }});
    document.getElementById('spd').innerHTML=out;
    document.getElementById('spdtop').innerHTML=(d.top_links||[]).map(function(x){{
      return '<tr><td>'+x.tentacle+'</td><td>'+x.rate_per_min+' 次/分</td><td>'+x.avg_ms+' ms</td><td>'+
             x.tokens+'</td><td>'+((x.ok_rate*100).toFixed(0))+'%</td></tr>';}}).join('')||
      '<tr><td colspan=5 class=muted>窗口内没有链路活动</td></tr>';
    document.getElementById('spdshared').innerHTML='共享调用（只读工具按域）：'+((d.shared_calls||[]).map(function(x){{
      return x.domain+' '+x.calls+' 次('+x.rate_per_min+'/分)';}}).join(' · ')||'窗口内无');
  }}catch(e){{ document.getElementById('spdstat').textContent='速度读数取不到：'+e.message; }}
}}
async function loadLinks(){{
  try{{
    var q=document.getElementById('lq').value.trim();
    var g=document.getElementById('lg').value;
    var o=document.getElementById('lo').checked?1:0;
    var d=await (await fetch('/api/cloud/links?q='+encodeURIComponent(q)+'&group='+encodeURIComponent(g)+'&only_on='+o)).json();
    var sel=document.getElementById('lg');
    if(sel.options.length===0){{ for(var k in d.families){{ var op=document.createElement('option');
      op.value=k; op.textContent=d.families[k]; sel.appendChild(op);}} }}
    document.getElementById('linkrows').innerHTML=(d.items||[]).map(function(x){{
      var col = x.state.indexOf('在岗·已绑')>=0?'g':(x.state.indexOf('停用')>=0?'y':(x.state==='预留'?'muted':'y'));
      return '<tr><td title="'+x.key+'">'+x.slot+'. '+x.slug+'</td><td>'+x.family+'</td>'+
        '<td>'+(x.enabled?'<span class=g>开</span>':'<span class=muted>关</span>')+'</td>'+
        '<td class=muted>'+x.egress+'</td><td>'+x.bound_tentacles+'</td><td>'+x.shared_links+'</td>'+
        '<td>'+(x.model_ready?'<span class=g>就绪</span>':'<span class=warn>待核</span>')+'</td>'+
        '<td class="'+col+'">'+x.state+'</td></tr>';}}).join('')||'<tr><td colspan=8 class=muted>没有符合条件的插件</td></tr>';
    document.getElementById('linkstat').textContent='显示 '+d.count+' / '+d.total+' 个槽';
  }}catch(e){{ document.getElementById('linkstat').textContent='连接状态表取不到：'+e.message; }}
}}
document.getElementById('lq').addEventListener('input', loadLinks);
document.getElementById('lg').addEventListener('change', loadLinks);
document.getElementById('lo').addEventListener('change', loadLinks);
loadLinks(); loadSpeed();
setInterval(loadSpeed, 15000); setInterval(loadLinks, 60000);
loadViz(); setInterval(loadViz, 20000);
"""
    return Page(title="云插件中枢 · Cloudflare Workers AI", body=body, current="/cloud",
                extra_css=extra_css, extra_js=js, dock=True).render()
