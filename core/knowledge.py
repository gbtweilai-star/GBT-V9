# core/knowledge.py —— 三本书的蒸馏知识层（可执行，不是笔记）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-07）：把《AIGC提示词美学定义》《AI短剧全流程创作》《Agentic智能体
#   设计模式》三本书蒸馏成**最专业最细节化的操作知识**，进 GBT小土豆V9 框架。
#
# 蒸馏口径：提炼的是**方法论与操作参数**（可执行、可调用、可验收），不是复述书原文；
#   每条知识都带"怎么用"——接到流水线/数字人/触手上才叫进框架。
#
# 三个域：
#   aesthetic  《AIGC提示词美学定义》→ 结构化提示词引擎（八大美学维度 + 负面词 + 打分）
#   shortdrama 《AI短剧全流程创作》→ 短剧全流程操作手册（钩子公式/分镜表/运镜/节奏/验收）
#   agentic    《Agentic智能体设计模式》→ 六大模式对照 V9 落点（含缺口清单）
import re

# ══════════════════ 域一：AIGC 提示词美学（提示词引擎） ══════════════════
LIGHTING = {
    "黄金时刻": "golden hour backlight, warm rim light",
    "伦勃朗光": "Rembrandt lighting, soft shadow triangle on cheek",
    "轮廓光": "strong rim light, glowing edge separation",
    "霓虹": "neon lighting, magenta and cyan reflections",
    "电影感": "cinematic lighting, volumetric haze, high contrast",
    "自然窗光": "soft window light, natural falloff",
}
CAMERA = {
    "大特写": "extreme close-up, facial detail",
    "特写": "close-up shot",
    "中景": "medium shot, waist up",
    "全景": "wide shot, full body in environment",
    "航拍": "aerial drone view",
    "荷兰角": "dutch angle, tilted horizon, tension",
    "低角度": "low angle shot, heroic",
}
COMPOSITION = {
    "三分法": "rule of thirds",
    "居中对称": "centered symmetrical composition, Wes Anderson style",
    "引导线": "leading lines composition",
    "框架中框架": "framed through foreground elements",
    "负空间": "minimalist negative space",
}
COLOR = {
    "赛博霓虹": "cyberpunk neon palette, teal and magenta",
    "莫兰迪": "muted Morandi palette, low saturation",
    "高饱和国漫": "vivid Chinese animation palette, rich gradients",
    "黑白": "black and white, film noir contrast",
    "黄昏橙青": "teal and orange grade",
}
STYLE = {
    "新海诚": "Makoto Shinkai style, luminous skies, hyper-detailed clouds",
    "吉卜力": "Studio Ghibli style, hand-painted warmth",
    "写实电影": "photorealistic cinematic still, 35mm film grain",
    "国风水墨": "Chinese ink wash painting, ethereal mist",
    "3D皮克斯": "Pixar style 3D render, soft global illumination",
    "暗黑奇幻": "dark fantasy concept art, dramatic atmosphere",
}
NEGATIVE = ("lowres, blurry, deformed hands, extra fingers, watermark, text artifacts, "
            "bad anatomy, jpeg artifacts, oversaturated, duplicated faces")

DIMENSIONS = ("主体", "场景", "光线", "镜头", "构图", "色彩", "风格", "负面词")


def build_prompt(*, subject: str, scene: str = "", lighting: str = "电影感",
                 camera: str = "中景", composition: str = "三分法",
                 color: str = "电影感", style: str = "写实电影",
                 extra: str = "") -> dict:
    """结构化提示词引擎：八大美学维度 → 中文思路 + 英文成稿 + 负面词。

    书的核心方法：好提示词不是堆词，是**八个维度各就各位**。
    每个维度给的是词库真实键，拼错会 KeyError → 写错立即暴露。
    """
    for name, table, key in (("光线", LIGHTING, lighting), ("镜头", CAMERA, camera),
                             ("构图", COMPOSITION, composition), ("色彩", COLOR, color),
                             ("风格", STYLE, style)):
        if key not in table:
            return {"ok": False, "reason": f"{name}维度没有「{key}」；可选：{list(table)}"}
    en = ", ".join([subject, scene, LIGHTING[lighting], CAMERA[camera],
                    COMPOSITION[composition], COLOR[color], STYLE[style], extra]) \
        if extra else ", ".join([subject, scene, LIGHTING[lighting], CAMERA[camera],
                                 COMPOSITION[composition], COLOR[color], STYLE[style]])
    zh = f"{subject}｜{scene}｜{lighting}｜{camera}｜{composition}｜{color}｜{style}"
    return {"ok": True, "中文思路": zh, "英文成稿": en, "负面词": NEGATIVE,
            "维度": {k: v for k, v in (("主体", subject), ("场景", scene),
                                       ("光线", lighting), ("镜头", camera),
                                       ("构图", composition), ("色彩", color),
                                       ("风格", style))}}


def score(prompt: str) -> dict:
    """给一条已有提示词打美学分：八个维度各占一项，缺了点名（书的方法=逐维检查）。"""
    p = str(prompt or "").lower()
    hit = {
        "主体": bool(p.strip()), "场景": any(k in p for k in ("背景", "场景", "环境", "street", "room", "forest")),
        "光线": any(k in p for k in ("光", "light", "lighting", "glow", "backlight")),
        "镜头": any(k in p for k in ("特写", "远景", "shot", "angle", "close-up", "wide")),
        "构图": any(k in p for k in ("构图", "composition", "thirds", "symmetr", "negative space")),
        "色彩": any(k in p for k in ("色", "color", "palette", "neon", "teal", "monochrome")),
        "风格": any(k in p for k in ("风格", "style", "cinematic", "ghibli", "shinkai", "ink")),
        "负面词": "negative" in p or "低质" in p,
    }
    return {"命中": hit, "得分": f"{sum(hit.values())}/8",
            "缺": [k for k, v in hit.items() if not v],
            "口径": "缺哪维补哪维——这就是《美学定义》的逐维检查法"}


# ══════════════════ 域二：AI 短剧全流程（操作手册） ══════════════════
STAGES = (
    {"stage": "选题", "要点": "垂直题材 + 情绪钩子 + 更新频率", "产出": "选题卡（题材/受众/爽点）"},
    {"stage": "剧本", "要点": "三幕结构；每集结尾必留钩子；对白短句化", "产出": "分集剧本"},
    {"stage": "分镜", "要点": "镜号/景别/运镜/时长/台词/音效 六栏缺一不可", "产出": "分镜表"},
    {"stage": "角色设定", "要点": "主角一致性（外观锚点固定，跨镜头不漂移）", "产出": "角色锚点卡"},
    {"stage": "生图/生视频", "要点": "每镜一条结构化提示词（用 aesthetic.build_prompt）+ 负面词", "产出": "逐镜素材"},
    {"stage": "配音", "要点": "语速匹配情绪；台湾腔通道已内置", "产出": "配音轨"},
    {"stage": "音效/音乐", "要点": "转场卡点；情绪段落配乐分层", "产出": "音效轨"},
    {"stage": "剪辑节奏", "要点": "前3秒钩子、每15秒一个转折、卡点切", "产出": "粗剪→精剪"},
    {"stage": "特效", "要点": "特效服务叙事，不炫技；转场统一风格", "产出": "特效层"},
    {"stage": "字幕", "要点": "竖屏安全区；台词逐句对齐；关键词强调", "产出": "字幕轨"},
    {"stage": "成片验收", "要点": "时长/比例/响度/字幕/素材来源 五查", "产出": "验收单"},
)
CAMERA_MOVES = ("推", "拉", "摇", "移", "跟", "甩", "升降", "环绕")
TRANSITIONS = ("硬切", "叠化", "遮罩转场", "匹配剪辑", "甩镜转场", "黑场")
HOOK_FORMULA = ("前3秒放冲突或结果", "第15秒给反转", "每30秒一个新钩子", "结尾留悬念引导下集")


def storyboard(episode: str, *, shots: int = 6) -> dict:
    """按专业六栏分镜表生成模板，每镜已配运镜/景别建议（可改）。"""
    景别 = ("大特写", "中景", "全景", "特写", "中景", "全景")
    运镜 = ("固定", "缓推", "横移", "跟拍", "甩", "升降")
    rows = []
    for i in range(max(1, int(shots))):
        rows.append({"镜号": i + 1, "景别": 景别[i % len(景别)], "运镜": 运镜[i % len(运镜)],
                     "时长s": 3 if i == 0 else 5,      # 开头 3 秒钩子（书里的铁律）
                     "画面": f"第{i + 1}镜画面（待填：用 aesthetic.build_prompt 生成提示词）",
                     "台词": "", "音效": ""})
    return {"剧集": str(episode)[:40], "分镜表": rows,
            "钩子公式": list(HOOK_FORMULA),
            "验收": "时长/比例/响度/字幕/素材来源 五查（接 workflows.acceptance）",
            "口径": "六栏缺一不可；每镜提示词走美学引擎（域一）"}


# ══════════════════ 域三：Agentic 设计模式（对照 V9 落点） ══════════════════
PATTERNS = (
    {"模式": "反思 Reflection", "V9落点": "core/memory/metacog.py（元认知）+ workflows 审查段",
     "状态": "已实现"},
    {"模式": "工具使用 Tool Use", "V9落点": "body.tools 只读面 + skills.caps 能力注册表 + 插件工具",
     "状态": "已实现"},
    {"模式": "规划 Planning / ReAct", "V9落点": "core/commander.py（计划→编译→派发→验收）",
     "状态": "已实现"},
    {"模式": "多智能体协作", "V9落点": "core/orchestration/team.py + 100 触手编队 + 分片调度",
     "状态": "已实现"},
    {"模式": "记忆 Memory", "V9落点": "core/memory（统一记忆 + 热度 + 生命起源存档）",
     "状态": "已实现"},
    {"模式": "护栏 Guardrails", "V9落点": "core/hooks（防偷懒钩子）+ market_research 闸门 + 授权闸门",
     "状态": "已实现"},
    {"模式": "经验学习 Learning", "V9落点": "core/memory（教训分类 + 热度）", "状态": "部分"},
    {"模式": "规模化并行 Scale", "V9落点": "触手编队 100→可扩；分片/资源池语义已留",
     "状态": "部分"},
)


def agentic_map() -> dict:
    return {"模式数": len(PATTERNS),
            "已实现": sum(1 for p in PATTERNS if p["状态"] == "已实现"),
            "部分": [p["模式"] for p in PATTERNS if p["状态"] != "已实现"],
            "项": PATTERNS,
            "口径": "每个模式都标 V9 落点——书上的模式在我们框架里长在哪，一眼看清"}


def status() -> dict:
    return {"域": {"aesthetic": "提示词美学引擎（8 维 + 负面词 + 打分）",
                   "shortdrama": "短剧全流程（11 阶段 + 六栏分镜 + 钩子公式）",
                   "agentic": "设计模式对照（6/8 已实现）"},
            "口径": "蒸馏的是方法论与操作参数，全部可执行可调用，不是读书笔记"}


# ══════════════════ 进框架：接大脑 + 接流水线 ══════════════════
def onboard() -> dict:
    """把三域知识**记进原生大脑**（分类=做法），供触手召回。"""
    from core.memory import brain as B
    recs = []
    for title, text, cat in (
        ("提示词美学·八维法",
         "好提示词按八维检查：主体/场景/光线/镜头/构图/色彩/风格/负面词，缺哪维补哪维。"
         "用 core.knowledge.build_prompt 生成，score 打分。", "做法"),
        ("短剧·前3秒钩子铁律",
         "第1镜3秒内必须给冲突或结果；每15秒一个转折；结尾留钩子。分镜表六栏缺一不可："
         "镜号/景别/运镜/时长/台词/音效。用 core.knowledge.storyboard 生成。", "做法"),
        ("Agentic·六大模式",
         "反思/工具使用/规划/多智能体/记忆/护栏六模式在 V9 已有落点；经验学习与规模化为部分。"
         "用 core.knowledge.agentic_map 查落点。", "做法"),
    ):
        # 标题要进原文：召回按措辞找，标题里有"前3秒/铁律"这类关键词才搜得到（真踩过）
        r = B.remember(f"{title}：{text}", owner="main", origin="knowledge", scope="技能",
                       category=cat, meta={"书": title})
        recs.append({"知识": title, "id": r.get("id")})
    return {"ok": True, "入脑": recs,
            "口径": "知识进大脑后，触手提问即可召回（brain.ask）"}


__all__ = ["LIGHTING", "CAMERA", "COMPOSITION", "COLOR", "STYLE", "NEGATIVE", "DIMENSIONS",
           "build_prompt", "score", "STAGES", "CAMERA_MOVES", "TRANSITIONS", "HOOK_FORMULA",
           "storyboard", "PATTERNS", "agentic_map", "status", "onboard"]
