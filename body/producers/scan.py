# body/producers/scan.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径: 分母只认【成功结束的全量扫描】冻结的项目树 generation;
#       没有全量扫描 / generation 不匹配 → coverage=null, 不许拿"已扫数"自作分母
import os
from body.snapshots import db_now, evidence, source, publish_snapshot

PERIOD = int(os.getenv("SCAN_SAMPLE_PERIOD", 300))

# ← 对齐点：表名按你实际的 scan_sweeps / scan_coverage / cross_scan_results / audit_gaps
SQL_LAST_SWEEP = """SELECT sweep_id, tree_generation, enumerated, finished_at
                    FROM scan_sweeps WHERE mode='full' AND status='complete'
                    ORDER BY finished_at DESC LIMIT 1"""
SQL_SCANNED = "SELECT COUNT(*) AS n FROM scan_coverage WHERE sweep_id=? AND state='scanned'"
SQL_FINDINGS= "SELECT COUNT(*) AS n FROM cross_scan_results WHERE sweep_id=?"
SQL_GAPS    = "SELECT COUNT(*) AS n FROM audit_gaps WHERE sweep_id=?"
SQL_GAP_BY_T= """SELECT responsible_tentacle AS tentacle_id, COUNT(*) AS gaps
                 FROM audit_gaps WHERE sweep_id=?
                 GROUP BY responsible_tentacle ORDER BY gaps DESC LIMIT 10"""
SQL_GEN_NOW = """SELECT COALESCE(MAX(generation),0) AS g FROM body_files"""


async def collect(ledger, *, state, now_fn=None):
    end = await db_now(ledger)
    sweep = await ledger.fetch_one(SQL_LAST_SWEEP)

    if sweep is None:                                   # ★从没跑过完整全量扫描
        payload = {"status": "unknown", "reason": "no_complete_full_sweep",
                   "expected": None, "scanned": None, "coverage": None,
                   "findings": None, "gaps": None, "top_tentacles": []}
        return await publish_snapshot(ledger, "scan", payload=payload,
                                      evidence=evidence(window=None,
                                          query_id="scan.coverage.v1", params={},
                                          result_digest=_digest(payload),
                                          sources=[{"table": "scan_sweeps", "available": False}]),
                                      sample_period_s=PERIOD, producer="producers.scan")

    cur_gen = (await ledger.fetch_one(SQL_GEN_NOW))["g"]
    gen_ok = (sweep["tree_generation"] == cur_gen)
    scanned = (await ledger.fetch_one(SQL_SCANNED, (sweep["sweep_id"],)))["n"]
    findings = (await ledger.fetch_one(SQL_FINDINGS, (sweep["sweep_id"],)))["n"]
    gaps = (await ledger.fetch_one(SQL_GAPS, (sweep["sweep_id"],)))["n"]
    by_t = await ledger.fetch_all(SQL_GAP_BY_T, (sweep["sweep_id"],))

    expected = sweep["enumerated"]
    if not gen_ok:                                      # 树变了 → 旧的覆盖数不算数
        status, coverage = "partial", None
    elif not expected:
        status, coverage = "unknown", None
    else:
        status, coverage = ("complete" if scanned >= expected else "partial"), \
                           round(scanned / expected, 4)

    payload = {"status": status, "reason": (None if gen_ok else "tree_generation_changed"),
               "sweep_id": sweep["sweep_id"], "sweep_finished_at": sweep["finished_at"],
               "expected": expected, "scanned": scanned, "coverage": coverage,
               "findings": findings, "gaps": gaps,
               "top_tentacles": [{"tentacle_id": r["tentacle_id"] or "unassigned",
                                  "gaps": r["gaps"]} for r in by_t]}
    ev = evidence(window=None, query_id="scan.coverage.v1",
                  params={"sweep_id": sweep["sweep_id"], "mode": "full"},
                  result_digest=_digest(payload),
                  sources=[source("scan_coverage", time_column="scanned_at",
                                  w=(sweep["finished_at"], sweep["finished_at"]),
                                  row_count=scanned),
                           source("cross_scan_results", time_column="created_at",
                                  w=(sweep["finished_at"], sweep["finished_at"]),
                                  row_count=findings),
                           source("audit_gaps", time_column="created_at",
                                  w=(sweep["finished_at"], sweep["finished_at"]),
                                  row_count=gaps),
                           {"table": "scan_sweeps", "sweep_id": sweep["sweep_id"],
                            "tree_generation": sweep["tree_generation"],
                            "current_generation": cur_gen, "available": True}])
    return await publish_snapshot(ledger, "scan", payload=payload, evidence=ev,
                                  sample_period_s=PERIOD, producer="producers.scan")
