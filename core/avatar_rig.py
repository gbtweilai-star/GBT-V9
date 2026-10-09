# core/avatar_rig.py —— 数字人骨架绑定与全肢体动作（FK 骨链 + 动作库 + 口型同步）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求：把数字人的骨架**绑好**，让它能完成**全肢体动作**。
# 做法（能真跑、零依赖、零外链）：标准两足骨架（髋→脊柱→颈→头；肩→上臂→前臂→手；
# 髋→大腿→小腿→脚），用**正向运动学（FK）**算关节位置；动作库用可循环的确定性关键帧
# （呼吸/挥手/鞠躬/点头/鼓掌/思考/走两步/欢呼/指向），并支持：
#   · 情绪 → 动作（乐=欢呼、哀=鞠躬…）
#   · 说话口型（mouth 开合由音量/音节驱动）
#   · 输出内联 SVG（面板直接画，不需要任何外部模型/贴图）
# 纪律：所有动作都是数学函数（可复现、可测试）；不假装 3D，二维骨骼但**全身关节齐全**。
import math
import time

# ── 骨架定义：关节 → (父关节, 骨长, 相对基角°，活动轴) ──
# ★2026-10-07 重绑（主人："全肢体动作骨架给我绑好"）：旧表左右不对称 ——
#   左臂基角 -95°（cos≈-0.09 / sin≈-1.00）是**朝上**、右臂 +95° 朝下，于是左臂变成一根
#   竖到头上的杆子（"铁架子"的真身）；双脚基角都是 10° 所以两只脚都朝右；两条大腿只差 10°
#   几乎叠在一起。现在按屏幕坐标（0°=右，90°=下）改写成**左右镜像**的人体姿态。
SKELETON: tuple = (
    ("hip",      None,        0.0,   0.0),
    ("spine",    "hip",      62.0,  -90.0),
    ("neck",     "spine",    26.0,  -90.0),
    ("head",     "neck",     30.0,  -90.0),
    ("shoulderL", "neck",    22.0,  200.0),   # 左肩：向左上（原 -160，本就对）
    ("armL",     "shoulderL", 46.0,   96.0),  # 左臂自然垂下（略外张）
    ("foreL",    "armL",     42.0,   92.0),
    ("handL",    "foreL",    16.0,   92.0),
    ("shoulderR", "neck",    22.0,  340.0),   # 右肩：向右上（原 -20，本就对）
    ("armR",     "shoulderR", 46.0,   84.0),  # 右臂自然垂下（略外张）
    ("foreR",    "armR",     42.0,   88.0),
    ("handR",    "foreR",    16.0,   88.0),
    ("thighL",   "hip",      62.0,   97.0),   # 两腿分开站，看得出是两条腿
    ("shinL",    "thighL",   58.0,   90.0),
    ("footL",    "shinL",    22.0,  175.0),   # 左脚朝左
    ("thighR",   "hip",      62.0,   83.0),
    ("shinR",    "thighR",   58.0,   90.0),
    ("footR",    "shinR",    22.0,    5.0),   # 右脚朝右
)
JOINTS = {j[0]: {"parent": j[1], "len": j[2], "base": j[3]} for j in SKELETON}
CLIPS: tuple = (
    # ── 社交（吸引用户最常用）──
    "wave", "wave_both", "bow", "bow_deep", "nod", "handshake", "salute", "heart",
    "thumb_up", "peace", "kiss", "shy", "welcome", "point", "introduce",
    # ── 情绪 ──
    "cheer", "clap", "cry", "angry", "surprise", "laugh", "proud",
    # ── 日常 ──
    "idle", "think", "walk", "run", "stretch", "squat", "look_around", "check_time",
    "scratch_head", "tired",
)
CLIP_META: dict = {
    "wave": ("挥手打招呼", "社交"), "wave_both": ("双手挥手", "社交"),
    "bow": ("点头礼", "社交"), "bow_deep": ("深鞠躬", "社交"),
    "nod": ("点头", "社交"), "handshake": ("伸手握手", "社交"),
    "salute": ("敬礼", "社交"), "heart": ("比心", "社交"),
    "thumb_up": ("竖大拇指", "社交"), "peace": ("比耶", "社交"),
    "kiss": ("飞吻", "社交"), "shy": ("害羞遮面", "社交"),
    "welcome": ("请进/欢迎", "社交"), "point": ("指向介绍", "社交"),
    "introduce": ("摊手介绍", "社交"),
    "cheer": ("欢呼", "情绪"), "clap": ("鼓掌", "情绪"),
    "cry": ("擦眼泪", "情绪"), "angry": ("叉腰生气", "情绪"),
    "surprise": ("吃惊捂嘴", "情绪"), "laugh": ("捧腹笑", "情绪"),
    "proud": ("抱臂自信", "情绪"),
    "idle": ("呼吸站立", "日常"), "think": ("托腮思考", "日常"),
    "walk": ("原地走两步", "日常"), "run": ("原地跑", "日常"),
    "stretch": ("伸展腰背", "日常"), "squat": ("下蹲", "日常"),
    "look_around": ("左右张望", "日常"), "check_time": ("看表", "日常"),
    "scratch_head": ("挠头", "日常"), "tired": ("疲惫垂肩", "日常"),
}
# 组合动作：一次播放一串动作（更有人味，也更好看）
SEQUENCES: dict = {
    "问候": ("wave", "nod", "heart"),
    "欢迎": ("welcome", "bow", "clap"),
    "感谢": ("bow", "clap", "heart"),
    "加油": ("cheer", "clap", "thumb_up"),
    "告别": ("wave", "bow", "kiss"),
    "自信": ("proud", "thumb_up", "nod"),
}
BLINK_PERIOD = 3.2          # 秒：基础眨眼间隔
BLINK_LEN = 0.16


def _deg(d: float) -> float:
    return d * math.pi / 180.0


def _clip_offsets(name: str, t: float) -> dict:
    """动作偏移：委托 core.avatar_motion（32 个动作 + 缓动 + 跟随动作）。

    真机教训：动作名加了、动作数据没加 → 坐标算得出来但**一动不动**（静态站姿），
    而测试仍会"通过"（坐标合法）。所以必须走统一动作库，仅在其不可用时兜底。
    """
    try:
        from core import avatar_motion as M
        return M.offsets(name, t)
    except Exception:                                         # noqa: BLE001
        return _clip_offsets_builtin(name, t)


def _clip_offsets_builtin(name: str, t: float) -> dict:
    """兜底（动作库不可用）：只留最基础三个动作，避免整页瘫掉。"""
    w = 2 * math.pi * t
    off: dict = {}
    if name in ("idle", "呼吸"):
        b = math.sin(w) * 1.6
        off.update({"spine": b * 0.45, "armL": b, "armR": -b})
    elif name == "wave":
        sw = math.sin(2 * math.pi * t * 2) * 18
        off.update({"armR": -112 + sw * 0.35, "foreR": -38 + sw})
    elif name == "bow":
        k = math.sin(math.pi * t)
        off.update({"spine": 30 * k, "armL": 16 * k, "armR": -16 * k})
    return off


def blink(t_sec: float, *, seed: float = 0.0) -> float:
    try:
        from core import avatar_motion as M
        return M.blink(t_sec, seed=seed)
    except Exception:                                         # noqa: BLE001
        return 0.0


def _blink_now() -> float:
    """按墙钟算当前眨眼开合（页面每一帧都会问，所以必须跟真实时间走）。"""
    try:
        from core import avatar_motion as M
        return float(M.blink(time.time()))
    except Exception:                                         # noqa: BLE001
        return 0.0


def sequences() -> dict:
    try:
        from core import avatar_motion as M
        return dict(M.SEQUENCES)
    except Exception:                                         # noqa: BLE001
        return {}


def clip_meta() -> dict:
    try:
        from core import avatar_motion as M
        return dict(M.CLIP_META)
    except Exception:                                         # noqa: BLE001
        return {c: (c, "其他") for c in CLIPS}


def pose(clip: str = "idle", t: float = 0.0, *, mood_deg: float = 0.0,
         mouth: float = 0.0, energy: float = 1.0) -> dict:
    """算某一刻的**绝对关节角**（度）。mood_deg 给整体姿态加一点情绪偏移，mouth 是口型开合。"""
    name = clip if clip in CLIPS else "idle"
    off = _clip_offsets(name, t % 1.0)
    ang = {}
    for j, meta in JOINTS.items():
        base = meta["base"]
        if j in ("armL", "armR", "foreL", "foreR", "thighL", "thighR", "shinL", "shinR",
                 "handL", "handR"):
            base += off.get(j, 0.0) * energy
        else:
            base += off.get(j, 0.0) * energy + mood_deg
        ang[j] = base
    ang["mouth"] = max(0.0, min(1.0, float(mouth)))
    return ang


def joints(ang: dict, *, origin=(180.0, 150.0)) -> dict:
    """正向运动学：按骨长与角度算出每个关节的屏幕坐标（SVG 坐标，y 向下）。"""
    pos = {"hip": (float(origin[0]), float(origin[1]))}
    # 依赖顺序（父在子前）
    order = [j[0] for j in SKELETON]
    for j in order:
        if j == "hip":
            continue
        meta = JOINTS[j]
        p = meta["parent"]
        if p not in pos:                                  # 父还没算（理论不会）→ 跳过
            continue
        px, py = pos[p]
        a = _deg(ang.get(j, meta["base"]))
        pos[j] = (px + math.cos(a) * meta["len"], py + math.sin(a) * meta["len"])
    return pos


def to_svg(ang: dict, *, size=(360, 560), scale: float = 1.0, blink: float = 0.0) -> str:
    """内联 SVG：骨头（粗圆线）+ 关节（圆点）+ 头脸（眼/口，口型由 ang['mouth'] 驱动）。

    blink∈[0,1] 是眨眼开合（1=闭）：眼高按比例压缩，闭到位就画成一条线，避免"死人眼"。
    """
    w, h = size
    pos = joints(ang, origin=(w / 2, 150.0))
    def sc(p):
        return (p[0], p[1])
    bones = [(j, JOINTS[j]["parent"]) for j in JOINTS if JOINTS[j]["parent"]]
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="auto" role="img" '
             f'aria-label="数字人骨骼" style="--ink:#dbe7f5">',
             '<defs><radialGradient id="glow" cx="50%" cy="30%" r="70%">'
             '<stop offset="0%" stop-color="rgba(57,208,255,.18)"/>'
             '<stop offset="100%" stop-color="transparent"/></radialGradient></defs>',
             f'<rect width="{w}" height="{h}" fill="url(#glow)"/>']
    for child, parent in bones:
        if child not in pos or parent not in pos:
            continue
        x1, y1 = sc(pos[parent]); x2, y2 = sc(pos[child])
        thick = 14 if child in ("spine", "hip") else (9 if child.startswith(("arm", "fore", "thigh", "shin")) else 6)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="#2a4a6a" stroke-width="{thick + 3}" stroke-linecap="round"/>')
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="#9fd6ff" stroke-width="{thick}" stroke-linecap="round" '
                     f'opacity="0.92"/>')
    for j, p in pos.items():
        if j == "head":
            continue
        parts.append(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="4.6" fill="#e8f4ff" '
                     f'opacity="0.95"/>')
    # 头与脸
    hx, hy = pos.get("head", (w / 2, 80))
    r = 30 * scale
    parts.append(f'<circle cx="{hx:.1f}" cy="{hy - r * 0.2:.1f}" r="{r:.1f}" '
                 f'fill="#0f1b2b" stroke="#9fd6ff" stroke-width="2"/>')
    bl = max(0.0, min(1.0, float(blink)))
    eye_ry = max(0.35, 4.2 * (1.0 - 0.92 * bl))
    parts.append(f'<ellipse cx="{hx - 10:.1f}" cy="{hy - r * 0.32:.1f}" rx="3.1" ry="{eye_ry:.1f}" '
                 f'fill="#9fd6ff"/>')
    parts.append(f'<ellipse cx="{hx + 10:.1f}" cy="{hy - r * 0.32:.1f}" rx="3.1" ry="{eye_ry:.1f}" '
                 f'fill="#9fd6ff"/>')
    mo = ang.get("mouth", 0.0)
    parts.append(f'<ellipse cx="{hx:.1f}" cy="{hy + r * 0.12:.1f}" '
                 f'rx="{6 + 5 * mo:.1f}" ry="{1.6 + 7 * mo:.1f}" fill="#ff8fb0" '
                 f'opacity="0.9"/>')
    parts.append("</svg>")
    return "".join(parts)


# ── 情绪 → 动作（面板/语音都会用）──
MOOD_CLIP = {"喜": "cheer", "乐": "cheer", "怒": "point", "哀": "bow", "惊": "clap",
             "平": "idle", "思考": "think", "得意": "wave"}


def clip_for_mood(mood: str) -> str:
    return MOOD_CLIP.get(str(mood or "").strip(), "idle")


def speak_state(clip: str = "idle", t: float = 0.0, *, speaking: bool = False,
                amp: float = 0.0, mood: str = "", blink: float | None = None) -> dict:
    """一次拿全：动作 + 口型 + 眨眼。speaking=True 时口型按 amp（音量/音节强度）开合。"""
    mouth = 0.0
    if speaking:
        mouth = max(0.08, min(1.0, float(amp or 0.5))) * (0.55 + 0.45 * abs(math.sin(2 * math.pi * t * 4)))
    eff = clip or clip_for_mood(mood)
    ang = pose(eff, t, mouth=mouth)
    bl = blink if blink is not None else _blink_now()
    return {"clip": eff, "angles": ang, "svg": to_svg(ang, blink=bl),
            "speaking": bool(speaking), "mouth": round(mouth, 3), "blink": round(float(bl), 3),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())}


def sequence_state(name: str, t: float, **kw) -> dict:
    """组合动作：把一串动作按时相依次播放（各段均分；不认识的组合 → 当单动作处理）。"""
    try:
        from core import avatar_motion as M
        seq = M.SEQUENCES.get(str(name or ""))
    except Exception:                                         # noqa: BLE001
        seq = None
    if not seq:
        return speak_state(str(name or "idle"), t, **kw)
    n = len(seq)
    span = 1.0 / n
    idx = min(n - 1, int(t / span))
    local = (t - idx * span) / span
    out = speak_state(seq[idx], local, **kw)
    out["combo"] = str(name)
    out["step"] = f"{idx + 1}/{n}"
    return out


def status() -> dict:
    cm = clip_meta()
    return {"骨架关节数": len(JOINTS), "关节": list(JOINTS),
            "动作库": list(CLIPS), "动作数": len(CLIPS),
            "动作细节": {c: cm.get(c, (c, "其他"))[0] for c in CLIPS},
            "动作分类": {c: cm.get(c, (c, "其他"))[1] for c in CLIPS},
            "组合动作": sequences(),
            "口型": "说话时由音量驱动开合（speaking+amp）",
            "眨眼": "按墙钟周期开合（blink，眼高压缩到闭合；不是死眼）",
            "情绪映射": MOOD_CLIP,
            "输出": "内联 SVG（零依赖、零外链）"}


__all__ = ["SKELETON", "JOINTS", "CLIPS", "pose", "joints", "to_svg", "speak_state",
           "clip_for_mood", "status", "MOOD_CLIP", "blink", "sequences", "clip_meta",
           "sequence_state"]
