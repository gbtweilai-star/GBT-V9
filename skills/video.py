# skills/video.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 每个能力都必须声明 spec(in/out); run() 只输出【已核验】的结果,
#       未核验一律 unknown, 不许把"点了按钮"当成"做成了"; 剪映无公开 API,
#       全部经触手/脉冲 UI 自动化, 界面变了就暂停回报卡点, 不盲点。
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

class NativeSkill(Protocol):
    name: str
    version: str
    def spec(self) -> dict: ...
    def probe(self) -> dict: ...          # 界面/依赖可用性探测
    async def run(self, ctx, **kwargs) -> dict: ...


# ── 通用 spec 片段：所有能力共享同一套引用与错误码 ──
INPUT_COMMON = {
    "project_id": {"type": "string"},
    "assets":      {"type": "array", "items": {"type": "string"}},   # 只放引用, 不放路径/密钥
    "timeline":    {"type": "array"},
    "prefs":       {"type": "object"},
}
OUTPUT_COMMON = {
    "ok":         {"type": "boolean"},
    "artifact":   {"type": "string"},        # 产物引用（非路径）
    "verified":   {"type": "boolean"},       # 是否经吞噬能/界面核验
    "error_code": {"type": ["string", "null"]},
}


def _spec(name, desc, props_in, props_out):
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "additionalProperties": False,
                           "properties": {**INPUT_COMMON, **props_in}, "required": ["project_id"]},
            "returns":   {"type": "object",
                          "properties": {**OUTPUT_COMMON, **props_out},
                          "required": ["ok", "verified"]}}


# ── 1) 剪辑 ────────────────────────────────────────────────
@dataclass
class VideoEdit:
    name = "video.edit"
    version = "1.0.0"

    def spec(self):
        return _spec(self.name, "对时间线做剪辑: 裁切/分割/删除/变速/倒放/关键帧裁剪",
            {"ops": {"type": "array", "items": {
                "type": "object", "properties": {
                    "op":   {"enum": ["cut", "split", "delete", "speed", "reverse"]},
                    "at_s": {"type": "number"},
                    "to_s": {"type": "number"},
                    "rate": {"type": "number"}}}}},
            {"timeline_id": {"type": "string"}, "op_log": {"type": "array"}})

    def probe(self):
        return {"editor": "jianying/capcut", "ready": self._ui_ready()}

    async def run(self, ctx, *, project_id, ops, **_) -> dict:
        log = []
        for op in ops:
            # ── 触手 UI 自动化: 定位控件 → 操作 → 等待 → 截图识别 → 验证 ──
            ok = await ctx.touch.act("editor.timeline", op)      # 每步都返回真读数
            if not ok:
                return {"ok": False, "verified": False,
                        "error_code": f"ui_step_failed:{op['op']}", "op_log": log}
            log.append(op)
        # 校验: 用吞噬能采帧确认时间线真的变了（不是按钮点歪了）
        verified = await ctx.devour.verify_timeline(project_id, expect=ops)
        return {"ok": True, "verified": verified, "timeline_id": project_id, "op_log": log}


# ── 2) 字幕（书里最重的一块）────────────────────────────────
@dataclass
class VideoSubtitles:
    name = "video.subtitles"
    version = "1.0.0"

    def spec(self):
        return _spec(self.name, "识别/生成字幕后设样式: 字体/描边/位置/关键词高亮",
            {"lang": {"type": "string"}, "style": {"type": "object"},
             "keywords": {"type": "array", "items": {"type": "string"}}},
            {"srt_ref": {"type": "string"}, "styled": {"type": "array"},
             "timing_ok": {"type": "boolean"}})

    def probe(self): return {"asr": self._asr_ready(), "editor": self._ui_ready()}

    async def run(self, ctx, *, project_id, audio_ref, style, keywords=None, lang="zh", **_) -> dict:
        srt = await ctx.asr.transcribe(audio_ref, lang=lang)          # 复用你的 ASR
        highlight = [s for s in srt["segments"]
                     if any(k in s["text"] for k in (keywords or []))]
        await ctx.touch.act("editor.subtitle", {"srt": srt, "style": style,
                                                "highlight": highlight})
        # 校验: 字幕时序不能超界/重叠（书里最容易翻车处）
        timing_ok = await ctx.devour.check_subtitle_timing(project_id, srt)
        return {"ok": True, "verified": timing_ok, "srt_ref": srt["ref"],
                "styled": highlight, "timing_ok": timing_ok}


VIDEO_SKILLS = [VideoEdit(), VideoSubtitles()]   # + color/effects/audio/template/export/script/assets/qa
