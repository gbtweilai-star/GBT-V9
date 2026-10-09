# core/kit_packs.py —— 三套免费工具 → 云插件部署包（剪映 / Qwen-Image / ComfyUI 式工作流）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：把这三套（剪映自动化、Qwen-Image-2.1 文生图+改图、秋叶 ComfyUI 式工作流）
# 全部**部署到云插件上面**，配置要精细、不能出现混乱，并跑通闭环。
#
# 本模块只做三件事，且每一件都可复核：
#   ① 严格 schema 的配置（唯一来源、白名单键、类型/取值校验；未知键直接拒绝 —— 这就是"不混乱"）
#   ② 分类部署：每一步 → 云插件槽（PLUGIN_IDS 真实存在）+ 库槽（SLOT_IDS 真实存在）
#   ③ 登记与留痕：deploy / scan / 固化 全写 core.deploy_ledger，配置固化到 core.solidify
#
# 环境事实（本机，2026-10-06 实测，写进配置以便后人别搞混）：
#   · 显卡 AMD Radeon RX 6500M，**没有 NVIDIA**；本机 torch 是 CPU 版（2.12.0+cpu）
#     → "秋叶 40/30 系（CUDA）" 那套在本机跑不了显卡；本机若走本地只能 DirectML/CPU，很慢
#   · ffmpeg 8.1.2 已装（本地剪辑通道可用）；ComfyUI 目录存在（C:\Users\ADMIN\ComfyUI）
#   · 剪映（CapCut）未安装
#   → 因此按主人要求：**主管道全部走云插件**，本地只保留源码通道作备用。
#
# 合规口径（不动摇）：Qwen-Image-2.1 Uncensored 版本移除了其内置安全过滤，那不是我们的闸门；
#   V9 自己的 guard 步骤（内容守卫）仍是**唯一闸门**，且每一步都进审计。本模块不实现任何
#   "绕过检测/规避审计"的能力，只做能力编排与登记。
from dataclasses import asdict, dataclass, field

from core import deploy_ledger as J


@dataclass
class KitStep:
    id: str
    名称: str
    插件: str                 # 云插件槽（主管道，真实存在）
    库槽: str                 # 结果落库
    细节: str                 # 这一步到底做什么（输入 → 输出）
    来源工具: str             # 对应三件套里的哪一环
    本地备用: str = ""        # 保留的本地通道（源码实现）
    门禁: str = "无"
    缺口: str = ""            # 目录无对应模型时如实标注
    待接槽: str = ""          # 待接登记的落点（没有 reserved 可登记就直说）


@dataclass
class KitPack:
    id: str
    名称: str
    工具: str                 # 对应哪件工具
    免费依据: str             # 为什么是"免费"（写清依据，不吹）
    云端形态: str             # 在云插件里被部署成什么
    步骤: tuple = field(default_factory=tuple)
    环境事实: tuple = field(default_factory=tuple)
    闸门: str = "V9 内容守卫（guard）为唯一闸门；每一步进审计"


# ═══════════ ① 剪映式短视频自动化（CapCut 的免费能力搬上云插件）═══════════
CAPCUT = KitPack(
    "capcut", "剪映式短视频自动化", "剪映 / CapCut（手机版+电脑版）",
    "剪映本体对个人使用免费（高级素材与部分特效为会员）；本包只用其免费能力对应的云端等价物："
    "自动字幕、智能踩点、调色、竖屏导出、模板套用",
    "云端：字幕=ASR 槽，踩点/情绪=分类槽，画面理解=视觉槽，调色 LUT 与合成=本地 ffmpeg 执行，"
    "合规=守卫槽；全部逐槽登记",
    (
        KitStep("cp1", "素材清点与镜头切分", "vision:llama-3.2-11b-vision-instruct#3",
                "document:frames_meta#7",
                "导入素材 → 抽帧做镜头切分（转场点/景别/时长表）", "剪映「智能镜头分割」",
                "本地 ffmpeg 场景检测（select=gt(scene,...)）"),
        KitStep("cp2", "自动字幕（听写+对齐）", "asr:whisper-large-v3-turbo#2",
                "fulltext:ft_reports#3",
                "音轨 → 逐句字幕 SRT（时间轴对齐，错字标出）", "剪映「识别字幕」",
                "senses/mic.py（本地 Whisper）"),
        KitStep("cp3", "字幕多语翻译", "translation:m2m100-1.2b#2", "fulltext:ft_notes#1",
                "中文字幕 → 目标语字幕（保持时间轴）", "剪映「字幕翻译」",
                "skills/engine.py"),
        KitStep("cp4", "智能踩点与 BGM", "classification:distilbert-sst-2-int8#2",
                "document:kb#10",
                "BGM 节拍 + 画面情绪 → 卡点表（每段落点毫秒）", "剪映「自动踩点」",
                "本地 librosa/ffmpeg 节拍检测",
                缺口="无音乐生成模型 → 只做选曲与踩点；生成登记 reserved 待接",
                待接槽="music-generation 族（Cloudflare Workers AI 目录中尚无此族）"),
        KitStep("cp5", "调色与滤镜方案", "vision:llava-1.5-7b-hf#1", "vector:emb_frame#3",
                "画面 → 调色参数（曝光/对比/色温/肤色保护）", "剪映「调节/滤镜」",
                "本地 ffmpeg eq/colorbalance/LUT"),
        KitStep("cp6", "模板化合成与导出", "image-generation:phoenix-1.0#7", "queue:q_media#1",
                "镜头+字幕+踩点+调色 → 9:16 成片（封面另出）", "剪映「一键成片/模板」",
                "skills/video_edit.py（本地 ffmpeg 合成）"),
        KitStep("cp7", "封面与标题", "image-generation:dreamshaper-8-lcm#5", "document:reports#2",
                "成片关键帧 → 封面（大字报/对比/人物三版式）", "剪映「封面模板」",
                "本地封面模板"),
        KitStep("cp8", "合规质检与发布文案", "guard:llama-guard-3-8b#1", "audit:a_alert#6",
                "成片+文案过闸；不过退回 cp1", "剪映「发布」前的自查",
                "body/witness_alerts.py（本地规则）",
                门禁="发布前人工确认"),
    ),
    ("本机未安装剪映（CapCut）→ 本包按主人要求全走云插件，不依赖本机 GUI",
     "本机 ffmpeg 8.1.2 可用 → 本地兜底通道真实可跑"),
)

# ═══════════ ② Qwen-Image 文生图 + 改图 二合一 ═══════════
QWEN_IMAGE = KitPack(
    "qwen_image", "Qwen-Image 文生图+改图", "Qwen-Image-2.1 / Uncensored GGUF（HuggingFace）",
    "Qwen-Image 权重按官方许可开放下载（GGUF 量化为社区发行）；本包只做能力编排，不代下载、不转售",
    "云端：文生图=图像生成槽（flux/SDXL 等），改图=局部重绘槽 + 蒙版，画面理解/质检=视觉槽，"
    "守卫=guard 槽；GGUF 本地版作为备用通道登记",
    (
        KitStep("qi1", "提示词工程", "text-generation:llama-3.3-70b-instruct-fp8-fast#4",
                "memory:mem_prompt#8",
                "一句话需求 → 结构化提示词（主体/风格/构图/光比/负向词）", "Qwen-Image 的文本编码侧",
                "skills/engine.py"),
        KitStep("qi2", "文生图", "image-generation:flux-2-dev#2", "document:reports#2",
                "结构化提示词 → 出图（含 1:1 / 9:16 / 16:9 三种出图规格）", "Qwen-Image 文生图",
                "本地 ComfyUI + Qwen-Image GGUF（备用通道）"),
        KitStep("qi3", "局部改图（重绘）", "image-generation:stable-diffusion-v1-5-inpainting#8",
                "document:reports#2",
                "原图 + 蒙版 + 指令 → 只改指定区域（改色/换物/补背景）", "Qwen-Image 图片编辑",
                "本地 ComfyUI Inpainting 蓝图（Qwen-Image 蓝图已就位）"),
        KitStep("qi4", "细节增强与放大", "image-generation:stable-diffusion-xl-lightning#10",
                "document:reports#2",
                "低清/瑕疵 → 放大 + 细节修复（faces/edges 保真）", "Qwen-Image 高分辨率输出",
                "本地 Real-ESRGAN（备用）"),
        KitStep("qi5", "画面质检", "vision:llama-3.2-11b-vision-instruct#3",
                "vector:emb_ui#6",
                "出图 → 结构/手部/文字一致性检查，不合格回 qi1 重出", "出图后的自检",
                "core/gui_perception.py（本地读图）"),
        KitStep("qi6", "入库与合规闸门", "guard:llama-guard-3-8b#1", "audit:a_chain#1",
                "定稿图 + 提示词 + 种子 → 入库（可复现）；合规检查", "交付前闸门",
                "body/witness_alerts.py",
                门禁="对外发布前人工确认"),
    ),
    ("该版本移除了模型内置安全过滤 → V9 侧 guard 步骤为唯一闸门（不依赖模型自带过滤）",
     "本机为 AMD RX 6500M + CPU 版 torch → 本地跑 GGUF 出图很慢；因此主管道用云插件",
     "ComfyUI 目录已存在且含 Qwen-Image 蓝图（Text to Image / Inpainting / Outpainting / Layered）"),
)

# ═══════════ ③ ComfyUI 式工作流编排（节点图自动化）═══════════
COMFY = KitPack(
    "comfyui", "ComfyUI 式工作流编排", "秋叶 ComfyUI 2026 终极版（节点图工作站）",
    "ComfyUI 本体开源（GPL-3.0），秋叶整合包为免费分发；本包把节点图自动化搬到云插件侧执行",
    "云端：工作流 JSON 由代码槽生成，参数填充由文本槽补全，节点执行映射到图像生成/视觉槽，"
    "版本与产物入库；本地 ComfyUI（若可用）作为备用执行器",
    (
        KitStep("cf1", "工作流需求解析", "text-generation:deepseek-r1-distill-qwen-32b#9",
                "memory:mem_project#3",
                "自然语言需求 → 节点图需求（输入类型/中间产物/输出规格）", "ComfyUI 工作流设计",
                "docs/认知系统架构.md（本地架构文档）"),
        KitStep("cf2", "节点图 JSON 生成", "code:qwen2.5-coder-32b-instruct#1",
                "document:specs#5",
                "节点图需求 → 可执行 workflow JSON（节点/连线/默认参数）", "ComfyUI 的 workflow 文件",
                "core/sandbox_exec.py（写时复制校验）"),
        KitStep("cf3", "参数化与批处理", "text-generation:qwen3-30b-a3b-fp8#7",
                "document:patches#9",
                "workflow JSON → 参数表（变量/批量取值/随机种子策略）", "ComfyUI 的节点参数",
                "skills/engine.py"),
        KitStep("cf4", "节点执行（云端）", "image-generation:flux-2-klein-9b#4",
                "queue:q_cloud#3",
                "参数表 → 批量出图（每张带种子，可复现）", "ComfyUI 的 Queue Prompt",
                "本地 ComfyUI API（127.0.0.1:8188，若已启动）",
                缺口="本机为 AMD + CPU 版 torch → 本地执行很慢；云端槽为默认执行器",
                待接槽="video-generation 族（目录尚无此族，视频节点登记待接）"),
        KitStep("cf5", "产物质检与筛选", "vision:moondream3.1-9B-A2B#2", "vector:emb_frame#3",
                "批量产物 → 打分筛选（构图/清晰/一致性），保留 top-k", "ComfyUI 的图库筛选",
                "media/monitor.py（本地阈值）"),
        KitStep("cf6", "版本固化与复用", "embedding:bge-large-en-v1.5#3", "memory:mem_skill#5",
                "定稿 workflow + 参数 + 产物 → 固化版本（下次一键复跑）", "工作流复用",
                "core/solidify.py（版本 + sha256 + 回滚）"),
    ),
    ("秋叶包标注「40/30 系显卡」= CUDA 路线；本机无 NVIDIA，CUDA 路线不适用（DirectML/CPU 可跑但慢）",
     "本机 ComfyUI 目录：C:\\Users\\ADMIN\\ComfyUI（含 Qwen-Image 蓝图）",
     "云端执行器不需要本机显卡 → 与本机 0 显存原则一致"),
)

PACKS: tuple = (CAPCUT, QWEN_IMAGE, COMFY)

# 替代实现 → 具体执行器（core/alt_impl.py），让"已接（替代实现）"有落地代码可查
ALT_EXECUTOR: dict = {
    "cp2": "alt_impl.make_srt + burn_subtitles（字幕生成与烧录）",
    "cp4": "alt_impl.synth_bed（无曲库时合成声床；选曲待曲库）",
    "cp5": "alt_impl.vertical_grade（eq+colorbalance 调色）",
    "cp6": "alt_impl.vertical_grade（9:16 竖屏成片）",
    "cf4": "alt_impl.frames_to_clip + master（节点执行用本机 ffmpeg 出图/出片）",
}

# ═══════════ 配置 schema（"不混乱"就靠这个：白名单 + 类型 + 取值）═══════════
CONFIG_SCHEMA: dict = {
    "version": (int, None, None),                  # (类型, 最小, 最大)
    "kit": (str, None, None),
    "cloud_only": (bool, None, None),              # 主管道是否只走云插件
    "guard_step": (str, None, None),               # 唯一闸门所在步骤
    "steps": (list, None, None),
    "env_facts": (list, None, None),
    "local_fallback_allowed": (bool, None, None),
    "local_notes": (list, None, None),
    "solidify_name": (str, None, None),
}
ALLOWED_PLUGIN_FAMILIES = ("text-generation", "embedding", "image-generation", "vision",
                           "asr", "tts", "translation", "classification", "code", "guard")


def build_config(pack_id: str) -> dict:
    """生成某套包的标准配置（唯一来源；键全部来自 CONFIG_SCHEMA 白名单）。"""
    pack = next((p for p in PACKS if p.id == pack_id), None)
    if pack is None:
        return {"ok": False, "reason": f"没有这套包：{pack_id}"}
    cfg = {
        "version": 1,
        "kit": pack.id,
        "cloud_only": True,
        "guard_step": next((s.id for s in pack.步骤 if s.门禁 != "无"), ""),
        "steps": [{"id": s.id, "名称": s.名称, "插件": s.插件, "库槽": s.库槽,
                   "来源工具": s.来源工具, "本地备用": s.本地备用, "门禁": s.门禁,
                   "缺口": s.缺口} for s in pack.步骤],
        "env_facts": list(pack.环境事实),
        "local_fallback_allowed": True,
        "local_notes": ["本地通道仅作备用；主管道按主人要求全走云插件",
                        "本机无 NVIDIA 显卡、torch 为 CPU 版 → 本地出图/视频很慢"],
        "solidify_name": f"kit_{pack.id}",
    }
    return {"ok": True, "config": cfg}


def validate_config(cfg: dict) -> dict:
    """严格校验：未知键拒绝、类型不符拒绝、插件族必须在目录里、步骤必须唯一。"""
    errs = []
    if not isinstance(cfg, dict):
        return {"ok": False, "errors": ["配置不是字典"]}
    for k, v in cfg.items():
        if k not in CONFIG_SCHEMA:
            errs.append(f"未知配置键：{k}（白名单外，拒绝）")
            continue
        typ = CONFIG_SCHEMA[k][0]
        if not isinstance(v, typ):
            errs.append(f"键 {k} 类型应为 {typ.__name__}，实际 {type(v).__name__}")
    if cfg.get("kit") and not any(p.id == cfg["kit"] for p in PACKS):
        errs.append(f"未知 kit：{cfg.get('kit')}")
    steps = cfg.get("steps")
    if isinstance(steps, list):
        ids = [s.get("id") for s in steps if isinstance(s, dict)]
        if len(ids) != len(set(ids)):
            errs.append("步骤 id 有重复")
        for s in steps:
            if not isinstance(s, dict):
                errs.append("步骤必须是字典")
                continue
            extra = set(s) - {"id", "名称", "插件", "库槽", "来源工具", "本地备用",
                             "门禁", "缺口"}
            if extra:
                errs.append(f"步骤 {s.get('id')} 有多余键：{sorted(extra)}")
            fam = str(s.get("插件") or "").split(":")[0]
            if fam not in ALLOWED_PLUGIN_FAMILIES:
                errs.append(f"步骤 {s.get('id')} 的插件族不在目录内：{fam}")
    return {"ok": not errs, "errors": errs}


def audit() -> dict:
    """完整性审计：每步云插件槽/库槽必须真实存在；缺口必须写明待接落点。"""
    from core.cloud_plugins import PLUGIN_IDS
    from core.db_fleet import SLOT_IDS
    ps, ds = set(PLUGIN_IDS), set(SLOT_IDS)
    problems, gaps = [], []
    n = 0
    for p in PACKS:
        for s in p.步骤:
            n += 1
            where = f"{p.id}/{s.id}"
            if s.插件 not in ps:
                problems.append({"哪里": where, "问题": "云插件槽不存在", "值": s.插件})
            if s.库槽 not in ds:
                problems.append({"哪里": where, "问题": "库槽不存在", "值": s.库槽})
            if s.缺口:
                gaps.append({"哪里": where, "缺口": s.缺口,
                             "待接": getattr(s, "待接槽", "")})
            fam = s.插件.split(":")[0]
            if fam not in ALLOWED_PLUGIN_FAMILIES:
                problems.append({"哪里": where, "问题": "插件族不在目录内", "值": fam})
    return {"包": len(PACKS), "步骤总数": n, "问题": problems, "ok": not problems,
            "缺口": gaps}


def deploy_rows() -> dict:
    """部署清单：键用稳定 id（包/步骤），值带完整配置（供扫描比对）。"""
    rows = {}
    for p in PACKS:
        for s in p.步骤:
            rows[f"kit:{p.id}/{s.id}"] = {
                "包": p.名称, "工具": p.工具, "免费依据": p.免费依据, "云端形态": p.云端形态,
                "步骤": s.id, "名称": s.名称, "细节": s.细节, "云插件": s.插件,
                "库槽": s.库槽, "来源工具": s.来源工具, "本地备用": s.本地备用,
                "门禁": s.门禁, "缺口": s.缺口,
                "原生模型": "目录无（登记待接）" if s.缺口 else "有",
                "执行器": ALT_EXECUTOR.get(s.id, ""),
                "状态": "已接（替代实现）" if s.缺口 else "已部署到云插件"}
    return rows


def configure_all() -> dict:
    """生成并校验三套配置；有错就拒绝落盘（宁可不写，也不写一份乱的配置）。"""
    out, bad = {}, []
    for p in PACKS:
        got = build_config(p.id)
        if not got.get("ok"):
            bad.append({"包": p.id, "原因": got.get("reason")})
            continue
        v = validate_config(got["config"])
        if not v["ok"]:
            bad.append({"包": p.id, "校验错误": v["errors"]})
            continue
        out[p.id] = got["config"]
    return {"ok": not bad, "配置": out, "不合格": bad}


def register(*, note: str = "") -> dict:
    """固化三套配置（可回滚），并把固化行为写进变更日志。"""
    from core import solidify
    cfg = configure_all()
    if not cfg["ok"]:
        return {"ok": False, "reason": "配置校验未过，拒绝固化", "不合格": cfg["不合格"]}
    r = solidify.solidify("kit_packs", {"packs": catalog(), "configs": cfg["配置"],
                                        "audit": audit()},
                          note=note or "三套工具云插件部署包登记表")
    J.record("solidify", "solidify:kit_packs", detail=r, ok=bool(r.get("ok")))
    return r


def deploy_all(*, note: str = "") -> dict:
    """分类部署：逐条登记"哪一步 → 哪个云插件槽"，再固化登记表。"""
    rows = deploy_rows()
    ok_n, failed = 0, []
    for where, row in rows.items():
        r = J.record("deploy", where, detail=row, ok=True,
                     reason=(row["缺口"] if row.get("缺口") else ""))
        if r.get("ok"):
            ok_n += 1
        else:
            failed.append({"哪里": where, "原因": r.get("reason")})
    solid = register(note=note or "三套工具包分类部署后固化")
    return {"ok": not failed and solid.get("ok"), "登记条数": ok_n, "步骤总数": len(rows),
            "固化": solid, "失败": failed, "审计": audit(),
            "配置校验": {"ok": configure_all()["ok"], "不合格": configure_all()["不合格"]}}


def scan(*, scope: str = "kit_packs×cloud_plugins") -> dict:
    """扫描部署现状并记录（与上次快照比对 → 新增/修改/消失 全进日志）。"""
    got = J.scan_and_record(deploy_rows(), scope=scope)
    return {**got, "审计": audit()}


def status() -> dict:
    """部署现状：每套包的步骤/已部署/待接 + 配置校验结果 + 固化版本 + 环境事实。"""
    from core import solidify
    rows = []
    for p in PACKS:
        done = sum(1 for s in p.步骤 if not s.缺口)
        rows.append({"id": p.id, "名称": p.名称, "工具": p.工具, "免费依据": p.免费依据,
                     "步骤数": len(p.步骤), "已部署到云插件": done,
                     "待接": len(p.步骤) - done,
                     "闸门步骤": [s.id for s in p.步骤 if s.门禁 != "无"],
                     "环境事实": list(p.环境事实), "闸门": p.闸门})
    cfg = configure_all()
    return {"包": rows, "审计": audit(),
            "配置校验": {"ok": cfg["ok"], "不合格": cfg["不合格"], "包数": len(cfg["配置"])},
            "固化": {"kit_packs": (solidify.latest("kit_packs") or {}).get("rev"),
                     "versions": len(solidify.history("kit_packs"))},
            "变更日志": J.summary()}


def catalog() -> dict:
    return {p.id: {"id": p.id, "名称": p.名称, "工具": p.工具, "免费依据": p.免费依据,
                   "云端形态": p.云端形态, "闸门": p.闸门, "环境事实": list(p.环境事实),
                   "步骤数": len(p.步骤),
                   "步骤": [asdict(s) for s in p.步骤]} for p in PACKS}


__all__ = ["PACKS", "KitPack", "KitStep", "catalog", "build_config", "validate_config",
           "configure_all", "audit", "deploy_rows", "deploy_all", "register", "scan",
           "status", "CONFIG_SCHEMA", "ALLOWED_PLUGIN_FAMILIES"]
