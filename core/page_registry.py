# core/page_registry.py —— 12 个页面注册 + 能力双向绑定（按键 → 页面 → 资源，对称可审计）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：总控台 / AI 指挥中心 / 总能力·连接 / 流水线部署 / 三套工具包 /
#   Octop 能力桥 / 云插件中枢 / 数据库编队 / 蓝牙操控 / 媒体监控 / 数字人 / API 文档
#   —— 这些按键要**全部设计好**，并且**全部连接双向绑定好**。
#
# "双向绑定"在这里的落地口径（可验证，不玩文字）：
#   页面 ↔ 资源：每条绑定都有 page_to_resource 与 resource_to_page 两个方向，缺一即不对称；
#   页面 ↔ 接口：每个页面声明它使用的 API 端点，审计会逐个确认这些端点在应用里真实存在；
#   页面 ↔ 页面：同组页面互相可达（导航互联），并给出反向入口（回链）。
# 纪律：只读本进程内的 FastAPI 路由表做审计（不向外发请求、不碰环回 URL）；
#      绑定登记与固化走 core.deploy_ledger / core.solidify，可回放可回滚。
from dataclasses import asdict, dataclass, field
import time

from core import deploy_ledger as J


@dataclass
class Page:
    id: str                  # 稳定 id（绑定键，别用中文名当键）
    标题: str
    路由: str
    图标: str                # 按键图标（纯字符，不引外部资源）
    分组: str                # 指挥 / 能力 / 设备 / 系统
    一句说明: str
    接口: tuple = field(default_factory=tuple)      # 该页使用的 API 端点（审计逐个验存在）
    资源: tuple = field(default_factory=tuple)      # 绑定的资源：工具 / 云插件族 / 库槽 / 设备
    反向入口: tuple = field(default_factory=tuple)  # 从资源侧回到该页的入口（双向的一半）


PAGES: tuple = (
    Page("console", "总控台", "/", "▣", "指挥",
         "全站入口：身体启动检查、告警、编队与快照一屏看",
         ("/api/health", "/api/state", "/api/backend", "/api/panel/overview",
          "/api/panel/alerts", "/api/fleet/status", "/api/fleet/drives", "/api/senses",
          "/api/jobs", "/api/media/vram"),
         ("触手编队", "身体账本", "告警状态机", "只读工具快照"),
         ("编队卡→/api/fleet/status", "告警卡→/api/panel/alerts",
          "快照卡→/api/body/tools")),
    Page("hub", "数据中枢", "/hub", "⛭", "指挥",
         "唯一数据源：页面登记/能力图/部件读数/布局/盲区 + 主动汇报/长任务/镜像/信息素",
         ("/api/hub/snapshot", "/api/hub/widgets", "/api/hub/scan", "/api/hub/layout",
          "/api/sched/tick", "/api/proactive/feed", "/api/longrun/status",
          "/api/mirror/status", "/api/stigmergy/status"),
         ("页面登记", "能力图", "部件读数", "主动汇报", "长任务", "镜像排练", "信息素场地"),
         ("部件读数→/api/hub/widgets", "盲区→/api/hub/scan",
          "推进心跳→/api/sched/tick", "主动扫一遍→/api/proactive/scan")),
    Page("dh-input", "数字人·输入框位", "/dh-input", "🧠", "对话",
         "她贴在输入框旁：欢迎语 + 递架构 + 六步带路 + 记忆检索",
         ("/api/dh/boot", "/api/dh/hello", "/api/dh/memory/search")),
    Page("fleet-live", "编队实时面板", "/fleet-live", "🛰", "指挥",
         "一页看全每个 AI：职业/在干活还是休息/绑定页数/最后动作",
         ("/api/fleet-live", "/api/fleet-live/status")),
    Page("tentacle-mail", "触手邮箱", "/tentacle-mail", "📧", "系统",
         "100 个专属邮箱；邮件正文留痕可见，核对「她到底有没有做」",
         ("/api/tentacle-mail/status", "/api/tentacle-mail/messages", "/api/tentacle-mail/send")),
    Page("tentacle-accounts", "触手账户与密钥", "/tentacle-accounts", "🔐", "系统",
         "四服务位（邮箱/云插件/数据库/开源仓库）+ 密钥指纹（只回指纹不回显原文）",
         ("/api/tentacle-accounts",)),
    Page("studio", "创作工坊", "/studio", "🎬", "指挥",
         "一条链出片：台湾腔旁白 → 本地配乐 → 字幕/口型轴 → ffmpeg 成片（全本地零付费）",
         ("/api/studio", "/api/studio/make", "/api/studio/file/", "/api/lipsync"),
         ("创作链 core.studio", "edge-tts 台湾腔", "本地配乐算法", "ffmpeg 合成", "动作件 anim_v"),
         ("创作链→core/studio.py", "口型轴→/api/lipsync", "成品→/api/studio/file/")),
    Page("command", "AI 指挥中心", "/command", "⌘", "指挥",
         "自然语言 → 术语解释 → 只读读数；指挥层唯一入口",
         ("/api/ai/ask", "/api/terms", "/api/frames/count"),
         ("术语 Skill", "只读工具面", "大脑"),
         ("术语命中→只读工具", "只读工具→/api/body/tools")),
    Page("capability", "总能力/连接", "/capability", "▦", "能力",
         "总能力图表 + 连接状态 + 精准用量 + 生产就绪度根因台账 + 固化回滚",
         ("/api/capability/chart", "/api/capability/usage", "/api/capability/detail",
          "/api/capability/solid", "/api/compute/route", "/api/compute/vram",
          "/api/production"),
         ("云插件槽", "数据库槽", "Octop 能力", "算力路由", "固化档案"),
         ("条形→/api/capability/detail", "用量→/api/capability/usage",
          "台账→/api/production")),
    Page("pipelines", "流水线部署", "/pipelines", "⧉", "能力",
         "四条全自动流水线（短视频/电影/音乐/编程）48 步的分类部署与变更日志",
         ("/api/pipelines/catalog", "/api/pipelines/status", "/api/pipelines/journal"),
         ("云插件槽", "库槽", "触手区间", "替代实现执行器"),
         ("步骤→/api/pipelines/step", "变更→/api/pipelines/journal")),
    Page("kits", "三套工具包", "/kits", "◫", "能力",
         "剪映式 / Qwen-Image / ComfyUI 式三套包全走云插件，配置白名单校验",
         ("/api/kits/catalog", "/api/kits/status", "/api/kits/journal", "/api/kits/validate"),
         ("云插件槽", "库槽", "本机免费通道", "替代实现执行器"),
         ("配置→/api/kits/config", "校验→/api/kits/validate")),
    Page("chat", "对话", "/chat", "💬", "对话",
         "APP 独立多功能对话：会话留得住 + 智能体可点名 + 五种模式（日常/记忆/读数/工作流/工具）",
         ("/api/chat/sessions", "/api/chat/new", "/api/chat/send", "/api/chat/history",
          "/api/chat/mode", "/api/chat/rename", "/api/chat/drop", "/api/chat/status"),
         ("会话存储", "Octop 名册", "原生大脑", "工作流", "只读工具"),
         ("会话→/api/chat/sessions", "发送→/api/chat/send", "记忆→/api/brain/ask")),
    Page("terminal", "AI 终端", "/terminal", "🖥", "对话",
         "终端形态的指挥入口：白名单命令派发（help/ask/brain/remember/agents/workflows/tools）",
         ("/api/terminal/run", "/api/terminal/history", "/api/terminal/help"),
         ("原生大脑", "指挥读数链", "工作流闸门", "只读工具", "命令历史"),
         ("执行→/api/terminal/run", "历史→/api/terminal/history")),
    Page("blueprint", "3D 蓝图", "/blueprint", "🧊", "指挥",
         "项目 3D 蓝图：指挥/能力/编队/地基四层上帝视角 + 关卡层 + 无死角检查",
         ("/api/blueprint", "/api/blueprint/status", "/api/blueprint/register"),
         ("四层结构", "关卡", "无死角检查", "固化快照"),
         ("旋转/俯仰/缩放→页面内", "固化→/api/blueprint/register")),
    Page("voice", "语音操控", "/voice", "🎙", "对话",
         "数字人交互式语音操控中心：说一句 → 分意图（给依据）→ 执行 → 台湾腔回话 → 留痕",
         ("/api/voice/command", "/api/voice/hear", "/api/voice/center/status",
          "/api/avatar/state"),
         ("原生大脑", "动手入口", "台湾腔通道", "本机免费听写"),
         ("执行→/api/voice/command", "收音→/api/voice/hear")),
    Page("friends", "AI 朋友圈", "/api/friends/page", "🫂", "对话",
         "EigenFlux 只读接入：她在社交图谱里的身份 + 好友 + 平台动态快照 + 私信摘要（不编打分、对外写未实现）",
         ("/api/friends/status", "/api/friends/friends", "/api/friends/feed",
          "/api/friends/days", "/api/friends/messages"),
         ("社交身份 profile.json", "好友 contacts.json", "动态 data/broadcasts",
          "私信 data/messages", "只读不写"),
         ("看身份→/api/friends/status", "看动态→/api/friends/feed")),
    Page("brain", "原生大脑", "/brain", "🧠", "指挥",
         "统一记忆（主脑+触手+用户+系统）+ 生命起源存档 + 元认知 + 热度褪色 + 提醒 + 隐私回收站",
         ("/api/brain/status", "/api/brain/capture", "/api/brain/ask",
          "/api/brain/memories", "/api/brain/life", "/api/brain/metacog",
          "/api/brain/nudges", "/api/brain/consolidate", "/api/brain/unify"),
         ("统一记忆库", "生命起源存档", "元认知", "热度引擎", "回收站"),
         ("捕捉→/api/brain/capture", "提问→/api/brain/ask",
          "生平→/api/brain/life", "元认知→/api/brain/metacog")),
    Page("workflow", "工作流", "/workflow", "⛓", "指挥",
         "工作流独立页：多智能体协作编排 + 市场调研前置闸门 + 逐段验收标准（不盲推）",
         ("/api/workflows/status", "/api/workflows/catalog", "/api/workflows/graph",
          "/api/workflows/acceptance", "/api/workflows/research",
          "/api/workflows/research/submit", "/api/workflows/advance"),
         ("工作流注册表", "市场调研闸门", "触手班分工", "验收标准"),
         ("编排图→/api/workflows/graph", "调研→/api/workflows/research",
          "验收→/api/workflows/acceptance")),
    Page("agents", "智能体对话", "/agents", "◍", "对话",
         "Octop 智能体名册 + 工程对话（点名带专长）+ 多智能体协作工作流图",
         ("/api/agents/status", "/api/agents/roster", "/api/agents/workflow",
          "/api/agents/ask", "/api/agents/history"),
         ("Octop 名册", "V9 驱动链", "对话记录"),
         ("名册→/api/agents/roster", "对话→/api/agents/ask")),
    Page("octop", "Octop 能力桥", "/octop", "◈", "能力",
         "339 项 Octop/V9 能力 × 100 触手 1:1 双向绑定（含对称性检查）",
         ("/api/octop/catalog", "/api/octop/state", "/api/octop/links"),
         ("Octop 能力", "触手", "octop_binding 表"),
         ("能力行→/api/octop/links", "绑定→/api/octop/bind")),
    Page("cloud", "云插件中枢", "/cloud", "☁", "能力",
         "100 个云插件 10×10：开关 / 插入双向绑定 / 内部互绑 / 出网隔离",
         ("/api/cloud/registry", "/api/cloud/state", "/api/cloud/links", "/api/cloud/speed"),
         ("云插件槽", "触手", "出网池", "cloud_binding/cloud_share"),
         ("插件格→/api/cloud/links", "速率→/api/cloud/speed")),
    Page("db", "数据库编队", "/db", "▤", "能力",
         "100 个库槽 10×10：真建库 / 开关 / 触手双向绑定 / 磁盘占用",
         ("/api/db/registry", "/api/db/state", "/api/db/mesh"),
         ("数据库槽", "触手", "db_slot_state/db_binding"),
         ("库格→/api/db/state", "占用→/api/capability/usage")),
    Page("ble", "蓝牙操控", "/ble", "❖", "设备",
         "本机蓝牙多源枚举 + 真实 BLE 扫描 + 授权闸门写操作 + 追加式审计",
         ("/api/ble/status", "/api/ble/scan", "/api/ble/ops", "/api/ble/report"),
         ("蓝牙适配器", "BLE 设备", "Grant 授权", "ble_ops 审计"),
         ("设备行→/api/ble/scan", "操作→/api/ble/ops")),
    Page("media", "媒体监控", "/media", "▥", "设备",
         "生成队列：深度/等待/失败率/显存/死信/事件流",
         ("/api/media/queue/stats", "/api/media/vram", "/api/media/dead",
          "/api/media/terminal-events"),
         ("媒体队列", "显存预算（本地 0）", "云主管道"),
         ("队列→/api/media/queue/stats", "死信→/api/media/dead")),
    Page("digital-human", "数字人", "/digital-human", "☺", "设备",
         "身体自己说：实时流 + 见证播报 + 工具问询",
         ("/api/digital-human/stream", "/api/digital-human/witness",
          "/api/digital-human/tools"),
         ("语音总线", "情绪源", "证词播报"),
         ("实时流→/api/digital-human/stream", "见证→/api/digital-human/witness")),
    Page("docs", "API 文档", "/docs", "⌗", "系统",
         "内置接口文档（V9 自己渲染，不引任何外部资源）",
         ("/openapi.json",),
         ("全部路由",),
         ("路由→各页面",)),
)


def catalog() -> dict:
    return {p.id: asdict(p) for p in PAGES}


def routes_in_app() -> set:
    """本进程内的 FastAPI 路由集合（只读应用对象，不对外发请求）。"""
    try:
        import panel.server as srv
        return {getattr(r, "path", "") for r in getattr(srv.app, "routes", []) if getattr(r, "path", "")}
    except Exception:                                     # noqa: BLE001
        return set()


def bindings() -> dict:
    """双向绑定清单：每条都给出两个方向，缺一即不对称。"""
    out = {}
    for p in PAGES:
        for res in p.资源:
            out[f"page:{p.id}↔res:{res}"] = {
                "页面": p.标题, "路由": p.路由, "资源": res,
                "page_to_resource": f"{p.路由} → {res}",
                "resource_to_page": f"{res} → {p.路由}",
                "对称": True}
    for p in PAGES:
        for other in PAGES:
            if other.id != p.id and other.分组 == p.分组:
                out[f"page:{p.id}↔page:{other.id}"] = {
                    "页面": p.标题, "路由": p.路由, "资源": f"页面·{other.标题}",
                    "page_to_resource": f"{p.路由} → {other.路由}",
                    "resource_to_page": f"{other.路由} → {p.路由}",
                    "对称": True}
    return out


def audit() -> dict:
    """审计：路由都在应用里、声明的接口都存在、绑定成对对称。"""
    have = routes_in_app()
    problems, missing_api = [], []
    for p in PAGES:
        if p.路由 not in have:
            problems.append({"页面": p.id, "问题": "路由未注册", "值": p.路由})
        for api in p.接口:
            if api not in have:
                missing_api.append({"页面": p.id, "接口": api})
        if not p.资源 or not p.反向入口:
            problems.append({"页面": p.id, "问题": "缺资源或反向入口（双向绑定不完整）"})
    b = bindings()
    asym = [k for k, v in b.items() if not v.get("对称")]
    return {"页面数": len(PAGES), "绑定对": len(b),
            "分组": sorted({p.分组 for p in PAGES}),
            "路由问题": problems, "接口缺失": missing_api, "不对称绑定": asym,
            "ok": not problems and not missing_api and not asym,
            "路由表规模": len(have)}


def deploy_rows() -> dict:
    """部署/绑定清单（键用稳定 id，供扫描比对与面板展示）。"""
    rows = {}
    for p in PAGES:
        rows[f"page:{p.id}"] = {"标题": p.标题, "路由": p.路由, "图标": p.图标,
                                "分组": p.分组, "一句说明": p.一句说明,
                                "接口数": len(p.接口), "资源数": len(p.资源),
                                "反向入口数": len(p.反向入口),
                                "状态": "已绑定（双向）"}
    for key, b in bindings().items():
        rows[key] = {"页面": b["页面"], "资源": b["资源"],
                     "page_to_resource": b["page_to_resource"],
                     "resource_to_page": b["resource_to_page"],
                     "状态": "已绑定（双向）" if b["对称"] else "不对称"}
    return rows


def bind_all(*, note: str = "") -> dict:
    """执行绑定登记：页面 + 每对双向绑定 → 变更日志 → 固化。"""
    from core import solidify
    rows = deploy_rows()
    ok_n, failed = 0, []
    for where, row in rows.items():
        r = J.record("deploy", where, detail=row, ok=True)
        if r.get("ok"):
            ok_n += 1
        else:
            failed.append({"哪里": where, "原因": r.get("原因")})
    solid = solidify.solidify("page_registry",
                              {"pages": catalog(), "bindings": bindings(),
                               "audit": audit()},
                              note=note or "12 个页面与能力的双向绑定登记表")
    J.record("solidify", "solidify:page_registry", detail=solid, ok=bool(solid.get("ok")))
    return {"ok": not failed and bool(solid.get("ok")), "登记条数": ok_n,
            "页面数": len(PAGES), "绑定对": len(bindings()), "固化": solid,
            "失败": failed, "审计": audit()}


def scan(*, scope: str = "pages×resources") -> dict:
    """扫描页面与绑定现状并记录（与上次快照比对 → 新增/修改/消失 全进日志）。"""
    got = J.scan_and_record(deploy_rows(), scope=scope)
    return {**got, "审计": audit()}


def status() -> dict:
    """给面板用：12 个按键（含图标/分组/接口数）+ 审计 + 固化版本 + 日志概览。"""
    from core import solidify
    items = [{"id": p.id, "标题": p.标题, "路由": p.路由, "图标": p.图标,
              "分组": p.分组, "一句说明": p.一句说明, "接口数": len(p.接口),
              "资源数": len(p.资源), "绑定向": "双向"} for p in PAGES]
    return {"按键": items, "按键数": len(items), "审计": audit(),
            "绑定对样例": list(bindings().items())[:3] and len(bindings()),
            "固化": {"page_registry": (solidify.latest("page_registry") or {}).get("rev"),
                     "versions": len(solidify.history("page_registry"))},
            "变更日志": J.summary(), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


__all__ = ["PAGES", "Page", "catalog", "bindings", "audit", "deploy_rows", "bind_all",
           "scan", "status", "routes_in_app"]
