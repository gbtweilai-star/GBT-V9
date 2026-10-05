# body/boot.py —— 登记链校验 + 重启必读自检
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 全程只读单个一致性快照; 流式分批, 绝不把全表捞进内存;
#       发现损坏必须导出原始行证据, 不许"跳过继续"; 任何不确定都算 unhealthy
from __future__ import annotations
import json, os
from dataclasses import dataclass, asdict
from body.hash import GENESIS, event_hash, manifest_hash, body_token, targets_hash


def utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

BATCH       = 2000
CHECKPOINT  = os.environ.get("BODY_CHECKPOINT_PATH", "var/body_chain_checkpoint.json")

# ← 对齐点 A: 占位符与时间列按 dialect 适配层换 (? / %s, TEXT / TIMESTAMPTZ)
SQL_LAST_REG  = ("SELECT seq, event_hash FROM registration ORDER BY seq DESC LIMIT 1")
SQL_REG_RANGE = ("SELECT seq, trace_id, event_type, actor_tentacle, created_at, "
                 "payload_json, targets_hash, prev_hash, event_hash "
                 "FROM registration WHERE seq >= ? ORDER BY seq ASC LIMIT ?")
SQL_REG_ONE   = "SELECT * FROM registration WHERE seq = ?"
SQL_TARGETS   = ("SELECT seq, path, before_hash, after_hash FROM registration_targets "
                 "WHERE seq >= ? AND seq <= ? ORDER BY seq, path")
SQL_MANIFEST  = "SELECT * FROM brain_boot_manifest WHERE root_id = ?"
SQL_INDEX_STAT= ("SELECT COUNT(*) AS indexed, COALESCE(MAX(generation),0) AS generation, "
                 "COALESCE(SUM(CASE WHEN state='dirty'   THEN 1 ELSE 0 END),0) AS dirty, "
                 "COALESCE(SUM(CASE WHEN state='missing' THEN 1 ELSE 0 END),0) AS missing "
                 "FROM body_files WHERE root_id = ?")
SQL_LAST_SCAN = ("SELECT payload_json FROM registration WHERE event_type='scan' "
                 "ORDER BY seq DESC LIMIT 1")


async def _all(snap, sql, args=()):
    return await snap.fetch_all(sql, args)

async def _one(snap, sql, args=()):
    rows = await snap.fetch_all(sql, args)
    return rows[0] if rows else None


# ═══════════ 0. 增量验链可信点（存库，不落文件：无路径逃逸面，重启可读）═══════════
async def save_checkpoint(db, cp, *, root_id="main") -> None:
    """保存增量验链可信点（只有全链通过才允许调用）。"""
    await db.execute(
        "INSERT INTO body_chain_checkpoint (root_id, seq, hash, at) "
        "VALUES (?,?,?,?) ON CONFLICT (root_id) DO UPDATE SET "
        "seq=EXCLUDED.seq, hash=EXCLUDED.hash, at=EXCLUDED.at",
        (root_id, int(cp["seq"]), str(cp["hash"]), utc_now_iso()))


async def load_checkpoint(db, *, root_id="main"):
    rows = await db.fetch_all(
        "SELECT seq, hash FROM body_chain_checkpoint WHERE root_id=?", (root_id,))
    if not rows or not rows[0]["hash"]:
        return None
    return {"seq": int(rows[0]["seq"]), "hash": rows[0]["hash"]}


async def drop_checkpoint(db, *, root_id="main") -> None:
    """作废可信点 → 强制下次全量重验（链断时调用）。"""
    await db.execute("DELETE FROM body_chain_checkpoint WHERE root_id=?", (root_id,))


async def boot_check(snap, *, root_id="main", checkpoint=None, anchor_multi=None,
                     export_dir=None, epoch=None, fail_mode=None,
                     record=True, write_db=None, use_checkpoint=False) -> dict:
    """启动自检：全链复核 + 锚点交叉复核 → 写 body_boot_checks（重启不失忆）。

    ★ 启动自检**默认全链**（use_checkpoint=False）：增量验链有已知盲区
      （校验点之前的篡改它发现不了），启动这一刻必须从头验，否则等于自我安慰。
      只有显式的快速路径（use_checkpoint=True）才读库里的可信点。
    只读用 snap；落痕用 write_db（缺省与 snap 同一对象；读快照场景传入可写库）。
    fail_mode（BODY_CHAIN_FAIL_MODE，默认 warn）：warn 只告警 / freeze 置链封冻 /
    halt 停机 —— 本函数只返回 should_freeze / should_halt，动作由调用方执行。
    """
    from body.anchor import verify_anchors            # 延迟导入避免环
    import uuid as _uuid

    fm = (fail_mode or os.environ.get("BODY_CHAIN_FAIL_MODE", "warn")).lower()
    cp = checkpoint
    if cp is None and use_checkpoint:
        db_cp = write_db if write_db is not None else snap
        try:
            cp = await load_checkpoint(db_cp, root_id=root_id)
        except Exception:
            cp = None
    chain = await verify_chain(snap, root_id=root_id, checkpoint=cp,
                               export_dir=export_dir)
    try:
        anchors = await verify_anchors(snap, anchor_multi, root_id=root_id,
                                       epoch=epoch, export_dir=export_dir)
    except Exception as e:                # 锚点层不可用 ≠ 链断；如实记录，不判篡改
        anchors = {"ok": False, "checked": 0,
                   "problems": [{"kind": "anchor_layer_error",
                                 "detail": f"{type(e).__name__}: {e}"}],
                   "external": {"status": "error"}}

    ok = bool(chain.ok and anchors.get("ok"))
    reason = None
    if not chain.ok:
        reason = f"chain:{chain.reason}"
    elif not anchors.get("ok"):
        hard = [p["kind"] for p in anchors.get("problems", [])
                if p["kind"] != "external_unreachable"]
        reason = "anchors:" + (",".join(hard) if hard else "unreachable")

    idx = (await _all(snap,
                      "SELECT COUNT(*) AS indexed, COALESCE(MAX(generation),0) "
                      "AS generation FROM body_files WHERE root_id = ?",
                      (root_id,)) or [{"indexed": 0, "generation": 0}])[0]
    prev_self = await _one(snap,
                           "SELECT * FROM body_boot_checks WHERE root_id=? "
                           "ORDER BY at DESC LIMIT 1", (root_id,))
    out = {
        "ok": ok, "reason": reason, "fail_mode": fm, "root_id": root_id,
        "head_seq": chain.head_seq, "head_hash": chain.head_hash,
        "checked": chain.checked,
        "chain": {"ok": chain.ok, "reason": chain.reason, "at_seq": chain.at_seq,
                  "checked": chain.checked, "head_seq": chain.head_seq},
        "anchors": {"ok": anchors.get("ok"), "checked": anchors.get("checked"),
                    "problems": anchors.get("problems", []),
                    "external": anchors.get("external"),
                    "undetectable_window": anchors.get("undetectable_window")},
        "index": {"indexed": int(idx["indexed"] or 0),
                  "generation": int(idx["generation"] or 0)},
        "prev_check_at": prev_self["at"] if prev_self else None,
        "should_freeze": (not ok) and fm == "freeze",
        "should_halt": (not ok) and fm == "halt",
    }
    if record:
        dbw = write_db if write_db is not None else snap
        await dbw.execute(
            "INSERT INTO body_boot_checks (check_id, at, root_id, ok, head_seq, "
            "head_hash, chain_ok, anchors_ok, checked, anchors_checked, reason, "
            "fail_mode, detail_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_uuid.uuid4().hex[:16], utc_now_iso(), root_id,
             1 if ok else 0, chain.head_seq, chain.head_hash,
             1 if chain.ok else 0, 1 if anchors.get("ok") else 0,
             chain.checked, int(anchors.get("checked") or 0), reason, fm,
             json.dumps(out, ensure_ascii=False, default=str)[:4000]))
    return out


# ═══════════ 1. verify_chain ═══════════
@dataclass
class ChainResult:
    ok: bool
    checked: int          # 本次实验条数
    head_seq: int
    head_hash: str
    at_seq: int | None = None
    reason: str | None = None
    evidence: dict | None = None

    def as_dict(self): return asdict(self)


def _check_row(r, expected_seq, prev_hash, th_recomputed) -> dict | None:
    if r["seq"] != expected_seq:
        return {"reason": "seq_jump", "expected": expected_seq, "found": r["seq"]}
    if r["prev_hash"] != prev_hash:
        return {"reason": "prev_hash_mismatch",
                "expected": prev_hash, "found": r["prev_hash"]}
    if th_recomputed is None:
        return {"reason": "targets_missing", "note": "登记行没有任何责任页"}
    if th_recomputed != r["targets_hash"]:
        return {"reason": "targets_hash_mismatch",
                "recorded": r["targets_hash"], "recomputed": th_recomputed}
    rec = event_hash(r["seq"], r["event_type"], r["actor_tentacle"],
                     r["created_at"], r["payload_json"], r["targets_hash"], r["prev_hash"])
    if rec != r["event_hash"]:
        return {"reason": "event_hash_mismatch",
                "recorded": r["event_hash"], "recomputed": rec}
    return None


async def _targets_hashes(snap, seqs) -> dict:
    """按 seq 批量重算 targets_hash（每个 seq 的 targets 行一起取，避免 N+1）。"""
    if not seqs:
        return {}
    wanted = set(seqs)
    rows = await _all(snap, SQL_TARGETS, (min(seqs), max(seqs)))
    bucket: dict[int, list] = {}
    for r in rows:
        if r["seq"] in wanted:
            bucket.setdefault(r["seq"], []).append(r)
    return {seq: targets_hash(rs) for seq, rs in bucket.items()}


async def _evidence(snap, r, problem, export_dir) -> dict:
    """损坏现场：原始登记行 + 责任页 + 判定，落盘成可审计 artifact。"""
    ev = {"problem": problem,
          "registration": {k: r[k] for k in r.keys()},
          "targets": list(await _all(snap, SQL_TARGETS, (r["seq"], r["seq"])))}
    if export_dir:
        os.makedirs(export_dir, exist_ok=True)
        p = os.path.join(export_dir, f"chain_break_seq{r['seq']}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False, sort_keys=True, indent=2)
        ev["artifact"] = p
    return ev


async def verify_chain(snap, *, root_id="main", checkpoint=None,
                       until_seq=None, export_dir=None, batch=BATCH,
                       on_progress=None) -> ChainResult:
    """流式验链。

    checkpoint={"seq":int,"hash":str} 是上一次验证通过的可信点。
    ⚠ 增量只能往后验 —— 校验点之前的篡改它发现不了（需定期全链复核或外部 head 锚点）。
    """
    start_seq, start_hash = 1, GENESIS
    if checkpoint:
        c = await _one(snap, SQL_REG_ONE, (checkpoint["seq"],))
        if c is None or c["event_hash"] != checkpoint["hash"]:
            return ChainResult(False, 0, 0, "", checkpoint["seq"], "checkpoint_mismatch",
                               {"checkpoint": checkpoint,
                                "found_hash": c["event_hash"] if c else None})
        start_seq, start_hash = checkpoint["seq"] + 1, checkpoint["hash"]

    expected_seq, prev_hash, checked = start_seq, start_hash, 0
    tail = await _one(snap, SQL_LAST_REG)

    while True:
        rows = await _all(snap, SQL_REG_RANGE, (expected_seq, batch))
        if not rows:
            break
        th = await _targets_hashes(snap, [r["seq"] for r in rows])
        for r in rows:
            problem = _check_row(r, expected_seq, prev_hash, th.get(r["seq"]))
            if problem:
                return ChainResult(False, checked, expected_seq - 1, prev_hash,
                                   r["seq"], problem["reason"],
                                   await _evidence(snap, r, problem, export_dir))
            checked += 1
            prev_hash = r["event_hash"]
            expected_seq = r["seq"] + 1
            if on_progress and checked % 500 == 0:      # 分批让出：长链不霸占事件循环
                await on_progress(checked, r["seq"], prev_hash)
        if len(rows) < batch:
            break

    # 链尾一致：登记表最后一条必须正是我们验到的最后一条（删尾/多尾都能查出来）
    if tail and expected_seq - 1 != tail["seq"]:
        return ChainResult(False, checked, expected_seq - 1, prev_hash,
                           expected_seq, "tail_mismatch",
                           {"table_last_seq": tail["seq"],
                            "verified_last_seq": expected_seq - 1})
    return ChainResult(True, checked, expected_seq - 1, prev_hash)
