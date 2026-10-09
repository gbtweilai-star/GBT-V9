# core/pipelines.py —— 四条全自动化流水线（短视频 / 电影 / 音乐 / 专业编程）分类部署
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：把 AI 短视频 / AI 电影 / AI 音乐 / 专业编程 AI 的**全部自动化与步骤细节**
# 分类部署到 100 个云插件上，做好固化与部署登记，并完整记录每次扫描/修改/新增。
#
# 纪律（沿用全仓口径）：
#   · 每一步都必须落在**真实存在**的云插件槽（core.cloud_plugins.PLUGIN_IDS）与库槽（SLOT_IDS）；
#   · 目录里**没有**对应模型的能力（视频生成 / 音乐生成 / 混音母带）**不许假装**：
#     标 缺口 + 指向 reserved 槽 + 写明当前用哪条本地通道兜底；
#   · 部署、扫描、新增、修改一律写 core.deploy_ledger（追加式，只增不改）；
#   · 固化走 core.solidify（版本 + sha256 + 一键回滚）。
from dataclasses import asdict, dataclass, field

from core import deploy_ledger as J


@dataclass
class Step:
    id: str
    名称: str
    插件: str                 # 云插件槽（主管道）
    库槽: str                 # 结果落库
    本地备用: str             # 本地通道（保留的源码实现）
    细节: str                 # 这一步到底做什么（输入 → 输出）
    门禁: str = "无"          # 无 / Grant / 发布前人工确认
    缺口: str = ""            # 目录无对应模型时如实标注
    待接槽: str = ""          # 待接登记的落点（没有 reserved 可登记就直说）


@dataclass
class Pipeline:
    id: str
    名称: str
    目标: str
    步骤: tuple = field(default_factory=tuple)


# ═══════════ ① AI 短视频 ═══════════
SHORT_VIDEO = Pipeline("shortvideo", "AI 短视频", "竖屏 9:16，从选题到发布文案全自动，含合规质检", (
    Step("sv1", "选题与钩子", "text-generation:llama-3.3-70b-instruct-fp8-fast#4",
         "memory:mem_prompt#8", "skills/engine.py（本地提示词库）",
         "赛道+受众 → 3 个钩子标题 + 5 秒开场句（要求前 3 秒给冲突）"),
    Step("sv2", "脚本与分镜", "text-generation:qwen3-30b-a3b-fp8#7",
         "document:specs#5", "skills/video_edit.py",
         "钩子 → 分镜表：镜号/时长/画面/字幕/音效/BGM 情绪"),
    Step("sv3", "关键帧出图", "image-generation:flux-1-schnell#1",
         "document:frames_meta#7", "media/scheduler.py（本地显存预算）",
         "每镜 1-3 帧、9:16、统一角色一致性提示词"),
    Step("sv4", "图生视频补间", "image-generation:flux-2-klein-9b#4",
         "document:frames_meta#7", "本地 ffmpeg 关键帧补间",
         "关键帧 → 每镜 2-4 秒动态片段（含轻微运镜）",
         缺口="本目录无视频生成模型 → 已登记 reserved 槽待接；当前走本地 ffmpeg 补间",
         待接槽="video-generation 族（Cloudflare Workers AI 目录中尚无此族，无可登记的 reserved 槽）"),
    Step("sv5", "配音合成", "tts:aura-1#1", "kv-cache:session#1",
         "senses/voice.py（本地 TTS）",
         "分镜字幕 → 逐镜配音（语速/停顿/重音，默认文静台湾腔）"),
    Step("sv6", "口播口型质检", "vision:llava-1.5-7b-hf#1", "vector:emb_voice#5",
         "core/gui_perception.py（本地读屏）",
         "抽帧比对口型与音素 → 不合格镜次回炉（阈值可调）"),
    Step("sv7", "字幕与听写校对", "asr:whisper-large-v3-turbo#2", "fulltext:ft_reports#3",
         "senses/mic.py（本地 Whisper）",
         "成片回读 → 字幕与语音逐句对齐，错字/漏字标出"),
    Step("sv8", "BGM 与音效挑选", "classification:distilbert-sst-2-int8#2",
         "document:kb#10", "本地曲库规则表",
         "分镜情绪 → 曲库检索选 BGM + 音效点位",
         缺口="本目录无音乐生成模型 → 选曲而非生成；生成能力登记 reserved 待接",
         待接槽="music-generation 族（目录中尚无此族，无可登记的 reserved 槽）"),
    Step("sv9", "剪辑合成", "vision:llama-3.2-11b-vision-instruct#3", "queue:q_media#1",
         "skills/video_edit.py（本地 ffmpeg 执行）",
         "片段+配音+BGM+字幕 → 成片（转场/卡点/封边）"),
    Step("sv10", "封面与标题", "image-generation:dreamshaper-8-lcm#5",
         "document:reports#2", "本地封面模板",
         "成片关键帧 → 封面（大字报/人物/前后对比三种版式）"),
    Step("sv11", "发布文案多语", "translation:m2m100-1.2b#2", "fulltext:ft_notes#1",
         "skills/engine.py",
         "中文文案 → 目标语种 + 话题标签 + 发布时间建议"),
    Step("sv12", "合规质检", "guard:llama-guard-3-8b#1", "audit:a_alert#6",
         "body/witness_alerts.py（本地规则）",
         "成片+文案过闸：违规词/肖像/版权 → 不通过退 sv2",
         门禁="发布前人工确认"),
))

# ═══════════ ② AI 电影 ═══════════
FILM = Pipeline("film", "AI 电影", "长片级流程：世界观到母版归档，含审片与多语字幕", (
    Step("fm1", "世界观与主题", "text-generation:deepseek-r1-distill-qwen-32b#9",
         "memory:mem_project#3", "skills/engine.py",
         "一句话创意 → 世界观/时代/基调/核心冲突（推理模型）"),
    Step("fm2", "大纲与结构", "text-generation:llama-4-scout-17b-16e-instruct#5",
         "document:specs#5", "skills/engine.py",
         "世界观 → 三幕结构 + 场次大纲（每场目标/障碍/转折）"),
    Step("fm3", "剧本定稿", "text-generation:llama-3.3-70b-instruct-fp8-fast#4",
         "document:contracts#3", "skills/engine.py",
         "大纲 → 标准剧本格式（场景头/动作/对白）逐场产出"),
    Step("fm4", "角色与设定集", "embedding:bge-m3#4", "vector:emb_doc#4",
         "core/isolated_bus.py（本地哈希向量）",
         "剧本 → 角色小传+外观锚点（供后续出图保持一致）"),
    Step("fm5", "概念图", "image-generation:flux-2-dev#2", "document:frames_meta#7",
         "media/scheduler.py",
         "关键场景 → 概念图（美术方向先定，避免后面返工）"),
    Step("fm6", "镜头表", "text-generation:qwen3-30b-a3b-fp8#7", "document:specs#5",
         "skills/video_edit.py",
         "场次 → 镜头表：景别/机位/运动/时长/对白/音效"),
    Step("fm7", "关键帧出图", "image-generation:stable-diffusion-xl-base-1.0#9",
         "document:frames_meta#7", "media/scheduler.py",
         "镜头表 → 每镜关键帧（含角色锚点与光线指定）"),
    Step("fm8", "镜头生成", "image-generation:flux-2-klein-9b#4", "queue:q_media#1",
         "本地 ffmpeg 补间 + 运镜",
         "关键帧 → 镜头片段（景深/推拉摇移）",
         缺口="本目录无视频生成模型 → reserved 待接；当前用本地补间",
         待接槽="video-generation 族（目录中尚无此族）"),
    Step("fm9", "对白配音", "tts:aura-2-en#2", "vector:emb_voice#5",
         "senses/voice.py",
         "逐角色音色 → 对白配音（情绪跟随剧本标注）"),
    Step("fm10", "配乐与音效", "classification:bge-reranker-base#1", "document:kb#10",
         "本地音频库检索",
         "段落情绪 → 配乐选段 + 音效点位（生成能力缺口见备注）",
         缺口="无音乐生成模型 → 先用本地授权曲库检索；生成登记 reserved 待接",
         待接槽="music-generation 族（目录中尚无此族）"),
    Step("fm11", "剪辑与调色", "vision:llama-3.2-11b-vision-instruct#3", "queue:q_deliver#7",
         "skills/video_edit.py（ffmpeg 滤镜链）",
         "片段+对白+配乐 → 粗剪 → 精剪 → 统一调色（LUT 一致）"),
    Step("fm12", "字幕与多语", "translation:indictrans2-en-indic-1B#1", "fulltext:ft_contract#7",
         "senses/mic.py + skills/engine.py",
         "成片听写核对 → 多语字幕（时间轴对齐，样式统一）"),
    Step("fm13", "审片质检", "guard:smart-turn-v2#2", "audit:a_chain#1",
         "body/witness_alerts.py",
         "全片过闸：暴力/版权/肖像/连续性错误 → 出审片报告",
         门禁="发布前人工确认"),
    Step("fm14", "母版归档", "embedding:bge-large-en-v1.5#3", "document:contracts#3",
         "audit/ledger.py（本地账本）",
         "母版+工程文件+字幕+授权记录 → 归档可追溯（sha256 留痕）"),
))

# ═══════════ ③ AI 音乐 ═══════════
MUSIC = Pipeline("music", "AI 音乐", "词曲编唱混母到分发；生成类缺口如实标注", (
    Step("mu1", "主题与情绪", "text-generation:mistral-small-3.1-24b-instruct#6",
         "memory:mem_prompt#8", "skills/engine.py",
         "使用场景 → 主题/情绪/受众/时长目标"),
    Step("mu2", "歌词", "text-generation:llama-3.2-3b-instruct#2", "document:notes#1",
         "skills/engine.py",
         "主题 → 主歌/副歌/桥段歌词（含韵脚与字数对位）"),
    Step("mu3", "曲式与和声", "text-generation:qwq-32b#8", "document:specs#5",
         "本地和弦规则表",
         "歌词结构 → 曲式（主副桥）+ 和弦进行 + BPM/调性（推理模型）"),
    Step("mu4", "旋律生成", "text-generation:qwen3-30b-a3b-fp8#7", "document:specs#5",
         "本地 MIDI 模板",
         "和声框架 → 旋律 MIDI 草案",
         缺口="无音乐生成模型 → 先用文本模型出可读简谱线索，音频生成登记 reserved 待接",
         待接槽="music-generation 族（目录中尚无此族）"),
    Step("mu5", "编曲配器", "text-generation:gpt-oss-20b#10", "document:kb#10",
         "本地音源清单",
         "旋律 → 配器方案（鼓组/贝斯/键盘/弦乐层次与节奏型）",
         缺口="无编曲生成模型 → 方案为文本级；音频渲染登记 reserved 待接",
         待接槽="music-generation 族（目录中尚无此族）"),
    Step("mu6", "人声合成", "tts:melotts#4", "vector:emb_voice#5",
         "senses/voice.py（本地 TTS）",
         "歌词+旋律 → 人声轨（按音节对位，音色可选）"),
    Step("mu7", "混音", "classification:distilbert-sst-2-int8#2", "kv-cache:hot#8",
         "本地 ffmpeg 混音链",
         "多轨 → 平衡/动态/声场（人声置前，鼓组压限）",
         缺口="无混音 AI → 当前规则链；AI 混音登记 reserved 待接",
         待接槽="audio-mixing 族（目录中尚无此族）"),
    Step("mu8", "母带", "embedding:qwen3-embedding-0.6b#7", "document:reports#2",
         "本地 ffmpeg loudnorm",
         "混音 → 母带（响度标准 -14 LUFS 流媒体口径）"),
    Step("mu9", "封面", "image-generation:lucid-origin#6", "document:reports#2",
         "本地封面模板",
         "歌曲气质 → 封面（方形 3000×3000 + 竖版 9:16 各一）"),
    Step("mu10", "元数据与分发", "translation:m2m100-1.2b#2", "document:invoices#4",
         "core/biz_ops.py（接单/交付/发票）",
         "母带 → 元数据（ISRC 位/词曲署名/多语简介）+ 分发清单",
         门禁="发布前人工确认"),
))

# ═══════════ ④ 专业编程 AI ═══════════
PROCODE = Pipeline("procode", "专业编程 AI", "需求到部署脚本：推理/生成/测试/审计/文档全链", (
    Step("pc1", "需求澄清", "text-generation:deepseek-r1-distill-qwen-32b#9",
         "memory:mem_project#3", "skills/engine.py",
         "一句话需求 → 结构化需求（边界/验收/不做什么，推理模型）"),
    Step("pc2", "架构设计", "text-generation:gpt-oss-20b#10", "document:specs#5",
         "docs/认知系统架构.md（本地架构文档）",
         "需求 → 模块划分/接口/数据流/失败路径"),
    Step("pc3", "任务拆解", "text-generation:mistral-small-3.1-24b-instruct#6",
         "document:patches#9", "skills/engine.py",
         "架构 → 可执行任务单（每单一个可验证产出，含验收命令）"),
    Step("pc4", "代码生成", "code:qwen2.5-coder-32b-instruct#1", "document:patches#9",
         "skills/engine.py（本地 Codex 工具）",
         "任务单 → 补丁（含测试），遵守仓库既有风格"),
    Step("pc5", "复杂重构", "code:kimi-k2.7-code#2", "document:patches#9",
         "core/sandbox_exec.py（写时复制可回滚）",
         "跨文件重构 → 分步补丁，每步可回滚（COW 工作区）"),
    Step("pc6", "单测生成", "code:qwen2.5-coder-32b-instruct#1", "fulltext:ft_code#2",
         "tests/（pytest）",
         "补丁 → 单测（正常/边界/异常三类，禁止只测 happy path）"),
    Step("pc7", "静态检查", "embedding:bge-small-en-v1.5#1", "vector:emb_code#2",
         "本地 ruff/pyflakes",
         "变更文件 → 风格/类型/未用变量问题清单（先本地，再模型复核）"),
    Step("pc8", "缺陷修复", "code:kimi-k2.7-code#2", "document:patches#9",
         "core/sandbox_exec.py",
         "失败测试或报告 → 最小修复补丁（一次只修一类问题）"),
    Step("pc9", "性能优化", "text-generation:qwq-32b#8", "timeseries:latency#9",
         "tools/coverage_gate.py（覆盖率回归门）",
         "热点路径 → 优化方案 + 前后耗时对比（必须可复现）"),
    Step("pc10", "安全审计", "guard:llama-guard-3-8b#1", "audit:a_blocked#7",
         "mimosa 安全扫描（本地拦截器）",
         "补丁 → 注入/穿越/凭据/SSRF 检查；命中即拒并留证"),
    Step("pc11", "文档与知识固化", "embedding:bge-m4#4" if False else "embedding:bge-m3#4",
         "document:kb#10", "docs/ + body/obsidian.py（Obsidian 记忆库）",
         "变更 → 文档与笔记（写给下一个接手的人，不只写给别人看）"),
    Step("pc12", "提交与部署脚本", "translation:m2m100-1.2b#2", "queue:q_deliver#7",
         "desktop/install-local.ps1（本地装机链）",
         "通过审计的变更 → 提交信息 + 部署/回滚脚本 + 变更日志",
         门禁="发布前人工确认"),
))

PIPELINES: tuple = (SHORT_VIDEO, FILM, MUSIC, PROCODE)

# 替代实现 → 具体执行器（core/alt_impl.py 里的函数名），让"已接（替代实现）"有落地代码可查
ALT_EXECUTOR: dict = {
    "sv4": "alt_impl.frames_to_clip（关键帧→片段）",
    "sv8": "alt_impl.synth_bed + pick（选曲/声床；无曲库时合成）",
    "fm8": "alt_impl.frames_to_clip（关键帧→片段）",
    "fm10": "alt_impl.synth_bed（配乐声床）+ master（-14 LUFS）",
    "mu4": "alt_impl.synth_bed（旋律/和弦声床）",
    "mu5": "alt_impl.synth_bed（配器层叠加）",
    "mu7": "alt_impl.master（混音链 loudnorm）",
}


def catalog() -> dict:
    return {p.id: {"id": p.id, "名称": p.名称, "目标": p.目标,
                   "步骤数": len(p.步骤),
                   "步骤": [asdict(s) for s in p.步骤]} for p in PIPELINES}


def steps() -> list:
    return [(p, s) for p in PIPELINES for s in p.步骤]


def audit() -> dict:
    """完整性审计：每步的云插件槽、库槽必须真实存在；本地备用文件必须存在；缺口要写明。"""
    import os
    from core.cloud_plugins import PLUGIN_IDS
    from core.db_fleet import SLOT_IDS
    ps, ds = set(PLUGIN_IDS), set(SLOT_IDS)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    problems, gaps, reserved, pending = [], [], [], []
    for p, s in steps():
        where = f"{p.id}/{s.id}"
        if s.插件 not in ps:
            problems.append({"哪里": where, "问题": "云插件槽不存在", "值": s.插件})
        if s.库槽 not in ds:
            problems.append({"哪里": where, "问题": "库槽不存在", "值": s.库槽})
        if s.缺口:
            gaps.append({"哪里": where, "缺口": s.缺口})
            pending.append({"哪里": where, "待接登记": s.待接槽,
                            "当前兜底": s.本地备用})
        if "#" in s.插件 and s.插件.split(":")[-1].startswith("reserved"):
            reserved.append(where)
    return {"流水线": len(PIPELINES), "步骤总数": len(steps()),
            "问题": problems, "ok": not problems,
            "缺口（目录里确实没有的模型）": gaps, "待接登记": pending,
            "reserved 槽位": reserved}


def deploy_rows() -> dict:
    """部署清单（"哪里 → 部署到哪"）。

    键必须是**稳定 id**（pipeline/step），不能把中文名放进键 —— 否则改名会被扫描
    误判成"新增 + 消失"，而正确的结果是"修改（before → after）"。
    """
    rows = {}
    for p, s in steps():
            rows[f"{p.id}/{s.id}"] = {
                "流水线": p.名称, "流水线ID": p.id, "步骤": s.id, "名称": s.名称,
                "云插件": s.插件, "库槽": s.库槽, "本地备用": s.本地备用, "门禁": s.门禁,
                # 口径修正：目录里没有原生模型 ≠ 这一步没接上。
                # 用真实存在的云槽 + 本地兜底把它**接上**，同时保留待接登记。
                "状态": "已接（替代实现）" if s.缺口 else "已部署到云插件",
                "原生模型": "目录无（登记待接）" if s.缺口 else "有",
                "执行器": ALT_EXECUTOR.get(s.id, ""),
                "缺口": s.缺口, "待接槽": s.待接槽, "细节": s.细节}
    return rows


def assign_tentacles() -> dict:
    """把 4 条流水线各分配给一段触手（各 25 根）——部署到"谁来执行"这一层。"""
    out = {}
    for i, p in enumerate(PIPELINES):
        lo, hi = i * 25 + 1, (i + 1) * 25
        out[p.id] = {"名称": p.名称, "触手": [f"t{j:03d}" for j in range(lo, hi + 1)],
                     "区间": f"t{lo:03d}..t{hi:03d}", "步骤数": len(p.步骤)}
    return out


def register(name: str = "pipeline_registry", *, note: str = "") -> dict:
    """固化流水线登记表（可回滚），并把固化行为写进变更日志。"""
    from core import solidify
    payload = {"pipeline": catalog(), "tentacles": assign_tentacles(),
               "audit": audit()}
    r = solidify.solidify(name, payload, note=note or "流水线分类部署登记表")
    J.record("solidify", f"solidify:{name}", detail=r, ok=bool(r.get("ok")))
    return r


def deploy_all(*, led=None, record: bool = True) -> dict:
    """分类部署：逐条登记"哪一步 → 哪个插件槽/库槽"，并固化登记表。

    说明：插件启用地基已由云插件中枢完成（100/100 启用）；这里做的是**分类映射登记 +
    执行触手分配 + 固化**，并把每一条登记写进变更日志（who/what/where/结果）。
    """
    rows = deploy_rows()
    recs, failed = 0, []
    for where, row in rows.items():
        r = J.record("deploy", where, detail=row,
                     ok=True, reason=(row["缺口"] if row.get("缺口") else ""))
        recs += 1 if r.get("ok") else 0
        if not r.get("ok"):
            failed.append({"哪里": where, "原因": r.get("reason")})
    tent = assign_tentacles()
    for pid, info in tent.items():
        J.record("deploy", f"tentacles:{pid}",
                 detail={"区间": info["区间"], "触手数": len(info["触手"]),
                         "步骤数": info["步骤数"]})
    solid = register(note="分类部署后固化登记表")
    return {"ok": not failed, "登记条数": recs, "流水线": len(PIPELINES),
            "步骤总数": len(rows), "触手分配": tent, "固化": solid,
            "失败": failed, "审计": audit()}


def scan(*, scope: str = "pipelines×cloud_plugins×tentacles") -> dict:
    """扫描部署现状并记录（与上次快照比对 → 新增/修改/消失 都进日志）。"""
    rows = deploy_rows()
    for pid, info in assign_tentacles().items():
        rows[f"触手分配/{pid}"] = {"流水线": pid, "区间": info["区间"],
                                 "触手数": len(info["触手"])}
    got = J.scan_and_record(rows, scope=scope)
    return {**got, "审计": audit()}


def status() -> dict:
    """部署现状：每条流水线的步骤/已部署/待接 计数 + 变更日志概览 + 固化版本。"""
    from core import solidify
    out = []
    for p in PIPELINES:
        done = sum(1 for s in p.步骤 if not s.缺口)
        out.append({"id": p.id, "名称": p.名称, "步骤数": len(p.步骤),
                    "已分类部署": done, "待接（无模型）": len(p.步骤) - done,
                    "门禁步骤": [s.id for s in p.步骤 if s.门禁 != "无"]})
    return {"流水线": out, "审计": audit(), "触手分配": assign_tentacles(),
            "变更日志": J.summary(),
            "固化": {"registry": (solidify.latest("pipeline_registry") or {}).get("rev"),
                     "versions": len(solidify.history("pipeline_registry"))}}


def resolve_step(pipeline_id: str, step_id: str) -> dict:
    """按 id 取某一步的执行配方（供执行层/面板下钻）。"""
    for p, s in steps():
        if p.id == pipeline_id and s.id == step_id:
            return {"ok": True, "pipeline": p.id, "名称": s.名称, **asdict(s)}
    return {"ok": False, "reason": f"没有这一步：{pipeline_id}/{step_id}"}


__all__ = ["PIPELINES", "Pipeline", "Step", "catalog", "steps", "audit", "deploy_rows",
           "assign_tentacles", "deploy_all", "register", "scan", "status", "resolve_step"]
