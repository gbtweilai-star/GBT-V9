# devour.py —— 吞噬能 · dev: 自由的风 · 万像皆吞，原样吐还
import os, time, zlib, hashlib, threading, json
from pathlib import Path
import mss, numpy as np
from PIL import Image

class Devourer:
    """吞噬通道：与钩子(抽样)并行，全量无损能力"""
    def __init__(self, tentacle_id, ledger, brain, outdir="devoured"):
        self.tid, self.ledger, self.brain = tentacle_id, ledger, brain
        self.out = Path(outdir) / tentacle_id
        self.out.mkdir(parents=True, exist_ok=True)
        self.index_path = self.out / "index.jsonl"          # 帧索引：seq→文件+哈希
        self._seq, self._lock = 0, threading.Lock()
        self._devouring = False

    # ── 吞噬主循环：独占线程，绝不与钩子共用采样器（防止钩子节流波及吞噬）──
    def start(self, fps=None):
        self._devouring = True
        def loop():
            last_t = time.perf_counter()
            while self._devouring:
                frame = self._grab()                          # 眼睛：全量抓取
                self._swallow(frame)                          # 吞下：落盘+索引+哈希
                # 精确帧率节拍，宁可稍慢也不跳帧
                next_t = last_t + 1.0 / (fps or 60)
                sleep = next_t - time.perf_counter()
                if sleep > 0: time.sleep(sleep)
                last_t = next_t
        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self._devouring = False

    def _grab(self):
        with mss.mss() as s:
            shot = s.grab(s.monitors[1])
            img = np.ascontiguousarray(
                Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
        return img

    # ── 吞下：PNG无损压缩（zlib级别0优先速度）+ 哈希 + 索引 ──
    def _swallow(self, frame):
        with self._lock:
            seq, self._seq = self._seq, self._seq + 1
        f = self.out / f"f{seq:08d}.png"
        Image.fromarray(frame).save(f, compress_level=1)      # PNG=无损
        digest = hashlib.sha256(frame.tobytes()).hexdigest()
        with open(self.index_path, "a") as ix:
            ix.write(json.dumps({"seq": seq, "file": f.name, "ts": time.time(),
                                 "sha256": digest, "size": frame.shape[:2]}) + "\n")
        self.ledger.log(self.tid, f"frame:{seq}", "scanned", digest[:16])

    # ── 丢帧检测：序号断档 = 丢失，立即上报主脑 ──
    def audit(self):
        seqs, broken = [], []
        for line in open(self.index_path):
            r = json.loads(line); seqs.append(r["seq"])
            img = np.array(Image.open(self.out / r["file"]))
            if hashlib.sha256(img.tobytes()).hexdigest() != r["sha256"]:
                broken.append(r["seq"])                       # 帧损坏
        gaps = sorted(set(range(min(seqs), max(seqs)+1)) - set(seqs))
        if gaps or broken:
            verdict = self.brain.ask(self.tid, "devour_integrity",
                                     f"丢帧={gaps} 损坏帧={broken}")
            self.ledger.log(self.tid, "devour", "blocked", f"gaps={gaps} broken={broken}")
        return {"gaps": gaps, "broken": broken,
                "total": len(seqs), "lossless": not gaps and not broken}
