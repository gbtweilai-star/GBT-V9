// panel/static/witness_card.js
const W_C = { live:'#35d39a', partial:'#ffbd59', gap:'#2a3442',
              backfill:'#9b7bff', pending:'#39d0ff' };
const STATE_COLOR = { valid:'#35d39a', pending:'#39d0ff', unreachable:'#ffbd59',
                      missing:'#ffbd59', invalid:'#ff5d73', disagreement:'#ff5d73' };

// ① 回填区间条：一行一见证，横轴 = seq
function coverageBar(segments, headSeq, { height = 14 } = {}) {
  const scale = seq => (headSeq ? (seq / headSeq) * 100 : 0);
  const rects = segments.map(s => `<rect x="${scale(s.from)}%" y="0"
      width="${Math.max(0.4, scale(s.to + 1) - scale(s.from))}%"
      height="${height}"><title>${s.kind} #${s.from}–${s.to}</title></rect>`);
  return `<svg class="cov-bar" viewBox="0 0 100 ${height}" preserveAspectRatio="none">
    <rect width="100" height="${height}" fill="${W_C.gap}"/>${rects.join('')}</svg>`;
}

async function renderWitnessCard(el) {
  const [w, cov] = await Promise.all([
    fetchJSON('/api/body/witnesses'), fetchJSON('/api/body/witnesses/coverage')]);
  const { quorum: q, probe, latest_sealed: sealed } = w;

  // ② 颜色优先级：冲突/签名错 → 红；不足 quorum 或快照过期 → 琥珀；否则绿
  const crit = w.witnesses.some(x => ['invalid','disagreement'].includes(x.live_status));
  const color = crit ? '#ff5d73'
              : (q.valid < q.required || probe.stale || q.status !== 'healthy') ? '#ffbd59'
              : '#35d39a';
  const label = crit ? '冲突 · 需人工裁决'
              : probe.stale ? '快照过期'
              : (q.valid < q.required ? `见证不足 ${q.valid}/${q.required}` : '健康');

  el.innerHTML = `
  <div class="panel-card">
    <header><span class="dot" style="background:${color}"></span><b>外部见证</b>
      <span class="pill" style="color:${color}">${label}</span>
      <span class="pill mono" title="历史事实：最新一条已封存锚当时的计数">
        sealed@${sealed ? '#' + sealed.seq : '-'}</span></header>

    <div class="grid">
      <div>实时有效见证 <b style="color:${color}">${q.valid}/${q.required}</b></div>
      <div>核验年龄 <b>${probe.age_seconds == null ? '未知'
        : fmtDuration(probe.age_seconds)}${probe.stale ? ' ⚠过期' : ''}</b></div>
      <div>上次核验 <b class="mono">${probe.observed_at || '-'}</b></div>
      <div>锚点总数 <b>${cov.global.reduce((n,s)=>n+(s.to-s.from+1),0)}</b></div>
    </div>

    <div class="cov-global" data-drill="global">
      ${coverageBar(cov.global, cov.head_seq, { height: 10 })}</div>
    <div class="cov-legend"><span style="color:${W_C.live}">足量实时见证</span>
      <span style="color:${W_C.partial}">见证不足</span>
      <span style="color:${W_C.gap}">无锚点</span></div>

    <div class="wrows">${cov.per_witness.map(t => {
      const s = w.witnesses.find(x => x.witness_id === t.witness_id) || {};
      const c = STATE_COLOR[s.live_status] || '#888';
      return `<div class="wrow" data-witness="${t.witness_id}">
        <span class="dot" style="background:${c}"></span>
        <span class="mono wname">${t.witness_id}</span>
        <span class="pill" style="color:${c}">${s.live_status || '-'}</span>
        <span class="mono dim">${s.kid || '-'}</span>
        <span class="seg-bar">${coverageBar(t.segments, cov.head_seq)}</span>
        <span class="mono dim">实时自 #${s.live_from_seq ?? '-'}</span>
        <span class="mono dim" title="仅回填，不计 quorum">回填至 #${s.backfilled_through_seq ?? '-'}</span>
        ${s.consecutive_fail ? `<em class="warn">连失 ${s.consecutive_fail}</em>` : ''}
      </div>`; }).join('')}</div>

    ${w.witnesses.some(x => x.last_error) ? `<div class="alerts">
      ${w.witnesses.filter(x => x.last_error).map(x =>
        `<span class="warn mono">${x.witness_id}: ${x.last_error}</span>`).join('')}</div>` : ''}
  </div>`;

  el.querySelectorAll('[data-witness]').forEach(n => n.addEventListener('click',
    () => navPush({kind:'body_witness', id:n.dataset.witness})));
}
