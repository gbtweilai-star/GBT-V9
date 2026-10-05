// panel/layers_scrape.js —— 抓取列表 / 记录详情 / 选择器命中详情
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 纪律: 抓取内容是第三方不可信输入 → 只用 textContent, 永不 innerHTML;
//       低命中/策略拦截用"文字+数值"表达, 不靠颜色单独区分;
//       筛选就地改不 push, 改筛选 → replaceState + load(true)

// ────────────────────────── 工具 ──────────────────────────
const normDomain = s => String(s ?? "").trim().toLowerCase().replace(/\.$/, "");
const TIERS = ["", "http", "browser", "stealth"];
const MATCHED = ["", "1", "0"];
const PREVIEW_MAX = 2000;

/** ★唯一文本入口：任何不可信文本都走 textContent */
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, String(v));
  }
  for (const kid of kids) if (kid) n.append(kid);
  return n;
}

function fmtScore(v) {
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 && n <= 1 ? n.toFixed(2) : "—";
}

/** 低命中/未命中：文字 + 数值 + 处置建议，色只作辅助 */
function matchBadge(row) {
  const score = Number(row.match_score);
  if (!Number.isFinite(score)) return el("span", { class: "badge", text: "无自适应记录" });
  if (!row.matched)  return el("span", { class: "badge badge-warn",
      text: `未命中 · 需回退选择器` });
  if (score < 0.6)   return el("span", { class: "badge badge-warn",
      text: `低置信度 · ${fmtScore(score)} · 建议人工确认` });
  return el("span", { class: "badge", text: `命中 · ${fmtScore(score)}` });
}

function policyBadge(row) {
  if (row.status !== "blocked_by_policy") return null;
  return el("span", { class: "badge badge-crit",
    text: `策略拦截 · ${row.policy_code || "unknown"}` });   // ★文字+代码
}

function normalizeScrapeFilter(f = {}) {
  const txt = v => (typeof v === "string" && v.length <= 128 &&
                    !/[\u0000-\u001f\u007f]/.test(v)) ? v.trim() : "";
  const tier = TIERS.includes(f.tier) ? f.tier : "";
  const matched = MATCHED.includes(String(f.matched ?? "")) ? String(f.matched ?? "") : "";
  return { host: txt(f.host), tier, matched,
           from_ts: Number(f.from_ts) || 0, to_ts: Number(f.to_ts) || 0 };
}

// ────────────────────────── ① 抓取列表层 ──────────────────────────
registerLayer("scrape", p => {
  const layer = {
    type: "scrape",
    keyParts: { scope: p.scope || "monitor" },     // ★只放稳定维度
    title: "网页抓取",
    state: { filter: normalizeScrapeFilter(p.filter), composing: false,
             debounceTimer: null, scrollTop: 0, lc: null },
    render: () => renderScrapeList(layer),
  };
  layer.state.lc = new ListController({
    url: "/api/web/scrapes",
    query: () => ({ ...layer.state.filter, limit: 50 }),
    extract: d => d.items ?? [],
  });
  return layer;
});

function updateScrapeFilter(layer, patch, immediate = false) {
  const s = layer.state;
  Object.assign(s.filter, patch);
  clearTimeout(s.debounceTimer);
  const run = () => { s.lc.load(true); syncScrapeHash(s.filter); };
  if (immediate) run(); else s.debounceTimer = setTimeout(run, 300);
}

/** 深链同步用 replaceState（改筛选不制造历史层） */
function syncScrapeHash(f) {
  const q = new URLSearchParams();
  for (const k of ["host", "tier", "matched"]) if (f[k]) q.set(k, f[k]);
  if (f.from_ts) q.set("from_ts", f.from_ts);
  if (f.to_ts)   q.set("to_ts", f.to_ts);
  history.replaceState({ ...history.state }, "",
    q.toString() ? `#scrape?${q}` : "#scrape");
}

function renderScrapeList(layer) {
  const host = el("div", { id: "drawer-body" });
  const f = layer.state.filter;

  const hostInput = el("input", { name: "host", maxlength: "128",
    placeholder: "域名 host", "aria-label": "域名", value: f.host });
  const tierSel = el("select", { name: "tier", "aria-label": "抓取档位" },
    ...TIERS.map(t => el("option", { value: t, text: t || "全部档位" })));
  tierSel.value = f.tier;
  const matchedSel = el("select", { name: "matched", "aria-label": "自适应命中" },
    ...MATCHED.map(m => el("option", { value: m,
      text: m === "" ? "全部" : m === "1" ? "命中" : "未命中" })));
  matchedSel.value = f.matched;
  const clearBtn = el("button", { type: "button", "data-clear": "1", text: "清空筛选" });

  const group = el("div", { class: "scrape-filters", role: "group",
    "aria-label": "筛选抓取记录" }, hostInput, tierSel, matchedSel, clearBtn);

  const results = el("div", { "data-results": "1", "aria-live": "polite" });
  host.append(group, results);

  // 文本：防抖 + IME
  hostInput.addEventListener("compositionstart", () => { layer.state.composing = true; });
  hostInput.addEventListener("compositionend", () => {
    layer.state.composing = false;
    updateScrapeFilter(layer, { host: hostInput.value });
  });
  hostInput.addEventListener("input", () => {
    if (!layer.state.composing) updateScrapeFilter(layer, { host: hostInput.value });
  });
  tierSel.addEventListener("change", () =>
    updateScrapeFilter(layer, { tier: tierSel.value }, true));
  matchedSel.addEventListener("change", () =>
    updateScrapeFilter(layer, { matched: matchedSel.value }, true));
  clearBtn.addEventListener("click", () => {
    hostInput.value = ""; tierSel.value = ""; matchedSel.value = "";
    updateScrapeFilter(layer, normalizeScrapeFilter({}), true);
  });

  // ★DOM 构建全部走 el()（textContent），绝无 innerHTML
  const lc = layer.state.lc;
  if (lc.loading && !lc.items.length) results.append(el("p", { class: "muted", text: "加载中…" }));
  else if (lc.error) results.append(el("p", { role: "alert", text: lc.error }));
  else if (!lc.items.length) results.append(el("p", { class: "muted",
    text: f.host || f.tier || f.matched ? "当前筛选无抓取记录" : "暂无抓取记录" }));
  else {
    const ul = el("ul", { class: "scrape-rows" });
    for (const r of lc.items) {
      const row = el("li", { class: "row", tabindex: "0", role: "button",
                             "data-run": String(r.run_id) });
      row.append(
        el("b", { text: normDomain(r.host) || "(空)" }),
        el("span", { class: "muted", text: ` · ${r.tier} · ${r.tentacle_id || "—"}` }),
        el("span", { class: "muted", text: ` · 结果 ${r.result_count} 条` }),
        matchBadge(r) || el("span"),
        policyBadge(r) || el("span"),
        el("time", { class: "muted",
          text: new Date((r.created_at_ts || 0) * 1000).toLocaleString() }));
      row.addEventListener("click", () => pushLayer("scrapeRun", { run_id: r.run_id }));
      row.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault();
          pushLayer("scrapeRun", { run_id: r.run_id }); }
      });
      ul.append(row);
    }
    results.append(ul);

    if (lc.hasMore)
      results.append(el("button", { type: "button", text: "加载更多",
        onclick: () => lc.load(false) }));            // 追加，event_id/run_id 去重由 LC 负责
    else results.append(el("p", { class: "muted", text: "已到底" }));
  }

  root.innerHTML = "";
  root.append(host);
  queueMicrotask(() => { host.scrollTop = layer.state.scrollTop || 0; });
}
