# panel/server.py —— 触手总控面板 + 回放端 · dev: 自由的风
# 一键预览 / 勾选导出 / 状态看板 / 缓存管理
from core.swallow import swallow as _swallow
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

# 全内置纪律：关掉 FastAPI 自带的 /docs 与 /redoc（它们从 CDN 拉 swagger 资源 = 外链），
# 由本服务自己渲染一份内置接口文档页（见下方 builtin_docs）。
app = FastAPI(title="GBT小土豆V9 · 总控台", docs_url=None, redoc_url=None)

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
    except Exception as e:
        from core import swallow as _sw; _sw.swallow(__file__, e)



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
    # 身体服务分项状态：哪一块没起、为什么 —— /api/health 直接看得到（别再"看着绿其实没跑"）
    parts = getattr(app.state, "body_services", None) or {}
    out["body_parts"] = {k: bool(v.get("ok")) for k, v in parts.items()}
    if parts:
        out["body_missing"] = [k for k, v in parts.items() if not v.get("ok")]
    return out


@app.get("/api/octop/health")
def api_octop_health():
    """同源探活 Octop 底座（8766）：浏览器直连会吃 CORS，改由服务端探。

    这是**固定**的本机端口（不是用户传入的 URL），只做健康读数，不代理任何请求体。
    """
    import socket
    port = int(os.environ.get("OCTOP_PORT", "8766"))
    out = {"port": port, "up": False}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.5)
            out["up"] = s.connect_ex(("127.0.0.1", port)) == 0
    except Exception as exc:                                  # noqa: BLE001
        out["error"] = type(exc).__name__
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
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)


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
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

        # SQLite 也把「斜率/耗尽预测/控制器」算出来（以前这三格是"—"，看着像没做）
        try:
            from audit import scale_sqlite as _ss
            _ss.sample(led, rows=local.get("rows"))          # 每次看面板就采一次样
            raw = _ss.read(led)
            # 面板那三格读的是 growth/used_gb/capacity_gb/daemon —— 这里必须**对齐字段名**，
            # 否则 SQLite 后端会显示 "undefinedGB / 无记录"（看着像没做，其实是名字对不上）。
            mb_day = raw.get("growth_mb_day")
            cap_mb = raw.get("cap_mb") or 0
            samples = raw.get("samples") or 0
            ctrl = raw.get("controller")
            ctrl_zh = {"ok": "正常", "watch": "观察中", "act": "需动作"}.get(str(ctrl), str(ctrl or "—"))
            age = raw.get("sample_age_sec")
            return {"enabled": True, "backend": "sqlite", "local": local,
                    "used_pct": raw.get("used_pct"),
                    "used_gb": round((raw.get("size_mb") or 0) / 1024, 3),
                    "capacity_gb": round(cap_mb / 1024, 1),
                    "cap_source": raw.get("cap_source"),
                    "conn_pct": None,
                    "sample_age_sec": (round(age, 1) if isinstance(age, (int, float)) else None),
                    "growth": ({"gb_day": round((mb_day or 0) / 1024, 4),
                                "mb_hour": round((mb_day or 0) / 24, 3)}
                               if mb_day is not None else None),
                    "eta_days": raw.get("eta_days"),
                    "controller": ctrl, "controller_why": raw.get("controller_why"),
                    "daemon": {"online": True,
                               "state": "SQLite · 控制器" + ctrl_zh,
                               "spec": "按设计不自动 resize（表级归档 + 文件轮转）",
                               "resizes_month": 0, "cooldown_sec": 0,
                               "heartbeat_age": (round(age, 1) if isinstance(age, (int, float)) else 0),
                               "protect": ctrl == "act"},
                    "partitions": {}, "audit": [],
                    "warnings": ([] if samples >= 2 else [f"样本 {samples} 条（需≥2条才算斜率）"]),
                    "reason": raw.get("reason", ""),
                    "note": "SQLite 后端：体量采样 + 斜率/ETA/控制器均为真读数"}
        except Exception as exc:                              # noqa: BLE001
            return {"enabled": False, "backend": "sqlite", "local": local,
                    "reason": f"SQLite 扩容读数失败：{type(exc).__name__}"}

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
        # 表不存在 ≠ 通道没建：听写通道是本机 SAPI（见 out["asr"]），这条只是**历史计数**。
        out["mic"] = {k: v for k, v in rows} if rows is not None else {}
        if rows is None:
            out["mic_note"] = "还没有转写记录（点卡片上的「试转写」即可产生第一条）"
        ev = _q("SELECT kind, COUNT(*) FROM mic_events GROUP BY kind")
        if ev is not None:
            out["mic_events"] = {k: v for k, v in ev}
        vrows = _q("SELECT status, COUNT(*) FROM voice_jobs GROUP BY status")
        out["voice"] = {k: v for k, v in vrows} if vrows is not None else {}
        if vrows is None:
            out["voice_note"] = "还没有配音记录（说通道就绪：本机 SAPI 台湾腔）"

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
    try:
        from senses import voice_sapi as VS          # 免费离线听写通道（带 TTL 缓存，轮询不拖机器）
        out["asr"] = VS.asr_status()
    except Exception as e:  # noqa: BLE001
        out["asr"] = {"可用": False, "说明": f"{type(e).__name__}: {e}"}
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


# ── 品牌 Logo / favicon（全站网页与浏览器标签都补上主人给的 Logo）──
_LOGO_PNG = Path(__file__).resolve().parent / "static" / "logo.png"
_LOGO_ICO = Path(__file__).resolve().parent / "static" / "favicon.ico"


def _builtin_docs_html() -> str:
    """内置接口文档：从本服务自己的 OpenAPI 生成，不引任何外部资源（不外链）。"""
    spec = app.openapi() or {}
    rows = []
    for path, item in sorted((spec.get("paths") or {}).items()):
        for method, op in sorted((item or {}).items()):
            if method.lower() not in ("get", "post", "put", "delete", "patch"):
                continue
            rows.append(
                f'<tr><td class=num><b>{method.upper()}</b></td><td><code>{path}</code></td>'
                f'<td class=muted>{(op or {}).get("summary") or (op or {}).get("operationId") or ""}'
                f'</td></tr>')
    title = spec.get("info", {}).get("title", "GBT小土豆V9")
    # 本页以前**自带一套 GitHub-dark 调色板**、绕过统一注入 —— 全站唯一一处"不在体系里"的页面。
    # 现在只留结构，视觉交给 skins.ui_design（统一 token + 导航 + 按键），颜色不再硬编码。
    html = ("<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            "<link rel=icon href=/favicon.ico><title>API 文档（内置）</title></head><body>"
            f"<h1>{title} · 接口文档（全内置，无外链资源）</h1>"
            f"<p class=muted>接口数 {len(rows)} · 原始 schema：<a href=/openapi.json>"
            "/openapi.json</a> · 回 <a href=/>总控台</a></p>"
            "<table><thead><tr><th>方法</th><th>路径</th><th>说明</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></body></html>")
    try:
        from skills.ui_design import inject
        return inject(html, "/docs")
    except Exception:                                         # noqa: BLE001
        return html


@app.get("/docs", response_class=HTMLResponse)
def builtin_docs():
    return _builtin_docs_html()


@app.get("/logo.png")
def brand_logo():
    if not _LOGO_PNG.is_file():
        raise HTTPException(404, "Logo 资源缺失")
    return FileResponse(_LOGO_PNG, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/favicon.ico")
def brand_favicon():
    src = _LOGO_ICO if _LOGO_ICO.is_file() else _LOGO_PNG
    if not src.is_file():
        raise HTTPException(404, "favicon 资源缺失")
    return FileResponse(src, media_type="image/x-icon" if src.suffix == ".ico" else "image/png",
                        headers={"Cache-Control": "public, max-age=3600"})

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
def _dock(html: str, current: str = "/") -> str:
    """给任何页面注入统一导航 + AI 停靠坞（AI 穿透所有页面）。失败不改坏原页面。"""
    try:
        from skills.ui_design import inject
        return inject(html, current)
    except Exception:                                          # noqa: BLE001
        return html


@app.get("/", response_class=HTMLResponse)
def page():
    return _dock(PAGE, "/")

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
 /* 按键/标签样式统一在 skills/ui_design（这里不再另写一套；旧的 button/.prim 已删） */
 video{width:100%;max-height:360px;background:#000;border-radius:6px;margin-top:8px}
 .bar{height:6px;background:#21262d;border-radius:3px;overflow:hidden;margin-top:6px}
 .bar>i{display:block;height:100%;background:#238636}
 #toast{position:fixed;right:20px;bottom:20px;background:#161b22;border:1px solid #30363d;
        padding:10px 16px;border-radius:8px;display:none}
</style>
<h2>GBT小土豆V9 · 总控台 <span class=muted id=ts></span></h2>

<!-- 导航只留一份：统一导航（inject 注入的 nav.top，带分组与胶囊样式）。
     这里原先还有一条手写"导航"条 —— 与统一导航重复，样式各写各的，页面上看着就是两排。 -->
<span class=muted id=octopstate style="font-size:12px"></span>

<!-- 触手编队 + 只读工具快照（之前只有 API，现在上台面） -->
<div class=card id=fleetcard><b>触手编队 · 统一密钥 · 只读工具快照</b>
  <span class=muted id=fleetstate>加载中…</span>
  <div id=fleetbody></div>
</div>
<script>
async function tickFleet(){
  var el=document.getElementById('fleetbody'), st=document.getElementById('fleetstate');
  try{
    var f=await (await fetch('/api/fleet/status')).json();
    var d=await (await fetch('/api/body/snapshots')).json();
    var c=f.config||{}, r=f.drives||{};
    var rows='<div class=row><div class=card><div class=muted>编队规模</div>'+
      '<b class="'+((c.n>=100)?'g':'y')+'">'+(c.n||0)+' 根</b>'+
      '<div class=muted>角色 '+(c.roles?Object.keys(c.roles).length:0)+' 类 · 指挥官 '+(c.commander||'-')+'</div></div>'+
      '<div class=card><div class=muted>统一密钥（只显指纹）</div>'+
      '<b>'+(c.key_id||'未配置')+'</b><div class=muted>来源 '+(c.key_source||'-')+' · '+
      (c.same_key?'<span class=g>全编队同一把</span>':'<span class=r>不统一</span>')+'</div></div>'+
      '<div class=card><div class=muted>驱动审计</div><b>'+(r.drives||0)+' 次</b>'+
      '<div class=muted>用过 '+(r.tentacles_used||0)+' 根 · tokens '+(r.tokens||0)+'</div></div></div>';
    var tools='<table><tr><th>只读工具</th><th>域</th><th>快照版本</th><th>安全句（它自己说的话）</th></tr>';
    for(var k in d){var v=d[k]||{};
      tools+='<tr><td>'+k+'</td><td>'+(v.domain||'-')+'</td><td class="'+(v.stale?'y':'g')+'">'+
        (v.revision==null?'-':('#'+v.revision))+(v.stale?' ⚠过期':'')+'</td><td class=muted>'+
        ((v.safe_sentence||'').slice(0,72))+'</td></tr>';}
    tools+='</table>';
    el.innerHTML=rows+tools;
    st.textContent='';
  }catch(e){ st.textContent='（编队/工具面读不到：'+e.message+'）'; }
}
async function tickOctop(){
  var el=document.getElementById('octopstate');
  try{ var j=await (await fetch('/api/octop/health',{cache:'no-store'})).json();
    el.innerHTML = j.up ? ' <span class=g>● 底座在跑（端口 '+j.port+'）</span>'
                        : ' <span class=y>● 底座未运行（点左边链接会尝试拉起）</span>';
  }catch(e){ el.innerHTML=' <span class=muted>● 底座状态未知</span>'; }
}
tickFleet(); setInterval(tickFleet, 15000);
tickOctop(); setInterval(tickOctop, 10000);
</script>

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
       (d.protect?'<br><span class=r>保护模式开启</span>':'')+'<br><span class=muted>心跳 '+d.heartbeat_age+'s前</span>'+
       (s.controller_why?('<br><span class=muted>'+s.controller_why+'</span>'):''))
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

async function pulseTest(btn){
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '脉冲中…';
  try{
    const d = await (await fetch('/api/pulse/selftest', {method:'POST'})).json();
    btn.textContent = d.ok ? ('✅ 插上 '+(d['插上']||[]).length+' 个') : '❌ 未成';
    // 注意：这里必须写 \\n（两个字符）—— 直接写单个反斜杠 n 会被 Python 三引号当成真换行，
    // 把 JS 单引号字符串截断，整块脚本报 "Invalid or unexpected token"，全页卡片全哑（真踩过）
    alert('插上：' + (d['插上']||[]).join('、') + '\\n失败：' + (d['失败']||[]).join('、') +
          '\\n分发：' + JSON.stringify(d['分发']));
  }catch(e){ btn.textContent = '失败：' + e; }
  setTimeout(function(){ btn.textContent = old; btn.disabled = false; }, 4000);
  tickSenses();
}
async function asrTest(btn){
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '转写中…（约 3~6 秒）';
  try{
    const r = await fetch('/api/asr/selftest', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({})});
    const d = await r.json();
    btn.textContent = d.ok ? ('✅ 命中 '+Math.round((d['命中率']||0)*100)+'%') : ('❌ '+(d.reason||'未成'));
    // '\\n' 必须双反斜杠：单写会被 Python 三引号变成真换行 → JS 串被截断 → 全页脚本哑掉
    alert('原文：' + (d['原文']||'') + '\\n转写：' + (d['转写']||'') +
          '\\n命中率：' + d['命中率'] + ' · 识别器：' + (d['识别器']||'') + ' · ' + d.ms + 'ms');
  }catch(e){ btn.textContent = '失败：' + e; }
  setTimeout(function(){ btn.textContent = old; btn.disabled = false; }, 4000);
  tickSenses();
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
  const mic = s.mic||{}, asr = s.asr||{};
  let asrBody;
  if (asr['可用']) {
    asrBody = '<span class=g>就绪</span> <span class=muted>('+(asr['语言']||'')+' · '+(asr['识别器']||'')+
      ' · 离线/0显存)</span><br>转写 <b>'+(mic.done||0)+'</b>'+
      ((mic.failed) ? (' · 失败 <span class=y>'+mic.failed+'</span>') : '')+
      ((s.mic_events && s.mic_events.keyword) ? (' · 关键词 <span class=y>'+s.mic_events.keyword+'</span>') : '')+
      (s.mic_note ? ('<div class=muted style="margin-top:4px">'+s.mic_note+'</div>') : '')+
      '<div style="margin-top:6px"><button class=ghost onclick="asrTest(this)">试转写（自证）</button></div>';
  } else {
    asrBody = '<span class=y>未就绪</span><br><span class=muted>'+((asr['说明']||'').slice(0,90))+'</span>'+
      (asr['装中文识别器的方法'] ? ('<br><span class=muted>'+asr['装中文识别器的方法']+'</span>') : '');
  }
  h += cell('👂 听 · ASR', asrBody);
  const vo = s.voice||{};
  h += cell('🗣 说 · TTS', '<span class=g>就绪</span> <span class=muted>(本机 SAPI 台湾腔 · 离线/0显存)</span><br>'+
      '完成 <b>'+(vo.done||0)+'</b> · 队列 '+(vo.queued||0)+
      ' · 失败 <span class="'+(vo.failed?'y':'g')+'">'+(vo.failed||0)+'</span>'+
      (s.voice_note ? ('<div class=muted style="margin-top:4px">'+s.voice_note+'</div>') : ''));
  h += cell('🖐 控 · 脉冲', '调用 <b>'+((s.pulse||{}).runs||0)+'</b> 次'+
      (((s.pulse||{}).last_ts) ? ('<br><span class=muted>最近 '+new Date(s.pulse.last_ts*1000).toLocaleTimeString()+'</span>')
                              : '<br><span class=muted>待命（可点下面自检真跑一次）</span>')+
      '<div style="margin-top:6px"><button class=ghost onclick="pulseTest(this)">跑一次脉冲（自检）</button></div>');
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
      else if(rv.real_source==='unavailable') vr+=' · <span class=muted>真实显存探测不可用'
        +'（本机无 NVIDIA / 未装 nvml；按设计不占本地显存，活走云主管道）</span>';
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
    if(!ws.length){
      // 未登记见证 ≠ 系统故障：这是**配置项**。把"缺什么、怎么开"直接写在卡上，并给一键开通。
      rows='<div class=muted style="margin-top:6px">还没有登记见证 → 仍可与本地链对账，但拿不到"外部独立锚"。</div>'
        +'<div class=muted style="margin-top:4px">要开通：设 <code>BODY_WITNESS_W1_ENDPOINT/BUCKET/'
        +'ACCESS_KEY_ID/SECRET_ACCESS_KEY</code>（S3 兼容，如 Cloudflare R2 / Backblaze B2），'
        +'两处独立的存储即为 2 个见证。</div>'
        +'<div style="margin-top:6px"><button class=btn onclick="witnessOnboard(1)">先探测(dry-run)</button> '
        +'<button class=btn onclick="witnessOnboard(0)">开通并登记</button> '
        +'<span id=wmsg class=muted></span></div>';
    }
    el.innerHTML='<b>🔗 身体登记链 · 外部见证</b><div style="margin-top:6px">'
      +'实时有效见证 <b class="'+col+'">'+((q.valid==null)?'-':q.valid)+'/'+((q.required==null)?'-':q.required)+'</b>'
      +' · <span class="'+col+'">'+label+'</span><br>'
      +'核验年龄 <b>'+age+'</b>'+(p.stale&&ws.length?' <span class=y>⚠过期</span>':'')
      +' · <span class=muted>sealed@'+((sealed&&sealed.seq!=null)?('#'+sealed.seq+'（当时）'):'-')+'</span>'
      +evrows+rows+'</div>';
  }catch(e){}
}
async function witnessOnboard(dry){
  var m=document.getElementById('wmsg'); if(m) m.textContent='执行中…';
  try{
    var r=await fetch('/api/witness/onboard?dry_run='+dry,{method:'POST'});
    var d=await r.json();
    if(m) m.textContent = (d['已登记']&&d['已登记'].length)
      ? ('已处理 '+d['已登记'].length+' 个见证'+(dry?'（dry-run）':'（已登记）'))
      : ('未开通：'+(d['计划']&&d['计划']['还缺']?('缺 '+d['计划']['还缺'].join('/')):'见计划'));
    setTimeout(function(){location.reload()},1200);
  }catch(e){ if(m) m.textContent='失败：'+e; }
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
from panel import friends_page as _friends_page   # AI 朋友圈（EigenFlux 只读接入）
app.include_router(_friends_page.router)          # 自带 /api/friends 前缀；页面在 /api/friends/page
from panel.routes import body_tools as _body_tools_routes
from panel.routes import digital_human as _digital_human_routes
from panel.routes import fleet as _fleet_routes
from panel import ai_center as _ai_center          # AI 指挥中心（/command）+ /api/ai/ask
from panel import hub_page as _hub_page           # 数据中枢（/hub）+ 自主层/镜像/信息素/调度接口
app.include_router(_hub_page.router)

from panel import cloud_page as _cloud_page        # 云插件中枢（/cloud）+ /api/cloud/*
from panel import db_page as _db_page              # 数据库编队中枢（/db）+ /api/db/*
from panel import cloud_status as _cloud_status    # 连接状态表 + 共享资源速度（/api/cloud/links|speed）
from panel import octop_page as _octop_page        # Octop 能力桥（/api/octop/* + 页面）

app.include_router(_ai_center.router)             # 指挥中心页面 + 全站 AI 问询（口语→术语→只读读数）
app.include_router(_cloud_page.router)            # 100 个云插件：10×10 排布 + 连接可视化 + 双向绑定
app.include_router(_db_page.router)               # 100 个数据库：10×10 + 真建库 + 双向绑定 + 连接可视化
app.include_router(_cloud_status.router)          # 云插件连接状态表 + 共享资源速度（真实口径）
app.include_router(_octop_page.router)            # Octop 339 项能力 × 100 触手 1:1 双向绑定
from panel import capability_page as _capability_page  # 总能力图表 + 连接状态 + 精准用量 + 固化回滚
app.include_router(_capability_page.router)       # /capability + /api/capability/* + /api/compute/*
from panel import ble_page as _ble_page           # 蓝牙操控（AI 决策 → 授权 → 执行 → 审计）
app.include_router(_ble_page.router)              # /ble + /api/ble/*
from panel import pipelines_page as _pipelines_page  # 流水线分类部署 + 变更日志 + 固化回滚
app.include_router(_pipelines_page.router)        # /pipelines + /api/pipelines/*
from panel import agents_page as _agents_page  # 智能体工程对话面板（名册+真对话+协作工作流图）
app.include_router(_agents_page.router)        # /agents + /api/agents/*
from panel import kits_page as _kits_page         # 三套免费工具 → 云插件部署（剪映/Qwen-Image/ComfyUI 式）
app.include_router(_kits_page.router)             # /kits + /api/kits/*
from panel import workflow_page as _workflow_page  # 工作流独立页：多智能体协作 + 调研前置闸门 + 逐段验收
app.include_router(_workflow_page.router)          # /workflow + /api/workflows/*
from panel import brain_page as _brain_page        # 原生大脑：统一记忆 + 生命起源存档 + 元认知
app.include_router(_brain_page.router)             # /brain + /api/brain/*
from panel import chat_page as _chat_page          # APP 独立多功能对话（会话/多智能体/五模式）
app.include_router(_chat_page.router)              # /chat + /api/chat/*
from panel import terminal_page as _terminal_page  # AI 终端对话面板（白名单命令派发）
app.include_router(_terminal_page.router)          # /terminal + /api/terminal/*
from panel import blueprint_page as _blueprint_page  # 项目 3D 蓝图（上帝视角 · 纯 CSS 3D）
app.include_router(_blueprint_page.router)          # /blueprint + /api/blueprint/*
from panel import voice_page as _voice_page        # 数字人交互式语音操控中心
app.include_router(_voice_page.router)             # /voice + /api/voice/*

# ── 她操作页面（用户同意为前提）：同意闸门 + 指令队列 + 结果回传 + 审计 ──
from fastapi import Body as _CTL_BODY
from core import page_control as _pc                            # noqa: E402


@app.get("/api/control/status")
def api_control_status():
    return _pc.status()


@app.post("/api/control/consent")
async def api_control_consent(payload: dict = _CTL_BODY):
    p = payload or {}
    grant = p.get("grant")
    return _pc.consent(grant=(None if grant is None else bool(grant)),
                       by=str(p.get("by") or "用户"), scope=str(p.get("scope") or "全部页面"))


@app.post("/api/control/submit")
async def api_control_submit(payload: dict = _CTL_BODY):
    p = payload or {}
    return _pc.submit(str(p.get("action") or ""), p.get("args") or {},
                      by=str(p.get("by") or "数字人"))


@app.post("/api/control/execute")
async def api_control_execute(payload: dict = _CTL_BODY):
    """她说一句话 → 翻成页面操作并投递（未授权会明确要求授权）。"""
    return _pc.execute(str((payload or {}).get("text") or ""))


@app.get("/api/control/next")
def api_control_next(limit: int = 5):
    """页面执行器来取指令（未授权时队列是空的，取不到）。"""
    return {"ok": True, "commands": _pc.next_commands(limit=limit),
            "授权": _pc.consent()["授权"]}


@app.post("/api/control/result")
async def api_control_result(payload: dict = _CTL_BODY):
    p = payload or {}
    return _pc.post_result(str(p.get("id") or ""), bool(p.get("ok")),
                           detail=str(p.get("detail") or ""), page=str(p.get("page") or ""))


@app.get("/api/control/audit")
def api_control_audit(limit: int = 50):
    return {"行": _pc.audit(limit=limit)}
from panel import panel_api as _panel_api         # 面板总览/拓扑/能力/流水线/告警/扩容（此前存在但未挂载）
# ★导入必须在 include_router **之前**：workflows_api 是把路由挂到 panel_api 那只 router 上的，
#   而 FastAPI 在 include_router 时就把路由**拷进 app** —— 挂在 include 之后 = 永远到不了 app（真踩过：
#   /api/panel/workflows 一直 404）。
from panel import workflows_api  # noqa: F401
app.include_router(_panel_api.router)             # /api/panel/*（含能力链/DAG 路由）
from panel import dh_console as _dh_console       # 数字人 · 未来世界 AI 语音交互台（新页，老页不动）
app.include_router(_dh_console.router)            # /digital-human/console
from panel import studio_page as _studio_page     # 创作工坊：一条链出片（面板上一个按钮）
app.include_router(_studio_page.router)
from panel import tentacle_mail_page as _tmail_routes     # 触手邮箱页（正文可见）
from panel import tentacle_accounts_page as _tacc_routes  # 触手账户与密钥页（只回指纹）
try:
    from core import dh_boot as _dh_boot
    _dh_boot.boot()          # ★ 数字人一开机就灌记忆 + 带路
except Exception:
    pass

from panel import fleet_live_page as _fleet_live_page   # 编队实时面板（一页看全）
app.include_router(_fleet_live_page.router)
app.include_router(_tmail_routes.router)                  # /tentacle-mail
app.include_router(_tacc_routes.router)                   # /tentacle-accounts
from panel import pulse_page as _pulse_page       # 万能插面板口 + 角色模型钉死表 + 排除登记
app.include_router(_pulse_page.router)
from panel import ops_page as _ops_page           # 专业化操作绑定表 /api/ops（她能查）
app.include_router(_ops_page.router)
from panel import dh_companion as _dh_companion   # 任意页面的伴随件 + 首启密钥闸
app.include_router(_dh_companion.router)          # /api/setup/status · /api/setup/keys
try:
    _dh_companion.apply_env()                     # 启动即装载 state/keys.env（env 已有值不覆盖）
    # 主人设计：配好密钥 → 触手收到启动信号自己配好（专业/装备/账号/云终端）。没配密钥就不空跑。
    from core import tentacle_bootstrap as _tb
    _BOOT = _tb.ensure_started()
except Exception:                                 # noqa: BLE001
    _BOOT = {"ok": False, "reason": "自举未跑"}
try:
    pass
except Exception as e:
    _swallow(__file__, e)

app.include_router(_body_tools_routes.router)     # /api/body/(tools|snapshots) 只读工具面
app.include_router(_digital_human_routes.router)  # /api/digital-human（实时行 + 问询 + SSE）
app.include_router(_fleet_routes.router)          # /api/fleet（触手编队只读：规模/密钥指纹/审计）

@app.get("/octop", response_class=HTMLResponse)
async def octop_bridge_page() -> str:
    """Octop 能力桥页面（独立窗口）。"""
    from panel import octop_page as _op
    return await _op.octop_page()


@app.get("/digital-human", response_class=HTMLResponse)
async def digital_human_page() -> str:
    """数字人对讲页：实时见证行 + 只读工具问询 + 语音流水。"""
    from panel.digital_human_page import DIGITAL_HUMAN_PAGE
    return _dock(DIGITAL_HUMAN_PAGE, "/digital-human")

@app.get("/media", response_class=HTMLResponse)
async def media_monitor_page() -> str:
    """生成队列监控页（可下钻）：深度/等待/失败率/显存/死信/事件流"""
    from panel.media_page import MEDIA_PAGE
    return _dock(MEDIA_PAGE, "/media")

# ── 生成队列监控：队列深度/等待/失败率/显存/死信 → 面板卡 + 告警状态机 ──
from media.queue import JobQueue as _JobQueue, ensure_tables as _ensure_media_tables
from media.scheduler import VramBudget as _VramBudget
from core.compute_router import media_defaults as _media_defaults
from panel.alerts import AlertManager as _AlertManager
from media.monitor import MediaMonitor as _MediaMonitor

_MEDIA_BUDGET = _VramBudget(int(os.environ.get("MEDIA_VRAM_MB", "8192")),
                            cloud_mode=_media_defaults()["cloud_primary"])
_MEDIA_MONITOR = None


@app.on_event("startup")
async def _start_scale_sampler() -> None:
    """后台每 10 分钟给账本库采一次体量样本（这样面板的斜率/ETA 才是真算出来的）。"""
    import asyncio as _a

    async def loop():
        while True:
            try:
                from audit import scale_sqlite as _ss
                led = get_ledger()
                if led is not None:
                    _ss.sample(led)
            except Exception as e:
                _swallow(__file__, e)
            await _a.sleep(600)
    app.state.scale_sampler = _a.create_task(loop())


@app.on_event("startup")
async def _start_brain() -> None:
    """拉起原生大脑：① 出生/冷启动回填 ② 后台编码线程（理解稍后发生，页面不等）。

    纪律：这里只做"能力就绪"，不批量导入历史数据（导入由面板/接口显式发起，避免启动变慢）。
    """
    import asyncio as _a

    def _boot():
        out = {}
        try:
            from core.memory import brain as _B
            out["born"] = bool(_B.born().get("ok"))
            out["life"] = _B.life().get("出生", {}).get("标题") if _B.life().get("出生") else ""
            out["status"] = {k: v for k, v in _B.status().items()
                             if k in ("统一记忆", "主体", "分类")}
        except Exception as exc:                                 # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
        try:
            from core.memory import worker as _W
            _W.start_background(led=get_ledger(), interval=25.0)
            out["encode_worker"] = True
        except Exception as exc:                                 # noqa: BLE001
            out["encode_worker"] = f"{type(exc).__name__}"
        return out

    try:
        app.state.brain_boot = await _a.to_thread(_boot)
        print("[panel] brain boot:", app.state.brain_boot, flush=True)
    except Exception as exc:                                     # noqa: BLE001
        app.state.brain_boot = {"error": f"{type(exc).__name__}: {exc}"}


@app.on_event("startup")
async def _warm_dashboard_caches() -> None:
    """后台预热面板重活（清单规模 / 蓝牙 / 生产闸门 / 闭环状态 / 蓝牙读数）。

    这几件事都是真读数，但要 5~9 秒一次。放在启动后台线程里先算一遍，
    老板打开"总能力"就不用等（真机实测：不预热首次 39 秒）。

    每一小步都计时并**打日志**：以前全是 `except: pass`，预热悄悄失败也看不出来，
    表现就是"启动后第一次打开还是要等十几秒"。
    """
    import asyncio as _a
    import time as _t

    def _steps_total():
        from core import capability_map as _cm
        return _cm._totals()

    def _steps_ble_block():
        from core import capability_map as _cm
        return _cm._ble_block()

    def _steps_ble_report():
        from core import ble_control as _bc
        return _bc.report()

    def _steps_gate():
        from core import production_gate as _pg
        from panel import capability_page as _cp
        return _cp._cached("gate", _pg.summary)

    def _steps_gate_status():
        from panel import capability_page as _cp
        return _cp._cached("gate_status", _cp.pg_status)

    def _steps_loops():
        from panel import capability_page as _cp
        lp = _cp._cached("loops", _cp.loops_status)
        return _cp._cached("loops_graph_svg", lambda: _cp.graph_svg(lp))

    def _steps_blueprint():
        from core import blueprint as _bp
        return _bp.build()

    # ★顺序即优先级：先"总能力页要用到的轻活"，最后才是 3D 蓝图（它一项就要 40s+）
    PLAN = (("totals", _steps_total), ("ble_block", _steps_ble_block),
            ("ble_report", _steps_ble_report), ("gate", _steps_gate),
            ("gate_status", _steps_gate_status), ("loops+graph", _steps_loops),
            ("blueprint", _steps_blueprint))

    async def _run():
        # ★真机教训（主人 2026-10-07 截图）：这些活加起来 **约 100 秒**的重计算，
        #   以前启动后 3 秒就一股脑跑，跟事件循环抢 GIL —— 表现就是"开机首屏与扫描卡在 0%、
        #   点哪都慢"，连 1MB 的帧图都加载不出来。
        #   现在**默认不预热**：页面各自用 TTL 缓存按需算（首次慢一点，但不拖累首屏）。
        #   想要旧行为：设 V9_WARM_DASHBOARD=1。
        import os as _os
        if _os.environ.get("V9_WARM_DASHBOARD", "") not in ("1", "true", "yes"):
            app.state.dash_warm = ["skipped（默认不预热：把资源留给启动首屏；设 V9_WARM_DASHBOARD=1 打开）"]
            print("[panel] dashboard warm: skipped（默认不预热）", flush=True)
            return
        await _a.sleep(45)
        steps = []
        for name, fn in PLAN:
            t0 = _t.time()
            try:
                await _a.to_thread(fn)
                steps.append(f"{name}={_t.time() - t0:.1f}s")
            except Exception as exc:                      # noqa: BLE001
                steps.append(f"{name}=FAIL({type(exc).__name__})")
            await _a.sleep(1.2)                           # 每步之间喘口气，别把首屏堵住
        print("[panel] dashboard warm:", " | ".join(steps), flush=True)
        app.state.dash_warm = steps
    app.state.dash_warmer = _a.create_task(_run())


@app.on_event("startup")
async def _start_body_services() -> None:
    """装配身体服务：app.state.ledger + 语音总线 + 见证实时探测 + 快照采样 + 定期全链复核。

    接线说明（诚实）：
      · 见证探测 loop 在 body.witness_runtime（身份探测 + 内容复核 + 跳变写 outbox + 播报）；
      · 快照采样 loop 在 body.tools.collect（吞噬/覆盖/队列 → body_read_snapshots）；
      · 两者都靠 recheck_leader 门控，多 worker 时只有一个进程在写。

    真机教训（重要）：这个函数的 `@app.on_event("startup")` 曾经**被误删**，
    结果是整块身体服务从来没启动过 —— 面板上"只读工具"永远显示"过期"、
    见证永远 0 个，而日志里连一句报错都没有（因为压根没进这个函数）。
    另外下面每个子服务**各自 try**：一个起不来不许拖垮其余的（快照采集必须活）。
    """
    parts: dict = {}

    def _ok(name: str, extra=None):
        parts[name] = {"ok": True, **(extra or {})}

    def _fail(name: str, exc: BaseException):
        parts[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        from panel.deps import db as _body_db
        app.state.ledger = _body_db
        if not hasattr(app.state, "anchor_multi"):
            app.state.anchor_multi = None
        _ok("ledger", {"db": getattr(_body_db, "path", str(_body_db))})
    except Exception as exc:  # noqa: BLE001
        _fail("ledger", exc)
        app.state.body_services = parts
        print("[panel] body services:", parts)
        return

    # /api/panel 需要账本与能力注册表：不接就等于挂着空壳
    try:
        _panel_api.router.ledger = get_ledger()
        # ★注入**适配后**的注册表（2026-10-08 真机病因）：原先注原始 CapRegistry，
        #   而 DAG 引擎/节点目录/指挥官/panel_api 全按 SkillRegistry 形状读 .skills/.call
        #   ⇒ 53 项能力在面板、拓扑、能力链里全部看不见。适配后两边形状一致，一条链走通。
        from skills.caps.adapter import as_skill_registry
        from skills.caps.registry import build_caps_registry
        _panel_api.router.registry = as_skill_registry(
            build_caps_registry(ledger=get_ledger()))
        _ok("panel_api")
    except Exception as exc:  # noqa: BLE001
        app.state.panel_api_wiring_error = f"{type(exc).__name__}: {exc}"
        _fail("panel_api", exc)

    try:
        _wire_voice_bus()
        _ok("voice_bus")
    except Exception as exc:  # noqa: BLE001
        _fail("voice_bus", exc)

    # ★SQLite 下 recheck_leader 会"非单进程就不写"：面板默认单进程，
    #   这里显式声明单 worker，否则见证探测/快照采样会静默全跳过（看着绿，其实没跑）。
    if os.environ.get("BODY_SINGLE_WORKER") is None:
        os.environ["BODY_SINGLE_WORKER"] = "1"
        print("[panel] BODY_SINGLE_WORKER=1（单进程身体服务）；多 worker 部署请显式设 0")

    if os.environ.get("BODY_RECHECK", "1") != "0":
        try:
            from body import recheck as _recheck
            await _recheck.start(app)
            _ok("recheck")
        except Exception as exc:  # noqa: BLE001
            _fail("recheck", exc)

    if os.environ.get("BODY_WITNESS_PROBE", "1") != "0":
        try:
            from body import witness_runtime as _wr
            await _wr.start(app)
            _ok("witness_probe")
        except Exception as exc:  # noqa: BLE001
            _fail("witness_probe", exc)

    # ★快照采样：**单独 try**，任何别的子服务失败都必须照样起（面板上的读数靠它保鲜）
    try:
        from body.tools import collect as _collect
        _collect_stop = threading.Event()
        app.state.collect_stop = _collect_stop

        async def _collect_loop():
            await _collect.refresh_loop(_body_db, frame_dir=FRAME_DIR,
                                        scan_ledger=get_ledger(), stop=_collect_stop)
        app.state.collect_task = asyncio.create_task(_collect_loop())
        _ok("snapshot_collect")

        # ★官方目录自动补位心跳：state/cf_official_models.json 出现/更新 → 自动把
        #   官方新增模型填进 reserved 槽（每 30 分钟查一次，幂等）。
        async def _align_loop():
            while True:
                await asyncio.sleep(1800)
                try:
                    from core import cloud_plugins as _cp
                    r = _cp.check_and_align()
                    if r.get("align") and r.get("结果", {}).get("新增"):
                        print("[panel] 云插件官方目录自动补位:", r["结果"].get("补位明细"), flush=True)
                except Exception as e:
                    _swallow(__file__, e)
        app.state.align_task = asyncio.create_task(_align_loop())
        _ok("cloud_align")
    except Exception as exc:  # noqa: BLE001
        _fail("snapshot_collect", exc)

    # ★自主层心跳：补齐默认调度（幂等）+ 周期推进到期的活（信息素衰减/长任务心跳/主动汇报扫描/镜像摘要）
    try:
        from core import sched as _sched

        def _boot_sched():
            try:
                return _sched.ensure_defaults()
            except Exception:                              # noqa: BLE001
                return {}

        await asyncio.to_thread(_boot_sched)

        async def _sched_loop():
            await asyncio.sleep(25)                        # 先让启动首屏跑完
            while True:
                try:
                    got = await asyncio.to_thread(_sched.tick, limit=3)
                    if got.get("跑了"):
                        print("[panel] 心跳推进:", [x["任务"] for x in got["跑了"]], flush=True)
                except Exception as e:
                    _swallow(__file__, e)
                await asyncio.sleep(60)

        app.state.sched_task = asyncio.create_task(_sched_loop())
        _ok("sched_heartbeat")
    except Exception as exc:  # noqa: BLE001
        _fail("sched_heartbeat", exc)

    # ★情绪喂料：真实事件（吞噬丢帧 / 扫描覆盖 / 队列积压 / 覆盖率回归）→ 情绪 + 播报 + 告警
    if os.environ.get("VOICE_EMOTION", "1") != "0":
        try:
            await _start_emotion_feeder(app, _body_db)
            _ok("emotion_feeder")
        except Exception as exc:  # noqa: BLE001
            _fail("emotion_feeder", exc)

    app.state.body_services = parts
    bad = [k for k, v in parts.items() if not v.get("ok")]
    print("[panel] body services:", "全部就绪" if not bad else ("未起：" + "、".join(bad)), parts)
    try:
        await _start_scale_sampler()          # 体量采样（面板上斜率/ETA 的真来源）
    except Exception as e:
        _swallow(__file__, e)


async def _start_emotion_feeder(app, body_db) -> None:
    """装配 VoiceDirector + EmotionFeeder（真实事件源），情绪随任务实时变化。

    语音口音：默认"文静台湾腔"（body/prosody.DEFAULT_STYLE，VOICE_STYLE 可换）。
    """
    try:
        from body.emotion import EmotionEngine
        from body.emotion_feeder import EmotionFeeder
        from body.event_feeds import default_feeds
        from body.social import SocialLayer
        from body.voice_director import VoiceDirector, install

        class _VoiceShim:
            """把同步的 VoiceAdapter.enqueue 包成 director 需要的 async enqueue。"""

            def __init__(self, adapter):
                self.adapter = adapter

            async def enqueue(self, text, priority=1, dedupe_key=None):
                if self.adapter is None:
                    return None
                fn = getattr(self.adapter, "enqueue", None)
                if fn is None:
                    return None
                return fn(text, event_id=dedupe_key, priority=priority)

        adapter = None
        if os.environ.get("BODY_VOICE", "1") != "0":
            try:
                from senses.voice import VoiceAdapter
                adapter = VoiceAdapter()
                adapter.start()                     # ★不 start 就是空转队列（入队=无声）
            except Exception as exc:                          # noqa: BLE001
                print("[panel] emotion feeder: tts unavailable:", repr(exc))
        director = VoiceDirector(_VoiceShim(adapter), EmotionEngine(body_db),
                                 SocialLayer(body_db), tts=None, db=body_db)
        install(director)
        app.state.voice_director = director
        feeder = EmotionFeeder(director, db=body_db, feeds=default_feeds(),
                               interval_s=float(os.environ.get("EMOTION_FEED_INTERVAL", "15")))
        # 重启后不重播旧边沿（先载入上次状态）
        try:
            await feeder.load()
        except Exception as e:
            _swallow(__file__, e)
        app.state.emotion_feeder = feeder
        app.state.emotion_task = feeder.start()
        print("[panel] emotion feeder started（真实事件源：吞噬/扫描覆盖/队列/覆盖率回归）")
    except Exception as exc:  # noqa: BLE001
        print("[panel] emotion feeder skipped:", repr(exc))


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
                tts.start()                         # ★同上：常开队列必须真跑
            except Exception as exc:                  # noqa: BLE001
                print("[panel] tts unavailable:", repr(exc))
        app.state.voice_bus = VoiceBus(tts, ledger=app.state.ledger,
                                       on_page_event=publish)
        from body.voice_bus import install_bus
        install_bus(app.state.voice_bus)       # 让 body 里拿不到 bus 的地方也能播报
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
    except Exception as e:
        from core import swallow as _sw; _sw.swallow(__file__, e)



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
    # ★ 旧件退役：开机顺手把已知垃圾（临时脚手架 / 已合成动画的中间帧 / 旧版产物 / 过程截图）
    #   送进回收站。主人要求：设计新件就清旧件，别让重启把旧的当新的。
    import threading

    def _retire() -> None:
        try:
            from core import retire as R
            r = R.sweep(dry_run=False, note="开机自动：旧件退役")
            if r.get("候选"):
                print(f"[panel] retire sweep: {r.get('成功')}/{r.get('候选')} 件进回收站"
                      f"（{r.get('总MB')} MB）")
        except Exception as exc:                                # noqa: BLE001
            print(f"[panel] retire sweep skipped: {type(exc).__name__}")

    threading.Thread(target=_retire, name="v9-retire-sweep", daemon=True).start()

    def _rebuild_meta() -> None:
        # 开机把她的元数据刷新一遍（真读数）→ 页面上显示的版本/校验永远对应当前资产
        try:
            from core import avatar_meta as AM
            m = AM.build()
            ck = m.get("校验") or {}
            print(f"[panel] avatar meta: 版本={m.get('版本')} 动作={len(m.get('动作') or [])} "
                  f"校验={ck.get('通过')}/{ck.get('总项')}")
        except Exception as exc:                                # noqa: BLE001
            print(f"[panel] avatar meta skipped: {type(exc).__name__}")

    threading.Thread(target=_rebuild_meta, name="v9-avatar-meta", daemon=True).start()
    # ★ 权威指针：新面板启动就宣称"我才是当前的"，写 state/panel_current.json（端口/构建号/pid/时间）。
    #   壳只认这个指针，因此**不会再连到跑着旧代码的旧面板**（幽灵 socket 也骗不到它）。
    #   旧指针一并退役（进回收站），并记账 —— 就是主人要的"新件落地顺手清旧件"。
    try:
        import json as _json
        import os as _os
        import time as _time
        from pathlib import Path as _P
        ptr = _P(__file__).resolve().parent.parent / "state" / "panel_current.json"
        old = None
        if ptr.is_file():
            try:
                old = _json.loads(ptr.read_text(encoding="utf-8"))
            except Exception:                                   # noqa: BLE001
                old = None
        port = int(_os.environ.get("PANEL_PORT", "8765"))
        build = max((p.stat().st_mtime for d in ("core", "panel", "senses")
                     for p in (_P(__file__).resolve().parent.parent / d).rglob("*.py")),
                    default=0.0)
        cur = {"port": port, "build": round(build, 3), "pid": _os.getpid(),
               "at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())}
        ptr.parent.mkdir(parents=True, exist_ok=True)
        ptr.write_text(_json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        if old and (old.get("port") != port or old.get("build") != cur["build"]):
            print(f"[panel] 当前面板已换成 port={port} build={cur['build']}"
                  f"（旧的 port={old.get('port')} build={old.get('build')} 被取代）")
            try:
                from core import deploy_ledger as _J
                _J.record("modify", "panel_current",
                          detail={"新": cur, "旧": old}, before=str(old), after=str(cur))
            except Exception as e:
                _swallow(__file__, e)
    except Exception as exc:                                    # noqa: BLE001
        print(f"[panel] 面板指针写入失败：{type(exc).__name__}")
    print(f"[panel] cache reaper started: {[b.name for b in REAPER.budgets]}")


# ══════════ 可直接启动：python -m panel.server（等价 uvicorn panel.server:app）══════════
# 桌面快捷方式/壳探活都依赖这个入口；端口用 PANEL_PORT（默认 8765）。
def _main() -> None:
    import uvicorn
    host = os.environ.get("PANEL_HOST", "127.0.0.1")
    port = int(os.environ.get("PANEL_PORT", "8765"))
    print(f"[panel] GBT小土豆V9 总控台 http://{host}:{port}")
    # ★多 worker：单 worker 时，身体服务/周期性重活会占住事件循环，
    #   表现就是"总控台点哪都慢、首屏转不动"。默认 4 个 worker（可用 V9_PANEL_WORKERS 调）；
    #   写库的周期性任务靠 recheck_leader 门控，多 worker 下只有一个是 leader（框架本来就这么设计）。
    try:
        workers = max(1, int(os.environ.get("V9_PANEL_WORKERS", "4")))
    except Exception:                                          # noqa: BLE001
        workers = 4
    if workers > 1:
        uvicorn.run("panel.server:app", host=host, port=port, workers=workers,
                    log_level=os.environ.get("PANEL_LOG", "info"))
    else:
        uvicorn.run(app, host=host, port=port, log_level=os.environ.get("PANEL_LOG", "info"))


if __name__ == "__main__":
    _main()
