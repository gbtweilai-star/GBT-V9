# audit/ledger_factory.py —— 账本后端工厂：SQLite / PostgreSQL 一键切换
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 切换规则（优先级从高到低）:
#   1. LEDGER_BACKEND=pg|sqlite   显式指定
#   2. 有 DATABASE_URL 且 LEDGER_BACKEND 未设 -> pg
#   3. 否则 -> sqlite
import os

def backend_name() -> str:
    explicit = os.environ.get("LEDGER_BACKEND", "").lower()
    if explicit in ("pg", "postgres", "postgresql"): return "pg"
    if explicit in ("sqlite", "sqlite3"):            return "sqlite"
    return "pg" if os.environ.get("DATABASE_URL") else "sqlite"

def make_ledger(**kw):
    """返回统一接口的账本对象（扫描器/触手无感）"""
    name = backend_name()
    if name == "pg":
        from audit.ledger_pg import PGLedger
        maxconn = int(os.environ.get("PG_MAXCONN", 32))
        slow_ms = int(os.environ.get("PG_SLOW_MS", 200))
        led = PGLedger(maxconn=maxconn, slow_ms=slow_ms, **kw)
        led.backend = "pg"
        return led
    from audit.ledger import Ledger
    led = Ledger(db=os.environ.get("LEDGER_DB", "tentacle_ledger.db"), **kw)
    led.backend = "sqlite"
    return led

def make_cross_board(ledger, brain=None, **kw):
    """返回统一接口的交叉复核板（plan/claim/progress 同名）"""
    if getattr(ledger, "backend", "sqlite") == "pg":
        from audit.ledger_pg import PGCrossBoard
        return PGCrossBoard(ledger, brain, **kw)
    from scan.cross_scan import CrossBoard
    return CrossBoard(ledger, brain, **kw)

def backend_info() -> dict:
    name = backend_name()
    if name == "pg":
        dsn = os.environ.get("DATABASE_URL", "")
        safe = dsn.split("@")[-1] if "@" in dsn else "(未配置)"
        return {"backend": "pg", "target": safe,
                "maxconn": os.environ.get("PG_MAXCONN", 32)}
    return {"backend": "sqlite",
            "target": os.environ.get("LEDGER_DB", "tentacle_ledger.db")}
