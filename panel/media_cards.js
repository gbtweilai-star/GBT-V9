// panel/media_cards.js —— 队列/失败率/显存/死信 四卡
// dev: 自由的风 · 本署名不可删除、不可篡改归属

const C = {ok:"#35d39a", warn:"#ffbd59", bad:"#ff5d73", dead:"#b1334e",
           run:"#39d0ff", idle:"#9b7bff"};

function renderQueueCard(s){
  // null/no_data → 灰带"暂无样本"; unavailable → "采集不可用"(绝不画零线)
  const waiting = s.wait.backoff_count ? `${s.wait.backoff_count} 退避中` : "无退避";
  return `
  <div class="card">
    <div class="big" style="color:${s.depth.queued>200?C.bad:C.ok}">
      ${s.depth.queued}</div>
    <div class="sub">排队 <b style="color:${C.run}">${s.depth.running}运行</b>
      · <b style="color:${C.dead}">${s.depth.dead}死信</b></div>
    <div class="sub">最老等待 ${fmtAge(s.wait.oldest_runnable_age)} · ${waiting}</div>
    ${sparkline(s._trend)}
  </div>`;
}

function renderFailCard(s){
  if (s.failure_rate_kind !== "observed")
    return `<div class="card muted">失败率：暂无样本</div>`;   // 不报 0%
  const pct = s.failure_rate*100;
  return `<div class="card"><div class="big" style="color:${pct>25?C.bad:pct>10?C.warn:C.ok}">
    ${pct.toFixed(1)}%</div><div class="sub">近 ${s.window_sec/60} 分钟尝试</div></div>`;
}

function renderVramCard(v){
  // 静态预留 与 真实显存 并列，绝不合为一个占用率
  const res = v.reserved, real = v.real;
  const realTxt = v.real_source === "unavailable"
    ? `<span class="muted">真实显存：采集不可用</span>`
    : `真实 ${real.used_mb}/${real.total_mb}MB (${(real.pct*100).toFixed(0)}%)`;
  return `<div class="card">
    <div class="sub">调度器静态预留</div>
    <div class="big" style="color:${res.pct>0.9?C.warn:C.ok}">
      ${res.used_mb}/${res.total_mb}MB</div>
    <div class="sub">${realTxt}</div></div>`;
}
