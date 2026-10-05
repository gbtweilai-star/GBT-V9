# audit/ddl.py —— 方言无关 DDL 片段（SQLite / PostgreSQL 共用）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 核心约定: 时间统一存 epoch 秒（浮点），两种后端都用 REAL/DOUBLE PRECISION
#   好处: 不碰 PG 的 TIMESTAMPTZ / now() / to_timestamp，也不碰 SQLite 缺的函数

def pk(d):        return "INTEGER PRIMARY KEY AUTOINCREMENT" if d == "sqlite" \
                     else "BIGSERIAL PRIMARY KEY"
def epoch(d):     return "REAL" if d == "sqlite" else "DOUBLE PRECISION"
def bool_t(d):    return "INTEGER" if d == "sqlite" else "BOOLEAN"
def bool_v(d, v): return ("1" if v else "0") if d == "sqlite" else \
                     ("true" if v else "false")
def now_expr(d):  return "strftime('%s','now')" if d == "sqlite" else "EXTRACT(EPOCH FROM now())"

def executescript(cur, ddl, dialect):
    """两种后端统一建表入口"""
    if dialect == "sqlite":
        cur.executescript(ddl)
    else:
        cur.execute(ddl)


def run_script(cur, ddl: str, dialect: str):
    """事务内安全的建表脚本执行：sqlite 的 executescript 会隐式 COMMIT，因此拆成单语句逐条处理。"""
    if dialect == "sqlite":
        for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
            cur.execute(stmt)
    else:
        cur.execute(ddl)
