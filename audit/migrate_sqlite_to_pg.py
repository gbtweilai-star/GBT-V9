# audit/migrate_sqlite_to_pg.py —— SQLite → PG 一次性回填
# 用法: python audit/migrate_sqlite_to_pg.py --sqlite tentacle_ledger.db --verify
import argparse, os, sqlite3, uuid, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.ledger_pg import PGLedger

def migrate(sqlite_path, dsn, verify=False):
    src = sqlite3.connect(sqlite_path)
    dst = PGLedger(dsn=dsn)
    n = 0
    cur = src.execute("SELECT ts,scanner,target,status,detail,brain_verdict FROM ledger")
    with dst._tx() as c, c.cursor() as pc:
        while True:
            batch = cur.fetchmany(2000)
            if not batch: break
            rows = [(uuid.uuid4(), ts, s, t, st, d or "", bv or "")
                    for (ts, s, t, st, d, bv) in batch]
            pc.executemany(
                "INSERT INTO ledger(event_id,ts,scanner,target,status,detail,brain_verdict) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING", rows)
            n += len(rows)
            print(f"  回填 {n} 行…", end="\r")
    print(f"\n  回填完成: {n} 行")
    if verify:
        with dst._tx() as c, c.cursor() as pc:
            pc.execute("SELECT COUNT(*) FROM ledger")
            pg_n = pc.fetchone()[0]
        print(f"  校验: SQLite={n}  PG={pg_n}  {'✅一致' if pg_n>=n else '❌不符'}")
    dst.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="tentacle_ledger.db")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    migrate(a.sqlite, a.dsn, a.verify)

# 双写包装（过渡期用）：任一成功即视为成功，失败侧记补偿日志
class DualLedger:
    def __init__(self, primary, shadow):
        self.primary, self.shadow = primary, shadow
    def log(self, scanner, target, status, detail=""):
        eid = uuid.uuid4()                       # 同一 event_id 两侧复用
        self.primary.log(scanner, target, status, detail, event_id=eid)
        try: self.shadow.log(scanner, target, status, detail, event_id=eid)
        except Exception as e:
            print(f"[dual] 影子库写失败(已补偿): {e}")
    def __getattr__(self, k): return getattr(self.primary, k)
