// panel/static/body_card.js
const BODY_C = { ok:'#35d39a', warn:'#ffbd59', bad:'#ff5d73',
                 scan:'#39d0ff', change:'#9b7bff', fix:'#35d39a', harden:'#ffbd59' };

function bodyRing(ratio, color, size = 96, width = 10) {           // ← 覆盖率环
  const r = (size - width) / 2, c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(1, ratio ?? 0));
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none"
            stroke="#1b2430" stroke-width="${width}"/>
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="${color}"
            stroke-width="${width}" stroke-linecap="round"
            stroke-dasharray="${c * pct} ${c}" transform="rotate(-90 ${size/2} ${size/2})"/>
    <text x="50%" y="54%" text-anchor="middle" fill="${color}"
          font-size="18" font-family="monospace">${(pct*100).toFixed(1)}%</text></svg>`;
}

async function renderBodyCard(el) {
  const [cov, boot] = await Promise.all([
    fetchJSON('/api/body/coverage'), fetchJSON('/api/boot/self-check')]);
  const h = cov.head, chainOk = boot && boot.healthy;
  const color = chainOk ? BODY_C.ok : BODY_C.bad;
  const dist = [['scan',h.n_scan],['change',h.n_change],['fix',h.n_fix],['harden',h.n_harden]];
  el.innerHTML = `
  <div class="panel-card">
    <header><span class="dot" style="background:${color}"></span>
      <b>全身登记链</b>
      <span class="pill" style="color:${color}">${chainOk ? '健康' : '异常 · 待大脑指令'}</span>
      <span class="pill mono">head#${h.head_seq ?? '-'} · ${(boot?.head_hash||'').slice(0,12)}…</span>
    </header>
    <div class="row">
      ${bodyRing(cov.manifest ? 1 : 0, color)}
      <div class="grid">
        <div>已索引 <b data-drill="coverage">${cov.index.clean}</b></div>
        <div>脏页 <b style="color:${cov.index.dirty?BODY_C.warn:'inherit'}" data-drill="dirty">${cov.index.dirty}</b></div>
        <div>缺失 <b style="color:${cov.index.missing?BODY_C.bad:'inherit'}">${cov.index.missing}</b></div>
        <div>登记总数 <b>${h.total}</b></div>
      </div>
    </div>
    <div class="bars">${dist.map(([k,v]) =>
      `<div class="bar" data-drill="type" data-type="${k}" title="${k}: ${v}">
         <i style="height:${Math.min(48, v*2)}px;background:${BODY_C[k]}"></i><em>${k}</em><b>${v}</b>
       </div>`).join('')}</div>
    <div class="tentacle-rank">${cov.per_tentacle.map((t,i) =>
      `<div class="rank-row" data-drill="tentacle" data-id="${t.tentacle_id}">
         <span>#${i+1}</span><span class="mono">${t.tentacle_id}</span>
         <span class="bar-inline"><i style="width:${
           100 * t.pages / (cov.per_tentacle[0]?.pages || 1)}%"></i></span>
         <b>${t.pages} 页</b></div>`).join('')}</div>
    ${(boot?.problems?.length || boot?.warnings?.length) ? `<div class="alerts">
      ${(boot.problems||[]).map(p => `<span class="bad">${p}</span>`).join('')}
      ${(boot.warnings||[]).map(p => `<span class="warn">${p}</span>`).join('')}</div>` : ''}
    ${(boot?.unresolved||[]).length ? `<div class="unresolved">
      <em>未闭环任务 ${boot.unresolved.length}</em>
      ${boot.unresolved.slice(0,5).map(u => `<div class="mono">${u.trace_id||u.id} · ${u.reason||''}</div>`).join('')}
    </div>` : ''}
  </div>`;

  // 全部下钻走导航栈，统一返回行为
  el.querySelectorAll('[data-drill]').forEach(n => n.addEventListener('click', () => {
    const d = n.dataset.drill;
    if (d === 'tentacle')  navPush({kind:'body_tentacle', id:n.dataset.id});
    else if (d === 'type') navPush({kind:'body_chain', types:n.dataset.type});
    else                   navPush({kind:'body_chain', types:null});
  }));
}

async function renderChainDrawer(nav) {                     // 事件列表 → 条目详情 → 责任页
  const qs = nav.types ? `?types=${nav.types}` : '';
  const page = await fetchJSON(`/api/body/chain${qs}`);
  panelDrawer(`登记链 · ${nav.types || '全部'}`, html`
    ${page.items.map(r => `<div class="chain-row" data-seq="${r.seq}">
        <span class="mono">#${r.seq}</span>
        <span class="pill" style="color:${BODY_C[r.event_type]}">${r.event_type}</span>
        <span class="mono">${r.actor_tentacle || '-'}</span>
        <span>${r.targets} 页</span><span class="mono">${r.event_hash.slice(0,10)}</span>
        <span class="mono dim">prev ${r.prev_hash.slice(0,10)}</span></div>`).join('')}
    ${page.has_more ? `<button id="more" data-cursor="${page.next_cursor}">加载更多</button>` : ''}`);
  drawerOn('click', '.chain-row', e => openChainEntry(e.currentTarget.dataset.seq));
  drawerOn('click', '#more', e => appendChainPage(e.currentTarget.dataset.cursor, nav.types));
}

async function openChainEntry(seq) {                        // 条目 → 责任页 before/after
  const d = await fetchJSON(`/api/body/chain/${seq}`);
  pushNav({kind:'body_entry', seq});
  panelDrawer(`登记条目 #${seq}` + (d.verify.event_hash_match ? ' ✅' : ' ❌哈希不符'), html`
    <pre class="mono">${JSON.stringify(JSON.parse(d.registration.payload_json || '{}'), null, 2)}</pre>
    <table>${d.targets.map(t => `<tr>
      <td class="mono">${t.path}</td>
      <td class="mono dim">${(t.before_hash||'-').slice(0,10)}</td>
      <td class="mono">${(t.after_hash||'-').slice(0,10)}</td></tr>`).join('')}</table>`);
}

async function openTentaclePages(nav) {                     // 触手 → 绑定页 + 页搜索
  let cursor = null, q = '';
  async function load(reset) {
    const u = `/api/body/tentacle/${encodeURIComponent(nav.id)}/pages?limit=200`
            + (q ? `&q=${encodeURIComponent(q)}` : '') + (cursor && !reset ? `&cursor=${cursor}` : '');
    const d = await fetchJSON(u);
    if (reset) cursor = null;
    cursor = d.next_cursor;
    return d;
  }
  const first = await load(true);
  panelDrawer(`触手 ${nav.id} · 绑定页`, html`
    <input id="q" placeholder="搜索路径…" value="${q}">
    <div id="pages">${first.items.map(p =>
      `<div class="page-row mono">${p.path} <em class="dim">${p.rule_id}</em></div>`).join('')}</div>`);
  let t; drawerOn('input', '#q', e => { clearTimeout(t);
    t = setTimeout(async () => { q = e.target.value; const d = await load(true);
      document.querySelector('#pages').innerHTML = d.items.map(p =>
        `<div class="page-row mono">${p.path} <em>${p.rule_id}</em></div>`).join(''); }, 250); });
}

navRegistry['body_chain']    = renderChainDrawer;
navRegistry['body_entry']    = n => openChainEntry(n.seq);
navRegistry['body_tentacle'] = openTentaclePages;
