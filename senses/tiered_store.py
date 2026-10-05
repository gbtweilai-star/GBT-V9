# senses/tiered_store.py —— 分层存储：水位触发归档 + 帧段封装 + R2 上传
# dev: 自由的风 · 吞噬能配套 · 帧段无损封存，本地只留索引
import json, os, shutil, subprocess, sys, threading, time, zipfile
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from senses.r2 import (R2Unavailable, r2_bucket, r2_client, sha256_file)  # noqa: E402

@dataclass
class Segment:
    """一个帧段：一段连续帧封装成的无损 MKV"""
    seg_id: str            # seg-<起始seq>-<结束seq>
    start_seq: int
    end_seq: int
    local_path: str
    size: int
    sha256: str
    frame_count: int
    state: str             # sealed | uploading | archived | failed
    bucket: str = ""
    key: str = ""
    ts: float = 0.0

class TieredStore:
    """热层(本地SSD) → 温层(R2) 的分层管家"""
    def __init__(self, frame_dir: Path, ledger=None, brain=None,
                 high=0.85, low=0.60, bucket=None, seg_frames=1800,
                 keep_last_segments=2):
        self.frames  = Path(frame_dir)          # 吞噬器落帧目录 (f00000000.png ...)
        self.archive = self.frames / "_segments"  # 封装后的段存放处
        self.archive.mkdir(exist_ok=True)
        self.index   = self.frames / "segments.jsonl"
        self.ledger, self.brain = ledger, brain
        self.high, self.low = high, low         # 水位：高于 high 归档，低于 low 停手
        self.bucket = bucket or os.environ.get("R2_BUCKET_NAME", "tentacle-archive")
        self.seg_frames = seg_frames            # 每段帧数（1800帧 ≈ 60s@30fps）
        self.keep_last_segments = keep_last_segments  # 本地保留最后 N 段供快速回看
        self.fps = int(os.getenv("DEVOUR_FPS", "30"))
        self._lock = threading.Lock()
        self._running = False

    # ── 水位探针 ──
    def disk_usage(self) -> float:
        u = shutil.disk_usage(self.frames)
        return u.used / u.total

    # ── 帧段封装：PNG序列 → 无损 FFV1/MKV ──
    def seal_segment(self, start_seq: int, end_seq: int) -> Segment:
        seg_id = f"seg-{start_seq:08d}-{end_seq:08d}"
        out = self.archive / f"{seg_id}.mkv"
        # FFV1 = 归档级无损视频编码，装在 MKV 里；没有 ffmpeg 时回退为帧包 zip（同样无损可回放）
        try:
            subprocess.run([
                "ffmpeg", "-y", "-framerate", str(self.fps),
                "-start_number", str(start_seq),
                "-i", str(self.frames / "f%08d.png"),
                "-frames:v", str(end_seq - start_seq + 1),
                "-c:v", "ffv1", "-level", "3", "-g", "1",
                "-pix_fmt", "bgr0", str(out),
            ], check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            out = self.archive / f"{seg_id}.frames.zip"
            with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
                for seq in range(start_seq, end_seq + 1):
                    z.write(self.frames / f"f{seq:08d}.png", arcname=f"f{seq:08d}.png")
        digest = sha256_file(out)
        return Segment(seg_id, start_seq, end_seq, str(out), out.stat().st_size,
                       digest, end_seq - start_seq + 1, "sealed", ts=time.time())

    # ── 上传 R2：分段上传 + 校验 + 幂等 ──
    def upload_segment(self, seg: Segment):
        key = f"{self.frames.name}/{seg.seg_id}.mkv"
        seg.state, seg.bucket, seg.key = "uploading", self.bucket, key
        self._log_index(seg)
        try:
            client, mode = r2_client()
            seg.mode = mode  # type: ignore[attr-defined]
            client.upload_file(seg.local_path, self.bucket, key,
                ExtraArgs={"Metadata": {"sha256": seg.sha256,
                                        "frames": str(seg.frame_count)}})
            # 回读校验：远端大小一致才算归档成功
            head = client.head_object(Bucket=self.bucket, Key=key)
            if head["ContentLength"] != seg.size:
                raise IOError(f"远端大小不符 {head['ContentLength']} != {seg.size}")
            seg.state = "archived"
        except Exception as e:
            seg.state = "failed"
            if self.brain:
                self.brain.ask("t1", "r2_upload_failed", f"{seg.seg_id}: {e}")
        self._log_index(seg)
        return seg

    # ── 归档一段后：删本地帧 + 超龄段 ──
    def evict(self, seg: Segment):
        for seq in range(seg.start_seq, seg.end_seq + 1):
            (self.frames / f"f{seq:08d}.png").unlink(missing_ok=True)
        # 本地只留最后 N 段，其余段文件也删（索引仍在）
        sealed = self._segments()
        for old in sealed[:-self.keep_last_segments] if sealed else []:
            if old.state == "archived" and Path(old.local_path).exists():
                Path(old.local_path).unlink()
        if self.ledger:
            self.ledger.log("t1", f"archive:{seg.seg_id}", "scanned",
                            f"{seg.frame_count}帧 {seg.size//1024//1024}MB → R2")

    # ── 主循环：水位触发，循环归档直到降到 low ──
    def run(self, stop_flag=None):
        self._running = True
        while self._running and not (stop_flag and stop_flag()):
            if self.disk_usage() > self.high:
                seg = self._oldest_unsealed_batch()
                if seg:
                    self.upload_segment(seg)
                    if seg.state == "archived":
                        self.evict(seg)
                    else:
                        time.sleep(5)          # 上传失败退避，下轮重试
                else:
                    time.sleep(1)
            else:
                time.sleep(2)

    def stop(self): self._running = False

    # ── 对外：跑一轮归档（面板/验收/测试用；force=True 忽略水位）──
    def archive_once(self, force: bool = False) -> dict:
        if not force and self.disk_usage() <= self.high:
            return {"skipped": True, "watermark": self.disk_usage()}
        seg = self._oldest_unsealed_batch()
        if seg is None:
            return {"skipped": True, "reason": "帧数不足一个段"}
        self.upload_segment(seg)
        if seg.state == "archived":
            self.evict(seg)
        return {"seg_id": seg.seg_id, "state": seg.state,
                "bytes": seg.size, "key": seg.key}

    # ── 对外：状态（面板/验收真读数）──
    def status(self) -> dict:
        segs = self._segments()
        return {
            "frame_dir": str(self.frames),
            "watermark": round(self.disk_usage(), 4),
            "high": self.high, "low": self.low,
            "seg_frames": self.seg_frames,
            "keep_last_segments": self.keep_last_segments,
            "frames_on_disk": len(list(self.frames.glob("f*.png"))),
            "segments": {
                "total": len(segs),
                "archived": sum(1 for s in segs if s.state == "archived"),
                "failed": sum(1 for s in segs if s.state == "failed"),
                "local": sum(1 for s in segs if Path(s.local_path).exists()),
            },
            "bucket": self.bucket,
        }

    # ── 内部工具 ──
    def _oldest_unsealed_batch(self):
        """从最旧帧开始取连续段：≥seg_frames 封满一段；不足但有断档后继 → 封短段
        （缺口本身由 devour 的断档审计负责，不许让归档永久卡死）。"""
        seqs = sorted(int(p.stem[1:]) for p in self.frames.glob("f*.png"))
        if not seqs:
            return None
        start = seqs[0]
        run = [start]
        for nxt in seqs[1:]:
            if nxt == run[-1] + 1:
                run.append(nxt)
            else:
                break
        if len(run) >= self.seg_frames:
            end = run[self.seg_frames - 1]
        elif len(run) < len(seqs):        # 连续段后还有帧（被断档隔开）→ 先封这段，别卡死
            end = run[-1]
        else:
            return None                    # 尾部还在增长，等够一段再封
        return self.seal_segment(start, end)

    def _segments(self):
        """同 seg_id 以最后一条为准（uploading → archived 的更新语义）。"""
        if not self.index.exists(): return []
        latest: dict[str, Segment] = {}
        for line in self.index.read_text().splitlines():
            if not line.strip():
                continue
            try:
                seg = Segment(**json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
            latest[seg.seg_id] = seg
        return sorted(latest.values(), key=lambda s: s.start_seq)

    def _log_index(self, seg):
        with self._lock, open(self.index, "a") as f:
            f.write(json.dumps(asdict(seg)) + "\n")

    @staticmethod
    def _sha256(path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
        return h.hexdigest()
