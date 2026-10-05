// panel/static/digital_human_witness.js
async function initWitnessPanel(root) {
  const line = await fetchJSON('/api/digital-human/witness');
  render(root, line);
  const es = new EventSource('/api/digital-human/stream');     // 浏览器自动带 Last-Event-ID
  es.addEventListener('witness', e => {
    const m = JSON.parse(e.data);
    if (m.type === 'snapshot') render(root, { ...m, safe_sentence: currentSentence });
    if (m.type === 'alert' || m.type === 'spoken' || m.type === 'interrupted')
      appendEvent(root, m);
  });
}

function render(root, d) {
  const color = d.stale ? '#ffbd59' : (d.valid_count >= d.required ? '#35d39a' : '#ffbd59');
  root.querySelector('.wline').innerHTML = `
    <span class="dot" style="background:${color}"></span>
    <b>有效见证 <span style="color:${color}">${d.valid_count ?? '-'}/${d.required}</span></b>
    <span class="pill" style="color:${color}">${d.status || '未知'}</span>
    ${d.stale ? `<span class="pill warn">过期 ${d.age_seconds}s</span>` : ''}
    <span class="mono dim">rev ${d.revision ?? '-'} · ${d.observed_at || '-'}</span>
    <button id="ask">问它当前见证数</button>`;
  root.querySelector('#ask').onclick = async () => {
    const r = await fetchJSON('/api/digital-human/ask/witness', { method: 'POST' });
    appendEvent(root, { type: 'qa', text: r.safe_sentence, stale: r.stale });
  };
}

function appendEvent(root, m) {
  const tag = { alert:'告警', spoken:'已播', interrupted:'被打断', qa:'回答' }[m.type] || m.type;
  const failed = m.state === 'spoken_failed';
  root.querySelector('.evts').insertAdjacentHTML('afterbegin', `
    <div class="evt ${failed ? 'failed' : ''}">
      <span class="pill">${tag}</span>
      ${failed ? '<span class="pill bad">未播报</span>' : ''}
      <span>${escapeHtml(m.text || m.safe_sentence || '')}</span>
      <span class="mono dim">rev ${m.revision ?? '-'}</span>
    </div>`);
}
