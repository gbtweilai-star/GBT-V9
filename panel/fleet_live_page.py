# panel/fleet_live_page.py —— 编队实时面板：一页看全每个 AI（谁·职业·在干嘛·最后动作）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from skills.ui_design import inject

router = APIRouter()


@router.get("/fleet-live", response_class=HTMLResponse)
async def fleet_live_page() -> HTMLResponse:
    html = """<!doctype html><meta charset=utf-8><title>编队实时面板</title>
<style>
 body{margin:0;background:#0b0e14;color:#e6edf3;font:13px/1.55 system-ui,"Microsoft YaHei"}
 .wrap{padding:16px 20px}
 h1{font-size:18px;margin:4px 0 6px} .muted{color:#7d8da4;font-size:12px}
 .bar{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0 14px}
 .pill{background:#0f131c;border:1px solid #1e2635;border-radius:10px;padding:6px 12px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(196px,1fr));gap:8px}
 .card{background:#0f131c;border:1px solid #1e2635;border-radius:10px;padding:9px 11px}
 .t{font-weight:600;color:#a5d6ff} .p{color:#ffd479;font-size:12px}
 .s{float:right;font-size:12px} .w{color:#57d38c}.i{color:#e9c46a}.r{color:#8fb2d9}.o{color:#8a94a6}.a{color:#c792ea}
 .k{color:#7d8da4;font-size:11px;margin-top:4px;word-break:break-all}
</style>
<div class=wrap>
 <h1>🛰 编队实时面板 · 一页看全</h1>
 <div class=muted>状态只有**落了账**才算「工作」；无记录=离线。职业来自每根触手的册子文件，不编。</div>
 <div class=bar id=bar>加载中…</div>
 <div class=grid id=g>加载中…</div>
</div>
<script>
const CLS={工作:'w',待命:'i',休息:'r',离线:'o',待授权:'a'};
function esc(s){return (s||'').toString().replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}
async function tick(){
 try{
  const d=await (await fetch('/api/fleet-live')).json();
  const st=d.统计||{};
  document.getElementById('bar').innerHTML =
    '<div class=pill>根数 <b>'+d.根数+'</b></div>'+
    Object.keys(st).map(k=>'<div class=pill>'+k+' <b class="'+(CLS[k]||'')+'">'+st[k]+'</b></div>').join('')+
    '<div class=pill>快照 <b>'+(d.ms||0)+'ms</b></div>'+
    '<div class=pill>更新 <b>'+(d.at||'').slice(11,19)+'</b></div>';
  document.getElementById('g').innerHTML=(d.触手||[]).map(r=>
    '<div class=card><span class=t>'+esc(r.触手)+'</span><span class="s '+(CLS[r.状态]||'')+'">'+esc(r.状态)+'</span>'+
    '<div class=p>'+esc(r.职业)+'</div>'+
    '<div class=k>绑定 '+r.绑定页+' 页 · 最后动作 '+esc(r.最后动作)+'</div>'+
    '<div class=k>'+esc(r.最后时间||'无记录')+' · '+esc(r.台账||'')+'</div></div>').join('');
 }catch(e){ document.getElementById('bar').textContent='读取失败: '+e; }
}
tick(); setInterval(tick, 5000);
</script>"""
    return inject(html, "/fleet-live")


@router.get("/api/fleet-live")
async def fleet_live_snapshot() -> dict:
    from core import fleet_live as FL
    return FL.snapshot()


@router.get("/api/fleet-live/status")
async def fleet_live_status() -> dict:
    from core import fleet_live as FL
    return FL.status()


@router.get("/api/fleet-live/timeline/{tentacle}")
async def fleet_live_timeline(tentacle: str, limit: int = 20) -> dict:
    from core import fleet_live as FL
    return FL.timeline(tentacle, limit=limit)
