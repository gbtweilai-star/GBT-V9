// panel/media_monitor.js —— 终态事件抽屉
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 纪律: 游标绑定 from/to/event/order → 改条件必须 reset(清空+cursor=null);
//       reqSeq 丢弃过期响应; event_id 去重; 载入更多用 next_cursor 追加

const drawer = {
  from: 0, to: 0, event: "all", order: "desc",
  items: [], next_cursor: null, has_more: false,
  loading: false, error: "", reqSeq: 0, abort: null,
};

async function loadEvents({ reset = false } = {}) {
  if (drawer.loading && !reset) return;            // 防重复点"加载更多"
  if (reset) {
    drawer.abort?.abort();                          // 取消在飞请求
    drawer.items = []; drawer.next_cursor = null;
    drawer.has_more = false; drawer.error = "";
  }

  const seq = ++drawer.reqSeq;                      // 单调序号 → 丢弃过期
  const cursor = reset ? null : drawer.next_cursor; // reset 后不带旧游标
  const controller = new AbortController();
  drawer.abort = controller;
  drawer.loading = true;
  renderEventDrawer();

  const q = new URLSearchParams({
    from_ts: drawer.from, to_ts: drawer.to,
    event: drawer.event, order: drawer.order, limit: "50",
  });
  if (cursor) q.set("cursor", cursor);

  try {
    const res = await fetch(`/api/media/terminal-events?${q}`,
                            { signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    const data = body.data ?? body;

    if (seq !== drawer.reqSeq) return;              // 过期响应 → 丢
    if (data.event !== drawer.event || data.order !== drawer.order) return;

    const seen = new Set(drawer.items.map(x => x.event_id));
    drawer.items = reset ? data.events
      : [...drawer.items, ...data.events.filter(x => !seen.has(x.event_id))];
    drawer.next_cursor = data.next_cursor;
    drawer.has_more = data.has_more;
  } catch (e) {
    if (e.name !== "AbortError" && seq === drawer.reqSeq)
      drawer.error = "加载失败，请重试";
  } finally {
    if (seq === drawer.reqSeq) {                    // 只有最新请求能收尾
      drawer.loading = false;
      renderEventDrawer();
    }
  }
}

function setEventFilter(event) {
  if (drawer.event === event) return;
  drawer.event = event;
  loadEvents({ reset: true });                       // 条件变 → 游标作废
}
function toggleOrder() {
  drawer.order = drawer.order === "desc" ? "asc" : "desc";
  loadEvents({ reset: true });
}

const FILTERS = [["all", "全部"], ["failed", "失败"], ["completed", "成功"]];

function renderEventDrawer() {
  const root = document.querySelector("#terminal-events");
  if (!root) return;
  const label = Object.fromEntries(FILTERS);

  root.innerHTML = `
    <header class="ev-head">
      <p class="muted">时间窗：${new Date(drawer.from*1000).toLocaleString()}
        — ${new Date(drawer.to*1000).toLocaleString()}</p>
      <div role="group" aria-label="事件筛选">
        ${FILTERS.map(([f, t]) => `<button data-event="${f}"
          aria-pressed="${drawer.event === f}">${t}</button>`).join("")}
      </div>
      <button id="sort-events" aria-label="切换排序">
        时间 ${drawer.order === "desc" ? "最新优先" : "最早优先"}</button>
    </header>

    ${drawer.error ? `<p role="alert">${esc(drawer.error)}
        <button id="retry-events">重试</button></p>` : ""}
    ${drawer.loading && !drawer.items.length ? `<p class="muted">加载中…</p>` : ""}
    ${!drawer.loading && !drawer.error && !drawer.items.length
      ? `<p class="muted">该时间窗无 ${label[drawer.event]} 事件</p>` : ""}

    <ul class="timeline">${drawer.items.map(e => `
      <li>
        <b class="${e.event === "failed" ? "failed" : "completed"}">${esc(e.event)}</b>
        <span>${esc(e.job_id)} · 尝试 #${esc(e.attempt_no)}</span>
        <time class="muted">${new Date(e.ts*1000).toLocaleString()}</time>
      </li>`).join("")}</ul>

    ${drawer.loading && drawer.items.length ? `<p class="muted">加载更多中…</p>` : ""}
    ${drawer.has_more ? `<button id="more-events"
        ${drawer.loading ? "disabled" : ""}>加载更多</button>` : ""}
    ${!drawer.loading && drawer.items.length && !drawer.has_more
      ? `<p class="muted">已到底</p>` : ""}
  `;

  root.querySelectorAll("[data-event]").forEach(b =>
    b.onclick = () => setEventFilter(b.dataset.event));
  root.querySelector("#sort-events")?.addEventListener("click", toggleOrder);
  root.querySelector("#more-events")?.addEventListener("click", () => loadEvents());
  root.querySelector("#retry-events")?.addEventListener("click",
    () => loadEvents({ reset: true }));
}

// 主题进入点：从趋势图桶下钻
window.openTerminalBucket = (ts, bucketSec) => {
  drawer.from = ts; drawer.to = ts + (bucketSec || 300);
  drawer.event = "all"; drawer.order = "desc";
  openDrawer("终态事件明细", `<div id="terminal-events" aria-live="polite"></div>`);
  loadEvents({ reset: true });
};
