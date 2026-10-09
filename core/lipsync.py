# core/lipsync.py —— 口型时间轴：让"嘴"跟着"发音"走（不是随便张合）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令（2026-10-09）：「只要动作自然，**嘴和发音一致**就行了，别看起来很假。」
#
# 怎么做到"一致"（本仓无逐字音素对齐，所以按可复核的近似口径做，并如实标注）：
#   ① 中文按**拼音首/韵母 → 口型类**映射（A/E/I/O/U/M/F 七类，卡通口型标准做法）；
#   ② 每个字的时间片 = 音频总时长 ÷ 字数（等分近似；有逐字时间戳时用真时间戳覆盖）；
#   ③ 标点/停顿 → 闭嘴帧（M），让呼吸与停顿有空档 —— 这一点最影响"假不假"；
#   ④ 输出 [{起, 止, 口型, 字}]，前后端都按它切嘴。
# 🔴 诚实：这是**近似口型**（等分时间 + 拼音映射），不是声学强制对齐；
#   要毫秒级精准需要 ASR 的逐字时间戳（本仓 voice_sapi 只给整句文本，给不了时间戳）。
from __future__ import annotations

# 口型类（7 类，够表达中文）：A 大张 / E 中扁 / I 扁长 / O 圆 / U 收圆 / M 闭 / F 唇齿
VISEMES = ("A", "E", "I", "O", "U", "M", "F")

# 韵母 → 口型（按发音开口度与唇形归类；同韵合并，便于逐个复核）
_FINAL = {
    "a": "A", "ia": "A", "ua": "A", "ang": "A", "iang": "A", "uang": "A",
    "ai": "A", "uai": "A", "ao": "A", "iao": "A", "an": "A", "ian": "A", "uan": "A",
    "e": "E", "er": "E", "ei": "E", "ie": "E", "ue": "E", "en": "E", "eng": "E",
    "i": "I", "in": "I", "ing": "I",
    "o": "O", "ong": "O", "iong": "O", "ou": "O", "uo": "O",
    "u": "U", "ui": "U", "un": "U", "iu": "U",
}
_INITIAL_LIP = {"f": "F"}          # 唇齿音：f 一出来就是 F 口型
_PAUSE = "，。！？；：、,.!?;:…—\n "


def _pinyin_like(ch: str) -> str:
    """无第三方拼音库时的**大字表**兜底：常见字给韵母，生僻字给中性。"""
    table = {
        "你": "i", "好": "ao", "我": "o", "是": "i", "的": "e", "不": "u", "在": "ai",
        "了": "e", "吗": "a", "很": "en", "会": "ui", "说": "uo", "话": "ua", "看": "an",
        "来": "ai", "去": "u", "这": "e", "那": "a", "什么": "en", "谢": "ie", "请": "ing",
        "对": "ui", "起": "i", "再": "ai", "见": "ian", "早": "ao", "晚": "an", "安": "an",
        "天": "ian", "气": "i", "听": "ing", "觉": "ue", "得": "e", "知": "i", "道": "ao",
    }
    return table.get(ch, "")


def viseme_of(ch: str) -> str:
    """一个字 → 口型类。"""
    if ch in _PAUSE:
        return "M"
    f = _pinyin_like(ch)
    if not f:
        f = "e" if ch.isascii() else "a"      # 兜底：中文默认 A（开口），英文默认 E
    if f.startswith("f"):
        return "F"
    return _FINAL.get(f, "E")


def timeline(text: str, duration: float, *, stamps: list | None = None) -> dict:
    """文字 + 音频时长 → 口型时间轴。
",
    stamps: 有逐字时间戳时传 [[字, 起, 止], …]（真对齐优先，无则等分）。
    """
    t = str(text or "")
    chars = [c for c in t if c != "\r"]
    if not chars:
        return {"ok": False, "reason": "空文本"}
    d = max(0.2, float(duration or 0))
    out = []
    if stamps:
        for it in stamps:
            ch = str(it[0]); a = float(it[1]); b = float(it[2])
            out.append({"起": round(a, 3), "止": round(b, 3), "口型": viseme_of(ch), "字": ch})
    else:
        step = d / len(chars)
        for i, ch in enumerate(chars):
            out.append({"起": round(i * step, 3), "止": round((i + 1) * step, 3),
                        "口型": viseme_of(ch), "字": ch})
    # 相邻同口型合并（少切帧，看起来更自然）
    merged = []
    for f in out:
        if merged and merged[-1]["口型"] == f["口型"] and abs(merged[-1]["止"] - f["起"]) < 0.02:
            merged[-1]["止"] = f["止"]
            merged[-1]["字"] += f["字"]
        else:
            merged.append(dict(f))
    return {"ok": True, "时长": round(d, 3), "字数": len(chars), "帧数": len(merged),
            "轴": merged, "用真时间戳": bool(stamps),
            "口径": "等分时间 + 拼音韵母映射（近似口型）；有逐字时间戳时以真戳为准"}


def stats() -> dict:
    from collections import Counter
    demo = "你好，我是小土豆。今天天气不错，要不要一起看看这个展览？"
    tl = timeline(demo, 6.0)
    c = Counter(f["口型"] for f in tl["轴"])
    return {"口型类": list(VISEMES), "示例句": demo, "示例帧数": tl["帧数"],
            "口型分布": dict(c), "口径": tl["口径"]}


__all__ = ["VISEMES", "viseme_of", "timeline", "stats"]
