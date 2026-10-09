# body/anchor.py —— head hash 外部锚点（防整库重建）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 签名密钥绝不入库; 锚点存储必须至少一个在数据库之外;
#       写入用条件写(if-none-match)保证不可覆盖; 校验失败一律按篡改处理(fail closed)
from __future__ import annotations
import asyncio, hashlib, hmac, json, os, time, uuid
from dataclasses import dataclass
from pathlib import Path
from body.hash import canon, GENESIS
from core.swallow import swallow as _swallow

# ── 锚定的最小字段集（少一个都能重放）──
def anchor_record(root_id, epoch, seq, head_hash, prev_anchor_hash, kid, at) -> dict:
    return {"ns": "anchor", "root_id": root_id, "epoch": epoch, "seq": int(seq),
            "head_hash": head_hash, "prev_anchor_hash": prev_anchor_hash,
            "kid": kid, "at": at}

def anchor_hash(rec: dict) -> str:
    return hashlib.sha256(canon(rec).encode("utf-8")).hexdigest()

def sign(kid: str, key_hex: str, rec: dict) -> str:
    return hmac.new(bytes.fromhex(key_hex), canon(rec).encode("utf-8"),
                    hashlib.sha256).hexdigest()

def anchor_key(prefix, root_id, epoch, seq, head_hash) -> str:
    return f"{prefix}/{root_id}/{epoch}/{int(seq):012d}-{head_hash[:16]}.json"

def latest_key(prefix, root_id, epoch) -> str:
    return f"{prefix}/{root_id}/{epoch}/LATEST.json"


# ═══ providers ═══
class LocalDirAnchor:
    """offline / dev。只能防数据库文件单独损坏，防不了整机重建。"""
    is_external = False
    name = "local"

    def __init__(self, root): self.root = Path(root)

    async def put(self, key, payload: bytes) -> dict:
        return await asyncio.to_thread(self._put, key, payload)

    def _put(self, key, payload):
        p = self.root / key
        if p.exists():
            if p.read_bytes() != payload:               # ★不可覆盖：同 key 不同内容 = 冲突
                return {"ok": False, "reason": "immutable_conflict", "ref": str(p)}
            return {"ok": True, "ref": str(p), "deduped": True}
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp"); tmp.write_bytes(payload)
        os.replace(tmp, p)                              # 原子落盘
        return {"ok": True, "ref": str(p)}

    async def get(self, key) -> bytes | None:
        p = self.root / key
        return p.read_bytes() if p.exists() else None

    async def list_keys(self, prefix) -> list[str]:
        d = self.root / prefix
        if not d.exists(): return []
        return sorted(str(p.relative_to(self.root)) for p in d.rglob("*.json"))


class R2Anchor:
    """full。← 对齐点 A：换成你现有 R2 客户端的实际方法名。"""
    is_external = True
    name = "r2"

    def __init__(self, client, bucket, prefix="anchors"):
        self.client, self.bucket, self.prefix = client, bucket, prefix

    async def put(self, key, payload: bytes) -> dict:
        try:
            # 条件写：对象已存在 → 412，绝不覆盖
            await self.client.put_object(Bucket=self.bucket, Key=key, Body=payload,
                                         IfNoneMatch="*")
            return {"ok": True, "ref": f"r2://{self.bucket}/{key}"}
        except Exception as e:
            if "PreconditionFailed" in type(e).__name__ or "412" in str(e):
                old = await self.get(key)
                if old == payload:
                    return {"ok": True, "ref": f"r2://{self.bucket}/{key}", "deduped": True}
                return {"ok": False, "reason": "immutable_conflict", "key": key}
            return {"ok": False, "reason": f"{type(e).__name__}: {e}"}

    async def get(self, key) -> bytes | None:
        try:
            r = await self.client.get_object(Bucket=self.bucket, Key=key)
            return await r["Body"].read()
        except Exception:
            return None

    async def list_keys(self, prefix) -> list[str]:
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token: kw["ContinuationToken"] = token
            r = await self.client.list_objects_v2(**kw)
            out += [o["Key"] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"): break
            token = r.get("NextContinuationToken")
        return sorted(out)


class MultiAnchor:
    """多存储写入 + 一致性策略。policy='all' 生产默认；'majority' 容忍单点故障。"""
    def __init__(self, providers, policy="all"):
        self.providers, self.policy = providers, policy
        assert policy in {"all", "majority"}

    def has_external(self) -> bool:
        return any(getattr(p, "is_external", False) for p in self.providers)

    async def put(self, key, payload: bytes) -> dict:
        results = await asyncio.gather(
            *(p.put(key, payload) for p in self.providers), return_exceptions=True)
        per = {}
        for p, r in zip(self.providers, results):
            per[p.name] = ({"ok": False, "reason": f"{type(r).__name__}: {r}"}
                           if isinstance(r, Exception) else r)
        need = len(self.providers) if self.policy == "all" else len(self.providers) // 2 + 1
        ok_n = sum(1 for r in per.values() if r.get("ok"))
        # ★外部见证必须成功（本地盘成功不算有锚点）
        ext_ok = False
        for p, r in per.items():
            prov = next((x for x in self.providers if x.name == p), None)
            if prov is not None and bool(getattr(prov, "is_external", False)) \
                    and r.get("ok"):
                ext_ok = True
                break
        return {"ok": ok_n >= need and ext_ok, "providers": per,
                "ok_count": ok_n, "need": need, "external_ok": ext_ok}

    async def list_union(self, prefix) -> dict[str, list[str]]:
        out = {}
        for p in self.providers:
            try:
                out[p.name] = await p.list_keys(prefix)
            except Exception as e:
                out[p.name] = [] if not getattr(p, "is_external", False) else []
        return out

    async def get(self, key) -> tuple[bytes | None, list[str]]:
        """返回 (内容, 命中它的 provider 名)。多副本内容不一致会在校验层暴露。"""
        hits = []
        for p in self.providers:
            try:
                b = await p.get(key)
            except Exception:
                b = None
            if b is not None:
                hits.append((p.name, b))
        if not hits:
            return None, []
        first = hits[0][1]
        if any(b != first for _, b in hits):        # 副本不一致 → 立刻算问题
            return None, [n for n, _ in hits] + ["__divergent__"]
        return first, [n for n, _ in hits]


# ═══════════ 锚点落库 + 外部复核（防整库重建）═══════════
SQL_ANCHORS = ("SELECT anchor_uid, root_id, epoch, seq, head_hash, anchor_hash, "
               "prev_anchor_hash, kid, at, providers_json, sealed "
               "FROM body_anchors WHERE root_id=? ORDER BY seq ASC")
SQL_LAST_REG = ("SELECT seq, event_hash FROM registration WHERE root_id=? "
                "ORDER BY seq DESC LIMIT 1")
SQL_ANCHOR_MAX = ("SELECT COALESCE(MAX(seq),0) AS seq FROM body_anchors "
                  "WHERE root_id=?")
SQL_PREV_ANCHOR = ("SELECT anchor_hash FROM body_anchors WHERE root_id=? "
                   "ORDER BY seq DESC LIMIT 1")
SQL_ANCHOR_ONE = "SELECT anchor_hash FROM body_anchors WHERE anchor_uid=?"
SQL_ANCHOR_SEQ = ("SELECT anchor_uid, head_hash, sealed FROM body_anchors "
                  "WHERE root_id=? AND epoch=? AND seq=?")


def utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def write_anchor(db, *, root_id, epoch, seq, head_hash, kid, at,
                       prev_anchor_hash=None, providers_json=None,
                       sealed=False) -> dict:
    """落一条锚点行。

    幂等键是 (root_id, epoch, seq)——**不是** at：
      同 seq 同 head_hash → 已锚过，直接返回（deduped），绝不写第二行
        （否则重启后再锚一次就会因 at 不同而误报冲突）；
      同 seq 不同 head_hash → 真冲突，按篡改处理直接抛错。
    """
    rec = anchor_record(root_id, epoch, seq, head_hash, prev_anchor_hash, kid, at)
    ah = anchor_hash(rec)
    uid = f"{root_id}/{epoch}/{int(seq)}"
    existing = await db.fetch_all(SQL_ANCHOR_SEQ, (root_id, epoch, int(seq)))
    if existing:
        if existing[0]["head_hash"] != head_hash:
            raise RuntimeError(
                "锚点冲突：seq=" + str(seq) + " 已有不同 head_hash（"
                + str(existing[0]["head_hash"])[:12] + " != " + head_hash[:12]
                + "）—— 按篡改处理")
        return {"anchor_uid": existing[0]["anchor_uid"], "anchor_hash": ah,
                "deduped": True, "sealed": int(existing[0]["sealed"] or 0)}
    await db.execute(
        """INSERT INTO body_anchors
           (anchor_uid, root_id, epoch, seq, head_hash, anchor_hash,
            prev_anchor_hash, kid, at, providers_json, sealed)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, root_id, epoch, int(seq), head_hash, ah, prev_anchor_hash, kid,
         at, providers_json, 1 if sealed else 0))
    return {"anchor_uid": uid, "anchor_hash": ah, "deduped": False}


async def verify_anchors(db, anchor_multi=None, *, root_id="main", epoch=None,
                         export_dir=None) -> dict:
    """外部锚点复核：库里锚链自洽 + 与外部存储比对 + 库回滚检测。

    任一硬判据命中 → ok=False（按篡改处理）；外部不可达只记 external_unreachable
    （不是篡改，走 degraded 去抖）。
    """
    rows = [dict(r) for r in await db.fetch_all(SQL_ANCHORS, (root_id,))]
    if epoch:
        rows = [r for r in rows if r["epoch"] == epoch]
    problems: list[dict] = []
    prev_ah, seen_seq = None, {}
    for r in rows:
        rec = anchor_record(r["root_id"], r["epoch"], r["seq"], r["head_hash"],
                            r["prev_anchor_hash"], r["kid"], r["at"])
        recomputed = anchor_hash(rec)
        if recomputed != r["anchor_hash"]:
            problems.append({"kind": "anchor_hash_mismatch",
                             "anchor_uid": r["anchor_uid"],
                             "recorded": r["anchor_hash"], "recomputed": recomputed})
        if (r["prev_anchor_hash"] or GENESIS) != (prev_ah or GENESIS):
            problems.append({"kind": "prev_anchor_hash_break",
                             "anchor_uid": r["anchor_uid"],
                             "expected": prev_ah, "found": r["prev_anchor_hash"]})
        key = int(r["seq"])
        if key in seen_seq and seen_seq[key] != r["head_hash"]:
            problems.append({"kind": "same_seq_conflict", "seq": key,
                             "head_hashes": [seen_seq[key], r["head_hash"]]})
        seen_seq[key] = r["head_hash"]
        prev_ah = r["anchor_hash"]

    head = (await db.fetch_all(SQL_LAST_REG, (root_id,)) or [None])[0]
    head_seq = int(head["seq"]) if head else 0
    head_hash = head["event_hash"] if head else GENESIS
    max_anchor_seq = int((await db.fetch_all(SQL_ANCHOR_MAX, (root_id,)))[0]["seq"] or 0)
    if head_seq < max_anchor_seq:
        problems.append({"kind": "db_rollback_before_anchor",
                         "db_head_seq": head_seq, "max_anchor_seq": max_anchor_seq})
    if rows and head_seq == int(rows[-1]["seq"]) and head_hash != rows[-1]["head_hash"]:
        problems.append({"kind": "same_seq_conflict", "seq": head_seq,
                         "head_hashes": [rows[-1]["head_hash"], head_hash]})

    ext = {"checked": 0, "status": "not_configured"}
    if anchor_multi and rows:
        ep = rows[-1]["epoch"]
        key = latest_key(getattr(anchor_multi, "prefix", "anchors"), root_id, ep)
        try:
            payload, hits = await anchor_multi.get(key)
        except Exception as e:
            payload, hits = None, [f"error:{type(e).__name__}"]
        if payload is None:
            problems.append({"kind": "external_unreachable", "key": key,
                             "providers": hits})
            ext = {"checked": 0, "status": "unreachable"}
        else:
            try:
                got = json.loads(payload.decode("utf-8"))
            except Exception:
                got = {}
            if got.get("head_hash") != rows[-1]["head_hash"]:
                problems.append({"kind": "external_anchor_mismatch", "key": key,
                                 "external": got.get("head_hash"),
                                 "db": rows[-1]["head_hash"]})
            ext = {"checked": 1, "status": "compared", "providers": hits}

    hard = [p for p in problems if p["kind"] != "external_unreachable"]
    out = {"ok": not hard, "checked": len(rows), "problems": problems,
           "external": ext,
           "undetectable_window": {"from": max_anchor_seq + 1, "to": head_seq},
           "head_seq": head_seq, "max_anchor_seq": max_anchor_seq}
    if hard and export_dir:
        try:
            Path(export_dir).mkdir(parents=True, exist_ok=True)
            ep = Path(export_dir) / f"anchor_problems_{int(time.time())}.json"
            ep.write_text(json.dumps(out, ensure_ascii=False, sort_keys=True,
                                     indent=2), encoding="utf-8")
            out["artifact"] = str(ep)
        except Exception as e:
            _swallow(__file__, e)

    return out


class AnchorWriter:
    """定期把链头锚到外部（≥1 个外部存储），并落 body_anchors 行。

    maybe(force=True) 停机前强制锚一次；平时按 min_interval 节流，链头没动就跳过。
    """

    def __init__(self, db, multi, *, root_id="main", epoch="e1", kid=None,
                 prefix="anchors", min_interval=900):
        self.db, self.multi = db, multi
        self.root_id, self.epoch = root_id, epoch
        self.kid = kid or os.environ.get("BODY_ANCHOR_KID", "anchor/1")
        self.prefix = prefix
        self.min_interval = float(min_interval)
        self._last_seq, self._last_at = -1, 0.0

    async def maybe(self, *, force=False) -> dict:
        head = (await self.db.fetch_all(SQL_LAST_REG, (self.root_id,)) or [None])[0]
        if not head:
            return {"ok": False, "reason": "empty_chain"}
        seq, hh = int(head["seq"]), head["event_hash"]
        now = time.time()
        if not force and seq == self._last_seq:
            return {"ok": True, "skipped": "head_unchanged", "seq": seq}
        if not force and (now - self._last_at) < self.min_interval:
            return {"ok": True, "skipped": "interval", "seq": seq}
        prev = (await self.db.fetch_all(SQL_PREV_ANCHOR, (self.root_id,)) or [None])[0]
        prev_ah = prev["anchor_hash"] if prev else GENESIS
        at = utc_now_iso()
        rec = anchor_record(self.root_id, self.epoch, seq, hh, prev_ah, self.kid, at)
        payload = canon(rec).encode("utf-8")
        res = await self.multi.put(anchor_key(self.prefix, self.root_id,
                                              self.epoch, seq, hh), payload)
        latest = await self.multi.put(latest_key(self.prefix, self.root_id,
                                                 self.epoch), payload)
        sealed = bool(res.get("ok") and latest.get("ok"))
        row = await write_anchor(self.db, root_id=self.root_id, epoch=self.epoch,
                                 seq=seq, head_hash=hh, kid=self.kid, at=at,
                                 prev_anchor_hash=prev_ah,
                                 providers_json=json.dumps(res, ensure_ascii=False),
                                 sealed=sealed)
        self._last_seq, self._last_at = seq, now
        return {"ok": sealed, "seq": seq, "anchor": row, "put": res,
                "latest": latest}
