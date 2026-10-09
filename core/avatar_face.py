# core/avatar_face.py —— 数字人形象引擎：全肢体骨架绑定的兔耳女秘书（照原图 1:1）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-07）："按照原图一比一自作"，参考图为：
#   银灰长卷发 · 灰西装外套 + 白衬衫 + 灰领带 · 紧身灰短裙 · 白色高跟鞋 ·
#   兔耳（内侧粉）· 背后绒球尾巴 · 修长指甲；表情表：专注/恐惧/生气/开心/变身。
#
# ★血泪坑（靠"渲染出来亲眼看"才发现，字符串检查一概查不出）：
#   SVG 属性**必须加引号**！写成 `fill=url(#hairG)/>` 时 HTML 解析器会把斜杠吞进属性值
#   （fill="url(#hairG)/"），元素因此没自闭合 —— 后面的脸/眼睛/嘴全被解析成 <path> 的
#   子元素，而 SVG 不渲染 path 的子元素，于是"整张脸消失"。
#   做法：复用已验证的正向运动学（core/avatar_rig.joints），把美术画在骨段上。
import math

from core import avatar_motion as _M
from core import avatar_rig as _R

# 表情表按原图：专注 / 恐惧 / 生气 / 开心 / 变身（+ 平静作默认）
EXPRESSIONS = ("neutral", "focused", "fear", "angry", "happy", "transform")

_EXPR = {
    "neutral":   {"brow": 0,  "mouth": "soft",  "eye": 1.00, "ear": 1.00, "claw": 1.0},
    "focused":   {"brow": -2, "mouth": "soft",  "eye": 0.88, "ear": 1.02, "claw": 1.0},
    "fear":      {"brow": 11, "mouth": "open",  "eye": 1.30, "ear": 1.06, "claw": 1.0},
    "angry":     {"brow": 9,  "mouth": "flat",  "eye": 0.86, "ear": 0.98, "claw": 1.1},
    "happy":     {"brow": -5, "mouth": "smile", "eye": 0.82, "ear": 1.04, "claw": 1.0},
    "transform": {"brow": 0,  "mouth": "open",  "eye": 1.16, "ear": 1.22, "claw": 2.4},
}

VIEW = (240.0, 470.0)          # 全身画布
HIP = (120.0, 250.0)           # 髋关节落点
HEAD_SCALE = 0.70              # 头身比：可爱向（照原图的大头比例，脸要看得清）

# ── 原图配色 ──
HAIR_HI, HAIR_MID, HAIR_LO = "#dedee8", "#b4b4c4", "#83838f"   # 银灰长发
SKIN_HI, SKIN_LO = "#f7e9e6", "#ead4d0"                        # 冷白皮
SUIT_HI, SUIT_LO = "#6f7280", "#4a4d58"                        # 灰西装
SHIRT = "#f8f8fa"
TIE_HI, TIE_LO = "#8a8d9c", "#6b6e7c"                          # 灰领带
SHOE = "#f3f3f6"                                               # 白色高跟
INNER_EAR = "#dda9b2"
LASH = "#3b3b47"
LIPS = "#c2707f"
SKIRT_HI, SKIRT_LO = "#7a7e90", "#5a5e70"


def _mouth_path(kind: str, open_amt: float) -> str:
    """口型：说话时按 open_amt 开合；表情给基础形状（原图唇形小而饱满）。"""
    w, y = 17, 0
    if kind == "smile":
        return (f"M {100-w} {y+96} Q 100 {y+110}, {100+w} {y+96} "
                f"Q 100 {y+102}, {100-w} {y+96} Z")
    if kind == "flat":
        return (f"M {100-w} {y+99} L {100+w} {y+99} L {100+w} {y+103} "
                f"L {100-w} {y+103} Z")
    h = 5 + 13 * max(0.0, min(1.0, open_amt))
    return (f"M {100-w} {y+94} Q 100 {y+94+2}, {100+w} {y+94} "
            f"Q 100 {y+94+2+h*2}, {100-w} {y+94} Z")


def _bone_geo(pos: dict, a: str, b: str) -> tuple | None:
    """骨段几何：(x1,y1,x2,y2,长度,角度°)。缺关节返回 None。"""
    if a not in pos or b not in pos:
        return None
    x1, y1 = pos[a]
    x2, y2 = pos[b]
    ln = math.hypot(x2 - x1, y2 - y1) or 1.0
    return (x1, y1, x2, y2, ln, math.degrees(math.atan2(y2 - y1, x2 - x1)))


def _limb(pos: dict, a: str, b: str, *, w1: float, w2: float, fill: str) -> str:
    """沿骨段画一段带收细的肢体（梯形 + 两端圆头），跟着骨向量转。"""
    g = _bone_geo(pos, a, b)
    if not g:
        return ""
    x1, y1, _, _, ln, ang = g
    return (f'<g transform="translate({x1:.1f},{y1:.1f}) rotate({ang:.1f})">'
            f'<path d="M 0,{-w1/2:.1f} L {ln:.1f},{-w2/2:.1f} L {ln:.1f},{w2/2:.1f} '
            f'L 0,{w1/2:.1f} Z" fill="{fill}"/>'
            f'<circle cx="0" cy="0" r="{w1/2:.1f}" fill="{fill}"/>'
            f'<circle cx="{ln:.1f}" cy="0" r="{w2/2:.1f}" fill="{fill}"/></g>')


def _torso(pos: dict) -> str:
    """灰西装外套：小翻领 + 白衬衫三角 + 灰领带（照原图）。"""
    if not all(k in pos for k in ("hip", "spine", "neck")):
        return ""

    def perp(a: str, b: str, half: float) -> tuple:
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        return ((x1 + nx * half, y1 + ny * half), (x1 - nx * half, y1 - ny * half),
                (x2 + nx * half, y2 + ny * half), (x2 - nx * half, y2 - ny * half))

    _, _, spA, spB = perp("hip", "spine", 26.0)
    _, _, nkA, nkB = perp("spine", "neck", 16.0)
    hx, hy = pos["hip"]
    shA, shB = ((hx + 30.0, hy - 4.0), (hx - 30.0, hy - 4.0))
    pts = [shA, spA, nkA, nkB, spB, shB]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    nx, ny = pos["neck"]
    sx, sy = pos["spine"]
    return (
        f'<polygon points="{poly}" fill="url(#suitG)" stroke="#8d93a4" stroke-width="1"/>'
        # 白衬衫三角（露在西装里面）
        f'<path d="M {nx-13:.1f},{ny+1:.1f} L {nx:.1f},{ny+30:.1f} L {nx+13:.1f},{ny+1:.1f} Z" '
        f'fill="{SHIRT}"/>'
        # 领子
        f'<path d="M {nx-14:.1f},{ny+1:.1f} L {nx-4:.1f},{ny+9:.1f} L {nx:.1f},{ny+3:.1f} '
        f'L {nx+4:.1f},{ny+9:.1f} L {nx+14:.1f},{ny+1:.1f} L {nx+9:.1f},{ny-4:.1f} '
        f'L {nx:.1f},{ny+1:.1f} L {nx-9:.1f},{ny-4:.1f} Z" fill="{SHIRT}"/>'
        # 灰领带（窄长，照原图）
        f'<path d="M {nx:.1f},{ny+7:.1f} L {nx-5:.1f},{(ny+sy)/2:.1f} '
        f'L {nx:.1f},{(sy+hy)/2+10:.1f} L {nx+5:.1f},{(ny+sy)/2:.1f} Z" fill="url(#tieG)"/>'
        f'<path d="M {nx-4:.1f},{ny+3:.1f} L {nx+4:.1f},{ny+3:.1f} L {nx+3:.1f},{ny+9:.1f} '
        f'L {nx-3:.1f},{ny+9:.1f} Z" fill="{TIE_LO}"/>'
        # 西装翻领
        f'<path d="M {nx-13:.1f},{ny+2:.1f} L {sx-15:.1f},{sy+3:.1f}" stroke="#cfd3de" '
        f'stroke-width="2.4" fill="none" opacity="0.9"/>'
        f'<path d="M {nx+13:.1f},{ny+2:.1f} L {sx+15:.1f},{sy+3:.1f}" stroke="#cfd3de" '
        f'stroke-width="2.4" fill="none" opacity="0.9"/>'
        # 西装下摆开衩 + 口袋线
        f'<path d="M {sx-26:.1f},{sy+18:.1f} L {sx-24:.1f},{sy+30:.1f}" stroke="#3d404c" '
        f'stroke-width="1.2" opacity="0.7"/>'
        f'<path d="M {sx+26:.1f},{sy+18:.1f} L {sx+24:.1f},{sy+30:.1f}" stroke="#3d404c" '
        f'stroke-width="1.2" opacity="0.7"/>')


def _skirt(pos: dict) -> str:
    """紧身灰短裙（原图：很短、贴身、微微外扩）。"""
    hx, hy = pos.get("hip", (HIP[0], HIP[1]))
    top, bot = hy + 4.0, hy + 36.0
    return (f'<path d="M {hx-26:.1f},{top:.1f} L {hx+26:.1f},{top:.1f} '
            f'L {hx+30:.1f},{bot:.1f} L {hx-30:.1f},{bot:.1f} Z" fill="url(#skirtG)" '
            f'stroke="#8d93a4" stroke-width="1"/>'
            f'<path d="M {hx-12:.1f},{top+2:.1f} L {hx-14:.1f},{bot-2:.1f} M '
            f'{hx+12:.1f},{top+2:.1f} L {hx+14:.1f},{bot-2:.1f}" stroke="#3d404c" '
            f'stroke-width="0.9" opacity="0.55"/>')


def _tail(pos: dict) -> str:
    """背后绒球尾巴：画在裙后偏一侧，前面看是从裙边探出来的毛球（原图背视图）。"""
    hx, hy = pos.get("hip", (HIP[0], HIP[1]))
    cx, cy = hx - 30.0, hy + 11.0
    fluff = "".join(
        f'<circle cx="{cx + dx:.1f}" cy="{cy + dy:.1f}" r="{r:.1f}" fill="{HAIR_MID}" '
        f'opacity="0.95"/>'
        for dx, dy, r in ((8, -5, 6), (-8, -5, 6), (7, 6, 5.5),
                          (-7, 6, 5.5), (0, -9, 5.5), (0, 6, 5)))
    return (f'{fluff}<circle cx="{cx:.1f}" cy="{cy:.1f}" r="10" fill="{HAIR_HI}" '
            f'opacity="0.98" stroke="{HAIR_LO}" stroke-width="0.8"/>'
            f'<ellipse cx="{cx-3:.1f}" cy="{cy-3:.1f}" rx="3.6" ry="2.6" fill="#ffffff" '
            f'opacity="0.5"/>')


def _heels(pos: dict, side: str) -> str:
    """白色高跟鞋：泵式鞋身 + 细跟，脚尖朝外（原图）。

    不能用骨向量旋转画：脚骨是朝左右的，一转就把鞋横过来像脚蹼（踩过）。
    所以固定竖直绘制，只按左右镜像脚尖方向。
    """
    g = _bone_geo(pos, f"shin{side}", f"foot{side}")
    if not g:
        return ""
    _, _, x2, y2, _, _ = g
    s = -1.0 if side == "L" else 1.0
    return (f'<g transform="translate({x2:.1f},{y2:.1f})">'
            f'<path d="M {-7*s},-9 L {7*s},-7 L {12*s},7 L {-9*s},8 Z" fill="{SHOE}" '
            f'stroke="#c8c8d2" stroke-width="1"/>'
            f'<path d="M {-7*s},-7 L {7*s},-5" stroke="#e2e2ea" stroke-width="1.4"/>'
            f'<path d="M {5*s},7 L {1*s},27 L {9*s},7 Z" fill="#f6f6fa" '
            f'stroke="#c8c8d2" stroke-width="0.8"/>'
            f'<ellipse cx="{2*s}" cy="8" rx="10" ry="2.4" fill="#dcdce4" opacity="0.85"/>'
            f'</g>')


def _nails(pos: dict, side: str, *, claw: float) -> str:
    """修长指甲：沿手骨方向伸出的小尖刺；变身档更长更利（原图长指甲）。"""
    g = _bone_geo(pos, f"fore{side}", f"hand{side}")
    if not g:
        return ""
    _, _, x2, y2, _, ang = g
    ln = 5.0 + 7.0 * float(claw)
    rows = []
    for k, off in enumerate((-2.6, 0.0, 2.6)):
        rows.append(f'<path d="M 3,{off:.1f} Q {ln*0.7:.1f},{off*1.25:.1f} {ln:.1f},'
                    f'{off*1.05:.1f}" stroke="{LASH}" stroke-width="1.1" fill="none" '
                    f'stroke-linecap="round" opacity="0.9"/>')
    return (f'<g transform="translate({x2:.1f},{y2:.1f}) rotate({ang:.1f})">'
            f'{"".join(rows)}</g>')


def _ears(pos: dict, *, scale: float) -> str:
    """兔耳：生在头顶，外灰内粉；transform 档会更挺更长（原图变身）。"""
    hx, hy = pos.get("head", (HIP[0], HIP[1] - 118))
    ln = 62.0 * scale
    rows = []
    for sign, tilt in ((-1, -20.0), (1, 20.0)):
        bx = hx + sign * 12.0
        tip_x = bx + sign * ln * 0.34
        tip_y = hy - ln
        rows.append(
            f'<path d="M {bx:.1f},{hy-8:.1f} Q {bx + sign*ln*0.30:.1f},{hy-ln*0.60:.1f} '
            f'{tip_x:.1f},{tip_y:.1f} Q {bx + sign*ln*0.20:.1f},{hy-ln*0.58:.1f} '
            f'{bx + sign*11:.1f},{hy-6:.1f} Z" fill="url(#hairG)" stroke="#9a9aa8" '
            f'stroke-width="1"/>'
            f'<path d="M {bx + sign*3:.1f},{hy-14:.1f} Q {bx + sign*ln*0.24:.1f},'
            f'{hy-ln*0.58:.1f} {tip_x - sign*5:.1f},{tip_y + 10:.1f} Q '
            f'{bx + sign*ln*0.16:.1f},{hy-ln*0.54:.1f} {bx + sign*8:.1f},{hy-12:.1f} Z" '
            f'fill="{INNER_EAR}" opacity="0.85"/>')
    return "".join(rows)


def _head_group(pos: dict, *, expression: str, talking: bool, blink: float,
                 mouth_open: float) -> str:
    """头部组：跟着 neck→head 的骨向量转；含兔耳、银灰长卷发、表情层与口型。"""
    g = _bone_geo(pos, "neck", "head")
    if not g:
        return ""
    _, _, hx, hy, _, ang = g
    rot = ang + 90.0
    e = _EXPR.get(expression, _EXPR["neutral"])
    bl = max(0.0, min(1.0, float(blink or 0)))
    eye_ry = 8.6 * (1 - 0.94 * bl) * e["eye"]
    talk_h = (9 if talking else 0) + (7 if e["mouth"] == "open" else 0)
    mo = max(mouth_open, talk_h / 16.0)
    if talking or e["mouth"] == "open":
        mouth = _mouth_path("open", mo)
    elif e["mouth"] == "flat":
        mouth = _mouth_path("flat", 0)
    else:
        mouth = _mouth_path("smile", 0) if e["mouth"] == "smile" else _mouth_path("soft", 0)
    bd = e["brow"]
    return (
        # 兔耳在发层之下（从发里长出来）
        _ears(pos, scale=e["ear"]) +
        f'<g transform="translate({hx:.1f},{hy:.1f}) rotate({rot:.1f}) '
        f'scale({HEAD_SCALE}) translate(-100,-150)">'
        # 长发后层：银灰、长到胸口、带波浪
        '<path d="M 100 56 Q 44 60 40 130 Q 36 196 48 246 Q 56 268 70 272 Q 62 236 62 190 '
        'L 64 118 Q 76 94 100 90 Q 124 94 136 118 L 138 190 Q 138 236 130 272 Q 144 268 152 246 '
        'Q 164 196 160 130 Q 156 60 100 56 Z" fill="url(#hairG)" opacity="0.97"/>'
        # 发丝波浪（原图的卷曲感）
        '<path d="M 52 150 Q 44 190 56 232 M 148 150 Q 156 190 144 232 '
        'M 66 210 Q 60 244 74 262 M 134 210 Q 140 244 126 262" stroke="#6f6f7d" '
        'stroke-width="1.2" fill="none" opacity="0.5"/>'
        # 两侧发梢的卷（原图的蓬松卷感；别横穿胸口，否则像挂了条腰带）
        '<path d="M 52 236 q 6 12 12 3 q 7 11 13 3" fill="none" stroke="url(#hairG)" '
        'stroke-width="6" stroke-linecap="round" opacity="0.95"/>'
        '<path d="M 148 236 q -6 12 -12 3 q -7 11 -13 3" fill="none" stroke="url(#hairG)" '
        'stroke-width="6" stroke-linecap="round" opacity="0.95"/>'
        # 脸（冷白皮、尖下巴）
        '<path d="M 100 74 Q 66 74 62 112 Q 58 152 74 176 Q 88 196 100 198 Q 112 196 126 176 '
        'Q 142 152 138 112 Q 134 74 100 74 Z" fill="url(#skinG)" stroke="#e0c9c6" '
        'stroke-width="0.7"/>'
        # 刘海：中分微斜
        '<path d="M 62 112 Q 60 82 100 78 Q 140 82 138 112 L 128 100 Q 118 88 100 86 '
        'Q 82 88 72 100 Z" fill="url(#hairG)"/>'
        '<path d="M 74 96 Q 88 82 104 84 M 126 96 Q 116 84 104 84" '
        'fill="none" stroke="#ffffff" stroke-width="1.5" opacity="0.5"/>'
        # 眉（细、深）
        f'<path d="M 76 124 L 92 120" stroke="{LASH}" stroke-width="2.2" '
        f'stroke-linecap="round" transform="rotate({bd} 84 122)"/>'
        f'<path d="M 108 120 L 124 124" stroke="{LASH}" stroke-width="2.2" '
        f'stroke-linecap="round" transform="rotate({-bd} 116 122)"/>'
        # 眼：杏仁形 + 浓密上睫 + 浅灰瞳
        f'<path d="M 74 136 Q 84 128 94 136 Q 84 146 74 136 Z" fill="#fdfdff" '
        f'stroke="{LASH}" stroke-width="1.1"/>'
        f'<path d="M 106 136 Q 116 128 126 136 Q 116 146 106 136 Z" fill="#fdfdff" '
        f'stroke="{LASH}" stroke-width="1.1"/>'
        f'<path d="M 74 136 Q 84 128 94 136" stroke="{LASH}" stroke-width="2.1" fill="none" '
        f'stroke-linecap="round"/>'
        f'<path d="M 106 136 Q 116 128 126 136" stroke="{LASH}" stroke-width="2.1" fill="none" '
        f'stroke-linecap="round"/>'
        f'<ellipse cx="84" cy="136.5" rx="4.2" ry="{eye_ry:.2f}" fill="url(#irisG)"/>'
        f'<ellipse cx="116" cy="136.5" rx="4.2" ry="{eye_ry:.2f}" fill="url(#irisG)"/>'
        f'<circle cx="84" cy="136.5" r="1.9" fill="#2f3742"/>'
        f'<circle cx="116" cy="136.5" r="1.9" fill="#2f3742"/>'
        '<circle cx="85.4" cy="134.8" r="1.2" fill="#ffffff"/>'
        '<circle cx="117.4" cy="134.8" r="1.2" fill="#ffffff"/>'
        f'<path d="M 74 131 Q 84 126 94 130" stroke="{LASH}" stroke-width="0.9" fill="none" '
        f'opacity="0.8"/>'
        f'<path d="M 106 130 Q 116 126 126 131" stroke="{LASH}" stroke-width="0.9" fill="none" '
        f'opacity="0.8"/>'
        # 鼻 + 唇（小而饱满）
        '<path d="M 99 152 L 101 158 L 98 159" fill="none" stroke="#d8b3ae" stroke-width="1.3"/>'
        f'<path d="{mouth}" fill="{LIPS}" stroke="#a85a68" stroke-width="1"/>'
        # 腮红 + 耳饰（原图长耳坠）
        '<ellipse cx="74" cy="152" rx="6" ry="3" fill="#e6a2a2" opacity="0.42"/>'
        '<ellipse cx="126" cy="152" rx="6" ry="3" fill="#e6a2a2" opacity="0.42"/>'
        '<path d="M 60 140 L 60 156" stroke="#c9ccd6" stroke-width="1.1"/>'
        '<path d="M 140 140 L 140 156" stroke="#c9ccd6" stroke-width="1.1"/>'
        '<circle cx="60" cy="159" r="2.6" fill="#cfd3de"/>'
        '<circle cx="140" cy="159" r="2.6" fill="#cfd3de"/>'
        '</g>')


def character_svg(*, expression: str = "neutral", talking: bool = False,
                  blink: float = 0.0, mood_glow: str = "cyan",
                  clip: str = "idle", t: float = 0.0) -> str:
    """全身数字人形象（照原图 1:1）：兔耳女秘书 + **全肢体绑骨**（32 动作作用到四肢）。

    clip/t 决定姿态；expression 管表情（专注/恐惧/生气/开心/变身）；talking/blink 管口型与眨眼。
    零外链、纯内联，可直接 innerHTML。属性一律带引号（见文件头血泪坑）。
    """
    name = clip if clip in _M.CLIPS else "idle"
    ang = _R.pose(name, float(t or 0.0), mouth=0.0)
    pos = _R.joints(ang, origin=HIP)
    w, h = VIEW
    claw = _EXPR.get(expression, _EXPR["neutral"])["claw"]
    parts = [
        f'<svg viewBox="0 0 {w:.0f} {h:.0f}" width="100%" height="auto" role="img" '
        f'aria-label="数字人形象（兔耳女秘书·全身绑骨）" data-clip="{name}" '
        f'data-expr="{expression}">',
        '<defs>'
        f'<linearGradient id="hairG" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{HAIR_HI}"/><stop offset="0.55" stop-color="{HAIR_MID}"/>'
        f'<stop offset="1" stop-color="{HAIR_LO}"/></linearGradient>'
        f'<linearGradient id="skinG" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{SKIN_HI}"/><stop offset="1" stop-color="{SKIN_LO}"/>'
        f'</linearGradient>'
        f'<linearGradient id="suitG" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{SUIT_HI}"/><stop offset="1" stop-color="{SUIT_LO}"/>'
        f'</linearGradient>'
        f'<linearGradient id="skirtG" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{SKIRT_HI}"/><stop offset="1" stop-color="{SKIRT_LO}"/>'
        f'</linearGradient>'
        f'<linearGradient id="tieG" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{TIE_HI}"/><stop offset="1" stop-color="{TIE_LO}"/>'
        f'</linearGradient>'
        '<radialGradient id="irisG"><stop offset="0" stop-color="#cdd7e2"/>'
        '<stop offset="0.55" stop-color="#8d99a8"/><stop offset="1" stop-color="#4d5766"/>'
        '</radialGradient>'
        '<radialGradient id="glowG"><stop offset="0" stop-color="rgba(57,208,255,0.26)"/>'
        '<stop offset="1" stop-color="transparent"/></radialGradient>'
        '</defs>',
        f'<circle cx="{w/2:.0f}" cy="{h*0.42:.0f}" r="{h*0.36:.0f}" fill="url(#glowG)"/>',
    ]
    # ── 尾巴（在裙后，先画）──
    parts.append(_tail(pos))
    # ── 下肢：裸腿（原图）+ 白色高跟 ──
    parts.append(_limb(pos, "hip", "thighL", w1=21, w2=15, fill="url(#skinG)"))
    parts.append(_limb(pos, "hip", "thighR", w1=21, w2=15, fill="url(#skinG)"))
    parts.append(_limb(pos, "thighL", "shinL", w1=14, w2=9.5, fill="url(#skinG)"))
    parts.append(_limb(pos, "thighR", "shinR", w1=14, w2=9.5, fill="url(#skinG)"))
    parts.append(_heels(pos, "L"))
    parts.append(_heels(pos, "R"))
    # ── 西装外套 + 紧身短裙 ──
    parts.append(_torso(pos))
    parts.append(_skirt(pos))
    # ── 头（含兔耳/长发/表情），手臂压在上面 ──
    parts.append(_limb(pos, "neck", "head", w1=14, w2=13, fill="url(#skinG)"))
    parts.append(_head_group(pos, expression=expression, talking=talking,
                             blink=blink, mouth_open=0.0))
    # ── 上肢：灰西装袖 + 裸小臂 + 手 + 长指甲 ──
    for side in ("L", "R"):
        parts.append(_limb(pos, f"shoulder{side}", f"arm{side}", w1=15, w2=12,
                           fill="url(#suitG)"))
        parts.append(_limb(pos, f"arm{side}", f"fore{side}", w1=12, w2=9.5,
                           fill="url(#suitG)"))
        parts.append(_limb(pos, f"fore{side}", f"hand{side}", w1=9, w2=8,
                           fill="url(#skinG)"))
        parts.append(_nails(pos, side, claw=claw))
    parts.append('</svg>')
    return "".join(parts)


def joint_positions(*, clip: str = "idle", t: float = 0.0) -> dict:
    """此刻的全身关节坐标（诊断/测试用）。"""
    name = clip if clip in _M.CLIPS else "idle"
    return _R.joints(_R.pose(name, float(t or 0.0)), origin=HIP)


def expressions() -> list:
    return list(EXPRESSIONS)


def status() -> dict:
    return {"形象": "兔耳女秘书（银灰长卷发 / 灰西装+白衬衫+灰领带 / 紧身灰短裙 / 白色高跟 / 绒球尾 / 长指甲）",
            "骨架关节": len(_R.JOINTS), "动作数": len(_M.CLIPS),
            "表情": list(EXPRESSIONS),
            "表情对照": {"focused": "专注", "fear": "恐惧", "angry": "生气",
                     "happy": "开心", "transform": "变身（兔耳挺起·指甲变长）"},
            "动画": ["全肢体动作（32 个，作用到手臂/腿/躯干）", "眨眼", "说话口型", "表情驱动眉眼"],
            "口径": "照主人给的原图 1:1 重绘；角色美术绑在同一套 FK 骨链上（与骨架版共用数学）；零外链"}


__all__ = ["character_svg", "expressions", "status", "EXPRESSIONS", "joint_positions"]
