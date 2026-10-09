# core/avatar_motion.py —— 数字人动作库（32 个全肢体动作 + 6 组组合 + 眨眼/缓动）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求：**全肢体动作都要绑好**，而且要好看、能吸引用户。
# 这里把动作写成**确定性关键帧数学**（可循环、可测试、零依赖）：
#   · 覆盖社交 / 情绪 / 日常三类 32 个动作，全部动用上肢+躯干+下肢
#   · 带**缓动**（ease-in-out）与**跟随动作**（末端关节滞后 lag）→ 看起来像人，不像木偶
#   · 6 组**组合动作**（问候/欢迎/感谢/加油/告别/自信）：一次播放一串动作
#   · 眨眼函数（周期+抖动），配合口型让脸"活"起来
import math

CLIPS: tuple = (
    # 社交
    "wave", "wave_both", "bow", "bow_deep", "nod", "handshake", "salute", "heart",
    "thumb_up", "peace", "kiss", "shy", "welcome", "point", "introduce",
    # 情绪
    "cheer", "clap", "cry", "angry", "surprise", "laugh", "proud",
    # 日常
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
    "welcome": ("请进 / 欢迎", "社交"), "point": ("指向介绍", "社交"),
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

SEQUENCES: dict = {
    "问候": ("wave", "nod", "heart"),
    "欢迎": ("welcome", "bow", "clap"),
    "感谢": ("bow", "clap", "heart"),
    "加油": ("cheer", "clap", "thumb_up"),
    "告别": ("wave", "bow", "kiss"),
    "自信": ("proud", "thumb_up", "nod"),
}
BLINK_PERIOD = 3.2
BLINK_LEN = 0.16
LAG = 0.06                       # 跟随延迟：末端关节比根关节慢一点


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


HOLD_RAMP = 0.18                 # 保持型手势的起势/收势时长（占相位比例）
# 这些是"摆姿势"类动作：本身没有内部运动，必须靠包络起势 + 呼吸层，否则页面上一动不动。
_HELD: frozenset = frozenset({
    "salute", "thumb_up", "peace", "shy", "point", "angry", "proud", "tired", "think",
})


def _hold(t: float) -> float:
    """保持型手势的进出包络：快速起势 → 保持 → 收回；相位两端归零，可无缝循环。"""
    if t < HOLD_RAMP:
        return ease(t / HOLD_RAMP)
    if t > 1.0 - HOLD_RAMP:
        return ease((1.0 - t) / HOLD_RAMP)
    return 1.0


def _breath(t: float) -> dict:
    """常驻呼吸层：叠加在动作之上，保证任何姿势都不会"僵死"。"""
    w = 2 * math.pi * t
    b = math.sin(w) * 1.9
    return {"spine": b * .5, "neck": -b * .35, "armL": b * .8, "armR": -b * .8,
            "foreL": -b * .5, "foreR": b * .5, "head": math.sin(w * .5) * .9}


# ── 手臂动作改成"指向哪里"写法（2026-10-07 重绑）─────────────────────────────
# 偏移量是在基角上加减的，一旦基角错（旧表左臂基角是朝上的），写着"抬手"的动作会
# 变成"插在头上"。现在统一写成**目标方向**（屏幕角度：0°=右，90°=下，-90°=上），
# 由 _aim() 换算成偏移量；左右镜像用 _mir() 推，保证两侧对称动作真的对称。
_ARM = {"L": 96.0, "R": 84.0}
_FORE = {"L": 92.0, "R": 88.0}
_HAND = {"L": 92.0, "R": 88.0}


def _aim(kind: str, side: str, target: float) -> float:
    """想让这根骨头**指向**哪个方向（屏幕角度）→ 偏移量（归一化到 ±180）。"""
    base = {"arm": _ARM, "fore": _FORE, "hand": _HAND}[kind][side]
    return ((float(target) - base + 180.0) % 360.0) - 180.0


def _mir(target: float) -> float:
    """左右镜像：绕竖直中轴翻过去。"""
    return 180.0 - float(target)


def _arm(off: dict, side: str, arm_t: float, fore_t: float | None = None,
         hand_t: float | None = None) -> None:
    off["arm" + side] = _aim("arm", side, arm_t)
    if fore_t is not None:
        off["fore" + side] = _aim("fore", side, fore_t)
    if hand_t is not None:
        off["hand" + side] = _aim("hand", side, hand_t)


def _both(off: dict, arm_t: float, fore_t: float | None = None,
          hand_t: float | None = None, *, sway: float = 0.0) -> None:
    """对称双手动作：左边给目标，右边取镜像（sway 让两侧差一点，看起来不像机械）。"""
    _arm(off, "L", arm_t - sway, None if fore_t is None else fore_t - sway,
         None if hand_t is None else hand_t - sway)
    _arm(off, "R", _mir(arm_t) + sway, None if fore_t is None else _mir(fore_t) + sway,
         None if hand_t is None else _mir(hand_t) + sway)


def offsets(name: str, t: float) -> dict:
    """相位 t∈[0,1) → 关节角度偏移（度）。未知动作 → 空（上层会退回 idle）。

    手臂/腿一律用 `_arm/_both`（目标方向 + 镜像）写：这样两侧对称动作真的对称，
    也不会再出现"想抬手却插在头上"（旧基角左臂朝上导致的经典翻车）。
    """
    w = 2 * math.pi * t
    off: dict = {}
    if name == "idle":
        b = math.sin(w) * 1.6
        off.update({"spine": b * .45, "neck": -b * .3, "armL": b, "armR": -b,
                    "foreL": -b * .6, "foreR": b * .6, "head": math.sin(w * .5) * .8})
    elif name == "wave":                                   # 右手举起挥动，左手自然垂
        sw = math.sin(2 * math.pi * (t * 2)) * 18
        _arm(off, "R", -112 + sw * .35, -146 + sw, -156)
        _arm(off, "L", 98 + math.sin(2 * math.pi * (t - LAG) * 2) * 2, 92, 92)
        off.update({"spine": 2, "head": -2})
    elif name == "wave_both":
        sw = math.sin(2 * math.pi * t * 2) * 16
        _both(off, -110, -146, -156, sway=sw * .3)
        off.update({"spine": 3, "head": -3})
    elif name == "bow":
        k = ease(math.sin(math.pi * t))
        off.update({"spine": 30 * k, "neck": 9 * k, "hip": -5 * k})
        _both(off, 96 + 30 * k, 92 + 46 * k, 92 + 50 * k)
    elif name == "bow_deep":
        k = ease(math.sin(math.pi * t))
        off.update({"spine": 52 * k, "neck": 14 * k, "hip": -10 * k,
                    "thighL": 8 * k, "thighR": -8 * k})
        _both(off, 96 + 44 * k, 92 + 62 * k, 92 + 66 * k)
    elif name == "nod":
        k = math.sin(2 * math.pi * t * 2)
        off.update({"neck": 8 * k, "head": 6 * k, "spine": 1.2 * k})
    elif name == "handshake":                              # 右手向前伸出
        k = ease(math.sin(math.pi * t))
        _arm(off, "R", 30 - 8 * k, 16 - 6 * k, 12 - 4 * k)
        _arm(off, "L", 98, 92, 92)
        off.update({"spine": 4 * k, "head": 2 * k})
    elif name == "salute":                                 # 右手指到额角
        _arm(off, "R", -62, -166, -172)
        _arm(off, "L", 98, 92, 92)
        off.update({"spine": -1, "head": -2})
    elif name == "heart":                                  # 双手在胸前比心（肘外、手到胸前）
        k = ease(math.sin(math.pi * t))
        _arm(off, "L", 100, 92 - 110 * k, 92 - 118 * k)
        _arm(off, "R", _mir(100), _mir(92 - 110 * k), _mir(92 - 118 * k))
        off.update({"spine": 3 * k, "head": 3 * k})
    elif name == "thumb_up":                               # 前臂竖起：大拇指
        _arm(off, "R", 66, -66, -76)
        _arm(off, "L", 98, 92, 92)
        off.update({"spine": 1.5, "head": -2})
    elif name == "peace":                                  # 比耶：手举到脸侧
        _arm(off, "R", 34, -76, -88)
        _arm(off, "L", 98, 92, 92)
        off.update({"spine": 2, "head": -3})
    elif name == "kiss":                                   # 手到唇边再送出去
        k = ease(math.sin(math.pi * t))
        _arm(off, "R", 24 - 74 * k, -84 - 46 * k, -96 - 26 * k)
        off.update({"spine": 2 * k, "neck": -3 * k, "head": -4 * k})
    elif name == "shy":                                    # 双手遮脸（肘在头侧、手在脸前）
        _both(off, -118, 26, 30)
        off.update({"neck": 8, "head": 6, "spine": 4})
    elif name == "welcome":                                # 右手摊开请进
        k = ease(math.sin(math.pi * t))
        _arm(off, "R", 30 + 26 * k, 18 + 18 * k, 12 + 12 * k)
        _arm(off, "L", 104, 96, 96)
        off.update({"spine": 10 * k, "neck": 4 * k, "head": 3 * k})
    elif name == "point":                                  # 指向
        _arm(off, "R", 8, 2, -2)
        _arm(off, "L", 98, 92, 92)
        off.update({"spine": -1, "head": -1})
    elif name == "introduce":                              # 双手摊开介绍
        s = math.sin(2 * math.pi * t)
        _arm(off, "L", 130 + 8 * s, 116, 112)
        _arm(off, "R", _mir(130) - 8 * s, _mir(116), _mir(112))
        off.update({"spine": 6, "head": 2 * s})
    elif name == "cheer":                                  # 双手举起欢呼
        k = (math.sin(2 * math.pi * t) + 1) / 2
        _both(off, -96 - 18 * k, -150 - 12 * k, -160, sway=4 * k)
        off.update({"spine": -5 * k, "hip": -3 * k, "thighL": 4 * k, "thighR": -4 * k,
                    "head": -6 * k})
    elif name == "clap":                                   # 双手在胸前合掌（拍得到一起）
        k = abs(math.sin(2 * math.pi * t * 3))
        _arm(off, "L", 100, 12 - 16 * k, 6 - 14 * k)
        _arm(off, "R", _mir(100), _mir(12 - 16 * k), _mir(6 - 14 * k))
        off.update({"spine": 3, "head": -2 * k})
    elif name == "cry":                                    # 双手擦眼泪（手在眼角）
        k = math.sin(2 * math.pi * t * 1.5)
        _both(off, -112, 14 + 8 * k, 18 + 8 * k)
        off.update({"neck": 10, "head": 8, "spine": 6})
    elif name == "angry":                                  # 叉腰：肘朝外、手贴腰
        _arm(off, "L", 122, 6, 0)
        _arm(off, "R", _mir(122), _mir(6), _mir(0))
        off.update({"spine": -2, "head": -3, "thighL": 2, "thighR": -2})
    elif name == "surprise":                               # 双手捂嘴
        k = ease(min(1.0, t * 3)) if t < 0.4 else 1.0
        _both(off, -118 * k, 16 * k, 20 * k)
        off.update({"neck": -6 * k, "head": -5 * k, "spine": -4 * k})
    elif name == "laugh":                                  # 捧腹
        k = math.sin(2 * math.pi * t * 3)
        _both(off, 62, 92 - 122, 92 - 128)
        off.update({"spine": 14 + 5 * k, "neck": 8 + 4 * k, "head": 6 + 3 * k})
    elif name == "proud":                                  # 抱臂（手抬到胸前交叉）
        _arm(off, "L", 70, -25, -28)
        _arm(off, "R", _mir(70), _mir(-25), _mir(-28) + 7)
        off.update({"spine": -3, "neck": -4, "head": -4})
    elif name == "walk":
        s = math.sin(2 * math.pi * t)
        s2 = math.sin(2 * math.pi * (t - LAG))
        off.update({"thighL": 18 * s, "shinL": -12 * abs(s), "thighR": -18 * s,
                    "shinR": 12 * abs(s), "spine": 1.5,
                    "hip": math.sin(4 * math.pi * t) * 1.4,
                    "head": math.sin(2 * math.pi * t) * 1.5})
        _arm(off, "L", 96 + 24 * s2, 92 + 16 * s2, 92)
        _arm(off, "R", 84 - 24 * s2, 88 - 16 * s2, 88)
    elif name == "run":
        s = math.sin(2 * math.pi * t * 2)
        off.update({"thighL": 34 * s, "shinL": -26 * abs(s), "thighR": -34 * s,
                    "shinR": 26 * abs(s), "spine": 8,
                    "hip": math.sin(8 * math.pi * t) * 2})
        _arm(off, "L", 96 + 44 * s, -34, -40)              # 屈肘前后摆
        _arm(off, "R", 84 - 44 * s, _mir(-34), _mir(-40))
    elif name == "stretch":                                # 双手向上伸展
        k = ease(math.sin(math.pi * t))
        _both(off, 96 - 196 * k, 92 - 214 * k, 92 - 214 * k)
        off.update({"spine": -8 * k, "hip": 2 * k, "thighL": -3 * k, "thighR": 3 * k,
                    "neck": -4 * k})
    elif name == "squat":                                  # 下蹲（两腿对称弯）
        k = ease(math.sin(math.pi * t))
        off.update({"thighL": 46 * k, "thighR": -46 * k, "shinL": -40 * k,
                    "shinR": 40 * k, "spine": 14 * k, "hip": 18 * k})
        _both(off, 96 - 6 * k, 92 - 44 * k, 92 - 46 * k)
    elif name == "look_around":
        s = math.sin(2 * math.pi * t)
        off.update({"neck": 16 * s, "head": 10 * s, "spine": 3 * s, "hip": -1.5 * s})
        _arm(off, "L", 96 + 4 * s, 92, 92)
        _arm(off, "R", 84 - 4 * s, 88, 88)
    elif name == "check_time":                             # 左手抬腕看表
        k = ease(math.sin(math.pi * t))
        _arm(off, "L", 96 - 86 * k, 92 - 168 * k, 92 - 176 * k)
        off.update({"neck": 5 * k, "head": 4 * k, "spine": 3 * k})
    elif name == "scratch_head":                           # 右手挠头
        k = math.sin(2 * math.pi * t * 2)
        _arm(off, "R", -46, -140 + 8 * k, -150 + 8 * k)
        _arm(off, "L", 98, 92, 92)
        off.update({"neck": 4, "head": 3, "spine": 2})
    elif name == "think":                                  # 托腮思考（右手托下巴、左手托肘）
        s = math.sin(2 * math.pi * t * 1.5)
        _arm(off, "R", 34 + 3 * s, -92 + 5 * s, -100 + 4 * s)
        _arm(off, "L", 30, -76, -80)
        off.update({"neck": 7 + 1.5 * s, "head": 6 + 2 * s, "spine": 3})
    elif name == "tired":                                  # 疲惫垂肩
        off.update({"spine": 16, "neck": 10, "head": 8,
                    "thighL": 4, "thighR": -4})
        _both(off, 106, 100, 100)     # 手臂前垂：明显大于呼吸层，看得出疲惫
    if name in _HELD:                                     # 摆姿势类 → 加起势/收势
        k = _hold(t)
        off = {j: v * k for j, v in off.items()}
    if name != "idle":                                    # idle 自带呼吸，不叠加
        for j, v in _breath(t).items():
            off[j] = off.get(j, 0.0) + v
    return off


def blink(t_sec: float, *, seed: float = 0.0) -> float:
    """眨眼开合（0=睁，1=闭）：固定周期 + 轻微抖动，不机械。"""
    period = BLINK_PERIOD + 0.4 * math.sin(seed + t_sec * 0.37)
    phase = (t_sec % period) / period
    if phase > BLINK_LEN / period:
        return 0.0
    return math.sin(math.pi * (phase / (BLINK_LEN / period)))


def by_category() -> dict:
    out: dict = {}
    for c in CLIPS:
        name, cat = CLIP_META.get(c, (c, "其他"))
        out.setdefault(cat, []).append({"id": c, "名称": name})
    return out


__all__ = ["CLIPS", "CLIP_META", "SEQUENCES", "BLINK_PERIOD", "BLINK_LEN", "offsets",
           "blink", "by_category", "ease"]
