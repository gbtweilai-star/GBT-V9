# body/recheck.py —— 定期全链复核
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 复核结果写 chain_audit_runs, 绝不写回 registration(链头必须不动);
#       低优先级只读快照 + 分批让出事件循环; 断点可续; 失败作废 checkpoint
from __future__ import annotations
from core.swallow import swallow as _swallow
import asyncio, json, logging, os, random, signal, uuid
from contextlib import asynccontextmanager

from body.boot import CHECKPOINT, drop_checkpoint, save_checkpoint, verify_chain
from body.anchor import verify_anchors
from body.registry import register

log = logging.getLogger("body.recheck")


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

RECHECK_TASKS: set = set()

SQL_RUNNING = ("SELECT * FROM chain_audit_runs WHERE root_id=? AND mode=? "
               "AND status='running' ORDER BY started_at DESC LIMIT 1")
SQL_HEAD = "SELECT seq, event_hash FROM registration ORDER BY seq DESC LIMIT 1"
SQL_ROW  = "SELECT event_hash FROM registration WHERE seq=?"


# ── leader：PG 会话级 advisory lock / SQLite 单 worker ──
@asynccontextmanager
async def recheck_leader(ledger, *, key="body:recheck"):
    if ledger.dialect == "sqlite":
        if os.getenv("BODY_SINGLE_WORKER", "0") != "1":
            yield None                      # 多 worker SQLite：明确跳过，不假装
            return
        yield f"sqlite:{os.getpid()}"
        return
    conn = await ledger.acquire_conn()      # ★锁随会话；连接必须一直持有
    try:
        got = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", key)
        yield key if got else None
    finally:
        if got:
            await conn.fetchval("SELECT pg_advisory_unlock(hashtext($1))", key)
        await ledger.release_conn(conn)


async def _audit_upsert(ledger, run_id, **f):
    # NOT NULL 兜底：错误路径常常只带 status/reason，缺 root_id/mode 会反过来把真错吞掉
    f.setdefault("root_id", "main")
    f.setdefault("mode", "full")
    cols = ",".join(f)
    ph = ",".join("?" * len(f))
    await ledger.execute(
        f"""INSERT INTO chain_audit_runs (run_id,{cols}) VALUES (?,{ph})
            ON CONFLICT (run_id) DO UPDATE SET {','.join(f'{k}=EXCLUDED.{k}' for k in f)}""",
        (run_id, *f.values()))


async def recheck_once(ledger, *, root_id="main", mode="full", export_dir=None,
                       batch=2000, anchor_multi=None) -> dict:
    """一次全链复核。断点续跑；失败作废 checkpoint 并升级告警。"""
    fail_mode = os.getenv("BODY_CHAIN_FAIL_MODE", "warn")     # warn | freeze | halt
    prev = await ledger.fetch_one(SQL_RUNNING, (root_id, mode))
    run_id = prev["run_id"] if prev else uuid.uuid4().hex
    from_seq = (prev["verified_upto_seq"] + 1) if prev else 1
    my_task = asyncio.current_task(); RECHECK_TASKS.add(my_task)

    try:
        async with ledger.read_snapshot() as snap:            # ★低优先级只读快照
            head = await snap.fetch_all(SQL_HEAD)
            head_at_start = head[0]["event_hash"] if head else None
            cp = None
            if from_seq > 1:                                  # 续跑：接回上轮已验点
                r = await snap.fetch_all(SQL_ROW, (from_seq - 1,))
                if not r:
                    from_seq, cp = 1, None                    # 断点已失效 → 全量重来
                else:
                    cp = {"seq": from_seq - 1, "hash": r[0]["event_hash"]}

            await _audit_upsert(ledger, run_id, root_id=root_id, mode=mode,
                                status="running", started_at=_iso_now(),
                                from_seq=from_seq, verified_upto_seq=from_seq - 1,
                                head_at_start=head_at_start, checked=0,
                                worker_id=os.getenv("WORKER_ID", "worker"))

            async def progress(checked, upto_seq, _h):
                await _audit_upsert(ledger, run_id, checked=checked,
                                    verified_upto_seq=upto_seq)
                await asyncio.sleep(0)                        # ★让出事件循环

            res = await verify_chain(snap, root_id=root_id, checkpoint=cp,
                                     export_dir=export_dir, batch=batch,
                                     on_progress=progress)

        if not res.ok:                                        # ★断裂处置
            await _invalidate_checkpoint(ledger)              # 作废增量点 → 强制全量
            await _audit_upsert(ledger, run_id, status="broken", finished_at=_iso_now(),
                                verified_upto_seq=res.at_seq or 0, checked=res.checked,
                                reason=res.reason, broken_at=res.at_seq,
                                evidence_path=(res.evidence or {}).get("artifact"))
            await _escalate(ledger, res, fail_mode)
            # 这是真事件 → 唯一允许写 registration 的路径
            await register(ledger, event_type="harden", actor="recheck",
                           payload={"action": "chain_break", "reason": res.reason,
                                    "at_seq": res.at_seq, "mode": fail_mode},
                           targets=[{"path": "__body__/registration",
                                     "after": res.head_hash}])
            return {"status": "broken", "run_id": run_id, "reason": res.reason,
                    "at_seq": res.at_seq}

        await _audit_upsert(ledger, run_id, status="clean", finished_at=_iso_now(),
                            verified_upto_seq=res.head_seq, checked=res.checked)
        await save_checkpoint(ledger, {"seq": res.head_seq,
                                       "hash": res.head_hash})   # 全量通过才更新

        anchor_res = (await verify_anchors(ledger, anchor_multi, root_id=root_id,
                                           export_dir=export_dir)
                      if anchor_multi else {"ok": True, "checked": 0})
        if not anchor_res["ok"]:
            await _escalate_anchor(ledger, anchor_res, fail_mode)
            return {"status": "anchor_broken", "run_id": run_id,
                    "problems": anchor_res["problems"]}
        return {"status": "clean", "run_id": run_id, "checked": res.checked,
                "head_seq": res.head_seq, "anchors_checked": anchor_res["checked"],
                "undetectable_window": anchor_res.get("undetectable_window")}
    except asyncio.CancelledError:
        await _audit_upsert(ledger, run_id, status="error", reason="cancelled")
        raise
    except Exception as e:
        await _audit_upsert(ledger, run_id, status="error",
                            reason=f"{type(e).__name__}: {e}")
        raise
    finally:
        RECHECK_TASKS.discard(my_task)


async def _invalidate_checkpoint(ledger):
    """作废增量可信点（存库版本）：下次强制全量重验。"""
    try:
        await drop_checkpoint(ledger)
    except Exception as e:
        _swallow(__file__, e)


async def _escalate(ledger, res, fail_mode):
    await ledger.record_alert("chain_break", {"at_seq": res.at_seq, "reason": res.reason,
                                              "evidence": res.evidence}, level="critical")
    if fail_mode == "freeze":
        ledger.chain_frozen = True          # ★封冻写路径：register() 直接抛错
        await _announce("登记链断裂，已封冻写入，等待大脑指令")
    elif fail_mode == "halt":
        os.kill(os.getpid(), signal.SIGTERM)
    else:
        await _announce("登记链断裂，已告警，写入仍在继续，请尽快处置")


async def _announce(msg: str) -> None:
    """播报（有语音就播，没有就只记日志——绝不假装播过）。"""
    log.warning("body: %s", msg)
    try:
        from senses.voice import VoiceAdapter
        _v = VoiceAdapter()
        _v.enqueue(msg, event_id="recheck:" + str(int(asyncio.get_event_loop().time())),
                   priority=0)
    except Exception as e:
        _swallow(__file__, e)


async def _escalate_anchor(ledger, res, fail_mode):
    """锚点异常：与链断同级处置（critical 不去抖）。"""
    await ledger.record_alert("anchor_integrity", {
        "problems": res.get("problems", []),
        "checked": res.get("checked"),
        "undetectable_window": res.get("undetectable_window")}, level="critical")
    if fail_mode == "freeze":
        ledger.chain_frozen = True
        await _announce("外部锚点不一致，已封冻写入，等待大脑指令")
    elif fail_mode == "halt":
        os.kill(os.getpid(), signal.SIGTERM)
    else:
        await _announce("外部锚点不一致，已告警，写入仍在继续")


async def _needs_boot_recheck(app, *, max_age_s=None) -> bool:
    """距上次干净复核超过阈值 → 启动时立刻补跑一次。"""
    max_age = int(max_age_s or os.getenv("BODY_RECHECK_MAX_AGE", 6 * 3600))
    try:
        rows = await app.state.ledger.fetch_all(
            "SELECT finished_at FROM chain_audit_runs WHERE status='clean' "
            "ORDER BY finished_at DESC LIMIT 1")
    except Exception:
        return False
    if not rows or not rows[0]["finished_at"]:
        return True
    from common.timeutil import to_epoch
    import time as _t
    try:
        return (_t.time() - to_epoch(rows[0]["finished_at"])) > max_age
    except Exception:
        return False


async def recheck_loop(app, *, period=None, jitter=0.1):
    period = int(period or os.getenv("BODY_RECHECK_PERIOD", 6 * 3600))
    while True:
        await asyncio.sleep(period * (1 + random.uniform(-jitter, jitter)))   # 抖动防齐步
        try:
            async with recheck_leader(app.state.ledger) as who:
                if who is None:
                    continue
                await recheck_once(app.state.ledger, export_dir=app.state.evidence_dir,
                                   anchor_multi=app.state.anchor_multi)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("recheck loop error")     # 绝不拖垮 app


# ═══════════ 装配入口（不再在模块级挂 @app.on_event；由面板启动时显式调用）═══════════
# 装配点（对齐点）：app.state.ledger / app.state.anchor_multi / app.state.anchor_writer
# 面板接线见 panel/server.py 的 _start_body_services（未装配时本模块只提供能力，不自启）。
async def start(app) -> None:
    """启动定期全链复核：距上次干净复核超阈值先补跑一次，然后按 period 循环。"""
    if await _needs_boot_recheck(app):
        asyncio.create_task(recheck_once(app.state.ledger,
                                         anchor_multi=app.state.anchor_multi))
    app.state.recheck_task = asyncio.create_task(recheck_loop(app))
    log.info("body recheck loop started")


async def stop(app) -> None:
    """停机：取消循环、收干净所有在跑任务，最后强制锚一次。"""
    t = getattr(app.state, "recheck_task", None)
    if t is not None:
        t.cancel()
        await asyncio.gather(t, *RECHECK_TASKS, return_exceptions=True)
    writer = getattr(app.state, "anchor_writer", None)
    if writer is not None:
        try:
            await writer.maybe(force=True)
        except Exception:
            log.exception("anchor flush on shutdown failed")
