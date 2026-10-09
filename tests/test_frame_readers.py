# tests/test_frame_readers.py —— 5 个专用读帧插件：各报各的、读不到就说读不到
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import json

import pytest

from senses.frame_readers import (READERS, catalog, count_all, read_archive_index,
                                  read_devour_index, read_frame_lock,
                                  read_queue_frames, read_segments_index)


def test_catalog_has_exactly_five_plugins():
    c = catalog()
    assert c["count"] == 5
    assert [p["id"] for p in c["plugins"]] == list(READERS)
    assert all(p["desc"] for p in c["plugins"])          # 每个插件都要说清自己读什么


def _mk(tmp_path, name="d"):
    d = tmp_path / name
    d.mkdir()
    return d


# ① 采集索引
def test_devour_index_counts_stored_and_gaps(tmp_path):
    d = _mk(tmp_path)
    (d / "index.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"seq": 0, "state": "stored", "sha256": "a"},
        {"seq": 1, "state": "stored", "sha256": "b"},
        {"seq": 3, "state": "stored", "sha256": "c"}]) + "\n", encoding="utf-8")
    r = read_devour_index(d)
    assert r["ok"] is True and r["frames"] == 3 and r["span"] == 4 and r["gaps"] == 1
    assert r["detail"]["hashed"] == 3


def test_devour_index_missing_is_not_zero(tmp_path):
    r = read_devour_index(_mk(tmp_path))
    assert r["ok"] is False and r["frames"] is None and "不存在" in r["reason"]


# ② 帧段索引
def test_segments_index_sums_frames_in_segments(tmp_path):
    d = _mk(tmp_path)
    (d / "segments.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"seg_id": "s1", "start_seq": 0, "end_seq": 11, "state": "archived"},
        {"seg_id": "s2", "start_seq": 12, "end_seq": 23, "state": "sealed"}]) + "\n",
        encoding="utf-8")
    r = read_segments_index(d)
    assert r["ok"] is True and r["frames"] == 24
    assert r["detail"]["segments"] == 2 and r["detail"]["archived_segments"] == 1


# ③ 逐帧账
def test_frame_lock_reads_report(tmp_path):
    d = _mk(tmp_path)
    (d / "frame_lock.json").write_text(json.dumps(
        {"frames": 30, "span": 30, "gaps": 0, "verdict": "on_time", "late": 0}),
        encoding="utf-8")
    r = read_frame_lock(d)
    assert r["ok"] is True and r["frames"] == 30 and r["detail"]["verdict"] == "on_time"


def test_frame_lock_without_report_says_so(tmp_path):
    r = read_frame_lock(_mk(tmp_path))
    assert r["ok"] is False and "逐帧报告" in r["reason"]


def test_frame_lock_watch_dumps_report(tmp_path):
    from senses.frame_lock import FrameLock
    d = tmp_path / "v"
    fl = FrameLock(d, fps=30, grabber=lambda: b"x" * 32, keep_frames=False)
    fl.watch(0.2, warmup=False, auto_rate=False)
    assert (d / "frame_lock.json").is_file()             # 跑完就落账，插件才读得到
    assert read_frame_lock(d)["ok"] is True


# ④ 队列帧任务
def test_queue_frames_without_ledger_says_so():
    r = read_queue_frames(None)
    assert r["ok"] is False and "账本句柄" in r["reason"]


def test_queue_frames_reads_real_table(tmp_path):
    from audit.ledger import Ledger
    led = Ledger(db=str(tmp_path / "q.db"))
    try:
        with led._tx(write=True) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS media_jobs (
                job_id TEXT PRIMARY KEY, stage TEXT, state TEXT, created_at REAL)""")
            c.execute("INSERT INTO media_jobs VALUES ('j1','video','completed',1)")
            c.execute("INSERT INTO media_jobs VALUES ('j2','image','dead',1)")
            c.execute("INSERT INTO media_jobs VALUES ('j3','audio','queued',1)")
            # 事务由 _tx() 管理，这里不要再 commit()（会报 no transaction is active）
        r = read_queue_frames(led)
        assert r["ok"] is True
        assert r["frames"] == 2                          # video + image 属"帧相关"
        assert r["detail"]["by_stage_state"]["audio/queued"] == 1
    finally:
        led.close()


# ⑤ 归档帧段
def test_archive_index_counts_only_segment_objects(tmp_path, monkeypatch):
    bucket = tmp_path / "r2sim" / "tentacle-archive"
    (bucket / "t1").mkdir(parents=True)
    (bucket / "t1" / "seg-1.mkv").write_bytes(b"x")
    (bucket / "t1" / "seg-2.zip").write_bytes(b"y")
    (bucket / "t1" / "meta.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("R2_BUCKET", "tentacle-archive")
    r = read_archive_index(tmp_path / "r2sim")
    assert r["ok"] is True and r["detail"]["segment_objects"] == 2
    assert r["frames"] is None                            # 帧数不在本插件职责内，不硬报


def test_archive_index_missing_is_not_zero(tmp_path):
    r = read_archive_index(tmp_path / "nope")
    assert r["ok"] is False and "不存在" in r["reason"]


# 汇总：不跨源求和
def test_count_all_does_not_merge_sources(tmp_path):
    d = _mk(tmp_path)
    (d / "index.jsonl").write_text(json.dumps({"seq": 0, "state": "stored"}) + "\n",
                                   encoding="utf-8")
    out = count_all(frame_dir=d)
    assert out["_summary"]["plugins"] == 5
    assert "不做跨源求和" in out["_summary"]["note"]
    assert set(out) == set(READERS) | {"_summary"}
