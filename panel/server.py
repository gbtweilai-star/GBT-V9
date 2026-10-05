# panel/server.py —— 触手总控面板 + 回放端 · dev: 自由的风
# 一键预览 / 勾选导出 / 状态看板 / 缓存管理
import os, re, json, sqlite3, time, uuid, shutil, threading, subprocess, asyncio
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from senses.playback import Player, Segment          # 复用上一轮回放端
from senses.cache_reaper import default_reaper       # 缓存回收：_preview/_cache 双预算

FRAME_DIR   = Path(os.environ.get("DEVOUR_DIR", "devoured/t1-eye"))
LEDGER_DB   = os.environ.get("LEDGER_DB", "tentacle_ledger.db")
EXPORT_DIR  = Path("panel/exports"); EXPORT_DIR.mkdir(parents=True, exist_ok=True)
PREVIEW_DIR = FRAME_DIR / "_preview"; PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

player = Player(FRAME_DIR)
REAPER = default_reaper(FRAME_DIR)      # 自动回收：超限清最旧（LRU + pin 保护）
JOBS: dict[str, dict] = {}          # job_id -> {status,file,error,ts,segs}
_lock = threading.Lock()

app = FastAPI(title="GBT小土豆V9 · 总控台")

# ────────── 段 → 浏览器可播的 MP4（按需转码 + 缓存） ──────────
def preview_mp4(seg: Segment) -> Optional[Path]:
    return player.preview(seg, preview_dir=PREVIEW_DIR)   # 本地命中或从 R2 拉回（带哈希校验）

# ────────── 支持 Range 的流式响应（<video> 拖动进度条必需） ──────────
def serve_range(path: Path, request: Request, media_type="video/mp4"):
    size = path.stat().st_size
    rng = request.headers.get("range")
    if not rng:
        return FileResponse(path, media_type=media_type,
                            headers={"Accept-Ranges": "bytes"})
    m = re.match(r"bytes=(\d*)-(\d*)", rng)
    if not m:
        raise HTTPException(416, "invalid range")
    start = int(m.group(1)) if m.group(1) else 0
    end   = int(m.group(2)) if m.group(2) else size - 1
    end   = min(end, size - 1)
    length = end - start + 1

    def gen():
        with open(path, "rb") as f:
            f.seek(start); remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk: break
                remaining -= len(chunk); yield chunk

    return StreamingResponse(gen(), status_code=206, media_type=media_type,
        headers={"Content-Range": f"bytes {start}-{end}/{size}",
                 "Accept-Ranges": "bytes", "Content-Length": str(length)})

# ────────── 导出任务（后台线程，不阻塞面板） ──────────
def run_export(job_id: str, seg_ids: list[str], lossless: bool, name: str):
    job = JOBS[job_id]
    try:
        segs = [s for s in player.segments() if s.seg_id in seg_ids]
        if not segs: raise RuntimeError("未匹配到任何段")
        ext = ".mkv" if lossless else ".mp4"
        out = EXPORT_DIR / f"{name or job_id}{ext}"
        job.update(status="running", progress=f"0/{len(segs)}")
        path, n_parts = player.export(segs, out=out, lossless=lossless)   # 拉段+哈希校验+拼接
        job["progress"] = f"{n_parts}/{len(segs)}"
        job.update(status="done", file=str(out),
                   size=Path(path).stat().st_size, segs=n_parts, ts=time.time())
    except Exception as e:
        job.update(status="failed", error=f"{type(e).__name__}: {e}")

# ────────── API ──────────
# ────────── 账本后端：独立探测（面板与 main.py 是两个进程，不共享内存） ──────────
import time as _time
from audit.ledger_factory import backend_info, make_ledger

_LED = None
_LED_ERR = None
_LAST_ALERT = {"active": False, "ts": 0}


def get_ledger(reconnect: bool = False):
    """惰性连接账本；失败不崩面板，把原因带出去显示。"""
    global _LED, _LED_ERR
    if _LED is not None and not reconnect:
        return _LED
    try:
        _LED = make_ledger()
        _LED_ERR = None
    except Exception as e:  # noqa: BLE001
        _LED, _LED_ERR = None, f"{type(e).__name__}: {e}"
    return _LED


def probe_health(led) -> dict:
    """真探一次：连得上吗？往返多少毫秒？（不是查缓存）"""
    t0 = _time.perf_counter()
    try:
        led.counts()
        return {"ok": True, "rtt_ms": round((_time.perf_counter() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "rtt_ms": None, "error": f"{type(e).__name__}: {e}"}


def _log_blocked(detail: str) -> None:
    """告警落账（面板也是一等公民；写不进去只跳过，不崩）。"""
    led = get_ledger()
    if led is None:
        return
    try:
        led.log("panel", f"backend:{backend_info()['backend']}", "blocked", detail[:200])
    except Exception:
        pass


@app.get("/api/health")
def api_health():
    """桌面壳健康轮询契约（/api/health 200 = 后端就绪）。

    没有这条路由时，壳会一直报"网络不可用或后端未启动"—— 明明进程活着。
    返回值只报事实：账本可用否、身体服务是否在跑、见证快照新鲜度。
    """
    out = {"ok": True, "ts": _time.time(), "service": "GBT小土豆V9"}
    try:
        led = get_ledger()
        out["ledger"] = bool(led is not None)
    except Exception:                                          # noqa: BLE001
        out["ledger"] = False
    out["witness_probe"] = os.environ.get("BODY_WITNESS_PROBE", "1") != "0" and \
        getattr(app.state, "witness_probe_task", None) is not None
    out["collect"] = getattr(app.state, "collect_task", None) is not None
    return out


@app.get("/api/backend")
def api_backend():
    """当前账本后端 + 健康度 + PG 池快照（一处出全）。"""
    info = backend_info()
    led = get_ledger()
    out = {"ts": _time.time(), **info}
    if led is None:
        out.update({"healthy": False, "error": _LED_ERR or "未连接",
                    "hint": "检查 DATABASE_URL / LEDGER_DB 与网络"})
        return out
    health = probe_health(led)
    out.update({"healthy": health["ok"], "rtt_ms": health.get("rtt_ms")})
    if not health["ok"]:
        out["error"] = health.get("error")
    try:
        out["ledger"] = {"counts": led.counts()}
    except Exception as e:  # noqa: BLE001
        out["ledger"] = {"error": str(e)}

    alert_reasons = []
    if info["backend"] == "pg" and hasattr(led, "metrics"):
        snap = led.metrics.snapshot()
        probe = led.probe.sample() if hasattr(led, "probe") else {}
        out["pool"] = snap
        out["slow"] = led.metrics.slow_queries(10)
        out["server"] = probe.get("server", {})
        out["locks"] = probe.get("locks", [])
        out["long_running"] = probe.get("long_running", [])
        if snap.get("util_pct", 0) > 90:
            alert_reasons.append(f"池占用 {snap['util_pct']}% > 90%")
        if out["locks"]:
            alert_reasons.append(f"锁等待 {len(out['locks'])} 个")
        if out["long_running"]:
            alert_reasons.append(f"长事务 {len(out['long_running'])} 个")
    else:
        try:
            from pathlib import Path as _P
            p = _P(os.environ.get("LEDGER_DB", "tentacle_ledger.db"))
            out["sqlite"] = {"path": str(p),
                             "size_mb": round(p.stat().st_size / 1048576, 2) if p.exists() else 0}
        except Exception:
            pass

    out["alert"] = bool(alert_reasons)
    out["alert_reasons"] = alert_reasons
    if alert_reasons and not _LAST_ALERT["active"]:
        _log_blocked(" · ".join(alert_reasons))       # 边沿触发：进入告警记一条 blocked
        _LAST_ALERT.update({"active": True, "ts": _time.time()})
    elif not alert_reasons:
        _LAST_ALERT["active"] = False
    return out


@app.post("/api/backend/reconnect")
def api_reconnect():
    led = get_ledger(reconnect=True)
    return {"ok": led is not None, "backend": backend_info()["backend"],
            "error": _LED_ERR}


# ────────── 存储与扩容：面板只读 DB 自算（绝不触发扩容） ──────────
PARTITIONED_TABLES = ("ledger", "alert_events", "cross_tasks")


@app.get("/api/scale")
def api_scale():
    led = get_ledger()
    if led is None:
        return {"enabled": False, "reason": _LED_ERR or "账本未连接"}
    if getattr(led, "backend", "sqlite") != "pg":
        local = {}
        try:
            from pathlib import Path as _P
            p = _P(os.environ.get("LEDGER_DB", "tentacle_ledger.db"))
            local = {"path": str(p),
                     "size_mb": round(p.stat().st_size / 1048576, 2) if p.exists() else 0}
            with led._tx() as c:
                local["rows"] = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        except Exception:
            pass
        return {"enabled": False, "backend": "sqlite",
                "reason": "扩容监控仅 PG 后端支持（SQLite 走表级归档/文件轮转）",
                "local": local}

    out = {"enabled": True, "backend": "pg", "ts": _time.time(), "warnings": []}
    with txn(led) as cur:
        cur.execute("""SELECT EXTRACT(EPOCH FROM ts), db_bytes, capacity_bytes, conn_pct
                       FROM db_size_samples ORDER BY ts DESC LIMIT 2""")
        rows = cur.fetchall()
        if not rows:
            out["warnings"].append("无体积样本（守护进程未运行？）")
            out.update({"used_pct": None, "growth": None, "eta_days": None})
        else:
            t1, b1, cap, conn = rows[0]
            out["capacity_gb"] = round(cap / 1073741824, 1)
            out["used_gb"] = round(b1 / 1073741824, 3)
            out["used_pct"] = round(b1 / cap * 100, 2) if cap else None
            out["conn_pct"] = conn
            out["sample_age_sec"] = round(_time.time() - t1, 1)
            if out["sample_age_sec"] > 900:
                out["warnings"].append(f"样本陈旧({int(out['sample_age_sec'])}s前)")
            if len(rows) == 2:
                t0, b0 = rows[1][0], rows[1][1]
                dt = max(t1 - t0, 1)
                rate = (b1 - b0) / dt
                out["growth"] = {"gb_day": round(rate * 86400 / 1073741824, 3),
                                 "mb_hour": round(rate * 3600 / 1048576, 2)}
                eta = (cap - b1) / rate if rate > 0 else None
                out["eta_days"] = round(eta / 86400, 1) if eta else None
            else:
                out["warnings"].append("样本不足2条，算不出斜率")

        try:
            cur.execute("""SELECT state, spec_cpu, spec_mem, last_action,
                EXTRACT(EPOCH FROM (now()-heartbeat)) AS hb_age,
                protect_flag, resizes_this_month,
                EXTRACT(EPOCH FROM (cooldown_until-now())) AS cd
                FROM scaler_state LIMIT 1""")
            r = cur.fetchone()
            if r:
                hb_age = r[4]
                out["daemon"] = {"state": r[0], "spec": f"{r[1]}cpu/{r[2]}GB",
                                 "last_action": r[3], "protect": r[5],
                                 "resizes_month": r[6],
                                 "cooldown_sec": max(0, int(r[7] or 0)),
                                 "heartbeat_age": int(hb_age) if hb_age else None,
                                 "online": (hb_age or 999) < 120}
                if not out["daemon"]["online"]:
                    out["warnings"].append("扩容控制器离线（心跳超时）")
            else:
                out["daemon"] = None
                out["warnings"].append("无控制器状态记录")
        except Exception as e:  # noqa: BLE001
            out["daemon"] = None
            out["warnings"].append(f"控制器状态不可用: {e}")

        try:
            cur.execute("""
                SELECT p.relname, c.relname, pg_total_relation_size(c.oid),
                       pg_stat_get_live_tuples(c.oid)
                FROM pg_class c
                JOIN pg_inherits i ON i.inhrelid = c.oid
                JOIN pg_class p ON p.oid = i.inhparent
                WHERE p.relname = ANY(%s)
                ORDER BY p.relname, c.relname""", (list(PARTITIONED_TABLES),))
            parts = {}
            for tbl, part, b, n in cur.fetchall():
                parts.setdefault(tbl, []).append(
                    {"name": part, "mb": round(b / 1048576, 2), "rows": n or 0})
            out["partitions"] = parts
            out["partition_ok"] = True
        except Exception as e:  # noqa: BLE001
            out["partitions"] = None
            out["partition_ok"] = False
            out["warnings"].append(f"分区查询不可用: {e}")

        try:
            cur.execute("""SELECT action, detail, ok, EXTRACT(EPOCH FROM ts)
                FROM scale_audit ORDER BY ts DESC LIMIT 10""")
            out["audit"] = [{"action": r[0], "detail": r[1], "ok": r[2], "ts": r[3]}
                            for r in cur.fetchall()]
        except Exception:
            out["audit"] = []
    return out


# ────────── 感官与工程：看/听/说/控/想 + 编程工具（跨进程只读 DB） ──────────
@app.get("/api/senses")
def api_senses():
    out = {"ts": _time.time()}
    try:
        idx = FRAME_DIR / "index.jsonl"
        seg_idx = FRAME_DIR / "segments.jsonl"
        frames = [json.loads(l) for l in idx.read_text(encoding="utf-8").splitlines() if l.strip()] \
            if idx.exists() else []
        stored = [f for f in frames if f.get("state") == "stored"]
        seqs = sorted(f["seq"] for f in stored)
        gaps = len(set(range(seqs[0], seqs[-1] + 1)) - set(seqs)) if seqs else 0
        segs = [json.loads(l) for l in seg_idx.read_text(encoding="utf-8").splitlines() if l.strip()] \
            if seg_idx.exists() else []
        out["devour"] = {"frames": len(stored), "gaps": gaps,
                         "segments": len({s.get("seg_id") for s in segs})}
    except Exception as e:  # noqa: BLE001
        out["devour"] = {"error": str(e)}

    led = get_ledger()
    if led is not None:
        def _q(sql: str, args=()):
            try:
                with led._tx() as c:
                    return c.execute(sql, args).fetchall()
            except Exception:
                return None

        rows = _q("SELECT status, COUNT(*) FROM mic_segments GROUP BY status")
        out["mic"] = {k: v for k, v in rows} if rows is not None else {"table": "未建（麦克风未启用过）"}
        ev = _q("SELECT kind, COUNT(*) FROM mic_events GROUP BY kind")
        if ev is not None:
            out["mic_events"] = {k: v for k, v in ev}

        vrows = _q("SELECT status, COUNT(*) FROM voice_jobs GROUP BY status")
        out["voice"] = {k: v for k, v in vrows} if vrows is not None else {"table": "未建（语音未启用过）"}

        for scanner, key in (("pulse", "pulse"), ("codex", "codex"), ("coder", "coder"),
                             ("part-mgr", "partition"), ("scale", "scale"), ("panel", "panel")):
            r = _q("SELECT COUNT(*), MAX(ts) FROM ledger WHERE scanner=?", (scanner,))
            out[key] = {"runs": r[0][0] if r else 0, "last_ts": r[0][1] if r else None}
    try:
        from skills.native_codex import CodexTool
        av = CodexTool(ledger=None).probe()
        out["codex"] = {"available": av.ok, "detail": av.reason}
    except Exception as e:  # noqa: BLE001
        out["codex"] = {"available": False, "detail": f"{type(e).__name__}: {e}"}
    out["brain"] = {"gateway": bool(os.environ.get("OPENAI_BASE_URL")),
                    "ollama": bool(os.environ.get("OLLAMA_HOST"))}
    return out




@app.get("/api/state")
def state():
    db = sqlite3.connect(LEDGER_DB)
    nodes = {}
    for scanner, status, n in db.execute(
            "SELECT scanner, status, COUNT(*) FROM ledger GROUP BY 1,2"):
        nodes.setdefault(scanner, {})[status] = n
    segs = player.segments()
    archived = sum(1 for s in segs if s.state == "archived")
    return {"ts": time.time(), "nodes": nodes,
            "segments": {"total": len(segs), "archived": archived,
                         "local": sum(1 for s in segs if Path(s.local_path).exists())},
            "disk": _disk_pct(FRAME_DIR)}

@app.get("/api/segments")
def segments():
    out = []
    for s in player.segments():
        out.append({"seg_id": s.seg_id, "start": s.start_seq, "end": s.end_seq,
                    "frames": s.frame_count, "mb": round(s.size/1048576, 1),
                    "state": s.state, "ts": s.ts,
                    "local": Path(s.local_path).exists()})
    return {"segments": out}

@app.get("/api/preview/{seg_id}")
def preview(seg_id: str, request: Request):
    seg = next((s for s in player.segments() if s.seg_id == seg_id), None)
    if not seg: raise HTTPException(404, "段不存在")
    mp4 = preview_mp4(seg)
    if not mp4: raise HTTPException(502, "取段失败（本地与R2均未命中）")
    return serve_range(mp4, request)

@app.get("/api/sheet/{seg_id}")
def sheet(seg_id: str):
    seg = next((s for s in player.segments() if s.seg_id == seg_id), None)
    if not seg: raise HTTPException(404, "段不存在")
    out = PREVIEW_DIR / f"{seg_id}.png"
    if not out.exists():
        player.print_contact_sheet([seg], out=str(out))
    return FileResponse(out, media_type="image/png")

@app.post("/api/export")
def export(payload: dict):
    seg_ids = payload.get("seg_ids") or []
    if not seg_ids: raise HTTPException(400, "未选择段")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "ts": time.time(),
                    "lossless": bool(payload.get("lossless")),
                    "name": payload.get("name", ""), "file": None, "error": None}
    threading.Thread(target=run_export, args=(
        job_id, seg_ids, bool(payload.get("lossless")),
        payload.get("name", "")), daemon=True).start()
    return {"job_id": job_id}

@app.get("/api/jobs")
def jobs():
    return {"jobs": {k: v for k, v in sorted(JOBS.items(),
                    key=lambda kv: -kv[1]["ts"])}}

@app.get("/api/download/{job_id}")
def download(job_id: str):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done": raise HTTPException(404, "任务未完成")
    p = Path(job["file"])
    if not p.exists(): raise HTTPException(404, "文件已丢失")
    return FileResponse(p, filename=p.name)

@app.get("/api/cache")
def cache_status():
    """缓存状态：双目录预算 + 当前占用 + 最近一次回收读数。"""
    return {"budgets": REAPER.status(), "last_reap": REAPER.last,
            "preview_dir": str(PREVIEW_DIR),
            "pull_dir": str(FRAME_DIR / "_cache")}


@app.delete("/api/cache")
def clear_cache():
    """立即回收：超预算的清最旧（LRU + pin 保护），返回真读数。"""
    result = REAPER.reap_all()
    return {"reaped": result, "budgets": REAPER.status()}

def _disk_pct(p: Path) -> float:
    try: u = shutil.disk_usage(p); return round(u.used / u.total * 100, 1)
    except Exception: return 0.0

# ────────── 面板页面 ──────────
@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE

PAGE = """<!doctype html><html lang=zh><meta charset=utf-8>
<title>GBT小土豆V9 · 总控台</title>
<style>
 body{background:#0d1117;color:#c9d1d9;font:14px ui-monospace,SFMono,monospace;padding:20px}
 h2{margin:0 0 14px}.muted{color:#8b949e}
 .row{display:flex;gap:14px;flex-wrap:wrap}
 .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin:6px 0;flex:1;min-width:220px}
 .g{color:#3fb950}.r{color:#f85149}.y{color:#d29922}.b{color:#58a6ff}
 table{width:100%;border-collapse:collapse;margin-top:8px}
 th,td{border-bottom:1px solid #21262d;padding:7px;text-align:left;font-size:13px}
 th{color:#8b949e;font-weight:500}
 button{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;
        padding:5px 11px;cursor:pointer;font-family:inherit}
 button:hover{background:#30363d}button:disabled{opacity:.4;cursor:not-allowed}
 .prim{background:#238636;border-color:#2ea043}.prim:hover{background:#2ea043}
 video{width:100%;max-height:360px;background:#000;border-radius:6px;margin-top:8px}
 .pill{padding:1px 7px;border-radius:10px;font-size:11px;border:1px solid #30363d}
 .bar{height:6px;background:#21262d;border-radius:3px;overflow:hidden;margin-top:6px}
 .bar>i{display:block;height:100%;background:#238636}
 #toast{position:fixed;right:20px;bottom:20px;background:#161b22;border:1px solid #30363d;
        padding:10px 16px;border-radius:8px;display:none}
</style>
<h2>GBT小土豆V9 · 总控台 <span class=muted id=ts></span></h2>

<div class=card id=backendcard>
  <b>账本后端</b> <span class=muted>· 连接健康度 · 池快照</span>
  <button style="float:right" onclick="reconnect()">重连</button>
  <div class=row style="margin-top:10px">
    <div class=card style="margin:0;min-width:200px"><b>后端</b>
      <div id=be-backend style="font-size:20px;margin:6px 0">—</div>
      <div id=be-target class=muted>—</div></div>
    <div class=card style="margin:0;min-width:200px"><b>健康度</b>
      <div id=be-health style="font-size:20px;margin:6px 0">—</div>
      <div id=be-rtt class=muted>—</div></div>
    <div class=card style="margin:0;flex:2"><b>连接池</b>
      <div class=bar><i id=be-poolbar style="width:0%"></i></div>
      <div id=be-pool class=muted style="margin-top:6px">—</div></div>
  </div>
  <div id=be-detail class=muted style="margin-top:8px"></div>
  <table id=be-slowtable style="margin-top:8px">
    <thead><tr><th>类型</th><th>SQL / PID</th><th>耗时</th></tr></thead><tbody></tbody></table>
</div>

<div class=card id=scalecard>
  <b>存储与扩容</b> <span class=muted>· 用量 · 斜率 · ETA · 分区</span>
  <div id=scale-warn class=muted style="margin-top:6px"></div>
  <div class=row style="margin-top:10px">
    <div class=card style="margin:0;min-width:190px"><b>用量</b>
      <div class=bar style="margin-top:6px"><i id=sc-bar style="width:0%"></i></div>
      <div id=sc-used class=muted>—</div></div>
    <div class=card style="margin:0;min-width:190px"><b>增长斜率</b>
      <div id=sc-rate style="font-size:17px;margin:6px 0">—</div>
      <div id=sc-rate2 class=muted>—</div></div>
    <div class=card style="margin:0;min-width:190px"><b>耗尽预测</b>
      <div id=sc-eta style="font-size:17px;margin:6px 0">—</div>
      <div id=sc-eta2 class=muted>—</div></div>
    <div class=card style="margin:0;min-width:190px"><b>控制器</b>
      <div id=sc-daemon style="font-size:15px;margin:6px 0">—</div>
      <div id=sc-daemon2 class=muted>—</div></div>
  </div>
  <div id=sc-parts style="margin-top:10px"></div>
  <div id=sc-audit style="margin-top:8px"></div>
</div>

<div class=card id=sensescard>
  <b>感官与工程</b> <span class=muted>· 看(吞噬) 听(ASR) 说(TTS) 控(脉冲) 想(大脑) · 编程工具(Codex)</span>
  <div class=row style="margin-top:10px" id=sn-cards></div>
</div>


<div class=row id=stats></div>

<div class=card>
  <b>回放台</b> <span class=muted>选中段 → 预览 / 导出（勾选多段拼接）</span>
  <div style="margin-top:8px">
    <button class=prim onclick="doExport(false)">导出 MP4（给用户看）</button>
    <button onclick="doExport(true)">导出无损 MKV（原样还原）</button>
    <button onclick="clearCache()">清理预览缓存</button>
  </div>
  <video id=player controls></video>
  <div id=seginfo class=muted style="margin-top:6px">未选择</div>
  <table><thead><tr><th>选</th><th>段ID</th><th>帧数</th><th>大小</th><th>状态</th><th>位置</th><th>操作</th></tr></thead>
  <tbody id=segs></tbody></table>
</div>

<div class=card>
  <b>导出任务</b>
  <table><thead><tr><th>任务</th><th>模式</th><th>状态</th><th>进度</th><th>操作</th></tr></thead>
  <tbody id=jobs></tbody></table>
</div>

<div id=toast></div>
<script>
const $ = s => document.querySelector(s);
let SEL = new Set(), SEGS = [];

function toast(m){const t=$('#toast');t.textContent=m;t.style.display='block';
  clearTimeout(t._t);t._t=setTimeout(()=>t.style.display='none',2600);}

async function tickState(){
  const s = await (await fetch('/api/state')).json();
  $('#ts').textContent = new Date(s.ts*1000).toLocaleTimeString();
  let h = `<div class=card><b>存储</b><br>段总数 <span class=b>${s.segments.total}</span>
      · 已归档 <span class=g>${s.segments.archived}</span>
      · 本地在 <span class=y>${s.segments.local}</span>
      <div class=bar><i style="width:${s.disk}%"></i></div>
      <span class=muted>本地磁盘 ${s.disk}%</span></div>`;
  for (const [tid, st] of Object.entries(s.nodes||{})){
    const cells = Object.entries(st).map(([k,v])=>`<span class=pill>${k} ${v}</span>`).join(' ');
    h += `<div class=card><b>${tid}</b><div style="margin-top:6px">${cells}</div></div>`;
  }
  $('#stats').innerHTML = h;
}

async function tickSegs(){
  const d = await (await fetch('/api/segments')).json();
  SEGS = d.segments;
  $('#segs').innerHTML = SEGS.map(s => `<tr>
    <td><input type=checkbox ${SEL.has(s.seg_id)?'checked':''} onchange="toggle('${s.seg_id}',this.checked)"></td>
    <td>${s.seg_id}</td><td>${s.frames}</td><td>${s.mb} MB</td>
    <td>${s.state=='archived'?'<span class=g>archived</span>':s.state}</td>
    <td>${s.local?'<span class=y>本地</span>':'<span class=b>R2</span>'}</td>
    <td><button onclick="preview('${s.seg_id}')">预览</button>
        <button onclick="window.open('/api/sheet/${s.seg_id}')">联络表</button></td></tr>`).join('')
    || '<tr><td colspan=7 class=muted>暂无段</td></tr>';
  $('#seginfo').textContent = SEL.size ? `已选 ${SEL.size} 段` : '未选择';
}

function toggle(id,on){ on?SEL.add(id):SEL.delete(id); $('#seginfo').textContent = SEL.size?`已选 ${SEL.size} 段`:'未选择'; }

async function preview(id){
  const v = $('#player');
  v.src = '/api/preview/'+id; v.load(); v.play().catch(()=>{});
  toast('正在转码预览…首次较慢，之后走缓存');
}

async function doExport(lossless){
  const ids = [...SEL];
  if(!ids.length) return toast('先勾选要导出的段');
  const name = prompt('导出文件名（可留空）','') ?? '';
  const r = await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({seg_ids:ids, lossless, name})});
  if(!r.ok) return toast('导出请求失败');
  toast('导出任务已提交'); SEL.clear(); tickSegs(); tickJobs();
}

async function tickJobs(){
  const {jobs} = await (await fetch('/api/jobs')).json();
  $('#jobs').innerHTML = Object.entries(jobs).map(([id,j])=>`<tr>
    <td>${id}</td><td>${j.lossless?'无损MKV':'MP4'}</td>
    <td>${j.status=='done'?'<span class=g>完成</span>':j.status=='failed'?'<span class=r>失败</span>':'<span class=y>'+j.status+'</span>'}</td>
    <td>${j.progress||''} ${j.error?'<span class=r>'+j.error+'</span>':''}</td>
    <td>${j.status=='done'?`<button class=prim onclick="location.href='/api/download/${id}'">下载</button>`:''}</td>
  </tr>`).join('') || '<tr><td colspan=5 class=muted>暂无任务</td></tr>';
}

async function clearCache(){ const r = await (await fetch('/api/cache',{method:'DELETE'})).json(); toast('已清理 '+r.cleared+' 个预览缓存'); }

tickState(); tickSegs(); tickJobs();

async function tickBackend(){
  let m;
  try { m = await (await fetch('/api/backend')).json(); }
  catch(e){ $('#be-health').innerHTML='<span class=r>面板后端请求失败</span>'; return; }
  const badge = m.backend==='pg'
    ? '<span class=pill style="border-color:#58a6ff;color:#58a6ff">PostgreSQL</span>'
    : '<span class=pill style="border-color:#d29922;color:#d29922">SQLite</span>';
  $('#be-backend').innerHTML = badge;
  $('#be-target').textContent = m.target || '—';
  const healthy = m.healthy;
  $('#be-health').innerHTML = healthy ? '<span class=g>● 正常</span>' : '<span class=r>● 异常</span>';
  $('#be-rtt').innerHTML = healthy ? ('往返 '+m.rtt_ms+'ms')
    : ('<span class=r>'+(m.error||'连接失败')+'</span>'+(m.hint?'<br><span class=muted>'+m.hint+'</span>':''));
  if(m.backend==='pg' && m.pool){
    const p = m.pool, pct = p.util_pct;
    $('#be-poolbar').style.width = pct+'%';
    $('#be-poolbar').style.background = pct>90?'#f85149':pct>60?'#d29922':'#238636';
    $('#be-pool').innerHTML =
      '在用 <b>'+p.in_use+'</b>/'+p.maxconn+' ('+pct+'%) · 空闲 '+p.idle+'<br>'+
      '峰值 '+p.peak_in_use+' · 借出 '+p.total_borrowed+' 次 · 等待 '+p.total_waited+' 次<br>'+
      '等待均值 '+p.wait_ms_avg+'ms / 峰 '+p.wait_ms_max+'ms · 超时 <span class="'+(p.timeouts?'r':'g')+'">'+p.timeouts+'</span><br>'+
      '查询 '+p.query_count+' · 均值 '+p.query_ms_avg+'ms · 慢查询 <span class="'+(p.slow_count?'y':'g')+'">'+p.slow_count+'</span>';
  } else if(m.backend==='sqlite' && m.sqlite){
    $('#be-poolbar').style.width='0%';
    $('#be-pool').innerHTML = 'SQLite 无连接池<br>文件 '+m.sqlite.path+'<br>大小 '+m.sqlite.size_mb+' MB';
  } else { $('#be-pool').textContent='—'; }
  const parts = [];
  if(m.ledger && m.ledger.counts){
    const c = m.ledger.counts;
    const badges = Object.entries(c).map(function(kv){ return '<span class=pill>'+kv[0]+' '+kv[1]+'</span>'; }).join(' ');
    parts.push('账本: '+(badges||'<span class=muted>空</span>'));
  }
  if(m.backend==='pg' && m.server){
    const sv = m.server;
    parts.push('服务端连接 '+(sv.connections||'?')+'/'+(sv.max_connections||'?')+' ('+(sv.conn_pct||0)+'%) · 缓存命中 '+(sv.cache_hit_pct||0)+'%');
    parts.push('<span class="'+((m.locks||[]).length?'r':'g')+'">锁等待 '+((m.locks||[]).length)+'</span> · '+
               '<span class="'+((m.long_running||[]).length?'y':'g')+'">长事务 '+((m.long_running||[]).length)+'</span>');
  }
  $('#be-detail').innerHTML = parts.join(' &nbsp;|&nbsp; ');
  const card = document.getElementById('backendcard');
  card.style.borderColor = m.alert ? '#f85149' : '#30363d';
  if(m.alert) $('#be-detail').innerHTML +=
    ' &nbsp;<span class=r>⚠ '+ (m.alert_reasons||[]).join(' · ') +'</span>';
  if(m.backend==='pg'){
    const rows = [].concat(
      ((m.slow)||[]).map(function(r){ return {t:'慢SQL', k:r.sql, ms:r.ms}; }),
      ((m.locks)||[]).map(function(r){ return {t:'锁等待', k:'PID '+r.pid+' · '+r.query, ms:Math.round(r.wait_sec*1000)}; })
    ).sort(function(a,b){ return b.ms-a.ms; }).slice(0,10);
    const t = $('#be-slowtable tbody');
    if(t) t.innerHTML = rows.map(function(r){
        return '<tr><td><span class="'+(r.t==='锁等待'?'r':'y')+'">'+r.t+'</span></td><td>'+r.k+'</td><td>'+r.ms+'ms</td></tr>';
      }).join('') || '<tr><td colspan=3 class=muted>无慢查询/锁等待</td></tr>';
  }
}

async function reconnect(){
  const r = await (await fetch('/api/backend/reconnect',{method:'POST'})).json();
  toast(r.ok? ('已重连 '+r.backend) : ('重连失败: '+(r.error||'')));
  tickBackend();
}

async function tickScale(){
  const s = await (await fetch('/api/scale')).json();
  const card = document.getElementById('scalecard');
  if(!s.enabled){
    card.style.opacity=.6;
    document.getElementById('sc-used').textContent = s.reason||'未启用';
    if(s.local) document.getElementById('sc-used').innerHTML +=
      '<br><span class=muted>'+s.local.path+' · '+s.local.size_mb+'MB · '+(s.local.rows||0)+' 行</span>';
    ['sc-bar','sc-rate','sc-eta','sc-daemon','sc-parts','sc-audit']
      .forEach(function(id){ document.getElementById(id).innerHTML=''; });
    return;
  }
  card.style.opacity=1;
  const warns = s.warnings||[];
  document.getElementById('scale-warn').innerHTML = warns.length
    ? '<span class=y>⚠ '+warns.join(' · ')+'</span>' : '<span class=g>✓ 数据新鲜</span>';
  const pct = s.used_pct;
  const color = pct==null?'#8b949e':pct>=95?'#f85149':pct>=70?'#d29922':'#238636';
  document.getElementById('sc-bar').style.width = (pct||0)+'%';
  document.getElementById('sc-bar').style.background = color;
  document.getElementById('sc-used').innerHTML = pct==null ? '<span class=muted>无数据</span>'
    : '<b style="color:'+color+'">'+pct+'%</b> · '+s.used_gb+'GB / '+s.capacity_gb+'GB'+
      '<br><span class=muted>连接占用 '+(s.conn_pct||0)+'% · 样本 '+(s.sample_age_sec||0)+'s前</span>';
  const g = s.growth;
  document.getElementById('sc-rate').innerHTML = g ? '<b>'+g.gb_day+' GB/天</b>' : '<span class=muted>样本不足</span>';
  document.getElementById('sc-rate2').textContent = g ? (g.mb_hour+' MB/小时') : '需≥2条样本';
  const eta = s.eta_days;
  document.getElementById('sc-eta').innerHTML = eta==null ? '<span class=muted>—</span>'
    : '<b style="color:'+(eta<7?'#f85149':eta<30?'#d29922':'#3fb950')+'">'+eta+' 天</b>';
  document.getElementById('sc-eta2').textContent = eta==null ? '无增长或无容量' : '距容量上限';
  const d = s.daemon;
  document.getElementById('sc-daemon').innerHTML = d
    ? '<span class="'+(d.online?'g':'r')+'">● '+d.state+'</span>' : '<span class=r>● 无记录</span>';
  document.getElementById('sc-daemon2').innerHTML = d
    ? (d.spec+' · 本月resize '+d.resizes_month+'次'+(d.cooldown_sec>0?('<br>冷却剩 '+d.cooldown_sec+'s'):'')+
       (d.protect?'<br><span class=r>保护模式开启</span>':'')+'<br><span class=muted>心跳 '+d.heartbeat_age+'s前</span>')
    : '<span class=muted>控制器未运行</span>';
  if(s.partition_ok===false){
    document.getElementById('sc-parts').innerHTML = '<span class=r>分区信息不可用</span>';
  } else if(s.partitions && Object.keys(s.partitions).length){
    document.getElementById('sc-parts').innerHTML = Object.entries(s.partitions).map(function(e){
      const tbl = e[0], ps = e[1];
      const total = ps.reduce(function(a,p){ return a+p.mb; },0).toFixed(1);
      return '<div style="margin:5px 0"><b>'+tbl+'</b> <span class=muted>'+ps.length+'个分区 · '+total+'MB</span><br>'+
        ps.map(function(p){ return '<span class=pill style="margin:2px 4px 0 0">'+p.name+' '+p.mb+'MB/'+p.rows+'行</span>'; }).join('')+'</div>';
    }).join('');
  } else {
    document.getElementById('sc-parts').innerHTML = '<span class=muted>无分区（表未分区）</span>';
  }
  const au = s.audit||[];
  document.getElementById('sc-audit').innerHTML = au.length
    ? '<b class=muted>扩容审计</b><br>'+au.slice(0,6).map(function(a){
        return '<span class="'+(a.ok?'g':'r')+'">●</span> '+a.action+' <span class=muted>'+(a.detail||'')+' · '+new Date(a.ts*1000).toLocaleTimeString()+'</span>';
      }).join('<br>')
    : '<span class=muted>暂无扩容动作</span>';
}

async function tickSenses(){
  let s; try { s = await (await fetch('/api/senses')).json(); } catch(e){ return; }
  function cell(title, body){
    return '<div class=card style="margin:0;min-width:200px"><b>'+title+'</b><div style="margin-top:6px">'+body+'</div></div>';
  }
  let h = '';
  const dv = s.devour||{};
  h += cell('👁 看 · 吞噬', dv.error ? ('<span class=r>'+dv.error+'</span>')
      : ('帧 <b>'+(dv.frames||0)+'</b> · 缺口 <span class="'+(dv.gaps?'r':'g')+'">'+(dv.gaps||0)+'</span> · 段 '+(dv.segments||0)));
  const mic = s.mic||{};
  h += cell('👂 听 · ASR', mic.table ? ('<span class=muted>'+mic.table+'</span>')
      : ('转写 <b>'+(mic.done||0)+'</b> · 失败 <span class="'+(mic.failed?'y':'g')+'">'+(mic.failed||0)+'</span>'+
         (s.mic_events && s.mic_events.keyword ? (' · 关键词 <span class=y>'+s.mic_events.keyword+'</span>') : '')));
  const vo = s.voice||{};
  h += cell('🗣 说 · TTS', vo.table ? ('<span class=muted>'+vo.table+'</span>')
      : ('完成 <b>'+(vo.done||0)+'</b> · 队列 '+(vo.queued||0)+' · 失败 <span class="'+(vo.failed?'y':'g')+'">'+(vo.failed||0)+'</span>'));
  h += cell('🖐 控 · 脉冲', '调用 <b>'+((s.pulse||{}).runs||0)+'</b> 次'+
      (((s.pulse||{}).last_ts) ? ('<br><span class=muted>最近 '+new Date(s.pulse.last_ts*1000).toLocaleTimeString()+'</span>') : ''));
  h += cell('🧠 想 · 大脑', '网关 '+(s.brain&&s.brain.gateway?'<span class=g>已配</span>':'<span class=muted>未配</span>')+' · '+
      'Ollama '+(s.brain&&s.brain.ollama?'<span class=g>已配</span>':'<span class=muted>未配</span>')+' · '+
      'Coder '+((s.coder||{}).runs||0)+' 次');
  const cx = s.codex||{};
  h += cell('⌨ 编程工具 · Codex', cx.available ? ('<span class=g>就绪</span> · 调用 '+((s.codex||{}).runs||0)+' 次')
      : ('<span class=y>不可用</span><br><span class=muted>'+((cx.detail||'').slice(0,80))+'</span>'));
  document.getElementById('sn-cards').innerHTML = h;
}

tickBackend(); setInterval(tickBackend, 3000);
tickScale(); setInterval(tickScale, 5000);
async function tickMedia(){
  let el=document.getElementById('mediacard');
  try{
    const s=await (await fetch('/api/media/queue/stats')).json();
    if(!el){el=document.createElement('div');el.className='card';el.id='mediacard';
      const a=document.getElementById('backendcard'); if(a&&a.after) a.after(el); else return;}
    if(s.coverage==='unavailable'){el.innerHTML='<b>🎞 生成队列</b><div style="margin-top:6px"><span class=r>采集不可用</span></div>';return;}
    const d=s.data||{};const q=d.depth||{};const w=d.wait||{};
    const fr=(d.failure_rate_kind==='observed')?((d.failure_rate*100).toFixed(1)+'%'):'<span class=muted>暂无样本</span>';
    let vr='';
    try{const v=await (await fetch('/api/media/vram')).json();const rv=v.data||{};
      if(rv.reserved) vr+='<br>显存预算 '+rv.reserved.used_mb+'/'+rv.reserved.total_mb+'MB';
      if(rv.real) vr+=' · 真实 '+rv.real.used_mb+'/'+rv.real.total_mb+'MB';
      else if(rv.real_source==='unavailable') vr+=' · <span class=muted>真实显存不可用</span>';
    }catch(e){}
    el.innerHTML='<b>🎞 生成队列</b><div style="margin-top:6px">'
      +'排队 <b>'+(q.queued||0)+'</b> · 运行 <b>'+(q.running||0)+'</b> · 死信 <span class="'+((q.dead||0)?'r':'g')+'">'+(q.dead||0)+'</span><br>'
      +'最老等待 <b>'+Math.round(w.oldest_runnable_age||0)+'s</b> · 退避 '+(w.backoff_count||0)+'<br>'
      +'失败率 '+fr+' <span class=muted>(近'+Math.round((d.window_sec||3600)/60)+'分钟)</span>'+vr+'</div>';
  }catch(e){}
}
async function tickWitness(){
  let el=document.getElementById('witnesscard');
  try{
    const w=await (await fetch('/api/body/witnesses')).json();
    if(!el){el=document.createElement('div');el.className='card';el.id='witnesscard';
      const a=document.getElementById('mediacard')||document.getElementById('backendcard');
      if(a&&a.after) a.after(el); else return;}
    const q=w.quorum||{}, p=w.probe||{}, sealed=w.latest_sealed||null;
    const ev=w.evidence||{}, evw=ev.witnesses||[];
    const ws=w.witnesses||[];
    const crit=ws.some(x=>['invalid','disagreement'].includes(x.live_status))
      || evw.some(x=>x.identity_status==='critical_conflict');
    const degraded=(q.valid<q.required)||p.stale||q.status!=='healthy'
      || (ev.valid_count!=null&&ev.valid_count<ev.required);
    const col=crit?'r':(degraded?'y':'g');
    const label=crit?'冲突 · 需人工裁决':(p.stale?'快照过期':((q.valid<q.required)?('见证不足 '+(q.valid||0)+'/'+(q.required||0)):'健康'));
    const age=(p.age_seconds==null)?'未知':Math.round(p.age_seconds)+'s';
    // ★证据等级行：这一票算不算、为什么不算（与数字人工具同一份快照）
    const REASON={CONFIG_IDENTITY_MISMATCH:'身份配置与服务端不符',
      CONFIG_OWNER_MISMATCH:'所有者配置与服务端不符',
      AUTHORITY_IDENTITY_CONFLICT:'权威身份冲突（已隔离）',
      IDENTITY_UNVERIFIED:'身份验证不足',IDENTITY_FIELD_FORBIDDEN:'无权读取身份字段',
      IDENTITY_FIELD_UNSUPPORTED:'服务端不支持身份查询',AUTH_FAILED:'凭据认证失败',
      UNREACHABLE:'身份探测不可达',EVIDENCE_BELOW_REQUIRED:'证据等级不足',
      CONTENT_NOT_VERIFIED:'内容对不上',CREDENTIAL_DOMAIN_DUPLICATE:'与另一见证共用凭据',
      NOT_LIVE_YET:'还没到实时区间',IDENTITY_STALE:'身份证据已过期'};
    let evrows=evw.map(function(x){
      const ok=x.vote_eligible===true, unk=(x.vote_eligible==null);
      const c=unk?'':(ok?'g':(x.identity_status==='critical_conflict'?'r':'y'));
      return '<div class=muted style="margin-top:3px"><span class="'+c+'">'+x.witness_id+'</span>'
        +' · '+(x.identity_status||'-')+' · 证据 '+(x.evidence_level||'-')
        +' · '+(unk?'未知':(ok?'<span class=g>计入</span>':'<span class='+(c||'y')+'>不计</span>'))
        +(x.reason_code?(' · '+((REASON[x.reason_code])||x.reason_code)):'')
        +(x.isolated?' · <span class=r>已隔离</span>':'')+'</div>';
    }).join('');
    if(evw.length) evrows='<div style="margin-top:6px" class=muted>证据等级（快照 #'
      +((ev.revision==null)?'-':ev.revision)+'）</div>'+evrows;
    let rows=ws.map(function(x){
      const c=({valid:'g',pending:'','unreachable':'y',missing:'y',invalid:'r',disagreement:'r'})[x.live_status]||'';
      return '<div class=muted style="margin-top:3px">'+'<span class="'+c+'">'+x.witness_id+'</span>'
        +' · '+x.live_status+' · kid '+((x.kid)||'-')
        +' · 实时自#'+((x.live_from_seq==null)?'-':x.live_from_seq)
        +' · 回填至#'+((x.backfilled_through_seq==null)?'-':x.backfilled_through_seq)+'</div>';
    }).join('');
    if(!ws.length) rows='<div class=muted style="margin-top:4px">未配置见证（BODY_WITNESSES 为空）</div>';
    el.innerHTML='<b>🔗 身体登记链 · 外部见证</b><div style="margin-top:6px">'
      +'实时有效见证 <b class="'+col+'">'+((q.valid==null)?'-':q.valid)+'/'+((q.required==null)?'-':q.required)+'</b>'
      +' · <span class="'+col+'">'+label+'</span><br>'
      +'核验年龄 <b>'+age+'</b>'+(p.stale?' <span class=y>⚠过期</span>':'')
      +' · <span class=muted>sealed@'+((sealed&&sealed.seq!=null)?('#'+sealed.seq+'（当时）'):'-')+'</span>'
      +evrows+rows+'</div>';
  }catch(e){}
}
tickWitness(); setInterval(tickWitness, 10000);
tickMedia(); setInterval(tickMedia, 5000);
tickSenses(); setInterval(tickSenses, 5000);
setInterval(tickState, 3000); setInterval(tickSegs, 5000); setInterval(tickJobs, 2000);
</script></html>"""

# ── V9 路由（帧证据链 / 覆盖率 / 校准 / 语音情绪）──
from panel.routes import calibrations as _calibrations_routes
from panel.routes import coverage as _coverage_routes
from panel.routes import frame_evidence as _frame_evidence_routes
from panel.routes import monitor as _monitor_routes
from panel.routes import voice_emotion as _voice_emotion_routes
from senses.sqldialect import txn

app.include_router(_frame_evidence_routes.router, prefix="/api")
app.include_router(_coverage_routes.router, prefix="/api")
app.include_router(_calibrations_routes.router, prefix="/api")
app.include_router(_voice_emotion_routes.router)   # 该路由自带 /api 前缀
app.include_router(_monitor_routes.router)         # 自带 /api 前缀（session/db-health/media queue）
from panel import media_api as _media_api_routes  # noqa: E402
app.include_router(_media_api_routes.router, prefix="/api")  # 队列指标/显存/死信/终态事件
from panel.routes import body as _body_routes
from panel.routes import body_witness as _body_witness_routes

app.include_router(_body_routes.router)          # 自带 /api/body 前缀（登记链/责任页/覆盖）
app.include_router(_body_witness_routes.router)  # 自带 /api/body/witnesses 前缀（见证/回填区间/证据）
from panel.routes import body_tools as _body_tools_routes
from panel.routes import digital_human as _digital_human_routes

app.include_router(_body_tools_routes.router)     # /api/body/(tools|snapshots) 只读工具面
app.include_router(_digital_human_routes.router)  # /api/digital-human（实时行 + 问询 + SSE）

@app.get("/digital-human", response_class=HTMLResponse)
async def digital_human_page() -> str:
    """数字人对讲页：实时见证行 + 只读工具问询 + 语音流水。"""
    from panel.digital_human_page import DIGITAL_HUMAN_PAGE
    return DIGITAL_HUMAN_PAGE

@app.get("/media", response_class=HTMLResponse)
async def media_monitor_page() -> str:
    """生成队列监控页（可下钻）：深度/等待/失败率/显存/死信/事件流"""
    from panel.media_page import MEDIA_PAGE
    return MEDIA_PAGE

# ── 生成队列监控：队列深度/等待/失败率/显存/死信 → 面板卡 + 告警状态机 ──
from media.queue import JobQueue as _JobQueue, ensure_tables as _ensure_media_tables
from media.scheduler import VramBudget as _VramBudget
from panel.alerts import AlertManager as _AlertManager
from media.monitor import MediaMonitor as _MediaMonitor

_MEDIA_BUDGET = _VramBudget(int(os.environ.get("MEDIA_VRAM_MB", "8192")))
_MEDIA_MONITOR = None


@app.on_event("startup")
async def _start_body_services() -> None:
    """装配身体服务：app.state.ledger + 语音总线 + 见证实时探测 + 快照采样 + 定期全链复核。

    接线说明（诚实）：
      · 见证探测 loop 在 body.witness_runtime（身份探测 + 内容复核 + 跳变写 outbox + 播报）；
      · 快照采样 loop 在 body.tools.collect（吞噬/覆盖/队列 → body_read_snapshots）；
      · 两者都靠 recheck_leader 门控，多 worker 时只有一个进程在写。
    """
    try:
        from panel.deps import db as _body_db
        app.state.ledger = _body_db
        if not hasattr(app.state, "anchor_multi"):
            app.state.anchor_multi = None
        _wire_voice_bus()
        # ★SQLite 下 recheck_leader 会"非单进程就不写"：面板默认单进程，
        #   这里显式声明单 worker，否则见证探测/快照采样会静默全跳过（看着绿，其实没跑）。
        if os.environ.get("BODY_SINGLE_WORKER") is None:
            os.environ["BODY_SINGLE_WORKER"] = "1"
            print("[panel] BODY_SINGLE_WORKER=1（单进程身体服务）；多 worker 部署请显式设 0")
        if os.environ.get("BODY_RECHECK", "1") != "0":
            from body import recheck as _recheck
            await _recheck.start(app)
        if os.environ.get("BODY_WITNESS_PROBE", "1") != "0":
            from body import witness_runtime as _wr
            await _wr.start(app)
            # 快照采样与探测同一进程：面板只读库写不了，必须在这里写
            from body.tools import collect as _collect
            _collect_stop = threading.Event()
            app.state.collect_stop = _collect_stop

            async def _collect_loop():
                await _collect.refresh_loop(_body_db, frame_dir=FRAME_DIR,
                                            scan_ledger=get_ledger(), stop=_collect_stop)
            app.state.collect_task = asyncio.create_task(_collect_loop())
    except Exception as exc:  # noqa: BLE001
        print("[panel] body services skipped:", repr(exc))


def _wire_voice_bus() -> None:
    """语音总线：TTS 可选（没有也能把话显示到页面），页面推送走数字人的订阅表。"""
    try:
        from body.voice_bus import VoiceBus
        from panel.routes.digital_human import publish
        from panel.routes import digital_human as _dh
        _dh.LEDGER_IMPORT = app.state.ledger          # 工具审计要写身体库（可写）
        tts = None
        if os.environ.get("BODY_VOICE", "1") != "0":
            try:
                from senses.voice import VoiceAdapter
                tts = VoiceAdapter()
            except Exception as exc:                  # noqa: BLE001
                print("[panel] tts unavailable:", repr(exc))
        app.state.voice_bus = VoiceBus(tts, ledger=app.state.ledger,
                                       on_page_event=publish)
    except Exception as exc:  # noqa: BLE001
        print("[panel] voice bus skipped:", repr(exc))


@app.on_event("shutdown")
async def _stop_body_services() -> None:
    try:
        from body import recheck as _recheck, witness_runtime as _wr
        await _recheck.stop(app)
        await _wr.stop(app)
        stop = getattr(app.state, "collect_stop", None)
        if stop is not None:
            stop.set()
        t = getattr(app.state, "collect_task", None)
        if t is not None:
            t.cancel()
    except Exception:
        pass


@app.on_event("startup")
async def _body_boot_check() -> None:
    """启动自检：登记链全链复核 + 锚点交叉复核 → 写 body_boot_checks（重启不失忆）"""
    try:
        from migrations.runner import apply_pending
        from body.boot import boot_check
        from panel.deps import db as db   # ★与面板路由同一个身体库（单一事实源）
        res = await boot_check(db, write_db=db, record=True)
        print(f"[panel] body boot_check ok={res['ok']} head_seq={res['head_seq']} "
              f"chain={res['chain']['ok']} anchors={res['anchors']['ok']} "
              f"reason={res['reason']}")
        if res.get("should_freeze"):
            setattr(db, "chain_frozen", True)
            print("[panel] 链断 + fail_mode=freeze：登记写入已封冻，等待处置")
    except Exception as exc:
        print("[panel] body boot_check skipped:", repr(exc))


@app.on_event("startup")
async def _start_media_monitor() -> None:
    global _MEDIA_MONITOR
    try:
        led = get_ledger()
        _ensure_media_tables(led)
        _media_api_routes.router.ledger = led
        _media_api_routes.router.queue = _JobQueue(led)
        _media_api_routes.router.budget = _MEDIA_BUDGET
        alerts = _AlertManager(led, lambda: {"backend_ok": True}, recover_confirm=1)
        voice = None
        try:
            from senses.voice import VoiceAdapter
            voice = VoiceAdapter(ledger=led)
            voice.start()
        except Exception:
            voice = None
        _MEDIA_MONITOR = _MediaMonitor(
            led, alerts, voice,
            interval=float(os.environ.get("MEDIA_MONITOR_SEC", "30")),
            queue=_media_api_routes.router.queue, budget=_MEDIA_BUDGET)
        if os.environ.get("MEDIA_MONITOR", "1") != "0":
            _MEDIA_MONITOR.start()
        print("[panel] media monitor started (depth/wait/failure/vram/dead)")
    except Exception as exc:
        print("[panel] media monitor skipped:", repr(exc))


# ── 启动接线（对话里的收口图）: apply_pending() → boot_check ──
@app.on_event("startup")
async def _boot_migrations() -> None:
    """面板启动时自动跑迁移并校验（失败只告警，不阻断面板起来）。"""
    try:
        from migrations.runner import apply_pending, boot_check
        from panel.deps import db as _db
        applied = await apply_pending(_db)
        await boot_check(_db)
        if applied:
            print(f"[panel] migrations applied: {applied}")
    except Exception as exc:  # 迁移有问题不应让只读面板 500 到底
        print(f"[panel] migration bootstrap skipped: {exc!r}")


@app.on_event("startup")
async def _start_reaper() -> None:
    """缓存回收后台循环：_preview/_cache 超限自动清最旧。"""
    REAPER.start()
    print(f"[panel] cache reaper started: {[b.name for b in REAPER.budgets]}")
