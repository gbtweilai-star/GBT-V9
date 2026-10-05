# body/tools/collect.py —— 只读快照的采样与写入（引擎侧；面板只读，绝不从这里写）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 采样失败【不伪造数字】—— 快照里写 coverage=unavailable，工具层据此说"无法确认"；
#       采样只读真实来源（吞噬 index.jsonl / 登记链 / 队列指标），不做任何聚合猜测。
from __future__ import annotations
import json
import os
import time
from pathlib import Path

DEVOUR_SAMPLE_S = 5
SCAN_SAMPLE_S = 30
QUEUE_SAMPLE_S = 5
VRAM_MIN_GAP_S = 30          # 显存要走 nvidia-smi，不必每轮都拉


# ─────────── 吞噬能：与 /api/senses 共用同一读数函数（防两处漂移）───────────
def read_devour_index(frame_dir) -> dict:
    """吞噬 index.jsonl + segments.jsonl 的真读数。文件不存在 = 真 0 帧，不是错误。"""
    d = Path(frame_dir)
    idx, seg_idx = d / "index.jsonl", d / "segments.jsonl"

    def _rows(p: Path) -> list[dict]:
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    stored = [f for f in _rows(idx) if f.get("state") == "stored"]
    seqs = sorted(f["seq"] for f in stored if "seq" in f)
    gaps = sorted(set(range(seqs[0], seqs[-1] + 1)) - set(seqs)) if seqs else []
    segs = _rows(seg_idx)
    return {"frame_dir": str(d), "index_present": idx.exists(),
            "segments_index_present": seg_idx.exists(),
            "frames": len(stored), "hashes": sum(1 for f in stored if f.get("sha256")),
            "span": (seqs[-1] - seqs[0] + 1) if seqs else 0,
            "gaps": len(gaps), "gap_seqs": gaps[:10],
            "segments": len({s.get("seg_id") for s in segs if s.get("seg_id")})}


def collect_devour(frame_dir) -> tuple[dict, list[dict]]:
    try:
        facts = read_devour_index(frame_dir)
        facts["coverage"] = "observed"
        ev = [{"kind": "index_jsonl", "ref": str(Path(frame_dir) / "index.jsonl"),
               "level": "E1", "frames": facts["frames"]},
              {"kind": "segments_jsonl",
               "ref": str(Path(frame_dir) / "segments.jsonl"), "level": "E1"}]
        return facts, ev
    except Exception as e:                                   # noqa: BLE001
        return ({"coverage": "unavailable", "error": type(e).__name__}, [])


# ─────────── 扫描覆盖：登记链 + 索引状态 + 触手责任页 ───────────
async def collect_scan(ledger) -> tuple[dict, list[dict]]:
    try:
        head = await ledger.fetch_one("""SELECT
                (SELECT MAX(seq) FROM registration) AS head_seq,
                (SELECT COUNT(*)  FROM registration) AS total,
                COALESCE(SUM(CASE WHEN event_type='scan'   THEN 1 ELSE 0 END),0) AS n_scan,
                COALESCE(SUM(CASE WHEN event_type='change' THEN 1 ELSE 0 END),0) AS n_change,
                COALESCE(SUM(CASE WHEN event_type='fix'    THEN 1 ELSE 0 END),0) AS n_fix,
                COALESCE(SUM(CASE WHEN event_type='harden' THEN 1 ELSE 0 END),0) AS n_harden
            FROM registration""")
        idx = await ledger.fetch_one("""SELECT
                COALESCE(SUM(CASE WHEN state='clean'   THEN 1 ELSE 0 END),0) AS clean,
                COALESCE(SUM(CASE WHEN state='dirty'   THEN 1 ELSE 0 END),0) AS dirty,
                COALESCE(SUM(CASE WHEN state='missing' THEN 1 ELSE 0 END),0) AS missing,
                COUNT(*) AS indexed FROM body_files""")
        per = await ledger.fetch_all("""SELECT tentacle_id, COUNT(*) AS pages
            FROM tentacle_file_bindings GROUP BY tentacle_id
            ORDER BY pages DESC LIMIT 50""")
        man = await ledger.fetch_one(
            "SELECT head_seq, head_hash, indexed, generation FROM brain_boot_manifest "
            "WHERE root_id=?", ("main",))
        facts = {"coverage": "observed", "head_seq": head["head_seq"] or 0,
                 "total": head["total"] or 0, "n_scan": head["n_scan"],
                 "n_change": head["n_change"], "n_fix": head["n_fix"],
                 "n_harden": head["n_harden"], "index": dict(idx or {}),
                 "per_tentacle": {r["tentacle_id"]: r["pages"] for r in per},
                 "tentacles": len(per), "manifest": dict(man) if man else None}
        ev = [{"kind": "registration", "level": "E1", "head_seq": facts["head_seq"]},
              {"kind": "body_files", "level": "E1",
               "indexed": (idx or {}).get("indexed", 0)},
              {"kind": "tentacle_file_bindings", "level": "E1",
               "tentacles": facts["tentacles"]}]
        return facts, ev
    except Exception as e:                                   # noqa: BLE001
        return ({"coverage": "unavailable", "error": type(e).__name__}, [])


# ─────────── 队列状态：媒体指标 + 显存（显存低频采样并缓存）───────────
_VRAM_CACHE: dict = {"at": 0.0, "value": None}


def collect_queue(scan_ledger, *, budget=None, now_fn=time.time) -> tuple[dict, list[dict]]:
    from media.metrics import queue_stats, read_vram, vram_snapshot
    stats = queue_stats(scan_ledger)
    # 显存：nvidia-smi 有开销 → 30 秒一次，缓存其间不复采（读数带采样时刻）
    if scan_ledger is not None and now_fn() - _VRAM_CACHE["at"] >= VRAM_MIN_GAP_S:
        value, src = read_vram()
        _VRAM_CACHE.update(at=now_fn(),
                           value=({"available": True, **(value or {}), "source": src}
                                  if value else {"available": False, "source": src}))
    stats["vram"] = dict(_VRAM_CACHE["value"] or {"available": False,
                                                  "source": "unavailable"})
    stats["vram"]["sampled_at"] = _VRAM_CACHE["at"] or None
    stats["scheduler_reserved"] = vram_snapshot(budget)      # 静态预留，与真实读数并列
    ev = [{"kind": "media_jobs", "level": "E1", "window_sec": stats.get("window_sec")},
          {"kind": "vram", "level": "E1", "source": stats["vram"].get("source")}]
    return stats, ev


# ─────────── 写入：revision 单调递增，字段全部参数绑定 ───────────
async def write_snapshot(ledger, domain: str, facts: dict, evidence: list,
                         sample_period_s: int, *, now_fn=time.time) -> dict:
    prev = await ledger.fetch_one(
        "SELECT revision FROM body_read_snapshots WHERE domain=?", (domain,))
    rev = int((prev or {}).get("revision") or 0) + 1
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_fn()))
    await ledger.execute(
        """INSERT INTO body_read_snapshots
             (domain, revision, observed_at, sample_period_s, payload_json, evidence_json)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT (domain) DO UPDATE SET revision=EXCLUDED.revision,
             observed_at=EXCLUDED.observed_at,
             sample_period_s=EXCLUDED.sample_period_s,
             payload_json=EXCLUDED.payload_json,
             evidence_json=EXCLUDED.evidence_json""",
        (domain, rev, at, int(sample_period_s),
         json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str),
         json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)))
    return {"domain": domain, "revision": rev, "observed_at": at}


async def refresh_once(ledger, *, frame_dir=None, scan_ledger=None, budget=None,
                       now_fn=time.time, domains=("devour", "scan", "queue")) -> dict:
    """采一轮并落库。任一域失败只影响该域（coverage=unavailable），不影响其它域。"""
    out = {}
    frame_dir = frame_dir or os.environ.get("DEVOUR_DIR", "devoured/t1-eye")
    if "devour" in domains:
        facts, ev = collect_devour(frame_dir)
        out["devour"] = await write_snapshot(ledger, "devour", facts, ev,
                                             DEVOUR_SAMPLE_S, now_fn=now_fn)
    if "scan" in domains:
        facts, ev = await collect_scan(ledger)
        out["scan"] = await write_snapshot(ledger, "scan", facts, ev,
                                           SCAN_SAMPLE_S, now_fn=now_fn)
    if "queue" in domains:
        facts, ev = collect_queue(scan_ledger, budget=budget, now_fn=now_fn)
        out["queue"] = await write_snapshot(ledger, "queue", facts, ev,
                                            QUEUE_SAMPLE_S, now_fn=now_fn)
    return out


async def refresh_loop(ledger, *, frame_dir=None, scan_ledger=None, budget=None,
                       interval=QUEUE_SAMPLE_S, stop=None):
    """后台采样循环。stop 是 (threading.Event 或 asyncio.Event)。"""
    import asyncio
    fails = 0
    while True:
        if stop is not None and stop.is_set():
            return
        try:
            await refresh_once(ledger, frame_dir=frame_dir,
                               scan_ledger=scan_ledger, budget=budget)
            fails = 0
        except Exception as e:                                # noqa: BLE001
            fails += 1
            if fails in (1, 5, 30):                            # 不刷屏：第 1/5/30 次留痕
                try:
                    await ledger.record_blocked("read_snapshot_refresh_failed",
                                                {"fails": fails,
                                                 "error": type(e).__name__})
                except Exception:
                    pass
        await asyncio.sleep(interval)


__all__ = ["read_devour_index", "collect_devour", "collect_scan", "collect_queue",
           "write_snapshot", "refresh_once", "refresh_loop",
           "DEVOUR_SAMPLE_S", "SCAN_SAMPLE_S", "QUEUE_SAMPLE_S"]
