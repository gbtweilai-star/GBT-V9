// panel/layers.js —— 统一抽屉层（工厂 + 注册表）
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 纪律: 同类型不同参数可共存(queue:不同筛选), 完全相同层不重复压栈;
//       列表层返回复用缓存(不重拉); job 层 onResume 刷新

const LAYERS = Object.create(null);
const nav = { stack: [], maxDepth: 8 };

function registerLayer(type, factory) { LAYERS[type] = factory; }

function makeLayer(type, params = {}) {
  const f = LAYERS[type];
  if (!f) throw new Error(`未知抽屉类型: ${type}`);
  return f(params);
}

// 规范化参数 → 区分同类实例、拦住完全重复层
function routeKey(layer) {
  return `${layer.type}:${JSON.stringify(layer.keyParts ?? {})}`;
}

function saveTopScroll() {
  const cur = nav.stack.at(-1);
  if (cur) cur.scrollTop = document.querySelector("#drawer-body")?.scrollTop || 0;
}

function pushLayer(type, params) {
  const layer = makeLayer(type, params);
  const key = routeKey(layer);
  const at = nav.stack.findIndex(x => routeKey(x) === key);

  if (at >= 0) {                          // 已存在 → 逐层退回，不重复压栈
    const n = nav.stack.length - 1 - at;  // 需要弹掉的层数
    if (n === 0) return;                  // 就是当前层
    history.go(-n);                       // popstate 逐个 pop 并恢复
    return;
  }
  if (nav.stack.length >= nav.maxDepth) throw new Error("抽屉层级已达上限");
  saveTopScroll();
  nav.stack.push(layer);
  history.pushState({ drawerDepth: nav.stack.length }, "");
  renderTop();
}
