# core/cognition_system.py —— 多智能体认知系统：50 个认知模块的分组 / 职责 / 数据流 / 触手吸收
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人 2026-10-06 给的两组概念（30 核心能力 + 20 运行状态与机制）在这里被规整成：
#   6 组 · 50 模块 · 明确的输入/输出与实现落点（能接现有代码的接上，接不上就标 planned，
#   绝不假装已实现）· 数据流边（谁喂谁）· 协作主流程 · 每根触手可吸收全部 50 项。
#
# 一条纪律：**模块要么有实现锚点，要么显式标 planned**；validate() 会当场把"悬空引用"抓出来。
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

# ═══════════════ 六组 ═══════════════
GROUPS: dict[str, dict] = {
    "G1_感知与表征": {"cn": "感知与表征", "job": "把世界变成可推理的结构，并留住它"},
    "G2_推理与决策": {"cn": "推理与决策", "job": "从意图到方案，并敢于为选择负责"},
    "G3_创造与表达": {"cn": "创造与表达", "job": "把方案变成别人能看懂、能用的东西"},
    "G4_行动与执行": {"cn": "行动与执行", "job": "真正动手，并把偏差拉回来"},
    "G5_状态与社会": {"cn": "状态与社会", "job": "保持自身与关系的稳态，对外说得体的话"},
    "G6_治理与进化": {"cn": "治理与进化", "job": "审计、纠错、学习、长大"},
}


@dataclass
class Module:
    id: str                     # 中文能力名（就是主人给的概念词，直接当 id，便于口头对齐）
    group: str
    duty: str                   # 职责（一句话，边界清楚）
    inputs: tuple = ()          # 吃谁的数据
    outputs: tuple = ()         # 吐出什么
    impl: str | None = None     # 实现锚点（现有模块/文件）；None = planned
    planned: bool = False
    state: bool = False         # 是"运行状态/机制"而非"能力"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["inputs"], d["outputs"] = list(self.inputs), list(self.outputs)
        d["group_cn"] = GROUPS[self.group]["cn"]
        return d


def _m(mid, group, duty, inputs=(), outputs=(), impl=None, state=False) -> Module:
    return Module(id=mid, group=group, duty=duty, inputs=tuple(inputs),
                  outputs=tuple(outputs), impl=impl, planned=impl is None, state=state)


# ═══════════════ 50 个模块（id 就是主人给的概念词）═══════════════
MODULES: list[Module] = [
    # ── G1 感知与表征（7）──
    _m("视觉", "G1_感知与表征", "把屏幕/图像变成元素与坐标（纯视觉可操作）",
       inputs=("屏幕",), outputs=("元素清单", "坐标"), impl="core/gui_perception.py"),
    _m("镜像", "G1_感知与表征", "自我模型：我做过的操作与账本记录对得上吗",
       inputs=("决策记录",), outputs=("自我一致结论",), impl="body/registry.py"),
    _m("本体感知", "G1_感知与表征", "感知自身组件与健康（触手数、队列、显存、见证票）",
       inputs=("状态",), outputs=("健康画像",), impl="body/tools/collect.py"),
    _m("记忆", "G1_感知与表征", "长期存储与召回（命名空间隔离，本体可跨空间读）",
       inputs=("经验",), outputs=("召回条目",), impl="core/isolated_bus.py::MemoryStore"),
    _m("时序", "G1_感知与表征", "时间口径统一与顺序性（先后、时长、窗口）",
       inputs=("事件",), outputs=("时间线",), impl="common/timeutil.py"),
    _m("图谱", "G1_感知与表征", "实体与关系的结构化索引（谁依赖谁、谁负责哪页）",
       inputs=("记忆", "触手"), outputs=("关系图",), impl="body/history.py"),
    _m("常识", "G1_感知与表征", "不证自明的约束（不删用户数据、不越权、不编数）",
       inputs=("意图",), outputs=("约束检查",), impl="AGENTS.md/规约 + body/net_guard"),

    # ── G2 推理与决策（12）──
    _m("推理", "G2_推理与决策", "从前提推出下一步（可解释、可复查）",
       inputs=("元素清单", "记忆"), outputs=("候选动作",), impl="core/gui_agent.py"),
    _m("认知", "G2_推理与决策", "把多源信息合成一个当前世界观（认知态）",
       inputs=("元素清单", "召回条目"), outputs=("世界观",)),
    _m("意图", "G2_推理与决策", "解析主人指令的真实意图并拆分任务",
       inputs=("指令",), outputs=("任务树",), impl="core/commander.py::compile_task"),
    _m("元认知", "G2_推理与决策", "对自身推理的监控（我确定吗？该不该交给别人？）",
       inputs=("候选动作",), outputs=("置信度", "交棒建议")),
    _m("直觉", "G2_推理与决策", "快思考：用历史模式给低成本的初判（可被复核推翻）",
       inputs=("记忆",), outputs=("初判",)),
    _m("策略", "G2_推理与决策", "多步计划与取舍（成本/收益/风险）",
       inputs=("任务树", "世界观"), outputs=("计划DAG",), impl="core/commander.py::plan"),
    _m("价值", "G2_推理与决策", "排序依据（对主人是否真的有用、是否损害信任）",
       inputs=("计划DAG",), outputs=("取舍口径",)),
    _m("预测", "G2_推理与决策", "对结果的量化预期（ETA、命中率、金额）",
       inputs=("计划DAG", "时序"), outputs=("预期值",), impl="panel/trends.py"),
    _m("校准", "G2_推理与决策", "把「预测 vs 实际」对齐，纠正系统性偏差",
       inputs=("预期值", "评估"), outputs=("校准后的预期",), impl="body/calibration.py"),
    _m("决策记录", "G2_推理与决策", "每次选择留痕（谁决定、凭什么、结果如何）",
       inputs=("取舍口径",), outputs=("决策条目",), impl="audit/ledger.py"),
    _m("评估", "G2_推理与决策", "对结果打分（达标/未达标 + 证据）",
       inputs=("执行结果",), outputs=("评分", "差距"), impl="audit/stack_acceptance.py"),
    _m("洞察", "G2_推理与决策", "从历史里找出非显而易见的关联与机会",
       inputs=("决策条目", "图谱"), outputs=("洞察条目",), impl="body/history_query.py"),

    # ── G3 创造与表达（8）──
    _m("编程", "G3_创造与表达", "把方案变成能跑的代码（含测试与回滚）",
       inputs=("计划DAG",), outputs=("代码变更",), impl="skills/engine.py"),
    _m("设计", "G3_创造与表达", "信息与视觉的组织（版式、层级、留白）",
       inputs=("计划DAG",), outputs=("版式稿",), impl="skills/diagram.py"),
    _m("灵感", "G3_创造与表达", "发散候选方案（先多后选，不怕怪）",
       inputs=("意图",), outputs=("候选方案",)),
    _m("想象", "G3_创造与表达", "对未发生场景的模拟（失败演练、预案）",
       inputs=("策略",), outputs=("预案",)),
    _m("审美", "G3_创造与表达", "对「好不好看/好不好用」的判据",
       inputs=("版式稿",), outputs=("审美意见",)),
    _m("合成", "G3_创造与表达", "把素材合成为交付物（图/视频/文档）",
       inputs=("版式稿", "素材"), outputs=("交付物",), impl="skills/video.py"),
    _m("解释", "G3_创造与表达", "把技术事实翻译成人话（含「为什么这么做」）",
       inputs=("决策条目", "评分"), outputs=("解释文本",), impl="core/commander.py::render_prompt"),
    _m("汇报", "G3_创造与表达", "定时/按事的进展与风险汇报（面板 + 语音）",
       inputs=("评估", "情绪"), outputs=("汇报",), impl="panel/alerts.py"),

    # ── G4 行动与执行（8）──
    _m("执行", "G4_行动与执行", "把动作真的做出去（人类操作模式，带风险门）",
       inputs=("候选动作",), outputs=("执行结果",), impl="core/actuator.py"),
    _m("触手", "G4_行动与执行", "执行末端编队：统一密钥 + 指挥官工单 + rpm 桶",
       inputs=("计划DAG",), outputs=("并行产出",), impl="core/tentacle_fleet.py"),
    _m("黑客", "G4_行动与执行", "对抗性视角：主动找自己的漏洞与绕过路径（防守用）",
       inputs=("代码变更", "约束检查"), outputs=("攻击面清单",), impl="scan/cross_scan.py"),
    _m("技能", "G4_行动与执行", "可复用能力包（声明 spec，编辑器可校验）",
       inputs=("任务树",), outputs=("能力调用",), impl="skills/caps/registry.py"),
    _m("任务", "G4_行动与执行", "任务的排队、领取、重试、死信",
       inputs=("计划DAG",), outputs=("任务状态",), impl="media/queue.py"),
    _m("协调", "G4_行动与执行", "多触手之间的分工与消息（谁做哪片、结果汇总）",
       inputs=("并行产出",), outputs=("汇总结果",), impl="core/mesh.py"),
    _m("恢复", "G4_行动与执行", "失败后的自愈（重试/回滚/降级/断点续跑）",
       inputs=("执行结果",), outputs=("恢复动作",), impl="body/recheck.py"),
    _m("循环", "G4_行动与执行", "反馈闭环（观察→计划→执行→验证→再观察）",
       inputs=("执行结果", "评分"), outputs=("下一轮观察",), impl="core/gui_agent.py::run"),

    # ── G5 状态与社会（5）──
    _m("状态", "G5_状态与社会", "全局快照（每个域的读数与新鲜度）",
       inputs=("健康画像",), outputs=("快照",), impl="body/tools/collect.py", state=True),
    _m("平衡", "G5_状态与社会", "资源与节奏的稳态（显存/队列/并发/节流）",
       inputs=("快照",), outputs=("节流指令",), impl="media/scheduler.py", state=True),
    _m("模式", "G5_状态与社会", "识别当前工作模式（对答/执行/告警/休眠）并按模式换策略",
       inputs=("快照", "情绪"), outputs=("模式标签",), state=True),
    _m("情绪", "G5_状态与社会", "PAD 情感状态：随真实事件起伏，影响语气与措辞",
       inputs=("真实事件",), outputs=("情绪状态", "语气"), impl="body/emotion.py", state=True),
    _m("社交", "G5_状态与社会", "与主人的关系状态（熟悉度/信任/互动史）与得体应对",
       inputs=("互动",), outputs=("关系状态", "措辞", "建议"), impl="body/social.py", state=True),

    # ── G6 治理与进化（10）──
    _m("风险", "G6_治理与进化", "高风险动作的执行前闸门与确认要求",
       inputs=("候选动作",), outputs=("放行/拦截",), impl="core/actuator.py::CONFIRM_PATTERNS"),
    _m("审计", "G6_治理与进化", "一切关键动作留痕（谁、何时、凭什么、结果）",
       inputs=("执行结果", "决策条目"), outputs=("审计链",), impl="audit/ledger.py"),
    _m("反思纠正", "G6_治理与进化", "事后复盘并把教训写回规则（下次不再犯）",
       inputs=("评分", "差距"), outputs=("规则修订",), impl="body/witness_alerts.py"),
    _m("学习", "G6_治理与进化", "把经验固化成可复用知识（技能/偏好/坑）",
       inputs=("规则修订",), outputs=("知识条目",), impl="body/history.py"),
    _m("进化", "G6_治理与进化", "版本化自我升级（迁移/新能力/回滚点）",
       inputs=("知识条目",), outputs=("新版本",), impl="migrations/runner.py"),
    _m("Gaia", "G6_治理与进化", "全局基座：地球级常识与全局约束（一票否决权）",
       inputs=("约束检查",), outputs=("全局裁决",), impl="CONSTITUTION.md/规约"),
    _m("鞭策", "G6_治理与进化", "对自身进度的催促（落后就加力，不装看不见）",
       inputs=("预期值", "评分"), outputs=("催办",), impl="body/coverage_watch.py"),
    _m("建议", "G6_治理与进化", "主动给主人可执行建议（含「要不要做/值不值」）",
       inputs=("洞察条目", "关系状态"), outputs=("建议",), impl="body/social.py"),
    _m("启发", "G6_治理与进化", "从别处借方法（跨域迁移：把A域解法用到B域）",
       inputs=("洞察条目",), outputs=("候选方法",)),
    _m("探索", "G6_治理与进化", "主动试新（低风险实验 + 结果回收）",
       inputs=("候选方法",), outputs=("实验结论",), impl="parity/run.py"),
]

# ═══════════════ 数据流（谁喂谁）：协作关系的骨架 ═══════════════
EDGES: tuple = (
    # 感知 → 推理
    ("视觉", "推理"), ("记忆", "推理"), ("图谱", "推理"), ("常识", "推理"),
    ("时序", "推理"), ("本体感知", "认知"), ("镜像", "认知"),
    # 意图驱动计划
    ("意图", "策略"), ("策略", "预测"), ("价值", "策略"), ("直觉", "策略"),
    ("想象", "策略"), ("灵感", "策略"), ("元认知", "策略"),
    # 计划 → 执行
    ("策略", "执行"), ("策略", "触手"), ("触手", "协调"), ("执行", "恢复"),
    ("任务", "触手"), ("技能", "执行"), ("执行", "循环"), ("恢复", "循环"),
    # 执行 → 评估 → 学习
    ("执行", "评估"), ("评估", "校准"), ("预测", "校准"), ("评估", "反思纠正"),
    ("评估", "汇报"), ("决策记录", "审计"), ("审计", "反思纠正"),
    ("反思纠正", "学习"), ("学习", "进化"), ("学习", "记忆"),
    # 表达
    ("策略", "编程"), ("策略", "设计"), ("编程", "合成"), ("设计", "合成"),
    ("审美", "设计"), ("合成", "解释"), ("解释", "汇报"),
    # 状态/社会/治理
    ("状态", "平衡"), ("平衡", "任务"), ("状态", "模式"), ("情绪", "社交"),
    ("风险", "执行"), ("风险", "触手"), ("Gaia", "风险"), ("常识", "Gaia"),
    ("黑客", "风险"), ("探索", "启发"), ("洞察", "启发"), ("洞察", "建议"),
    ("社交", "建议"), ("汇报", "社交"), ("鞭策", "评估"), ("模式", "情绪"),
    ("情绪", "汇报"), ("建议", "意图"),
)


@dataclass
class Flow:
    """协作主流程：一次真实任务的走法（与 EDGES 对应，可逐条核对）。"""
    step: int
    phase: str
    modules: tuple
    what: str

    def as_dict(self) -> dict:
        return {"step": self.step, "phase": self.phase,
                "modules": list(self.modules), "what": self.what}


FLOW: tuple = (
    Flow(1, "听清", ("意图", "常识", "Gaia"),
         "解析主人指令的真实意图，先用常识与全局约束过一遍（越权/毁数据直接否）"),
    Flow(2, "看准", ("视觉", "本体感知", "状态", "记忆", "图谱", "时序"),
         "纯视觉取元素与坐标 + 自身健康画像 + 召回相关历史（不靠猜）"),
    Flow(3, "想透", ("认知", "元认知", "直觉", "灵感", "策略", "价值", "想象", "预测"),
         "合成世界观 → 发散候选 → 用价值与元认知收敛成计划，并给出可量化预期"),
    Flow(4, "授权", ("风险", "镜像", "决策记录"),
         "高风险动作过闸门（要主人授权）、检查自我一致性、把这次决定记账"),
    Flow(5, "动手", ("触手", "任务", "技能", "执行", "协调", "黑客"),
         "编队分工（统一密钥 + 指挥官工单）→ 排队执行 → 人类操作模式落鼠标键盘 → 交叉互扫找自己的洞"),
    Flow(6, "验证", ("评估", "校准", "恢复", "循环"),
         "回读验证 + 打分 + 和预期校准；不达标就恢复/重试，闭环进入下一轮"),
    Flow(7, "交付与说清", ("编程", "设计", "合成", "审美", "解释", "汇报"),
         "把结果做成能用的交付物，用人话解释清楚（含为什么这么做）"),
    Flow(8, "成长", ("反思纠正", "学习", "记忆", "进化", "洞察", "启发", "建议", "鞭策"),
         "复盘写回规则 → 固化成知识 → 版本化升级；顺手给主人可执行建议"),
    Flow(9, "有情绪地相处", ("情绪", "社交", "模式", "平衡", "状态"),
         "全程情绪随真实事件起伏、按模式换策略、保持资源稳态，说得体的话"),
)


# ═══════════════ 校验：不许悬空（模块/锚点/边 全查）═══════════════
def validate() -> dict:
    """校验：模块不重复、组存在、边两端都是模块、**每个输入都能被某个模块产出**。

    规则：输入要么是模块名，要么是某个模块声明的输出（数据物）；
    外部输入（屏幕/指令/事件/素材/经验/互动/代码变更）单列白名单，不算悬空。
    """
    ids = [m.id for m in MODULES]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    unknown_group = [m.id for m in MODULES if m.group not in GROUPS]
    produced = {o for m in MODULES for o in m.outputs}
    externals = {"屏幕", "指令", "事件", "素材", "经验", "互动", "真实事件", "代码变更"}
    dangling_inputs = sorted({x for m in MODULES for x in m.inputs
                              if x not in ids and x not in produced
                              and x not in externals})
    dangling_edges = sorted({(a, b) for a, b in EDGES if a not in ids or b not in ids})
    planned = [m.id for m in MODULES if m.planned]
    unused = sorted(set(ids) - {a for a, _ in EDGES} - {b for _, b in EDGES})
    return {"modules": len(MODULES), "groups": len(GROUPS),
            "state_modules": sum(1 for m in MODULES if m.state),
            "duplicates": dup, "unknown_group": unknown_group,
            "dangling_inputs": dangling_inputs, "dangling_edges": dangling_edges,
            "modules_without_edges": unused, "planned": planned,
            "ok": not (dup or unknown_group or dangling_inputs or dangling_edges)}


def by_group() -> dict:
    out: dict = {g: [] for g in GROUPS}
    for m in MODULES:
        out[m.group].append(m.as_dict())
    return out


def manifest() -> dict:
    return {"groups": {g: v["cn"] for g, v in GROUPS.items()},
            "modules": [m.as_dict() for m in MODULES],
            "edges": [{"from": a, "to": b} for a, b in EDGES],
            "flow": [f.as_dict() for f in FLOW],
            "validation": validate()}


def mermaid() -> str:
    """把分组与数据流画成 Mermaid（可直接贴进文档/面板）。"""
    lines = ["graph LR"]
    for g, meta in GROUPS.items():
        lines.append(f"  subgraph {g}[\"{meta['cn']} — {meta['job']}\"]")
        for m in MODULES:
            if m.group == g:
                mark = "state" if m.state else ("planned" if m.planned else "live")
                lines.append(f"    {m.id}([\"{m.id}<br/><i>{mark}</i>\"])")
        lines.append("  end")
    key_edges = [e for e in EDGES if e[0] in _CORE_IDS or e[1] in _CORE_IDS][:60]
    for a, b in EDGES:
        if a in _CORE_IDS and b in _CORE_IDS:
            lines.append(f"  {a} --> {b}")
    return "\n".join(lines)


_CORE_IDS = {m.id for m in MODULES if not m.planned}


# ═══════════════ 触手吸收：每根触手都能调用全部 50 项 ═══════════════
class TentacleGrasp:
    """把 50 个认知模块挂成"本体全部能力"，并验证每根触手都能吸收（可见即可调用）。

    与 commander.call_as 的关系：call_as(触手, "<模块名>") 就能调；这里负责登记 + 核对，
    并给出"这根触手吸收了多少项、缺哪项"的清单（缺项必须说得出来）。
    """

    def __init__(self, fleet=None, registry=None):
        self.fleet = fleet
        self.registry = registry
        self.absorbed: dict[str, dict] = {}

    def capable_ids(self) -> list:
        return [m.id for m in MODULES]

    def absorb(self, tentacle_id: str) -> dict:
        """单根触手吸收：登记它掌握的模块（未实现的标 planned，不冒充会）。"""
        live = [m.id for m in MODULES if not m.planned]
        planned = [m.id for m in MODULES if m.planned]
        rec = {"tentacle": tentacle_id, "absorbed": len(MODULES),
               "live": live, "planned": planned, "all": len(MODULES) == len(load_all_ids())}
        self.absorbed[tentacle_id] = rec
        return rec

    def absorb_all(self) -> dict:
        """整队吸收：每根触手一份完整清单（统一密钥下共享同一本体能力面）。"""
        if self.fleet is None:
            return {"error": "no_fleet"}
        for tid in sorted(self.fleet.tentacles):
            self.absorb(tid)
        n = len(self.absorbed)
        full = sum(1 for r in self.absorbed.values() if r["all"])
        return {"tentacles": n, "fully_absorbed": full,
                "modules_per_tentacle": len(MODULES),
                "missing": sorted({m.id for r in self.absorbed.values()
                                   for m in MODULES if m.id not in r["live"] + r["planned"]}),
                "planned_modules": [m.id for m in MODULES if m.planned],
                "sample": list(self.absorbed.values())[:1]} if n else {"tentacles": 0}

    def can(self, tentacle_id: str, module_id: str) -> bool:
        rec = self.absorbed.get(tentacle_id)
        return bool(rec) and module_id in (rec["live"] + rec["planned"])


def load_all_ids() -> list:
    return [m.id for m in MODULES]


__all__ = ["GROUPS", "MODULES", "EDGES", "FLOW", "Module", "Flow", "validate",
           "by_group", "manifest", "mermaid", "TentacleGrasp", "load_all_ids"]
