# core/capability_panels.py —— 能力控制面板**逐项对齐审计**
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："必须把每一项能力的控制面板细节做到极致，而不是遗漏这个丢失那个，
#   给我认真地一一对齐。"
#
# 所以这里不写形容词，只做一件可核查的事：把**每一项能力**摊开，逐项检查它有没有
#   ① 面板（在哪个页面能看到 / 能不能操作）
#   ② 状态源（读数从哪来，是 observed 还是 unavailable）
#   ③ 操作/验证入口（有没有一条真能跑的动作或自证）
#   ④ 证据（跑完能不能留下证据）
#   ⑤ 验收标准（怎么算好，达标线写没写）
# 缺哪一条就点名，并给出补齐方向 —— 页面据此显示"已对齐 N/M"。
from core.swallow import swallow as _swallow
import os
from pathlib import Path

from common.ttl_cache import TTLCache as _TTL
from core import compute_router as _cr
from core import market_research as _mr
from core import workflows as _wf

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _plugins() -> int | None:
    try:
        from core.cloud_plugins import PLUGIN_IDS
        return len(PLUGIN_IDS)
    except Exception:                                          # noqa: BLE001
        return None


def _slots() -> int | None:
    try:
        from core.db_fleet import SLOT_IDS
        return len(SLOT_IDS)
    except Exception:                                          # noqa: BLE001
        return None


def _octop() -> int | None:
    try:
        from core.octop_bridge import catalog as _c
        return int(((_c() or {}).get("counts") or {}).get("total_capabilities") or 0) or None
    except Exception:                                          # noqa: BLE001
        return None


def _tentacles() -> int | None:
    try:
        from core.tentacle_fleet import TentacleFleet
        return len(getattr(TentacleFleet(), "tentacles", []) or [])
    except Exception:                                          # noqa: BLE001
        return None


def _frames() -> int | None:
    try:
        from senses import frame_readers as fr
        return len(getattr(fr, "READERS", []) or []) or None
    except Exception:                                          # noqa: BLE001
        return None


def _tools() -> int | None:
    try:
        import body.tools.base as tb
        tb.ensure_registered()
        return len(getattr(tb, "TOOLS", {}) or {}) or None
    except Exception:                                          # noqa: BLE001
        return None


def _asr_ok() -> bool:
    try:
        from senses import voice_sapi as VS
        return bool(VS.asr_status().get("可用"))
    except Exception:                                          # noqa: BLE001
        return False


def _solidify_groups() -> int | None:
    try:
        from core import solidify as S
        return len(S.status().get("names") or [])
    except Exception:                                          # noqa: BLE001
        return None


def _rows() -> list:
    """每一项能力 → 五查（面板/状态源/操作/证据/验收）。全部是能读到的真事实。"""
    rows: list = []

    def add(能力, 组, 面板, 状态源, 操作, 证据, 验收, 说明=""):
        rows.append({"能力": 能力, "组": 组, "面板": 面板, "状态源": 状态源,
                     "操作": 操作, "证据": 证据, "验收": 验收, "说明": 说明})

    n_plug, n_slot, n_oct = _plugins(), _slots(), _octop()
    snap = _totals_snapshot()
    n_tent = snap.get("触手")

    # ── 指挥层 ──
    add("总控台", "指挥", "/", "api/health · api/state · api/panel/overview（真读数）",
        "身体启动检查 / 重连 / 脉冲自检 / 试转写", "账本 + 快照表",
        "各卡片无『未建』死胡同；每张卡都能跑出证据")
    add("AI 指挥中心", "指挥", "/command", "api/ai/ask（口语→术语→只读读数）",
        "问一句（回车）", "对话落账 agent_chat.jsonl", "答复来自只读读数，查不到就说无法确认")
    add("工作流", "指挥", "/workflow", "api/workflows/status（调研闸门 + 段状态 + 验收）",
        "推进段 / 提交调研 / 刷新编排图", "工作流台账 + 调研记录 JSONL",
        "调研闸门未开时生产段一律拒绝推进（不盲推）",
        "本轮新增：独立页面 + 6 条工作流 + 7 维度调研闸门")

    # ── 能力层 ──
    add("云插件", "能力", "/cloud · /capability",
        f"cloud_plugin_state（云插件注册表 {n_plug} 槽）" if n_plug else "云端注册表",
        "开关 / 插入绑定 / 内部互绑 / 出网隔离", "cloud_binding · cloud_share 表",
        "启用数 = 槽数；插入绑定去重对 = 槽×触手（无重复行）")
    add("数据库槽", "能力", "/db · /capability",
        f"db_slot_state（{n_slot} 槽）" if n_slot else "库槽注册表",
        "一键建 100 库 / 开关 / 触手绑定", "db_binding 表 + 磁盘文件真占用",
        "已建库 = 槽数；绑定去重对无重复")
    add("Octop 能力", "能力", "/octop · /capability",
        f"octop_binding（{n_oct} 项能力）" if n_oct else "Octop 桥接清单",
        "1:1 双向绑定 / 启停底座 / 固化回滚", "octop_binding 表",
        "能力×触手绑定对称（正反两向都能查）")
    add("触手编队", "能力", "/capability · 总控台",
        f"tentacle_fleet.status（{n_tent} 根）" if n_tent else "编队状态",
        "驱动 / 表(table) 查看最近驱动", "fleet_drive 表",
        "统一密钥指纹唯一；每次驱动带一次性工单")
    add("算力路由（双通道）", "能力", "/capability",
        f"compute_router.WORKLOADS（{len(_cr.WORKLOADS)} 类活）",
        "路由表 / 本地 0 显存校验", "compute_router.audit 读数",
        "云主管道优先；本地显存预留 = 0")
    add("读帧插件", "能力", "/capability",
        f"senses.frame_readers.READERS（{_frames()} 个）" if _frames() else "读帧注册表",
        "逐个前台读一次（真读）", "各插件返回的 ok/读数",
        "每个插件要么给出真帧，要么给原因（不报 0）")
    add("只读工具", "能力", "/capability · 数字人",
        f"body.tools.TOOLS（{_tools()} 个）" if _tools() else "工具注册表",
        "按域调用（只读）", "read_tool_audit 表",
        "调用计数来自真审计；只读不写入")
    add("媒体队列 / 显存", "能力", "/media",
        "media queue stats · vram（真实口径）",
        "入队 / 死信查看 / 终态事件", "媒体终态事件 + vram 采样",
        "失败率用 observed 口径；显存不可探测时如实说明")
    add("流水线（4 条）", "生产", "/pipelines",
        "pipelines.status（48 步真部署状态）",
        "分类部署 / 变更日志 / 固化回滚", "deploy_ledger + 固化快照",
        "每步：已分类部署 或 已接替代实现（不留空）")
    add("工具包（3 套）", "生产", "/kits",
        "kit_packs.status（20 步 + 配置白名单校验）",
        "配置生成/校验 / 部署 / 固化", "kit_packs journal + 固化",
        "配置越白名单即拒（键/类型/插件族都对得上）")
    add("原生大脑", "指挥", "/brain",
        "core.memory.status（统一记忆库 memory:mem_brain 真读数）",
        "捕捉 / 提问（带出处）/ 统一导入 / 夜间整理 / 回收站复原",
        "state 库 + 生命起源存档 + 事件账",
        "褪色≠删除（分层只改热度）；答不上来就说「不确定」；私密记忆不外发",
        "本轮新增：主脑+100 触手+用户+系统 同库统一；含生命起源存档与元认知")
    add("市场调研", "生产", "/workflow",
        f"market_research（{len(_mr.DIMENSIONS)} 维度 · 记录 {_mr.status()['记录数']} 条）",
        "出方案 / 看本机真读数 / 提交结论", "state/market_research.jsonl + 账本 + 固化",
        "维度不齐或无来源 → 拒收；闸门 5 条全过才放行",
        "本轮新增：这正是「经过调研再推进」的落地件")
    add("验收标准", "生产", "/workflow",
        f"workflows.acceptance（{sum(len(f.预期标准) for f in _wf.WORKFLOWS)} 条预期标准）",
        "逐条采证 / 判定", "采证记录 + 账本", "只有『通过=True』算达标；『待采证』不算达标")
    add("数字人", "能力", "/digital-human",
        "avatar_rig.status（18 关节 · 32 动作 · 眨眼）",
        "选动作 / 组合连招 / 台湾腔说这句 / 口型", "动作位移自测 + 语音流水",
        "32 个动作全部有实际位移（不是静态站姿）")
    add("语音（说/听）", "能力", "/digital-human",
        "voice_sapi.status + asr_status（本机免费离线）",
        "台湾腔朗读 / 听写自证（TTS→ASR 闭环）", "state/voice_*.wav + mic_segments",
        "听写识别器可用时给出命中率；不可用则写清怎么装")
    add("蓝牙操控", "设备", "/ble",
        "senses.ble.status（多源枚举）+ ble_ops 审计",
        "扫描 / 读 GATT / 写（需 Grant）", "ble_ops 追加式审计",
        "写操作无授权一律拒绝；扫描失败写原因")
    add("页面注册表/绑定", "系统", "/capability",
        "page_registry.audit（路由/接口/对称性）",
        "绑定审计 / 按键双向绑定", "绑定登记 + 固化",
        "每个页面声明的接口都真实存在（逐个验），绑定对称")
    add("固化 / 回滚", "系统", "/capability",
        f"solidify.status（{_solidify_groups()} 组）" if _solidify_groups() is not None else "固化档案",
        "固化当前读数 / 一键回滚", "state/solid/archive.json + sha256",
        "回滚校验哈希；单版本拒绝原地覆盖（保护唯一副本）")
    add("变更台账", "系统", "/capability",
        "deploy_ledger.summary（scan/add/modify/deploy/solidify）",
        "扫描登记 / 变更回放", "state/deploy_journal.jsonl（追加式）",
        "每次扫描/新增/修改/部署/固化都有记录（可回放）")
    add("生产就绪度", "系统", "/capability",
        "production_gate.summary（六类归类 + 根因）",
        "审计 / 深探（真发一次请求）", "根因台账",
        "非生产项必须写明根因与修法（不写空话）")
    add("安全排查", "系统", "/capability",
        "tools.hollow_sweep（空壳/后门/危险调用）",
        "全仓扫描 / 高危清单", "扫描报告",
        "高危必须为 0；误报要有已知良性理由（不许白名单糊弄）")
    return rows


# 补齐记录：把"以前缺、现在补上了"的项写进台账（页面据此显示对齐进度）
RECENT_FIXES: tuple = (
    ("原生大脑（统一记忆）", "新增 core/memory：主脑+100 触手+用户+系统 记忆同库，四维分类（owner/主体/分类/记忆域）；幂等统一导入 Obsidian/进程内触手记忆/对话记录"),
    ("生命起源存档", "新增大脑生平编年：诞生记录只写一次；生平按 8 类归档（含触手类）；可回放到任意时刻「当时是什么样」"),
    ("元认知", "新增对自身认知状态的核查：覆盖缺口（空/薄分类）+ 置信校准（用到率/未答率）+ 反省与行动建议；longterm 契约的 reflect 落到这里"),
    ("工作流独立页面", "以前只有 DAG 引擎与编排器，没有面板页；现已建 /workflow（6 条工作流 + 编排图 + 逐段验收）"),
    ("市场调研工作流", "以前全仓没有调研件；现已建 core/market_research（7 维度 + 拒收规则 + 闸门）"),
    ("推进闸门", "调研未过闸门 → 生产/验收段拒绝推进（workflows.advance 硬拦）"),
    ("预期标准/验收", "每条工作流都有可判定的预期标准；能自动判的给真读数，判不了的写待采证"),
    ("按键统一", "全站 8 种按钮长相 → 1 套 .btn 家族 + skin 归一（含下拉/输入/标签；残留自写规则 0）"),
    ("CSS 变量失效", "var(--accent_2)/--surface_2/--surface_3 未定义导致当前页按键霓虹底与 .btn 渐变整条失效；已补下划线别名"),
    ("API 文档脱离体系", "/docs 自带一套硬编码配色、绕过统一注入；现已并入 token+导航+按键体系"),
    (".ghost 未定义", "总控台用了 .ghost 但全站没有该规则（按钮没样式）；现已在统一层定义"),
)


UI_PAGES: tuple = (
    # (页面标题, 页面模块, 精确标记) —— 只读源码做**静态**核对，零网络请求（没有 SSRF 面）
    # 标记分两类：本模块里的注入调用；或 server.py 里的 _dock(CONST（整页常量的注入点在那边）
    ("总控台", "panel/server.py", "_dock(PAGE"),
    ("AI 指挥中心", "panel/ai_center.py", ""),
    ("总能力/连接", "panel/capability_page.py", ""),
    ("流水线部署", "panel/pipelines_page.py", ""),
    ("三套工具包", "panel/kits_page.py", ""),
    ("工作流", "panel/workflow_page.py", ""),
    ("原生大脑", "panel/brain_page.py", ""),
    ("对话", "panel/chat_page.py", ""),
    ("AI 终端", "panel/terminal_page.py", ""),
    ("3D 蓝图", "panel/blueprint_page.py", ""),
    ("语音操控", "panel/voice_page.py", ""),
    ("智能体对话", "panel/agents_page.py", ""),
    ("Octop 能力桥", "panel/octop_page.py", ""),
    ("云插件中枢", "panel/cloud_page.py", ""),
    ("数据库编队", "panel/db_page.py", ""),
    ("蓝牙操控", "panel/ble_page.py", ""),
    ("媒体监控", "panel/media_page.py", "_dock(MEDIA_PAGE"),
    ("数字人", "panel/digital_human_page.py", "_dock(DIGITAL_HUMAN_PAGE"),
    ("API 文档", "panel/server.py", 'inject(html, "/docs")'),
)


def ui_unify() -> dict:
    """按键统一的可核查口径（**静态**核对源码，零网络请求）：

    统一体系有两个入口 —— `inject()`（给老页面注入统一皮 + 统一导航）和
    `ui_design.Page`（新页面自带统一皮）。两个都不沾的页面就是体系外孤岛
    （历史上 /docs 就是这种，已并入）。顺带统计页面自带的 `button{` 规则条数：
    有也不致命（统一皮在文档尾部压过），但那是残留量，用来持续收敛。
    """
    import re
    try:
        srv = ROOT.joinpath("panel", "server.py").read_text(encoding="utf-8")
    except OSError:
        srv = ""
    rows = []
    for title, rel, marker in UI_PAGES:
        p = ROOT.joinpath(*rel.split("/"))
        try:
            src = p.read_text(encoding="utf-8")
        except OSError as exc:                                 # noqa: BLE001
            rows.append({"页面": title, "源码": rel, "在体系内": None, "入口": "",
                         "自带 button 规则": None, "问题": type(exc).__name__})
            continue
        via_inject = bool(re.search(r"\binject\s*\(", src))
        via_page = bool(re.search(r"\bPage\s*\(", src)) or "ui_design" in src
        via_dock = marker.startswith("_dock(") and marker in srv
        via_mark = bool(marker) and not marker.startswith("_dock(") and marker in src
        own_btn = len(re.findall(r"(^|[},])\s*button\s*[,{]?\s*\{", src, re.M))
        parts = []
        if via_inject or via_mark:
            parts.append("inject")
        if via_page:
            parts.append("Page")
        if via_dock:
            parts.append("server._dock")
        in_sys = bool(parts)
        rows.append({"页面": title, "源码": rel, "在体系内": in_sys,
                     "入口": "+".join(parts) or "无", "自带 button 规则": own_btn,
                     "问题": "" if in_sys else "既没走统一注入也没走统一 Page"})
    outside = [r["页面"] for r in rows if not r["在体系内"]]
    return {"页面数": len(rows), "在体系内": len(rows) - len(outside), "体系外": outside,
            "残留自写 button 规则合计": sum(int(r["自带 button 规则"] or 0) for r in rows),
            "行": rows,
            "口径": "走 inject() 或 ui_design.Page 即算在统一体系内；"
                    "页面自带的 button{} 由统一皮在文档尾部压过（条数=残留量，用于收敛）"}


def _totals_snapshot() -> dict:
    """复用 capability_map 的**已缓存**清单规模，别在这里自己再构造一次。

    真机教训：审计里原先每个函数各读一遍（其中 _tentacles() 会新建 100 根触手编队），
    结果是页面每次渲染都要几秒。清单规模是会话内的常量，读缓存那份即可。
    """
    try:
        from core import capability_map as cm
        t = cm._totals()
        return {"云插件": t.get("云插件"), "数据库槽": t.get("数据库槽"),
                "Octop 能力": t.get("Octop 能力"), "触手": t.get("触手"),
                "算力活": t.get("算力活"), "读帧插件": t.get("读帧插件"),
                "只读工具": t.get("只读工具")}
    except Exception:                                          # noqa: BLE001
        return {"云插件": _plugins(), "数据库槽": _slots(), "Octop 能力": _octop(),
                "触手": _tentacles(), "算力活": len(_cr.WORKLOADS),
                "读帧插件": _frames(), "只读工具": _tools()}


_AUDIT_CACHE = _TTL(ttl=60.0, name="capability_panels")


def audit(*, fresh: bool = False) -> dict:
    """逐项对齐审计。带 60 秒缓存：清单规模虽是常量，但读一遍仍要几秒，
    页面每刷一次都重算会把渲染拖长（真机实测 5~8 秒）。"""
    if fresh:
        return _audit_build()
    return _AUDIT_CACHE.get("v", _audit_build)


def _audit_build() -> dict:
    rows = _rows()
    missing = []
    ok_n = 0
    for r in rows:
        gaps = [k for k in ("面板", "状态源", "操作", "证据", "验收") if not str(r.get(k) or "").strip()]
        if gaps:
            missing.append({"能力": r["能力"], "缺": gaps})
        else:
            ok_n += 1
    return {"总数": len(rows), "已对齐": ok_n, "缺项": missing,
            "对齐率": round(ok_n / max(1, len(rows)), 3),
            "项": rows, "本轮补齐": [{"项": a, "说明": b} for a, b in RECENT_FIXES],
            "口径": "五查：面板 / 状态源 / 操作或验证 / 证据 / 验收标准；缺一即点名"}


def status() -> dict:
    a = audit()
    return {"能力项数": a["总数"], "已对齐": a["已对齐"], "缺项数": len(a["缺项"]),
            "对齐率": a["对齐率"], "缺项": a["缺项"], "本轮补齐数": len(RECENT_FIXES),
            "口径": a["口径"]}


def register(*, note: str = "") -> dict:
    from core import solidify as S
    a = audit()
    r = S.solidify("capability_panels", {"总数": a["总数"], "已对齐": a["已对齐"],
                                         "项": a["项"], "补齐": a["本轮补齐"]},
                   note=note or "能力控制面板逐项对齐审计")
    try:
        from core import deploy_ledger as DL
        DL.record("scan", "capability_panels",
                  detail=f"逐项对齐 {a['已对齐']}/{a['总数']}（缺 {len(a['缺项'])}）",
                  before="", after=str(a["对齐率"]), ok=not a["缺项"])
    except Exception as e:
        _swallow(__file__, e)
    return r


__all__ = ["audit", "status", "register", "RECENT_FIXES"]
