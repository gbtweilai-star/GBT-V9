# main.py —— 总装入口：多触手并发扫描（账本线程安全）· 并发吞噬 · 语音/麦克风 · Codex 工具
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用法示例：
#   python main.py --root /你要扫描的项目路径 --workers 8 --k 1        # 8 触手并发 + 交叉互扫
#   python main.py --root . --workers 4 --fps 30 --frames 60           # 顺带并发吞噬 60 帧
#   python main.py --code "给 parse.py 修掉空输入崩溃并补测试"           # 用 Codex 工具做一次编程任务
#   LEDGER_BACKEND=pg DATABASE_URL=... python main.py --root .          # 切 PG 账本（真并发写）
#
# 退出码：0 全部完成 · 1 运行中异常 · 2 门禁未达标（覆盖率/环境）
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from audit.ledger_factory import backend_info, make_ledger  # noqa: E402
from scan import scan_rules  # noqa: E402
from scan.cross_scan import cross_sweep  # noqa: E402
from scan.scanner import enumerate_targets  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 账本写入：线程安全封装（SQLite 有 WAL+busy+退避；PG 用池 + 同款退避）
# ─────────────────────────────────────────────────────────────
def safe_log(led, scanner: str, target: str, status: str, detail: str = "",
             retries: int = 6) -> bool:
    """并发写账本：失败退避重试；最终失败也不静默（返回 False 由调用方记账）。"""
    for attempt in range(retries):
        try:
            led.log(scanner, target, status, detail)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.05 * (2 ** attempt))
    return False


class Tentacle:
    """一根触手：并发扫描工作单元（互不共享游标，账本写入走 safe_log）。"""

    def __init__(self, tid: str, ledger, brain=None, rpm: int = 0):
        self.id, self.led, self.brain = tid, ledger, brain
        self.rpm = rpm
        self._calls: list[float] = []
        self.scanned = 0
        self.findings = 0
        self.started = time.time()

    def _llm_gate(self) -> bool:
        """每触手独立 rpm 桶：超了就跳过反思，不拖累别人。"""
        if self.rpm <= 0:
            return False
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.rpm:
            return False
        self._calls.append(now)
        return True

    def scan_one(self, target: str) -> dict:
        try:
            data = Path(target).read_bytes()
        except OSError as exc:
            safe_log(self.led, self.id, target, "blocked", f"unreadable: {exc}")
            return {"ok": False, "error": str(exc)}
        hits = []
        if target.lower().endswith(scan_rules.TEXT_SUFFIXES):
            hits = scan_rules.scan_text(target, data)
        self.scanned += 1
        self.findings += len(hits)
        status = "vuln" if hits else "scanned"
        safe_log(self.led, self.id, target, status,
                 json.dumps(hits[:3], ensure_ascii=False) if hits else "clean")
        if hits and self.brain and self._llm_gate():
            try:
                self.brain.report(self.id, target, f"发现 {len(hits)} 处规则命中")
            except Exception:
                pass
        return {"ok": True, "findings": len(hits)}

    def status(self) -> dict:
        return {"id": self.id, "scanned": self.scanned, "findings": self.findings,
                "seconds": round(time.time() - self.started, 2)}


# ─────────────────────────────────────────────────────────────
# 多触手并发扫描：权威全集分片给 N 根触手并发扫 + 任意两根交叉互扫
# ─────────────────────────────────────────────────────────────
def concurrent_scan(root: str, ledger, *, workers: int = 4, k: int = 1,
                    brain=None, rpm: int = 0) -> dict:
    targets = enumerate_targets(root)
    if not targets:
        return {"total": 0, "scanned": 0, "coverage": 1.0, "findings": 0,
                "seconds": 0.0,
                "note": "根目录为空（无目标）", "tentacles": [], "cross": {}}

    tentacles = [Tentacle(f"t{i + 1}", ledger, brain=brain, rpm=rpm)
                 for i in range(max(1, workers))]
    shards = [targets[i::len(tentacles)] for i in range(len(tentacles))]

    # 扫描会话心跳：一触手一 session，让"扫描事件/小时"能严谨计算
    try:
        from senses.scan_sessions import ScanSession, ensure_tables
        ensure_tables(ledger)
        run_id = f"scan-{int(time.time())}"
        sessions = {t.id: ScanSession(ledger, run_id=run_id, tentacle_id=t.id)
                    for t in tentacles}
    except Exception as exc:  # noqa: BLE001
        sessions = {}
        safe_log(ledger, "main", "scan-session", "blocked",
                 f"会话心跳不可用: {type(exc).__name__}: {exc}")

    def _run(t: Tentacle, shard: list):
        sess = sessions.get(t.id)
        ctx_mgr = sess.__enter__() if sess else None
        try:
            for target in shard:
                r = t.scan_one(target)
                if sess and r.get("ok"):
                    sess.done_one()          # 进度心跳（限频落库）
        finally:
            if sess:
                sess.__exit__(None, None, None)

    threads = [threading.Thread(target=_run, args=(t, s), daemon=True, name=t.id)
               for t, s in zip(tentacles, shards)]
    t0 = time.time()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    elapsed = round(time.time() - t0, 2)

    scanned = sum(t.scanned for t in tentacles)
    findings = sum(t.findings for t in tentacles)
    coverage = scanned / len(targets)
    safe_log(ledger, "main", f"concurrent-scan:{root}", "scanned",
             f"workers={workers} targets={len(targets)} cov={coverage:.4f} {elapsed}s")

    cross: dict = {}
    try:
        cross = cross_sweep([{"id": t.id} for t in tentacles], ledger, targets,
                            brain, k=k, workers=min(max(1, workers), 8))
        safe_log(ledger, "main", f"cross-sweep:k={k}", "scanned",
                 json.dumps(cross, ensure_ascii=False)[:200])
    except Exception as exc:  # noqa: BLE001
        safe_log(ledger, "main", f"cross-sweep:k={k}", "blocked",
                 f"{type(exc).__name__}: {exc}")

    return {"total": len(targets), "scanned": scanned, "coverage": round(coverage, 4),
            "findings": findings, "seconds": elapsed,
            "tentacles": [t.status() for t in tentacles],
            "cross": cross}


# ─────────────────────────────────────────────────────────────
# 语音闭环（可选）：说 TTS · 听 ASR/麦克风（失败不阻塞主流程）
# ─────────────────────────────────────────────────────────────
def wire_senses(ledger, brain, args) -> dict:
    state: dict = {}
    try:
        from senses.voice import VoiceAdapter
        voice = VoiceAdapter(ledger=ledger, brain=brain)
        voice.start()
        if brain is not None:
            try:
                brain.voice = voice
            except Exception:
                pass
        state["voice"] = {"started": True,
                          "base": os.environ.get("VOICE_BASE_URL", "http://127.0.0.1:3900/v1")}
        if args.say:
            state["voice"]["enqueue"] = voice.enqueue(
                args.say, event_id=f"cli:{int(time.time())}", priority=1)
    except Exception as exc:  # noqa: BLE001
        state["voice"] = {"started": False, "error": f"{type(exc).__name__}: {exc}"}

    mic = None
    if args.mic or os.environ.get("MIC_ENABLE") == "1":
        try:
            from senses.mic import MicCapture
            from senses.voice import VoiceAdapter
            adapter = VoiceAdapter(ledger=ledger, brain=brain)
            mic = MicCapture("t1-ear", ledger, adapter, brain=brain,
                             device=os.environ.get("MIC_DEVICE") or None)
            state["mic"] = {"started": True, **mic.start()}
        except Exception as exc:  # noqa: BLE001
            state["mic"] = {"started": False, "error": f"{type(exc).__name__}: {exc}"}
    else:
        state["mic"] = {"started": False, "note": "未开启（--mic 或 MIC_ENABLE=1）"}
    return {"state": state, "mic": mic}


# ─────────────────────────────────────────────────────────────
# 编程工具（Codex）：V9 的工具之一，由主脑按需调用
# ─────────────────────────────────────────────────────────────
def run_codex(ledger, task: str, cwd: str, sandbox: str = "workspace-write") -> dict:
    from skills.native_codex import CodexTool
    tool = CodexTool(ledger=ledger, default_cwd=cwd)
    av = tool.probe()
    if not av.ok:
        return {"ok": False, "error": av.reason}
    r = tool.run({"task": task, "mode": "exec", "sandbox": sandbox, "cwd": cwd})
    return {"ok": r.ok, "output": r.output, "error": r.error}


# ─────────────────────────────────────────────────────────────
# 顶尖工程师路径（编程引擎）：Codex 优先，失败降级自研大脑；
# 每轮一行 coder 账本（引擎/成败/改动文件/耗时），面板「Coder」直接读它
# ─────────────────────────────────────────────────────────────
def run_coder(ledger, task: str, root: str, *, test_cmd: str = "",
              prefer: str = "codex", policy: dict | None = None) -> dict:
    from skills.engine import CoderRouter
    brain = None
    try:
        from core.brain import Brain
        brain = Brain()
    except Exception:
        pass
    router = CoderRouter(brain, workspace=root, test_cmd=test_cmd or None, prefer=prefer)
    t0 = time.time()
    r = router.build(task, policy=policy)
    dt = round(time.time() - t0, 2)
    detail = (f"engine={r.engine} ok={r.ok} files={len(r.files)} {dt}s"
              + (f" trace={json.dumps(r.trace, ensure_ascii=False)}" if r.trace else ""))
    safe_log(ledger, "coder", f"coder:{task[:60]}",
             "scanned" if r.ok else "blocked", detail[:500])
    return {"ok": r.ok, "engine": r.engine, "files": r.files,
            "patch": (r.patch or "")[:2000], "seconds": dt, "error": r.error,
            "trace": r.trace}


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="GBT小土豆V9 总装入口")
    ap.add_argument("--root", default=".", help="要扫描的项目根（默认当前目录）")
    ap.add_argument("--workers", type=int, default=4, help="触手数（并发分片数）")
    ap.add_argument("--k", type=int, default=1, help="交叉互扫冗余度（任意两根触手互查）")
    ap.add_argument("--rpm", type=int, default=0, help="每触手每分钟 LLM 反思上限（0=关）")
    ap.add_argument("--fps", type=int, default=int(os.environ.get("DEVOUR_FPS", "30")))
    ap.add_argument("--frames", type=int, default=0, help="并发吞噬帧数（0=不吞噬）")
    ap.add_argument("--code", default="", help='用 Codex 工具做一次编程任务')
    ap.add_argument("--coder", default="", help='用编程引擎做一次任务（默认 Codex，失败降级自研大脑）')
    ap.add_argument("--goal", default="", help='交给执行层（Actuator）的电脑操作目标，如"打开浏览器访问 localhost:8765"')
    ap.add_argument("--commander", default="", help='AI 指挥官：一条人类指令 → 拆解 → 专业化指令 → 派工 → 验收')
    ap.add_argument("--test-cmd", default="", help='配合 --coder：改完自动跑这条命令验活（如 pytest -q）')
    ap.add_argument("--engine", default=os.environ.get("CODER_ENGINE", "codex"),
                    choices=["codex", "brain"], help="编程引擎偏好（也读 CODER_ENGINE）")
    ap.add_argument("--say", default="", help="播报一句话（走 TTS 队列）")
    ap.add_argument("--mic", action="store_true", help="开启实时麦克风采集（也可 MIC_ENABLE=1）")
    ap.add_argument("--no-gate", action="store_true", help="跳过环境门禁（不推荐）")
    args = ap.parse_args()

    info = backend_info()
    print(f"[总装] 账本后端 = {info['backend']} ({info.get('target', '?')})")
    try:
        ledger = make_ledger()
    except Exception as exc:  # noqa: BLE001
        print(f"[总装] 账本不可用：{type(exc).__name__}: {exc}")
        return 1

    summary: dict = {"backend": info, "root": str(Path(args.root).resolve())}

    devour_thread = None
    devour = None
    if args.frames > 0:
        try:
            from senses.devour import Devour
            devour = Devour(frame_dir=os.environ.get("DEVOUR_DIR", "devoured/t1-eye"),
                            fps=args.fps, ledger=ledger,
                            source=os.environ.get("DEVOUR_SOURCE", "screen"))
            devour_thread = threading.Thread(
                target=lambda: devour.run(max_frames=args.frames),
                daemon=True, name="devour")
            devour_thread.start()
            print(f"[吞噬] 并发采集中（{args.frames} 帧 @ {args.fps}fps）")
        except Exception as exc:  # noqa: BLE001
            summary["devour_error"] = f"{type(exc).__name__}: {exc}"

    brain = None
    try:
        from core.brain import Brain
        brain = Brain()
    except Exception as exc:  # noqa: BLE001
        summary["brain"] = {"available": False, "note": f"{type(exc).__name__}: {exc}"}

    senses = wire_senses(ledger, brain, args)
    summary["senses"] = senses["state"]

    result = concurrent_scan(args.root, ledger, workers=args.workers, k=args.k,
                             brain=brain, rpm=args.rpm)
    summary["scan"] = result
    print(f"[扫描] 目标 {result['total']} · 已扫 {result['scanned']} · 覆盖率 {result['coverage']}"
          f" · 命中 {result['findings']} · {result['seconds']}s · {args.workers} 触手")
    for t in result["tentacles"]:
        print(f"   - {t['id']}: {t['scanned']} 目标 / {t['findings']} 命中 / {t['seconds']}s")

    if args.code:
        print(f"[Codex] 任务：{args.code}")
        cx = run_codex(ledger, args.code, cwd=str(Path(args.root).resolve()))
        summary["codex"] = cx
        print(f"[Codex] {'完成' if cx.get('ok') else '失败'} · "
              f"{json.dumps((cx.get('output') or {}), ensure_ascii=False)[:200]}")

    if args.coder:
        print(f"[工程师] 任务：{args.coder}（引擎偏好 {args.engine}"
              f"{'，验活 ' + args.test_cmd if args.test_cmd else ''}）")
        cr = run_coder(ledger, args.coder, root=str(Path(args.root).resolve()),
                       test_cmd=args.test_cmd, prefer=args.engine)
        summary["coder"] = cr
        print(f"[工程师] {'完成' if cr['ok'] else '失败'} · 引擎={cr['engine']}"
              f" · 改动 {len(cr['files'])} 个文件 · {cr['seconds']}s"
              + (f" · {cr['error']}" if cr.get("error") else ""))

    if args.goal:
        try:
            from core.actuator import Actuator
            act = Actuator(ledger, brain, devour=devour,
                           auto_confirm=os.environ.get("AUTO_CONFIRM") == "1")
            print(f"[执行层] 目标：{args.goal}")
            gr = act.run_task(args.goal)
            summary["actuator"] = gr
            print(f"[执行层] ok={gr.get('ok')} steps={gr.get('steps')}"
                  + ("（需人工确认，已暂停）" if gr.get("needs_confirm") else ""))
        except Exception as exc:  # noqa: BLE001
            summary["actuator"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print(f"[执行层] 不可用：{summary['actuator']['error']}")

    if args.commander:
        try:
            from core.commander import Commander, IDENTITY
            from skills.integrate import build_registry
            if brain is not None:
                brain.identity = IDENTITY          # 主脑身份定位：AI 指挥官
            reg = build_registry(ledger=ledger, brain=brain)
            cmd = Commander(brain, registry=reg, ledger=ledger)
            print(f"[指挥官] 指令：{args.commander}")
            cr = cmd.run_chain(args.commander)
            summary["commander"] = {"ok": cr.get("ok"), "stage": cr.get("stage"),
                                    "nodes": cr.get("nodes"),
                                    "trace_id": cr.get("trace_id"),
                                    "errors": cr.get("errors", [])[:3]}
            print(f"[指挥官] {'完成' if cr.get('ok') else '未达标'} · "
                  f"节点 {cr.get('nodes')} · trace {cr.get('trace_id')}")
            for r_ in (cr.get("results") or [])[:6]:
                print("   -", r_.get("node"), r_.get("skill"),
                      "✓" if r_.get("ok") else "✗ " + str(r_.get("error", ""))[:80])
        except Exception as exc:  # noqa: BLE001
            summary["commander"] = {"ok": False,
                                    "error": f"{type(exc).__name__}: {exc}"}
            print(f"[指挥官] 不可用：{summary['commander']['error']}")

    if devour_thread is not None:
        devour_thread.join(timeout=60)
        try:
            summary["devour"] = devour.status() if devour else None
            print(f"[吞噬] 完成：{summary['devour']}")
        except Exception:
            pass
    if senses["mic"] is not None:
        try:
            senses["mic"].stop()
            summary["mic_final"] = senses["mic"].status()
        except Exception:
            pass
    try:
        summary["ledger_counts"] = ledger.counts()
    except Exception:
        pass

    brief = {k: summary[k] for k in summary if k != "scan"}
    print(f"\n[总装] 完成：{json.dumps(brief, ensure_ascii=False, default=str)[:400]}")
    ok = result["coverage"] >= 1.0 and not summary.get("devour_error")
    if not ok and not args.no_gate:
        print("[总装] 门禁未达标（覆盖率 < 1.0 或吞噬异常）")
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(1)
