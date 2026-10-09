# audit/scale_sqlite.py —— SQLite 版存储与扩容读数（斜率 / 耗尽预测 / 控制器）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 面板上"存储与扩容"三格以前在 SQLite 下是"—"（只给 PG 做了）。这里补上 SQLite 的真读数：
#   · 每次采样把 (体量, 行数) 追加进 db_size_samples（本表自建，不动业务表）
#   · 斜率 = 相邻样本的 MB/天（按时间差换算）；样本不足就如实说"样本不足（还需 N 秒）"
#   · 耗尽预测 = (软上限 − 当前) / 斜率 → 天数；没斜率就说算不了
#   · 控制器 = 按占用率与斜率给 ok/watch/act 三态（并说明依据）
# 纪律：只读业务库 + 只往自己的采样表写；拿不到就给原因，不编数字。
import os
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 软上限：本地磁盘可用空间的 80%（真探测；探测不到就用 20GB 兜底并标注）
FALLBACK_CAP_MB = float(os.environ.get("V9_DB_CAP_MB", "20480"))


def _db_path() -> Path:
    return Path(os.environ.get("LEDGER_DB", str(ROOT.joinpath("tentacle_ledger.db"))))


def _size_mb(p: Path) -> float | None:
    try:
        return round(p.stat().st_size / 1048576.0, 3)
    except OSError:
        return None


def _disk_free_mb(p: Path) -> float | None:
    try:
        import shutil
        return round(shutil.disk_usage(str(p.parent if p.parent.exists() else ROOT)).free
                     / 1048576.0, 1)
    except Exception:                                          # noqa: BLE001
        return None


def _cap_mb(p: Path) -> tuple:
    """软上限 = 磁盘剩余 × 0.8（真探测）；探测不到 → 兜底值并标注来源。"""
    free = _disk_free_mb(p)
    if free is None:
        return FALLBACK_CAP_MB, "兜底值（磁盘探测不可用）"
    return round(free * 0.8, 1), "磁盘剩余 × 0.8（真探测）"


def ensure_table(led) -> bool:
    if led is None:
        return False
    try:
        with led._tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS db_size_samples ("
                      "ts REAL, db TEXT, size_mb REAL, rows INTEGER, free_mb REAL)")
        return True
    except Exception:                                          # noqa: BLE001
        return False


def sample(led, *, rows: int | None = None) -> dict:
    """采一次样（追加一行），返回本次读数。"""
    if led is None:
        return {"ok": False, "reason": "无账本连接"}
    if not ensure_table(led):
        return {"ok": False, "reason": "采样表不可用"}
    p = _db_path()
    size = _size_mb(p)
    if size is None:
        return {"ok": False, "reason": f"库文件读不到：{p.name}"}
    if rows is None:
        try:
            with led._tx() as c:
                rows = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        except Exception:                                      # noqa: BLE001
            rows = None
    free = _disk_free_mb(p)
    try:
        with led._tx() as c:
            c.execute("INSERT INTO db_size_samples(ts, db, size_mb, rows, free_mb) "
                      "VALUES(?,?,?,?,?)",
                      (time.time(), p.name, float(size), int(rows or 0), free))
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "reason": f"写样本失败 {type(exc).__name__}"}
    return {"ok": True, "db": p.name, "size_mb": size, "rows": rows, "free_mb": free}


def read(led, *, window_s: float = 6 * 3600) -> dict:
    """算斜率/耗尽/控制器：用窗口内最早与最新的样本（样本不足如实说明）。"""
    if led is None:
        return {"ok": False, "enabled": False, "reason": "无账本连接"}
    if not ensure_table(led):
        return {"ok": False, "enabled": False, "reason": "采样表不可用"}
    p = _db_path()
    cap, cap_src = _cap_mb(p)
    try:
        with led._tx() as c:
            rows = c.execute("SELECT ts, size_mb, rows, free_mb FROM db_size_samples "
                             "WHERE ts >= ? ORDER BY ts ASC",
                             (time.time() - window_s,)).fetchall()
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "enabled": False, "reason": type(exc).__name__}
    cur_mb = _size_mb(p)
    out = {"ok": True, "enabled": True, "backend": "sqlite", "db": p.name,
           "size_mb": cur_mb, "cap_mb": cap, "cap_source": cap_src,
           "used_pct": (round(100.0 * (cur_mb or 0) / cap, 2) if cap else None),
           "samples": len(rows or []), "window_h": round(window_s / 3600, 1),
           "sample_age_sec": (round(time.time() - float(rows[-1][0]), 1) if rows else None)}
    if not rows:
        out.update({"growth_mb_day": None, "eta_days": None, "controller": "watch",
                    "reason": "还没有样本（先采一次样即可开始跟踪）"})
        return out
    first, last = rows[0], rows[-1]
    dt_days = (float(last[0]) - float(first[0])) / 86400.0
    if dt_days <= 0 or len(rows) < 2:
        out.update({"growth_mb_day": None, "eta_days": None, "controller": "watch",
                    "reason": f"样本不足（{len(rows)} 条，至少 2 条且要有时间差）"})
        return out
    growth = (float(last[1]) - float(first[1])) / dt_days
    out["growth_mb_day"] = round(growth, 3)
    out["rows_now"] = int(last[2] or 0)
    if growth > 0 and cap > (cur_mb or 0):
        out["eta_days"] = round((cap - (cur_mb or 0)) / growth, 1)
    else:
        out["eta_days"] = None
        out["eta_note"] = "体量未增长或已达上限 → 不给 ETA（不编）"
    used = out.get("used_pct") or 0
    if used >= 80 or (out["eta_days"] is not None and out["eta_days"] < 7):
        out["controller"] = "act"
        out["controller_why"] = "占用≥80% 或 7 天内将耗尽 → 该归档/轮转了"
    elif used >= 50:
        out["controller"] = "watch"
        out["controller_why"] = "占用≥50% → 持续观察"
    else:
        out["controller"] = "ok"
        out["controller_why"] = "占用低且增长平稳 → 无需动作"
    return out


__all__ = ["sample", "read", "ensure_table"]
