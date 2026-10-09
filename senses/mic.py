# senses/mic.py —— 实时麦克风采集 · VAD切段 · 连续转写 · 关键词触发
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计（经复核）:
#   - sounddevice(PortAudio) 16kHz 单声道 int16，回调只写环形缓冲，不做IO/网络
#   - WebRTC VAD 20ms 帧切段：静音600ms收段，最短0.8s，最长15s，前滚200ms
#   - 有界队列 + 单转写线程；满则显式记丢段/过载，不静默丢
#   - 每段单调 seq + 唯一ID，DB 唯一键防重复
#   - 关键词 NFKC+大小写折叠+标点归一后短语匹配，冷却去重，命中发主脑
#   - 默认只留转写文本，临时音频处理完即删；提供停止开关
from core.swallow import swallow as _swallow
import os, re, time, uuid, queue, threading, unicodedata, wave, tempfile
from pathlib import Path
from dataclasses import dataclass, field

from senses.sqldialect import is_pg, txn

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    np = None; sd = None

try:
    import webrtcvad
except ImportError:
    webrtcvad = None

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000        # 320

# ── 分段参数（thinker 校准）──
SILENCE_MS = 600          # 静音多久收段
MIN_SEG_MS = 800          # 最短段
MAX_SEG_MS = 15000        # 最长段
PREROLL_MS = 200          # 段首前滚


# ─────────────────────────────────────────────────────────────
# 关键词表：归一化后做短语匹配；命中发主脑（不直接执行高风险操作）
# ─────────────────────────────────────────────────────────────
DEFAULT_KEYWORDS = {
    "紧急": {"level": "critical", "action": "alert"},
    "救命": {"level": "critical", "action": "alert"},
    "错误": {"level": "warning",  "action": "notify"},
    "失败": {"level": "warning",  "action": "notify"},
    "停止": {"level": "warning",  "action": "notify"},
}

def normalize(text: str) -> str:
    """NFKC + 大小写折叠 + 去标点空白"""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = re.sub(r"[\s\W_]+", "", t)
    return t

def match_keywords(text: str, keywords: dict):
    """返回命中的关键词列表（短语包含匹配）"""
    norm = normalize(text)
    if not norm: return []
    hits = []
    for kw, meta in keywords.items():
        if normalize(kw) in norm:
            hits.append({"keyword": kw, **meta})
    return hits


# ─────────────────────────────────────────────────────────────
@dataclass
class Segment:
    seq: int
    start_ts: float
    end_ts: float
    pcm: bytes
    sample_rate: int = SAMPLE_RATE
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

class MicCapture:
    """实时麦克风 → VAD切段 → 有界队列 → 转写 → 关键词触发"""
    def __init__(self, tentacle_id, ledger, adapter, keywords=None,
                 queue_size=32, device=None, keep_audio=False,
                 vad_aggr=2, brain=None, on_keyword=None):
        if sd is None:
            raise RuntimeError("缺 sounddevice，请 pip install sounddevice numpy")
        self.tid, self.led, self.voice = tentacle_id, ledger, adapter
        self.keywords = keywords or DEFAULT_KEYWORDS
        self.brain, self.on_keyword = brain, on_keyword
        self.keep_audio = keep_audio or os.environ.get("MIC_KEEP_AUDIO") == "1"
        self.device, self.queue_size = device, queue_size
        self.q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self.stats = {"segments": 0, "transcribed": 0, "failed": 0,
                      "dropped": 0, "overflow": 0, "keywords": 0}
        self._ring = np.zeros(SAMPLE_RATE * 2, dtype=np.int16)   # 2秒环形缓冲
        self._ring_len = 0
        self._vad = webrtcvad.Vad(vad_aggr) if webrtcvad else None
        self._cooldown = {}                                  # 关键词冷却
        self._init_tables()
        self._audio_dir = Path(os.environ.get("MIC_AUDIO_DIR", "devoured/mic")) / tentacle_id
        if self.keep_audio: self._audio_dir.mkdir(parents=True, exist_ok=True)

    def _init_tables(self):
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS mic_segments(
                    seg_id TEXT PRIMARY KEY, tentacle_id TEXT, seq INTEGER,
                    t_start TIMESTAMPTZ, t_end TIMESTAMPTZ, ms INTEGER,
                    text TEXT, keywords TEXT, status TEXT, error TEXT,
                    audio_file TEXT, UNIQUE(tentacle_id, seq))""")
                cur.execute("""CREATE TABLE IF NOT EXISTS mic_events(
                    id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(),
                    kind TEXT, detail TEXT)""")
        else:
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS mic_segments(
                    seg_id TEXT PRIMARY KEY, tentacle_id TEXT, seq INTEGER,
                    t_start REAL, t_end REAL, ms INTEGER,
                    text TEXT, keywords TEXT, status TEXT, error TEXT,
                    audio_file TEXT, UNIQUE(tentacle_id, seq))""")
                cur.execute("""CREATE TABLE IF NOT EXISTS mic_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT DEFAULT (datetime('now')),
                    kind TEXT, detail TEXT)""")

    def _note(self, kind, detail=""):
        try:
            if is_pg(self.led):
                with txn(self.led) as cur:
                    cur.execute("INSERT INTO mic_events(kind,detail) VALUES(%s,%s)",
                                (kind, detail[:300]))
            else:
                with txn(self.led) as cur:
                    cur.execute("INSERT INTO mic_events(kind,detail) VALUES(?,?)",
                                (kind, detail[:300]))
        except Exception as e:
            _swallow(__file__, e)

    # ── 音频回调：只写环形缓冲，绝不做 IO/网络 ──
    def _callback(self, indata, frames, time_info, status):
        if status: self.stats["overflow"] += 1
        mono = indata[:, 0] if indata.ndim > 1 else indata
        n = len(mono)
        with self._seq_lock:
            buf = self._ring
            if n >= len(buf):
                self._ring[:] = mono[-len(buf):]; self._ring_len = len(buf)
            else:
                buf[:-n] = buf[n:]; buf[-n:] = mono
                self._ring_len = min(self._ring_len + n, len(buf))

    # ── VAD 分段线程 ──
    def _segmenter(self):
        silence_frames = 0
        seg_frames = []
        pre = []
        silence_limit = SILENCE_MS // FRAME_MS
        preroll = PREROLL_MS // FRAME_MS
        max_frames = MAX_SEG_MS // FRAME_MS
        min_frames = MIN_SEG_MS // FRAME_MS

        while not self._stop.is_set():
            time.sleep(FRAME_MS / 1000)
            with self._seq_lock:
                if self._ring_len < FRAME_SAMPLES: continue
                frame = self._ring[-FRAME_SAMPLES:].copy()
            if self._vad is None:
                # 无 VAD 库 → 降级为固定时长切段
                seg_frames.append(frame)
                if len(seg_frames) >= max_frames:
                    self._emit(seg_frames); seg_frames = []
                continue
            try:
                voiced = self._vad.is_speech(frame.tobytes(), SAMPLE_RATE)
            except Exception:
                voiced = True
            if voiced:
                if not seg_frames and pre:      # 段首补前滚
                    seg_frames = pre[-preroll:]; pre = []
                seg_frames.append(frame); silence_frames = 0
            else:
                if seg_frames:
                    silence_frames += 1
                    seg_frames.append(frame)
                    if silence_frames >= silence_limit:
                        if len(seg_frames) >= min_frames: self._emit(seg_frames)
                        seg_frames = []
                        silence_frames = 0
                else:
                    pre.append(frame); pre = pre[-preroll:]
            if len(seg_frames) >= max_frames:    # 超长强制切（保留少量重叠）
                self._emit(seg_frames)
                seg_frames = seg_frames[-5:]; silence_frames = 0

    def _emit(self, frames):
        with self._seq_lock:
            seq = self._seq; self._seq += 1
        pcm = np.concatenate(frames).tobytes()
        ms = len(pcm) // 2 * 1000 // SAMPLE_RATE
        seg = Segment(seq=seq, start_ts=time.time() - ms / 1000,
                      end_ts=time.time(), pcm=pcm)
        try:
            self.q.put_nowait(seg)                # 满则丢，显式记数
            self.stats["segments"] += 1
        except queue.Full:
            self.stats["dropped"] += 1
            self._note("dropped", f"seq={seq} 队列满")

    # ── 转写工作线程（单线程，防重复）──
    def _worker(self):
        while not self._stop.is_set() or not self.q.empty():
            try:
                seg = self.q.get(timeout=1)
            except queue.Empty:
                continue
            self._process(seg)
            self.q.task_done()

    def _process(self, seg: Segment):
        tmp = None
        try:
            # PCM -> 临时 WAV（处理完即删）
            fd, path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            tmp = Path(path)
            with wave.open(str(tmp), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
                w.writeframes(seg.pcm)
            data = tmp.read_bytes()
            text, used, why = "", "", ""
            try:
                from senses.voice import _post
                r = _post("/audio/transcriptions", {"model": "whisper-1", "language": "auto"},
                          files={"file": (f"{seg.id}.wav", data)},
                          timeout=max(self.voice.timeout, 120))
                text = (r.get("text") or "").strip()
                used = "voicestudio" if text else ""
            except Exception as exc:                 # noqa: BLE001
                why = f"{type(exc).__name__}: {exc}"
            if not text:
                # ★2026-10-08：3900 不可达就**回落本机 SAPI 离线识别** —— 常开耳朵这才算通
                #   （原先只打 3900 /audio/transcriptions，而它压根没这个路由 ⇒ 永远空文本）
                try:
                    from senses import voice_sapi as _vs
                    loc = _vs.recognize(tmp)
                    got_text = (loc.get("text") or "").strip()
                    if loc.get("ok") and got_text:
                        text, used = got_text, "sapi-local"
                    else:
                        why = (why + " | " if why else "") + \
                              f"本机SAPI: {loc.get('reason') or '没听出文本'}"
                except Exception as exc:             # noqa: BLE001
                    why = (why + " | " if why else "") + f"{type(exc).__name__}: {exc}"
            # 纪律：**所有通道都没给出文本 = failed**，不拿空文本冒充成功
            #       （tests/test_mic.py:65 守的就是这条）
            if text:
                status, err = "done", None
                self.stats["transcribed"] += 1
            else:
                status, err = "failed", (why or "所有听写通道都没给出文本")
                self.stats["failed"] += 1
        except Exception as e:
            text, status, err = "", "failed", str(e)
            self.stats["failed"] += 1
        finally:
            if tmp and tmp.exists():
                if self.keep_audio:               # 显式开启才留档
                    keep = self._audio_dir / f"{seg.seq:08d}.wav"
                    tmp.replace(keep)
                else:
                    tmp.unlink(missing_ok=True)   # 默认：处理完即删

        hits = match_keywords(text, self.keywords) if text else []
        if hits: self._trigger(seg, text, hits)

        ms = int((seg.end_ts - seg.start_ts) * 1000)
        kws = ",".join(h["keyword"] for h in hits)
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""INSERT INTO mic_segments
                    (seg_id,tentacle_id,seq,t_start,t_end,ms,text,keywords,status,error)
                    VALUES(%s,%s,%s,to_timestamp(%s),to_timestamp(%s),%s,%s,%s,%s,%s)
                    ON CONFLICT (seg_id) DO NOTHING""",
                    (seg.id, self.tid, seg.seq, seg.start_ts, seg.end_ts,
                     ms, text, kws, status, err))
        else:
            with txn(self.led) as cur:
                cur.execute("""INSERT INTO mic_segments
                    (seg_id,tentacle_id,seq,t_start,t_end,ms,text,keywords,status,error)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (seg_id) DO NOTHING""",
                    (seg.id, self.tid, seg.seq, seg.start_ts, seg.end_ts,
                     ms, text, kws, status, err))

    # ── 关键词触发：冷却去重 + 发主脑（不直接执行高风险操作）──
    def _trigger(self, seg, text, hits):
        now = time.time()
        fresh = []
        for h in hits:
            last = self._cooldown.get(h["keyword"], 0)
            if now - last >= 30:                  # 30秒冷却，防连续触发
                self._cooldown[h["keyword"]] = now
                fresh.append(h)
        if not fresh: return
        self.stats["keywords"] += len(fresh)
        self._note("keyword", f"seq={seg.seq} hits={[h['keyword'] for h in fresh]}")
        # 交主脑裁决，不直接跑 shell/高风险操作
        if self.brain:
            try:
                self.brain.ask(self.tid, f"mic:seq{seg.seq}",
                    f"语音命中关键词 {[h['keyword'] for h in fresh]} 原文: {text[:200]}")
            except Exception as e:
                _swallow(__file__, e)
        # 可选回调（面板通知等）
        if self.on_keyword:
            try: self.on_keyword(seg, text, fresh)
            except Exception as e:
                _swallow(__file__, e)
        # 严重级关键词额外语音回执
        if any(h["level"] == "critical" for h in fresh):
            try: self.voice.enqueue(f"检测到紧急语音关键词：{fresh[0]['keyword']}",
                                    event_id=f"mic:{seg.id}", priority=0)
            except Exception as e:
                _swallow(__file__, e)

    # ── 启停 ──
    def start(self):
        if self.device is None:
            try:
                self.device = sd.query_devices(kind="input")["name"]
            except Exception as e:
                raise RuntimeError(f"无可用输入设备: {e}")
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=FRAME_SAMPLES, device=self.device, callback=self._callback)
        self.stream.start()
        threading.Thread(target=self._segmenter, daemon=True, name="mic-seg").start()
        threading.Thread(target=self._worker, daemon=True, name="mic-asr").start()
        self._note("started", f"device={self.device} vad={'on' if self._vad else 'off'}")
        return {"device": self.device, "vad": bool(self._vad),
                "sample_rate": SAMPLE_RATE}

    def stop(self):
        self._stop.set()
        try: self.stream.stop(); self.stream.close()
        except Exception as e:
            _swallow(__file__, e)
        self._note("stopped", f"stats={self.stats}")

    def status(self):
        return {"running": not self._stop.is_set(), "device": self.device,
                "vad": bool(self._vad), "queue": self.q.qsize(),
                "queue_cap": self.queue_size, **self.stats}

    # ── 检索：按关键词/时间段（可选过滤用 NULL 通配，杜绝动态拼 SQL）──
    def search(self, keyword=None, since=None, until=None, limit=100):
        kw = f"%{keyword}%" if keyword else None
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""SELECT seq, EXTRACT(EPOCH FROM t_start), text, keywords
                    FROM mic_segments
                    WHERE tentacle_id=%s
                      AND (%s::text IS NULL OR text ILIKE %s::text)
                      AND (%s::float8 IS NULL OR t_start >= to_timestamp(%s::float8))
                      AND (%s::float8 IS NULL OR t_start <= to_timestamp(%s::float8))
                    ORDER BY seq LIMIT %s""",
                    (self.tid, kw, kw, since, since, until, until, limit))
                return [{"seq": r[0], "t": r[1], "text": r[2], "keywords": r[3]}
                        for r in cur.fetchall()]
        with txn(self.led) as cur:
            cur.execute("""SELECT seq, t_start, text, keywords
                FROM mic_segments
                WHERE tentacle_id=?
                  AND (? IS NULL OR text LIKE ?)
                  AND (? IS NULL OR t_start >= ?)
                  AND (? IS NULL OR t_start <= ?)
                ORDER BY seq LIMIT ?""",
                (self.tid, kw, kw, since, since, until, until, limit))
            return [{"seq": r[0], "t": r[1], "text": r[2], "keywords": r[3]}
                    for r in cur.fetchall()]
