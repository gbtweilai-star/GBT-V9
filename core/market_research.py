# core/market_research.py —— 市场调研工作流引擎 + **推进闸门**
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要这个（主人原话口径）：
#   "不管做什么，在经过市场调研之后再推进，跟你盲目推进，差距在那里。"
#   → 所以调研不是一个可选的文档，而是一道**闸门**：没有完成调研的记录，
#     工作流就停在第一段，不许往下走（core.workflows 会来问这道闸门）。
#
# 纪律（可核查，不玩文字）：
#   · 维度不齐 / 每条没来源 → 提交直接被拒（宁可不收，也不收"拍脑袋"）；
#   · 本机能观测的，用真读数（账本/流水线/能力面/固化档案），标 observed + 来源；
#   · 本机观测不到的（竞品份额、真实定价…），**如实标"需联网/需人补"，绝不编数**；
#   · 结论只有三种：建议推进 / 建议不推进 / 补证据（等级 A/B/C 由来源决定）；
#   · 记录追加式落 state/market_research.jsonl + 账本 + 固化快照（可回放可回滚）。
from core.swallow import swallow as _swallow
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOURNAL = ROOT.joinpath("state", "market_research.jsonl")

# 证据等级（决定"能不能凭它推进"）
LEVEL_A = "A 第三方可核（公开报告/平台数据/真实报价单）"
LEVEL_B = "B 自有实测（本机真读数/自测实验/真实转化数据）"
LEVEL_C = "C 推断（经验判断/类比，需人工确认才可推进）"


@dataclass(frozen=True)
class Dimension:
    id: str
    名称: str
    要回答的问题: tuple
    需要的样本量: str
    来源类型: str                      # 本机可观测 | 需联网 | 需人补
    判定线: str                        # 什么算"这条过了"


DIMENSIONS: tuple = (
    Dimension("demand", "需求", (
        "谁在用？具体到人群/角色，不许写'所有人'",
        "他现在怎么解决？痛点有多痛（有没有为它花过钱）",
        "为什么是现在（触发时机/政策/技术拐点）",
    ), "≥5 个真实访谈或 ≥30 份问卷/评论样本", "需人补", "至少 3 条独立证据指向同一痛点"),
    Dimension("rivals", "竞品", (
        "头部 3 家是谁？他们做到哪一步了",
        "他们没做好的地方（我们的缝隙）",
        "他们的价格、渠道、节奏",
    ), "≥3 家竞品逐项拆解", "需联网", "能找到 1 条明确缝隙且有证据"),
    Dimension("scale", "规模与趋势", (
        "盘子多大（可核查的口径）",
        "在涨还是在跌，拐点在哪",
    ), "≥1 个可核查来源 + 我们的可达份额测算", "需联网", "可达份额够覆盖成本"),
    Dimension("price", "定价与成本", (
        "用户价格锚点是多少",
        "我们的成本结构（算力/人力/渠道）与毛利",
    ), "≥3 个真实报价锚点 + 成本清单", "需人补", "毛利 > 0 且有安全带"),
    Dimension("channel", "渠道与触达", (
        "第一批用户从哪来（可执行的获客路径）",
        "获客单价粗算",
    ), "≥1 条已验证的触达路径", "需人补", "有 1 条路径能跑通并能量化"),
    Dimension("risk", "风险与合规", (
        "法律/平台规则/版权风险",
        "被平台封禁/被抄袭的应对",
    ), "≥1 份规则原文核对", "本机可观测", "无红线项，或红线有可执行规避"),
    Dimension("feasible", "技术可行性", (
        "我们现有机件能做到哪一步（真读数）",
        "缺什么、要多久补",
    ), "本机能力面真读数 + 缺口清单", "本机可观测", "缺口有明确补法且不超预算"),
)


def _sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts else _now()))


# ───────────────────────── ① 调研方案（先立题，再收证） ─────────────────────────
def plan(topic: str, *, kind: str = "通用") -> dict:
    """给一个题材产出**调研方案**：每个维度要回答什么、要多少样本、判定线在哪。

    这一步是"不盲推"的具体化：先把"什么样的证据能让我下结论"写死，再去收。
    """
    t = str(topic or "").strip()
    if not t:
        return {"ok": False, "reason": "题材为空；先说要调研什么"}
    dims = []
    for d in DIMENSIONS:
        dims.append({**asdict(d), "来源类型": d.来源类型})
    return {"ok": True, "题材": t, "类型": kind, "维度数": len(DIMENSIONS),
            "维度": dims,
            "决策规则": {"建议推进": "≥5 个维度过线，且需求/竞品/可行性三线必过",
                         "建议不推进": "需求或可行性任一不过线且无补法",
                         "补证据": "其余情况（缺口列出来，别硬上）"},
            "等级口径": {"A": LEVEL_A, "B": LEVEL_B, "C": LEVEL_C},
            "下一步": "按维度收证 → POST /api/workflows/research/submit 提交"}


# ───────────────────────── ② 本机真读数（观测得到的部分） ─────────────────────────
def local_evidence(topic: str = "") -> dict:
    """本机**真能观测到**的证据：账本/流水线/能力面/固化档案的实读数。

    这里只放观测得到的。观不到的（竞品份额、真实定价…）在 dimensions 里已标
    "需联网/需人补" —— 本条函数**不许**替它们编数。
    """
    items: list = []

    def add(项, 值, 来源, 级别="observed", 说明=""):
        items.append({"项": 项, "值": 值, "来源": 来源, "级别": 级别, "说明": 说明})

    try:
        from core import deploy_ledger as DL
        s = DL.summary()
        add("变更台账条数", sum((s.get("by_kind") or {}).values()), "deploy_ledger.summary")
    except Exception as exc:                                   # noqa: BLE001
        add("变更台账条数", None, "deploy_ledger", "unavailable", type(exc).__name__)

    try:
        from core import pipelines as P
        st = P.status()
        rows = st.get("流水线") or []
        add("流水线已分类部署步骤",
            f"{sum(int(r.get('已分类部署') or 0) for r in rows)} / {sum(int(r.get('步骤数') or 0) for r in rows)}",
            "pipelines.status.流水线")
    except Exception as exc:                                   # noqa: BLE001
        add("流水线已分类部署步骤", None, "pipelines.status", "unavailable", type(exc).__name__)

    try:
        from core import capability_map as cm
        tot = cm._totals()
        add("云插件槽 / 数据库槽 / 触手", f"{tot.get('云插件')} / {tot.get('数据库槽')} / {tot.get('触手')}",
            "capability_map._totals")
    except Exception as exc:                                   # noqa: BLE001
        add("云插件槽 / 数据库槽 / 触手", None, "capability_map", "unavailable", type(exc).__name__)

    try:
        from core import solidify as S
        st = S.status()
        add("固化档案组数", len(st.get("names") or []), "solidify.status")
    except Exception as exc:                                   # noqa: BLE001
        add("固化档案组数", None, "solidify.status", "unavailable", type(exc).__name__)

    try:
        from skills import voice_sapi_shim  # noqa: F401
    except Exception as e:
        _swallow(__file__, e)
    try:
        from senses import voice_sapi as VS
        a = VS.asr_status()
        add("本机免费离线听写", ("可用 " + str(a.get("语言"))) if a.get("可用") else "不可用",
            "senses.voice_sapi.asr_status")
    except Exception as exc:                                   # noqa: BLE001
        add("本机免费离线听写", None, "voice_sapi", "unavailable", type(exc).__name__)

    gaps = [d for d in DIMENSIONS if d.来源类型 != "本机可观测"]
    return {"题材": str(topic or ""), "at": _iso(), "本机可观测": items,
            "本机观不到": [{"维度": d.名称, "要补什么": d.需要的样本量, "从哪来": d.来源类型}
                           for d in gaps],
            "口径": "只列观测到的真读数；观不到的一律标 pending，不替它编数字"}


# ───────────────────────── ③ 提交：维度不齐一律拒收 ─────────────────────────
def _read_all() -> list:
    if not JOURNAL.exists():
        return []
    out = []
    for ln in JOURNAL.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _append(rec: dict) -> bool:
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def grade(findings: list) -> str:
    """按来源给证据等级：只要有一条 A 就是 A；否则有 B 就是 B；否则 C。"""
    src = " ".join(str((f or {}).get("来源") or "") for f in findings or [])
    if any(k in src for k in ("公开", "报告", "平台数据", "报价单", "官方")):
        return "A"
    if any(k in src for k in ("实测", "本机", "自测", "日志", "账本", "实验")):
        return "B"
    return "C"


def verdict_of(dim_rows: list, lvl: str) -> dict:
    """三条必过线（需求/竞品/可行性）+ 过线维度数 → 结论。规则写在 plan() 里，这里照着判。"""
    passed = {r["维度"] for r in dim_rows if r.get("过线")}
    must = {"需求", "竞品", "技术可行性"}
    ok_must = must <= passed
    if len(passed) >= 5 and ok_must:
        v = "建议推进"
    elif ("需求" not in passed) or ("技术可行性" not in passed):
        v = "建议不推进"
    else:
        v = "补证据"
    if v == "建议推进" and lvl == "C":
        v = "补证据"                                          # 只有推断不许直接推进
    return {"结论": v, "过线维度数": len(passed), "必过线": sorted(must),
            "必过线是否全过": ok_must, "证据等级": lvl,
            "理由": ("≥5 维度过线且需求/竞品/可行性全过" if v == "建议推进"
                     else "需求或可行性未过线" if v == "建议不推进"
                     else "缺口待补（或仅有推断，需人工确认）")}


def submit(topic: str, findings: list, *, sources: list | None = None,
           by: str = "人工", dry_run: bool = False) -> dict:
    """提交调研结果。**维度不齐 / 条目无来源 → 拒收**（这就是"不盲推"的闸门本体）。

    findings: [{"维度": "需求", "结论": "...", "证据": "...", "来源": "...", "过线": true}]
    """
    t = str(topic or "").strip()
    if not t:
        return {"ok": False, "reason": "题材为空"}
    rows = [dict(f or {}) for f in (findings or [])]
    if not rows:
        return {"ok": False, "reason": "没有任何条目；调研不能空手提交"}
    names = {d.名称 for d in DIMENSIONS}
    bad = []
    for i, r in enumerate(rows):
        dim = str(r.get("维度") or "").strip()
        if dim not in names:
            bad.append(f"第{i + 1}条：维度 {dim!r} 不在 {sorted(names)} 里")
            continue
        if not str(r.get("来源") or "").strip():
            bad.append(f"第{i + 1}条（{dim}）：没有来源 —— 无来源的结论不收")
        if not str(r.get("证据") or "").strip():
            bad.append(f"第{i + 1}条（{dim}）：没有证据内容")
    if bad:
        return {"ok": False, "reason": "提交被拒（维度/来源不齐）", "问题": bad,
                "口径": "宁可不收，也不收拍脑袋的结论"}
    got_dims = {str(r.get("维度")).strip() for r in rows}
    missing = sorted(names - got_dims)
    lvl = grade(rows)
    dim_rows = [{"维度": str(r.get("维度")).strip(), "结论": str(r.get("结论") or ""),
                 "证据": str(r.get("证据") or ""), "来源": str(r.get("来源") or ""),
                 "过线": bool(r.get("过线"))} for r in rows]
    vd = verdict_of(dim_rows, lvl)
    rec = {"rec_id": _sha(t + str(_now()))[:12], "at": _now(), "iso": _iso(),
           "题材": t, "提交人": str(by or "人工"), "维度数": len(DIMENSIONS),
           "已答维度": sorted(got_dims), "缺维度": missing, "证据等级": lvl,
           "条目": dim_rows, "来源清单": [str(s) for s in (sources or [])][:50],
           **vd}
    rec["payload_sha256"] = _sha(json.dumps(rec, ensure_ascii=False, sort_keys=True))
    if dry_run:
        return {"ok": True, "dry_run": True, "记录": rec}
    if not _append(rec):
        return {"ok": False, "reason": "写调研记录失败（state/market_research.jsonl）"}
    try:
        from core import deploy_ledger as DL
        DL.record("add", "market_research:" + t,
                  detail=f"调研提交 结论={vd['结论']} 等级={lvl} 维度 {len(got_dims)}/{len(DIMENSIONS)}",
                  before="", after=rec["payload_sha256"], ok=(vd["结论"] == "建议推进"),
                  reason=("缺维度：" + "、".join(missing)) if missing else "")
    except Exception as e:
        _swallow(__file__, e)
    try:
        from core import solidify as S
        S.solidify("market_research_" + _sha(t)[:8], rec, note=f"调研：{t} → {vd['结论']}")
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": True, "记录": rec}


# ───────────────────────── ④ 推进闸门（工作流来问的就是它） ─────────────────────────
def latest(topic: str) -> dict | None:
    t = str(topic or "").strip()
    for rec in reversed(_read_all()):
        if rec.get("题材") == t:
            return rec
    return None


def gate(topic: str, *, max_age_days: float = 14.0, fresh: bool = False) -> dict:
    """调研闸门：能不能往下推进。

    允许推进必须**同时**满足：有记录 / 未过期 / 必过维度齐 / 结论=建议推进 / 等级 A或B。
    任何一条不满足 → blocked，并说明缺什么、怎么补（这才是"不盲推"的可执行形态）。
    """
    rec = latest(topic)
    if rec is None:
        return {"allowed": False, "state": "blocked", "题材": str(topic or ""),
                "reason": "还没有市场调研记录 → 按纪律不允许推进",
                "怎么开": "先出调研方案（plan）→ 收证 → 提交（submit）"}
    age_days = (_now() - float(rec.get("at") or 0)) / 86400.0
    stale = age_days > max_age_days
    missing = rec.get("缺维度") or []
    lvl = str(rec.get("证据等级") or "C")
    vd = str(rec.get("结论") or "")
    problems = []
    if stale:
        problems.append(f"调研已过期（{age_days:.1f} 天前，阈值 {max_age_days:g} 天）")
    if missing:
        problems.append("缺维度：" + "、".join(missing))
    if vd != "建议推进":
        problems.append(f"结论是「{vd}」而不是「建议推进」")
    if lvl.startswith("C"):
        problems.append("证据等级仅 C（推断），需人工书面确认")
    return {"allowed": not problems, "state": "open" if not problems else "blocked",
            "题材": str(topic or ""), "记录": rec, "过了几天": round(age_days, 1),
            "结论": vd, "证据等级": lvl, "缺维度": missing, "问题": problems,
            "reason": "调研已通过，允许推进" if not problems else "；".join(problems),
            "怎么开": "" if not problems else "补：缺的维度收证后重新 submit"}


def status() -> dict:
    recs = _read_all()
    by_verdict: dict = {}
    for r in recs:
        by_verdict[r.get("结论") or "—"] = by_verdict.get(r.get("结论") or "—", 0) + 1
    return {"维度数": len(DIMENSIONS), "维度": [asdict(d) for d in DIMENSIONS],
            "记录数": len(recs), "结论分布": by_verdict,
            "最近": recs[-1] if recs else None,
            "等级口径": {"A": LEVEL_A, "B": LEVEL_B, "C": LEVEL_C},
            "闸门口径": "有记录 + 未过期 + 必过维度齐 + 结论=建议推进 + 等级 A/B → 才允许推进"}


def history(limit: int = 20) -> list:
    return list(reversed(_read_all()))[: int(limit)]


__all__ = ["DIMENSIONS", "Dimension", "plan", "local_evidence", "submit", "gate",
           "latest", "status", "history", "grade", "verdict_of", "JOURNAL"]
