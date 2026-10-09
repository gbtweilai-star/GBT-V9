# panel/voice_page.py —— 开机 · 数字人全息语音对讲（APP 启动第一屏）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：
#   "在 APP 下载启动的时候是数字人页面……启动的时候数字人指挥 AI 开始扫描环境，
#    每一步都使用自然的台湾腔女声来跟用户沟通对话式操作，设置一个退出数字人语音对讲
#    操作模式的开关，后面才是总控台和各类能力页面按键以及操控界面，
#    以及用户和数字人对话按住空格键对话全部都要设计好。"
#   + 视觉参考：全息 HUD（青色光环 / 数据浮层 / 扫描线 / 海报字）
#   + "数字人 AI 可以操控任何一个页面，只要用户同意" → 这里就是**同意闸门**的开关
#
# 一屏四块：① 全息数字人（骨架 + 光环 + 扫描线）② 开机九步环境扫描（逐步台湾腔播报）
#          ③ 按住空格说话（本机录音→本机听写→执行→回话）④ 授权开关 + 退出到总控台
import asyncio as _a

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse

from core import boot_scan as BS
from core import page_control as PC
from core import voice_center as VC
from skills.ui_design import Page

router = APIRouter()


JS = """
let T0=performance.now(), SPK=false, SPK_UNTIL=0, REC=false, MEDIA=null, CHUNKS=[], GRANTED=false;
let SRV=false, HOLD=0, MICOK=false;
const S=id=>document.getElementById(id);
function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
let BLINK=0, EXPR='neutral';
// ★数字人形象 = 3D 模型的**骨骼驱动连续动作**（mp4 循环播）——不是切图片。
// 实现：页面上只留一个 <video autoplay loop muted playsinline>，切换动作时只换 src，
//       所以没有逐帧轮询（既自然又轻）。
let PV_ACT='idle', PV_ANIMS=[], PV_READY=false, PV_TALK=false, PV_LAST=0;
let PV_FILES={};
function pvFile(kind){ return PV_FILES[kind] || (kind+'.webm'); }
function pvURL(kind){ return '/api/tripo/anim?name='+pvFile(kind); }
function pvPoster(kind){ return '/api/tripo/anim?name='+kind+'_poster.png'; }
function pvHas(kind){ return !!PV_FILES[kind]; }
function pvPlay(kind){
  if(!pvHas(kind)) kind = pvHas('idle') ? 'idle' : (PV_ANIMS[0]||{名:''})['名'];
  if(!kind) return;
  const el=S('rig');
  let v=el.querySelector('video');
  if(v && v.dataset.act===kind) return;
  if(!v){
    el.innerHTML='<video class=v9face data-act="'+kind+'" autoplay loop muted playsinline '+
      'poster="'+pvPoster(kind)+'" src="'+pvURL(kind)+'"></video>';
    const nv=el.querySelector('video'); if(nv){ nv.play && nv.play().catch(function(){}); }
    PV_ACT=kind; return;
  }
  if(PV_ACT!==kind){ PV_ACT=kind; v.dataset.act=kind; v.poster=pvPoster(kind); v.src=pvURL(kind);
    v.play && v.play().catch(function(){}); }
}
async function rig(){
  try{
    if(!PV_READY){
      const d=await (await fetch('/api/tripo/anims')).json();
      PV_ANIMS=d['动作']||[];
      PV_FILES={}; PV_ANIMS.forEach(function(a){ PV_FILES[a['名']]=a['文件']||(a['名']+'.webm'); });
      if(!PV_ANIMS.length){ S('rig').innerHTML='<div class=muted style="padding:24px">还没有动作视频：去 /digital-human 渲一段</div>'; return; }
      PV_READY=true;
    }
    const talking = PV_TALK && (performance.now()-PV_LAST) < 2600;
    pvPlay(talking && pvHas('wave') ? 'wave' : 'idle');
  }catch(e){}
}
function pvView(k){                    // 视角按钮：正面/左侧/背面/右侧
  PV_HOLD=1; PV_I={front:4,left:2,back:0,right:6,three:5}[k]; PV_LAST=performance.now();
  PV_I=PV_I===undefined?0:PV_I;
  rig();
  setTimeout(function(){ PV_HOLD=0; }, 2500);
}
function setExpr(e){ EXPR=e; }
function setClip(c,expr){                       // 换动作：从头播一次
  if(c) CLIP=c;
  if(expr) EXPR=expr;
  CLIP0=performance.now();
}
function say(txt, who){
  const box=S('talk'); if(!box) return;
  box.insertAdjacentHTML('beforeend',
    '<div class="bubble'+(who==='me'?' me':'')+'">'+esc(txt)+'</div>');
  box.scrollTop=box.scrollHeight;
}
async function speak(text, style){
  if(!text) return; SPK=true;
  PV_TALK=true; PV_LAST=performance.now();   // 说话时短暂切脸，其余时间轮转全身
  try{ await fetch('/api/voice/say',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text:text,taiwan:true,style:style||''})}); }catch(e){}
  setTimeout(function(){ SPK=false; PV_TALK=false; }, Math.min(9000, 90*text.length+900));
}
async function mood(档位, 文本){                  // 人格档位：换动作+表情+用台湾腔韵律说一句
  try{
    const q='/api/persona/react'+(档位?('?档='+encodeURIComponent(档位)):'')+
      (文本?('&文本='+encodeURIComponent(文本)):'');
    const r=await (await fetch(q)).json();
    档=r['档']; setClip(r['动作'], r['表情']);
    say(r['台词']); await speak(r['台词'], r['样式']);
    S('pg').innerHTML='<span class=ok>🎭 人格</span> '+esc(r['性格'])+' · 当前 <b>'+esc(档)+'</b>'+
      ' <span class=muted>'+esc(r['说明'])+'</span>';
    S('pgbtn').innerHTML=['可爱','讨好','发火'].map(g=>
      '<button class="btn sm'+(g===档?' on':'')+'" data-g="'+g+'" onclick="mood(this.dataset.g)">'+
      ({'可爱':'🎀 可爱','讨好':'🥺 撒娇讨好','发火':'💢 泼辣发火'}[g])+'</button>').join('');
  }catch(e){}
}
async function pgstat(){
  try{
    const d=await (await fetch('/api/persona/state')).json();
    const p=d['人格']||{};
    S('pg').innerHTML='<span class=ok>🎭 人格</span> '+esc(p['性格']||'')+' · 当前 <b>'+esc(d['当前档']||'可爱')+'</b>'+
      '<br><span class=muted>嗓子：'+esc(p['嗓子']||'')+'</span>';
    S('pgbtn').innerHTML=['可爱','讨好','发火'].map(g=>
      '<button class="btn sm'+(g===(d['当前档']||'可爱')?' on':'')+'" data-g="'+g+'" onclick="mood(this.dataset.g)">'+
      ({'可爱':'🎀 可爱','讨好':'🥺 撒娇讨好','发火':'💢 泼辣发火'}[g])+'</button>').join('');
  }catch(e){}
}
async function boot(){
  const hello='哈囉，我是 GBT小土豆V9 的数字人。开机了，我先帮你把环境扫描一遍喔。';
  say(hello); speak(hello);          // 打招呼不阻塞扫描（语音排队播，进度条该走就走）
  for(let i=0;i<12;i++){
    const d=await (await fetch('/api/boot/step?i='+i)).json();
    if(d.done){ say(d['说']); speak(d['说']); break; }
    S('scan').insertAdjacentHTML('beforeend',
      '<div class="chip '+(d.ok?'g':'y')+'"><b>'+(d.ok?'✅':'⚠️')+' '+esc(d['名称'])+'</b>'+
      '<span class=muted> '+d.ms+'ms</span><br><span class=dim>'+esc(d['播报'])+'</span></div>');
    say(d['名称']+'：'+d['播报']);
    speak(d['播报']);                 // 不 await：扫描进度不等语音
    S('pct').textContent=Math.round((i+1)/d['总数']*100)+'%';
  }
  S('hint').innerHTML='扫描完了。按住 <b>空格</b> 跟我说话；要我帮你操作页面，先把右边开关打开。';
}
async function grant(on){
  const d=await fetch('/api/control/consent',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({grant:!!on})}).then(r=>r.json());
  GRANTED=!!d['授权'];
  S('consent').innerHTML = GRANTED
    ? '<span class=ok>● 已授权：她可以操作任何页面</span>'
    : '<span class=muted>○ 未授权：她不动手</span>';
  S('cbtn').textContent = GRANTED ? '收回授权' : '允许她操作页面';
  S('cbtn').onclick = function(){ grant(!GRANTED); };
  say(GRANTED ? '好，你同意的话，我就可以帮你操作任何页面了。'
              : '好，我收手了，只动嘴不动手。');
  await speak(GRANTED ? '好，你同意的话，我就可以帮你操作任何页面了。'
                      : '好，我收手了，只动嘴不动手。');
}
async function cstat(){
  try{ const d=await (await fetch('/api/control/status')).json();
    GRANTED=!!(d['授权']||{})['授权'];
    S('consent').innerHTML = GRANTED
      ? '<span class=ok>● 已授权：她可以操作任何页面</span>'
      : '<span class=muted>○ 未授权：她不动手</span>';
    S('cbtn').textContent = GRANTED ? '收回授权' : '允许她操作页面';
  }catch(e){}
}
async function tog(){
  if(SRV){ return togSrv(); }                  // 浏览器拿不到话筒 → 走本机（服务端）话筒
  if(!REC){
    try{
      const st=await navigator.mediaDevices.getUserMedia({audio:true});
      MEDIA=new MediaRecorder(st); CHUNKS=[];
      MEDIA.ondataavailable=e=>{ if(e.data && e.data.size) CHUNKS.push(e.data); };
      MEDIA.start(); REC=true; S('ptt').innerHTML='<span class=bad>● 正在听（放开空格就发送）</span>';
    }catch(e){
      SRV=true;                                  // 转到本机话筒能力（离线听写，无需密钥）
      await micstat(true);
      S('ptt').innerHTML='<span class=warn>浏览器没有可用话筒 → 已切本机话筒</span>';
      togSrv();
    }
    return;
  }
  REC=false; S('ptt').innerHTML='… 辨识中';
  await new Promise(r=>{ MEDIA.onstop=r; MEDIA.stop(); });
  const blob=new Blob(CHUNKS,{type:(MEDIA.mimeType||'audio/webm')});
  const buf=await blob.arrayBuffer(); const u8=new Uint8Array(buf);
  let bin=''; for(let i=0;i<u8.length;i++) bin+=String.fromCharCode(u8[i]);
  const d=await fetch('/api/voice/ptt',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({audio_b64:btoa(bin),mime:blob.type})}).then(r=>r.json());
  if(d['听见']){
    say(d['听见'],'me');
    if(d['意图']==='命令' && GRANTED){        // 命令 + 已授权 → 交给页面操控链
      const p=await fetch('/api/control/execute',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text:d['听见']})}).then(r=>r.json());
      if(p['ok']){ const msg=p['说']||d['回话']; say(msg); await speak(msg); }
      else { say(p['reason']||d['回话']); await speak(p['说']||p['reason']||'我不太会做这个，你说清楚一点？'); }
    } else { say(d['回话']); await speak(d['回话']); }
  } else { say('（没听成：'+(d.reason||'')+'）'); await speak('我刚刚没听清楚，你再说一次好不好？'); }
  S('ptt').innerHTML='按住 <b>空格</b> 说话';
}
document.addEventListener('keydown',function(e){
  if(e.code==='Space'){
    e.preventDefault();
    if(SRV){ if(!HOLD){ HOLD=Date.now(); S('ptt').innerHTML='<span class=bad>● 本机话筒正在听（放开空格就辨识）</span>'; } return; }
    if(!REC) tog();
  }
  if(e.code==='Escape'){ location.href='/'; }
});
document.addEventListener('keyup',function(e){
  if(e.code!=='Space') return;
  if(SRV){ if(HOLD){ const sec=Math.max(1.2,Math.min(15,(Date.now()-HOLD)/1000)); HOLD=0; togSrv(sec); } return; }
  if(REC) tog();
});
async function togSrv(sec){                      // 本机话筒：服务端采集 → 离线听写 → 执行 → 回话
  S('ptt').innerHTML='… 本机话筒采集 '+(sec||3)+' 秒';
  let d={};
  try{ d=await (await fetch('/api/mic/listen?sec='+(sec||3))).json(); }catch(e){ d={ok:false,reason:'本机话筒接口不通'}; }
  const txt=d['文本']||'';
  if(!txt){
    say('（本机话筒：'+(d['结论']||d['reason']||'没听到内容')+'）');
    await speak('我这边没有听到清楚的声音，你可以打字跟我说，或者插上麦克风再试一次。');
    S('ptt').innerHTML='按住 <b>空格</b> 说话（本机话筒）'; micstat(true); return;
  }
  say(txt,'me');
  const pr=await reactSet(txt);                     // 先按她说的话定人格档位（被呛就叉腰瞪眼）
  let r={};
  try{ r=await (await fetch('/api/mic/say',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text:txt})})).json(); }catch(e){ r={ok:false,reason:'回话接口不通'}; }
  const msg=r['回话']||r['说']||r['reason']||'我听到了，但没想好怎么回。';
  say(msg); await speak(msg, pr?pr['样式']:'');
  S('ptt').innerHTML='按住 <b>空格</b> 说话（本机话筒）'; micstat(true);
}
async function reactSet(文本){                     // 只换档与姿势（不出声），对话路径里用
  try{
    const r=await (await fetch('/api/persona/react'+(文本?('?文本='+encodeURIComponent(文本)):''))).json();
    档=r['档']; setClip(r['动作'], r['表情']);
    S('pg').innerHTML='<span class=ok>🎭 人格</span> '+esc(r['性格'])+' · 当前 <b>'+esc(档)+'</b>'+
      ' <span class=muted>'+esc(r['说明'])+'</span>';
    S('pgbtn').innerHTML=['可爱','讨好','发火'].map(g=>
      '<button class="btn sm'+(g===档?' on':'')+'" data-g="'+g+'" onclick="mood(this.dataset.g)">'+
      ({'可爱':'🎀 可爱','讨好':'🥺 撒娇讨好','发火':'💢 泼辣发火'}[g])+'</button>').join('');
    return r;
  }catch(e){ return null; }
}
async function micstat(probe){
  try{
    const d=await (await fetch('/api/mic/status'+(probe?'?probe=1':''))).json();
    MICOK = probe ? !!(d['能收声的端点']||[]).length : null;
    const dev=(d['记忆']||{})['名称']||'未部署';
    const concl=d['结论']||'';
    const tips=(d['建议']||[]).slice(0,2).join('；');
    S('mic').innerHTML='<span class="'+(probe&&!MICOK?'warn':'ok')+'">🎙 本机话筒</span> '+
      esc(dev)+' · '+esc(concl)+
      (probe&&!MICOK ? '<br><span class=muted>'+esc(tips)+'</span>' : '')+
      ' <button class="btn sm" onclick="micdeploy()">换/重部署话筒</button>';
  }catch(e){ S('mic').innerHTML='<span class=warn>话筒状态取不到</span>'; }
}
async function micdeploy(){
  S('mic').innerHTML='… 正在枚举并部署话筒';
  try{ const d=await (await fetch('/api/mic/deploy',{method:'POST'})).json();
    S('mic').innerHTML='<span class=ok>🎙</span> '+esc(d['结论']||'已部署');
  }catch(e){ S('mic').innerHTML='<span class=warn>部署失败</span>'; }
  micstat(true);
}
async function exprs(){                          // 这一排 = 她会做哪几个动作（有视频才显示）
  const d=await (await fetch('/api/tripo/anims')).json();
  const names=(d['动作']||[]).map(function(a){ return a['名']; });
  PV_FILES={}; (d['动作']||[]).forEach(function(a){ PV_FILES[a['名']]=a['文件']||(a['名']+'.webm'); });
  PV_ANIMS=d['动作']||[];
  const label={idle:'呼吸站立',wave:'挥手',bow:'鞠躬',turn:'左右看',cheer:'欢呼',
               salute:'敬礼',armsx:'抱臂',think:'托腮',nod:'点头',shake:'摇头',shy:'害羞'};
  S('exprs').innerHTML = names.map(function(n){
    return '<button class="btn sm" data-a="'+n+'" onclick="pvPlay(this.dataset.a)">'+(label[n]||n)+'</button>';
  }).join('') || '<span class=muted>（还没渲动作）</span>';
}
async function voicestat(){                      // 她用什么嗓子说话：如实显示，不吹
  try{
    const d=await (await fetch('/api/voice/center/status')).json();
    const tts=(d['通道']||{})['回声通道']||{};
    const twv=tts['台湾女声(edge-tw)']||{};
    const sapi=tts['本机SAPI']||{};
    const isTw = String(twv['锁定语音']||'').startsWith('zh-TW-');
    const v = isTw ? (twv['锁定语音']+'（台湾女声·神经语音）') : (sapi['选中']||'未定');
    S('vb').innerHTML='<span class=ok>🔊 她的嗓子</span> '+esc(v)+
      ' · '+(isTw ? '<span class=ok>真台湾女声</span>' : '台湾腔韵律（回落本机 SAPI）')+
      '<br><span class=muted>断网时自动回落本机中文女声 + 台湾腔韵律；恢复联网即回台湾女声</span>';
  }catch(e){ S('vb').innerHTML='<span class=warn>声音状态取不到</span>'; }
  try{                                  // 她的元数据：版本/动作数/校验（接入后一眼可见）
    const m=await (await fetch('/api/avatar/meta')).json();
    if(S('meta')) S('meta').innerHTML='<span class=ok>🪪 她的元数据</span> 版本 '+esc(m['版本']||'')+
      ' · 动作 '+((m['动作']||[]).length)+' 支 · 校验 '+esc(String(((m['校验']||{})['通过']))+'/'+String(((m['校验']||{})['总项'])))+
      (m['built_at'] ? (' <span class=muted>· 生成于 '+esc(m['built_at'])+'</span>') : '');
  }catch(e){ if(S('meta')) S('meta').innerHTML='<span class=warn>元数据取不到</span>'; }
}
exprs();
micstat(false); voicestat(); pgstat();          // 状态条先取：开机扫描很重，别让它们排队等
rig(); setInterval(rig,340); boot(); cstat();
"""

CSS = """
.v9face{width:100%;border-radius:12px;display:block;box-shadow:0 6px 24px rgba(0,0,0,.35);background:transparent}
.holo{position:relative;border-radius:var(--r-lg);padding:14px;overflow:hidden;
  background:linear-gradient(180deg,rgba(10,32,48,.72),rgba(6,14,26,.82));
  border:1px solid rgba(57,208,255,.45);
  box-shadow:0 0 0 1px rgba(57,208,255,.12) inset,0 0 42px rgba(57,208,255,.10),var(--sh2-d3);
  clip-path:polygon(14px 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%,0 14px)}
.holo::before{content:'';position:absolute;inset:0;pointer-events:none;
  background:repeating-linear-gradient(180deg,transparent 0 3px,rgba(57,208,255,.05) 3px 4px)}
.holo::after{content:'';position:absolute;left:0;right:0;height:42%;pointer-events:none;
  background:linear-gradient(180deg,transparent,rgba(57,208,255,.09),transparent);
  animation:v9sweep 4.6s linear infinite}
@keyframes v9sweep{0%{top:-45%}100%{top:105%}}
.rings{position:absolute;left:50%;top:46%;transform:translate(-50%,-50%);pointer-events:none}
.ring{position:absolute;left:50%;top:50%;border-radius:50%;border:1px solid rgba(57,208,255,.32);
  transform:translate(-50%,-50%)}
.ring.r1{width:250px;height:250px;border-style:dashed;animation:v9spin 22s linear infinite}
.ring.r2{width:320px;height:320px;border-color:rgba(124,92,255,.28);animation:v9spin 34s linear infinite reverse}
.ring.r3{width:180px;height:180px;border-color:rgba(57,208,255,.5);animation:v9spin 14s linear infinite}
@keyframes v9spin{to{transform:translate(-50%,-50%) rotate(360deg)}}
.poster{font-size:30px;font-weight:800;letter-spacing:.32em;line-height:1;
  background:linear-gradient(92deg,var(--neon-ice),var(--accent) 45%,var(--neon-violet));
  -webkit-background-clip:text;background-clip:text;color:transparent;
  text-shadow:0 0 30px rgba(57,208,255,.25)}
.chip{padding:6px 10px;margin:4px 0;border-radius:var(--r-sm);font-size:12px;
  background:linear-gradient(180deg,rgba(57,208,255,.08),rgba(57,208,255,.02));
  border-left:2px solid var(--accent)}
.chip.g{border-left-color:var(--ok)} .chip.y{border-left-color:var(--warn)}
.chip .dim{color:var(--muted)}
.hudnum{font-variant-numeric:tabular-nums;color:var(--neon-ice);font-size:12px}
"""


@router.get("/voice", response_class=HTMLResponse)
async def voice_page():
    plan = await _a.to_thread(BS.plan)
    chips = "".join(f'<div class="hudnum">▸ {_e(s["名称"])}</div>' for s in plan)
    body = (
        '<div class=holo>'
        '<div class=rings><i class="ring r1"></i><i class="ring r2"></i><i class="ring r3"></i></div>'
        '<div style="display:flex;gap:18px;position:relative;z-index:2">'
        '<div style="flex:0 0 300px;text-align:center">'
        '<div class=poster>GBT&nbsp;V9</div>'
        '<div class=muted style="font-size:11px;letter-spacing:.24em;margin-top:2px">'
        'DIGITAL&nbsp;HUMAN&nbsp;/&nbsp;VOICE&nbsp;INTERCOM</div>'
        '<div id=rig style="min-height:280px;margin-top:4px"></div>'
        '<div id=ptt class=muted style="font-size:13px">按住 <b>空格</b> 说话</div>'
        '<div id=mic class=muted style="font-size:12px;margin-top:4px">… 话筒状态</div>'
        '<div id=vb class=muted style="font-size:12px;margin-top:3px">… 声音状态</div>'
        '<div id=meta class=muted style="font-size:12px;margin-top:3px">… 元数据</div>'
        '<div id=pg class=muted style="font-size:12px;margin-top:3px">… 人格</div>'
        '<div id=pgbtn class=btnbar style="justify-content:center;margin-top:4px">… 人格档位</div>'
        '<div id=consent class=muted style="font-size:12px;margin-top:6px">○ 未授权：她不动手</div>'
        '<div id=exprs class=btnbar style="justify-content:center;margin-top:4px"></div>'
        '<div class=btnbar style="justify-content:center;margin-top:6px">'
        '<button class=btn id=cbtn onclick="grant(true)">允许她操作页面</button></div>'
        '</div>'
        '<div style="flex:1">'
        '<div style="display:flex;justify-content:space-between;align-items:center">'
        '<b>开机环境扫描</b><span class=hudnum>进度 <b id=pct>0%</b></span></div>'
        '<div id=scan style="max-height:250px;overflow:auto;margin-top:6px"></div>'
        '<div id=hint class=muted style="font-size:12px;margin-top:8px">正在扫描环境…</div>'
        '</div>'
        '<div style="flex:0 0 190px"><b>扫描项</b><div style="margin-top:6px">' + chips +
        '</div>'
        '<div style="margin-top:12px"><button class="btn primary" onclick="location.href=\'/\'">'
        '退出语音对讲 → 总控台</button></div>'
        '<div class=muted style="font-size:11px;margin-top:6px">也可以直接按 Esc</div>'
        '</div></div>'
        '<div class=msgs id=talk style="margin-top:12px;max-height:200px;overflow:auto;'
        'position:relative;z-index:2"></div>'
        '</div>')
    return Page(title="GBT小土豆V9 · 数字人语音对讲", body=body, current="/voice",
                extra_css=CSS, extra_js=JS).render()


def _e(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if v is not None else "")


# ═══════════════ API ═══════════════
def _port(request) -> int | None:
    """本次请求落到的端口 —— 自检就拿它去探自己（别写死 8765：本机 8765 是另一套面板）。"""
    try:
        return int(request.url.port)
    except Exception:                                          # noqa: BLE001
        return None


@router.get("/api/boot/scan")
async def api_boot_plan(request: Request):
    return await _a.to_thread(BS.status)


@router.get("/api/avatar/meta")
async def api_avatar_meta():
    """数字人的**元数据**（真读数：身份/人格/嗓子/形象/动作清单 + 版本 + 校验）。

    主人要求："她的元数据你是不是都没接入啊！" —— 这里把它接出来：页面/别的模块
    都从这一处取"她是谁、什么版本、由什么构成、齐不齐"。
    """
    from core import avatar_meta as AM
    return await _a.to_thread(AM.current)


@router.get("/api/boot/step")
async def api_boot_step(request: Request, i: int = 0):
    """逐步跑开机扫描（页面边扫边用台湾腔播报）。"""
    return await _a.to_thread(BS.run_step, i, _port(request))


@router.post("/api/boot/all")
async def api_boot_all(request: Request):
    return await _a.to_thread(BS.run_all, _port(request))


@router.post("/api/voice/command")
async def api_command(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(VC.command, str(p.get("text") or ""),
                              speak_reply=bool(p.get("speak", True)),
                              taiwan=bool(p.get("taiwan", True)),
                              owner=str(p.get("owner") or "main"))


@router.post("/api/voice/ptt")
async def api_ptt(payload: dict = Body(default_factory=dict)):
    """按住空格说话：收浏览器录音（base64）→ 本机转换/听写 → 执行 → 回话。音频不出本机。"""
    import base64
    p = payload or {}
    b64 = str(p.get("audio_b64") or "")
    if not b64:
        return {"ok": False, "reason": "没收到音频"}
    try:
        audio = base64.b64decode(b64, validate=False)
    except Exception:                                          # noqa: BLE001
        return {"ok": False, "reason": "音频数据不合法"}
    mime = str(p.get("mime") or "audio/webm")
    suffix = ".wav" if "wav" in mime else (".ogg" if "ogg" in mime else ".webm")
    return await _a.to_thread(VC.ptt, audio, suffix=suffix,
                              speak_reply=bool(p.get("speak", True)),
                              taiwan=bool(p.get("taiwan", True)))


@router.post("/api/voice/hear")
async def api_hear(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(VC.hear, float((payload or {}).get("seconds") or 5.0))


@router.get("/api/voice/center/status")
async def api_center_status():
    return await _a.to_thread(VC.status)


from fastapi.responses import JSONResponse


@router.get("/api/avatar/face")
async def api_avatar_face(expr: str = "neutral", talking: int = 0, blink: float = 0.0,
                          clip: str = "idle", t: float = 0.0):
    """全身数字人形象（**全肢体绑骨**）：表情 + 口型 + 眨眼 + 动作（clip/t 走 32 动作库）。"""
    from core import avatar_face as AF
    e = expr if expr in AF.EXPRESSIONS else "neutral"
    svg = await _a.to_thread(AF.character_svg, expression=e, talking=bool(talking),
                             blink=blink, clip=clip, t=t)
    return {"svg": svg, "表情": e, "动作": clip, "t": round(float(t), 3),
            "talking": bool(talking), "blink": blink}


@router.get("/api/avatar/face/exprs")
async def api_avatar_exprs():
    from core import avatar_face as AF
    return {"表情": AF.expressions(), "形象": AF.status()}


@router.get("/api/persona/state")
async def api_persona_state():
    """人格状态：可爱 + 泼辣，能讨好也能发火骂人（三档 + 绑定的动作/表情/韵律）。"""
    from core import persona as P
    return await _a.to_thread(P.state)


@router.get("/api/persona/react")
async def api_persona_react(档: str = "", 事件: str = "", 文本: str = "", 情绪: str = ""):
    """取一次人格反应：档位 + 台词 + 动作 + 表情 + 韵律样式（页面直接播，不用再判一次）。"""
    from core import persona as P
    if 档 in P.GEARS:
        g = P.GEARS[档]
        return {"档": 档, "台词": await _a.to_thread(P.line, 档), "动作": g["动作"][0],
                "表情": g["表情"], "样式": g["样式"], "说明": g["说明"],
                "性格": P.PERSONA["性格"], "嗓子": P.PERSONA["嗓子"],
                "边界": P.PERSONA["边界"]}
    return await _a.to_thread(P.react, 事件=事件, 文本=文本, 情绪=情绪)


@router.get("/api/voice/pin")
async def api_voice_pin():
    """钉死的嗓子：固定女声 + 台湾腔（男声永不入选）。"""
    from senses import voice_sapi as VS
    return await _a.to_thread(VS.pinned)


@router.get("/api/mic/status")
async def api_mic_status(probe: int = 0):
    """本机话筒能力状态：有哪些采集端、哪一路真能收声、缺麦时怎么办（如实报）。"""
    from core import mic_io as MI
    return await _a.to_thread(MI.status, probe=bool(probe))


@router.post("/api/mic/deploy")
async def api_mic_deploy():
    """枚举并部署本机话筒（钩子三步 + 固化；写入 state/mic.json 重启后沿用）。"""
    from core import mic_io as MI
    return await _a.to_thread(MI.deploy)


@router.get("/api/mic/listen")
async def api_mic_listen(sec: float = 3.5):
    """本机话筒采集一段 → 离线听写（无密钥）→ 返回文字。"""
    from core import mic_io as MI
    return await _a.to_thread(MI.listen, sec)


@router.post("/api/mic/say")
async def api_mic_say(payload: dict = Body(default_factory=dict)):
    """把（本机话筒听来的）文字交给对话/执行链，与她按住空格说话同一条路。
    默认不由服务端发声（SAPI 播放会占住音频设备并拖住响应），发声交给页面做。"""
    p = payload or {}
    txt = str(p.get("text") or "").strip()
    if not txt:
        return {"ok": False, "reason": "没听到内容"}
    return await _a.to_thread(VC.command, txt, speak_reply=bool(p.get("speak", False)))


@router.get("/api/voice/consent")
async def api_consent_state():
    return await _a.to_thread(PC.consent)


__all__ = ["router"]
