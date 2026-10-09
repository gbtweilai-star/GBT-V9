# skills/terminology.py —— 术语 Skill：教你怎么说（口语 → 术语 → 能力 → 参数）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么需要它：主人说的是人话（「鼠标放上去那张大一点」），而工程要的是术语
# （hover 抬起 + 邻位让位）。这一层负责把口语**翻译成术语再交给实现**，并在说不准时
# **教主人怎么说**（suggest），而不是硬猜一个动作就动手。
#
# 纪律：① 匹配不到就说匹配不到（给候选与更准的说法），绝不硬翻译；
#       ② 术语表可扩展（TERMS_EXTRA_JSON），不自作主张改主人的词；
#       ③ 每个术语都标它属于哪个技能（skill），实现由那个技能负责（本模块只翻译）。
import json
import os
import re
from dataclasses import dataclass, field, asdict


@dataclass
class Term:
    term: str                     # 术语（工程口径）
    intent: str                   # 意图 id（实现技能认得的 key）
    skill: str                    # 由哪个技能负责实现
    params: dict = field(default_factory=dict)
    say: str = ""                 # 更准的说法（教主人怎么说）
    aliases: tuple = ()           # 口语别名（含主人原话的常见说法）
    note: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["aliases"] = list(self.aliases)
        return d


# ── 术语表：按技能分区 ──
TERMS: tuple = (
    # ── UI 设计 Skill（视觉与交互）──
    Term("悬停抬起 + 邻位让位", "hover_lift_yield", "ui.design",
         {"effect": "lift", "lift_px": 4, "neighbors": "yield", "duration_ms": 160},
         say="「鼠标放上去，卡片抬起来一点，旁边的给它让位」",
         aliases=("鼠标放上去那张大一点", "鼠标放上去大一点", "悬停变大", "hover 变大",
                  "鼠标移上去放大", "放上去那长大"),
         note="抬起用 transform，不用改宽高（否则会触发重排、抖）"),
    Term("悬停高亮描边", "hover_outline", "ui.design",
         {"effect": "outline", "color": "accent", "width": 1},
         say="「鼠标放上去加一圈边框」",
         aliases=("放上去加个边框", "悬停描边", "鼠标放上去有个框")),
    Term("对比度达标", "contrast_ok", "ui.design",
         {"min_ratio": 4.5, "level": "AA"},
         say="「文字要看得清（对比度达标）」",
         aliases=("看不清", "字太淡", "对比度不够", "颜色太浅")),
    Term("间距上刻度", "spacing_on_scale", "ui.design",
         {"scale": (4, 8, 12, 16, 24, 32)},
         say="「间距用 4 的倍数」",
         aliases=("间距乱", "间距不齐", "间距对齐一下", "留白不舒服")),
    Term("层级分明", "hierarchy_clear", "ui.design",
         {"max_levels": 3},
         say="「重要的更醒目，次要的更安静（最多三层）」",
         aliases=("看着乱", "没重点", "层级不清", "分不清主次")),
    Term("空态要有话说", "empty_state_honest", "ui.design",
         {"kind": "empty"},
         say="「没有数据的时候要写清楚为什么没有」",
         aliases=("空白一片", "什么都没有", "空的时候显示啥")),
    Term("加载要有进度", "loading_progress", "ui.design",
         {"kind": "progress"},
         say="「等待的时候给个进度或转圈」",
         aliases=("卡住了", "不知道在干嘛", "没反应")),

    # ── 只读工具/语音（数据与播报）──
    Term("问一下当前读数", "read_snapshot", "body.tools",
         {"domains": ("devour", "scan", "queue", "witness")},
         say="「问一下吞噬能/扫描覆盖/队列/见证现在什么情况」",
         aliases=("现在什么情况", "有没有丢帧", "覆盖率多少", "队列堵了吗", "见证几票")),
    Term("念给我听", "voice_say", "voice",
         {"voice": "文静台湾腔"},
         say="「念给我听（用文静台湾腔）」",
         aliases=("念一下", "说出来", "语音播报", "读给我听")),

    # ── 桌面操控（执行域）──
    Term("点这个元素", "click_element", "exec.gui_agent",
         {"need_vision": True},
         say="「点第 3 个元素 / 点左上角的保存」",
         aliases=("点一下这个", "点那个按钮", "帮我点")),
    Term("输入一段字", "type_text", "exec.gui_agent",
         {"mode": "human"},
         say="「在光标处输入：xxx」",
         aliases=("打一段字", "输入文字", "帮我打字")),
    Term("看屏再决定", "observe_first", "exec.gui_agent",
         {"vision_only": True},
         say="「先看一眼屏幕再决定怎么做」",
         aliases=("先看看", "看下屏幕", "看看再动手")),

    # ── 业务（接单交付变现）──
    Term("报个价", "biz_quote", "biz",
         {"explainable": True},
         say="「按工时 10 小时、时薪 300 报个价」",
         aliases=("多少钱", "报价", "这个收多少", "怎么算钱")),
    Term("开账单", "biz_invoice", "biz", {"kind": "milestone"},
         say="「开一张 M2 的账单，30 天账期」",
         aliases=("要钱", "开发票", "开票", "催款")),

    # ── 记忆与知识（Obsidian）──
    Term("记到永久记忆里", "vault_remember", "obsidian",
         {"scope": "tentacle"},
         say="「把这条记到触手的永久记忆里」",
         aliases=("记住这个", "存起来", "记笔记", "别忘了")),
    Term("翻一下以前的记录", "vault_recall", "obsidian", {"limit": 20},
         say="「翻一下以前关于 xx 的记录」",
         aliases=("之前怎么做的", "历史记录", "以前记过吗")),
)


def _extra_terms() -> list:
    """TERMS_EXTRA_JSON：[{term,intent,skill,params,say,aliases}] 主人可自行加词。"""
    raw = os.environ.get("TERMS_EXTRA_JSON", "").strip()
    if not raw:
        return []
    try:
        out = []
        for item in json.loads(raw):
            out.append(Term(term=item.get("term", ""), intent=item.get("intent", ""),
                            skill=item.get("skill", ""), params=item.get("params") or {},
                            say=item.get("say", ""),
                            aliases=tuple(item.get("aliases") or ()),
                            note=item.get("note", "")))
        return out
    except Exception:                                        # noqa: BLE001
        return []


def all_terms() -> tuple:
    return TERMS + tuple(_extra_terms())


def _norm(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～]+", "", str(text or "")).lower()


# 关键词兜底：自然说法常把别名拆开（「翻一下以前关于扫描的记录」），
# 别名匹配不到时按关键词理解，并**如实标注** matched_by=keyword（不冒充精确命中）。
KEYWORDS: dict = {
    "vault_recall": ("翻", "记录", "以前", "历史", "记过", "查过", "找一下"),
    "vault_remember": ("记住", "记下", "记到", "存起来", "笔记", "别忘", "永久记忆"),
    "read_snapshot": ("情况", "覆盖率", "丢帧", "队列", "见证", "读数", "怎么样", "现状"),
    "biz_quote": ("报价", "多少钱", "收费", "算钱"),
    "biz_invoice": ("开票", "账单", "催款", "要钱", "回款"),
    "voice_say": ("念", "读给我", "说出来", "播报"),
    "observe_first": ("先看", "看看屏幕", "看一眼"),
    "click_element": ("点一下", "点那个", "点这个", "按钮", "点开", "点它"),
    "type_text": ("输入", "打字", "填一下", "写上", "键入"),
}


def _by_keyword(n: str) -> tuple:
    """返回 (score, Term, hits)。

    评分：**长关键词（≥3 字）一票即中**（「永久记忆」「覆盖率」这类词本身就有指向性）；
    短关键词需两票。宁可漏，不可硬猜 —— 分数不够就老实说没听懂。
    """
    best, best_score, best_hits = None, 0.0, []
    for t in all_terms():
        kws = KEYWORDS.get(t.intent)
        if not kws:
            continue
        hits = [k for k in kws if k in n]
        if not hits:
            continue
        strong = [k for k in hits if len(k) >= 3]
        score = 1.0 if strong else min(1.0, len(hits) / 2.0)
        if score > best_score:
            best, best_score, best_hits = t, score, hits
    return best_score, best, best_hits


def translate(text: str, *, threshold: float = 0.5) -> dict:
    """口语 → 术语。匹配不到就**如实说匹配不到**，并给候选与更准的说法。"""
    raw = str(text or "").strip()
    n = _norm(raw)
    if not n:
        return {"raw": raw, "matched": False, "reason": "empty"}
    best, best_score = None, 0.0
    for t in all_terms():
        cands = (t.term,) + tuple(t.aliases)
        for c in cands:
            cn = _norm(c)
            if not cn:
                continue
            if cn == n:
                score = 1.0
            elif cn in n or (len(cn) >= 3 and n in cn):
                score = 0.75 + 0.05 * min(len(cn), len(n)) / max(len(cn), len(n))
            else:
                common = len(set(cn) & set(n))
                score = (common / max(len(set(cn)), len(set(n)))) * 0.6
            if score > best_score:
                best, best_score = t, score
    if best is None or best_score < threshold:
        # ② 关键词兜底：别名没命中时按关键词理解（如实标注，不冒充精确命中）
        ratio, kterm, hits = _by_keyword(n)
        if kterm is not None and ratio >= 0.5:
            return {"raw": raw, "matched": True, "matched_by": "keyword",
                    "score": round(ratio, 3), "keywords": hits,
                    "term": kterm.term, "intent": kterm.intent, "skill": kterm.skill,
                    "params": dict(kterm.params), "note": kterm.note,
                    "say_back": f"我按关键词（{'、'.join(hits)}）理解为：{kterm.term}"
                                + (f"。更准的说法：{kterm.say}" if kterm.say else "")}
        return {"raw": raw, "matched": False, "reason": "no_term",
                "candidates": [t.term for t in all_terms()][:12],
                "hint": "说清「做什么 + 在哪 + 什么程度」最省事，例如："
                        "「卡片悬停时抬起 4 像素，旁边卡片让位」"}
    return {"raw": raw, "matched": True, "matched_by": "alias",
            "score": round(best_score, 3),
            "term": best.term, "intent": best.intent, "skill": best.skill,
            "params": dict(best.params), "note": best.note,
            "say_back": f"我听懂的是：{best.term}（由 {best.skill} 实现）"
                        + (f"。更准的说法：{best.say}" if best.say else "")}


def explain(term_or_intent: str) -> dict:
    """术语 → 人话（教主人怎么说，也教她自己怎么讲）。"""
    q = str(term_or_intent or "").strip()
    for t in all_terms():
        if q in (t.term, t.intent):
            return {"term": t.term, "intent": t.intent, "skill": t.skill,
                    "plain": t.say or t.term, "params": dict(t.params), "note": t.note}
    return {"error": f"没有这个术语：{q}", "terms": [t.term for t in all_terms()]}


def suggest(text: str, *, limit: int = 5) -> list:
    """说不准时教怎么说：按字面相似度给最接近的术语建议。"""
    n = _norm(text)
    scored = []
    for t in all_terms():
        pool = (t.term,) + tuple(t.aliases)
        best = max((len(set(_norm(c)) & set(n)) / max(1, len(set(_norm(c)))) for c in pool),
                   default=0.0)
        scored.append((round(best, 3), t))
    scored.sort(key=lambda x: -x[0])
    return [{"term": t.term, "skill": t.skill, "say": t.say, "score": s}
            for s, t in scored[:limit]]


def catalog() -> dict:
    by_skill: dict = {}
    for t in all_terms():
        by_skill.setdefault(t.skill, []).append({"term": t.term, "intent": t.intent,
                                                 "say": t.say})
    return {"count": len(all_terms()), "by_skill": by_skill}


__all__ = ["Term", "TERMS", "all_terms", "translate", "explain", "suggest", "catalog"]
