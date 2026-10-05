# panel/media_page.py —— 生成队列监控页（可下钻）：深度/等待/失败率/显存/死信/事件流
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 诚实规则（页面侧）：
#   - 无样本 → 「暂无样本」灰字；采集不可用 → 「采集不可用」红字；绝不把 null 画成 0
#   - checklist：显存「静态预算」与「真实读数」分开显示，不合成为一个占用率
MEDIA_PAGE = r"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>GBT小土豆V9 · 生成队列监控</title>
<style>
body{margin:0;background:#080c18;color:#e8eefc;font:14px/1.55 system-ui,"Microsoft YaHei",sans-serif}
h1{font-size:18px;padding:14px 20px;margin:0;border-bottom:1px solid #24334a;display:flex;gap:12px;align-items:center}
h1 a{color:#39d0ff;font-size:13px;text-decoration:none;margin-left:auto}
.wrap{padding:16px 20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:#111a2b;border:1px solid #24334a;border-radius:12px;padding:12px 14px}
.card h3{margin:0 0 8px;font-size:13px;color:#9b7bff}
.big{font-size:26px;font-weight:800}
.kv{display:flex;justify-content:space-between;font-size:12px;color:#7f8db3;padding:2px 0}
.g{color:#35d39a}.y{color:#ffbd59}.r{color:#ff5d73}.muted{color:#7f8db3}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
th,td{padding:5px 8px;border-bottom:1px solid #24334a;text-align:left}
th{color:#7f8db3;font-weight:600}
tr.click{cursor:pointer}tr.click:hover{background:#16203a}
button{background:#1b2440;color:#cfe0ff;border:1px solid #24334a;border-radius:8px;
  padding:5px 10px;font-size:12px;cursor:pointer}
button.on{background:#12303f;border-color:#39d0ff;color:#39d0ff}
#tools{display:flex;gap:8px;padding:0 20px 6px;flex-wrap:wrap;align-items:center}
#drawer{position:fixed;top:0;right:0;height:100vh;width:min(560px,94vw);background:#0d1524;
  border-left:1px solid #39d0ff;transform:translateX(100%);transition:transform .2s;
  overflow-y:auto;padding:18px;z-index:50}
#drawer.on{transform:none}
pre{background:#0a1120;border:1px solid #24334a;border-radius:8px;padding:8px;font-size:11px;
  overflow:auto;max-height:220px}
</style></head><body>
<h1>🎞 GBT小土豆V9 · 生成队列监控
  <a href="/">← 回总控台</a></h1>

<div class=wrap>
  <div class=card id=c-depth><h3>队列深度</h3><div class=load>加载中…</div></div>
  <div class=card id=c-wait><h3>等待时长</h3><div class=load>加载中…</div></div>
  <div class=card id=c-fail><h3>失败率（事件表口径）</h3><div class=load>加载中…</div></div>
  <div class=card id=c-vram><h3>显存（预算 / 真实）</h3><div class=load>加载中…</div></div>
</div>

<div id=tools>
  <b style="font-size:13px">终态事件</b>
  <button id=f-all class=on>全部</button>
  <button id=f-failed>failed</button>
  <button id=f-completed>completed</button>
  <button id=f-order>排序：倒序</button>
  <span class=muted id=ev-meta></span>
</div>
<div style="padding:0 20px"><table><thead><tr>
  <th>时间</th><th>事件</th><th>任务</th><th>尝试</th><th>详情</th></tr></thead>
  <tbody id=ev></tbody></table>
  <div style="margin:8px 0 20px"><button id=more>加载更多</button></div>
</div>

<div class=wrap>
  <div class=card style="grid-column:1/-1"><h3>死信队列</h3>
    <table><thead><tr><th>任务</th><th>阶段</th><th>技能</th><th>尝试</th><th>错误</th><th></th></tr></thead>
    <tbody id=dead></tbody></table>
  </div>
</div>

<aside id=drawer><div style="float:right;cursor:pointer;color:#7f8db3" onclick="closeD()">✕</div>
  <div id=dBody></div></aside>

<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const t=ts=>ts?new Date(ts*1000).toLocaleString():"—";
let EV={event:null,order:"desc",cursor:null,rows:[]};

function closeD(){ $("#drawer").classList.remove("on"); }

function openD(title,html){
  $("#dBody").innerHTML="<h3 style='margin-top:0'>"+esc(title)+"</h3>"+html;
  $("#drawer").classList.add("on");
}

async function tickCards(){
  try{
    const r=await (await fetch("/api/media/queue/stats")).json();
    if(r.coverage==="unavailable"){
      $("#c-depth").innerHTML="<h3>队列深度</h3><div class=r>采集不可用</div>";
      $("#c-fail").innerHTML="<h3>失败率</h3><div class=r>采集不可用</div>";
      return;
    }
    const d=r.data, q=d.depth||{}, w=d.wait||{};
    $("#c-depth").innerHTML="<h3>队列深度</h3>"
      +"<div class=big>"+(q.queued||0)+"</div>"
      +"<div class=kv><span>运行中</span><b>"+(q.running||0)+"</b></div>"
      +"<div class=kv><span>死信</span><b class="+((q.dead||0)?"r":"g")+">"+(q.dead||0)+"</b></div>";
    $("#c-wait").innerHTML="<h3>等待时长</h3>"
      +"<div class=big>"+Math.round(w.oldest_runnable_age||0)+"s</div>"
      +"<div class=kv><span>最老可运行等待</span><span class=muted>退避不算等 GPU</span></div>"
      +"<div class=kv><span>退避中</span><b>"+(w.backoff_count||0)+"</b></div>"
      +"<div class=kv><span>下次重试（秒）</span><b>"+((w.next_retry_in==null)?"—":Math.round(w.next_retry_in))+"</b></div>";
    const fr=(d.failure_rate_kind==="observed")
      ? ("<div class=big>"+(d.failure_rate*100).toFixed(1)+"%</div>")
      : ("<div class=big muted>暂无样本</div>");
    $("#c-fail").innerHTML="<h3>失败率（事件表口径）</h3>"+fr
      +"<div class=kv><span>failed 原始计数</span><b class=r>"+(d.failed_count||0)+"</b></div>"
      +"<div class=kv><span>success 原始计数</span><b class=g>"+(d.success_count||0)+"</b></div>"
      +"<div class=kv><span>窗口</span><span class=muted>近 "+Math.round((d.window_sec||3600)/60)+" 分钟</span></div>";
  }catch(e){}
  try{
    const v=await (await fetch("/api/media/vram")).json();
    const d=v.data||{};
    let h="<h3>显存（预算 / 真实）</h3>";
    if(d.reserved){
      const pct=d.reserved.pct==null?"—":(d.reserved.pct*100).toFixed(0)+"%";
      h+="<div class=big>"+pct+"</div><div class=kv><span>静态预算</span><b>"
        +d.reserved.used_mb+"/"+d.reserved.total_mb+"MB</b></div>";
    } else h+="<div class=muted>静态预算：未配置</div>";
    if(d.real) h+="<div class=kv><span>真实显存 ("+esc(d.real_source)+")</span><b>"
      +d.real.used_mb+"/"+d.real.total_mb+"MB</b></div>";
    else h+="<div class=kv><span>真实显存</span><b class=muted>采集不可用</span></b></div>";
    $("#c-vram").innerHTML=h;
  }catch(e){}
}

async function tickEvents(more){
  const u=new URLSearchParams({limit:"20",order:EV.order});
  if(EV.event) u.set("event",EV.event);
  if(more&&EV.cursor){u.set("cursor_ts",EV.cursor.ts);u.set("cursor_id",EV.cursor.id);}
  const r=await (await fetch("/api/media/terminal-events?"+u.toString())).json();
  if(r.coverage==="unavailable"){
    $("#ev").innerHTML="<tr><td colspan=5 class=r>采集不可用</td></tr>";return;
  }
  const d=r.data;
  $("#ev-meta").textContent="共 "+d.total+" 条 · 排序 "+d.order;
  const rows=d.items.map(i=>
    "<tr class=click onclick=\"jobDetail('"+esc(i.job_id)+"')\">"
    +"<td>"+t(i.ts)+"</td>"
    +"<td class="+(i.event==="failed"?"r":"g")+">"+esc(i.event)+"</td>"
    +"<td>"+esc(i.job_id)+"</td><td>"+i.attempt_no+"</td>"
    +"<td class=muted>"+esc((i.detail||"").slice(0,60))+"</td></tr>");
  EV.rows = more? EV.rows.concat(rows): rows;
  $("#ev").innerHTML=EV.rows.join("")||"<tr><td colspan=5 class=muted>无记录</td></tr>";
  EV.cursor=d.next_cursor;
  $("#more").style.display=d.next_cursor?"":"none";
}

async function jobDetail(jobId){
  openD("任务 "+jobId,"<div class=muted>加载中…</div>");
  const r=await (await fetch("/api/media/queue/"+encodeURIComponent(jobId))).json();
  if(r.error){$("#dBody").innerHTML="<div class=r>"+esc(r.error)+"</div>";return;}
  const j=r.data.job,e=r.data.events||[];
  let h="<table>";
  [["项目",j.project_id],["阶段",j.stage],["技能",j.skill],["状态",j.state],
   ["尝试",j.attempts+"/"+j.max_attempts],["产物SHA",j.artifact_sha],
   ["错误",j.error],["租约",j.lease_owner],["创建",t(j.created_at)]]
   .forEach(kv=>h+="<tr><th>"+kv[0]+"</th><td>"+esc(kv[1]==null?"—":kv[1])+"</td></tr>");
  h+="</table><h3 style='font-size:13px'>事件链（同一任务）</h3><table>";
  h+="<tr><th>时间</th><th>事件</th><th>尝试</th><th>详情</th></tr>";
  e.forEach(x=>h+="<tr><td>"+t(x.ts)+"</td><td class="
    +(x.event==="failed"||x.event==="dead"?"r":"g")+">"+esc(x.event)
    +"</td><td>"+x.attempt_no+"</td><td class=muted>"+esc(x.detail||"")+"</td></tr>");
  h+="</table>";
  if(j.checkpoint) h+="<h3 style='font-size:13px'>checkpoint（断点续产）</h3><pre>"
    +esc(JSON.stringify(j.checkpoint,null,2))+"</pre>";
  $("#dBody").innerHTML=h;
}

async function tickDead(){
  const r=await (await fetch("/api/media/dead?limit=50")).json();
  if(r.coverage==="unavailable"){
    $("#dead").innerHTML="<tr><td colspan=6 class=r>采集不可用</td></tr>";return;
  }
  const d=r.data;
  $("#dead").innerHTML=(d.jobs||[]).map(j=>
    "<tr class=click onclick=\"jobDetail('"+esc(j.job_id)+"')\">"
    +"<td>"+esc(j.job_id)+"</td><td>"+esc(j.stage)+"</td><td>"+esc(j.skill)+"</td>"
    +"<td>"+j.attempts+"</td><td class=r>"+esc((j.error||"").slice(0,60))+"</td>"
    +"<td><button onclick=\"event.stopPropagation();requeue('"+esc(j.job_id)+"')\">重入队</button></td></tr>")
    .join("")||"<tr><td colspan=6 class=muted>无死信</td></tr>";
}

async function requeue(jobId){
  const rid=(crypto.randomUUID?crypto.randomUUID():String(Date.now()));
  const r=await (await fetch("/api/media/dead/"+encodeURIComponent(jobId)
      +"/requeue?request_id="+encodeURIComponent(rid),{method:"POST"})).json();
  if(r.error){alert("重入队失败："+r.error);return;}
  alert("已建立补偿任务："+JSON.stringify(r.data));
  tickDead(); tickCards();
}

$("#f-all").onclick=()=>{EV={event:null,order:EV.order,cursor:null,rows:[]};mark();tickEvents();};
$("#f-failed").onclick=()=>{EV={event:"failed",order:EV.order,cursor:null,rows:[]};mark();tickEvents();};
$("#f-completed").onclick=()=>{EV={event:"completed",order:EV.order,cursor:null,rows:[]};mark();tickEvents();};
$("#f-order").onclick=()=>{EV.order=(EV.order==="desc"?"asc":"desc");EV.cursor=null;EV.rows=[];
  $("#f-order").textContent="排序："+(EV.order==="desc"?"倒序":"正序");tickEvents();};
$("#more").onclick=()=>tickEvents(true);
function mark(){$("#f-all").className=EV.event?"":"on";$("#f-failed").className=EV.event==="failed"?"on":"";
  $("#f-completed").className=EV.event==="completed"?"on":"";}

tickCards(); tickEvents(); tickDead();
setInterval(tickCards,5000); setInterval(tickDead,10000);
</script></body></html>
"""
