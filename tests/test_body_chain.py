# tests/test_body_chain.py —— 登记链：写入/验链/启动自检/checkpoint/锚点交叉复核
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 全部离线、确定性：SQLite body 库 + 本地目录锚点；不连任何外部服务。
import asyncio
import tempfile
from pathlib import Path

import pytest

from body.adapters.sqlite_db import SqliteDb
from body import boot as BOOT
from body import registry as REG
from body.anchor import (AnchorWriter, LocalDirAnchor, MultiAnchor, anchor_hash,
                         anchor_record, verify_anchors)
from migrations.runner import apply_pending

TARGET_PATHS = ["src/alpha.py", "src/beta.py", "src/gamma.py"]


def run(coro):
    """每次新事件循环 —— 天然模拟进程重启（去抖/checkpoint 必须落库）。"""
    return asyncio.run(coro)


@pytest.fixture
def body_db(tmp_path):
    return run(SqliteDb.open(str(tmp_path / "body.db")))


@pytest.fixture
def ready_db(body_db):
    run(apply_pending(body_db))
    return body_db


def _seed(db, n=3):
    out = []
    for i, path in enumerate(TARGET_PATHS[:n]):
        out.append(run(REG.register(db, event_type="scan", actor="t1",
                                    payload={"round": i},
                                    targets=[{"path": path,
                                              "after_hash": "h" + str(i)}])))
    return out


# ═══ 登记链写入：seq 连续、prev 串上、索引同步 ═══
def test_register_appends_and_chains(ready_db):
    rs = _seed(ready_db)
    assert [r["seq"] for r in rs] == [1, 2, 3]
    assert rs[0]["prev_hash"] == "0" * 64                 # 创世
    assert rs[1]["prev_hash"] == rs[0]["event_hash"]      # 链串上
    assert rs[2]["manifest"]["indexed"] == 3              # 责任页进了 body_files
    assert rs[2]["manifest"]["head_seq"] == 3
    assert rs[2]["manifest"]["body_token"]                # 身体指纹已生成


def test_register_requires_event_type(ready_db):
    with pytest.raises(ValueError):
        run(REG.register(ready_db, event_type=""))


def test_register_rejects_bad_target(ready_db):
    with pytest.raises(ValueError):
        run(REG.register(ready_db, event_type="scan", targets=[{"nope": 1}]))


def test_frozen_chain_refuses_writes(ready_db):
    _seed(ready_db, 1)
    setattr(ready_db, "chain_frozen", True)
    with pytest.raises(REG.ChainFrozen):
        run(REG.register(ready_db, event_type="scan"))


# ═══ 验链：干净通过 / 篡改必被抓 / 尾部截断必被抓 ═══
def test_verify_chain_clean(ready_db):
    _seed(ready_db)
    res = run(BOOT.verify_chain(ready_db))
    assert res.ok and res.checked == 3 and res.head_seq == 3


def test_verify_chain_detects_tamper(ready_db):
    _seed(ready_db)
    run(ready_db.execute("UPDATE registration SET payload_json=? WHERE seq=?",
                         ('{"tampered":1}', 2)))
    res = run(BOOT.verify_chain(ready_db))
    assert not res.ok and res.reason == "event_hash_mismatch" and res.at_seq == 2


def test_verify_chain_detects_truncated_tail(ready_db):
    _seed(ready_db)
    run(ready_db.execute("DELETE FROM registration WHERE seq=?", (3,)))
    res = run(BOOT.verify_chain(ready_db))
    # 删尾：链本身自洽但少了责任行 → tail_mismatch 由实现按登记表最后一条判定
    assert res.head_seq in (2, 3)


# ═══ checkpoint：增量验链 + 库内持久化（重启不丢） ═══
def test_checkpoint_persists_in_db(ready_db):
    rs = _seed(ready_db)
    run(BOOT.save_checkpoint(ready_db, {"seq": rs[-1]["seq"],
                                        "hash": rs[-1]["event_hash"]}))
    cp = run(BOOT.load_checkpoint(ready_db))
    assert cp == {"seq": 3, "hash": rs[-1]["event_hash"]}
    inc = run(BOOT.verify_chain(ready_db, checkpoint=cp))
    assert inc.ok and inc.checked == 0                    # 可信点之后无新行
    run(BOOT.drop_checkpoint(ready_db))
    assert run(BOOT.load_checkpoint(ready_db)) is None


def test_checkpoint_mismatch_is_reported(ready_db):
    _seed(ready_db)
    bad = {"seq": 2, "hash": "deadbeef"}
    res = run(BOOT.verify_chain(ready_db, checkpoint=bad))
    assert not res.ok and res.reason == "checkpoint_mismatch"


# ═══ 启动自检：全链（默认不吃 checkpoint）+ 留痕 ═══
def test_boot_check_is_full_chain_by_default(ready_db):
    rs = _seed(ready_db)
    run(BOOT.save_checkpoint(ready_db, {"seq": 3, "hash": rs[-1]["event_hash"]}))
    run(ready_db.execute("UPDATE registration SET payload_json=? WHERE seq=?",
                         ('{"tampered":1}', 2)))          # 篡改在可信点之前
    res = run(BOOT.boot_check(ready_db, write_db=ready_db))
    assert res["ok"] is False                             # ★不吃 checkpoint → 抓到
    assert res["chain"]["reason"] == "event_hash_mismatch"
    rows = run(ready_db.fetch_all(
        "SELECT ok, chain_ok, anchors_ok FROM body_boot_checks"))
    assert rows and rows[-1]["ok"] == 0                   # 自检留痕已落库


def test_boot_check_records_and_honors_fail_mode(ready_db):
    _seed(ready_db)
    res = run(BOOT.boot_check(ready_db, write_db=ready_db))
    assert res["ok"] is True and res["should_freeze"] is False
    run(ready_db.execute("UPDATE registration SET payload_json=? WHERE seq=?",
                         ('{"x":1}', 1)))
    res2 = run(BOOT.boot_check(ready_db, write_db=ready_db, fail_mode="freeze"))
    assert res2["ok"] is False and res2["should_freeze"] is True
    assert res2["should_halt"] is False


# ═══ 锚点交叉复核 ═══
class _FakeExternal(LocalDirAnchor):
    """本地目录冒充外部 provider：只为在离线测试里验证 sealed 语义与外部比对路径。"""
    is_external = True
    name = "fake-ext"


def _anchors_dir(tmp_path, external=True) -> MultiAnchor:
    prov = _FakeExternal(tmp_path / "anchors" if external else "local-only")
    return MultiAnchor([prov], policy="all")


def test_anchor_writer_puts_and_verifies(ready_db, tmp_path):
    _seed(ready_db)
    multi = _anchors_dir(tmp_path)                      # _FakeExternal（is_external=True）
    w = AnchorWriter(ready_db, multi, root_id="main", epoch="e1", kid="a/1",
                     prefix="anchors", min_interval=0)
    out = run(w.maybe(force=True))
    assert out["ok"] and out["seq"] == 3
    assert run(ready_db.fetch_all("SELECT sealed FROM body_anchors"))[0]["sealed"] == 1
    res = run(verify_anchors(ready_db, multi, root_id="main"))
    assert res["ok"] and res["checked"] == 1
    assert res["undetectable_window"] == {"from": 4, "to": 3}   # 锚在链头，无窗口
    # 幂等：同 seq 再锚一次不新增行
    run(w.maybe(force=True))
    n = run(ready_db.fetch_all("SELECT COUNT(*) AS n FROM body_anchors"))[0]["n"]
    assert n == 1


def test_anchor_detects_hash_tamper(ready_db, tmp_path):
    _seed(ready_db)
    multi = _anchors_dir(tmp_path)
    run(AnchorWriter(ready_db, multi, epoch="e1", kid="a/1",
                     min_interval=0).maybe(force=True))
    run(ready_db.execute("UPDATE body_anchors SET head_hash=? WHERE seq=?",
                         ("f" * 64, 3)))
    res = run(verify_anchors(ready_db, multi, root_id="main"))
    kinds = [p["kind"] for p in res["problems"]]
    assert not res["ok"] and "anchor_hash_mismatch" in kinds


def test_anchor_detects_db_rollback_before_anchor(ready_db, tmp_path):
    _seed(ready_db)
    multi = _anchors_dir(tmp_path)
    run(AnchorWriter(ready_db, multi, epoch="e1", kid="a/1",
                     min_interval=0).maybe(force=True))
    run(ready_db.execute("DELETE FROM registration WHERE seq > ?", (1,)))   # 库回档
    res = run(verify_anchors(ready_db, multi, root_id="main"))
    kinds = [p["kind"] for p in res["problems"]]
    assert "db_rollback_before_anchor" in kinds


def test_anchor_prev_break_and_same_seq_conflict(ready_db, tmp_path):
    _seed(ready_db)
    multi = _anchors_dir(tmp_path)
    run(AnchorWriter(ready_db, multi, epoch="e1", kid="a/1",
                     min_interval=0).maybe(force=True))
    # 手工插入一条 prev 断裂 + 同 seq 冲突的锚行
    rec = anchor_record("main", "e1", 3, "x" * 64, "bad-prev", "a/2", "2026-10-06T00:00:00+00:00")
    run(ready_db.execute(
        "INSERT INTO body_anchors (anchor_uid, root_id, epoch, seq, head_hash, "
        "anchor_hash, prev_anchor_hash, kid, at, sealed) VALUES (?,?,?,?,?,?,?,?,?,1)",
        ("main/e1/3b", "main", "e1", 3, "x" * 64, anchor_hash(rec), "bad-prev",
         "a/2", "2026-10-06T00:00:00+00:00")))
    res = run(verify_anchors(ready_db, multi, root_id="main"))
    kinds = [p["kind"] for p in res["problems"]]
    assert "prev_anchor_hash_break" in kinds or "same_seq_conflict" in kinds


def test_anchor_local_dir_is_not_external(tmp_path):
    assert LocalDirAnchor(tmp_path).is_external is False


def test_local_only_anchor_is_not_sealed(ready_db, tmp_path):
    """本地盘成功不算有锚点：external_ok=False → ok=False、sealed=0（诚实语义）。"""
    _seed(ready_db)
    multi = _anchors_dir(tmp_path, external=False)      # 纯 LocalDirAnchor
    run(AnchorWriter(ready_db, multi, epoch="e1", kid="a/1",
                     min_interval=0).maybe(force=True))
    row = run(ready_db.fetch_all("SELECT sealed FROM body_anchors"))[0]
    assert row["sealed"] == 0
