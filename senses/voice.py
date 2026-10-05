# senses/voice.py —— 语音能力适配器 · VoiceStudio 本地 REST
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 集成点（经复核）:
#   TTS  -> 告警播报 / 大脑裁决 / 扫描摘要（后台队列，按 event_id 缓存去重）
#   ASR  -> 吞噬能音频支路，转写写独立 transcripts 表（不塞大二进制进账本）
#   配音 -> 回放导出阶段，还原录像后配音，原档与成品分开存
#   降级 -> VoiceStudio 挂了不影响扫描/采集/文字告警
import os, json, time, uuid, hashlib, threading, queue, subprocess
from pathlib import Path
from dataclasses import dataclass
import urllib.request, urllib.error

from senses.sqldialect import is_pg, txn

VOICE_BASE = os.environ.get("VOICE_BASE_URL", "http://127.0.0.1:3900/v1")
VOICE_KEY  = os.environ.get("VOICE_API_KEY", "local")   # 本机任意非空
CACHE_DIR  = Path(os.environ.get("VOICE_CACHE", "voice_cache")); CACHE_DIR.mkdir(exist_ok=True)

def _post(path, data=None, files=None, timeout=120, raw=False):
    url = f"{VOICE_BASE}{path}"
    headers = {"Authorization": f"Bearer {VOICE_KEY}"}
    if files:
        import uuid as _u
        boundary = _u.uuid4().hex
        body = b""
        for k, v in (data or {}).items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        for k, (fn, content) in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                     f"filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
            body += content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    else:
        body = json.dumps(data or {}).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read() if raw else json.loads(r.read() or b"{}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"VoiceStudio 不可达: {e.reason}")

def _get(path, timeout=10):
    req = urllib.request.Request(f"{VOICE_BASE}{path}",
        headers={"Authorization": f"Bearer {VOICE_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"VoiceStudio 不可达: {e.reason}")


# ─────────────────────────────────────────────────────────────
# TTS：后台队列 + event_id 缓存去重
# ─────────────────────────────────────────────────────────────
@dataclass
class SpeechJob:
    text: str
    event_id: str          # 去重键：同一事件不重复朗读
    priority: int          # 0=critical 1=normal 2=summary
    voice: str = "default"
    fmt: str = "mp3"

    def __lt__(self, other):
        # PriorityQueue 的 (priority, ts) 平手时 heapq 会比较第三项；
        # 不定义 __lt__ 会 TypeError（同秒同优先级两条播报即可触发）
        return self.event_id < other.event_id

class VoiceAdapter:
    name, version = "voice", "voicestudio-1.0"

    def __init__(self, ledger=None, brain=None, queue_size=50,
                 max_concurrent=2, timeout_ms=60000):
        self.led, self.brain = ledger, brain
        self.timeout = timeout_ms / 1000
        self.q: queue.PriorityQueue = queue.PriorityQueue(maxsize=queue_size)
        self._seen: set = set()
        self._seen_lock = threading.Lock()
        self._sem = threading.Semaphore(max_concurrent)
        self._stop = threading.Event()
        self.stats = {"done": 0, "failed": 0, "dropped": 0, "deduped": 0}
        if ledger: self._init_table()

    def _init_table(self):
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS voice_jobs(
                    event_id TEXT PRIMARY KEY, kind TEXT, text TEXT, status TEXT,
                    file TEXT, error TEXT, created_at TIMESTAMPTZ DEFAULT now(),
                    done_at TIMESTAMPTZ)""")
        else:
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS voice_jobs(
                    event_id TEXT PRIMARY KEY, kind TEXT, text TEXT, status TEXT,
                    file TEXT, error TEXT, created_at TEXT DEFAULT (datetime('now')),
                    done_at TEXT)""")

    def _record(self, event_id, kind, text, status, file=None, error=None):
        if not self.led: return
        try:
            if is_pg(self.led):
                with txn(self.led) as cur:
                    cur.execute("""INSERT INTO voice_jobs(event_id,kind,text,status,file,error)
                        VALUES(%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (event_id) DO UPDATE SET status=EXCLUDED.status,
                        file=EXCLUDED.file, error=EXCLUDED.error, done_at=now()""",
                        (event_id, kind, text[:500], status, file, error))
            else:
                with txn(self.led) as cur:
                    cur.execute("""INSERT INTO voice_jobs(event_id,kind,text,status,file,error)
                        VALUES(?,?,?,?,?,?)
                        ON CONFLICT (event_id) DO UPDATE SET status=EXCLUDED.status,
                        file=EXCLUDED.file, error=EXCLUDED.error, done_at=datetime('now')""",
                        (event_id, kind, text[:500], status, file, error))
        except Exception: pass

    # ── 入队（去重 + 背压）──
    def enqueue(self, text, event_id=None, priority=1, voice="default"):
        event_id = event_id or uuid.uuid4().hex[:12]
        with self._seen_lock:
            if event_id in self._seen:
                self.stats["deduped"] += 1
                return {"queued": False, "reason": "duplicate"}
            self._seen.add(event_id)
        try:
            self.q.put_nowait((priority, time.time(), SpeechJob(text, event_id, priority, voice)))
            self._record(event_id, "tts", text, "queued")
            return {"queued": True, "event_id": event_id}
        except queue.Full:
            self.stats["dropped"] += 1
            self._record(event_id, "tts", text, "dropped", error="queue full")
            return {"queued": False, "reason": "queue_full"}   # 丢弃低优先级，不阻塞主流程

    # ── 合成（带缓存）──
    def synthesize(self, text, voice="default", fmt="mp3") -> Path:
        key = hashlib.sha256(f"{voice}|{fmt}|{text}".encode()).hexdigest()[:20]
        out = CACHE_DIR / f"{key}.{fmt}"
        if out.exists(): return out                          # 命中缓存
        audio = _post("/audio/speech", {"model": "tts-1", "voice": voice,
                                        "input": text, "response_format": fmt},
                      timeout=self.timeout, raw=True)
        out.write_bytes(audio)
        return out

    # ── 消费线程 ──
    def start(self):
        def loop():
            while not self._stop.is_set():
                try:
                    _, _, job = self.q.get(timeout=1)
                except queue.Empty:
                    continue
                with self._sem:                            # 限并发
                    try:
                        f = self.synthesize(job.text, job.voice, job.fmt)
                        self.stats["done"] += 1
                        self._record(job.event_id, "tts", job.text, "done", str(f))
                        self._play(f)                       # 可选：本地播放
                    except Exception as e:
                        self.stats["failed"] += 1
                        self._record(job.event_id, "tts", job.text, "failed", error=str(e))
                self.q.task_done()
        threading.Thread(target=loop, daemon=True, name="voice-tts").start()

    def _play(self, path: Path):
        """本机播放（可选）；无播放器则静默跳过"""
        if os.environ.get("VOICE_AUTOPLAY", "0") != "1": return
        try:
            subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                              str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception: pass

    def stop(self): self._stop.set()

    # ── 便捷封装：三类播报 ──
    def spec(self) -> dict:
        return {
            "inputs": {
                "action": {"type": "enum", "values": ["say", "synthesize"],
                           "required": True, "default": "say"},
                "text": {"type": "string", "required": True},
                "voice": {"type": "string", "default": "default"},
                "event_id": {"type": "string",
                             "help": "say 的去重键（同一事件不重复播报）"},
                "priority": {"type": "integer", "default": 1,
                             "help": "0=critical 1=normal 2=summary"},
            },
            "outputs": {"queued": {"type": "boolean"},
                        "event_id": {"type": "string"},
                        "file": {"type": "string"}},
            "idempotent": False, "risk": "low",
        }

    def run(self, ctx, request):
        """NativeSkill 适配：request -> enqueue/synthesize"""
        from skills.native import SkillResult
        action = request.get("action", "say")
        text = request.get("text")
        if not text:
            return SkillResult(False, error="缺少 text")
        if action == "synthesize":
            try:
                f = self.synthesize(text, request.get("voice", "default"))
                return SkillResult(True, output={"file": str(f)})
            except Exception as e:
                return SkillResult(False, error=str(e))
        r = self.enqueue(text, event_id=request.get("event_id"),
                         priority=int(request.get("priority", 1)),
                         voice=request.get("voice", "default"))
        return SkillResult(bool(r.get("queued")), output=r)

    def say_alert(self, alert: dict):
        """告警播报（critical 优先，边沿触发时调一次）"""
        lvl = "严重" if alert.get("level") == "critical" else "警告"
        txt = f"{lvl}告警：{alert.get('label')}，当前{alert.get('value')}{alert.get('unit','')}"
        return self.enqueue(txt, event_id=f"alert:{alert.get('episode')}",
                            priority=0 if alert.get("level") == "critical" else 1)

    def say_verdict(self, verdict: dict, target: str):
        txt = f"大脑裁决：{verdict.get('verdict')}。{verdict.get('hint','')}"
        return self.enqueue(txt, event_id=f"verdict:{target}:{int(time.time()//60)}", priority=1)

    def say_summary(self, stats: dict):
        txt = (f"扫描完成，共{stats.get('total_targets')}个目标，"
               f"发现{stats.get('aggregate',{}).get('vuln',0)}处疑似漏洞，"
               f"卡点{stats.get('aggregate',{}).get('blocked',0)}处")
        return self.enqueue(txt, event_id=f"summary:{int(time.time()//300)}", priority=2)


# ─────────────────────────────────────────────────────────────
# ASR：吞噬能的音频支路（屏幕帧与音频分开采）
# ─────────────────────────────────────────────────────────────
class AudioDevourer:
    """音频吞噬：切段 → 异步转写 → 写 transcripts 表（不塞二进制进账本）"""
    def __init__(self, tentacle_id, ledger, adapter: VoiceAdapter,
                 chunk_seconds=30, outdir="devoured/audio"):
        self.tid, self.led, self.voice = tentacle_id, ledger, adapter
        self.chunk, self.out = chunk_seconds, Path(outdir) / tentacle_id
        self.out.mkdir(parents=True, exist_ok=True)
        self._init_table()

    def _init_table(self):
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS transcripts(
                    id BIGSERIAL PRIMARY KEY, tentacle_id TEXT, seq INTEGER,
                    t_start TIMESTAMPTZ, t_end TIMESTAMPTZ, source TEXT,
                    text TEXT, lang TEXT, status TEXT, error TEXT, audio_file TEXT,
                    UNIQUE(tentacle_id, seq))""")
        else:
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS transcripts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tentacle_id TEXT, seq INTEGER,
                    t_start REAL, t_end REAL, source TEXT,
                    text TEXT, lang TEXT, status TEXT, error TEXT, audio_file TEXT,
                    UNIQUE(tentacle_id, seq))""")

    def transcribe_file(self, audio_path: Path, seq: int,
                        t_start=None, t_end=None, source="mic"):
        try:
            data = audio_path.read_bytes()
            r = _post("/audio/transcriptions",
                      {"model": "whisper-1", "language": "auto"},
                      files={"file": (audio_path.name, data)},
                      timeout=max(self.voice.timeout, 180))
            text = r.get("text", "")
            status, err = "done", None
        except Exception as e:
            text, status, err = "", "failed", str(e)
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""INSERT INTO transcripts
                    (tentacle_id,seq,t_start,t_end,source,text,lang,status,error,audio_file)
                    VALUES(%s,%s,to_timestamp(%s),to_timestamp(%s),%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tentacle_id,seq) DO NOTHING""",
                    (self.tid, seq, t_start or time.time(), t_end or time.time(),
                     source, text, "auto", status, err, str(audio_path)))
        else:
            with txn(self.led) as cur:
                cur.execute("""INSERT INTO transcripts
                    (tentacle_id,seq,t_start,t_end,source,text,lang,status,error,audio_file)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (tentacle_id,seq) DO NOTHING""",
                    (self.tid, seq, t_start or time.time(), t_end or time.time(),
                     source, text, "auto", status, err, str(audio_path)))
        return {"seq": seq, "text": text, "status": status, "error": err}

    def search(self, keyword: str, limit=50):
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""SELECT seq, EXTRACT(EPOCH FROM t_start), text
                    FROM transcripts WHERE tentacle_id=%s AND text ILIKE %s
                    ORDER BY seq LIMIT %s""", (self.tid, f"%{keyword}%", limit))
                return [{"seq": r[0], "t": r[1], "text": r[2]} for r in cur.fetchall()]
        with txn(self.led) as cur:
            cur.execute("""SELECT seq, t_start, text
                FROM transcripts WHERE tentacle_id=? AND text LIKE ?
                ORDER BY seq LIMIT ?""", (self.tid, f"%{keyword}%", limit))
            return [{"seq": r[0], "t": r[1], "text": r[2]} for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────
# 配音：回放导出阶段（原档与成品分开存）
# ─────────────────────────────────────────────────────────────
class Dubbing:
    def __init__(self, adapter: VoiceAdapter, outdir="panel/exports"):
        self.voice, self.out = adapter, Path(outdir); self.out.mkdir(exist_ok=True, parents=True)

    def dub_video(self, video_path: Path, target_lang="zh", voice="default") -> dict:
        data = video_path.read_bytes()
        r = _post("/dub/upload",
                  {"target_lang": target_lang, "voice": voice},
                  files={"file": (video_path.name, data)}, timeout=300)
        job_id = r.get("id") or r.get("job_id")
        return {"job_id": job_id, "status": r.get("status", "submitted"),
                "note": "配音成品单独存，不覆盖原档"}
