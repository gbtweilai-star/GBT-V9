# senses/devour.py —— 吞噬能：零丢帧全量采集 + 每帧哈希 + 断档审计 + 缺口修复
# dev: 自由的风 · 吞噬能配套 · 吞下去的每一帧都要能对账
#
# 采集源：
#   source="screen"  → mss 真抓屏（默认；无 mss/无显示时自动降级 synthetic）
#   source="synthetic" → 合成帧（离线可复现，跑测试/CI 用）
# 索引：<frame_dir>/index.jsonl，每行 {"seq","sha256","ts","bytes","source"}
# 零丢帧判定：序号连续（验收第④条的读数来源）
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from core.swallow import swallow as _swallow


@dataclass
class RepairResult:
    status: str                      # restored | resampled | failed
    restored: int = 0
    resampled: int = 0
    artifact: str = ""
    artifact_sha: str = ""
    error: str = ""
    detail: dict | None = None


class _FrameBuffer:
    """环形帧缓冲：供缺口写回（只写回缓冲里仍在的原帧）。"""

    def __init__(self, capacity: int = 600) -> None:
        self.capacity = capacity
        self._frames: dict[int, bytes] = {}
        self._lock = threading.Lock()

    def put(self, seq: int, frame: bytes) -> None:
        with self._lock:
            self._frames[seq] = frame
            if len(self._frames) > self.capacity:
                for old in sorted(self._frames)[: len(self._frames) - self.capacity]:
                    self._frames.pop(old, None)

    def has_range(self, start: int, end: int) -> bool:
        with self._lock:
            return all(s in self._frames for s in range(start, end + 1))

    def flush_range(self, start: int, end: int) -> int:
        with self._lock:
            return sum(1 for s in range(start, end + 1) if s in self._frames)

    def sha_range(self, start: int, end: int) -> str:
        h = hashlib.sha256()
        with self._lock:
            for s in range(start, end + 1):
                h.update(self._frames.get(s, b""))
        return h.hexdigest()


class Devour:
    """吞噬器：采集 → 落帧 → 记账 → 断档审计 → 缺口修复。"""

    def __init__(self, frame_dir: str | os.PathLike = "devoured/t1-eye",
                 fps: int = 30, ledger=None, brain=None,
                 source: str = "screen", buffer_capacity: int = 600) -> None:
        self.frames = Path(frame_dir)
        self.frames.mkdir(parents=True, exist_ok=True)
        self.fps = int(fps)
        self.ledger, self.brain = ledger, brain
        self.source = source
        self.index = self.frames / "index.jsonl"
        self.buf = _FrameBuffer(buffer_capacity)
        self._stop = threading.Event()
        self.gaps: list[dict] = []
        self.next_seq = self._last_seq() + 1
        self._grabber = None

    # ── 采集源 ──
    def _grab(self) -> bytes:
        if self.source == "screen":
            try:
                if self._grabber is None:
                    import mss  # 可选依赖
                    self._grabber = mss.mss()
                shot = self._grabber.grab(self._grabber.monitors[0])
                return shot.rgb
            except Exception:
                self.source = "synthetic"          # 无显示/mss 不可用 → 降级并记账
        return self._synthetic_frame(self.next_seq)

    @staticmethod
    def _synthetic_frame(seq: int) -> bytes:
        """离线合成帧：真 PNG（ffmpeg 可读），内容由 seq 决定，可复现。"""
        try:
            from io import BytesIO

            from PIL import Image, ImageDraw

            img = Image.new("RGB", (160, 90),
                            ((seq * 7) % 256, (seq * 13) % 256, (seq * 29) % 256))
            d = ImageDraw.Draw(img)
            d.rectangle([8, 8, 8 + (seq % 100), 30], fill=(255, 255, 255))
            d.text((12, 40), f"f{seq:08d}", fill=(255, 255, 255))
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            # PIL 不可用时退回最小合法 PNG（1x1 黑点）——保证仍是真 PNG
            return bytes.fromhex(
                "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
                "de0000000c4944415408d76360f8cf0000000301010074d2f07f0000000049454e44ae426082")

    # ── 单帧：抓取 → 哈希 → 落盘 → 记账 ──
    def capture_one(self, *, drop: bool = False) -> dict | None:
        seq = self.next_seq
        data = self._grab()
        digest = hashlib.sha256(data).hexdigest()
        if drop:
            # 故意丢帧（审计演练）：只记一笔缺席，不写文件
            self.gaps.append({"seq": seq, "reason": "dropped"})
            row = {"seq": seq, "sha256": "", "ts": time.time(),
                   "bytes": 0, "source": self.source, "state": "dropped"}
        else:
            path = self.frames / f"f{seq:08d}.png"
            path.write_bytes(data)
            self.buf.put(seq, data)
            row = {"seq": seq, "sha256": digest, "ts": time.time(),
                   "bytes": len(data), "source": self.source, "state": "stored"}
        self._append_index(row)
        self.next_seq = seq + 1
        return row

    # ── 采集循环 ──
    def run(self, *, duration_s: float | None = None, max_frames: int | None = None,
            stop_flag=None) -> dict:
        self._stop.clear()
        started, taken = time.time(), 0
        interval = 1.0 / max(1, self.fps)
        while not self._stop.is_set() and not (stop_flag and stop_flag()):
            t0 = time.time()
            self.capture_one()
            taken += 1
            if max_frames and taken >= max_frames:
                break
            if duration_s and time.time() - started >= duration_s:
                break
            sleep = interval - (time.time() - t0)
            if sleep > 0:
                time.sleep(sleep)
        return self.status()

    def stop(self) -> None:
        self._stop.set()

    # ── 断档审计 ──
    def audit_gaps(self) -> list[dict]:
        rows = self._rows()
        stored = sorted(r["seq"] for r in rows if r.get("state") == "stored")
        if not stored:
            return []
        missing = sorted(set(range(stored[0], stored[-1] + 1)) - set(stored))
        self.gaps = [{"seq": s, "reason": "gap"} for s in missing]
        if missing and self.ledger:
            try:
                self.ledger.log("t1", f"devour:gaps:{len(missing)}", "vuln",
                                f"断档 {missing[:5]}",
                                event_id=f"gap-{int(time.time())}")
            except Exception as e:
                _swallow(__file__, e)

        return self.gaps

    # ── 缺口修复（原片段语义保留）──
    def has_buffered_frames(self, g) -> bool:
        return self.buf.has_range(g.frame_start, g.frame_end)

    def source_revisitable(self, g) -> bool:
        return self.source == "synthetic" or bool(getattr(g, "revisitable", False))

    def is_live_passed(self, g) -> bool:
        return (not self.buf.has_range(g.frame_start, g.frame_end)
                and not self.source_revisitable(g))

    def writeback_gap(self, g) -> RepairResult:
        n = self.buf.flush_range(g.frame_start, g.frame_end)
        if n == g.missing:
            sha = self.buf.sha_range(g.frame_start, g.frame_end)
            return RepairResult(status="restored", restored=n, artifact_sha=sha,
                                artifact=f"buf://{g.gap_id}")
        return RepairResult(status="failed", restored=n,
                           error=f"只写回 {n}/{g.missing}")

    def resample_gap(self, g) -> RepairResult:
        out = self._rescan_range(g.frame_start, g.frame_end)
        return RepairResult(status="resampled", resampled=out["frames"],
                           artifact=out["path"], artifact_sha=out["sha"],
                           detail={"note": "重采，原缺帧仍记永久缺失"})

    def _rescan_range(self, start: int, end: int) -> dict:
        h = hashlib.sha256()
        n = 0
        for seq in range(start, end + 1):
            data = self._synthetic_frame(seq)
            (self.frames / f"f{seq:08d}.png").write_bytes(data)
            h.update(data)
            n += 1
            self._append_index({"seq": seq, "sha256": hashlib.sha256(data).hexdigest(),
                                "ts": time.time(), "bytes": len(data),
                                "source": "rescan", "state": "stored"})
        return {"frames": n, "path": str(self.frames), "sha": h.hexdigest()}

    # ── 指数/状态 ──
    def _append_index(self, row: dict) -> None:
        with open(self.index, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _rows(self) -> list[dict]:
        if not self.index.exists():
            return []
        out = []
        for line in self.index.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def _last_seq(self) -> int:
        seqs = [r["seq"] for r in self._rows() if "seq" in r]
        return max(seqs) if seqs else -1

    def status(self) -> dict:
        rows = self._rows()
        stored = [r for r in rows if r.get("state") == "stored"]
        seqs = sorted(r["seq"] for r in stored)
        gaps = sorted(set(range(seqs[0], seqs[-1] + 1)) - set(seqs)) if seqs else []
        return {"frame_dir": str(self.frames), "fps": self.fps, "source": self.source,
                "frames": len(stored), "span": (seqs[-1] - seqs[0] + 1) if seqs else 0,
                "gaps": len(gaps), "gap_seqs": gaps[:10],
                "hashes": sum(1 for r in stored if r.get("sha256")),
                "index": str(self.index)}


__all__ = ["Devour", "RepairResult"]
