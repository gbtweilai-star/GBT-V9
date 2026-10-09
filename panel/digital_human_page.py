# panel/digital_human_page.py —— 数字人对讲页（实时见证行 + 只读工具问询）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
DIGITAL_HUMAN_PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>GBT小土豆V9 · 数字人</title>
<style>
 body{margin:0;background:#0b0f14;color:#e6edf3;font:14px/1.6 -apple-system,"Segoe UI",sans-serif}
 .wrap{max-width:960px;margin:0 auto;padding:18px}
 .row{display:flex;gap:12px;flex-wrap:wrap}
 .card{flex:1;min-width:260px;background:#131a22;border:1px solid #1f2b36;border-radius:10px;padding:12px}
 .num{font-size:30px;font-weight:700}
 .ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}.muted{color:#8b949e}
 pre{white-space:pre-wrap;word-break:break-all;background:#0d131a;border-radius:8px;padding:8px;font-size:12px}
 /* 按键统一在 skills/ui_design（旧的本地 button / button.ghost 已删） */
 .talk{background:#0d131a;border-left:3px solid #1f6feb;padding:8px;border-radius:6px;min-height:42px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 td,th{border-bottom:1px solid #1f2b36;padding:5px 6px;text-align:left}
</style></head><body>
<!-- 统一导航：三个页面都要在（V9 自己的页 + Octop 原生台），一个都不丢 -->
<div style="max-width:960px;margin:8px auto 0;padding:8px 12px;border:1px solid #1f2b36;
     border-radius:8px;background:#0f1620;font-size:13px">
  <b>导航</b>
  <a href="/" style="color:#3fb950;text-decoration:none;margin-left:8px">▣ 总控台</a> ·
  <a href="/capability" style="color:#58a6ff;text-decoration:none">▦ 总能力/连接</a> ·
  <a href="/octop" style="color:#58a6ff;text-decoration:none">◈ Octop 能力桥</a> ·
  <a href="/media" style="color:#58a6ff;text-decoration:none">▤ 媒体监控</a> ·
  <a href="/digital-human" style="color:#3fb950;text-decoration:none">☺ 数字人</a> ·
  <a href="/docs" style="color:#8b949e;text-decoration:none">⌘ API 文档</a>
</div>
<div class="wrap">
<div class="row" style="align-items:flex-start">
<div class="card" style="flex:1 1 100%">
  <b>数字人形象 · 云端图生3D + 本地影棚与风格化渲染</b>
  <div class="muted" style="font-size:12px;margin-top:2px">
    几何与贴图由云端「图生3D」生成（Tripo OpenAPI），渲染在本机 Blender 完成：
    皮肤次表面散射 / 布料绒面 / 眼部高光 + 影棚三点光 + Freestyle 描边 + AgX 调色。
    拖图或拉滑杆可转视角（2D 卡通版已按主人要求撤下）。
  </div>
  <div class="row" style="gap:12px;margin-top:6px;align-items:flex-start">
    <div style="text-align:center">
      <img id="tvturn" width="220" alt="3D 转台" style="border-radius:10px;background:#2f3038">
      <div class="muted" style="font-size:11px">转台 · 拖动我换角度</div>
      <input id="tvr" type="range" min="0" max="7" value="0" style="width:210px">
    </div>
    <div style="text-align:center">
      <img id="tvhero" width="220" alt="3D 主图" style="border-radius:10px;background:#2f3038">
      <div class="muted" style="font-size:11px">主图（正面 3/4）</div>
    </div>
    <div style="text-align:center">
      <img id="tvface" width="160" alt="脸部特写" style="border-radius:10px;background:#2f3038">
      <div class="muted" style="font-size:11px">脸部特写</div>
    </div>
  </div>
  <div class="row" style="gap:6px;margin-top:8px;flex-wrap:wrap">
    <select id="tvmodel" style="width:auto;max-width:280px"></select>
    <select id="tvl" style="width:auto"></select>
    <button onclick="tvRender()">用这个模型重渲染</button>
    <span id="tvmsg" class="muted">（渲染跑一次 Blender，约 1~2 分钟）</span>
  </div>
</div>
</div>

<h2>数字人 · 身体自己说 <span class="muted" id="conn">连接中…</span></h2>
 <div class="row">
  <div class="card"><div class="muted">有效见证</div>
    <div class="num" id="valid">—</div>
    <div class="muted" id="stale"></div></div>
  <div class="card"><div class="muted">最近一次开口</div>
    <div class="talk" id="talk">（还没说话）</div>
    <button class="ghost" onclick="sayWitness()">主动说见证数</button>
    <label class="muted"><input type="checkbox" id="mute" style="width:auto"> 静音</label></div>
 </div>

 <div class="card" style="margin-top:12px">
  <b>见证证据等级</b>
  <table id="wit"><tr><th>见证</th><th>身份状态</th><th>证据等级</th><th>计票</th><th>原因</th></tr></table>
 </div>

 <div class="card" style="margin-top:12px">
  <b>问只读工具</b>（数字来自快照；stale 就说"无法确认"）
  <div style="margin:8px 0">
   <button onclick="ask('media.capture')">吞噬能</button>
   <button onclick="ask('scan.coverage')">扫描覆盖</button>
   <button onclick="ask('media.queue')">队列状态</button>
   <button onclick="ask('get_witness_snapshot')">见证</button>
  </div>
  <input id="tid" placeholder="触手 id（scan.coverage 可选）"/>
  <pre id="out">（未问询）</pre>
 </div>

 <div class="card" style="margin-top:12px">
  <b>元认知（她怎么看自己）</b>
  <div id="meta" class="muted">加载中…</div>
 </div>
 <div class="card" style="margin-top:12px"><b>语音流水</b><pre id="log"></pre></div>
</div>
<script>
const $=(i)=>document.getElementById(i);
function log(s){$('log').textContent=(s+"\n"+$('log').textContent).slice(0,4000);}
function speak(t){ if($('mute').checked) return;
  try{ const u=new SpeechSynthesisUtterance(t); u.lang='zh-CN'; speechSynthesis.speak(u);}catch(e){} }
function say(t){ $('talk').textContent=t; log("🗣 "+t); speak(t); }

function renderSnap(d){
  $('valid').textContent=(d.valid_count==null?'—':d.valid_count+" / "+(d.required??'—'));
  const c=(d.status==='critical')?'bad':(d.status==='degraded')?'warn':'ok';
  $('valid').className='num '+c;
  $('stale').textContent = d.stale ? '⚠ 快照已过期：'+d.safe_sentence : '观测于 '+(d.observed_at||'—');
  if(d.witness_id!==undefined || d.valid_count!==undefined){
    const t=$('wit'); t.innerHTML='<tr><th>见证</th><th>身份状态</th><th>证据等级</th><th>计票</th><th>原因</th></tr>';
    let n=0;
    for(const [k,v] of Object.entries(d.states||{})){ n++;
      t.innerHTML+=`<tr><td>${k}</td><td>${v.identity_status||'—'}</td><td>${v.evidence_level||'—'}</td>
        <td class="${v.vote_eligible?'ok':'bad'}">${v.vote_eligible?'算':'不算'}</td>
        <td class="muted">${v.reason_code||''}</td></tr>`; }
    if(!n) t.innerHTML+='<tr><td colspan=5 class="muted">还没有登记任何见证</td></tr>';
  }
}
async function load(){
  try{ const d=await (await fetch('/api/digital-human/witness')).json(); renderSnap(d); }
  catch(e){ $('valid').textContent='—'; }
}
async function loadMeta(){
  const el=$('meta'); if(!el) return;
  const pct=v=>(v==null?'—':Math.round(v*100)+'%');
  try{
    const m=await (await fetch('/api/brain/metacog')).json();
    const c=m['校准']||{};
    el.innerHTML='<div class=muted>'+esc(m['自我陈述']||'（还没有可陈述的）')+'</div>'+
      '<div class=kv><span>记忆</span><b>'+(c['记忆数']??'—')+'</b></div>'+
      '<div class=kv><span>用到率</span><span>'+pct(c['用到率'])+'</span></div>'+
      '<div class=kv><span>未答率</span><span>'+pct(c['未答率'])+'</span></div>'+
      '<div class=muted style="margin-top:6px">该做的：'+esc(((m['该做的']||[])[0])||'—')+'</div>';
  }catch(e){ el.textContent='元认知取不到：'+e.message; }
}
async function ask(tool){
  const params={}; const tid=$('tid').value.trim();
  if(tool==='scan.coverage'&&tid) params.tentacle_id=tid;
  const r=await fetch('/api/digital-human/ask/'+tool,{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({session_id:'witness',params})});
  const d=await r.json();
  $('out').textContent=JSON.stringify({sentence:d.safe_sentence,stale:d.stale,
    unknown_reason:d.unknown_reason,observed_at:d.observed_at,facts:d.facts},null,1);
  say(d.safe_sentence);
}
async function sayWitness(){
  const r=await fetch('/api/digital-human/say-witness',{method:'POST'});
  const d=await r.json(); renderSnap(d); say(d.safe_sentence);
}
function connect(){
  const es=new EventSource('/api/digital-human/stream');
  es.onopen=()=>{$('conn').textContent='已连接';$('conn').className='ok';};
  es.onerror=()=>{$('conn').textContent='断线重连中…';$('conn').className='warn';};
  es.addEventListener('witness',(e)=>{
    const m=JSON.parse(e.data);
    if(m.type==='snapshot') renderSnap({valid_count:m.valid_count,required:m.required,
        status:m.status,stale:false,observed_at:m.observed_at,states:m.states||{}});
    else if(m.type==='speaking'||m.type==='spoken') say(m.text);
    else if(m.type==='alert') speak(m.text||'');
    else if(m.type==='backpressure') log('⚠ 队列满，文本留在页面：'+m.text);
  });
}
load(); connect(); loadMeta(); setInterval(load, 30000); setInterval(loadMeta, 60000);
</script>
<script>
let RIG=null, CURCLIP='idle', CURCOMBO='', T0=performance.now(), SPEAKING=false, STATUS=null;
function draw(){ if(!RIG) return; }
async function buildButtons(){
  if(!STATUS){ try{ STATUS=await (await fetch('/api/avatar/status')).json(); }catch(e){ return; } }
  const meta=STATUS['动作细节']||{}, cat=STATUS['动作分类']||{}, combo=STATUS['组合动作']||{};
  if(!document.getElementById('combobtns').dataset.done){
    document.getElementById('combobtns').innerHTML=Object.keys(combo).map(function(n){
      return '<button class=ghost data-k="'+n+'" onclick="pickCombo(this.dataset.k)">'+n+'（'+combo[n].length+'步）</button>';}).join('');
    document.getElementById('combobtns').dataset.done='1';
  }
  if(!document.getElementById('clipbtns').dataset.done){
    const groups={};
    Object.keys(meta).forEach(function(k){ const g=cat[k]||'其他'; (groups[g]=groups[g]||[]).push(k); });
    document.getElementById('clipbtns').innerHTML=Object.keys(groups).map(function(g){
      return '<div class="muted" style="margin-top:4px;font-size:12px">'+g+'</div>'+
        groups[g].map(function(k){ return '<button class=ghost style="padding:4px 8px;font-size:12px" '+
          'data-k="'+k+'" onclick="pick(this.dataset.k)">'+(meta[k]||k)+'</button>';}).join('');
    }).join('');
    document.getElementById('clipbtns').dataset.done='1';
  }
}
async function loadRig(){
  try{
    const ph=(((performance.now()-T0)/1200)%1).toFixed(3);
    const q='/api/avatar/state?clip='+CURCLIP+'&combo='+encodeURIComponent(CURCOMBO)+
            '&t='+ph+'&speaking='+(SPEAKING?1:0)+'&amp=0.7';
    const r=await fetch(q);
    const d=await r.json();
    document.getElementById('rig').innerHTML=d.svg;
    document.getElementById('riginfo').textContent='关节 '+d['关节数']+' · 当前动作 '+
      (d.combo?('组合 '+d.combo+'（第 '+d.step+' 步）→ '+d.clip):d.clip)+
      ' · 眨眼 '+d.blink+(d.speaking?(' · 说话口型 '+d.mouth):'');
    await buildButtons();
  }catch(e){ document.getElementById('rig').textContent='骨架取不到：'+e; }
}
function pick(c){ CURCLIP=c; CURCOMBO=''; T0=performance.now(); }
function pickCombo(n){ CURCOMBO=n; CURCLIP=''; T0=performance.now(); }
function setMouth(){ SPEAKING=!SPEAKING; }
async function sayIt(){
  const m=document.getElementById('rigmsg'); m.textContent='合成中…（CPU 约 1~2 分钟）';
  SPEAKING=true;
  try{
    const r=await fetch('/api/voice/say',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:'主人好，我是 GBT小土豆V9 的数字人，帮你看一下網路的專案好不好？'})});
    const d=await r.json();
    m.textContent = d.ok ? ('已发声（'+d['通道']+'）') : ('未成：'+(d.reason||''));
  }catch(e){ m.textContent='失败：'+e; }
  SPEAKING=false;
}
loadRig(); setInterval(loadRig, 120); setInterval(function(){ RIG=1; }, 1000);

// ── 3D 形象：云上模型（Tripo）渲染图 —— 转台/主图/特写 ──
let TV_LABEL='stylized4', TV_ANG=0, TV_N=8;
function tvTurn(){
  const e=document.getElementById('tvturn'); if(!e) return;
  e.onerror=function(){ e.alt='（还没有 '+TV_LABEL+' 的转台图）'; };
  e.src='/api/tripo/frame?name='+TV_LABEL+'_turn'+String(TV_ANG%TV_N).padStart(2,'0')+'.png&t='+Date.now();
  const r=document.getElementById('tvr'); if(r) r.value=TV_ANG%TV_N;
}
function tvStatic(){
  const h=document.getElementById('tvhero'), f=document.getElementById('tvface');
  if(h) h.src='/api/tripo/frame?name='+TV_LABEL+'_hero.png&t='+Date.now();
  if(f) f.src='/api/tripo/frame?name='+TV_LABEL+'_face.png&t='+Date.now();
}
async function tvLabels(){
  try{
    const d=await (await fetch('/api/tripo/labels')).json();
    const sel=document.getElementById('tvl'); if(!sel) return;
    sel.innerHTML=(d['标签']||[]).map(function(x){ return '<option value="'+x+'">'+x+'</option>'; }).join('');
    if((d['标签']||[]).indexOf(TV_LABEL)<0 && (d['标签']||[]).length) TV_LABEL=d['标签'][0];
    sel.value=TV_LABEL;
    sel.onchange=function(){ TV_LABEL=sel.value; tvTurn(); tvStatic(); };
  }catch(e){}
}
async function tvModels(){
  try{
    const d=await (await fetch('/api/tripo/models')).json();
    const sel=document.getElementById('tvmodel'); if(!sel) return;
    sel.innerHTML=(d['模型']||[]).map(function(m){
      return '<option value="'+m['路径']+'">'+m['路径'].split('/').slice(-3,-2)+' · '+m['大小MB']+'MB</option>'; }).join('');
  }catch(e){}
}
async function tvRender(){
  const msg=document.getElementById('tvmsg'); const sel=document.getElementById('tvmodel');
  if(!sel || !sel.value){ if(msg) msg.textContent='没有可用的云模型'; return; }
  if(msg) msg.textContent='渲染中…（Blender 后台跑）';
  try{
    const lab='m'+Date.now().toString().slice(-6);
    const r=await fetch('/api/tripo/render?glb='+encodeURIComponent(sel.value)+'&label='+lab+'&angles=8',
                        {method:'POST'});
    const d=await r.json();
    if(d.ok){ TV_LABEL=lab; TV_N=8; TV_ANG=0; tvTurn(); tvStatic(); await tvLabels();
      if(msg) msg.textContent='渲染好了：'+lab; }
    else if(msg) msg.textContent='渲染失败：'+(d.detail||d.reason||'');
  }catch(e){ if(msg) msg.textContent='渲染失败：'+e; }
}
(function tvInit(){
  const e=document.getElementById('tvturn'); if(!e) return;
  const r=document.getElementById('tvr');
  if(r) r.addEventListener('input', function(){ TV_ANG=parseInt(r.value,10)||0; tvTurn(); });
  let drag=false,x0=0,a0=0;
  e.addEventListener('mousedown', function(ev){ drag=true; x0=ev.clientX; a0=TV_ANG; ev.preventDefault(); });
  window.addEventListener('mousemove', function(ev){ if(!drag) return;
    TV_ANG=((a0+Math.round((ev.clientX-x0)/30))%TV_N+TV_N)%TV_N; tvTurn(); });
  window.addEventListener('mouseup', function(){ drag=false; });
  tvLabels(); tvModels(); tvTurn(); tvStatic();
})();


// ── 3D 预览：转台（拖拽/滑杆转视角）+ 动作序列（播放）──
let PV_CUR='wave', PV_ANG=0, PV_FR=0, PV_TIMER=null, PV_ANGLES=8, PV_FRAMES=8;
function pvSrc(prefix, i){
  return '/api/blender/frame?name='+PV_CUR+'_'+prefix+String(i).padStart(2,'0')+'.png&t='+Date.now();
}
function pvShowAngle(){
  const el=document.getElementById('tt'); if(!el) return;
  el.src=pvSrc('a', PV_ANG % PV_ANGLES);
  const r=document.getElementById('ttrange'); if(r) r.value=PV_ANG % PV_ANGLES;
  document.getElementById('fidx') && null;
}
function pvShowFrame(){
  const el=document.getElementById('strip'); if(!el) return;
  el.src=pvSrc('f', PV_FR % PV_FRAMES);
  const t=document.getElementById('fidx'); if(t) t.textContent=(PV_FR%PV_FRAMES+1)+'/'+PV_FRAMES;
}
function playStrip(){
  stopStrip();
  PV_TIMER=setInterval(function(){ PV_FR=(PV_FR+1)%PV_FRAMES; pvShowFrame(); }, 140);
}
function stopStrip(){ if(PV_TIMER){ clearInterval(PV_TIMER); PV_TIMER=null; } }
async function renderPreview(){
  const msg=document.getElementById('pvmsg'); const c=document.getElementById('pvclip').value;
  msg.textContent='渲染中…（Blender 后台跑，约半分钟）';
  try{
    const d=await (await fetch('/api/blender/preview?clip='+encodeURIComponent(c),
      {method:'POST'})).json();
    if(d.ok){ PV_CUR=d['动作']; PV_ANGLES=d['转台'].length; PV_FRAMES=d['序列'].length;
      PV_ANG=0; PV_FR=0; pvShowAngle(); pvShowFrame();
      msg.textContent='渲染好了：转台 '+PV_ANGLES+' 角度 · 序列 '+PV_FRAMES+' 帧'; }
    else { msg.textContent='渲染失败：'+(d.reason||''); }
  }catch(e){ msg.textContent='渲染失败：'+e; }
}
(function pvInit(){
  const tt=document.getElementById('tt'); if(!tt) return;
  const r=document.getElementById('ttrange');
  if(r) r.addEventListener('input', function(){ PV_ANG=parseInt(r.value,10)||0; pvShowAngle(); });
  let drag=false, x0=0, a0=0;
  tt.addEventListener('mousedown', function(e){ drag=true; x0=e.clientX; a0=PV_ANG; e.preventDefault(); });
  window.addEventListener('mousemove', function(e){
    if(!drag) return;
    const d=Math.round((e.clientX-x0)/28);
    PV_ANG=((a0+d)%PV_ANGLES+PV_ANGLES)%PV_ANGLES; pvShowAngle();
  });
  window.addEventListener('mouseup', function(){ drag=false; });
  (async function(){
    try{
      const s=await (await fetch('/api/blender/status')).json();
      const sel=document.getElementById('pvclip');
      if(sel && s['动作']) sel.innerHTML=s['动作'].map(function(c){
        return '<option value="'+c+'">'+c+'</option>'; }).join('');
      if(sel) sel.value=PV_CUR;
    }catch(e){}
  })();
  pvShowAngle(); pvShowFrame();
})();
</script>
</body></html>"""
