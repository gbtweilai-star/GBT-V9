// panel/public_mode.js —— 公共设备模式（登出清除本地筛选记忆）
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 纪律: 登出后拿不到 user_id → 必须在登出前缓存派生键;
//       "确认登出" = 登出请求成功 + 会话接口确认匿名(网络错误不算);
//       只清该用户的 filter/remember 键, 不动其他数据

const PUBLIC_MODE_KEY = "gbt.v9.device.public-mode.v1";

let authContext = { userId: null, epoch: 0, filterKey: null, rememberKey: null };

function publicModeEnabled() {
  try { return localStorage.getItem(PUBLIC_MODE_KEY) === "1"; }
  catch { return false; }
}

function setPublicMode(enabled) {
  try { localStorage.setItem(PUBLIC_MODE_KEY, enabled ? "1" : "0"); }
  catch { /* 存储不可用不阻断 UI */ }
}
