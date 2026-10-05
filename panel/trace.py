# panel/trace.py —— 统一 trace：生成 / 绑定 / 回填 / 建列
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 原则: 新任务由主脑生成 trace_id 向下传递; 旧数据按 target+tentacle+时间窗回填,
#       匹配不唯一 → 标 unlinked, 绝不猜。
import os, time, uuid, contextvars
from senses.sqldialect import txn

_trace = contextvars.ContextVar("trace_id", default=None)

# 参与串联的表 → 时间列
TRACE_TABLES = {
    "ledger":            "ts",
    "skill_calls":       "ts",
    "cross_tasks":       "updated_at",
    "cross_arbitration": "ts",
    "action_log":        "ts",
    "voice_jobs":        "ts",
}
# 吞噬能表名多版本：探测实际存在哪个
DEVOUR_SEG_CANDIDATES = ("frame_segments", "devour_segments", "segments")
DEVOUR_GAP_CANDIDATES = ("frame_gaps", "devour_gaps", "gaps")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def bind_trace(trace_id=None):
    """上下文管理器：本作用域内所有写入自动带 trace_id"""
    class _Ctx:
        def __enter__(self):
            self.tok = _trace.set(trace_id or new_trace_id())
            return _trace.get()
        def __exit__(self, *a):
            _trace.reset(self.tok)
    return _Ctx()


def current_trace():
    return _trace.get()


# ── 幂等迁移：给表补 trace_id 列 + 索引 ──
def ensure_trace_columns(led):
    d = led.dialect
    created = []
    with txn(led) as cur:
        for table, tcol in TRACE_TABLES.items():
            if not _table_exists(cur, d, table):
                continue
            if "trace_id" not in _cols(cur, d, table):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN trace_id TEXT")
                created.append(f"{table}.trace_id")
            # 并联索引（PG 额外把时间列纳入）
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_trace "
                        f"ON {table}(trace_id)")
    return created


def _table_exists(cur, d, table):
    if d == "sqlite":
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    else:
        cur.execute("SELECT 1 FROM information_schema.tables "
                    "WHERE table_name=%s AND table_schema='public'", (table,))
    return cur.fetchone() is not None


def _cols(cur, d, table):
    if d == "sqlite":
        cur.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in cur.fetchall()]
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s", (table,))
    return [r[0] for r in cur.fetchall()]


def detect(led, candidates):
    """从候选表名里挑实际存在的那个；都没有返回 None"""
    d = led.dialect
    with txn(led) as cur:
        for t in candidates:
            if _table_exists(cur, d, t):
                return t
    return None
