# tests/test_persona_fullbody.py —— 全身绑骨 + 可爱泼辣人格 + 固定女声台湾腔
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-07）：
#   "全肢体动作骨架给我绑好，要以可爱的性格泼辣的性格能讨好也能发火骂人固定女声台湾腔"
# 纪律：动作要验"肢体真的在动"（不是坐标合法）；人格要验"三档都能出词"；
#       嗓子要验"男声永不入选"（本机回退表里曾混进男声 Kangkang）。
import math
from html.parser import HTMLParser

import pytest

from core import avatar_face as AF
from core import avatar_motion as M
from core import persona as P
from senses import voice_sapi as VS

_MALE = ("kangkang", "david", "mark", "male", "男")


def _disp(c1: str, c2: str, t: float = 0.5) -> float:
    a = AF.joint_positions(clip=c1, t=t)
    b = AF.joint_positions(clip=c2, t=t)
    return max(math.hypot(a[k][0] - b[k][0], a[k][1] - b[k][1]) for k in a)


def test_full_body_svg_has_limbs_and_all_joints():
    """角色是**全身**：18 个关节都在，四肢骨段都画出来（不是只到腰的半身）。"""
    pos = AF.joint_positions(clip="idle", t=0.0)
    assert len(pos) == 18
    for j in ("hip", "spine", "neck", "head", "armL", "armR", "foreL", "foreR",
              "handL", "handR", "thighL", "thighR", "shinL", "shinR", "footL", "footR"):
        assert j in pos, f"缺关节 {j}"
    svg = AF.character_svg(clip="wave")
    assert 'aria-label="数字人形象（兔耳女秘书·全身绑骨）"' in svg
    assert 'data-clip="wave"' in svg
    # 原图的标志性元素都要画出来：兔耳 / 灰领带 / 紧身短裙 / 白色高跟 / 绒球尾 / 长指甲
    assert 'data-expr="neutral"' in svg
    assert svg.count("<path") >= 30, "四肢/衣饰/五官/兔耳都要画出来"
    assert "url(#skirtG)" in svg and "url(#tieG)" in svg and "url(#suitG)" in svg


class _ShapeTree(HTMLParser):
    """自己搭标签栈检查 SVG 结构（不解析 XML，避免实体扩展风险）。

    抓的就是真事故：无引号属性值 + "/>" 会把斜杠吞进值里（fill="url(#hairG)/"），
    元素因此**没有自闭合**，后面的脸/眼睛/嘴被解析成 <path> 的子元素，而 SVG 不渲染
    path 的子元素 → 整张脸消失。只用字符串包含判断是查不出来的（踩过）。
    """

    LEAF = ("path", "ellipse", "circle", "rect", "polygon", "line", "stop")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list = []
        self.head_children = 0
        self.slash_value: list = []      # 属性值以 "/" 结尾 = 斜杠被吞（缺引号）
        self.nested_leaf: list = []      # 叶子元素里套了别的元素
        self.in_head = False

    def handle_starttag(self, tag, attrs):
        parent = self.stack[-1] if self.stack else ""
        if parent in self.LEAF:
            self.nested_leaf.append((parent, tag))
        for k, v in attrs:
            if v and str(v).endswith("/"):
                self.slash_value.append((k, v))
        if self.in_head and parent == "g":
            self.head_children += 1
        self.stack.append(tag)
        if tag == "g":
            self.in_head = True

    def handle_startendtag(self, tag, attrs):
        parent = self.stack[-1] if self.stack else ""
        if parent in self.LEAF:
            self.nested_leaf.append((parent, tag))
        for k, v in attrs:
            if v and str(v).endswith("/"):
                self.slash_value.append((k, v))
        if self.in_head and parent == "g":
            self.head_children += 1

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass


def test_svg_shape_structure_so_face_actually_renders():
    """结构检查：属性带引号（斜杠没被吞）+ 脸/眼/嘴都在头部组里（没变成别人的子元素）。"""
    for clip in M.CLIPS:
        svg = AF.character_svg(clip=clip, t=0.4, expression="happy",
                               talking=True, blink=0.3)
        assert len(svg) < 40000, f"{clip}：SVG 异常巨大"
        t = _ShapeTree()
        t.feed(svg)
        assert not t.slash_value, f"{clip}：属性值被吞了斜杠（缺引号）→ {t.slash_value[:2]}"
        assert not t.nested_leaf, f"{clip}：叶子元素里套了子元素（脸被吞）→ {t.nested_leaf[:3]}"
        assert t.head_children >= 18, f"{clip}：头部组只有 {t.head_children} 个子元素"
    html = AF.character_svg(clip="idle")
    for need in ("url(#skinG)", "url(#hairG)", "url(#irisG)"):
        assert need in html, f"头部组缺少引用 {need}"
    assert html.count("<ellipse") >= 5, "脸/眼睛/腮红都要画出来"


def test_rig_is_left_right_symmetric():
    """骨架必须左右镜像（旧表左臂基角朝上 = "铁架子"的根源）。"""
    from core import avatar_rig as R
    pos = R.joints(R.pose("idle", 0.0), origin=(120.0, 250.0))
    cx = pos["hip"][0]
    for a, b in (("shoulderL", "shoulderR"), ("armL", "armR"), ("foreL", "foreR"),
                 ("handL", "handR"), ("thighL", "thighR"), ("shinL", "shinR"),
                 ("footL", "footR")):
        dl, dr = cx - pos[a][0], pos[b][0] - cx
        assert abs(dl - dr) < 1.0, f"{a}/{b} 不镜像（{dl:.1f} vs {dr:.1f}）"
    # 手臂与腿都必须**朝下**（旧 bug：左臂基角朝上，像插在头上的杆子）
    for j in ("armL", "armR", "foreL", "foreR", "handL", "handR"):
        assert pos[j][1] > pos["shoulderL"][1], f"{j} 没有朝下垂"
    for j in ("thighL", "thighR", "shinL", "shinR", "footL", "footR"):
        assert pos[j][1] > pos["hip"][1], f"{j} 没有朝下垂"
    # 肩膀在颈的高度附近（略高于颈基关节才对，不是吊在脖子下面当坠子）
    assert abs(pos["shoulderL"][1] - pos["neck"][1]) < 25
    assert pos["hip"][1] > pos["neck"][1]


def test_every_clip_moves_the_limbs_not_just_coords():
    """32 个动作里每个都必须让**肢体**真的位移（曾出现"坐标合法但一动不动"）。"""
    dead = []
    for c in M.CLIPS:
        if c == "idle":
            continue
        best = max(_disp("idle", c, t) for t in (0.15, 0.3, 0.5, 0.7, 0.85))
        if best < 1.0:
            dead.append((c, round(best, 2)))
    assert not dead, f"这些动作肢体没动：{dead}"


def test_leg_clips_actually_move_the_legs():
    """腿部动作要作用到**下肢**（走路/跑步/下蹲/伸展），不能只摆手。

    相位必须采密：run 用 sin(2π·2t)，在 t=0.25 恰好归零 —— 只采一点会误判"没动"。
    """
    for c in ("walk", "run", "squat", "bow_deep"):
        a = AF.joint_positions(clip="idle", t=0.25)
        best = 0.0
        for t in (0.05, 0.15, 0.25, 0.35, 0.5, 0.65, 0.75, 0.85):
            b = AF.joint_positions(clip=c, t=t)
            best = max(best, max(math.hypot(a[k][0] - b[k][0], a[k][1] - b[k][1])
                                 for k in ("thighL", "shinL", "footL",
                                           "thighR", "shinR", "footR")))
        assert best > 1.0, f"{c} 下肢没动（{best:.2f}）"


def test_head_follows_neck_and_nod():
    """点头/张望要带动头部（头组跟着骨向量转，不是贴死在脖子上）。"""
    a = AF.joint_positions(clip="idle", t=0.45)
    b = AF.joint_positions(clip="nod", t=0.45)
    assert math.hypot(a["head"][0] - b["head"][0], a["head"][1] - b["head"][1]) > 0.8
    svg = AF.character_svg(clip="look_around", t=0.25)
    assert "rotate(" in svg, "头部组必须带旋转（跟随 head 骨向量）"


def test_persona_three_gears_all_have_lines_clips_and_styles():
    from body.prosody import STYLES
    for g, spec in P.GEARS.items():
        assert spec["台词"], f"{g} 没有台词"
        assert spec["动作"] and all(c in M.CLIPS for c in spec["动作"]), f"{g} 的动作不在动作库里"
        assert spec["样式"] in STYLES, f"{g} 的韵律样式 {spec['样式']} 不存在"
        assert spec["表情"] in AF.EXPRESSIONS, f"{g} 的表情不在表情表里"


def test_persona_can_coax_and_scold():
    """能讨好、也能发火骂人：两条路都要有词、有姿势。"""
    coax = P.react(事件="请求")
    fire = P.react(事件="被骂")
    assert coax["档"] == "讨好" and coax["动作"] in ("heart", "kiss", "shy", "cheer")
    assert fire["档"] == "发火" and fire["动作"] in ("angry", "point", "proud")
    assert P.gear_for(文本="拜托帮我一下好不好") == "讨好"
    assert P.gear_for(文本="你真的很烂耶") == "发火"
    assert P.gear_for(文本="今天天气不错") == "可爱"


def test_persona_taiwan_accent_and_no_slurs():
    """发火是**台湾腔的泼辣**：要有台湾腔助词，但不带脏字、不人身攻击。"""
    dirty = ("他妈", "操你", "干你", "屌", "婊", "贱", "智障", "白痴", "去死", "干林")
    tw = ("啦", "耶", "喔", "嘛", "齁", "好不好")
    for g, spec in P.GEARS.items():
        joined = "".join(spec["台词"])
        assert any(t in joined for t in tw), f"{g} 台词没有台湾腔味"
        for bad in dirty:
            assert bad not in joined, f"{g} 台词出现脏字/侮辱词：{bad}"


def test_persona_rotation_is_deterministic_and_repeating():
    """台词轮转：不重复上一句，且会循环（不是随机 —— 要可复现）。"""
    seen = [P.line("发火") for _ in range(len(P.GEARS["发火"]["台词"]) + 1)]
    assert seen[0] == seen[-1], "轮转一圈应回到第一句"
    assert seen[0] != seen[1], "不该连续重复同一句"


def test_voice_pin_is_female_taiwan_and_never_male():
    """嗓子钉死：永远是中文女声 + 台湾腔韵律；男声永不入选。"""
    got = VS.pick_voice()
    assert VS.VOICE_PIN["性别"] == "女" and VS.VOICE_PIN["锁定"] is True
    assert got.get("性别") == "女" and got.get("锁定") is True
    low = str(got.get("voice", "")).lower()
    assert not any(m in low for m in _MALE), f"选中了男声：{got.get('voice')}"
    pin = VS.pinned()
    assert pin["锁定"] is True
    assert "女" in pin["身份"] and "台湾" in pin["身份"]


def test_persona_tone_styles_are_taiwan_flavoured():
    from body.prosody import STYLES, render
    for name in ("撒娇讨好", "泼辣发火"):
        st = STYLES[name]
        assert st.interjections and st.softeners
        pr = render("人家很努力了。", st, ssml=False, intensity=0.7)
        assert pr.text != "人家很努力了。", f"{name} 没有做句末软收/助词"
        assert any(s in pr.text for s in st.softeners), f"{name} 软收没落到句尾"
    # 讨好更甜（音高更高）、发火更冲（语速更快）
    assert STYLES["撒娇讨好"].pitch > STYLES["文静台湾腔"].pitch
    assert STYLES["泼辣发火"].rate > STYLES["文静台湾腔"].rate


def test_real_speak_path_keeps_taiwan_softeners():
    """真发声路径（voice_sapi.speak）必须带 intensity，否则句末软收一句都听不到。"""
    import inspect
    from senses import voice_sapi as _VS
    src = inspect.getsource(_VS.speak)
    assert "intensity=" in src, "speak() 未给 intensity → 台湾腔句末软收会全失效"


def test_persona_and_pin_endpoints():
    from fastapi.testclient import TestClient
    from panel.server import app
    c = TestClient(app)
    st = c.get("/api/persona/state")
    assert st.status_code == 200 and st.json()["人格"]["性格"]
    for g in ("可爱", "讨好", "发火"):
        r = c.get(f"/api/persona/react?档={g}").json()
        assert r["档"] == g and r["台词"] and r["动作"] in M.CLIPS
    assert c.get("/api/persona/react?文本=你很烦耶").json()["档"] == "发火"
    pin = c.get("/api/voice/pin").json()
    assert pin["锁定"] is True
    face = c.get("/api/avatar/face?clip=angry&expr=angry&t=0.4").json()
    assert face["动作"] == "angry" and 'data-clip="angry"' in face["svg"]


def test_voice_page_exposes_persona_controls():
    from fastapi.testclient import TestClient
    from panel.server import app
    html = TestClient(app).get("/voice").text
    assert 'id=pgbtn' in html and "撒娇讨好" in html and "泼辣发火" in html
    assert "reactSet" in html and "setClip" in html


def test_avatar_matches_reference_sheet():
    """照原图 1:1 的要素都在：兔耳（粉内衬）/ 绒球尾 / 长指甲 / 灰领带 / 短裙 / 白高跟，
    表情表为 专注·恐惧·生气·开心·变身。"""
    assert AF.EXPRESSIONS == ("neutral", "focused", "fear", "angry", "happy", "transform")
    svg = AF.character_svg(clip="idle", expression="focused")
    assert AF.INNER_EAR in svg, "兔耳内衬（粉）没画"
    assert "url(#tieG)" in svg, "灰领带没画"
    assert "url(#skirtG)" in svg, "紧身短裙没画"
    assert AF.SHOE in svg, "白色高跟没画"
    assert AF.HAIR_MID in svg, "绒球尾（银灰）没画"
    # 变身档：指甲更长（原图变身）
    assert AF._EXPR["transform"]["claw"] > AF._EXPR["neutral"]["claw"]
    n_neutral = AF.character_svg(clip="idle", expression="neutral")
    n_trans = AF.character_svg(clip="idle", expression="transform")
    assert n_trans != n_neutral, "变身档没有任何变化"
    assert AF._EXPR["transform"]["ear"] > AF._EXPR["neutral"]["ear"], "变身时兔耳该挺起"
