"""韵律与发声样式：把 (文本 + 情绪) 渲染成 TTS 可用参数与 SSML。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape as _xml_escape


@dataclass(frozen=True)
class VoiceStyle:
    name: str
    rate: float = 1.0          # 1.0 = 常速
    pitch: float = 0.0         # 半音
    volume: float = 0.9        # 0..1
    pause_ms: int = 260
    interjections: tuple[str, ...] = ()
    softeners: tuple[str, ...] = ()


STYLES: dict[str, VoiceStyle] = {
    "温柔": VoiceStyle("温柔", 0.95, 0.5, 0.85, 320, ("嗯，", "来，"), ("呢。", "好吗？")),
    "干练": VoiceStyle("干练", 1.08, 0.0, 1.0, 180),
    "兴奋": VoiceStyle("兴奋", 1.18, 3.0, 1.05, 150, ("太好了！", "成了！")),
    "严肃": VoiceStyle("严肃", 0.92, -1.0, 1.0, 300, ("注意。",)),
    "疲惫": VoiceStyle("疲惫", 0.88, -1.5, 0.8, 380, ("嗯……",)),
    "共情": VoiceStyle("共情", 0.94, 0.5, 0.85, 360, ("我明白。", "别急。")),
    "俏皮": VoiceStyle("俏皮", 1.12, 2.0, 1.0, 200, ("嘿，",)),
    "平和": VoiceStyle("平和", 1.0, 0.0, 0.9, 260),
    # ★主人 2026-10-06 点名：文静台湾腔 —— 语速放慢、音高略抬、句尾软收（喔/啦/耶/好不好）
    "文静台湾腔": VoiceStyle(
        "文静台湾腔", rate=0.90, pitch=1.2, volume=0.82, pause_ms=340,
        interjections=("嗯，", "欸，", "那个，"),
        softeners=("喔。", "啦。", "耶。", "呢。", "好不好？")),
    # ★主人 2026-10-07："以可爱的性格泼辣的性格，能讨好也能发火骂人，固定女声台湾腔"
    #   两档都长在台湾腔上，只动韵律起伏：讨好=更甜更高更黏，发火=更快更冲更重。
    "撒娇讨好": VoiceStyle(
        "撒娇讨好", rate=1.04, pitch=2.6, volume=0.88, pause_ms=300,
        interjections=("主人～", "欸拜托，", "人家"),
        softeners=("嘛～", "好不好？", "啦～", "耶～")),
    "泼辣发火": VoiceStyle(
        "泼辣发火", rate=1.06, pitch=1.0, volume=1.0, pause_ms=200,
        interjections=("齁，", "欸你嘛，", "厚！"),
        softeners=("耶！", "啦！", "喔！", "好不好！")),
}

# 默认口音（可用 VOICE_STYLE 换名；VOICE_STYLE_STRICT=1 则一直用它，不随情绪切）
DEFAULT_STYLE = os.environ.get("VOICE_STYLE", "文静台湾腔")

_MOOD_STYLE = {"喜": "温柔", "乐": "俏皮", "怒": "干练",
               "哀": "共情", "惊": "严肃", "平": "平和"}


def choose_style(mood: str, intensity: float, purpose: str) -> str:
    """用途/情绪优先；不明确时回到默认口音（文静台湾腔）。"""
    if os.environ.get("VOICE_STYLE_STRICT") == "1":
        return DEFAULT_STYLE
    if purpose == "alert":
        return "严肃" if intensity >= 0.30 else "平和"
    if purpose == "comfort":
        return "共情"
    if purpose == "celebrate":
        return "兴奋"
    if purpose == "chitchat":
        return "俏皮" if mood == "乐" else "温柔"
    if mood in _MOOD_STYLE and intensity >= 0.25:
        return _MOOD_STYLE[mood]
    return DEFAULT_STYLE


def escape(text: str) -> str:
    return _xml_escape(text, {'"': "&quot;", "'": "&apos;"})


@dataclass
class ProsodyResult:
    text: str            # 纯文本（引擎不支持 SSML 时用）
    ssml: str
    rate: float
    pitch: float
    volume: float
    style: str


def interpolate(style: VoiceStyle, intensity: float) -> tuple[float, float, float]:
    """情绪越强，韵律越偏离常速基线（越有戏）。"""
    amp = 0.5 + 0.5 * intensity
    rate = 1.0 + (style.rate - 1.0) * amp
    pitch = style.pitch * amp
    volume = 0.9 + (style.volume - 0.9) * amp
    return round(rate, 3), round(pitch, 2), round(volume, 3)


def decorate(text: str, style: VoiceStyle, intensity: float) -> str:
    out = text.strip()
    if style.interjections and intensity >= 0.25:
        out = style.interjections[len(text) % len(style.interjections)] + out
    # 语气尾（喔/啦/耶/好不好）是"态度"的一部分：低强度也保留，否则听起来没有人味
    if style.softeners and intensity >= 0.15:
        out = out.rstrip("。！!") + style.softeners[len(text) % len(style.softeners)]
    return out


def to_ssml(text: str, rate: float, pitch: float, volume: float) -> str:
    rate_pct = f"{int(round((rate - 1.0) * 100)):+d}%"
    return ('<speak><prosody rate="{rate}" pitch="{pitch:+.1f}st" volume="{vol}%">'
            '{body}</prosody></speak>').format(
                rate=rate_pct, pitch=pitch, vol=int(round(volume * 100)),
                body=escape(text))


def render(text: str, style: VoiceStyle | str, *, intensity: float = 0.0,
           add_interjection: bool = True, ssml: bool = True) -> ProsodyResult:
    if isinstance(style, str):
        style = STYLES.get(style, STYLES["平和"])
    rate, pitch, volume = interpolate(style, intensity)
    spoken = decorate(text, style, intensity) if add_interjection else text.strip()
    return ProsodyResult(
        text=spoken,
        ssml=to_ssml(spoken, rate, pitch, volume) if ssml else spoken,
        rate=rate, pitch=pitch, volume=volume, style=style.name)
