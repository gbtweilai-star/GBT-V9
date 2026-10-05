/* 原生覆盖率面板。需要 #coverage-card 与 window.navStack。 */
(() => {
  "use strict";

  const api = "/api/coverage";
  const mount = document.getElementById("coverage-card");
  const nav = window.navStack;
  if (!mount || !nav) return;

  const state = {
    view: "coverage-list", backend: "", label: "",
    commit_sha: "", snapshot_id: "", module: "",
  };

  const el = (tag, cls = "", text = "") => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== "") node.textContent = text;
    return node;
  };
  const colorFor = (pct, t) => pct >= t ? "#35d39a" : pct >= t - 10 ? "#ffbd59" : "#ff5d73";
  const boolPassed = v => v === true || v === 1 || v === "1";

  function encodeState(value) {
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    let binary = ""; bytes.forEach(b => { binary += String.fromCharCode(b); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function decodeState(value) {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/")
      .padEnd(Math.ceil(value.length / 4) * 4, "=");
    const binary = atob(padded);
    return JSON.parse(new TextDecoder().decode(
      Uint8Array.from(binary, c => c.charCodeAt(0))));
  }
  function setLink(next) { Object.assign(state, next); location.hash = `coverage=${encodeState(state)}`; }
  function readLink() {
    const m = location.hash.match(/^#coverage=(.+)$/);
    if (!m) return null;
    try { return decodeState(m[1]); } catch (_) { return null; }
  }
  function push(next) { const t = { ...state, ...next }; nav.pushView(t); setLink(t); }

  function bar(label, percent, threshold, onClick = null) {
    const wrap = el("div", "cov-module");
    const head = el("div", "cov-module-head");
    head.append(el("span", "", label), el("span", "", `${percent.toFixed(1)}%`));
    const track = el("div", "cov-track");
    const fill = el("div", "cov-fill");
    fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    fill.style.background = colorFor(percent, threshold);
    track.appendChild(fill);
    wrap.append(head, track);
    if (onClick) { wrap.tabIndex = 0; wrap.addEventListener("click", onClick); }
    return wrap;
  }

  function addRunLink(parent, url, text = "查看运行") {
    if (!url) return;
    const link = el("a", "cov-run-link", text);
    link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    parent.appendChild(link);
  }

  function sparkline(items) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 240 52");
    svg.setAttribute("class", "cov-sparkline");
    if (!items.length) return svg;
    const values = items.map(i => Number(i.overall_percent));
    const points = values.map((v, i) => {
      const x = values.length === 1 ? 120 : 4 + i * 232 / (values.length - 1);
      const y = 48 - v * 0.44;
      return `${x},${y}`;
    }).join(" ");
    const line = document.createElementNS(svg.namespaceURI, "polyline");
    line.setAttribute("points", points);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#39d0ff");
    line.setAttribute("stroke-width", "2");
    svg.appendChild(line);
    return svg;
  }

  function filtersBar(facets) {
    const box = el("div", "cov-filters");

    const backend = el("select", "cov-select");
    backend.setAttribute("aria-label", "Backend");
    backend.appendChild(new Option("全部后端", ""));
    for (const name of facets.backends || []) backend.appendChild(new Option(name, name));
    backend.value = state.backend || "";

    const label = el("select", "cov-select");
    label.setAttribute("aria-label", "Label");
    label.appendChild(new Option("全部标签", ""));
    for (const name of facets.labels || []) label.appendChild(new Option(name, name));
    label.value = state.label || "";
    if (!facets.label_available) label.disabled = true;   // label 列不存在时禁用

    const apply = () => push({
      view: "coverage-list", backend: backend.value, label: label.value,
      commit_sha: "", snapshot_id: "", module: "",
    });
    backend.addEventListener("change", apply);
    label.addEventListener("change", apply);

    box.append(el("label", "", "Backend "), backend);
    box.append(el("label", "", "Label "), label);
    return box;
  }

  const loadFacets = () => fetch(`${api}/facets?limit=30`)
    .then(r => { if (!r.ok) throw new Error(`筛选项加载失败 (${r.status})`); return r.json(); });

  async function renderSnapshot(snapshot, parent) {
    const threshold = Number(snapshot.threshold ?? 80);
    const percent = Number(snapshot.overall_percent || 0);
    const card = el("section", "cov-backend");
    card.append(
      el("h3", "", `${snapshot.backend}${snapshot.label ? ` · ${snapshot.label}` : ""}`),
      el("span", boolPassed(snapshot.passed) ? "cov-pass" : "cov-fail",
         boolPassed(snapshot.passed) ? "PASS" : "FAIL"));

    addRunLink(card, snapshot.run_url,
      snapshot.commit_sha ? `提交 ${snapshot.commit_sha.slice(0, 10)} · 查看运行` : "查看运行");

    if (snapshot.commit_sha) {
      const b = el("button", "cov-button", "查看该提交");
      b.addEventListener("click", () => push({
        view: "coverage-commit", commit_sha: snapshot.commit_sha,
        snapshot_id: "", module: "",
      }));
      card.appendChild(b);
    }

    card.appendChild(el("strong", "cov-percent", `${percent.toFixed(1)}%`));
    card.appendChild(bar("overall", percent, threshold));

    let modules = snapshot.modules;
    if (typeof modules === "string") {
      try { modules = JSON.parse(modules); } catch (_) { modules = []; }
    }
    for (const item of modules || []) {
      card.appendChild(bar(item.module, Number(item.percent || 0), threshold, () =>
        push({ view: "coverage-module", snapshot_id: snapshot.snapshot_id, module: item.module })));
    }

    const query = new URLSearchParams();
    if (state.backend) query.set("backend", state.backend);
    if (state.label) query.set("label", state.label);
    query.set("limit", "60");
    const series = await fetch(`${api}/series?${query}`).then(r => r.json());
    card.appendChild(sparkline(series.items || []));
    parent.appendChild(card);
  }

  async function renderList(facets) {
    const q = new URLSearchParams({ limit: "30" });
    if (state.backend) q.set("backend", state.backend);
    if (state.label) q.set("label", state.label);

    const [overview, commits] = await Promise.all([
      fetch(`${api}?${q}`).then(r => r.json()),
      fetch(`${api}/commits?limit=50`).then(r => r.json()),
    ]);

    mount.replaceChildren();
    mount.appendChild(el("h2", "", "契约覆盖率"));
    mount.appendChild(filtersBar(facets));

    if (overview.table_missing) {
      mount.appendChild(el("p", "cov-muted", "暂无覆盖率快照。"));
      return;
    }

    mount.appendChild(el("h3", "", "最新快照"));
    for (const snapshot of overview.items || []) await renderSnapshot(snapshot, mount);

    mount.appendChild(el("h3", "", "提交列表"));
    if (commits.table_missing) {
      mount.appendChild(el("p", "cov-muted", "覆盖率表尚未迁移。"));
      return;
    }
    for (const commit of commits.items || []) {
      const row = el("article", "cov-commit-row");
      const pct = Number(commit.min_overall_percent || 0);
      row.append(
        el("strong", "", commit.commit_sha),
        el("span", commit.all_passed ? "cov-pass" : "cov-fail",
           commit.all_passed ? "PASS" : "FAIL"),
        el("span", "", `${pct.toFixed(1)}% · ${(commit.backends || []).join(", ")}`));
      addRunLink(row, commit.run_url);
      const open = el("button", "cov-button", "下钻");
      open.addEventListener("click", () => push({
        view: "coverage-commit", commit_sha: commit.commit_sha,
        snapshot_id: "", module: "",
      }));
      row.appendChild(open);
      mount.appendChild(row);
    }
  }

  async function renderCommit() {
    const data = await fetch(`${api}/commit/${encodeURIComponent(state.commit_sha)}`)
      .then(r => r.json());
    mount.replaceChildren();

    const back = el("button", "cov-button", "返回");
    back.addEventListener("click", goBack);
    mount.append(back, el("h2", "", `提交 ${state.commit_sha}`));

    if (data.table_missing) {
      mount.appendChild(el("p", "cov-muted", "覆盖率表尚未迁移。"));
      return;
    }

    const rollup = data.rollup || {};
    mount.appendChild(el("span", rollup.all_passed ? "cov-pass" : "cov-fail",
      `${rollup.all_passed ? "PASS" : "FAIL"} · 最低 ${Number(rollup.min_overall_percent || 0).toFixed(1)}%`));
    addRunLink(mount, rollup.run_url);

    for (const snapshot of data.items || []) await renderSnapshot(snapshot, mount);
  }

  async function renderModule() {
    const data = await fetch(`${api}/${encodeURIComponent(state.snapshot_id)}`)
      .then(r => r.json());
    mount.replaceChildren();

    const back = el("button", "cov-button", "返回");
    back.addEventListener("click", goBack);
    mount.append(back, el("h2", "", state.module));

    if (data.table_missing) {
      mount.appendChild(el("p", "cov-muted", "覆盖率表尚未迁移。"));
      return;
    }

    const module = (Array.isArray(data.modules) ? data.modules : [])
      .find(item => item.module === state.module);
    if (!module) {
      mount.appendChild(el("p", "cov-muted", "模块详情不存在。"));
      return;
    }
    mount.appendChild(bar(module.module, Number(module.percent || 0), Number(data.threshold || 80)));
    addRunLink(mount, data.run_url, `提交 ${data.commit_sha || ""} · 查看运行`);

    for (const file of module.files || []) {
      mount.appendChild(bar(file.path, Number(file.percent || 0), Number(data.threshold || 80)));
      if (Array.isArray(file.missing_lines) && file.missing_lines.length) {
        mount.appendChild(el("pre", "cov-missing", `未覆盖行: ${file.missing_lines.join(", ")}`));
      }
    }
  }

  async function render(next = state) {
    Object.assign(state, next);
    nav.restoreState({ ...state });
    const facets = await loadFacets();
    if (state.view === "coverage-commit") return renderCommit();
    if (state.view === "coverage-module") return renderModule();
    return renderList(facets);
  }

  async function goBack() {
    const previous = nav.popView() || {
      view: "coverage-list", backend: state.backend, label: state.label,
      commit_sha: "", snapshot_id: "", module: "",
    };
    nav.restoreState(previous); setLink(previous);
    await render(previous);
  }

  function showError(error) {
    mount.replaceChildren(el("p", "cov-fail", `覆盖率加载失败：${error.message}`));
  }

  const style = el("style");
  style.textContent = `
    .cov-backend,.cov-commit-row{background:#101827;color:#d9f7ff;
      border:1px solid #24506a;border-radius:10px;padding:12px;margin:8px 0}
    .cov-module{margin:8px 0;cursor:pointer}.cov-module-head{display:flex;
      justify-content:space-between;font-size:12px}.cov-track{height:7px;
      background:#263447;border-radius:5px;overflow:hidden}.cov-fill{height:100%}
    .cov-pass{color:#35d39a}.cov-fail,.cov-missing{color:#ff5d73}
    .cov-percent{font-size:26px}.cov-sparkline{width:100%;height:52px}
    .cov-muted{color:#9aaec1}.cov-button,.cov-select{background:#12283c;
      color:#39d0ff;border:1px solid #39d0ff;padding:7px 10px;border-radius:6px}
    .cov-filters{display:flex;gap:8px;align-items:center;margin:10px 0}
    .cov-run-link{color:#39d0ff;margin:0 8px}`;
  document.head.appendChild(style);

  window.addEventListener("hashchange", () => {
    const restored = readLink();
    if (restored) render(restored).catch(showError);
  });

  const initial = readLink() || state;
  nav.restoreState(initial);
  render(initial).catch(showError);
})();
