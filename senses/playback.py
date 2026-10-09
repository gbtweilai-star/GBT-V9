# senses/playback.py —— 回放端：索引命中 → 拉段 → 校验 → 拼回视频打印
# dev: 自由的风 · 吞噬能配套 · 吞下去的，原样吐还给用户
import hashlib, json, os, shutil, subprocess, sys, tempfile, time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from senses.r2 import R2Unavailable, r2_client, r2_bucket  # noqa: E402
from core.swallow import swallow as _swallow

@dataclass
class Segment:
    seg_id: str; start_seq: int; end_seq: int; local_path: str
    size: int; sha256: str; frame_count: int; state: str
    bucket: str = ""; key: str = ""; ts: float = 0.0; mode: str = ""

class Player:
    """吞噬能的回放端：给用户'打印'还原吞下的影像"""
    def __init__(self, frame_dir: Path, cache_dir=None):
        self.frames  = Path(frame_dir)
        self.index   = self.frames / "segments.jsonl"
        self.cache   = Path(cache_dir or (self.frames / "_cache"))
        self.cache.mkdir(exist_ok=True)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client, self.mode = r2_client()
        return self._client

    # ── 索引查询：按帧序号 / 时间范围找段 ──
    def segments(self) -> list[Segment]:
        """段索引：同 seg_id 以最后一条为准（uploading → archived 的更新语义）。"""
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

    def locate(self, start_seq=None, end_seq=None, since=None, until=None) -> list[Segment]:
        """支持两种查询：按帧序号区间，或按时间戳区间"""
        out = []
        for s in self.segments():
            if start_seq is not None and s.end_seq < start_seq: continue
            if end_seq   is not None and s.start_seq > end_seq: continue
            if since     is not None and s.ts < since: continue
            if until     is not None and s.ts > until: continue
            out.append(s)
        return out

    # ── 取段：本地命中直读，未命中从 R2 拉回并校验 ──
    def fetch(self, seg: Segment) -> Path | None:
        local = Path(seg.local_path)
        if local.exists() and self._verify(local, seg.sha256):
            return local                                   # 本地命中
        cached = self.cache / f"{seg.seg_id}.mkv"
        if cached.exists() and self._verify(cached, seg.sha256):
            return cached                                  # 缓存命中
        if seg.state != "archived" or not seg.key:
            return None                                    # 从未成功归档，拉不回来
        try:
            self.client.download_file(seg.bucket or r2_bucket(),
                                      seg.key, str(cached))
        except Exception as e:
            print(f"[playback] 拉取失败 {seg.seg_id}: {e}")
            return None
        if not self._verify(cached, seg.sha256):
            cached.unlink(missing_ok=True)                 # 哈希不符 → 丢弃，绝不吐坏帧
            print(f"[playback] 校验失败 {seg.seg_id}，已丢弃")
            return None
        return cached

    # ── 打印：把若干段拼成一条完整视频交给用户 ──
    def print_video(self, segs: list[Segment], out="restored.mp4",
                    lossless=False, fps=30):
        parts = [p for s in segs if (p := self.fetch(s))]
        if not parts:
            raise RuntimeError("没有可取回的段（本地与R2均未命中）")
        # 用 concat demuxer 拼接；段已是同编码同参数，直接流拷贝最快
        listfile = self.cache / "_concat.txt"
        listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
        if lossless:
            cmd_lossless = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(listfile), "-c", "copy", out]   # 无损直拼
            try:
                subprocess.run(cmd_lossless, check=True, capture_output=True)
                return out, len(parts)
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                _swallow(__file__, e)
                                              # 段参数不完全一致 → 走重编码
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
               "-i", str(listfile), "-c:v", "libx264", "-crf", "12",
               "-pix_fmt", "yuv420p", out]                        # 给用户看的 MP4
        subprocess.run(cmd, check=True, capture_output=True)
        return out, len(parts)

    # ── 面板 API：预览（转码小片，落 _preview 缓存）──
    def preview(self, seg: Segment, preview_dir=None, crf=23) -> Path | None:
        out_dir = Path(preview_dir or (self.frames / "_preview"))
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{seg.seg_id}.mp4"
        if out.exists() and out.stat().st_size > 0:
            return out                                   # 命中预览缓存
        src = self.fetch(seg)
        if not src:
            return None
        try:
            subprocess.run(["ffmpeg", "-y", "-i", str(src),
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                           check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            shutil.copyfile(src, out)                    # 无 ffmpeg：直接给原件（浏览器多半能播 mkv? 至少可下载）
        return out

    # ── 面板 API：导出（多段拼接，落 panel/exports）──
    def export(self, segs: list[Segment], out: str | os.PathLike,
               lossless: bool = False) -> tuple[str, int]:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        parts = [p for s in segs if (p := self.fetch(s))]
        if not parts:
            raise RuntimeError("没有可取回的段（本地与R2均未命中）")
        listfile = self.cache / "_concat.txt"
        listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
        codec = ["-c", "copy"] if lossless else \
                ["-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart"]
        try:
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(listfile), *codec, str(out)],
                           check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            # copy 拼不上（参数不完全一致）→ 重编码回退
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(listfile), "-c:v", "libx264", "-crf", "12",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                           check=True, capture_output=True)
        finally:
            listfile.unlink(missing_ok=True)
        return str(out), len(parts)

    # ── 打印：逐帧原样导出（要绝对原始时用）──
    def print_frames(self, segs: list[Segment], outdir="restored_frames"):
        Path(outdir).mkdir(exist_ok=True)
        n = 0
        for s in segs:
            p = self.fetch(s)
            if not p: continue
            subprocess.run(["ffmpeg", "-y", "-i", str(p),
                            f"{outdir}/f%08d.png"], check=True, capture_output=True)
            n += s.frame_count
        return outdir, n

    # ── 打印：联络表（一页概览）──
    def print_contact_sheet(self, segs: list[Segment], out="contact_sheet.png",
                            cols=8, thumb=(160, 90)):
        from PIL import Image
        frames = []
        for s in segs:
            p = self.fetch(s)
            if not p: continue
            with tempfile.TemporaryDirectory() as td:
                # 用 select 按帧号抽样（fps 滤镜在短段上会抽空 → 0 帧）
                step = max(1, s.frame_count // max(1, cols))
                subprocess.run(["ffmpeg", "-y", "-i", str(p),
                                "-vf", f"select='not(mod(n\\,{step}))'",
                                "-vsync", "0", "-frames:v", str(max(1, cols)),
                                f"{td}/t%03d.png"], check=True, capture_output=True)
                for f in sorted(Path(td).glob("t*.png")):
                    try:
                        frames.append(Image.open(f).convert("RGB").resize(thumb))
                    except Exception:
                        continue
        if not frames: raise RuntimeError("没有帧可用于联络表")
        rows = -(-len(frames) // cols)
        sheet = Image.new("RGB", (cols*thumb[0], rows*thumb[1]), "black")
        for i, im in enumerate(frames):
            sheet.paste(im, ((i % cols)*thumb[0], (i//cols)*thumb[1]))
        sheet.save(out)
        return out

    @staticmethod
    def _verify(path: Path, expect: str) -> bool:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""): h.update(c)
        return h.hexdigest() == expect
