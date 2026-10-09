# core/creator_watch.py —— 对标追踪 → 脚本 → 本地成片 → 自动进知识库（一条自动线）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 由来（2026-10-09）：LobeHub 记忆里，主人的主线是「AI 短剧 + 对标创作者情报」
#   （T-3 每日追踪：抖音 方桃子/虫羽/不吃榨菜/站立起来的影子；B站 W博士团队/扎克鸡）。
#   本模块把那条线**接到本地出片链**上：追踪清单 → 选题 → 脚本（云模型写）→ 本地成片 → 上传知识库。
#
# 纪律（照本仓口径）：
#   · 脚本优先走**云模型**（免费 neuron 额度），失败**如实降级**为本地模板并标注，不假称；
#   · 出片走 core.studio（全本地零付费）；上传走 lh CLI（已登录）；
#   · 每一步都返回真读数，不许"应该可以"。
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "creator_watch"
KB_ID = "kb_DBAaqzwh1Xuq"          # GBT小土豆V9 · AI短剧工作台

# 对标清单（来自平台记忆；可增删）
TRACKED: tuple = (
    dict(平台="抖音", 账号="方桃子", 赛道="AI短剧"),
    dict(平台="抖音", 账号="虫羽", 赛道="AI短剧"),
    dict(平台="抖音", 账号="不吃榨菜", 赛道="AI短剧"),
    dict(平台="抖音", 账号="站立起来的影子", 赛道="AI短剧"),
    dict(平台="B站", 账号="W博士团队", 赛道="AI短剧"),
    dict(平台="B站", 账号="扎克鸡", 赛道="AI短剧"),
)

# 选题库（本地兜底用；线上可被追踪结果替换）
TOPIC_POOL: tuple = (
    "为什么 AI 短剧突然这么火",
    "一个人怎么做出一集短剧",
    "对标账号的钩子是怎么写的",
    "AI 短剧的封面为什么比正片重要",
    "零成本做短剧的第一步",
)


def tracked() -> dict:
    return {"条数": len(TRACKED), "来源": "LobeHub 平台记忆（T-3 追踪）", "行": [dict(x) for x in TRACKED]}


def pick_topic(seed: str = "") -> str:
    if seed.strip():
        return seed.strip()[:40]
    import hashlib
    i = int(hashlib.sha256(str(int(time.time()) // 600).encode()).hexdigest(), 16) % len(TOPIC_POOL)
    return TOPIC_POOL[i]


def brief(topic: str, target_chars: int = 110) -> dict:
    """写脚本：**优先云模型**（免费额度），失败如实降级本地模板并标注。"""
    prompt = (
        "你是短视频口播编剧。请为中文竖版短视频写一段**口播旁白**，"
        "要求：%d 字左右、4~5 句、每句独立成意、口语、有钩子、结尾一句行动号召；"
        "只输出旁白正文，不要标题、不要分镜、不要引号。主题：%s" % (target_chars, topic))
    got = None
    used = ""
    try:
        from core import cloud_runner as CR
        kinds = list(getattr(CR, "TIERS", {}) or {"小": 0, "中": 1, "大": 2})
        for kind in kinds:
            r = CR.run_task(kind, prompt, tentacle="t001")
            txt = ""
            if isinstance(r, dict):
                txt = str(r.get("出字") or r.get("text") or r.get("回答") or r.get("回") or "").strip()
            if len(txt) >= 40:
                got, used = txt, "云模型/%s" % kind
                break
    except Exception as exc:  # noqa: BLE001
        used = "云模型失败:%s" % type(exc).__name__
    if not got:
        got = ("%s。这不是运气，是方法。对标不是抄，是看懂人家为什么能留住人。"
               "先写钩子，再写内容，最后写行动。你也能做出一集。" % topic)
        used = used or "本地模板（云模型不可用，如实降级）"
    got = got.replace("\n", " ").strip()
    return {"ok": True, "选题": topic, "脚本": got, "字数": len(got), "来源": used}


def produce(topic: str = "", title: str = "", upload: bool = True) -> dict:
    """一条自动线：选题 → 脚本 → 本地成片 → 上传知识库。"""
    STATE.mkdir(parents=True, exist_ok=True)
    tp = pick_topic(topic)
    bf = brief(tp)
    from core import studio as SD
    clips = [ROOT / "render/anim_v" / n for n in ("idle.mp4", "talk.mp4", "walk.mp4")]
    clips = [c for c in clips if c.is_file()] or list((ROOT / "render").glob("*.mp4"))
    name = "对标出片-%s.mp4" % time.strftime("%m%d-%H%M")
    mv = SD.plan(bf["脚本"], clips, name,
                 title=(title or tp)[:14], sub="对标追踪 · 自动出片")
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "选题": tp, "字数": bf["字数"],
           "脚本来源": bf["来源"], "成片": mv.get("文件"), "秒": mv.get("时长"),
           "KB": (mv.get("字节") or 0) // 1024, "分辨率": mv.get("分辨率")}
    up = {"ok": False, "reason": "未上传"}
    if upload and mv.get("ok"):
        p = ROOT / mv["文件"]
        r = subprocess.run(["cmd", "/c", "lh", "kb", "upload", KB_ID, str(p)],
                           capture_output=True)
        out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
        up = {"ok": "Uploaded" in out, "回执": out[-160:]}
        rec["上传"] = up["ok"]
    (STATE / "ledger.jsonl").parent.mkdir(parents=True, exist_ok=True)
    with (STATE / "ledger.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": bool(mv.get("ok")), "选题": tp, "脚本": bf, "成片": mv, "上传": up,
            "台账": str((STATE / "ledger.jsonl").relative_to(ROOT)),
            "对标清单": tracked()["条数"]}


def history(limit: int = 10) -> dict:
    p = STATE / "ledger.jsonl"
    if not p.is_file():
        return {"条数": 0, "行": []}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()][-limit:]
    return {"条数": len(rows), "行": rows}


__all__ = ["TRACKED", "TOPIC_POOL", "KB_ID", "tracked", "pick_topic", "brief", "produce", "history"]
