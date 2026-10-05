// panel/static/snapshot_cards.js
const NULL_TXT = '—';                       // ★null 永不显示为 0
const STATE_UI = { unknown:  { t:'未知',   c:'#9b7bff' },
                   partial:  { t:'不完整', c:'#ffbd59' },
                   violation:{ t:'数据不自洽', c:'#ff5d73' } };

const fmt = (v, digits = 0) =>
  (v === null || v === undefined) ? NULL_TXT : Number(v).toFixed(digits);

function head(v) {                            // 统一表头：rev + 年龄 + stale 徽标
  const stale = v.stale;
  return `<div class="head">
    <span class="mono dim">rev ${v.revision ?? NULL_TXT}</span>
    <span class="mono dim">${v.observed_at ?? ''}</span>
    ${stale ? '<span class="pill warn" title="读数已超出有效期">已过期</span>' : ''}
    ${v.unknown_reason && !stale ? `<span class="pill">${v.unknown_reason}</span>` : ''}
  </div>`;
}

function devourCard(v) {
  const f = v.facts, bad = f.consistency !== 'ok';
  return `<div class="panel-card" data-domain="devour">
    <header><span class="dot" style="background:${bad?'#ff5d73':(v.stale?'#ffbd59':'#35d39a')}"></span>
      <b>吞噬能</b>${stalePill(v)}</header>
    ${head(v)}
    <div class="grid">
      <div>采集帧 <b data-m="frames">${fmt(f.frames)}</b></div>
      <div>丢帧间隙 <b data-m="gaps" style="color:${f.gap_intervals?'#ffbd59':'inherit'}">${fmt(f.gap_intervals)}</b></div>
      <div>已补齐 <b data-m="recovered">${fmt(f.recovered_frames)}</b></div>
      <div>未解决 <b data-m="unresolved" style="color:${f.unresolved_gap_frames?'#ff5d73':'inherit'}">${fmt(f.unresolved_gap_frames)}</b></div>
      <div>归档 <b style="color:${f.archive?.status==='ok'?'#35d39a':'#ffbd59'}">${
         {ok:'正常',lagging:'积压',unknown:'未知'}[f.archive?.status] || NULL_TXT}</b></div>
      <div>缓存 <b data-m="cache">${
         f.cache?.ratio == null ? NULL_TXT : Math.round(f.cache.ratio*100) + '%'}</b></div>
    </div>
    ${bad ? `<div class="alerts"><span class="bad">${STATE_UI.violation.t}：${f.consistency}</span></div>` : ''}
    <div class="spark" data-spark="devour" data-metric="frames"></div>
  </div>`;
}

function scanCard(v) {
  const f = v.facts, st = STATE_UI[f.status] || STATE_UI.unknown;
  return `<div class="panel-card" data-domain="scan">
    <header><span class="dot" style="background:${st.c}"></span><b>扫描覆盖</b>
      <span class="pill" style="color:${st.c}">${st.t}</span></header>
    ${head(v)}
    <div class="grid">
      <div>覆盖 <b data-m="coverage" style="color:${st.c}">${
        f.coverage == null ? NULL_TXT : Math.round(f.coverage*100)+'%'}</b></div>
      <div>目标 <b>${fmt(f.scanned)}/${fmt(f.expected)}</b></div>
      <div>漏洞项 <b>${fmt(f.findings)}</b></div>
      <div>未覆盖 <b data-m="gaps" style="color:${f.gaps?'#ff5d73':'inherit'}">${fmt(f.gaps)}</b></div>
    </div>
    ${f.reason ? `<div class="alerts"><span class="warn">${f.reason}</span></div>` : ''}
    ${(f.top_tentacles||[]).length ? `<div class="rank">${f.top_tentacles.slice(0,3).map(t =>
      `<div class="rank-row" data-tentacle="${t.tentacle_id}"><span class="mono">${t.tentacle_id}</span>
       <b>${t.gaps} 项</b></div>`).join('')}</div>` : ''}
    <div class="spark" data-spark="scan" data-metric="coverage"></div>
  </div>`;
}

function queueCard(v) {
  const f = v.facts, vr = f.vram || {};
  return `<div class="panel-card" data-domain="queue">
    <header><span class="dot" style="background:${
      f.failure_rate == null ? '#ffbd59' : (f.failure_rate > 0.05 ? '#ff5d73' : '#35d39a')}"></span>
      <b>媒体队列</b>${stalePill(v)}</header>
    ${head(v)}
    <div class="grid">
      <div>排队 <b data-m="depth">${fmt(f.depth)}</b></div>
      <div>等待中位 <b data-m="p50">${f.wait_p50_s == null ? NULL_TXT : fmt(f.wait_p50_s)+'s'}</b></div>
      <div>失败率 <b data-m="rate" style="color:${
        f.failure_rate == null ? '#ffbd59' : (f.failure_rate > 0.05 ? '#ff5d73' : '#35d39a')}">${
        f.failure_rate == null ? NULL_TXT : (f.failure_rate*100).toFixed(2) + '%'}</b></div>
      <div>失败/完成 <b>${fmt(f.failed)}/${fmt(f.completed)}</b></div>
      <div>死信 <b data-m="dead" style="color:${f.dead?'#ff5d73':'inherit'}">${fmt(f.dead)}</b></div>
      <div>显存 <b>${vr.total_mb == null ? NULL_TXT
        : `${fmt(vr.used_mb)}/${fmt(vr.total_mb)} MB`}</b></div>
    </div>
    ${f.event_count === 0 ? `<div class="alerts"><span class="warn">该窗口无终态事件，失败率未知（不是 0%）</span></div>` : ''}
    <div class="spark" data-spark="queue" data-metric="failure_rate"></div>
  </div>`;
}
