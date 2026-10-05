# tests/test_cache.py —— 缓存回收：LRU / pin 保护 / 保底 / 宽限期
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import os, time
from pathlib import Path
from senses.cache_reaper import CacheReaper, Budget


def _mk(d: Path, name: str, size: int, age_sec: float):
    p = d / name
    p.write_bytes(b"x" * size)
    t = time.time() - age_sec
    os.utime(p, (t, t))
    return p


def test_cache_lru_eviction(tmp_path):
    """超预算时淘汰最旧的"""
    d = tmp_path / "c"; d.mkdir()
    _mk(d, "old.bin", 100, 300)
    _mk(d, "mid.bin", 100, 200)
    _mk(d, "new.bin", 100, 100)
    r = CacheReaper([Budget("t", d, max_bytes=250, min_keep=0, grace_sec=0)])
    freed_bytes, freed_files = r.reap_budget(r.budgets[0])
    assert freed_files == 1 and freed_bytes == 100
    assert not (d / "old.bin").exists()               # 最旧的被删
    assert (d / "new.bin").exists()                   # 最新的留着
    assert r.usage(r.budgets[0]) <= 250               # 真降到预算内


def test_cache_pin_protects(tmp_path):
    """被 pin 的文件（正在播放）不删，跳过去删下一个；
    解锁后再次超预算时，最旧的（曾 pin 的）按 LRU 先走。"""
    d = tmp_path / "c"; d.mkdir()
    old = _mk(d, "old.bin", 100, 300)
    _mk(d, "mid.bin", 100, 200)
    _mk(d, "new.bin", 100, 100)
    r = CacheReaper([Budget("t", d, max_bytes=250, min_keep=0, grace_sec=0)])
    r.pin(old)                                        # 锁住最旧的
    r.reap_budget(r.budgets[0])
    assert old.exists()                               # pin 生效
    assert not (d / "mid.bin").exists()               # 改为淘汰次旧
    assert (d / "new.bin").exists()
    r.unpin(old)
    # 此时 old+new=200 ≤ 预算 250，回收器不会动它（水位语义）；
    # 再灌 200B 超预算 → 旧者必须先走
    _mk(d, "extra.bin", 200, 50)
    r.reap_budget(r.budgets[0])
    assert not old.exists()                           # 解锁后按 LRU 先淘汰最旧
    assert (d / "extra.bin").exists()                 # 最新留在预算内


def test_cache_min_keep_floor(tmp_path):
    """哪怕预算极小，也要保底留 N 个"""
    d = tmp_path / "c"; d.mkdir()
    for i in range(3):
        _mk(d, f"f{i}.bin", 100, 300 - i * 10)
    r = CacheReaper([Budget("t", d, max_bytes=10, min_keep=1, grace_sec=0)])
    r.reap_budget(r.budgets[0])
    left = list(d.glob("*.bin"))
    assert len(left) == 1                             # 保底 1 个


def test_cache_grace_period(tmp_path):
    """刚写入的文件在宽限期内不删（防边写边删）"""
    d = tmp_path / "c"; d.mkdir()
    _mk(d, "fresh.bin", 100, 0)                       # 刚写
    r = CacheReaper([Budget("t", d, max_bytes=10, min_keep=0, grace_sec=3600)])
    r.reap_budget(r.budgets[0])
    assert (d / "fresh.bin").exists()                 # 宽限期保护


def test_cache_reap_all_logs(ledger, tmp_path):
    """reap_all 有真读数，并落一条账本"""
    d = tmp_path / "c"; d.mkdir()
    _mk(d, "a.bin", 100, 300)
    _mk(d, "b.bin", 100, 200)
    r = CacheReaper([Budget("t", d, max_bytes=100, min_keep=0, grace_sec=0)],
                    ledger=ledger)
    result = r.reap_all()
    assert result["t"]["freed_files"] == 1
    rows = ledger.conn.execute(
        "SELECT scanner,status FROM ledger WHERE scanner='cache-reaper'").fetchall()
    assert rows and rows[0][1] == "scanned"           # 回收动作进了账本
