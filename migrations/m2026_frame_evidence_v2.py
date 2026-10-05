# migrations/m2026_frame_evidence_v2.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 单 ref → 双帧【不许猜】; 能回填的才回填, 不能的进 quarantine 并保留旧数据;
#       加列/建索引幂等(重复执行安全); 旧 ref 与 summary_json 不删, 保证可回滚。
import json

OPS = {"cut", "split", "speed", "reverse", "color", "effects"}
VERDICTS = {"verified", "failed", "unknown"}


def _columns(db, table):
    if db.dialect == "sqlite":
        return {r["name"] for r in db.fetch_all(f"PRAGMA table_info({table})")}
    return {r["column_name"] for r in db.fetch_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=?", (table,))}


def up(db):
    cols = _columns(db, "frame_verification_evidence")
    for name, typ in [("baseline_ref", "TEXT"), ("result_ref", "TEXT"),
                      ("op", "TEXT"), ("verdict", "TEXT"),
                      ("migration_state", "TEXT NOT NULL DEFAULT 'ready'")]:
        if name not in cols:                                  # 幂等: 已有就跳过
            db.execute(f"ALTER TABLE frame_verification_evidence ADD COLUMN {name} {typ}")

    db.execute("""
      CREATE TABLE IF NOT EXISTS frame_evidence_migration_quarantine (
        evidence_id TEXT PRIMARY KEY, reason TEXT NOT NULL,
        legacy_ref TEXT, quarantined_at TEXT NOT NULL)""")

    # 用 Python 解析 JSON, 不用 JSON SQL 函数 —— 保 SQLite/PG 口径一致
    rows = db.fetch_all("""SELECT evidence_id, ref, summary_json, baseline_ref, result_ref, op, verdict
                           FROM frame_verification_evidence WHERE migration_state='ready'""")
    for r in rows:
        try:
            s = json.loads(r["summary_json"] or "{}")
        except (TypeError, ValueError):
            s = {}
        op = r["op"] or s.get("op")
        verdict = r["verdict"] or s.get("verdict")
        baseline = r["baseline_ref"] or s.get("baseline_ref") or (s.get("refs") or {}).get("baseline")
        result   = r["result_ref"]   or s.get("result_ref")   or (s.get("refs") or {}).get("result")

        # 只有显式 side 标记, 才允许把旧的单 ref 补到某一端
        side = s.get("legacy_ref_side")
        if r["ref"] and side == "baseline" and result:  baseline = baseline or r["ref"]
        elif r["ref"] and side == "result" and baseline: result   = result or r["ref"]

        reasons = []
        if op not in OPS:            reasons.append("missing_or_invalid_op")
        if verdict not in VERDICTS:  reasons.append("missing_or_invalid_verdict")
        if not baseline or not result: reasons.append("ambiguous_legacy_ref")

        if reasons:                                           # 无法映射 → 隔离, 不猜
            db.execute("""INSERT INTO frame_evidence_migration_quarantine
                          (evidence_id, reason, legacy_ref, quarantined_at)
                          VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                          ON CONFLICT(evidence_id) DO NOTHING""",
                       (r["evidence_id"], ",".join(reasons), r["ref"]))
            db.execute("UPDATE frame_verification_evidence SET migration_state='quarantined' "
                       "WHERE evidence_id=?", (r["evidence_id"],))
            continue

        db.execute("""UPDATE frame_verification_evidence
                      SET baseline_ref=?, result_ref=?, op=?, verdict=? WHERE evidence_id=?""",
                   (baseline, result, op, verdict, r["evidence_id"]))

    db.execute("CREATE INDEX IF NOT EXISTS ix_fve_op_verdict_time "
               "ON frame_verification_evidence(op, verdict, created_epoch DESC, evidence_id DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_fve_baseline_ref "
               "ON frame_verification_evidence(baseline_ref)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_fve_result_ref "
               "ON frame_verification_evidence(result_ref)")
