# ─────────────────────────  body/hash.py  ─────────────────────────
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations
import hashlib, json

GENESIS = "0" * 64                     # 创世 prev_hash

def canon(obj) -> str:
    """全链路唯一序列化口径：sort_keys + 无空格 + 不转义非 ASCII。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def payload_text(payload) -> str:
    """payload 先规范成文本再入库；哈希与入库用同一份文本，杜绝两端漂移。"""
    return canon(payload)

def targets_text(rows) -> str:
    return canon([{"path": r["path"],
                   "before": r.get("before_hash"),
                   "after": r.get("after_hash")}
                  for r in sorted(rows, key=lambda r: r["path"])])

def targets_hash(rows) -> str:
    return hashlib.sha256(targets_text(rows).encode("utf-8")).hexdigest()

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def event_hash(seq, event_type, actor_tentacle, created_at,
               payload_json, targets_hash_, prev_hash) -> str:
    return _sha(canon({
        "seq": int(seq), "event_type": str(event_type),
        "actor_tentacle": actor_tentacle or "", "created_at": str(created_at),
        "payload_json": payload_json, "targets_hash": targets_hash_,
        "prev_hash": prev_hash}))

def manifest_hash(m: dict) -> str:
    return _sha(canon({"root_id": m["root_id"], "body_token": m["body_token"],
                       "head_seq": int(m["head_seq"]), "head_hash": m["head_hash"],
                       "summary_json": m["summary_json"],
                       "unresolved_json": m["unresolved_json"]}))

def body_token(root_id, head_seq, head_hash, indexed, generation) -> str:
    """身体指纹 = 链头 + 索引规模 + 索引代。索引被清空/回滚 → token 变 → 判定失忆。"""
    return _sha(canon({"ns": "body", "root_id": root_id, "head_seq": int(head_seq),
                       "head_hash": head_hash, "indexed": int(indexed),
                       "generation": int(generation)}))
