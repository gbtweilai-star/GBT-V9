# panel/dh_input_page.py —— 数字人·输入框位（她贴在输入框旁：欢迎语 + 说话 + 六步带路）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from skills.ui_design import inject

router = APIRouter()


@router.get("/dh-input", response_class=HTMLResponse)
async def dh_input_page() -> HTMLResponse:
    html = """<!doctype html><meta charset=utf-8><title>数字人·输入框位</title>
<style>
 body{margin:0;background:#070a10;color:#e6edf3;font:14px/1.6 system-ui,"Microsoft YaHei";
      display:flex;height:100vh}
 .side{width:210px;padding:14px 12px;border-right:1px solid #16202e;overflow:auto}
 .t{font-weight:600;color:#a5d6ff;font-size:13px;margin:10px 0 4px}
 .k{color:#7d8da4;font-size:12px}
 .card{background:#0d1219;border:1px solid #1b2634;border-radius:10px;padding:8px 10px;margin:6px 0}
 .ok{color:#57d38c}.w{color:#e9c46a}.b{color:#ef6b6b}
 .main{flex:1;display:flex;flex-direction:column;justify-content:flex-end;padding:16px 18px}
 .row{display:flex;gap:12px;align-items:flex-start;margin-bottom:10px}
 .ava{width:46px;height:46px;border-radius:50%;flex:0 0 46px;
      background:radial-gradient(circle at 35% 30%,#7cc4ff,#1b3a63);box-shadow:0 0 18px #2a6cb055}
 .bubble{background:#101823;border:1px solid #1e2a3a;border-radius:14px;padding:10px 14px;white-space:pre-wrap}
 .inbox{display:flex;gap:10px;align-items:center;background:#0e141d;border:1px solid #1e2a3a;
        border-radius:16px;padding:10px 12px}
 input{flex:1;background:transparent;border:0;outline:0;color:#e6edf3;font:inherit}
 button{background:#1f6feb;border:0;color:#fff;border-radius:10px;padding:8px 16px;cursor:pointer;font:inherit}
</style>
<div class=side>
 <div class=t>状态</div>
 <div id=st class=k>加载中…</div>
 <div class=t>她记着</div>
 <div id=mem class=k>—</div>
 <div class=t>六步带路</div>
 <div id=steps class=k>—</div>
</div>
<div class=main>
 <div class=row><div class=ava></div><div class=bubble id=hello>…</div></div>
 <div class=inbox>
   <div class=ava style="width:28px;height:28px;flex:0 0 28px"></div>
   <input id=q placeholder="把架构/项目递给她 —— 剩下的她做（想停就说「停」）">
   <button onclick="send()">递给她</button>
 </div>
 <div class=k id=ans style="margin-top:8px"></div>
</div>
<script>
async function j(u,o){const r=await fetch(u,o);return await r.json()}
async function boot(){
 const d=await j('/api/dh/boot'); document.getElementById('hello').textContent=d.开场白||'…';
 const m=d.记忆||{}; document.getElementById('mem').textContent=
   '记忆 '+(m.记忆条数||0)+' 条 · '+Object.entries(m.分类||{}).map(([k,v])=>k+v).join(' ');
 const nx=(d.带路||{}).下一步; document.getElementById('steps').textContent= nx? ('第'+nx.步+'步 '+nx.标题) : '已走完';
 const s=await j('/api/fleet-live/status');
 document.getElementById('st').innerHTML=Object.entries(s.统计||{}).map(([k,v])=>'<span class=ok>'+k+' '+v+'</span>').join(' · ');
}
async function send(){
 const q=document.getElementById('q').value.trim(); if(!q) return;
 document.getElementById('ans').textContent='…';
 const r=await j('/api/dh/memory/search?q='+encodeURIComponent(q));
 document.getElementById('ans').innerHTML = r.命中? ('她说：记忆里有 <b>'+r.命中+'</b> 条依据 —— '+ (r.条目[0].标题||''))
   : ('她说：记忆里<b>没存到</b>这一条（我不编）。要我现学的话，把资料递给我。');
}
boot();
</script>"""
    return inject(html, "/dh-input")


@router.get("/api/dh/hello")
async def dh_hello() -> dict:
    """给 APP/输入框拿欢迎语用（一句话口）。"""
    from core import dh_boot as DB
    return {"欢迎语": DB.WELCOME, "开场白": DB.greeting()}
