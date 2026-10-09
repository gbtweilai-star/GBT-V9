# core/compute_router.py —— 算力双通道路由：云插件主管道 / 本地备用，本地 0 显存
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人口径（2026-10-06）：
#   ① 整个框架里**需要显存/显卡**的活，全部按原代码接到**云插件**上；
#   ② **本地 0 显存**（我们不在本机占显卡）；
#   ③ 全部以**云插件 + 数据库**驱动；
#   ④ **本地源代码保留 = 双通道**：本地是备用，云插件是主管道。
#
# 纪律：本模块只做"路由与账"，不偷偷占本地显卡 —— 云模式下本地 VRAM 预留恒为 0；
#      云不可用时**如实降级到本地备用并记账**（不是静默切，也不是假装云通了）。
import json
import os
import time
from dataclasses import dataclass, field, asdict

CHANNEL_ENV = "V9_COMPUTE_CHANNEL"
CHANNELS = ("cloud", "local", "auto")
LOCAL_VRAM_ENV = "V9_LOCAL_VRAM_MB"


def channel() -> str:
    c = (os.environ.get(CHANNEL_ENV) or "cloud").strip().lower()
    return c if c in CHANNELS else "cloud"


def local_vram_reserved_mb() -> int:
    """本地预留显存：云模式下**恒为 0**（我们不用本机显卡）。"""
    if channel() == "cloud":
        return 0
    try:
        return int(os.environ.get(LOCAL_VRAM_ENV, "0") or 0)
    except (TypeError, ValueError):
        return 0


# ═══════════ 需要显存/显卡的活 → 云插件（按 Cloudflare Workers AI 目录）═══════════
@dataclass
class Workload:
    id: str
    use: str                       # 干什么
    vram_mb: int                   # 本地跑要多少显存（估算）
    plugin: str                    # 云插件槽（10 族 × 10 槽里的 key）
    db: str                        # 结果落哪个库槽（100 库里的 key）
    local_impl: str                # 本地备用实现（保留源码的那条通道）
    source: str                    # 原代码落点（真实文件）
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


WORKLOADS: tuple = (
    Workload("text.generate", "文本生成/规划/对答", 6000,
             "text-generation:llama-3.1-8b-instruct-fp8#3",
             "core-rdb:ledger#1", "core/inference/backends.py（本地 LLM）",
             "core/inference/backends.py", "云端推理，本地只保留后端源码"),
    Workload("text.reason", "深度推理/复杂决策", 12000,
             "text-generation:qwq-32b#8", "core-rdb:ledger#1",
             "skills/engine.py（Brain/Codex 回退）", "skills/engine.py"),
    Workload("code.patch", "代码生成与修补", 9000,
             "code:qwen2.5-coder-32b-instruct#1", "core-rdb:body#3",
             "skills/engine.py（Codex 工具）", "skills/engine.py"),
    Workload("vision.ground", "看图定位（GUI 操控）", 8000,
             "vision:llava-1.5-7b-hf#1", "document:frames_meta#7",
             "core/gui_agent.py（本地 VLM 兜底）", "core/gui_agent.py"),
    Workload("vision.ocr", "读屏文字", 2000,
             "vision:moondream3.1-9B-A2B#2", "fulltext:ft_logs#4",
             "senses/frame_readers.py + pytesseract", "senses/frame_readers.py"),
    Workload("image.gen", "出图（相机/素材/插画）", 10000,
             "image-generation:flux-1-schnell#1", "document:reports#2",
             "media/scheduler.py（本地显存预算）", "media/scheduler.py"),
    Workload("video.gen", "出视频/分镜", 16000,
             "image-generation:flux-2-dev#2", "document:reports#2",
             "media/queue.py（本地 GPU 队列）", "media/queue.py"),
    Workload("video.edit", "剪辑/转码/字幕/镜头理解", 4000,
             "vision:llama-3.2-11b-vision-instruct#3", "document:frames_meta#7",
             "skills/video_edit.py（本地 ffmpeg）", "skills/video_edit.py"),
    Workload("asr.transcribe", "语音转写（听）", 3000,
             "asr:whisper#1", "fulltext:ft_chat#6",
             "senses/mic.py + voice.py（本地 Whisper）", "senses/mic.py"),
    Workload("tts.speak", "语音合成（说）", 2000,
             "tts:aura-1#1", "kv-cache:session#1",
             "senses/voice.py（本地 TTS）", "senses/voice.py"),
    Workload("embed.text", "文本/笔记向量化", 1500,
             "embedding:bge-m3#4", "vector:emb_note#1",
             "core/isolated_bus.py MemoryStore（本地哈希）", "core/isolated_bus.py"),
    Workload("embed.frame", "关键帧向量化", 2500,
             "embedding:bge-large-en-v1.5#3", "vector:emb_frame#3",
             "senses/devour.py（本地帧哈希）", "senses/devour.py"),
    Workload("guard.classify", "内容守卫与分类", 4000,
             "guard:llama-guard-3-8b#1", "audit:a_alert#6",
             "body/witness_alerts.py（本地规则）", "body/witness_alerts.py"),
    Workload("classify.sentiment", "情绪/情感分类", 800,
             "classification:distilbert-sst-2-int8#2", "kv-cache:state#10",
             "body/emotion.py（本地 PAD 规则）", "body/emotion.py"),
    Workload("text.translate", "多语翻译（接单/出海）", 6000,
             "translation:m2m100-1.2b#2", "document:kb#10",
             "skills/engine.py（本地提示词翻译）", "skills/engine.py"),
    Workload("image.classify", "图像打标/质检", 3000,
             "classification:resnet-50#3", "document:frames_meta#7",
             "media/monitor.py（本地阈值规则）", "media/monitor.py"),
    Workload("ble.decide", "蓝牙意图→动作（AI 操控蓝牙）", 6000,
             "text-generation:llama-3.1-8b-instruct-fp8#3", "audit:a_action#4",
             "core/ble_control.py 本地规则兜底（decided_by=rules）", "core/ble_control.py",
             note="扫描/读走本机 senses.ble；写必须持 Grant"),
)


def workloads() -> dict:
    return {w.id: w.as_dict() for w in WORKLOADS}


def plugin_slots() -> set:
    from core.cloud_plugins import PLUGIN_IDS
    return set(PLUGIN_IDS)


def db_slots() -> set:
    from core.db_fleet import SLOT_IDS
    return set(SLOT_IDS)


def audit() -> dict:
    """完整性审计：每个要显存的活都必须有**存在的**云插件槽与库槽，且保留本地实现。"""
    ps, ds = plugin_slots(), db_slots()
    bad = []
    for w in WORKLOADS:
        if w.plugin not in ps:
            bad.append({"workload": w.id, "problem": "云插件槽不存在", "value": w.plugin})
        if w.db not in ds:
            bad.append({"workload": w.id, "problem": "库槽不存在", "value": w.db})
        if not os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), w.source.replace("/", os.sep))):
            bad.append({"workload": w.id, "problem": "本地源码落点不存在", "value": w.source})
    vram_total = sum(w.vram_mb for w in WORKLOADS)
    return {"workloads": len(WORKLOADS), "channel": channel(),
            "local_vram_reserved_mb": local_vram_reserved_mb(),
            "local_vram_if_all_local_mb": vram_total,
            "saved_local_vram_mb": (vram_total if channel() == "cloud" else 0),
            "problems": bad, "ok": not bad,
            "note": "云模式下本地预留显存恒为 0；本地源码保留为备用通道"}


# ═══════════ 媒体队列 skill/stage → 算力活 ═══════════
SKILL_MAP: dict = {
    "media.image.gen": "image.gen", "media.image": "image.gen",
    "media.video.gen": "video.gen", "media.video": "video.gen",
    "media.video.edit": "video.edit", "media.clip": "video.edit",
    "media.subtitle": "video.edit", "media.transcode": "video.edit",
    "media.asr": "asr.transcribe", "media.stt": "asr.transcribe",
    "media.tts": "tts.speak", "voice.speak": "tts.speak",
    "media.embed": "embed.text", "media.embed.frame": "embed.frame",
    "media.guard": "guard.classify", "media.ocr": "vision.ocr",
    "media.vision": "vision.ground", "media.translate": "text.translate",
    "media.classify": "image.classify",
}
STAGE_MAP: dict = {
    "image": "image.gen", "gen_image": "image.gen", "video": "video.gen",
    "gen_video": "video.gen", "edit": "video.edit", "clip": "video.edit",
    "asr": "asr.transcribe", "tts": "tts.speak", "embed": "embed.text",
    "gpu": "image.gen",
}


def workload_of_job(job: dict) -> str | None:
    """媒体队列的 job → 算力活 id；登记不了就**如实返回 None**（不猜）。"""
    if not isinstance(job, dict):
        return None
    sk = str(job.get("skill") or "").strip().lower()
    if sk in SKILL_MAP:
        return SKILL_MAP[sk]
    for key, wid in SKILL_MAP.items():                  # media.video.gen.v2 这类后缀
        if sk.startswith(key):
            return wid
    st = str(job.get("stage") or "").strip().lower()
    return STAGE_MAP.get(st)


def policy() -> dict:
    """媒体/推理层要遵守的算力策略读数。"""
    ch = channel()
    return {"channel": ch, "cloud_primary": ch == "cloud",
            "local_backup_kept": True, "local_vram_mb": local_vram_reserved_mb(),
            "cloud_enabled_default": ch in ("cloud", "auto"),
            "refuse_local_gpu": ch == "cloud",
            "env": {CHANNEL_ENV: ch, LOCAL_VRAM_ENV: str(local_vram_reserved_mb())}}


def media_defaults() -> dict:
    """给 media 子系统（selector/scheduler/queue）用的默认值。"""
    p = policy()
    return {"cloud_enabled": p["cloud_enabled_default"],
            "local_vram_mb": p["local_vram_mb"],
            "cloud_primary": p["cloud_primary"]}


def route(workload_id: str, *, prefer: str | None = None) -> dict:
    """给一个活选通道：默认云插件（主管道）；云不可用且允许时降级本地备用（记账）。"""
    w = {x.id: x for x in WORKLOADS}.get(workload_id)
    if w is None:
        return {"ok": False, "reason": f"未知算力活：{workload_id}"}
    ch = (prefer or channel())
    if ch == "cloud":
        return {"ok": True, "workload": w.id, "channel": "cloud", "plugin": w.plugin,
                "db": w.db, "vram_mb_local": 0, "why": "云插件为主管道（本地 0 显存）",
                "local_backup": w.local_impl}
    if ch == "local":
        return {"ok": True, "workload": w.id, "channel": "local",
                "local_impl": w.local_impl, "source": w.source,
                "vram_mb_local": w.vram_mb, "why": "显式走本地备用通道",
                "db": w.db}
    # auto：先云，云不可用再本地
    ok, why = cloud_available()
    if ok:
        return {"ok": True, "workload": w.id, "channel": "cloud", "plugin": w.plugin,
                "db": w.db, "vram_mb_local": 0, "why": "auto→云可用"}
    return {"ok": True, "workload": w.id, "channel": "local", "local_impl": w.local_impl,
            "vram_mb_local": w.vram_mb, "db": w.db,
            "why": f"auto→云不可用（{why}），降级本地备用"}


def cloud_available() -> tuple:
    """云插件主管道是否可用：看统一密钥是否齐（有 key 才谈得上调云）。"""
    try:
        from core.tentacle_fleet import UnifiedKey
        k = UnifiedKey()
        if not k.loaded:
            return False, "统一密钥未配置（CLOUDFLARE/OPENAI key）"
        return True, "ok"
    except Exception as exc:                                   # noqa: BLE001
        return False, f"{type(exc).__name__}"


def report() -> dict:
    """给面板/审计看的完整读数：通道、本地显存、每个活的路由。"""
    a = audit()
    rows = [route(w.id) for w in WORKLOADS]
    return {**a, "routes": rows,
            "cloud_available": cloud_available(),
            "table": [{"活": w.id, "用途": w.use, "本地需显存MB": w.vram_mb,
                       "云插件（主管道）": w.plugin, "结果库": w.db,
                       "本地备用": w.local_impl} for w in WORKLOADS]}


def mark(result: dict, *, db=None) -> dict:
    """把路由决策落库（有账本才落）。"""
    if db is None:
        return {"ok": False, "reason": "no_ledger"}
    try:
        import uuid as _uuid
        db.execute(
            "INSERT INTO compute_route_audit (route_id, workload, channel, plugin, db_slot,"
            " vram_mb_local, at) VALUES (?,?,?,?,?,?,?)",
            (_uuid.uuid4().hex[:12], result.get("workload"), result.get("channel"),
             result.get("plugin"), result.get("db"), int(result.get("vram_mb_local") or 0),
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        return {"ok": True}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


__all__ = ["WORKLOADS", "Workload", "channel", "local_vram_reserved_mb", "workloads",
           "audit", "route", "report", "cloud_available", "mark", "CHANNELS",
           "policy", "media_defaults", "workload_of_job", "SKILL_MAP", "STAGE_MAP"]
