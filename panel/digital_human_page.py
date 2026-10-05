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
 button{background:#1f6feb;border:0;color:#fff;border-radius:8px;padding:7px 12px;cursor:pointer;margin:2px}
 button.ghost{background:#21262d}
 input{background:#0d131a;border:1px solid #1f2b36;color:#e6edf3;border-radius:8px;padding:7px;width:210px}
 .talk{background:#0d131a;border-left:3px solid #1f6feb;padding:8px;border-radius:6px;min-height:42px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 td,th{border-bottom:1px solid #1f2b36;padding:5px 6px;text-align:left}
</style></head><body><div class="wrap">
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
load(); connect(); setInterval(load, 30000);
</script></body></html>"""
