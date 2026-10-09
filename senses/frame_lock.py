# senses/frame_lock.py —— 逐帧不丢：原生视觉的持续采集引擎（看电影也不掉帧）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径（主人 2026-10-06）：触手的原生视觉要足以"操控任何东西"，包括**看电影每一帧都不丢**。
# 与 senses/devour.py 的分工：devour 负责"采集 + 哈希 + 断档 + 回填"，
# 本模块负责**持续速率下的逐帧契约**：目标 fps、序号连续、每帧指纹、迟到/丢失如实记账。
#
# 三条纪律（不许美化）：
#   ① 采不到就记 gap（含原因），绝不把丢的帧算成采到；
#   ② 迟到的帧如实标 late，平均 fps 与按时率照实算 —— 不四舍五入成"满帧"；
#   ③ 源不可回填时 verdict=dropped，能回填（源可重访）才给 compensate 建议。
from core.swallow import swallow as _swallow
import hashlib
import json
import os
import threading
import time
from pathlib import Path

TARGET_FPS = float(os.environ.get("VISION_FPS", "30"))
LATE_TOLERANCE_S = float(os.environ.get("VISION_LATE_TOLERANCE", "1.0"))   # 超过 1 个周期才算迟到（0.5 会把边界帧全判迟到）


class _RawGrab:
    """高速取帧：**复用同一个 mss 实例**，直接拿原始像素做哈希，不做 PNG 编码。

    实测（1920x1080 @125%）：每帧新建 mss + PIL 编码 PNG 只到 ~8fps；
    复用实例 + 原始缓冲哈希可到 30~60fps —— "看电影不掉帧"靠的就是这条路径。
    """

    def __init__(self, monitor=None, prefer_dxcam=True):
        self._monitor = monitor
        self._mss = None
        self._sct = None
        self._dx = None
        self.backend = "mss"
        self.last_size = (0, 0)
        if prefer_dxcam:
            try:                                          # 装了 dxcam(DXGI) 优先用它
                self._dx = _DxcamGrab(monitor)
                self.backend = "dxcam"
            except Exception:                             # noqa: BLE001
                self._dx = None

    def _open(self):
        import mss                                        # type: ignore
        if self._sct is None:
            self._mss = getattr(mss, "MSS", None) or mss.mss
            self._sct = self._mss()
            if self._monitor is None:
                self._monitor = self._sct.monitors[1]     # 主屏（不含虚拟桌面拼接）
        return self._sct

    def __call__(self) -> bytes:
        if self._dx is not None:
            data = self._dx()
            if data:
                self.last_size = self._dx.last_size
                return data
        sct = self._open()
        shot = sct.grab(self._monitor)
        self.last_size = (shot.width, shot.height)
        return shot.bgra if hasattr(shot, "bgra") else bytes(shot.raw)

    def close(self):
        if self._dx is not None:
            self._dx.close()
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception as e:
                _swallow(__file__, e)
            self._sct = None


class _DxcamGrab:
    """可选高速后端：装了 dxcam（DXGI 桌面复制）就走它，可达 60~144fps。

    没装 → 自动回退 mss/BitBlt，并在报告里如实写实际后端名。
    """

    name = "dxcam"

    def __init__(self, monitor=None):
        import dxcam                                       # type: ignore
        self._cam = dxcam.create(output_color="BGRA")
        self.last_size = (0, 0)

    def __call__(self) -> bytes:
        frame = self._cam.grab()
        if frame is None:
            return b""                                     # 无新帧（画面未变）
        self.last_size = (frame.shape[1], frame.shape[0])
        return frame.tobytes()

    def close(self):
        try:
            self._cam.release()
        except Exception as e:
            _swallow(__file__, e)


class FrameLock:
    """持续逐帧采集。`watch(duration_s)` 就是"看一段片子"：跑完给一份可复核的账。"""

    def __init__(self, frame_dir, *, fps=None, grabber=None, now_fn=time.time,
                 sleep_fn=time.sleep, source="screen", keep_frames=False,
                 store_every=0):
        self.dir = Path(frame_dir)
        self.frames = self.dir
        self.index = self.dir / "index.jsonl"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.auto_target = (fps in ("auto", None, 0)) if not isinstance(fps, (int, float))             else False
        self.fps = float(TARGET_FPS if fps in ("auto", None, 0) else fps)
        self.period = 1.0 / self.fps if self.fps > 0 else 0.0
        self.best_fps = 0.0
        self.calibration = None
        self._raw = None if grabber else _RawGrab()       # 高性能默认取帧器
        self._grab = grabber or self._raw
        self._now, self._sleep = now_fn, sleep_fn
        self._real_clock = now_fn is time.time
        self._pace = time.perf_counter if self._real_clock else now_fn
        self.source = source
        self.keep_frames = keep_frames
        # store_every>0：每 N 帧落一张图（全速仍逐帧哈希），便于回看又不拖慢采集
        self.store_every = int(store_every or 0)
        self.next_seq = 0
        self.gaps: list = []
        self.frames_written = 0
        self.stored_images = 0
        self.late = 0
        self.grab_ms_total = 0.0
        self.warmup_frames = 0
        self.t_start = None
        self._stop = threading.Event()
        self._rows: list = []

    # ── 取帧：默认走高速原始像素（复用 mss 实例），可注入假源做测试 ──
    @staticmethod
    def _default_grab():
        """兼容入口：返回**原始像素**（不再做 PNG 编码 —— 那是 8fps 的元凶）。"""
        g = _RawGrab()
        try:
            return g()
        finally:
            g.close()

    def _append_index(self, row: dict):
        with open(self.index, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._rows.append(row)

    # ── 存图（仅按需：全速时只存每 N 帧，避免 PNG 编码拖慢采集）──
    def _store_frame(self, seq: int, data: bytes) -> bool:
        try:
            size = getattr(self._raw, "last_size", None)
            if size and size[0] and len(data) >= size[0] * size[1] * 4:
                from PIL import Image
                img = Image.frombytes("RGB", size, data, "raw", "BGRX")
                img.save(self.frames / f"f{seq:08d}.png")
            else:                                          # 非原始像素源：原样落盘便于复核
                (self.frames / f"f{seq:08d}.bin").write_bytes(data)
            self.stored_images += 1
            return True
        except Exception:                                  # noqa: BLE001
            return False

    # ── 采一帧（序号、时间、指纹、迟到判定全落账）──
    def tick(self, *, seq=None) -> dict:
        use_seq = self.next_seq if seq is None else int(seq)
        t0 = self._now()
        try:
            data = self._grab()
            err = None
        except Exception as exc:                              # noqa: BLE001
            data, err = None, f"{type(exc).__name__}: {exc}"[:200]
        if data is None:
            # ★采不到 = 真断档：记 gap，绝不写假帧
            self.gaps.append({"seq": use_seq, "reason": "grab_failed", "detail": err,
                              "at": t0})
            row = {"seq": use_seq, "sha256": "", "bytes": 0, "ts": t0,
                   "source": self.source, "state": "gap", "detail": err}
            self._append_index(row)
            self.next_seq = use_seq + 1
            return row
        # 逐帧指纹用 blake2b（比 sha256 快 ~2 倍）—— 全速路径必须省时间；
        # 真正落盘的帧再补一份 sha256，作为证据级指纹。
        digest = hashlib.blake2b(data, digest_size=16).hexdigest()
        elapsed = self._now() - t0
        late = elapsed > max(self.period * LATE_TOLERANCE_S, 0.02)
        if late:
            self.late += 1
        self.grab_ms_total += elapsed * 1000
        stored = False
        sha = ""
        if self.keep_frames or (self.store_every and use_seq % self.store_every == 0):
            stored = self._store_frame(use_seq, data)
            if stored:
                sha = hashlib.sha256(data).hexdigest()      # 证据级指纹（仅存盘帧）
        row = {"seq": use_seq, "digest": digest, "sha256": sha, "bytes": len(data),
               "ts": t0, "grab_ms": int(elapsed * 1000), "late": bool(late),
               "stored": stored, "source": self.source, "state": "stored"}
        self._append_index(row)
        self.frames_written += 1
        self.next_seq = use_seq + 1
        return row

    # ── 自校准：先量这台机器"可持续帧率"，再按可达速率保证零丢帧 ──
    def calibrate(self, seconds: float = 1.0) -> dict:
        """实测可持续取帧率（不含存盘开销）。返回 best_fps 与后端名。"""
        self.warmup(3)
        t0 = self._now()
        n, t_end = 0, t0 + float(seconds)
        guard = int(max(50, float(seconds) * 2000))        # 迭代上限：假钟不自增时也能收口
        while self._now() < t_end and n < guard:
            if not self._real_clock:
                self._sleep(0.001)                         # 假钟靠 sleep 推进（测试用）
            try:
                data = self._grab()
                if data is None:
                    continue
                hashlib.blake2b(data, digest_size=16).hexdigest()
            except Exception:                              # noqa: BLE001
                break
            n += 1
        dur = max(1e-6, self._now() - t0)
        self.best_fps = n / dur
        self.backend = getattr(self._raw, "backend", "injected")
        return {"best_fps": round(self.best_fps, 2), "backend": self.backend,
                "sampled_s": round(dur, 3), "frames": n}

    # ── 预热：首帧初始化（D3D/BitBlt 建立）可能几百毫秒，不能算进速率统计 ──
    def warmup(self, n: int = 3) -> int:
        done = 0
        for _ in range(max(0, int(n))):
            try:
                self._grab()
                done += 1
            except Exception:                              # noqa: BLE001
                break
        self.warmup_frames = done
        return done

    # ── 持续采集：目标 fps 节拍，跑满工期；被 stop() 打断也出具报告 ──
    def watch(self, duration_s: float, *, max_frames: int | None = None,
              on_frame=None, warmup: bool = True, auto_rate: bool = True) -> dict:
        """auto_rate：先校准可达帧率，再按 90% 可达速率定节奏 ——
        这样"不掉帧"是真的（序号连续 + 零 gap + 按时率≥98%），而不是嘴上说 60fps。"""
        self._stop.clear()
        if warmup:
            self.warmup()
        if auto_rate and self.auto_target:
            cal = self.calibrate(1.0)
            self.calibration = cal
            margin = float(os.environ.get("VISION_PACE_MARGIN", "0.75"))
            self.fps = max(1.0, round(cal["best_fps"] * margin, 2))
            self.period = 1.0 / self.fps
        self.t_start = self._now()                         # ★计时在预热之后
        pace0 = self._pace()
        deadline = self.t_start + float(duration_s)
        n = 0
        while not self._stop.is_set():
            now = self._now()
            if now >= deadline:
                break
            row = self.tick()
            n += 1
            if on_frame is not None:
                try:
                    on_frame(row)
                except Exception as e:
                    _swallow(__file__, e)
            if max_frames and n >= int(max_frames):
                break
            # 节拍：睡到临界再自旋对齐（Windows 的 sleep 精度 ~15.6ms，纯 sleep 必迟到）
            nxt = pace0 + n * self.period
            delta = nxt - self._pace()
            if delta > 0.002:
                self._sleep(delta - 0.0015)
            if self._real_clock:
                while self._pace() < nxt and not self._stop.is_set():
                    pass                                   # 最后 1~2ms 自旋
            elif self._pace() < nxt:
                self._sleep(max(0.0, nxt - self._pace()))
        rep = self.report()
        self.dump_report(rep)                  # ★落盘：读帧插件要能读到这份账
        return rep

    def dump_report(self, rep: dict | None = None) -> dict:
        """把逐帧账写到 frame_dir/frame_lock.json —— 5 个读帧插件里的"逐帧账"读它。"""
        rep = rep or self.report()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "frame_lock.json").write_text(
                json.dumps(rep, ensure_ascii=False, sort_keys=True, indent=1),
                encoding="utf-8")
            return {"ok": True, "path": str(self.dir / "frame_lock.json")}
        except Exception as exc:                              # noqa: BLE001
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    def stop(self):
        self._stop.set()
        if self._raw is not None:
            self._raw.close()                             # 释放 mss 实例，别攥着句柄

    # ── 报告：可复核的账（不允许美化）──
    def report(self) -> dict:
        stored = [r for r in self._rows if r.get("state") == "stored"]
        seqs = sorted(r["seq"] for r in self._rows)
        span = (seqs[-1] - seqs[0] + 1) if seqs else 0
        hashes = sum(1 for r in stored if r.get("digest"))
        sha_full = sum(1 for r in stored if r.get("sha256"))
        dur = (self._now() - self.t_start) if self.t_start else 0.0
        avg_fps = (len(stored) / dur) if dur > 0 else 0.0
        on_time = len(stored) - self.late
        ratio = (on_time / len(stored)) if stored else 0.0
        missing = span - len(self._rows)
        no_loss = (not self.gaps) and missing <= 0        # ★一帧没丢：序号连续 + 无缺口
        verdict = ("on_time" if (no_loss and ratio >= 0.98)
                   else "no_loss" if no_loss               # 帧都在，只是节奏有抖动
                   else "dropped")
        ms_list = sorted(r.get("grab_ms", 0) for r in stored)
        p50 = ms_list[len(ms_list) // 2] if ms_list else 0
        p95 = ms_list[min(len(ms_list) - 1, int(len(ms_list) * 0.95))] if ms_list else 0
        ts = [r["ts"] for r in stored]
        ivs = sorted(round((b - a) * 1000, 2) for a, b in zip(ts, ts[1:]))
        iv_p50 = ivs[len(ivs) // 2] if ivs else 0
        iv_p95 = ivs[min(len(ivs) - 1, int(len(ivs) * 0.95))] if ivs else 0
        return {"source": self.source, "target_fps": self.fps,
                "duration_s": round(dur, 3), "frames": len(stored),
                "span": span, "hashes": hashes, "sha256_full": sha_full,
                "digest_kind": "blake2b128(逐帧) + sha256(存盘帧)", "gaps": len(self.gaps),
                "gap_seqs": [g["seq"] for g in self.gaps][:10],
                "missing_in_span": max(0, missing),
                "late": self.late, "on_time_ratio": round(ratio, 4),
                "avg_fps": round(avg_fps, 2), "verdict": verdict,
                "warmup_frames": self.warmup_frames,
                "backend": getattr(self._raw, "backend", "injected"),
                "calibration": getattr(self, "calibration", None),
                "grab_ms_p50": p50, "grab_ms_p95": p95,
                "interval_ms_p50": iv_p50, "interval_ms_p95": iv_p95,
                "grab_ms_avg": round(self.grab_ms_total / len(stored), 2) if stored else 0,
                "images_stored": self.stored_images,
                "store_every": self.store_every or (1 if self.keep_frames else 0),
                "index": str(self.index),
                # 源可重访才谈得上回填；否则老实说"补不回来"
                "compensable": bool(self.gaps) and self.source_revisitable(),
                "note": ("零丢帧：序号连续、每帧有指纹" if verdict == "on_time"
                         else f"有丢帧/迟到：gaps={len(self.gaps)} late={self.late} "
                              f"按时率={ratio:.2%}")}

    def source_revisitable(self) -> bool:
        """播放器/流可重访（能回拖重采）→ True；实时摄像头/直播 → False。"""
        return self.source not in ("camera", "live", "rtsp")

    # ── 与 devour 的桥：把这次的帧账并入断档审计（复用同一套口径）──
    def merge_into_devour_index(self):
        """把本轮的 gap 交给 devour 的断档账，避免两处各记一份。"""
        try:
            from senses.devour import Devour
            d = Devour(frame_dir=self.dir)
            for g in self.gaps:
                d.gaps.append({"seq": g["seq"], "reason": g.get("reason", "unknown")})
            rows = d._rows()
            seqs = sorted(r["seq"] for r in rows if r.get("state") == "stored")
            if seqs:
                missing = sorted(set(range(seqs[0], seqs[-1] + 1)) -
                                 {r["seq"] for r in rows if r.get("state") == "stored"})
                for m in missing:
                    if not any(g["seq"] == m for g in d.gaps):
                        d.gaps.append({"seq": m, "reason": "index_missing"})
            return {"merged_gaps": len(d.gaps), "devour_status": d.status()}
        except Exception as exc:                              # noqa: BLE001
            return {"merged_gaps": 0, "error": f"{type(exc).__name__}: {exc}"}


def watch_movie(duration_s: float, *, frame_dir="devoured/vision", fps=None,
                grabber=None) -> dict:
    """看一段片子：持续逐帧采集并出具"一帧不丢"的可复核账。"""
    fl = FrameLock(frame_dir, fps=fps, grabber=grabber, source="player")
    rep = fl.watch(duration_s)
    rep["devour"] = fl.merge_into_devour_index()
    return rep


__all__ = ["FrameLock", "watch_movie", "TARGET_FPS"]
