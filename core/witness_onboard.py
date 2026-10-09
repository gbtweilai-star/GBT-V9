# core/witness_onboard.py —— 见证开通（一条命令走完：探测 → 登记 → 校验 → 报就绪度）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么需要它：见证是"独立锚"——**必须**是另一处有凭据的对象存储（S3 兼容，如
# Cloudflare R2 / Backblaze B2 / MinIO），这是它的设计底线，不能用本地假锚冒充
# （那会变成"空壳见证"，比没有更糟）。所以这里把"上线"做到只剩一步：
#   · plan()     —— 看清每个见证 id 缺哪些环境变量（只读，不发请求）
#   · onboard()  —— 对已配置的 id 逐个走七步 dry-run，全绿才登记，然后跑一轮复核
#   · status()   —— 当前登记数/有效票/要求数 + 还缺什么（面板与语音共用）
# 凭据只从环境变量读；源码/测试里不出现任何真实凭据。
import os
import time

from core import deploy_ledger as J

# 候选见证 id（编号即身份；每号一套 BODY_WITNESS_<编号>_* 环境变量）
CANDIDATE_IDS = tuple(
    x.strip() for x in os.environ.get("V9_WITNESS_IDS", "W1,W2,W3,W4").split(",") if x.strip())
REQUIRED_VARS = ("KIND", "PROVIDER", "ENDPOINT", "BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY")
DEFAULT_KIND = "s3"


def _prefix(wid: str) -> str:
    return "BODY_WITNESS_" + str(wid).upper().replace("-", "_") + "_"


def plan() -> dict:
    """只读：每个候选 id 的变量齐不齐 + 缺哪几项（不打印任何值）。"""
    rows, ready = [], []
    for wid in CANDIDATE_IDS:
        pre = _prefix(wid)
        have = {v: bool(os.environ.get(pre + v)) for v in REQUIRED_VARS}
        missing = [v for v, ok in have.items() if not ok]
        rows.append({"见证": wid, "前缀": pre, "缺失": missing,
                     "就绪": not missing})
        if not missing:
            ready.append(wid)
    return {"候选": list(CANDIDATE_IDS), "明细": rows, "可登记": ready,
            "可登记数": len(ready),
            "要求数": int(os.environ.get("BODY_WITNESS_REQUIRED", "2")),
            "还缺": sorted({v for r in rows for v in r["缺失"]}),
            "说明": "凭据只从环境变量读；缺 kind 时默认 s3；endpoint 必须 https 且非内网"}


async def _recheck(db) -> dict:
    from body.witness_runtime import load_witnesses, verify_all
    try:
        ws = await load_witnesses(db, anchor_multi=getattr(db, "anchor_multi", None))
        rep = await verify_all(db, ws)
        return {"登记见证": len(ws), "复核": {k: v for k, v in (rep or {}).items()
                                            if k in ("quorum", "witnesses", "required")}}
    except Exception as exc:                                  # noqa: BLE001
        return {"登记见证": None, "复核失败": f"{type(exc).__name__}: {str(exc)[:120]}"}


async def onboard(db, *, dry_run: bool = False, approve_by: str = "GBT小土豆V9") -> dict:  # 主人令：默认真动手（要演练请显式传 True）
    """把已配置好的见证逐个接上：七步 dry-run 全绿才登记；随后跑一轮复核。

    dry_run=True 时只探测不登记（默认先看结果）；确认真能通再 dry_run=False。
    """
    from body.witness_admin import load_witness_config, make_client, probe_witness
    p = plan()
    out = {"计划": p, "已登记": [], "失败": [], "dry_run": dry_run}
    for wid in p["可登记"]:
        try:
            cfg = load_witness_config(wid)
            client = make_client(cfg)
            pb = probe_witness(cfg, client, dry_run=True)
            if not pb.get("ok"):
                out["失败"].append({"见证": wid, "原因": "七步 dry-run 未全绿",
                                    "首处失败": pb.get("first_failure")})
                continue
            if dry_run:
                out["已登记"].append({"见证": wid, "状态": "dry-run 通过（未登记）",
                                      "证据": pb.get("caps") or {}})
                continue
            from body.witness_admin import register_witness
            reg = await register_witness(db, cfg, pb, approve_by=approve_by)
            out["已登记"].append({"见证": wid, "状态": "已登记", "结果": reg})
        except Exception as exc:                              # noqa: BLE001
            out["失败"].append({"见证": wid, "原因": f"{type(exc).__name__}: {str(exc)[:160]}"})
    out["复核"] = await _recheck(db)
    J.record("deploy", "witness_onboard", detail={"可登记": p["可登记"],
                                                  "已登记": len(out["已登记"]),
                                                  "失败": len(out["失败"]),
                                                  "dry_run": dry_run,
                                                  "复核": out["复核"]},
             ok=bool(out["已登记"]) or p["可登记数"] == 0,
             reason="" if p["可登记数"] else f"缺环境变量：{p['还缺']}")
    return out


async def status(db) -> dict:
    """见证就绪度：登记数 / 有效票 / 要求数 / 还缺什么（面板与语音共用一套口径）。"""
    q = {}
    try:
        rows = await db.fetch_all("SELECT witness_id, vote_eligible, identity_status "
                                  "FROM body_witness_status")
        snap = await db.fetch_all("SELECT valid_count, required, status FROM witness_snapshot "
                                  "WHERE id=1")
        q = {"登记见证": len(rows or []),
             "可计票": sum(1 for r in (rows or []) if r.get("vote_eligible")),
             "有效票": (snap[0]["valid_count"] if snap else None),
             "要求": (snap[0]["required"] if snap else None),
             "状态": (snap[0]["status"] if snap else None)}
    except Exception as exc:                                  # noqa: BLE001
        q = {"读取失败": type(exc).__name__}
    p = plan()
    ok = bool(q.get("登记见证")) and (q.get("可计票") or 0) >= (q.get("要求") or 2)
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "就绪": ok,
            "现状": q, "开通计划": p,
            "下一步": ("见证已满足要求" if ok else
                       (f"设置这些环境变量后一键开通：{p['还缺']}" if p['还缺'] else
                        "已配置齐 → 调 POST /api/witness/onboard（dry_run=false）登记"))}


__all__ = ["plan", "onboard", "status", "CANDIDATE_IDS", "REQUIRED_VARS"]
