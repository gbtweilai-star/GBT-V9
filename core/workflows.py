# core/workflows.py —— 工作流注册表：多智能体协作 + **调研前置** + **验收标准**
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：
#   ① 工作流要有**独立页面**，不是埋在别处；
#   ② 每个工作流第一段必须是**市场调研**，调研没过闸门就不许往下推
#      （"经过调研再推进" vs "盲目推进"，差距就在这里）；
#   ③ 每一段都要写清：谁负责（智能体/触手班）、吃什么、产出什么、门禁、**验收标准**；
#   ④ 项目做完要能回答"作品达不达预期标准" → 每段 + 整条都有可判定的验收项。
#
# 与既有件的关系（不重复造）：
#   · 生产段的步骤明细来自 core.pipelines / core.kit_packs（真部署状态）；
#   · 调研闸门来自 core.market_research.gate（真记录，不是占位）；
#   · 验收读数来自 core.solidify / core.deploy_ledger / core.loop_verifier（真证据）；
#   · 多智能体协作编排口径对齐 core.orchestration.team（分解→派发→复核→终止→人工接管）。
from core.swallow import swallow as _swallow
from dataclasses import asdict, dataclass, field

from common.ttl_cache import TTLCache as _TTL

_STATUS_CACHE = _TTL(ttl=30.0, name="workflows_status")

# 触手班（100 根的分工：调研 / 生产 / 复查 / 验收，区间不重叠且全覆盖）
SQUADS: tuple = (
    ("调研班", "t001", "t012", "桌面研究、竞品拆解、样本收集"),
    ("生产班", "t013", "t062", "四条流水线的产线执行"),
    ("质检班", "t063", "t086", "成片/成品的技术质检（真读数）"),
    ("验收班", "t087", "t100", "对照预期标准逐条验收 + 落账"),
)


@dataclass(frozen=True)
class Stage:
    id: str
    名称: str
    段: str                     # 调研 | 设计 | 生产 | 质检 | 交付
    负责: tuple                 # 智能体/部门（来自 Octop 花名册口径）
    触手: str                   # 触手班名
    输入: str
    产出: str
    门禁: str = "无"
    验收: tuple = field(default_factory=tuple)
    证据: str = ""              # 真实证据在哪读（页面会照着读）


@dataclass(frozen=True)
class Flow:
    id: str
    名称: str
    类别: str
    目标: str
    题材: str                   # 调研题材（闸门按它查）
    阶段: tuple
    预期标准: tuple             # 整条工作流的交付标准（逐条可判定）
    依赖: tuple = field(default_factory=tuple)


_RESEARCH_STAGES: tuple = (
    Stage("r1", "立题与方案", "调研", ("市场调研智能体", "产品经理"), "调研班",
          "一句话需求", "调研方案（7 维度 · 判定线 · 样本量）",
          "无", ("方案含 7 维度且写明判定线", "写明样本量与来源类型"),
          "market_research.plan"),
    Stage("r2", "收证（桌面+竞品+需求）", "调研", ("市场调研智能体", "竞品分析智能体"), "调研班",
          "调研方案", "逐维度证据（每条带来源）", "无",
          ("每条证据都有来源", "需求维度 ≥5 条独立证据"), "market_research.local_evidence"),
    Stage("r3", "结论与决策", "调研", ("产品经理", "总指挥"), "调研班",
          "逐维度证据", "调研记录（结论 + 证据等级 + 缺口）", "调研闸门",
          ("结论 ∈ {建议推进, 建议不推进, 补证据}", "必过维度（需求/竞品/可行性）都有明确判定"),
          "market_research.gate"),
)


def _prod_stages(flow_id: str, 名称前缀: str) -> tuple:
    """生产段：直接引用 pipelines 里的真实步骤状态（不另造一套）。"""
    return (
        Stage(f"{flow_id}_p1", f"{名称前缀}·产线准备", "生产",
              ("制片/工程智能体",), "生产班", "调研通过", "产线就绪（步骤全部已分类部署）",
              "无", ("步骤全部已分类部署（pipelines.status 真读数）", "无待接步骤或已有替代实现"),
              "pipelines.status"),
        Stage(f"{flow_id}_p2", f"{名称前缀}·执行产出", "生产",
              ("制片/工程智能体",), "生产班", "产线就绪", "成片/成品（落盘）",
              "无", ("成品文件存在且可解码", "账本里有产出记录"), "alt_impl / 媒体队列"),
        Stage(f"{flow_id}_p3", f"{名称前缀}·技术质检", "质检",
              ("质检智能体",), "质检班", "成品", "质检报告",
              "无", ("时长/响度/分辨率在阈值内", "无丢帧或无失败重试"), "loop_verifier / alt_impl"),
    )


WORKFLOWS: tuple = (
    Flow("market_research", "市场调研工作流", "调研",
         "任何项目推进前的第一道闸门：把「要不要做」变成有证据的结论",
         "AI 短视频代做",
         _RESEARCH_STAGES + (
             Stage("r4", "闸门放行", "交付", ("总指挥",), "验收班", "调研记录", "放行/不放过",
                   "调研闸门", ("闸门 allowed=true 才允许放行到生产",),
                   "market_research.gate"),
         ),
         ("7 个维度全部有结论（不许留空）", "每条证据可回溯来源",
          "结论明确（推进/不推进/补证据），不含糊", "必过维度有量化判定"),
         ()),

    Flow("shortvideo", "AI 短视频工作流", "内容",
         "从选题到成片：调研 → 脚本 → 素材 → 剪辑 → 字幕 → 配音 → 质检 → 验收",
         "AI 短视频代做",
         _RESEARCH_STAGES + _prod_stages("sv", "短视频") + (
             Stage("sv_d1", "字幕与配音", "生产", ("剪辑智能体", "配音智能体"), "生产班",
                   "成片", "带字幕成片 + 配音轨", "无",
                   ("字幕与音频对齐（SRT 时间轴不越界）", "响度达标（-14 LUFS 附近）"),
                   "alt_impl.burn_subtitles / master"),
             Stage("sv_a1", "逐条验收", "交付", ("验收智能体", "总指挥"), "验收班",
                   "成片 + 质检报告", "验收结论（达标/返工）", "发布前人工确认",
                   ("竖屏 1080×1920 或按投放规格", "时长在目标区间", "无版权风险素材",
                    "账号发布清单齐（标题/封面/话题）"),
                   "workflows.acceptance + deploy_ledger"),
         ),
         ("调研闸门 open", "成片可解码且时长/规格达标", "响度 -14 LUFS ±1",
          "字幕时间轴与音频对齐", "每条预期标准都有采证记录"),
         ("market_research",)),

    Flow("film", "AI 电影工作流", "内容",
         "长片工序：调研 → 剧本 → 分镜 → 场景生成 → 剪辑 → 混音 → 质检 → 验收",
         "AI 电影制作",
         _RESEARCH_STAGES + _prod_stages("fm", "电影") + (
             Stage("fm_a1", "逐条验收", "交付", ("验收智能体", "总指挥"), "验收班",
                   "母带 + 质检报告", "验收结论", "发布前人工确认",
                   ("画面无占位素材", "整片可连续播放无断点", "响度达标", "交付清单齐"),
                   "workflows.acceptance"),
         ),
         ("调研闸门 open", "母带可解码且规格达标", "无占位/假素材",
          "响度达标", "预期标准逐条有采证"),
         ("market_research",)),

    Flow("music", "AI 音乐工作流", "内容",
         "调研 → 词曲 → 编曲 → 人声/合成 → 混音母带 → 质检 → 验收",
         "AI 音乐发行",
         _RESEARCH_STAGES + _prod_stages("mu", "音乐") + (
             Stage("mu_a1", "逐条验收", "交付", ("验收智能体",), "验收班",
                   "母带音频 + 质检报告", "验收结论", "发布前人工确认",
                   ("响度 -14 LUFS ±1", "无爆音/削波", "元数据（标题/作者/封面）齐"),
                   "workflows.acceptance"),
         ),
         ("调研闸门 open", "响度 -14 LUFS ±1", "无削波", "元数据齐", "预期标准逐条有采证"),
         ("market_research",)),

    Flow("procode", "专业编程 AI 工作流", "工程",
         "调研 → 需求规格 → 架构 → 实现 → 测试 → 评审 → 交付",
         "专业编程 AI 交付",
         _RESEARCH_STAGES + _prod_stages("pc", "编程") + (
             Stage("pc_a1", "逐条验收", "交付", ("验收智能体", "评审智能体"), "验收班",
                   "代码 + 测试报告", "验收结论", "发布前人工确认",
                   ("测试全绿（真跑，不是声称）", "无高危安全发现", "可回滚（固化档案存在）",
                    "文档/用法齐"),
                   "workflows.acceptance + hollow_sweep"),
         ),
         ("调研闸门 open", "测试全绿", "无高危发现", "有固化可回滚", "预期标准逐条有采证"),
         ("market_research",)),

    Flow("agent_collab", "多智能体协作工作流", "协作",
         "编排者分解 → 派发（智能体/触手）→ 复核 → 终止（一致/轮数/截止/人工）→ 交付",
         "多智能体协作",
         (
             Stage("c1", "立题（含调研前置）", "调研", ("总指挥", "市场调研智能体"), "调研班",
                   "任务描述", "任务书 + 调研闸门状态", "调研闸门",
                   ("调研闸门 open 或明确豁免理由",), "market_research.gate"),
             Stage("c2", "分解", "设计", ("编排者",), "生产班", "任务书",
                   "子任务清单（每条带 owner + done_when）", "无",
                   ("子任务每条有 owner", "写明可判定的完成条件"), "orchestration.team.decompose"),
             Stage("c3", "派发执行", "生产", ("各域智能体",), "生产班", "子任务清单",
                   "执行结果 + trace_id", "无",
                   ("每次派发都有 trace_id", "结果有证据（日志/文件/账本）"), "tentacle_fleet.drive"),
             Stage("c4", "复核", "质检", ("复核者",), "质检班", "执行结果",
                   "复核结论（continue/revise/done）", "无",
                   ("复核给判定而不是形容词", "revise 的项有具体整改点"), "orchestration.team.review"),
             Stage("c5", "终止与交付", "交付", ("总指挥",), "验收班", "复核结论",
                   "交付物 + 终止原因", "人工接管",
                   ("终止原因显式（一致/轮数/截止/人工）", "交付物对照预期标准逐条验收"),
                   "orchestration.team.run"),
         ),
         ("调研闸门 open", "每个子任务有 owner 与完成条件", "复核有判定",
          "终止原因显式", "交付物逐条验收"),
         ("market_research",)),
)


def _flows() -> tuple:
    """全部工作流 = 内置 4 条 + 蒸馏出的专业 SOP（core.playbooks，主人 2026-10-08）。

    懒导入避免循环依赖（playbooks 要用本模块的 Stage/Flow）。
    """
    try:
        from core.playbooks import PLAYBOOKS
        return WORKFLOWS + tuple(PLAYBOOKS)
    except Exception:                                          # noqa: BLE001
        return WORKFLOWS


def catalog() -> dict:
    return {f.id: {"id": f.id, "名称": f.名称, "类别": f.类别, "目标": f.目标,
                   "题材": f.题材, "阶段数": len(f.阶段), "依赖": list(f.依赖),
                   "阶段": [asdict(s) for s in f.阶段], "预期标准": list(f.预期标准)}
            for f in _flows()}


def flow(flow_id: str) -> Flow | None:
    for f in _flows():
        if f.id == flow_id:
            return f
    return None


def squads() -> list:
    return [{"班": n, "从": a, "到": b, "职责": d} for n, a, b, d in SQUADS]


# ───────────────── 段状态（用真证据判，不用形容词） ─────────────────
def research_gate(flow_id: str) -> dict:
    f = flow(flow_id)
    if f is None:
        return {"allowed": False, "reason": f"未知工作流 {flow_id}"}
    from core import market_research as MR
    return MR.gate(f.题材)


def _prod_evidence(flow_id: str) -> dict:
    """生产段的真读数：流水线部署状态。"""
    pid = {"shortvideo": "shortvideo", "film": "film", "music": "music",
           "procode": "procode"}.get(flow_id)
    if not pid:
        return {"ok": False, "reason": "该工作流没有流水线步骤（协作/调研类）"}
    try:
        from core import pipelines as P
        st = P.status()
        for row in st.get("流水线") or []:
            if row.get("id") == pid:
                return {"ok": True, "已分类部署": row.get("已分类部署"), "步骤数": row.get("步骤数"),
                        "待接": row.get("待接（无模型）"), "门禁步骤": row.get("门禁步骤")}
        return {"ok": False, "reason": "流水线清单里没有它"}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


def acceptance(flow_id: str) -> dict:
    """对照**预期标准**逐条判定：能自动判的给真读数，判不了的如实写"待采证"。"""
    f = flow(flow_id)
    if f is None:
        return {"ok": False, "reason": f"未知工作流 {flow_id}"}
    rows = []
    gate_state = research_gate(flow_id)
    prod = _prod_evidence(flow_id)
    solid_n = None
    try:
        from core import solidify as S
        solid_n = len(S.status().get("names") or [])
    except Exception:                                          # noqa: BLE001
        solid_n = None

    for std in f.预期标准:
        judge, ev, ok = "待采证", "", None
        if "调研闸门" in std:
            ok = bool(gate_state.get("allowed"))
            judge = "已通过" if ok else "未通过"
            ev = gate_state.get("reason", "")
        elif "响度" in std:
            judge, ev = "待采证", "需要一次母带真测（alt_impl.master 的 ebur128 读数）"
        elif "测试全绿" in std or "测试" in std:
            judge, ev = "待采证", "跑一次全量测试并把结果落到账本（真跑，不是声称）"
        elif "固化" in std or "回滚" in std:
            ok = bool(solid_n)
            judge = f"已具备（{solid_n} 组）" if ok else "缺失"
            ev = "solidify.status"
        elif "流水线" in std or "步骤" in std or "产线" in std:
            ok = bool(prod.get("ok"))
            judge = (f"{prod.get('已分类部署')}/{prod.get('步骤数')} 已部署"
                     if prod.get("ok") else "读数不可用")
            ev = "pipelines.status"
        elif "子任务" in std or "owner" in std or "复核" in std or "终止" in std:
            judge, ev = "待采证", "跑一次编排并把 transcripts 落到账本"
        elif "无占位" in std or "无版权" in std or "规格" in std or "时长" in std:
            judge, ev = "待采证", "对成品做一次真检（解码/时长/素材来源）"
        rows.append({"标准": std, "判定": judge, "通过": ok, "证据": ev or "—"})

    passed = sum(1 for r in rows if r["通过"] is True)
    pending = sum(1 for r in rows if r["通过"] is None)
    return {"ok": True, "工作流": f.名称, "标准数": len(rows), "已通过": passed,
            "待采证": pending, "未通过": sum(1 for r in rows if r["通过"] is False),
            "项": rows,
            "口径": "只有『通过=True』才算达标；『待采证』不是达标，是还没验"}


def status(*, fresh: bool = False) -> dict:
    """工作流总状态。带短 TTL 缓存：一次 status 要把每条工作流的调研闸门 + 验收都读一遍
    （实测 4~5 秒），页面每刷一次都重算是浪费，也让事件循环被堵住。"""
    if fresh:
        return _status_build()
    return _STATUS_CACHE.get("v", _status_build)


def _status_build() -> dict:
    out = []
    for f in WORKFLOWS:
        g = research_gate(f.id)
        acc = acceptance(f.id)
        # 段状态：调研段看闸门；生产段看流水线；质检/交付段看验收
        stages = []
        for s in f.阶段:
            if s.段 == "调研":
                st = "已通过" if (g.get("allowed") and s.id == "r3") else (
                    "阻塞" if not g.get("allowed") else "可执行")
                ev = g.get("reason", "")
            elif s.门禁 == "调研闸门":
                st = "已放行" if g.get("allowed") else "阻塞（等调研）"
                ev = g.get("reason", "")
            elif s.段 == "验收" or s.段 == "交付":
                st = "可执行" if g.get("allowed") else "阻塞（等调研）"
                ev = f"预计标准 {acc['标准数']} 条（已通过 {acc['已通过']} · 待采证 {acc['待采证']}）"
            else:
                st = "可执行" if g.get("allowed") else "阻塞（等调研）"
                ev = "pipelines.status" if s.证据.startswith("pipelines") else s.证据
            stages.append({**asdict(s), "状态": st, "当前证据": ev})
        out.append({"id": f.id, "名称": f.名称, "类别": f.类别, "目标": f.目标,
                    "题材": f.题材, "阶段数": len(f.阶段),
                    "调研闸门": {"allowed": g.get("allowed"), "reason": g.get("reason"),
                                "结论": g.get("结论"), "证据等级": g.get("证据等级")},
                    "验收": acc, "阶段": stages})
    return {"工作流数": len(WORKFLOWS), "触手班": squads(),
            "放行数": sum(1 for r in out if r["调研闸门"]["allowed"]),
            "阻塞数": sum(1 for r in out if not r["调研闸门"]["allowed"]),
            "清单": out,
            "纪律": "调研闸门未开 → 生产/验收段一律阻塞（不盲推）"}


# ───────────────── 编排图（页面画 SVG 用） ─────────────────
def graph(flow_id: str) -> dict:
    f = flow(flow_id)
    if f is None:
        return {"ok": False, "reason": f"未知工作流 {flow_id}"}
    g = research_gate(flow_id)
    nodes, edges = [], []

    def add(nid, 标题, 副, kind, state="可执行"):
        nodes.append({"id": nid, "标题": 标题, "副标题": 副, "kind": kind, "状态": state})

    add("in", "触发", "任务/需求", "start")
    last = "in"
    for s in f.阶段:
        st = "可执行"
        if s.段 == "调研":
            st = "已通过" if g.get("allowed") else "阻塞"
        elif not g.get("allowed"):
            st = "阻塞"
        add(s.id, s.名称, s.段 + " · " + s.触手, "gate" if s.门禁 != "无" else "stage", st)
        edges.append((last, s.id))
        last = s.id
    add("out", "交付", "对照预期标准验收", "end",
        "可执行" if g.get("allowed") else "阻塞")
    edges.append((last, "out"))
    return {"ok": True, "id": f.id, "名称": f.名称, "nodes": nodes,
            "edges": [{"from": a, "to": b} for a, b in edges],
            "闸门": {"allowed": g.get("allowed"), "reason": g.get("reason")},
            "预期标准": list(f.预期标准)}


def advance(flow_id: str, stage_id: str, *, by: str = "人工", note: str = "") -> dict:
    """尝试推进某一段。**调研闸门未开一律拒绝** —— 这就是"不盲推"的落地动作。"""
    f = flow(flow_id)
    if f is None:
        return {"ok": False, "reason": f"未知工作流 {flow_id}"}
    stage = next((s for s in f.阶段 if s.id == stage_id), None)
    if stage is None:
        return {"ok": False, "reason": f"{flow_id} 没有这一段：{stage_id}"}
    g = research_gate(flow_id)
    if stage.段 != "调研" and not g.get("allowed"):
        return {"ok": False, "reason": "调研闸门未开，按纪律不允许推进生产/验收段",
                "闸门": {"allowed": False, "reason": g.get("reason")},
                "怎么开": g.get("怎么开", "")}
    try:
        from core import deploy_ledger as DL
        DL.record("deploy", f"workflow:{flow_id}/{stage_id}",
                  detail=f"推进 {stage.名称}（{stage.段}）by {by}",
                  before="未推进", after="已推进", ok=True, reason=str(note or ""))
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": True, "工作流": f.名称, "段": stage.名称, "段状态": stage.段,
            "负责": list(stage.负责), "触手": stage.触手,
            "验收": list(stage.验收), "推进人": by,
            "提示": "推进已落账；这一段是否达标按验收项逐条采证"}


def deploy_rows() -> list:
    rows = []
    for f in WORKFLOWS:
        for s in f.阶段:
            rows.append({"工作流": f.名称, "工作流ID": f.id, "段ID": s.id, "段": s.名称,
                         "段类": s.段, "负责": "、".join(s.负责), "触手班": s.触手,
                         "门禁": s.门禁, "验收项数": len(s.验收),
                         "验收标准": "；".join(s.验收), "证据来源": s.证据})
    return rows


def register(*, note: str = "") -> dict:
    from core import solidify as S
    payload = {"catalog": catalog(), "squads": squads(), "节奏": status()["纪律"]}
    r = S.solidify("workflow_registry", payload, note=note or "工作流注册表（调研前置 + 验收标准）")
    try:
        from core import deploy_ledger as DL
        DL.record("solidify", "workflow_registry",
                  detail=f"工作流 {len(WORKFLOWS)} 条 / 段 {sum(len(f.阶段) for f in WORKFLOWS)} 段",
                  before="", after=str(r.get("rev") or r.get("version")), ok=bool(r.get("ok", True)))
    except Exception as e:
        _swallow(__file__, e)
    return r


def scan(*, scope: str = "workflows×pipelines×research") -> dict:
    try:
        from core import deploy_ledger as DL
        return DL.scan_and_record({"workflows": list(catalog()), "rows": deploy_rows()},
                                  scope=scope)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


__all__ = ["Stage", "Flow", "WORKFLOWS", "SQUADS", "catalog", "flow", "squads",
           "research_gate", "acceptance", "status", "graph", "advance",
           "deploy_rows", "register", "scan"]
