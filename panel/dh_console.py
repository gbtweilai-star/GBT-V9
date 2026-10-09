# panel/dh_console.py —— 数字人 · 语音交互台（简单 · 真会动 · 接真接口）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计读数（照 skills/gbt-aesthetic 的规矩，动手前先读题；主人 2026-10-08 原话：
#   「数字人页面不需要复杂的摆一堆东西，简单点，让人看来感觉自然逼真就行」）：
#   · 页面种类：交互台 ⇒ 主视觉只有一个她，读数收进抽屉，**默认不铺开**。
#   · 三档：VARIANCE=6（不对称但克制）· MOTION=7（呼吸/声波/动作件）· DENSITY=2（极简）。
#   · AI 味禁令：不排三张等宽卡、不堆玻璃拟态、不做无限微动效；按键走 .btn 家族。
#
# 诚实边界（写在代码里，不许含糊）：
#   ① 主视觉用的是仓里**真会动**的透明 WebM（state/tripo/render/*.webm，11 段，VP9 带 alpha）；
#      它们是**风格化**那套模型。写实那套（front_hero / full_hero）**只有静帧、没有动作件**。
#   ② 因此页面给一个「形象档」切换：会动（默认）/ 写实静帧。
#   ③ **没有专门的口型件** —— 说话时只能切呼吸/点头件 + 声波 + 高光脉冲，页面上如实标注。
#      真要做「说话对口型」的写实数字人，得重渲染一套带口型的动作件（下一步的活）。
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

CSS = """<style>
:root{--bg:#04070d;--ink:#e8f0fb;--dim:#7b8aa6;--line:#152337;--cy:#39d0ff;--vi:#9b7bff;--ok:#35d39a}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:radial-gradient(900px 620px at 50% 30%,#0a1626 0%,#04070d 62%) fixed;color:var(--ink);
     font:14px/1.55 system-ui,"Microsoft YaHei",sans-serif;overflow:hidden}
.hud{display:flex;align-items:center;gap:12px;padding:10px 18px;font-size:12.5px;color:var(--dim)}
.hud b{color:var(--ink);letter-spacing:.08em;font-weight:600}
.hud .dot{width:7px;height:7px;border-radius:50%;background:var(--ok);box-shadow:0 0 9px var(--ok)}
.hud .sp{margin-left:auto;display:flex;gap:8px;align-items:center}
.hud select,.hud .btn{font-size:12px}
.hud select{background:#0b1524;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:3px 8px}
.stage{position:relative;height:calc(100vh - 152px);display:grid;place-items:end center}
/* 人物：抬高到语音条之上（第一版被切脚），并给她一层背光把轮廓从虚空里剥出来 */
.stage video,.stage img{position:absolute;bottom:96px;height:min(72vh,690px);width:auto;
  filter:drop-shadow(0 22px 34px rgba(0,0,0,.6)) drop-shadow(0 0 22px rgba(57,208,255,.18));
  transition:opacity .4s ease,filter .4s ease}
.stage .hide{opacity:0;pointer-events:none}
/* 写实那套是**影棚渲染**（自带灰底，没有 alpha）：硬贴会像一块灰板，
   所以按「影棚肖像」处理 —— 圆角 + 边缘渐隐，让灰底融进虚空。 */
.stage img{border-radius:16px;
  -webkit-mask-image:radial-gradient(126% 104% at 50% 46%,#000 58%,transparent 99%);
  mask-image:radial-gradient(126% 104% at 50% 46%,#000 58%,transparent 99%)}
.back{position:absolute;bottom:150px;width:min(540px,66vw);height:min(540px,66vw);border-radius:50%;
  background:radial-gradient(closest-side,rgba(57,208,255,.15),rgba(155,123,255,.06) 55%,transparent 72%);
  filter:blur(12px);pointer-events:none}
.horizon{position:absolute;bottom:78px;left:0;right:0;height:1px;pointer-events:none;
  background:linear-gradient(90deg,transparent,rgba(57,208,255,.20) 30%,rgba(57,208,255,.20) 70%,transparent)}
.floor{position:absolute;bottom:60px;width:min(600px,76vw);height:130px;border-radius:50%;
  background:radial-gradient(closest-side,rgba(57,208,255,.16),rgba(57,208,255,.03) 62%,transparent);
  filter:blur(4px)}
/* 落地接触影：她得站在东西上，不是飘着 */
.contact{position:absolute;bottom:72px;width:min(280px,38vw);height:30px;border-radius:50%;
  background:radial-gradient(closest-side,rgba(0,0,0,.66),transparent 74%);filter:blur(5px)}
.rim{position:absolute;bottom:64px;width:min(500px,64vw);height:min(500px,64vw);border-radius:50%;
  border:1px solid rgba(57,208,255,.10);pointer-events:none}
/* 她的脸：设定图里切出来的表情件，放进**圆形通讯窗**（框会把裁切的边角吃掉）*/
/* 选择器写成 .stage .facewin 提权：否则会被上面的 .stage>*{position:relative} 盖掉绝对定位（踩过）*/
.stage .facewin{position:absolute;left:34px;bottom:150px;width:120px;height:120px;border-radius:50%;
  overflow:hidden;border:1px solid rgba(57,208,255,.30);background:#0a1220;z-index:4;
  box-shadow:0 0 22px rgba(57,208,255,.16);transition:box-shadow .3s ease,transform .3s ease}
/* ★position:static 必须显式写：否则会被上面的 .stage img{position:absolute;bottom:96px} 命中，
   图被顶到圆窗外，只剩顶部一条发丝（真踩过，不是猜）*/
.stage .facewin img{position:static;inset:auto;bottom:auto;left:auto;right:auto;top:auto;
  height:100%;width:100%;object-fit:cover;object-position:50% 42%;display:block;filter:none}
.stage .facewin.on{box-shadow:0 0 40px rgba(57,208,255,.55);transform:scale(1.03)}
.stage .facewin .cap{position:absolute;left:0;right:0;bottom:6px;text-align:center;font-size:11px;
  color:var(--dim);text-shadow:0 1px 3px #000}
.vignette{position:fixed;inset:0;pointer-events:none;z-index:1;
  background:radial-gradient(118% 88% at 50% 40%,transparent 44%,rgba(0,0,0,.52) 100%)}
.stage>*{position:relative;z-index:2}.stage video,.stage img{z-index:3}
.talk .stage video,.talk .stage img{filter:drop-shadow(0 26px 40px rgba(0,0,0,.55)) 
  drop-shadow(0 0 40px rgba(57,208,255,.42))}
.line{position:absolute;bottom:96px;left:34px;font-size:12.5px;color:var(--dim);letter-spacing:.04em;
  white-space:nowrap;font-variant-numeric:tabular-nums}
.voice{position:fixed;left:0;right:0;bottom:0;border-top:1px solid var(--line);
  background:linear-gradient(0deg,rgba(4,7,13,.98),rgba(6,11,20,.86));padding:10px 18px 12px;z-index:6}
.vrow{max-width:1100px;margin:0 auto;display:flex;gap:12px;align-items:center}
.wave{display:flex;align-items:flex-end;gap:3px;height:30px;margin-left:auto}
.wave i{width:3px;height:5px;border-radius:2px;background:var(--cy);opacity:.8}
.wave.on i{animation:wv .85s ease-in-out infinite}
.wave.on i:nth-child(2n){animation-delay:.1s}.wave.on i:nth-child(3n){animation-delay:.2s}
.wave.on i:nth-child(5n){animation-delay:.3s}
@keyframes wv{0%,100%{height:5px}50%{height:28px}}
@media(prefers-reduced-motion:reduce){.wave.on i{animation:none;height:12px}}
.say{flex:1;min-width:0;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.say .who{color:var(--cy)}.say .me{color:var(--vi)}
.drawer{position:fixed;right:14px;top:52px;width:min(340px,86vw);max-height:calc(100vh - 180px);
  overflow:auto;background:rgba(8,14,24,.96);border:1px solid var(--line);border-radius:14px;padding:12px 14px;
  z-index:8;display:none}.drawer.open{display:block}
.card{margin-bottom:10px}.card h4{margin:0 0 6px;font-size:11px;letter-spacing:.14em;color:var(--dim)}
.kv{display:flex;justify-content:space-between;font-size:12.5px;color:var(--dim)}.kv b{color:var(--ink)}
.bar{height:5px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden;margin:5px 0}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--cy),var(--vi))}
.muted{color:var(--dim)}.ok{color:var(--ok)}.warn{color:#ffbd59}
</style>"""

HTML = r"""<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GBT小土豆V9 · 她</title>__CSS__</head><body>
<header class=hud>
  <span class=dot></span><b>GBT小土豆V9</b><span id=st>数字人 · 语音交互台</span>
  <span class=sp>
    <select id=clip title="挑一个动作"></select>
    <select id=look title="形象档">
      <option value="anim">会动（默认）</option>
      <option value="real">写实静帧</option>
    </select>
    <button class=btn id=drawerbtn>读数</button>
  </span>
</header>

<div class=vignette></div>
<main class=stage id=stage>
  <div class=back></div><div class=floor></div><div class=horizon></div>
  <div class=rim></div><div class=contact></div>
  <video id=anim autoplay muted loop playsinline></video>
  <img id=real class=hide alt="写实形象">
  <div class=facewin id=facewin title="她的脸（情绪/说话时换表情）">
    <!-- 默认用 Blender 渲的 2048 高清件（设定图切出来的表情只有 ~112px，放大会糊） -->
    <img id=faceimg src="/api/tripo/frame?name=face_real.png" alt="她的脸">
    <span class=cap id=facecap>高清</span>
  </div>
  <div class=line id=line>她在待机</div>
</main>

<aside class=drawer id=drawer>
  <div class=card><h4>元认知 · 她怎么看自己</h4><div id=meta class=muted>加载中…</div></div>
  <div class=card><h4>身体 · 她自己说</h4><div class=kv><span>有效见证</span><b id=valid>—</b></div>
    <div class=muted id=stale style="font-size:12px"></div></div>
  <div class=card><h4>触手编队</h4><div id=fleet class=muted>加载中…</div></div>
  <div class=card><h4>说明</h4><div class=muted style="font-size:12px">
    动作件是仓里真有的透明 WebM（11 段全身动作）；写实档只有静帧。</div>
    <div class=warn style="font-size:12px">说话时用呼吸/点头件 + 声波：**还没有专门的口型件**。</div></div>
</aside>

<footer class=voice>
  <div class=vrow>
    <button class="btn primary" id=ptt>按住说话</button>
    <button class=btn id=saybtn>说一句</button>
    <label class=muted style="font-size:12px"><input type=checkbox id=mute style="width:auto"> 静音</label>
    <div class=say id=say>（还没说话）</div>
    <div class=wave id=wave aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
  </div>
</footer>
<script>
const $=i=>document.getElementById(i);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct=v=>(v==null?'—':Math.round(v*100)+'%');
let CLIPS=[], CUR='idle';
function talking(on){ $('stage').classList.toggle('talk',!!on); $('wave').classList.toggle('on',!!on);
  var fw=$('facewin'); if(fw) fw.classList.toggle('on',!!on); }
function say(t,who){ $('say').innerHTML='<span class='+(who==='me'?'me':'who')+'>'+(who==='me'?'你':'她')+'</span> '+esc(t); }
// 浏览器兜底也走台湾腔：优先挑 zh-TW 语音，挑不到也把 lang 标成 zh-TW（不许默认滑到 zh-CN）
function twVoice(){
  try{ const vs=speechSynthesis.getVoices()||[];
    return vs.find(v=>/zh[-_]TW/i.test(v.lang)) || vs.find(v=>/HsiaoChen|Yating|Taiwan/i.test(v.name)) || null;
  }catch(e){ return null; }
}
function speakLocal(t){ if($('mute').checked) return; try{ const u=new SpeechSynthesisUtterance(t);
  u.lang='zh-TW'; const v=twVoice(); if(v) u.voice=v; u.rate=0.95; u.pitch=1.05;
  speechSynthesis.speak(u); }catch(e){} }
function play(name){
  if(!name) return; CUR=name;
  const v=$('anim'); v.src='/api/tripo/anim?name='+encodeURIComponent(name+'.webm');
  v.play().catch(()=>{});
  $('line').textContent='动作：'+name;
}
async function loadClips(){
  try{ const d=await (await fetch('/api/tripo/anims')).json();
    CLIPS=(d['动作']||d['anims']||d['列表']||[]).map(x=>typeof x==='string'?x:(x['名']||x.name)).filter(Boolean);
  }catch(e){ CLIPS=[]; }
  if(!CLIPS.length) CLIPS=['idle','wave','nod','bow','cheer'];
  $('clip').innerHTML=CLIPS.map(n=>'<option value="'+n+'">'+n+'</option>').join('');
  $('clip').value=CUR; $('clip').onchange=e=>play(e.target.value);
  play(CUR);
}
function look(mode){
  const v=$('anim'), r=$('real');
  if(mode==='real'){ r.src='/api/tripo/frame?name=real_hero.png&t='+Date.now(); r.classList.remove('hide'); v.classList.add('hide'); }   // 真人档：主人给的新形象
  else { r.classList.add('hide'); v.classList.remove('hide'); v.play().catch(()=>{}); }
}
async function loadMeta(){
  try{ const m=await (await fetch('/api/brain/metacog')).json(); const c=m['校准']||{};
    $('meta').innerHTML='<div>'+esc(m['自我陈述']||'（还没有可陈述的）')+'</div>'+
      '<div class=kv style="margin-top:6px"><span>记忆</span><b>'+(c['记忆数']??'—')+'</b></div>'+
      '<div class=kv><span>被用到</span><b>'+pct(c['用到率'])+'</b></div>'+
      '<div class=bar><i style="width:'+Math.round((c['用到率']||0)*100)+'%"></i></div>'+
      '<div class=kv><span>答不上来</span><b>'+pct(c['未答率'])+'</b></div>';
  }catch(e){ $('meta').textContent='元认知取不到'; }
}
async function loadWitness(){
  try{ const d=await (await fetch('/api/digital-human/witness')).json();
    $('valid').textContent=(d.valid_count??'—')+' / '+(d.required??'—');
    $('stale').textContent=d.stale?('快照已过期：'+(d.safe_sentence||'')):('观测于 '+(d.observed_at||'—'));
  }catch(e){ $('valid').textContent='—'; }
}
async function loadFleet(){
  try{ const f=await (await fetch('/api/fleet/status')).json(); const c=f.config||{}, r=f.drives||{};
    $('fleet').innerHTML='<div class=kv><span>规模</span><b>'+(c.n||0)+' 根</b></div>'+
      '<div class=kv><span>独立凭据</span><b>'+(c.own_key_tentacles??'—')+'</b></div>'+
      '<div class=kv><span>驱动</span><b>'+(r.drives||0)+' 次</b></div>';
  }catch(e){ $('fleet').textContent='取不到'; }
}
function connect(){
  try{ const es=new EventSource('/api/digital-human/stream');
    es.onopen=()=>{ $('st').textContent='数字人 · 已连接'; };
    es.onerror=()=>{ $('st').textContent='数字人 · 重连中…'; };
    es.addEventListener('witness',e=>{ const m=JSON.parse(e.data);
      if(m.type==='speaking'){ talking(true); say(m.text); }
      else if(m.type==='spoken'){ talking(false); }
      else if(m.type==='snapshot'){ $('valid').textContent=(m.valid_count??'—')+' / '+(m.required??'—'); } });
  }catch(e){}
}
let REC=null, CH=[], ON=false;
async function pttStart(){
  if(ON) return;
  try{ const s=await navigator.mediaDevices.getUserMedia({audio:true}); CH=[];
    REC=new MediaRecorder(s); REC.ondataavailable=e=>{ if(e.data.size) CH.push(e.data); };
    REC.onstop=async()=>{ s.getTracks().forEach(t=>t.stop()); await pttSend(); };
    REC.start(); ON=true; $('ptt').classList.add('on'); talking(true); say('（我在听…）');
  }catch(e){ say('麦克风拿不到：'+e.message); }
}
function pttStop(){ if(!ON) return; ON=false; $('ptt').classList.remove('on'); try{ REC.stop(); }catch(e){} }
async function pttSend(){
  if(!CH.length){ talking(false); return; }
  try{ const r=await fetch('/api/voice/ptt',{method:'POST',headers:{'Content-Type':'audio/webm'},body:new Blob(CH,{type:'audio/webm'})});
    const d=await r.json();
    if(d['听见']) say(d['听见'],'me');
    if(d['回话']){ say(d['回话']); speakLocal(d['回话']); play('nod'); setTimeout(()=>play('idle'),1400); }
    if(!d.ok) $('line').textContent='没成：'+(d.reason||'');
  }catch(e){ say('对讲失败：'+e.message); }
  talking(false);
}
async function sayOne(){
  const t='主人好，我在。我看到什么就说什么，拿不到的我不会编。';
  say(t); talking(true); play('nod');
  try{ const d=await (await fetch('/api/voice/say',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})})).json();
    $('line').textContent=d.ok?('已发声（'+d['通道']+'）'):('未成：'+(d.reason||'')); }catch(e){ $('line').textContent='发声失败'; }
  setTimeout(()=>{ talking(false); play('idle'); },1800);
}
$('ptt').addEventListener('mousedown',pttStart); $('ptt').addEventListener('mouseup',pttStop);
$('ptt').addEventListener('mouseleave',pttStop);
$('ptt').addEventListener('touchstart',e=>{e.preventDefault();pttStart();});
$('ptt').addEventListener('touchend',e=>{e.preventDefault();pttStop();});
$('saybtn').addEventListener('click',sayOne);
$('look').addEventListener('change',e=>look(e.target.value));
$('drawerbtn').addEventListener('click',()=>{ $('drawer').classList.toggle('open'); loadMeta(); loadWitness(); loadFleet(); });
document.addEventListener('keydown',e=>{ if(e.code==='Space'&&document.activeElement===document.body){ e.preventDefault(); pttStart(); } });
document.addEventListener('keyup',e=>{ if(e.code==='Space') pttStop(); });
// ── 表情：设定图的 4 张脸 + 情绪映射（喜/乐→happy · 怒→angry · 哀/惊→fear · 其余→curious）──
// 表情档：设定图切出来的 4 张脸（~112px，放大会糊）；默认姿态用 Blender 渲的 2048 高清件。
// 口径写在脸上：切到表情档时标题如实带（设定图），别让低清冒充高清。
const FACE={happy:['face_happy.png','开心（设定图）'],angry:['face_angry.png','生气（设定图）'],
            fear:['face_fear.png','不安（设定图）'],curious:['face_curious.png','专注（设定图）'],
            hi:['face_hires.png','高清（Blender 渲）'],
            real:['face_real.png','真人档']};
function setFace(kind){
  const f=FACE[kind]||FACE.curious;
  $('faceimg').src='/api/tripo/frame?name='+f[0]+'&t='+Date.now();
  $('facecap').textContent=f[1];
}
function moodFace(m){
  m=String(m||'');
  if(!m) return 'real';   // 默认用真人档当脸（主人 2026-10-09：这张看起来真实点）
  if(/喜|乐|开心|happy/.test(m)) return 'happy';
  if(/怒|气|angry/.test(m)) return 'angry';
  if(/哀|惊|怕|fear|sad/.test(m)) return 'fear';
  return 'curious';
}
async function loadFace(){
  try{ const e2=await (await fetch('/api/voice/emotion')).json();
    setFace(moodFace(e2.mood));
  }catch(e){ /* 取不到情绪就保持当前表情，不编 */ }
}
loadClips(); connect(); loadWitness(); loadFace();
setInterval(loadFace, 30000);
setInterval(loadWitness,60000);
</script></body></html>"""


def render() -> str:
    return HTML.replace("__CSS__", CSS)


@router.get("/digital-human/console", response_class=HTMLResponse)
async def dh_console() -> str:
    from skills.ui_design import inject
    return inject(render(), "/digital-human/console")


__all__ = ["router", "render", "HTML", "CSS"]
