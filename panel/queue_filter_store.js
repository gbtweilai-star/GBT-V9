// panel/queue_filter_store.js —— 筛选持久化
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 纪律: 脏数据/过期/版本不符 → 丢弃; 存储异常(隐私模式/配额满)不阻断 UI;
//       恢复的值必须白名单校验(防非法 state/控制字符)

const FILTER_KEY   = "gbt.v9.media.queue-filter.v1";
const REMEMBER_KEY = "gbt.v9.media.queue-filter.remember.v1";
const FILTER_TTL   = 30 * 24 * 60 * 60 * 1000;
const STATES = new Set(["", "queued", "running", "completed", "dead"]);
const EMPTY_FILTER = { project_id: "", stage: "", state: "" };

function normalizeFilter(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const text = v => {
    if (typeof v !== "string" || v.length > 128
        || /[\u0000-\u001f\u007f]/.test(v)) return null;
    return v.trim();
  };
  const project_id = text(value.project_id ?? "");
  const stage      = text(value.stage ?? "");
  const state      = value.state ?? "";
  if (project_id === null || stage === null
      || typeof state !== "string" || !STATES.has(state)) return null;
  return { project_id, stage, state };
}

function rememberEnabled() {
  try { return localStorage.getItem(REMEMBER_KEY) !== "0"; }  // 默认开
  catch { return false; }
}

function loadSavedFilter(now = Date.now()) {
  try {
    const raw = localStorage.getItem(FILTER_KEY);
    if (!raw) return null;
    const rec = JSON.parse(raw);
    const filter = rec?.v === 1 ? normalizeFilter(rec.filter) : null;
    if (!filter || !Number.isFinite(rec.expires_at) || rec.expires_at <= now) {
      localStorage.removeItem(FILTER_KEY);          // 过期/版本不符 → 清
      return null;
    }
    return filter;
  } catch {
    return null;                                    // 隐私模式/坏数据 → 不影响启动
  }
}

function saveFilter(filter, now = Date.now()) {
  if (!rememberEnabled()) return;
  const safe = normalizeFilter(filter);
  if (!safe) return;
  try {
    localStorage.setItem(FILTER_KEY, JSON.stringify({
      v: 1, updated_at: now, expires_at: now + FILTER_TTL, filter: safe,
    }));
  } catch { /* 配额满/不可用：忽略，不阻断 UI */ }
}

function forgetFilter() {
  try { localStorage.removeItem(FILTER_KEY); } catch {}
}
