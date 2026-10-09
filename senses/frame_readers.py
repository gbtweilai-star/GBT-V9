# senses/frame_readers.py —— 5 个专用读帧插件（只为"读帧数"这一件事）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 说明：主人问的"之前 100 个云插件"在本仓库里**并不存在**（那轮只在对话里提过，没有落代码），
#   所以我没有假装从那 100 个里挑——而是直接造 5 个专用读帧插件，每个只读一种真实来源，
#   各报各的帧数，并且**互不冒充**：读不到就说读不到，绝不拿另一个来源的数字顶上。
#
# 纪律：只读；每个插件返回 {plugin, ok, frames, span, gaps, source, detail}；
#      来源缺失/不可用 → ok=False + reason（不是 0）。
from __future__ import annotations

import json
import os
from pathlib import Path

PLUGINS: dict[str, dict] = {
    "devour_index": {"label": "采集索引", "kind": "devour",
                     "desc": "devoured/*/index.jsonl 里 state=stored 的帧数、跨度、缺口"},
    "segments_index": {"label": "帧段索引", "kind": "segments",
                       "desc": "segments.jsonl：段数、段内帧数合计、已归档段数"},
    "frame_lock": {"label": "逐帧账", "kind": "vision",
                   "desc": "FrameLock 逐帧报告：frames/span/gaps/late/verdict"},
    "queue_frames": {"label": "队列帧任务", "kind": "queue",
                     "desc": "媒体队列里帧相关任务的计数（按状态分组）"},
    "archive_index": {"label": "归档帧段", "kind": "archive",
                      "desc": "归档侧（R2/模拟桶）已归档的帧段与帧数合计"},
}


def _rows(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:                                  # noqa: BLE001
                continue
    return out


def _frame_dir(frame_dir=None) -> Path:
    return Path(frame_dir or os.environ.get("DEVOUR_DIR", "devoured/t1-eye"))


def _base(plugin: str, source: str) -> dict:
    return {"plugin": plugin, "label": PLUGINS[plugin]["label"], "kind": PLUGINS[plugin]["kind"],
            "source": source, "ok": False, "frames": None, "span": None, "gaps": None,
            "detail": {}}


# ① 采集索引：本地到底存了多少帧
def read_devour_index(frame_dir=None) -> dict:
    out = _base("devour_index", str(_frame_dir(frame_dir)))
    rows = _rows(_frame_dir(frame_dir) / "index.jsonl")
    if not rows:
        out["reason"] = "index.jsonl 不存在或为空（还没采过帧）"
        return out
    stored = [r for r in rows if r.get("state") == "stored"]
    seqs = sorted(int(r["seq"]) for r in stored if "seq" in r)
    gaps = sorted(set(range(seqs[0], seqs[-1] + 1)) - set(seqs)) if seqs else []
    out.update({"ok": True, "frames": len(stored),
                "span": (seqs[-1] - seqs[0] + 1) if seqs else 0, "gaps": len(gaps),
                "detail": {"gap_seqs": gaps[:10], "hashed": sum(1 for r in stored
                                                                if r.get("digest") or r.get("sha256"))}})
    return out


# ② 帧段索引：段内帧数合计（归档前的口径）
def read_segments_index(frame_dir=None) -> dict:
    idx = _frame_dir(frame_dir) / "segments.jsonl"
    out = _base("segments_index", str(idx))
    rows = _rows(idx)
    if not rows:
        out["reason"] = "segments.jsonl 不存在或为空（还没有封过段）"
        return out
    segs = {r.get("seg_id") for r in rows if r.get("seg_id")}
    frames = 0
    for r in rows:
        s, e = r.get("start_seq"), r.get("end_seq")
        if isinstance(s, int) and isinstance(e, int):
            frames += (e - s + 1)
    archived = [r for r in rows if r.get("state") == "archived"]
    out.update({"ok": True, "frames": frames, "span": frames, "gaps": 0,
                "detail": {"segments": len(segs), "archived_segments": len(archived),
                           "states": sorted({r.get("state") for r in rows if r.get("state")})}})
    return out


# ③ 逐帧账：FrameLock 的报告（含 verdict）
def read_frame_lock(frame_dir=None) -> dict:
    d = _frame_dir(frame_dir)
    out = _base("frame_lock", str(d))
    rep = None
    for name in ("frame_lock.json", "vision_report.json"):
        p = d / name
        if p.exists():
            try:
                rep = json.loads(p.read_text(encoding="utf-8"))
            except Exception:                                  # noqa: BLE001
                rep = None
            break
    if rep is None:
        out["reason"] = "没有逐帧报告（先跑一次 FrameLock.watch()）"
        return out
    out.update({"ok": True, "frames": rep.get("frames"), "span": rep.get("span"),
                "gaps": rep.get("gaps"),
                "detail": {k: rep.get(k) for k in ("verdict", "late", "avg_fps",
                                                   "on_time_ratio", "target_fps")}})
    return out


# ④ 队列帧任务：队列里帧相关任务按状态计数
def read_queue_frames(scan_ledger=None) -> dict:
    out = _base("queue_frames", "media_jobs")
    if scan_ledger is None:
        out["reason"] = "没有队列账本句柄"
        return out
    try:
        from senses.sqldialect import txn
        with txn(scan_ledger) as cur:
            # 真实列名是 stage（不是 kind）：见 media_jobs DDL
            cur.execute("SELECT stage, state, COUNT(*) FROM media_jobs GROUP BY stage, state")
            rows = cur.fetchall()
    except Exception as exc:                                   # noqa: BLE001
        out["reason"] = f"队列表读不到（{type(exc).__name__}: {exc}）"
        return out
    by = {}
    total = 0
    for stage, state, n in rows or []:
        key = f"{stage or '-'}/{state or '-'}"
        by[key] = int(n)
        if str(stage or "").lower() in ("image", "video", "frame", "clip"):
            total += int(n)                                    # 与"帧"相关的产出阶段
    out.update({"ok": True, "frames": total, "span": None, "gaps": None,
                "detail": {"by_stage_state": by,
                           "note": "frames 只计 image/video/frame/clip 阶段的作业数；"
                                   "其余阶段见 by_stage_state"}})
    return out


# ⑤ 归档帧段：归档侧（R2 或模拟桶）的帧段与帧数
def read_archive_index(bucket_dir=None) -> dict:
    base = Path(bucket_dir or os.environ.get("R2_SIM_DIR", "state/r2sim"))
    bucket = os.environ.get("R2_BUCKET", "tentacle-archive")
    root = base / bucket
    out = _base("archive_index", str(root))
    if not root.exists():
        out["reason"] = "归档目录不存在（R2 未配置，或没走过归档）"
        return out
    objs = [p for p in root.rglob("*") if p.is_file()]
    segs = [p for p in objs if p.suffix.lower() in (".mkv", ".zip")]
    out.update({"ok": True, "frames": None, "span": None, "gaps": None,
                "detail": {"objects": len(objs), "segment_objects": len(segs),
                           "note": "对象数可数；帧数需读段内索引，不在本插件职责内"}})
    return out


READERS = {"devour_index": read_devour_index, "segments_index": read_segments_index,
           "frame_lock": read_frame_lock, "queue_frames": read_queue_frames,
           "archive_index": read_archive_index}


def count_all(*, frame_dir=None, scan_ledger=None, bucket_dir=None) -> dict:
    """跑全部 5 个读帧插件，各报各的（不合并、不互相顶替）。"""
    out = {}
    for name, fn in READERS.items():
        try:
            if name == "queue_frames":
                out[name] = fn(scan_ledger)
            elif name == "archive_index":
                out[name] = fn(bucket_dir)
            else:
                out[name] = fn(frame_dir)
        except Exception as exc:                               # noqa: BLE001
            r = _base(name, "-")
            r["reason"] = f"{type(exc).__name__}: {exc}"
            out[name] = r
    ok = [v for v in out.values() if v.get("ok")]
    out["_summary"] = {"plugins": len(READERS), "readable": len(ok),
                       "frames_total_by_readable": sum(int(v["frames"]) for v in ok
                                                       if isinstance(v.get("frames"), int)),
                       "note": "各来源口径不同，不做跨源求和"}
    return out


def catalog() -> dict:
    return {"plugins": [{"id": k, **v} for k, v in PLUGINS.items()],
            "count": len(PLUGINS)}


__all__ = ["PLUGINS", "READERS", "read_devour_index", "read_segments_index",
           "read_frame_lock", "read_queue_frames", "read_archive_index",
           "count_all", "catalog"]
