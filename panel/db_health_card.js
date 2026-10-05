// panel/db_health_card.js
const LEVEL_TEXT = { ok: "正常", warn: "注意", crit: "异常" };

async function renderDbHealthCard(rootSel = "#db-health-card") {
  const root = document.querySelector(rootSel);
  if (!root) return;
  let d;
  try {
    const r = await fetch("/api/monitor/db-health");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    d = (await r.json()).data;
  } catch {
    root.dataset.level = "unknown";
    root.textContent = "数据库健康度：无法获取（接口失败）";   // ★文字，不只靠颜色
    return;
  }

  const p = d.pool || {}, m = d.metrics || {};
  const ratio = p.pool?.used_ratio;
  const parts = [
    `级别：${LEVEL_TEXT[d.level] || d.level}`,
    `后端：${p.dialect || "—"}`,
    p.dialect === "postgres"
      ? `池占用：${ratio == null ? "—" : (ratio * 100).toFixed(0) + "%"}（${p.pool.used}/${p.pool.max}）`
      : `SQLite：busy/locked ${m.busy_locked ?? 0} 次`,
    `等待连接：${p.waiting ?? (p.dialect === "sqlite" ? "不适用" : "未知")}`,
    `锁等待：${p.lock_state === "unknown" ? "未知"
      : p.lock_state === "not_applicable" ? "不适用" : (p.lock_waits ?? "—")}`,
    `慢查询：${m.slow_total ?? 0} 条，p95 ≤ ${m.p95_ms == null ? "—" : m.p95_ms + "ms"}`,
    `采集范围：${m.scope || "per-worker"}（${m.worker_id || "—"}）`,
  ];

  root.dataset.level = d.level;
  root.replaceChildren();
  root.append(makeEl("h4", `数据库健康度`));
  const ul = makeEl("ul", []);
  parts.forEach(t => ul.append(makeEl("li", t)));
  root.append(ul);

  if (d.reasons?.length)
    root.append(makeEl("p", { class: "alert-text", text: "告警：" + d.reasons.join("；") }));

  const btn = makeEl("button", { type: "button", text: "查看慢查询明细" });
  btn.addEventListener("click", () => pushLayer("dbslow", { data: m }));
  root.append(btn);
}

registerLayer("dbslow", p => ({
  type: "dbslow", keyParts: { scope: "panel-db" }, title: "慢查询明细",
  state: { data: p.data }, onResume: () => refreshDbSlow(this),
  render() {
    const d = this.state.data || {};
    const box = document.createElement("div");
    box.id = "drawer-body";
    box.append(makeEl("h4", `Top 慢 SQL 指纹（p95 ≤ ${d.p95_ms ?? "—"}ms）`));
    for (const t of d.top_slow_sql || [])
      box.append(makeEl("p", { text: `${t.count}× · 最慢 ${t.max_ms}ms · ${t.sql}` }));
    box.append(makeEl("h4", "最近最慢"));
    for (const r of (d.recent_slow || []).slice(0, 10))
      box.append(makeEl("p", { text: `${r.ms}ms · ${r.route} · ${r.shape}` }));
    root.replaceChildren(box);
  },
}));
