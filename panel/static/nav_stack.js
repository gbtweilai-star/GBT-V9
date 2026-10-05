// panel/static/nav_stack.js（扩展）
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 铁律: 入口 key 稳定(用于去重/回退); 校验/校准状态只信服务端; 深链只放 UI 状态;
//       过期/删除/不匹配就地在当前视图诚实更新, 不重置游标/滚动, 不保留假绿。
const entryKey = e => ({
  calibration_list: `cal-list:${e.state.projectId}`,
  calibration:      `cal:${e.state.projectId}:${e.state.op_kind}:${e.state.algorithm_version}:${e.state.encode_fingerprint}`,
  evidence_list:    `ev-list:${e.state.projectId}:${stableJson(e.state.filters)}`,
  evidence:         `ev:${e.state.evidenceId}`,
  compare:          `compare:${e.state.evidenceId}`,
}[e.type]);

function open(entry) {
  snapshotActive();
  const i = nav.stack.findIndex(x => entryKey(x) === entryKey(entry));
  if (i >= 0) return history.go(i - nav.stack.length + 1);   // 已存在 → 回退, 不重复入栈
  nav.stack.push(entry);
  history.pushState({ v: 1, stack: nav.stack }, "", encodeHash(nav.stack));
  renderTop();
}

// 方向①: 校准 → 它的证据列表(按校准键列过滤) → 证据 → 对比
function evidenceForCalibration(c) {
  open({ type: "evidence_list", state: {
    projectId: c.projectId, cursor: null, scrollTop: 0,
    filters: { op_kind: c.op_kind, algorithm_version: c.algorithm_version,
               policy_version: c.policy_version, encode_fingerprint: c.encode_fingerprint } }});
}

// 方向②: 证据 → 产出它的那个校准(反查, 命中栈内则回退)
function calibrationForEvidence(e) {
  const k = e.calibration_key;                    // 落库的 compare_spec 版本列
  const target = { type: "calibration",
                   state: { projectId: e.projectId, ...k } };
  const i = nav.stack.findIndex(x => entryKey(x) === entryKey(target));
  if (i >= 0) return history.go(i - nav.stack.length + 1);
  open(target);
}

function breadcrumb() {
  const label = { calibration_list: "校准", calibration: "校准详情",
                  evidence_list: "证据", evidence: "证据详情", compare: "对比" };
  return nav.stack.map((e, i) =>
    `<a data-idx="${i}" class="${i===nav.stack.length-1?"cur":""}">${label[e.type]}</a>`
  ).join(' <span class="sep">›</span> ');     // 校准 › 证据 › 对比
}
// Back: 走浏览器历史; renderTop → restoreState 还原每层游标/滚动/模式

// #v1=<base64url(JSON)>; 限 6 层、≤4KB; 只允许已知 type 与筛选字段; 不含 URL/凭据/objectURL
function encodeHash(stack) {
  const slim = stack.map(e => ({ t: e.type, s: pickState(e.type, e.state) }));
  return "#v1=" + b64url(JSON.stringify({ v: 1, stack: slim }));
}
function parseHash(h) {                       // 校验失败 → 回列表 + 琥珀提示
  const m = /^#v1=(.+)$/.exec(h); if (!m) return null;
  try { const o = JSON.parse(b64urlDecode(m[1])); return validateStack(o) ? o.stack : null; }
  catch { return null; }
}
// 恢复时: 对每个条目【重新拉取】当前记录与状态, 不用哈希里的旧状态当现状

function onCalibrationRefresh(view, row) {
  // 服务端 status 说了算: 过期/算法变 → 琥珀"需重标"; 删除 → "校准已删除"; 键不匹配 → policy_mismatch
  view.badge = badgeFor(row.status);
  if (row.status !== "ok") view.recalibrateBtn.hidden = false;
  // ★ 不动 view.state.cursor / scrollTop / compare 的 mode/opacity/transform/wipe
  // ★ 绝不保留旧的绿色 ok
}
function badgeFor(status) {
  return ({ ok:["有效","#35d39a"], expiring_soon:["即将过期 · 需重标","#ffbd59"],
            expired:["已过期 · 需重标","#ff5d73"], policy_mismatch:["策略版本不匹配 · 需重标","#ffbd59"],
            algorithm_stale:["算法已更新 · 需重标","#ffbd59"],
            calibration_required:["未找到校准 · 需重标","#ffbd59"] }[status]
          || ["状态未知","#ffbd59"]);
}
