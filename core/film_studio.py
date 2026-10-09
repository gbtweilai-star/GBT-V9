# core/film_studio.py —— 高质量影视生产线（**作为技能挂到她的工作流引擎上**）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令（2026-10-09）：「你要直接调用她的工作流，把工作流跑通，能生产出高质量视频、高质量音乐、
#   高质量电影，然后做好固化。」
#
# 与既有件的关系（不重复造轮子）：
#   · 工作流引擎 = workflows.engine.WorkflowEngine（读 DAG，节点类型 gate/branch/map/skill）
#   · 技能注册表 = skills.native.SkillRegistry（注册 NativeSkill：.name/.version/.probe/.run）
#   · 本模块把「出片」拆成 4 个**真能产出文件**的技能，然后组成 DAG 交给引擎跑：
#       film_script  剧本 → 分镜表（按句切镜 + 三机位循环 + 每镜时长）
#       film_score   剧本 → 分段配乐（前奏/主歌/副歌/尾，四轨：贝斯/铺底/琶音/鼓）
#       film_shots   分镜表 → 逐镜渲染（Blender 三机位 + 推镜 + 动作 + 运动模糊）
#       film_edit    镜头+旁白+配乐 → 多镜头成片（句界切镜 + 交叉溶解 + 片头尾 + 字幕）
# 质量取舍（实测口径）：镜头 720x1280@24 渲（EEVEE 12 采样，快），成片统一 1080x1920@30。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import math
import re
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FILM = ROOT / "render" / "film"
SHOTS = ROOT / "state" / "film_shots"
W, H, FPS = 1080, 1920, 30
SW, SH, SFPS = 720, 1280, 24
# 资产：默认用**抹过线**的版本（贴图上的折线纹已按肤色区域消除，几何未动）
RIG_CLAMP = ROOT / "state" / "tripo" / "out3" / "rig" / "rigged-clamp3.glb"   # 权重裁剪版：压力测试最优
RIG_CLEAN = ROOT / "state" / "tripo" / "out3" / "rig" / "rigged-clean.glb"
RIG_RAW = ROOT / "state" / "tripo" / "out3" / "rig" / "rigged-fix.glb"
RIG = RIG_CLAMP if RIG_CLAMP.is_file() else (RIG_CLEAN if RIG_CLEAN.is_file() else RIG_RAW)

# 三机位（宽/中/近）：距离系数、镜头、目标高度系数 —— 剪辑时交替使用才像"电影"
CAM = (
    dict(名="宽", 距离=1.05, 焦距=30, 目标=0.52),
    dict(名="中", 距离=1.30, 焦距=45, 目标=0.58),
    dict(名="近", 距离=1.85, 焦距=60, 目标=0.66),
)


def _run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.returncode, (r.stdout or b"") + (r.stderr or b"")


def _dur(p) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(p)], capture_output=True)
    try:
        return float(json.loads(r.stdout.decode("utf-8", "replace"))["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


def split_sentences(script: str) -> list:
    parts = [x.strip() for x in re.split(r"[|\n。！？!?；;]+", str(script)) if x.strip()]
    out = []
    for p in parts:
        if len(p) <= 20:
            out.append(p)
        else:
            out += [x.strip() for x in re.split(r"[，,、]", p) if x.strip()]
    return out


# ── ① 分镜 ────────────────────────────────────────────────────────────────
def shot_plan(script: str, max_shots: int = 6) -> dict:
    sents = split_sentences(script)[:max_shots]
    if not sents:
        return {"ok": False, "error": "剧本为空"}
    total_chars = sum(len(s) for s in sents) or 1
    shots = []
    for i, s in enumerate(sents):
        cam = CAM[i % len(CAM)]
        shots.append({"i": i + 1, "文": s, "机位": cam["名"], "镜头": cam["焦距"],
                      "距离": cam["距离"], "目标": cam["目标"],
                      "权重": round(len(s) / total_chars, 4)})
    return {"ok": True, "shots": shots, "镜数": len(shots), "总字数": total_chars}


# ── ② 分段配乐（四轨，比单循环更像"配乐"）───────────────────────────────
def score(script: str = "", duration: float = 30.0, out: Path | None = None, sr: int = 44100,
          mode: str = "默认") -> dict:
    SHOTS.mkdir(parents=True, exist_ok=True)
    out = Path(out or (SHOTS / "score.wav"))
    dur = max(6.0, float(duration))
    t = np.arange(int(sr * dur)) / sr
    # 结构：前奏(10%) 主歌(35%) 副歌(35%) 尾(20%)；每段换和弦，副歌加高八度琶音
    if mode == "恐怖":
        # 港式僵尸：小调五声 + 低沉锣音 + 心跳双拍（60~90 BPM）
        secs = [("引子", 0.00, 0.18, [(146.83, 174.61, 196.00)]),
                ("逼近", 0.18, 0.52, [(138.59, 164.81, 196.00)]),
                ("尸现", 0.52, 0.82, [(130.81, 155.56, 185.00)]),
                ("收尸", 0.82, 1.00, [(123.47, 146.83, 174.61)])]
        _GONG = True
    else:
        _GONG = False
    secs = secs if mode == "恐怖" else [("前奏", 0.00, 0.10, [(196.00, 246.94, 293.66)]),
            ("主歌", 0.10, 0.45, [(220.00, 261.63, 329.63), (174.61, 220.00, 261.63)]),
            ("副歌", 0.45, 0.80, [(261.63, 329.63, 392.00), (196.00, 246.94, 293.66)]),
            ("尾",   0.80, 1.00, [(174.61, 220.00, 261.63)])]
    buf = np.zeros_like(t)
    for name, a, b, chords in secs:
        m = (t >= a * dur) & (t < b * dur)
        if not m.any():
            continue
        ts = t[m] - a * dur
        seg_dur = (b - a) * dur
        bar = seg_dur / max(1, len(chords) * 2)
        for k in range(len(chords) * 2):
            f = chords[k % len(chords)]
            sm = (ts >= k * bar) & (ts < (k + 1) * bar)
            if not sm.any():
                continue
            tk = ts[sm] - k * bar
            env = np.minimum(1.0, tk / 0.3) * np.exp(-tk * (0.7 if name != "副歌" else 0.5))
            for j, fr in enumerate(f):
                buf[m][sm] += (0.13 / (j + 1)) * np.sin(2 * np.pi * fr * tk) * env
                if name == "副歌":                       # 副歌加高八度琶音
                    buf[m][sm] += 0.05 * np.sin(2 * np.pi * fr * 2 * tk) * env
                buf[m][sm] += 0.05 * np.sin(2 * np.pi * (fr / 2) * tk) * env   # 低音铺底
        if name in ("主歌", "副歌"):                      # 鼓点
            step = 0.5
            n = int(seg_dur / step)
            for i2 in range(n):
                st = int((a * dur + i2 * step) * sr)
                ln = int(0.07 * sr)
                if st + ln < len(buf):
                    buf[st:st + ln] += (0.10 if i2 % 2 == 0 else 0.05) * np.exp(-np.arange(ln) / (0.014 * sr))
    buf = buf / max(1e-9, float(np.abs(buf).max())) * 0.5
    fade = int(1.5 * sr)
    buf[:fade] *= np.linspace(0, 1, fade)
    buf[-fade:] *= np.linspace(1, 0, fade)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.stack([buf, buf * 0.96], axis=1) * 32767).astype(np.int16).tobytes())
    return {"ok": out.is_file(), "文件": str(out.relative_to(ROOT)), "秒": round(dur, 2),
            "结构": [s[0] for s in secs], "轨": ["贝斯(低八度)", "铺底(三和弦)", "琶音(副歌高八度)", "鼓"],
            "字节": out.stat().st_size if out.is_file() else 0}


# ── ③ 逐镜渲染（Blender 三机位 + 推镜 + 动作 + 运动模糊）────────────────
_BLENDER = r'''# 单镜渲染（由 film_studio 生成）· **带景 + 带动作 + 带运镜**
import bpy, math, os, sys
from mathutils import Euler, Vector
GLB = r"__GLB__"
OUT = r"__OUT__"
CAM_D, CAM_LENS, CAM_TGT = __D__, __LENS__, __TGT__
DUR = __DUR__
ACT = __ACT__                      # 动作编号（0..N-1）
SETMODE = "__SETMODE__"            # 景：义庄 / 道堂 / 街景
LKEY = __LKEY__; LFILL = __LFILL__; LRIM = __LRIM__; LENV = __LENV__
LCK = (__LCK__); LCR = (__LCR__); KROT = (__KROT__); RROT = (__RROT__)
SET1 = (__SET1__); SET2 = (__SET2__)   # 景的主色/霓虹色（随主题）
R = math.radians
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB)
for o in list(bpy.data.objects):
    if o.type == 'MESH' and len(o.data.vertices) < 2000:
        bpy.data.objects.remove(o, do_unlink=True)
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
body = max((o for o in bpy.data.objects if o.type == 'MESH'), key=lambda o: len(o.data.vertices))
pt0 = [body.matrix_world @ Vector(c) for c in body.bound_box]
zs0 = [q.z for q in pt0]; xs0 = [q.x for q in pt0]; ys0 = [q.y for q in pt0]
cx = (min(xs0) + max(xs0)) / 2; cy = (min(ys0) + max(ys0)) / 2
zmin = min(zs0); Hh = max(zs0) - zmin
sc = bpy.context.scene
sc.render.engine = 'BLENDER_EEVEE_NEXT'; sc.eevee.taa_render_samples = 14
try:
    sc.render.use_motion_blur = True
except Exception as e:
    from core import swallow as _sw; _sw.swallow(__file__, e)

sc.render.resolution_x = __SW__; sc.render.resolution_y = __SH__; sc.render.fps = __SFPS__
sc.view_settings.view_transform = 'Standard'

def mat(name, col, rough=0.85, emit=0.0, alpha=1.0, metal=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (col[0]/255.0, col[1]/255.0, col[2]/255.0, 1)
    try: p.inputs["Roughness"].default_value = rough
    except Exception as e:
        _swallow(__file__, e)
    try: p.inputs["Metallic"].default_value = metal
    except Exception as e:
        _swallow(__file__, e)
    if emit > 0:
        try:
            p.inputs["Emission Color"].default_value = (col[0]/255.0, col[1]/255.0, col[2]/255.0, 1)
            p.inputs["Emission Strength"].default_value = emit
        except Exception as e:
            _swallow(__file__, e)
    if alpha < 1:
        p.inputs["Alpha"].default_value = alpha
        try: m.blend_method = 'BLEND'
        except Exception as e:
            _swallow(__file__, e)
    return m

def plane(name, size, loc, rot, m):
    bpy.ops.mesh.primitive_plane_add(size=size, location=loc); o = bpy.context.object
    o.name = name; o.rotation_euler = rot; o.data.materials.append(m); return o

def box(name, dims, loc, rot, m):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object
    o.name = name; o.scale = dims; o.rotation_euler = rot; o.data.materials.append(m); return o

def cyl(name, r, h, loc, rot, m):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=h, location=loc); o = bpy.context.object
    o.name = name; o.rotation_euler = rot; o.data.materials.append(m); return o

FLOOR = mat("floor", (46, 44, 42), 0.95)
WOOD = mat("wood", (58, 40, 30), 0.9)
DARK = mat("dark", (22, 22, 26), 0.9)
PAPER = mat("paper", (232, 208, 120), 0.7, emit=0.35)
RED = mat("red", (150, 30, 30), 0.8)
GOLD = mat("gold", (196, 158, 70), 0.4, metal=0.8)
FOG = mat("fog", (206, 214, 226), 1.0, alpha=0.10)
NEON1 = mat("neon1", SET1, 0.4, emit=2.6)
NEON2 = mat("neon2", SET2, 0.4, emit=2.2)
WALL = mat("wall", (40, 42, 50), 0.95)
# 地面（三景共用）
plane("floor", Hh * 6, (cx, cy, zmin - 0.005), (0, 0, 0), FLOOR)
# 墙
plane("backwall", Hh * 6, (cx, cy + Hh * 1.35, zmin + Hh * 1.5), (R(90), 0, 0), WOOD if SETMODE != "街景" else DARK)
plane("leftwall", Hh * 6, (cx - Hh * 1.9, cy, zmin + Hh * 1.5), (R(90), 0, R(90)), WOOD if SETMODE != "街景" else DARK)
if SETMODE == "义庄":
    # 棺材：两截（棺身 + 半开棺盖）
    box("coffin", (Hh * 0.50, Hh * 1.10, Hh * 0.28), (cx + Hh * 1.30, cy + Hh * 1.00, zmin + Hh * 0.14), (0, 0, R(4)), WOOD)
    box("lid", (Hh * 0.50, Hh * 1.10, Hh * 0.05), (cx + Hh * 1.28, cy + Hh * 1.00, zmin + Hh * 0.33), (R(-14), 0, R(4)), WOOD)
    # 供桌 + 香炉
    box("altar", (Hh * 1.5, Hh * 0.5, Hh * 0.07), (cx - Hh * 0.95, cy + Hh * 0.75, zmin + Hh * 0.46), (0, 0, 0), WOOD)
    cyl("censer", Hh * 0.07, Hh * 0.1, (cx - Hh * 0.95, cy + Hh * 0.75, zmin + Hh * 0.53), (0, 0, 0), GOLD)
    # 纸符（黄符）：四面悬空
    for i, (dx, dz) in enumerate(((-0.62, 1.20), (0.30, 1.12), (-0.18, 1.34), (0.58, 1.26), (-0.95, 1.08))):
        bpy.ops.mesh.primitive_plane_add(size=1, location=(cx + Hh * dx, cy + Hh * 1.33, zmin + Hh * dz))
        p = bpy.context.object; p.name = "talisman%d" % i
        p.scale = (Hh * 0.055, Hh * 0.13, 1); p.rotation_euler = (R(90), 0, R(-8 + 4 * i))
        p.data.materials.append(PAPER)
    # 灯笼两盏（暖光）
    for i, dx in enumerate((-1.35, 1.25)):
        cyl("lantern%d" % i, Hh * 0.10, Hh * 0.18, (cx + Hh * dx, cy + Hh * 0.70, zmin + Hh * 1.42), (0, 0, 0),
            mat("lamp%d" % i, (255, 186, 110), 0.5, emit=7.0))
        bpy.ops.object.light_add(type="POINT", location=(cx + Hh * dx, cy + Hh * 0.70, zmin + Hh * 1.40))
        lp = bpy.context.object; lp.name = "lampL%d" % i
        lp.data.energy = 30.0 * Hh * Hh; lp.data.color = (1.0, 0.72, 0.42)
        try: lp.data.shadow_soft_size = Hh * 0.12
        except Exception as e:
            _swallow(__file__, e)
elif SETMODE == "道堂":
    box("altar", (Hh * 1.7, Hh * 0.6, Hh * 0.08), (cx, cy + Hh * 0.85, zmin + Hh * 0.5), (0, 0, 0), WOOD)
    box("altar2", (Hh * 1.5, Hh * 0.5, Hh * 0.06), (cx, cy + Hh * 0.85, zmin + Hh * 0.9), (0, 0, 0), WOOD)
    cyl("censer", Hh * 0.08, Hh * 0.12, (cx, cy + Hh * 0.85, zmin + Hh * 0.98), (0, 0, 0), GOLD)
    # 一排符纸 + 蜡烛
    for i in range(6):
        plane("tab%d" % i, Hh * 0.1, (cx + Hh * (-0.7 + 0.28 * i), cy + Hh * 0.62, zmin + Hh * (1.05 + 0.05 * (i % 2))), (0, 0, 0), PAPER)
        cyl("candle%d" % i, Hh * 0.02, Hh * 0.12, (cx + Hh * (-0.75 + 0.3 * i), cy + Hh * 0.8, zmin + Hh * 0.99), (0, 0, 0),
            mat("flame%d" % i, (255, 196, 120), 0.4, emit=3.0))
    # 桃木剑（斜放）
    box("sword", (Hh * 0.03, Hh * 0.5, Hh * 0.01), (cx + Hh * 0.8, cy + Hh * 0.6, zmin + Hh * 0.75), (R(24), 0, R(18)), RED)
else:  # 街景
    for i, dx in enumerate((-1.8, 1.9)):
        box("building%d" % i, (Hh * 0.9, Hh * 0.9, Hh * 3.2), (cx + Hh * dx, cy + Hh * 1.1, zmin + Hh * 1.6), (0, 0, 0), DARK)
        box("neon%d" % i, (Hh * 0.5, Hh * 0.03, Hh * 0.12), (cx + Hh * dx, cy + Hh * 0.62, zmin + Hh * (1.2 + 0.5 * i)),
            (0, 0, 0), NEON1 if i == 0 else NEON2)
    for i in range(4):   # 铁闸（竖栅）
        box("gate%d" % i, (Hh * 0.02, Hh * 0.02, Hh * 1.1), (cx + Hh * (-0.5 + 0.32 * i), cy + Hh * 0.9, zmin + Hh * 0.55),
            (0, 0, 0), GOLD)
# 雾（三景共用，多层半透明）
# 雾要放在相机与角色之间才看得见（相机在 -Y 侧）
# 🔴 实测两次：雾平面只要**横在相机与角色之间**（哪怕挪到 -0.55H）就会把画面洗白。
# 定案：雾只放**角色身后**（cy + 正数），做"背景霾"，不挡镜头。
for i, (dy, dz) in enumerate(((0.30, 0.14), (0.60, 0.05), (0.95, 0.20))):
    plane("fog%d" % i, Hh * 5.0, (cx, cy + Hh * dy, zmin + Hh * dz), (R(90), 0, 0), FOG)

w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True
w.node_tree.nodes['Background'].inputs[0].default_value = (0.05, 0.055, 0.07, 1)
w.node_tree.nodes['Background'].inputs[1].default_value = LENV
for nm, rot, e, col in (('key', KROT, LKEY, LCK), ('fill', (R(70), 0, R(45)), LFILL, (255, 255, 255)),
                        ('rim', RROT, LRIM, LCR)):
    d = bpy.data.lights.new(nm, 'SUN'); d.energy = e; d.angle = R(15)
    try:
        d.color = (col[0] / 255.0, col[1] / 255.0, col[2] / 255.0)
    except Exception as e:
        from core import swallow as _sw; _sw.swallow(__file__, e)

    o = bpy.data.objects.new(nm, d); sc.collection.objects.link(o); o.rotation_euler = rot
# ── 动作：每镜给**起始**与**结束**两个姿势（真动作，不是站桩）──
N = [b.name for b in arm.pose.bones]
def Fb(*k):
    for n in N:
        if all(x.lower() in n.lower() for x in k): return n
    return None
BN = dict(sp=Fb("Spine"), sp1=Fb("Spine1"), nk=Fb("Neck"), hd=Fb("Head"),
          lau=Fb("LeftArm"), lfa=Fb("LeftForeArm"), rau=Fb("RightArm"), rfa=Fb("RightForeArm"),
          lul=Fb("LeftUpLeg"), lll=Fb("LeftLeg"), rul=Fb("RightUpLeg"), rll=Fb("RightLeg"))
# ACTS[act] = (起始姿势, 结束姿势)；角度大、方向明确
# 🔴 实测教训：±6° 在成片里等于"站桩"。僵尸动作要 60~115° 才看得出来。
ACTS = [
 ((10, 0, 6, -95, -18, -30, -60, 14, -20, 28, 0, -12, 0, 18, -14), (-4, 0, 0, -70, -6, -12, -95, 22, -30, 6, 0, -4, 0, 6, -4)),
 ((-14, 0, -6, -110, -30, -22, -40, 12, -14, 34, 0, -18, 0, 26, -20), (-2, 0, 0, -60, -10, -10, -80, 18, -22, 10, 0, -6, 0, 10, -8)),
 ((0, 0, 34, -80, -40, -26, -85, 34, -30, 16, 0, -10, 0, 12, -10), (0, 0, -30, -55, -18, -14, -55, -24, -18, -10, 0, 6, 0, -8, 6)),
 ((20, 0, 8, -70, -16, -18, -70, 14, -18, 52, 0, -26, 0, 34, -26), (6, 0, 2, -50, -8, -10, -50, 8, -12, 22, 0, -12, 0, 14, -10)),
 ((-6, 0, -26, -100, -28, -22, -60, 16, -20, 20, 0, -10, 0, 14, -10), (-16, 0, 24, -70, -12, -12, -95, 26, -26, -8, 0, 8, 0, -6, 6)),
 ((12, 0, 4, -105, -22, -26, -70, 18, -24, 30, 0, -16, 0, 20, -16), (-10, 0, -2, -55, -14, -8, -55, -14, -8, 8, 0, -2, 0, 4, -2)),
]
a0, a1 = ACTS[ACT % len(ACTS)]
def put(bone, f, rx, ry, rz):
    if not bone: return
    pb = arm.pose.bones[bone]; pb.rotation_mode = 'XYZ'
    pb.rotation_euler = Euler((R(rx), R(ry), R(rz)), 'XYZ'); pb.keyframe_insert('rotation_euler', frame=f)
def apply(pose, f):
    put(BN['sp'], f, pose[0], 0, pose[1]); put(BN['sp1'], f, pose[2], 0, 0)
    put(BN['lau'], f, pose[3], 0, pose[4]); put(BN['lfa'], f, pose[5], 0, 0)
    put(BN['rau'], f, pose[6], 0, pose[7]); put(BN['rfa'], f, pose[8], 0, 0)
    put(BN['lul'], f, pose[9], 0, pose[10]); put(BN['rul'], f, pose[11], 0, pose[12])
    put(BN['lll'], f, pose[13], 0, 0); put(BN['rll'], f, pose[14], 0, 0)
apply(a0, 1); apply(a1, max(2, DUR))
# ── 相机 + 运镜 ──
aim = bpy.data.objects.new('aim', None); sc.collection.objects.link(aim)
aim.location = (cx, cy, zmin + Hh * CAM_TGT)
cd = bpy.data.cameras.new('c'); cd.lens = CAM_LENS
cam = bpy.data.objects.new('c', cd); sc.collection.objects.link(cam); sc.camera = cam
cam.constraints.new('TRACK_TO').target = aim
d0 = Hh * CAM_D * 2.0
moves = [(0.86, 0.0, 0.0), (1.06, 0.18, 0.0), (0.94, -0.22, 0.10), (0.90, 0.0, -0.12)]
z, px, pz = moves[ACT % len(moves)]
cam.location = (cx + px * Hh, cy - d0, zmin + Hh * 0.05 + pz * Hh); cam.keyframe_insert('location', frame=1)
cam.location = (cx + px * Hh * 0.5, cy - d0 * z, zmin + Hh * 0.05 + pz * Hh * 0.4); cam.keyframe_insert('location', frame=max(2, DUR))
sc.frame_start, sc.frame_end = 1, DUR
sc.render.image_settings.file_format = 'FFMPEG'
sc.render.ffmpeg.format = 'MPEG4'; sc.render.ffmpeg.codec = 'H264'
sc.render.ffmpeg.constant_rate_factor = 'HIGH'
sc.render.filepath = OUT
bpy.ops.render.render(animation=True)
print("镜:", OUT, os.path.exists(OUT))
'''


def render_shots(plan: dict, seconds_per_shot: float = 2.4, light: dict | None = None,
                 setmode: str = "义庄", setcol: tuple | None = None) -> dict:
    SHOTS.mkdir(parents=True, exist_ok=True)
    clips = []
    for sh in plan["shots"]:
        out = SHOTS / ("shot%d.mp4" % sh["i"])
        frames = int(seconds_per_shot * SFPS)
        pose = (sh["i"] % 3) - 1                      # -1/0/1 三档姿势差异
        from core import post_studio as PS
        rec = PS.light_recipe(sh["机位"], sh["i"])      # **补光师**：这一镜用哪套三点布光
        if light:                                   # 主题覆盖（僵尸片的冷绿月光/灯笼暖黄等）
            rec = {**rec, **light}
        src = (_BLENDER.replace("__GLB__", str(RIG).replace("\\", "/"))
               .replace("__OUT__", str(out).replace("\\", "/"))
               .replace("__D__", str(sh["距离"])).replace("__LENS__", str(sh["镜头"]))
               .replace("__TGT__", str(sh["目标"])).replace("__DUR__", str(frames))
               .replace("__POSE__", str(pose))
               .replace("__LKEY__", str(rec["主光"])).replace("__LFILL__", str(rec["辅光"]))
               .replace("__LRIM__", str(rec["轮廓"])).replace("__LENV__", str(rec["环境"]))
               .replace("__LCK__", str(tuple(rec["主光色"]))).replace("__LCR__", str(tuple(rec["轮廓色"])))
               .replace("__KROT__", str(tuple(rec["主光角"]))).replace("__RROT__", str(tuple(rec["轮廓角"])))
               .replace("__ACT__", str(sh["i"] - 1)).replace("__SETMODE__", str(setmode))
               .replace("__SET1__", str(tuple(rec["轮廓色"])))
               .replace("__SET2__", str(tuple(setcol or (255, 118, 198))))
               .replace("__SW__", str(SW)).replace("__SH__", str(SH)).replace("__SFPS__", str(SFPS)))
        py = SHOTS / ("_shot%d.py" % sh["i"])
        py.write_text(src, encoding="utf-8")
        blender = r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"
        code, log = _run([blender, "-b", "--factory-startup", "-P", str(py)], 900)
        py.unlink(missing_ok=True)
        ok = out.is_file() and out.stat().st_size > 10000
        clips.append({"i": sh["i"], "机位": sh["机位"], "文": sh["文"], "文件": str(out.relative_to(ROOT)),
                      "秒": round(_dur(out), 2) if ok else 0.0, "ok": ok})
    return {"ok": all(c["ok"] for c in clips), "镜头": clips,
            "规格": "%dx%d@%d" % (SW, SH, SFPS)}


# ── ④ 多镜头剪辑成片（句界切镜 + 交叉溶解 + 片头尾 + 字幕 + 混音）─────────────
def edit(shots: list, vo: Path, bgm: Path, ass: Path, out: Path,
         intro: Path | None = None, outro: Path | None = None,
         script: str = "", title: str = "AI 短剧") -> dict:
    """把 N 个镜头剪成一支片：交叉溶解衔接、片头尾卡、字幕只压内容、旁白优先配乐让路。"""
    from core import studio as SD
    FILM.mkdir(parents=True, exist_ok=True)
    paths = [Path(c.get("文件") if isinstance(c, dict) else c) for c in shots]
    paths = [(ROOT / p) if not p.is_absolute() else p for p in paths]
    paths = [p for p in paths if p.is_file()]
    if not paths or not Path(vo).is_file():
        return {"ok": False, "error": "缺镜头或旁白"}
    d = _dur(vo)
    nvo = SD.normalize([vo]) if False else []       # 占位（旁白是音频，不归一化）
    # ① 镜头归一化到成片规格
    sm = ROOT / "state" / "film_shots"
    norm = SD.normalize(paths, outdir=sm / "norm")
    # ② 每个镜头按句权重分配时长（不足则循环铺满）
    plan = shot_plan(script, max_shots=len(norm)) if script else {"shots": []}
    weights = [s.get("权重", 1.0 / len(norm)) for s in plan.get("shots", [])] or [1.0 / len(norm)] * len(norm)
    segs, acc = [], 0.0
    for i, p in enumerate(norm):
        want = max(1.2, d * weights[i % len(weights)])
        seg = sm / ("seg%d.mp4" % (i + 1))
        _run(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(p),
              "-t", "%.2f" % want, "-vf", "scale=%d:%d,setsar=1,fps=%d" % (W, H, FPS),
              "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-an", str(seg)], 600)
        if seg.is_file():
            segs.append((seg, _dur(seg)))
            acc += _dur(seg)
    if not segs:
        return {"ok": False, "error": "镜头切段失败"}
    # ③ 交叉溶解串起来（0.35 秒）
    body = sm / "body_subbed.mp4"
    args = ["ffmpeg", "-y", "-v", "error"]
    for s, _ in segs:
        args += ["-i", str(s)]
    if len(segs) == 1:
        fc = "[0:v]null[v]"
    else:
        parts, prev, off = [], "[0:v]", 0.0
        for i in range(1, len(segs)):
            off += segs[i - 1][1] - 0.35
            label = "[v%d]" % i
            parts.append("%s[%d:v]xfade=transition=fade:duration=0.35:offset=%.2f%s" % (prev, i, max(0.0, off), label))
            prev = label
        fc = ";".join(parts).replace("[v%d]" % (len(segs) - 1), "[v]") if len(segs) > 1 else "[0:v]null[v]"
    sub = ("subtitles='%s'" % str(ass).replace("\\", "/").replace(":", "\\:")) if Path(ass).is_file() else "null"
    fc = fc + ";[v]%s[vsub]" % sub
    args += ["-filter_complex", fc, "-map", "[vsub]", "-c:v", "libx264", "-crf", "18",
             "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", str(body)]
    code, log = _run(args, 900)
    if not body.is_file():
        return {"ok": False, "error": "镜头串接失败", "log": log.decode("utf-8", "replace")[-200:]}
    # ④ 片头尾 + 混音（旁白 1.3，配乐 0.13 且闪避）→ 成片
    tail = ["file '" + str(body).replace("\\", "/") + "'"]
    lead = 0.0
    if intro and Path(intro).is_file():
        ni = SD.normalize([intro], outdir=sm / "norm2")
        if ni:
            tail = ["file '" + str(ni[0]).replace("\\", "/") + "'"] + tail
            lead = _dur(ni[0])
    if outro and Path(outro).is_file():
        no = SD.normalize([outro], outdir=sm / "norm3")
        if no:
            tail.append("file '" + str(no[0]).replace("\\", "/") + "'")
    seq = sm / "concat_film.txt"
    seq.write_text("\n".join(tail), encoding="utf-8")
    dvo = _dur(vo)
    fc2 = ("[0:v]setsar=1,fps=%d,setpts=N/FRAME_RATE/TB[v];"
           "[1:a]volume=1.3,adelay=%d|%d[a1];"
           "[2:a]volume=0.13,afade=t=in:st=0:d=0.8,afade=t=out:st=%.2f:d=1.6[a2];"
           "[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]"
           % (FPS, int(lead * 1000), int(lead * 1000), max(0.0, lead + dvo - 1.6)))
    code2, log2 = _run(["ffmpeg", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0",
                        "-i", str(seq), "-i", str(vo), "-i", str(bgm), "-filter_complex", fc2,
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
                        "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                        "-t", "%.2f" % (lead + dvo + (2.0 if outro else 0.4)), str(out)], 1200)
    ok = Path(out).is_file() and Path(out).stat().st_size > 10000
    return {"ok": ok, "文件": str(Path(out).relative_to(ROOT)),
            "字节": Path(out).stat().st_size if ok else 0, "秒": round(_dur(out), 2) if ok else 0,
            "镜数": len(segs), "分辨率": "%dx%d@%d" % (W, H, FPS),
            "接法": "xfade 0.35s", "退出码": code2}


# ── 挂到她的工作流引擎上 ────────────────────────────────────────────────
def _mk_skills():
    """把四步做成 NativeSkill（.name/.version/.probe/.run），引擎就能直接调。"""
    from skills.native import Availability, SkillResult, SkillContext  # noqa: F401

    class _Base:
        version = "film-1.0"

        def probe(self):
            return Availability(RIG.is_file(), "" if RIG.is_file() else "缺绑骨模型")

    class FilmScript(_Base):
        name = "film_script"

        def run(self, ctx, req):
            plan = shot_plan(str(req.get("script") or ""))
            return SkillResult(bool(plan.get("ok")), output=plan, error=plan.get("error", ""))

    class FilmVoice(_Base):
        name = "film_voice"

        def run(self, ctx, req):
            from core import studio as SD
            script = str(req.get("script") or "")
            vo = SD.narrate(script)
            cu = SD.cues(script, vo.get("时长") or 10.0) if vo.get("ok") else {}
            out = {"文件": vo.get("文件"), "字节": vo.get("字节"), "秒": vo.get("时长"),
                   "ass": cu.get("ass"), "条数": cu.get("条数")}
            return SkillResult(bool(vo.get("ok")), output=out, error="" if vo.get("ok") else "旁白失败")

    class FilmScore(_Base):
        name = "film_score"

        def run(self, ctx, req):
            # pipe 过来的是 n2 的整包（含 秒/文件/ass），所以要容忍两种形状
            dur = req.get("duration") or req.get("秒") or 30.0
            try:
                dur = float(dur)
            except Exception:  # noqa: BLE001
                dur = 30.0
            sc = score(str(req.get("script") or ""), dur, mode=str(req.get("音乐模式") or "默认"))
            return SkillResult(bool(sc.get("ok")), output=sc, error="" if sc.get("ok") else "配乐失败")

    class FilmShots(_Base):
        name = "film_shots"

        def run(self, ctx, req):
            # pipe 过来的是 n1 的分镜包 {"shots":[...]}；也容忍显式 plan / 重新算
            plan = req if req.get("shots") else (req.get("plan") or shot_plan(str(req.get("script") or "")))
            if not plan.get("shots"):
                return SkillResult(False, error="分镜为空（剧本太短？）")
            # 主题灯光覆盖（港式僵尸等主题各有自己的主光/轮廓色温）
            rs = render_shots(plan, float(req.get("per_shot") or req.get("每镜秒") or 2.4),
                              light=req.get("光配方"), setmode=str(req.get("景") or "义庄"),
                              setcol=tuple(req.get("霓虹色") or (255, 118, 198)))
            return SkillResult(bool(rs.get("ok")), output={"镜头": rs["镜头"], "规格": rs["规格"]},
                               error="" if rs.get("ok") else "逐镜渲染失败")

    class FilmEdit(_Base):
        name = "film_edit"

        def run(self, ctx, req):
            from core import studio as SD
            shots = req.get("镜头") or req.get("shots") or []
            vm = req.get("vo_meta") or {}
            bm = req.get("bgm_meta") or {}
            vo = req.get("vo") or vm.get("文件")
            ass = req.get("ass") or vm.get("ass") or ""
            bgm = req.get("bgm") or bm.get("文件")
            title = str(req.get("title") or "AI 短剧")
            intro = SD.card("片头", title[:14], "多机位 · 本地制作", seconds=2.4)
            outro = SD.card("片尾", "谢谢观看", "GBT 小土豆 V9", seconds=1.8)
            r = edit(shots, ROOT / str(vo), ROOT / str(bgm), ROOT / str(ass or ""),
                     ROOT / "render" / (str(req.get("out") or "多镜头成片.mp4")),
                     intro=ROOT / intro["文件"], outro=ROOT / outro["文件"],
                     script=str(req.get("script") or ""), title=title)
            return SkillResult(bool(r.get("ok")), output=r, error="" if r.get("ok") else r.get("error", "剪辑失败"))

    class PostPolish(_Base):
        name = "post_polish"

        def run(self, ctx, req):
            from core import post_studio as PS
            film = req.get("文件") or req.get("film")
            if not film:
                return SkillResult(False, error="没有可修饰的成片")
            r = PS.polish(ROOT / str(film), style=str(req.get("调色风格") or req.get("style") or "电影感"))
            return SkillResult(bool(r.get("ok")), output=r, error="" if r.get("ok") else str(r.get("卡在")))

    return [FilmScript(), FilmVoice(), FilmScore(), FilmShots(), FilmEdit(), PostPolish()]


def build_flow(script: str, title: str = "AI 短剧", out: str = "多镜头成片.mp4",
               theme: dict | None = None) -> dict:
    """按她的 SOP（选题→分镜→生产→配音→剪辑）组一张 DAG，节点全是真的生产者。"""
    return {
        "id": "film_studio_pipeline",
        "nodes": [
            {"id": "n1", "type": "skill", "skill": "film_script", "inputs": {"script": script}},
            {"id": "n2", "type": "skill", "skill": "film_voice", "inputs": {"script": script}},
            # 引擎只认 $ref:<节点>.output / .ok 与 pipe（没有字段级引用）——按它的口径接
            {"id": "n3", "type": "skill", "skill": "film_score",
             "inputs": {"script": script, "音乐模式": (theme or {}).get("音乐")}, "pipe": "n2"},
            {"id": "n4", "type": "skill", "skill": "film_shots",
             "inputs": {"光配方": (theme or {}).get("灯"), "每镜秒": (theme or {}).get("每镜秒"),
                        "景": (theme or {}).get("景"), "霓虹色": (theme or {}).get("霓虹色")}, "pipe": "n1"},
            {"id": "n5", "type": "skill", "skill": "film_edit", "pipe": "n4",
             "inputs": {"script": script, "title": title, "out": out,
                        "vo_meta": "$ref:n2.output", "bgm_meta": "$ref:n3.output"}},
            {"id": "n6", "type": "skill", "skill": "post_polish",
             "inputs": {"调色风格": (theme or {}).get("调色")}, "pipe": "n5"},
        ],
        "edges": [["n1", "n4"], ["n2", "n3"], ["n2", "n5"], ["n3", "n5"], ["n4", "n5"], ["n5", "n6"]],
    }


def run_pipeline(script: str, title: str = "AI 短剧", out: str = "多镜头成片.mp4",
                 *, theme: dict | None = None, verbose: bool = True) -> dict:
    """**直接调用她的工作流引擎**把这条路跑通（注册技能 → 组 DAG → 引擎执行）。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from skills.native import SkillRegistry
    from workflows.engine import WorkflowEngine
    reg = SkillRegistry()
    for s in _mk_skills():
        reg.register(s)
    eng = WorkflowEngine(reg, gate=lambda n: True)      # 第一个参数是位置参数 registry（我第一版写成 reg=，TypeError）
    t0 = time.time()
    res = eng.run(build_flow(script, title, out, theme))
    if verbose:
        for st in res.get("steps", []):
            print("   节点 %-4s ok=%-5s %sms %s" % (st["node"], st.get("ok"), st.get("ms"), st.get("error", "")[:60]))
    # ★细节化习惯：把每个节点按"六件套 + 回读"落台账（缺件/模糊词会被记下来）
    try:
        from core import detail_habit as DH
        names = {"n1": "分镜", "n2": "旁白与字幕", "n3": "分段配乐", "n4": "逐镜渲染",
                 "n5": "多镜头剪辑", "n6": "后期（补光/调色/混音/质检）"}
        skills = {n["id"]: n["skill"] for n in build_flow(script, title, out, theme)["nodes"]}
        for st in res.get("steps", []):
            o = (res.get("outputs") or {}).get(st["node"]) or {}
            files = []
            if isinstance(o, dict):
                for k in ("文件", "ass"):
                    if o.get(k):
                        files.append(str(o[k]))
                for c in (o.get("镜头") or [])[:3]:
                    if isinstance(c, dict) and c.get("文件"):
                        files.append(c["文件"])
            DH.record({
                "步骤": names.get(st["node"], st["node"]),
                "目标": "完成 %s（%s）" % (names.get(st["node"], st["node"]), skills.get(st["node"], "")),
                "输入": script[:60],
                "动作": "WorkflowEngine 调技能 %s" % skills.get(st["node"], "?"),
                "产出": files or ["(该节点无文件产出，读数见证据)"],
                "判据": "节点 ok=True；产物存在（有文件时）；读数回落",
                "证据": "trace=%s · 节点 %s · %sms" % (res.get("trace"), st["node"], st.get("ms")),
                "命令": "python tools/run_film_pipeline.py",
                "退出码": "0" if st.get("ok") else "1",
                "读数": "%s ms · %s" % (st.get("ms"), (st.get("error") or "无错")),
            })
    except Exception as exc:  # noqa: BLE001
        print("   细节化台账写入失败:", type(exc).__name__, exc)

    # 最后一环是**后期**（n6）：它才是交给观众的成片；**但报账形状必须统一**
    # （第一版直接把 n6 的输出当代成片，结果丢了 文件/时长/镜数 三个字段 ⇒ 验收器红。真踩过）
    outs = res.get("outputs") or {}
    n5o = outs.get("n5") or {}
    n6o = outs.get("n6") or {}
    film, film_stage = {}, "n5 剪辑（后期未成）"
    if n6o.get("成片"):
        p = ROOT / str(n6o["成片"])
        film = {"ok": p.is_file(), "文件": str(n6o["成片"]), "字节": p.stat().st_size if p.is_file() else 0,
                "秒": round(_dur(p), 2) if p.is_file() else 0.0, "分辨率": "%dx%d@%d" % (W, H, FPS),
                "镜数": n5o.get("镜数"), "阶段": "n6 后期（补光/调色/混音/质检）",
                "质检": (n6o.get("质检") or {}).get("问题") or [],
                "响度LUFS": (n6o.get("质检") or {}).get("响度LUFS"),
                "亮度均值": (n6o.get("质检") or {}).get("亮度均值")}
        film_stage = "n6 后期"
    elif n5o:
        film = n5o
    elif n6o:
        film = n6o
    return {"ok": bool(res.get("ok")), "trace": res.get("trace_id"), "步骤": res.get("steps"),
            "成片": film, "成片阶段": film_stage, "主题": (theme or {}).get("名"),
            "秒": round(time.time() - t0, 1)}

__all__ = ["CAM", "split_sentences", "shot_plan", "score", "render_shots", "edit",
           "build_flow", "run_pipeline", "W", "H", "FPS", "ROOT", "FILM"]
