# core/mirror.py —— 镜像排练空间：假设 / 推演 / 打磨，落地要过闸门（V9 决策层）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用途：让她**先在镜像里排练**，不碰现实：
#   · open_space：对着一件事开一个排练空间（目标 + 约束）
#   · propose：丢一个假设进来（带依据与信心），不是结论
#   · step：在镜像里推演一步（会怎样 / 代价 / 风险），全部留在空间里
#   · digest：把这个空间的推演压成一页结论（可读，不改现实）
#   · promote：要把排练结果**落到现实**，必须过闸门（防偷懒钩子 + 生产闸门），过不去就不许落
# 纪律：镜像里的一切都**不影响现实**；落地必须留证据与票据，且由闸门裁决。
from core.swallow import swallow as _swallow
import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPACES = ROOT / "state" / "mirror"


def _now() -> float:
    return time.time()


def _path(sid: str) -> Path:
    return SPACES / f"{sid}.json"


def _load(sid: str) -> dict:
    p = _path(sid)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def _save(s: dict) -> None:
    SPACES.mkdir(parents=True, exist_ok=True)
    _path(s["id"]).write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def open_space(目标: str, *, 约束: tuple | list = (), by: str = "main") -> dict:
    if not str(目标 or "").strip():
        return {"ok": False, "reason": "目标不能为空"}
    sid = "mir_" + uuid.uuid4().hex[:10]
    s = {"id": sid, "目标": str(目标), "约束": [str(x) for x in 约束], "by": by,
         "at": _now(), "假设": [], "推演": [], "结论": "", "状态": "排练中",
         "落地": None}
    _save(s)
    return {"ok": True, "空间": sid, "目标": s["目标"], "约束": s["约束"],
            "口径": "镜像里的一切都不影响现实"}


def propose(sid: str, 假设: str, *, 依据: str = "", 信心: float = 0.5) -> dict:
    s = _load(sid)
    if not s:
        return {"ok": False, "reason": f"没有这个排练空间：{sid}"}
    if not str(假设 or "").strip():
        return {"ok": False, "reason": "假设不能为空"}
    rec = {"at": _now(), "假设": str(假设)[:400], "依据": str(依据)[:300],
           "信心": round(max(0.0, min(1.0, float(信心))), 3)}
    s["假设"].append(rec)
    _save(s)
    return {"ok": True, "空间": sid, "假设数": len(s["假设"]), "信心": rec["信心"]}


def step(sid: str, 推演: str, *, 代价: str = "", 风险: str = "") -> dict:
    """在镜像里推演一步：会怎样、要付什么代价、有什么风险 —— 都留在空间里。"""
    s = _load(sid)
    if not s:
        return {"ok": False, "reason": f"没有这个排练空间：{sid}"}
    rec = {"at": _now(), "推演": str(推演)[:400], "代价": str(代价)[:200],
           "风险": str(风险)[:200]}
    s["推演"].append(rec)
    _save(s)
    return {"ok": True, "空间": sid, "推演数": len(s["推演"])}


def digest(sid: str) -> dict:
    """把排练压成一页结论（可读）。这里仍然**不落地**。"""
    s = _load(sid)
    if not s:
        return {"ok": False, "reason": f"没有这个排练空间：{sid}"}
    top = sorted(s.get("假设") or [], key=lambda x: -float(x.get("信心") or 0))[:3]
    risks = [x for x in (s.get("推演") or []) if x.get("风险")]
    s["结论"] = (f"目标：{s.get('目标')}；候选假设 {len(s.get('假设') or [])} 条（最高信心 "
                f"{top[0]['信心'] if top else 0}）；推演 {len(s.get('推演') or [])} 步；"
                f"标注风险 {len(risks)} 处")
    _save(s)
    return {"ok": True, "空间": sid, "结论": s["结论"],
            "最高信心假设": [t["假设"] for t in top], "状态": s.get("状态")}


def promote(sid: str, *, need_ticket: bool = True, evidence: str = "",
            steps: tuple | list = ()) -> dict:
    """把排练结果**落到现实**：必须过"防偷懒钩子 + 生产闸门"；过不去就拒，不硬塞。"""
    from core import hooks as H
    s = _load(sid)
    if not s:
        return {"ok": False, "reason": f"没有这个排练空间：{sid}"}
    try:
        return _promote_guarded(s, sid, need_ticket=need_ticket, evidence=evidence, steps=steps)
    except H.HookError as exc:
        return {"ok": False, "reason": str(exc), "空间": sid, "状态": s.get("状态", "排练中"),
                "口径": "闸门没过就不许落地"}


def _promote_guarded(s: dict, sid: str, *, need_ticket: bool, evidence: str, steps) -> dict:
    from core import hooks as H
    g = H.Guard("GBT小土豆V9·镜像落地", must_steps=("排练摘要", "闸门裁决", "留痕"))
    with g.step("排练摘要", expect="空间里有假设与推演，且已出结论") as st:
        dig = digest(sid)
        if not (s.get("假设") and s.get("推演")):
            raise H.HookError("空排练不许落地：至少要有假设与推演各一条")
        st.evidence(假设=len(s["假设"]), 推演=len(s["推演"]),
                    fingerprint=H.fingerprint(len(s["假设"]), len(s["推演"])))
    with g.step("闸门裁决", expect="生产闸门放行（或明确拒绝）") as st:
        verdict = {"放行": True, "原因": "未接生产闸门时默认放行（如需严格门请在生产段接线）"}
        try:
            from core import production_gate as PG
            got = PG.summary() if hasattr(PG, "summary") else {}
            blocked = got.get("阻塞") or got.get("blocked") or []
            if blocked:
                verdict = {"放行": False, "原因": f"生产闸门未放行：{str(blocked)[:160]}"}
        except Exception as e:
            _swallow(__file__, e)
        st.evidence(放行=verdict["放行"], 原因=verdict["原因"][:80],
                    fingerprint=H.fingerprint(verdict["放行"]))
        if not verdict["放行"]:
            raise H.HookError(verdict["原因"])
    with g.step("留痕", expect="落地记录写进镜像空间（可回放）") as st:
        s["落地"] = {"at": _now(), "证据": str(evidence)[:300], "票据": bool(need_ticket),
                     "步骤": [str(x) for x in steps], "裁决": verdict}
        s["状态"] = "已落地"
        _save(s)
        st.evidence(状态=s["状态"], fingerprint=H.fingerprint(s["id"], s["状态"]))
    a = g.finish()
    out = {"ok": True, "空间": sid, "状态": "已落地", "裁决": verdict,
           "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "镜像不改现实；落地要过闸门并留证据"}
    try:
        from core import solidify as S
        S.solidify("mirror_promote", {"空间": sid, "目标": s.get("目标"),
                                      "假设": len(s.get("假设") or []),
                                      "推演": len(s.get("推演") or [])},
                   note="镜像排练 → 闸门放行 → 落地留痕")
    except Exception as e:
        _swallow(__file__, e)
    return out


def status(sid: str = "") -> dict:
    if sid:
        s = _load(sid)
        return s or {"ok": False, "reason": "没有这个排练空间"}
    SPACES.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(SPACES.glob("mir_*.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        rows.append({"空间": s["id"], "目标": s.get("目标"), "状态": s.get("状态"),
                     "假设": len(s.get("假设") or []), "推演": len(s.get("推演") or []),
                     "已落地": bool(s.get("落地"))})
    return {"空间数": len(rows), "空间": rows,
            "纪律": "镜像里不影响现实；落地必须过闸门 + 留证据"}


__all__ = ["open_space", "propose", "step", "digest", "promote", "status", "SPACES"]
