# panel/blueprint_page.py —— 项目 3D 蓝图（上帝视角 · 无死角）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："每个项目要有一个 3D 蓝图显示页面，在每次设计架构和排布的时候都需要用到，
#   直接看项目整体和上帝视角看项目无死角。"
#
# 实现：**纯 CSS 3D + 内联 SVG**（零外部资源，符合"全站不外链"）——
#   四层空间叠起来（指挥层 / 能力层 / 编队层 / 地基），可旋转、俯仰、缩放、逐层开关；
#   每一层的数字都来自 core.blueprint 的真读数，图上不对就是数据不对。
import asyncio as _a

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse

from core import blueprint as BP
from skills.ui_design import Page

router = APIRouter()


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if v is not None else "")


def _layer_plane(i: int, L: dict) -> str:
    """一层 = 一个平面：节点按网格排开，层与层之间用 Z 轴拉开距离。"""
    nodes = L.get("节点") or []
    if not nodes:
        # 没有逐节点数据的层（能力/编队/地基）→ 用统计做"格子"
        st = L.get("统计") or {}
        cells = []
        for k, v in st.items():
            cells.append(f'<div class="bpcell"><div class=bpmuted>{_esc(k)}</div>'
                         f'<div class=bpnum>{_esc(v)}</div></div>')
        nodes_html = "".join(cells)
    else:
        nodes_html = "".join(
            f'<div class="bpnode grp{abs(hash(n.get("组") or "")) % 5}" title="{_esc(n.get("副"))}">'
            f'{_esc(n.get("标题"))}</div>' for n in nodes)
    extra = ""
    if L["名称"] == "指挥层":
        lp = L.get("闭环") or {}
        extra = (f'<div class=bpsub>闭环 {_esc(lp.get("节点"))} 节点 / {_esc(lp.get("边"))} 边 · '
                 f'就绪度 {_esc(lp.get("就绪度"))} · 工作流 {len(L.get("工作流") or [])} 条</div>')
    return (f'<div class="plane p{i}" style="--zi:{i}">'
            f'<div class=bphead><b>{_esc(L["名称"])}</b>'
            f'<span class=bpmuted>　{_esc(L["说明"])}</span></div>'
            f'<div class=bpgrid>{nodes_html}</div>'
            f'<div class=bpsrc>来源 {_esc(L["来源"])}</div>{extra}</div>')


def _gates(gates: list) -> str:
    return "".join(
        f'<div class="bpcell gate"><b>{_esc(g["关卡"])}</b>'
        + "".join(f'<div class=bpmuted>{_esc(k)} <b>{_esc(v)}</b></div>'
                  for k, v in g.items() if k not in ("关卡", "口径"))
        + f'<div class=bpsrc>{_esc(g.get("口径") or "")}</div></div>' for g in gates)


def _checklist(items: list) -> str:
    return "".join(
        f'<div class=bpcheck>{"✅" if x["有条目"] else "⚠"} {_esc(x["面"])}</div>'
        for x in items)


JS = """
let rotX=-22, rotZ=-28, zoom=1, hide={};
function apply(){
  const w=document.getElementById('bpworld');
  w.style.transform='rotateX('+rotX+'deg) rotateZ('+rotZ+'deg) scale('+zoom+')';
  Object.keys(hide).forEach(k=>{ const el=document.querySelector('.plane.p'+k);
    if(el) el.style.display = hide[k]? 'none':''; });
  document.getElementById('bprotx').textContent=rotX+'°';
  document.getElementById('bprotz').textContent=rotZ+'°';
  document.getElementById('bpzoom').textContent=Math.round(zoom*100)+'%';
}
function setAxis(which,v){ if(which==='x') rotX=+v; else rotZ=+v; apply(); }
function bump(which,d){ if(which==='x') rotX=Math.max(-80,Math.min(20,rotX+d));
  else rotZ+=d; apply(); }
function view(kind){
  if(kind==='god'){ rotX=-78; rotZ=0; zoom=0.9; }
  else if(kind==='side'){ rotX=-6; rotZ=-40; zoom=1; }
  else if(kind==='iso'){ rotX=-22; rotZ=-28; zoom=1; }
  hide={}; apply();
}
function toggleLayer(i,el){ hide[i]=!hide[i]; el.classList.toggle('off',!!hide[i]); apply(); }
function z(d){ zoom=Math.max(0.5,Math.min(1.8,zoom+d)); apply(); }
async function loadBP(){
  try{
    const d=await (await fetch('/api/blueprint')).json();
    if(!d['层'] || !d['层'].length) return;
    const w=document.getElementById('bpworld');
    w.innerHTML=(d['层']||[]).map(function(L,i){
      var cells=(L['节点']&&L['节点'].length)? L['节点'].map(function(n){
          return '<div class=bpnode>'+esc(n['标题'])+'</div>'; }).join('')
        : Object.keys(L['统计']||{}).map(function(k){
          return '<div class=bpcell><div class=bpmuted>'+esc(k)+'</div><div class=bpnum>'+esc(L['统计'][k])+'</div></div>'; }).join('');
      return '<div class="plane p'+i+'" style="--zi:'+i+'"><div class=bphead><b>'+esc(L['名称'])+'</b>'
        +'<span class=bpmuted>　'+esc(L['说明'])+'</span></div><div class=bpgrid>'+cells+'</div>'
        +'<div class=bpsrc>来源 '+esc(L['来源'])+'</div></div>'; }).join('');
    apply();
  }catch(e){}
}
function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
async function prov(kind){
  const m=document.getElementById('pmsg'), o=document.getElementById('pout');
  m.textContent='跑着呢…（配齐 400 位约几十秒）';
  try{
    const d=await fetch('/api/expand/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({n:100})}).then(r=>r.json());
    o.textContent=JSON.stringify(d,null,1).slice(0,3000);
    if(kind==='all') m.innerHTML='<span class=ok>已配齐：'+(d['配备']||(d['执行明细']||[]).length)
      +' 位 · 钩子过 '+(d['钩子']&&d['钩子']['通过'])+'</span>';
    else m.textContent='已出结果（见下）';
  }catch(e){ m.innerHTML='<span class=bad>失败：'+e+'</span>'; }
}
document.addEventListener('DOMContentLoaded',function(){ apply(); loadBP(); });
"""


@router.get("/blueprint", response_class=HTMLResponse)
async def blueprint_page():
    # 首屏不等重活：先给场景骨架，数据由 JS 取 /api/blueprint（后端带 60 秒缓存 + 启动预热）。
    # 真踩过：蓝图组装要读编队/能力/闭环/固化/台账/记忆，冷启 30 秒以上，页面会像死了一样。
    b = {"品牌": "GBT小土豆V9", "层": [], "关卡": [],
         "上帝视角": {"口径": "正在量蓝图…", "无死角检查": []}}
    planes = '<div id=bpload class=muted style="padding:24px">正在量蓝图（编队/能力/闭环/固化/台账/记忆）…'              '首次约 10~30 秒，之后就秒开。</div>'
    if BP.status.__module__:                     # 保持导入使用，避免静态检查误判未用
        pass
    layer_toggles = "".join(
        f'<button class=btn onclick="toggleLayer({i},this)">{_esc(L["名称"])}</button>'
        for i, L in enumerate(b["层"]))
    body = (
        f'<div class=card data-hud><h2>🧊 {_esc(b["品牌"])} · 3D 蓝图（上帝视角）</h2>'
        '<p class=muted>' + _esc(b["上帝视角"]["口径"]) + '</p>'
        '<div class=btnbar>'
        '<button class="btn primary" onclick="view(\'god\')">上帝视角（俯视）</button>'
        '<button class=btn onclick="view(\'iso\')">等轴视角</button>'
        '<button class=btn onclick="view(\'side\')">侧视</button>'
        '<span class=sep></span>'
        f'{layer_toggles}'
        '<span class=sep></span>'
        '<button class=btn onclick="bump(\'x\',-6)">俯仰 -</button>'
        '<button class=btn onclick="bump(\'x\',6)">俯仰 +</button>'
        '<button class=btn onclick="bump(\'z\',-10)">旋转 -</button>'
        '<button class=btn onclick="bump(\'z\',10)">旋转 +</button>'
        '<button class=btn onclick="z(0.1)">放大</button>'
        '<button class=btn onclick="z(-0.1)">缩小</button>'
        '<span class=muted>俯仰 <b id=bprotx>—</b> · 旋转 <b id=bprotz>—</b> · '
        '缩放 <b id=bpzoom>—</b></span></div>'
        '<div class=scene><div class=world id=bpworld>' + planes + '</div></div></div>'
        '<div class=card><h2>🚧 关卡层（每个都是真状态）</h2>'
        f'<div class=bpgrid>{_gates(b["关卡"])}</div></div>'
        '<div class=card data-hud><h2>🆔 触手身份位（一根触手一个）</h2>'
        '<p class=muted>四个服务面各一个身份位：邮箱 / 云插件 / 数据库 / 开源仓库。'
        '本地自签保底（下载即可用）；外部通道（凭据注入 / 官方 API / 你的开户脚本槽）有则用。'
        '脚本槽约定：<code>python tools/provision_hook.py &lt;触手号&gt; &lt;服务&gt;</code>，'
        'stdout 最后一行回句柄。</p>'
        '<div class=btnbar>'
        '<button class="btn primary" data-k="check" onclick="prov(this.dataset.k)">看缺口与计划</button>'
        '<button class=btn data-k="all" onclick="prov(this.dataset.k)">一条命令配齐全部身份位</button>'
        '<button class=btn data-k="first_boot" onclick="prov(this.dataset.k)">首启配备（幂等）</button>'
        '<span id=pmsg class=muted></span></div>'
        '<pre id=pout class=muted style="max-height:220px"></pre></div>'
        '<div class=card><h2>🔍 无死角检查</h2>'
        f'<div class=row>{_checklist(b["上帝视角"]["无死角检查"])}</div>'
        '<p class=muted>每个面都有条目才算"看得到全貌"；缺哪个面会在上面标 ⚠，'
        '补齐后蓝图才算完整。</p></div>')
    css = """
.scene{perspective:1400px;perspective-origin:50% 30%;height:460px;overflow:hidden;
  border-radius:var(--r-lg);border:1px solid var(--border);margin-top:10px;
  background:radial-gradient(120% 90% at 50% 0%,rgba(57,208,255,.07),transparent 65%),
  rgba(3,6,12,.75)}
.world{position:relative;width:100%;height:100%;transform-style:preserve-3d;
  transition:transform .25s ease;transform-origin:50% 50%}
.plane{position:absolute;left:6%;right:6%;top:calc(8% + var(--zi) * 74px);
  transform:translateZ(calc(var(--zi) * -70px));transform-style:preserve-3d;
  background:linear-gradient(180deg,rgba(21,32,52,.92),rgba(9,14,24,.92));
  border:1px solid var(--neon-edge);border-radius:var(--r-md);padding:8px 10px;
  box-shadow:var(--sh2-d2)}
.bphead{font-size:13px}
.bpmuted{color:var(--muted);font-size:11px}
.bpsrc{color:var(--muted);font-size:10px;margin-top:4px;opacity:.85}
.bpsub{color:var(--accent);font-size:11px;margin-top:2px}
.bpgrid{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.bpnode{padding:2px 7px;border-radius:var(--r-pill);border:1px solid var(--border);
  font-size:11px;background:rgba(255,255,255,.03)}
.bpnode.grp0{border-color:rgba(57,208,255,.5)} .bpnode.grp1{border-color:rgba(124,92,255,.5)}
.bpnode.grp2{border-color:rgba(63,214,138,.5)} .bpnode.grp3{border-color:rgba(240,180,41,.5)}
.bpnode.grp4{border-color:rgba(255,77,157,.5)}
.bpcell{min-width:92px;padding:6px 8px;border-radius:var(--r-sm);border:1px solid var(--border);
  background:rgba(255,255,255,.02)}
.bpnum{font-size:17px;font-weight:700;color:var(--accent)}
.bpcell.gate{border-color:var(--neon-edge_2, rgba(124,92,255,.45))}
.bpcheck{padding:4px 10px;border-radius:var(--r-pill);border:1px solid var(--border);font-size:12px}
button.off{opacity:.45}
"""
    return Page(title=f"{b['品牌']} · 3D 蓝图", body=body, current="/blueprint",
                extra_css=css, extra_js=JS).render()


# ═══════════════ API ═══════════════
@router.get("/api/blueprint")
async def api_blueprint():
    return await _a.to_thread(BP.build)


@router.post("/api/expand/check")
async def api_expand_check(payload: dict = Body(default_factory=dict)):
    """看缺口与计划（不动任何东西）。"""
    from core import expand as E
    n = int((payload or {}).get("n") or 100)
    return await _a.to_thread(lambda: {"扫描": E.scan(n=n), "计划": E.plan(n=n, limit=8)})


@router.post("/api/expand/all")
async def api_expand_all(payload: dict = Body(default_factory=dict)):
    """**一条命令配齐全部身份位**（走适配器链 + 钩子 + 复扫验证）。"""
    from core import expand as E
    n = int((payload or {}).get("n") or 100)
    return await _a.to_thread(E.run, n=n, dry_run=False,
                              limit=max(8, n * 4), allow_hook=True)


@router.post("/api/expand/first_boot")
async def api_expand_first(payload: dict = Body(default_factory=dict)):
    """新用户第一次启动跑的那条：占位（每根一次）+ 接已备凭据 + 留待办（幂等）。"""
    from core import expand as E
    n = int((payload or {}).get("n") or 100)
    return await _a.to_thread(E.first_boot, n=n)


@router.get("/api/identity/summary")
async def api_identity_summary(n: int = 100):
    from core import tentacle_identity as TI
    return await _a.to_thread(TI.summary, n=n)


@router.get("/api/identity/rows")
async def api_identity_rows(tentacle: str = "", service: str = "", state: str = ""):
    from core import tentacle_identity as TI
    return {"行": await _a.to_thread(TI.rows, tentacle=tentacle, service=service, state=state),
            "口径": "主脑全量视图（不传 tentacle 即全部）"}


@router.get("/api/blueprint/status")
async def api_status():
    return await _a.to_thread(BP.status)


@router.post("/api/blueprint/register")
async def api_register(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(BP.register, note=str((payload or {}).get("note") or ""))


__all__ = ["router"]
