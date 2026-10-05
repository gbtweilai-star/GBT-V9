// panel/nav_stack.js —— 抽屉层级导航栈（push/pop/循环检测）
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 纪律: 跳走前保存当前层 scrollTop; 返回用已存 state 恢复(不重拉);
//       栈深上限 + routeKey 循环检测; 浏览器后退走 popstate 统一恢复

const nav = { stack: [], maxDepth: 8 };
const routeKey = x => `${x.type}:${x.jobId || ""}`;

function pushLayer(layer) {
  if (nav.stack.length >= nav.maxDepth)
    throw new Error("抽屉层级已达上限");
  if (nav.stack.some(x => routeKey(x) === routeKey(layer)))
    throw new Error("该页面已在导航栈中");         // 防 A→B→A 死循环

  const cur = nav.stack.at(-1);
  if (cur) cur.scrollTop = document.querySelector("#drawer-body")?.scrollTop || 0;

  nav.stack.push(layer);
  history.pushState({ ...(history.state || {}), drawerDepth: nav.stack.length }, "");
  renderTop();
}

function backLayer() {
  if (nav.stack.length <= 1) return closeDrawer();  // 根层 → 关闭
  history.back();                                   // 交给 popstate 统一恢复
}

window.addEventListener("popstate", () => {
  if (nav.stack.length > 1) {
    nav.stack.pop();
    renderTop();                                    // 用已存 items/cursor，不重拉
  }
});

function renderTop() {
  const layer = nav.stack.at(-1);
  if (!layer) return closeDrawer();
  openDrawer(layer.title, `
    <button class="drawer-back" aria-label="返回上一层">← 返回</button>
    <nav aria-label="抽屉导航">${nav.stack.map(x => esc(x.title)).join(" › ")}</nav>
    <div id="drawer-body">${layer.html || ""}</div>`);
  document.querySelector(".drawer-back")?.addEventListener("click", backLayer);
  layer.render?.();
  document.querySelector("#drawer-body").scrollTop = layer.scrollTop || 0;
}
