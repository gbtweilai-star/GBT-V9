# panel/octop_page.py —— Octop 能力桥（独立窗口 /octop）：339 项能力 × 100 触手 1:1 双向绑定
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from common.db import get_db, fetch_all

router = APIRouter(prefix="/api/octop")


def _bridge():
    try:
        from panel.deps import db
        from core.octop_bridge import OctopBridge
        return OctopBridge(db)
    except Exception:                                          # noqa: BLE001
        return None


@router.get("/catalog")
async def octop_catalog():
    from core.octop_bridge import catalog, families
    return {"catalog": catalog(), "families": families()}


@router.get("/state")
async def octop_state():
    from core.octop_bridge import capability_ids, catalog
    b = _bridge()
    st = (await b.astate()) if b else {"reason": "body_ledger_unavailable"}
    c = catalog()
    return {**st, "divisions": len(c.get("divisions", [])),
            "agents": c.get("counts", {}).get("agents", 0),
            "experts": c.get("counts", {}).get("experts", 0),
            "plugins": c.get("counts", {}).get("plugins", 0),
            "skills": c.get("counts", {}).get("skills", 0),
            "v9_tools": c.get("counts", {}).get("v9_tools", 0),
            "caps_sample": capability_ids()[:5]}


@router.get("/links")
async def octop_links(q: str = "", family: str = "", db=Depends(get_db)):
    """连接状态表：每项能力 → 绑了多少根触手（DISTINCT）+ 状态。"""
    from core.octop_bridge import catalog
    c = catalog()
    bound: dict = {}
    try:
        rows = await fetch_all(db, """SELECT capability, COUNT(DISTINCT tentacle) AS n
            FROM octop_binding WHERE direction='t2c' GROUP BY capability""", [], db=db)
        bound = {r["capability"]: int(r["n"]) for r in (rows or [])}
    except Exception:                                          # noqa: BLE001
        bound = {}
    items = []
    for d in c.get("divisions", []):
        for a in d["agents"]:
            items.append({"cap": f"agent:{d['id']}/{a}", "family": "Octop 智能体",
                          "group": d["name"], "name": a})
    items += [{"cap": f"expert:{x}", "family": "Octop 专家包", "group": "专家", "name": x}
              for x in c.get("experts", [])]
    items += [{"cap": f"plugin:{x}", "family": "Octop 内置插件", "group": "插件", "name": x}
              for x in c.get("plugins_bundled", [])]
    items += [{"cap": f"skill:{x}", "family": "Octop 技能模块", "group": "技能", "name": x}
              for x in c.get("skills", [])]
    items += [{"cap": f"v9tool:{x}", "family": "V9 工具", "group": "V9", "name": x}
              for x in c.get("v9_tools", [])]
    for it in items:
        it["bound_tentacles"] = bound.get(it["cap"], 0)
        it["state"] = ("全绑·100" if it["bound_tentacles"] >= 100 else
                       ("部分·%d" % it["bound_tentacles"] if it["bound_tentacles"] else "未绑"))
    if family:
        items = [x for x in items if x["family"] == family]
    if q:
        ql = q.lower()
        items = [x for x in items if ql in x["cap"].lower() or ql in x["name"].lower()]
    fam: dict = {}
    for x in items:
        fam.setdefault(x["family"], {"count": 0, "bound": 0})
        fam[x["family"]]["count"] += 1
        fam[x["family"]]["bound"] += 1 if x["bound_tentacles"] else 0
    return {"count": len(items), "items": items[:600], "by_family": fam}


class BindBody(BaseModel):
    tentacle: str
    capability: str


class BindAllBody(BaseModel):
    tentacles: int = 100
    only_family: str = ""


@router.post("/bind")
async def octop_bind(body: BindBody):
    b = _bridge()
    if b is None:
        raise HTTPException(503, "body_ledger_unavailable")
    r = await b.abind(body.tentacle, body.capability)
    if not r.get("ok"):
        raise HTTPException(400, r.get("reason") or "bind_failed")
    return r


@router.post("/bind_all")
async def octop_bind_all(body: BindAllBody):
    """一键 1:1：339 项能力 × N 根触手（默认 100 → 33900 对 / 67800 行，耗时较长）。"""
    b = _bridge()
    if b is None:
        raise HTTPException(503, "body_ledger_unavailable")
    return await b.abind_all(tentacles=body.tentacles)


@router.get("/fusion")
async def octop_fusion_status():
    """V9 ⇄ Octop 融合总状态（真探活 + 真绑定 + 品牌 + 可操控）。"""
    from core import octop_fusion as OF
    return OF.status()


@router.get("/fusion/pages")
async def octop_fusion_pages():
    """Octop 原生页面清单（一个不丢）+ 分组。"""
    from core import octop_fusion as OF
    return {"pages": list(OF.pages().values()), "groups": OF.group_names(),
            "count": len(OF.PAGES), "底座": OF.health()}


@router.get("/fusion/solid")
async def octop_fusion_solid():
    from core import solidify as S
    return {"latest": S.latest("octop_fusion"), "history": S.history("octop_fusion")}


@router.post("/fusion/solidify")
async def octop_fusion_solidify():
    from core import octop_fusion as OF
    return OF.register(note="面板点击固化融合登记表")


@router.post("/fusion/rollback")
async def octop_fusion_rollback():
    from core import solidify as S
    from core import deploy_ledger as J
    r = S.rollback("octop_fusion")
    J.record("solidify", "rollback:octop_fusion", detail=r, ok=bool(r.get("ok")),
             reason=r.get("reason", ""))
    return r


@router.post("/control")
async def octop_control_api(body: dict = Body(...)):
    """V9 操控整个 APP：启动/停止/重启 Octop 底座（参数列表 spawn，无 shell）。"""
    from core import octop_fusion as OF
    return OF.control(str((body or {}).get("action") or "status"))


@router.get("/page", response_class=HTMLResponse)
async def octop_page() -> str:
    from core.octop_bridge import families
    from core import octop_fusion as OF
    from core import octop_intake as OI
    from skills.ui_design import Page
    fam = families()
    fus = OF.status()
    it = OI.intake()
    caps = OI.caps()
    cnt = caps.get("counts") or {}
    状态色 = {"原生实现": "ok", "站内内嵌": "warn", "待接": "bad"}
    行 = "".join(
        f'<tr><td class=muted>{r["分组"]}</td><td><b>{r["标题"]}</b></td>'
        f'<td class="{状态色.get(r["状态"], "")}">{r["状态"]}</td>'
        f'<td><code>{r["V9落点"]}</code></td><td class=muted>{r["承载"]}</td></tr>'
        for r in it["行"])
    独有 = "".join(f'<li><b>{r["页面"]}</b>（<code>{r["路由"]}</code>）：{r["说明"]}</li>'
                   for r in it["V9独有"])
    接入块 = f"""
<div class=card data-hud>
  <h2>🧩 Octop 原生能力 · 逐个接入台账</h2>
  <p class=muted>{it['口径']}　共 <b>{it['原生页面数']}</b> 个原生页面：
     {it['状态分布']}</p>
  <p class=muted>能力目录真读数：部门 <b>{cnt.get('divisions')}</b> ·
     智能体 <b>{cnt.get('agents')}</b> · 专家 <b>{cnt.get('experts')}</b> ·
     插件 <b>{cnt.get('plugins')}</b> · 技能 <b>{cnt.get('skills')}</b> ·
     V9 工具 <b>{cnt.get('v9_tools')}</b> · 合计 <b>{cnt.get('total_capabilities')}</b> 项</p>
  <table><tr><th>分组</th><th>Octop 原生页</th><th>状态</th><th>V9 落点</th><th>怎么承担</th></tr>
  {行}</table>
  <h2>V9 自己长出、Octop 原本没有的面</h2><ul>{独有}</ul>
  <p class=muted>{it['运维面口径']}</p>
</div>
"""
    body = f"""
<div class=card>
  <b>V9 ⇄ Octop 深度融合</b>
  <span class=muted>（原生页面全连接 · 品牌统一 · V9 可操控整个 APP · 固化可回滚）</span>
  <p style="margin:8px 0">融合度 <b>{fus['融合度']}%</b> ·
     Octop <b>{'在线' if fus['octop在线'] else '离线'}</b>（{fus['底座']}，
     agents {fus['健康'].get('agents_running')}）·
     原生页面 <b>{fus['原生页面数']}</b> 条 · 已连接 <b>{fus['已连接页面数']}</b> ·
     绑定对称 <b>{'是' if fus['绑定对称'] else '否'}</b> ·
     品牌插件 <b>{'已注册 v' + str(fus['品牌'].get('版本')) if fus['品牌'].get('已注册') else '未注册'}</b> ·
     可操控 <b>{'是' if fus['可操控']['可操控'] else '否'}</p>
  <div class=row style="gap:6px">
    <button class=btn onclick="oc('status')">看状态</button>
    <button class="btn primary" onclick="oc('start')">启动 Octop</button>
    <button class=btn onclick="oc('restart')">重启 Octop</button>
    <button class=btn onclick="oc('stop')">停止 Octop</button>
    <button class=btn onclick="freeze()">固化融合</button>
    <button class=btn onclick="rollbackFusion()">回滚上一版</button>
    <span id=ocmsg class=muted></span>
  </div>
</div>
<h2>Octop 原生页面（一个不丢 · 品牌统一内嵌）</h2>
<div class=card>
  <p class=muted>外框是 V9 品牌（Logo + 标题），内容在**浏览器侧内嵌**本机 Octop 页面
     （不经 V9 服务器转发，所以既不丢页面、也没有转发面）。点分组切换。</p>
  <div class=row id=ogg style="gap:6px;margin-bottom:8px"></div>
  <div id=oframe style="border:1px solid var(--border);border-radius:var(--r-md);overflow:hidden">
    <div class=brandbar><img src=/logo.png width=22 height=22 alt="GBT小土豆V9">
      <b>GBT小土豆V9</b><span class=muted>· Octop 原生页面</span>
      <span id=otitle class=muted style="margin-left:auto"></span></div>
    <iframe id=oif title="Octop 原生页面" src="about:blank" style="width:100%;height:70vh;border:0;
      background:#0b1020"></iframe>
  </div>
  <div id=olist class=muted style="margin-top:8px">加载页面清单…</div>
</div>
<div class=row>
  <div class=card style="flex:2">
    <b>总能力可视化面板</b> <span class=muted>（全部读自 Octop 安装目录 + V9 播种插件，真数字）</span>
    <div id=capsbars style="margin-top:10px"></div>
    <div id=capsstat class=muted style="margin-top:8px">加载中…</div>
  </div>
  <div class=card style="flex:1">
    <b>连接状态</b>
    <div id=linkstat class=muted style="margin-top:6px">加载中…</div>
    <div style="margin-top:8px"><button class="btn primary" onclick="bindAll(100)">一键 1:1 全绑（339 项 × 100 触手）</button></div>
    <div style="margin-top:6px"><button class=btn onclick="bindAll(1)">先绑 t001（先看效果）</button>
      <span id=bindmsg class=muted></span></div>
  </div>
</div>
<div class=card style="margin-top:8px">
  <b>融合登记（固化可回滚）</b>
  <span id=fusstat class=muted>加载中…</span>
</div>
<h2>连接状态图表（按族）</h2>
<div class=card><table id=famtable><tr><th>能力族</th><th>能力数</th><th>已绑覆盖率</th><th>状态</th></tr>
  <tbody id=famrows></tbody></table></div>
<h2>能力清单（339 项 · 可搜可筛）</h2>
<div class=card>
  <div class=row style="gap:6px;margin-bottom:6px">
    <input id=oq placeholder="搜能力/名称" style="flex:1;background:var(--bg);border:1px solid var(--border);
      color:var(--text);border-radius:var(--r-sm);padding:5px 8px">
    <select id=of style="background:var(--bg);border:1px solid var(--border);color:var(--text);
      border-radius:var(--r-sm);padding:5px"><option value="">全部族</option></select>
  </div>
  <div style="max-height:340px;overflow:auto"><table><tr><th>能力</th><th>族</th><th>分组</th>
    <th>绑定触手</th><th>状态</th><th>操作</th></tr><tbody id=orows></tbody></table></div>
  <div class=muted style="font-size:11px;margin-top:4px" id=ostat></div>
</div>
"""
    js = """
async function loadCaps(){
  var d=await (await fetch('/api/octop/catalog')).json();
  var f=d.families||{}, mx=Math.max.apply(null,Object.values(f).concat([1])), out='';
  var rows=Object.keys(f);
  rows.forEach(function(k,i){
    var v=f[k], w=Math.round((v/mx)*240), y=8+i*30;
    out+='<div style="margin:2px 0"><span class=muted style="display:inline-block;width:150px;font-size:12px">'+k+
      '</span><span style="display:inline-block;height:14px;width:'+w+'px;background:#39d0ff;border-radius:3px"></span>'+
      ' <b>'+v+'</b></div>';
  });
  document.getElementById('capsbars').innerHTML=out;
  var st=await (await fetch('/api/octop/state')).json();
  document.getElementById('capsstat').innerHTML=
    '能力总数 <b>'+(st.capabilities||0)+'</b> · 触手 <b>'+(st.tentacles||0)+'</b> · 绑定 <b>'+(st.bindings||0)+
    '</b> 对（应绑 '+(st.expected_pairs||0)+'）· 覆盖触手 <b>'+(st.tentacles_bound||0)+
    '</b> · 对称 <b>'+(st.symmetric?'✅':'❌')+'</b> · 覆盖率 <b>'+((st.coverage==null)?'-':(st.coverage*100).toFixed(1)+'%')+'</b>';
  document.getElementById('linkstat').innerHTML=
    'Octop 分域 <b>'+(st.divisions||0)+'</b> · 智能体 <b>'+(st.agents||0)+'</b> · 专家 <b>'+(st.experts||0)+
    '</b> · 插件 <b>'+(st.plugins||0)+'</b> · 技能 <b>'+(st.skills||0)+'</b> · V9 工具 <b>'+(st.v9_tools||0)+'</b>';
}
async function loadLinks(){
  var q=document.getElementById('oq').value.trim(), f=document.getElementById('of').value;
  var d=await (await fetch('/api/octop/links?q='+encodeURIComponent(q)+'&family='+encodeURIComponent(f))).json();
  var sel=document.getElementById('of');
  if(sel.options.length<=1){ Object.keys(d.by_family||{}).forEach(function(k){
    var o=document.createElement('option'); o.value=k; o.textContent=k; sel.appendChild(o); }); }
  document.getElementById('famrows').innerHTML=Object.keys(d.by_family||{}).map(function(k){
    var v=d.by_family[k], pct=v.count?(v.bound/v.count*100):0;
    var col=pct>=99?'g':(pct>0?'y':'muted');
    return '<tr><td>'+k+'</td><td>'+v.count+'</td><td class="'+col+'">'+pct.toFixed(1)+'%</td><td class="'+col+'">'+
      (pct>=99?'已全覆盖':(pct>0?'部分':'未绑'))+'</td></tr>';}).join('');
  document.getElementById('orows').innerHTML=(d.items||[]).slice(0,300).map(function(x){
    var col=x.bound_tentacles>=100?'g':(x.bound_tentacles?'y':'muted');
    return '<tr><td title="'+x.cap+'">'+x.name+'</td><td>'+x.family+'</td><td class=muted>'+x.group+'</td>'+
      '<td class="'+col+'">'+x.bound_tentacles+'</td><td class="'+col+'">'+x.state+'</td>'+
      '<td><button class=btn onclick="bindOne(\\''+x.cap+'\\')">绑 t001</button></td></tr>';}).join('');
  document.getElementById('ostat').textContent='显示 '+d.count+' 项（表内最多 300 行）';
}
async function bindOne(cap){
  var r=await fetch('/api/octop/bind',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tentacle:'t001',capability:cap})});
  var d=await r.json();
  document.getElementById('bindmsg').innerHTML=d.ok?'<span class=g>已绑 '+cap.slice(0,28)+'</span>':'<span class=bad>'+(d.detail||'失败')+'</span>';
  loadLinks(); loadCaps();

// ═══════════ V9 ⇄ Octop 融合：页面索引 + 内嵌 + 操控 + 固化 ═══════════
let OFPAGES=[];
let OCTOP_BASE='http://127.0.0.1:8766';
function octoOpen(path, title){
  document.getElementById('otitle').textContent = title ? ('· '+title) : '';
  document.getElementById('oif').src = OCTOP_BASE + path;
}
function renderOctopPages(d){
  OFPAGES = d.pages||[];
  const groups = d.groups||[];
  // 用 data 属性传值：直接 onclick="pickGroup(''+g+'')" 在 JS 里等于两个相邻字符串字面量
  // → SyntaxError（整块脚本哑掉）。Python 里允许相邻字面量拼接，JS 不允许（真踩过）。
  document.getElementById('ogg').innerHTML = groups.map((g,i)=>
    '<button class=btn data-g="'+g+'" onclick="pickGroup(this.dataset.g)">'+g+'</button>').join(' ');
  renderList('全部');
}
function pickGroup(g){ renderList(g); }
function renderList(g){
  const items = OFPAGES.filter(p=> g==='全部' ? true : p['分组']===g);
  document.getElementById('olist').innerHTML = items.map(p=>
    '<button class=btn style="margin:2px" data-p="'+p['octop路径']+'" data-t="'+p['标题']+'" '+
    'onclick="octoOpen(this.dataset.p,this.dataset.t)">'+
    p['标题']+' <span class=muted>'+p['octop路径']+'</span></button>').join('');
  if(!items.length) document.getElementById('olist').textContent='（该分组暂无页面）';
}
async function oc(action){
  var m=document.getElementById('ocmsg'); m.textContent='执行中…';
  try{
    var r=await fetch('/api/octop/control',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:action})});
    var d=await r.json();
    m.textContent = action+': '+(d.ok?'成功':'失败')+' · 在线='+d['在线']+' '+(d.reason||'');
    if(d['在线']) octoOpen('/', '总览面板');
  }catch(e){ m.textContent='失败：'+e; }
}
async function freeze(){
  var r=await fetch('/api/octop/fusion/solidify',{method:'POST'}); var d=await r.json();
  document.getElementById('fusstat').textContent = d.ok ? ('已固化 '+d.rev+' · sha256 '+String(d.sha256).slice(0,12)+'…') : ('固化失败：'+d.reason);
}
async function rollbackFusion(){
  var r=await fetch('/api/octop/fusion/rollback',{method:'POST'}); var d=await r.json();
  document.getElementById('fusstat').textContent = d.ok ? ('已回滚到 '+d.rolled_back_to+'（哈希校验通过）') : ('回滚被拒：'+d.reason);
}
async function loadFusion(){
  try{
    var st0=await (await fetch('/api/octop/fusion')).json();
    if(st0['端口']) OCTOP_BASE='http://127.0.0.1:'+st0['端口'];
    var d=await (await fetch('/api/octop/fusion/pages')).json(); renderOctopPages(d);
    var st=await (await fetch('/api/octop/fusion')).json();
    if(st['octop在线']) octoOpen('/', '总览面板');
    var fs=await (await fetch('/api/octop/fusion/solid')).json().catch(()=>({}));
    document.getElementById('fusstat').textContent = fs.latest ? ('当前版本 '+JSON.stringify(fs.latest)) : '尚未固化';
  }catch(e){ document.getElementById('olist').textContent='融合信息取不到：'+e; }
}
loadFusion();

}
async function bindAll(n){
  var m=document.getElementById('bindmsg'); m.textContent='绑定中…（339×'+n+'，请稍候）';
  var r=await fetch('/api/octop/bind_all',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tentacles:n})});
  var d=await r.json();
  m.innerHTML='<span class=g>已绑 '+(d.bound_pairs||0)+' 对（'+((d.bound_pairs||0)*2)+' 行）</span>';
  loadLinks(); loadCaps();
}
document.getElementById('oq').addEventListener('input', loadLinks);
document.getElementById('of').addEventListener('change', loadLinks);
loadCaps(); loadLinks(); setInterval(loadCaps, 20000);
"""
    return Page(title="Octop 能力桥 · 原生能力逐个接入", body=接入块 + body,
                current="/octop", extra_js=js, dock=True).render()
