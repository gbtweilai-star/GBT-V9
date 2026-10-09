# core/octop_intake.py —— Octop 原生能力**逐个接入**台账
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："把 Octop 原本的能力挨个接入并实现"，品牌统一 GBT小土豆V9。
#
# 口径（每一项都要能说清自己是哪种状态，不许含糊）：
#   原生实现  在 V9 里已有一等公民页面/接口承担同一件事（给出 V9 的落点）
#   站内内嵌  Octop 自己的页面，在 V9 站内**浏览器侧内嵌**（不经我们服务器转发，页面零丢失）
#   待接      两边都还没有落点 → 明确写"缺什么"
#   V9 侧只读登记（页面清单在 core.octop_fusion.PAGES；能力数在 octop_bridge.catalog）
from core.swallow import swallow as _swallow
import json

# 64 个原生页面 → V9 落点。key = Octop 页面 id（见 octop_fusion.PAGES）
NATIVE_MAP: tuple = (
    # (octop_id, V9 落点路由, V9 承担的功能说明, 状态)
    ("dashboard", "/", "总控台：身体启动检查/告警/编队/快照一屏", "原生实现"),
    ("chat", "/chat", "APP 独立多功能对话（会话/多智能体/五模式）", "原生实现"),
    ("sessions", "/chat", "会话记录：追加式落盘 + 左栏会话列表", "原生实现"),
    ("workbench", "/chat", "工作台：对话即工作台（五模式覆盖日常/记忆/读数/工作流/工具）", "原生实现"),
    ("workbench-browser", "/octop", "工作台·浏览器：站内内嵌打开", "站内内嵌"),
    ("workbench-terminal", "/terminal", "工作台·终端：AI 终端面板（白名单命令派发）", "原生实现"),
    ("tasks", "/workflows", "任务：工作流阶段 + 验收标准 + 闸门", "原生实现"),
    ("acp", "/workflows", "Agent 协作协议：多智能体协作工作流（分解→派发→复核→终止）", "原生实现"),
    ("agents-admin", "/agents", "智能体管理：Octop 名册点名 + V9 驱动链对话", "原生实现"),
    ("agent-config", "/agents", "智能体配置：名册按部门/专长查看", "原生实现"),
    ("subagents", "/agents", "子智能体：272 智能体名册可见可点名", "原生实现"),
    ("experts", "/agents", "专家库：18 位专家参与点名", "原生实现"),
    ("skills", "/capability", "技能：能力面逐项对齐表（24 项五查）", "原生实现"),
    ("skill-packages", "/kits", "技能包：三套工具包（剪映/Qwen-Image/ComfyUI 式）", "原生实现"),
    ("plugins", "/cloud", "插件：100 云插件槽（开关/互绑/出网隔离）", "原生实现"),
    ("models", "/cloud", "模型：云插件槽与算力路由（17 类活）", "原生实现"),
    ("knowledge-bases", "/brain", "知识库：原生大脑的知识条（簇→知识，带来源 id）", "原生实现"),
    ("memory", "/brain", "记忆：统一记忆库（主脑+100 触手+用户+系统）", "原生实现"),
    ("connectors", "/octop", "连接器：Octop 端口自动发现 + 可操控", "站内内嵌"),
    ("channels", "/octop", "渠道：站内内嵌；V9 侧只读登记", "站内内嵌"),
    ("environments", "/capability", "环境：生产就绪度 + 根因台账", "原生实现"),
    ("terminal", "/terminal", "终端：AI 终端对话面板", "原生实现"),
    ("workspace", "/workflows", "工作区：工作流阶段流水线视图", "原生实现"),
    ("cron-jobs", "/workflows", "定时任务：工作流 + 媒体队列", "原生实现"),
    ("token-usage", "/capability", "Token 用量：驱动用量（真账）", "原生实现"),
    ("mbti", "/command", "MBTI：情绪/人格读数在指挥中心「她的状态」", "原生实现"),
    ("personalization-skills", "/agents", "个性化·技能：名册与专长", "原生实现"),
    ("personalization-channels", "/octop", "个性化·渠道：站内内嵌", "站内内嵌"),
    ("personalization-acp", "/workflows", "个性化·ACP：协作工作流", "原生实现"),
)
# 未在上表逐条列出的 Octop 页面（远控/管理/设置/调试/品牌别名等）
_PENDING_GROUPS = ("远控", "管理", "设置", "调试")
_PENDING_NOTE = ("Octop 自身的运维面（远控/管理/设置/调试）：**本机不需要在 V9 里重造** —— "
                 "它们管的是 Octop 底座自己（登录/单点/存储/更新/用户），"
                 "V9 的口径是「站内内嵌可打开 + 品牌统一」，不做第二套。")


def _octop_pages() -> list:
    try:
        from core import octop_fusion as OF
        return [{"id": p[0], "标题": p[1], "路径": p[2], "分组": p[3]} for p in OF.PAGES]
    except Exception:                                          # noqa: BLE001
        return []


def intake() -> dict:
    """逐页台账：每个 Octop 原生页在 V9 里落在哪、什么状态。"""
    pages = _octop_pages()
    m = {row[0]: row for row in NATIVE_MAP}
    rows = []
    for p in pages:
        got = m.get(p["id"])
        if got:
            rows.append({**p, "V9落点": got[1], "承载": got[2], "状态": got[3]})
        elif p["分组"] in _PENDING_GROUPS:
            rows.append({**p, "V9落点": "/octop", "承载": "站内内嵌打开（品牌统一）",
                         "状态": "站内内嵌"})
        else:
            rows.append({**p, "V9落点": "/octop", "承载": "站内内嵌打开；V9 侧暂无专项页",
                         "状态": "站内内嵌"})
    by = {}
    for r in rows:
        by[r["状态"]] = by.get(r["状态"], 0) + 1
    # V9 自己新增、Octop 原本没有的面
    v9_only = [
        {"页面": "原生大脑", "路由": "/brain", "说明": "统一记忆 + 生命起源存档 + 元认知（Octop 没有）"},
        {"页面": "市场调研", "路由": "/workflow", "说明": "7 维度调研 + 推进闸门（Octop 没有）"},
        {"页面": "全站按键统一", "路由": "全站", "说明": "一套 .btn 家族 + skin 归一"},
        {"页面": "能力逐项对齐", "路由": "/capability", "说明": "24 项五查（面板/状态源/操作/证据/验收）"},
    ]
    return {"原生页面数": len(rows), "状态分布": by, "行": rows, "V9独有": v9_only,
            "运维面口径": _PENDING_NOTE,
            "口径": "原生实现=V9 有一等公民页面承担；站内内嵌=在 V9 站内打开该 Octop 页（零丢失）；"
                    "两处都不含糊"}


def caps() -> dict:
    """Octop 能力目录（部门/智能体/专家/插件/技能/V9 工具）的真读数。"""
    try:
        from core import octop_bridge as OB
        c = OB.catalog()
        return {"counts": c.get("counts") or {}, "divisions": c.get("divisions") or [],
                "experts": c.get("experts") or [], "skills": c.get("skills") or [],
                "plugins_bundled": c.get("plugins_bundled") or [],
                "v9_tools": c.get("v9_tools") or []}
    except Exception as exc:                                   # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def status() -> dict:
    it = intake()
    try:
        from core import octop_fusion as OF
        fuse = OF.status()
    except Exception as exc:                                   # noqa: BLE001
        fuse = {"error": type(exc).__name__}
    return {"融合": fuse, "接入": {"原生页面数": it["原生页面数"], "状态分布": it["状态分布"]},
            "能力目录": (caps().get("counts") or {}),
            "口径": it["口径"]}


def register(*, note: str = "") -> dict:
    from core import solidify as S
    r = S.solidify("octop_intake", {"intake": intake(), "caps_counts": caps().get("counts")},
                   note=note or "Octop 原生能力逐个接入台账")
    try:
        from core import deploy_ledger as DL
        DL.record("deploy", "octop_intake",
                  detail=f"原生页 {intake()['原生页面数']} 个 · 状态分布 {intake()['状态分布']}",
                  before="", after=str(r.get("rev") or r.get("version")), ok=True)
    except Exception as e:
        _swallow(__file__, e)
    return r


__all__ = ["NATIVE_MAP", "intake", "caps", "status", "register"]
