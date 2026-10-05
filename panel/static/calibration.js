// panel/static/calibration.js
const STATUS = {
  ok:                   ["有效",                 "#35d39a"],
  expiring_soon:        ["即将过期 · 需重标",      "#ffbd59"],
  expired:              ["已过期 · 需重标",        "#ff5d73"],
  insufficient_samples: ["样本不足 · 需重标",      "#ffbd59"],
  policy_mismatch:      ["策略版本不匹配 · 需重标", "#ffbd59"],
  algorithm_stale:      ["算法已更新 · 需重标",    "#ffbd59"],
};
function statusBadge(s) { const [t, c] = STATUS[s] || ["状态未知", "#ffbd59"]; return { t, c }; }
const display = v => (v == null ? "—（未知）" : String(v));          // null ≠ 0

function remainingText(sec) {
  if (sec <= 0) return "已过期";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600);
  return d > 0 ? `剩余 ${d} 天 ${h} 小时` : `剩余 ${h} 小时`;
}
