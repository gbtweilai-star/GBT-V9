# body/registry.py —— 登记链写入路径：append-only，链头只在这里前进
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 登记链是 append-only：只插新行，绝不 UPDATE/DELETE 历史行（复核只写 chain_audit_runs）
#   - seq / prev_hash / targets_hash / event_hash 在**同一事务内**从链头推出，防竞态
#   - 每次全量扫描 / 改动 / 修复加固都必须登记（否则重启即失忆）
#   - 封冻（chain_frozen）时 register() 直接抛错，写入路径拒绝继续
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from body.hash import (GENESIS, body_token, event_hash, manifest_hash,
                       payload_text, targets_hash)

REQUIRED_TARGET_FIELDS = ("path",)

SQL_HEAD = ("SELECT seq, event_hash FROM registration WHERE root_id=? "
            "ORDER BY seq DESC LIMIT 1")
SQL_IDX = ("SELECT COUNT(*) AS indexed, COALESCE(MAX(generation),0) AS generation "
           "FROM body_files WHERE root_id=?")
SQL_TARGETS_ONE = ("SELECT path, before_hash, after_hash FROM registration_targets "
                   "WHERE seq=? ORDER BY path")
SQL_MANIFEST_ONE = "SELECT * FROM brain_boot_manifest WHERE root_id=?"


class ChainFrozen(RuntimeError):
    """链已封冻：发现断裂后拒绝继续写入，等人工/大脑裁决。"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_targets(targets) -> list[dict]:
    """责任页规范化：path 必填；before/after 允许为空（新增页只有 after）。"""
    out = []
    for t in targets or []:
        if not isinstance(t, dict) or not t.get("path"):
            raise ValueError(f"非法责任页（缺 path）: {t!r}")
        out.append({"path": str(t["path"]),
                    "before_hash": t.get("before_hash") or t.get("before"),
                    "after_hash": t.get("after_hash") or t.get("after")})
    return sorted(out, key=lambda r: r["path"])


async def head(db, root_id: str = "main") -> dict:
    rows = await db.fetch_all(SQL_HEAD, (root_id,))
    if not rows:
        return {"seq": 0, "event_hash": GENESIS}
    return {"seq": int(rows[0]["seq"]), "event_hash": rows[0]["event_hash"]}


async def register(db, *, event_type: str, actor: str | None = None,
                   payload=None, targets=None, root_id: str = "main",
                   trace_id: str | None = None,
                   created_at: str | None = None) -> dict:
    """追加一条登记。返回 {seq, event_hash, prev_hash, targets_hash, manifest}。

    这是**唯一**允许推进链头的函数；任何调用方都不得直接 INSERT registration。
    """
    if getattr(db, "chain_frozen", False):
        raise ChainFrozen("登记链已封冻（chain_frozen=True），拒绝写入")
    if not event_type:
        raise ValueError("event_type 必填")
    rows = _norm_targets(targets)
    payload_json = payload_text(payload if payload is not None else {})
    th = targets_hash(rows)
    at = created_at or _iso_now()
    seq_hint = getattr(db, "_seq_hint", None)

    async with db.transaction(immediate=True):
        cur = await head(db, root_id)
        seq = cur["seq"] + 1
        if seq_hint is not None and seq != seq_hint:      # 并发下链头被人推进 → 显式失败
            raise RuntimeError(
                f"链头竞态：期望 seq={seq_hint}，实际 {seq}（重试即可）")
        prev = cur["event_hash"]
        eh = event_hash(seq, event_type, actor or "", at, payload_json, th, prev)
        await db.execute(
            """INSERT INTO registration
               (seq, root_id, trace_id, event_type, actor_tentacle, created_at,
                payload_json, targets_hash, prev_hash, event_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (seq, root_id, trace_id, event_type, actor or "", at,
             payload_json, th, prev, eh))
        for t in rows:
            await db.execute(
                """INSERT INTO registration_targets (seq, path, before_hash, after_hash)
                   VALUES (?,?,?,?)""",
                (seq, t["path"], t["before_hash"], t["after_hash"]))
            # 穿透式索引：责任页同步进 body_files（有 after 才算已落盘）
            if t["after_hash"]:
                await db.execute(
                    """INSERT INTO body_files (root_id, path, state, sha256, indexed_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT (root_id, path) DO UPDATE SET
                         state=EXCLUDED.state, sha256=EXCLUDED.sha256,
                         indexed_at=EXCLUDED.indexed_at""",
                    (root_id, t["path"], "clean", t["after_hash"], at))
    man = await refresh_manifest(db, root_id=root_id)
    return {"seq": seq, "event_hash": eh, "prev_hash": prev, "targets_hash": th,
            "manifest": man}


async def refresh_manifest(db, *, root_id: str = "main",
                           summary=None, unresolved=None) -> dict:
    """身体指纹清单：链头 + 索引规模 + 索引代。重启必读它才知道"我是谁"。"""
    h = await head(db, root_id)
    idx = (await db.fetch_all(SQL_IDX, (root_id,)))[0]
    indexed, generation = int(idx["indexed"] or 0), int(idx["generation"] or 0)
    m = {"root_id": root_id, "head_seq": h["seq"], "head_hash": h["event_hash"],
         "indexed": indexed, "generation": generation,
         "body_token": body_token(root_id, h["seq"], h["event_hash"],
                                  indexed, generation),
         "summary_json": json.dumps(summary or {}, ensure_ascii=False, sort_keys=True),
         "unresolved_json": json.dumps(unresolved or [], ensure_ascii=False,
                                       sort_keys=True)}
    m["manifest_hash"] = manifest_hash(m)
    await db.execute(
        """INSERT INTO brain_boot_manifest
           (root_id, head_seq, head_hash, body_token, indexed, generation,
            summary_json, unresolved_json, manifest_hash, at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (root_id) DO UPDATE SET
             head_seq=EXCLUDED.head_seq, head_hash=EXCLUDED.head_hash,
             body_token=EXCLUDED.body_token, indexed=EXCLUDED.indexed,
             generation=EXCLUDED.generation, summary_json=EXCLUDED.summary_json,
             unresolved_json=EXCLUDED.unresolved_json,
             manifest_hash=EXCLUDED.manifest_hash, at=EXCLUDED.at""",
        (root_id, m["head_seq"], m["head_hash"], m["body_token"], indexed,
         generation, m["summary_json"], m["unresolved_json"], m["manifest_hash"],
         _iso_now()))
    return m


async def manifest(db, root_id: str = "main") -> dict | None:
    rows = await db.fetch_all(SQL_MANIFEST_ONE, (root_id,))
    return dict(rows[0]) if rows else None


async def targets_of(db, seq: int) -> list[dict]:
    return [dict(r) for r in await db.fetch_all(SQL_TARGETS_ONE, (seq,))]


def scan_targets_from_index(db_files: list[dict]) -> list[dict]:
    """把全量扫描的索引结果转成责任页（scan 事件用）。"""
    return [{"path": f["path"], "before_hash": f.get("sha256"),
             "after_hash": f.get("sha256")} for f in db_files]


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


__all__ = ["register", "head", "manifest", "refresh_manifest", "targets_of",
           "scan_targets_from_index", "new_trace_id", "ChainFrozen",
           "REQUIRED_TARGET_FIELDS"]
