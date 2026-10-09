# -*- coding: utf-8 -*-
"""Phase 3 · 动画烘焙器：在**我们自己的 22 骨骨架**上写关键帧 → 烘成 glTF 动画（本地、零积分）

坐标口径（由数据实测得出，不是猜）：
  模型高度 = Y · 臂轴 = Z（双臂沿 Z 展开）· 正面 = +X
  ⇒ 腿/躯干前摆 = 绕 Z 正角 · 手臂前后摆 = 绕 Y · 扭转/头部左右 = 绕 Y · 侧倾 = 绕 X
每条 clip 都要**首末帧闭合**（循环无跳变），并输出读数：时长 / 通道数 / 闭合误差 / 根位移。

用法: python tools/codex-scripts/anim-make.py
"""
import json
import math
import pathlib
import struct
import numpy as np

import os as _os
WS = pathlib.Path(_os.environ.get("GBT_DH_WS") or r"C:\Users\ADMIN\Desktop\GBT小土豆V9")   # 参数化：env 优先，默认本仓
DH = WS / "holo_pet/assets/digital-human"
SRC = DH / "raw/rig/model-rigged.glb"                 # 低模绑骨件（动画挂它：小、快、随时可换高模）
OUT = DH / "raw/rig/model-rigged-anim.glb"

raw = SRC.read_bytes()
jl, = struct.unpack_from("<I", raw, 12)
J = json.loads(raw[20:20 + jl].decode("utf-8"))
off = 20 + jl + ((-jl) % 4)
bl, = struct.unpack_from("<I", raw, off)
BIN = bytearray(raw[off + 8: off + 8 + bl])
del raw

bone_node = {nd["name"].replace("mixamorig:", ""): i for i, nd in enumerate(J["nodes"]) if (nd.get("name") or "").startswith("mixamorig:")}
print(f"骨架节点 {len(bone_node)} 个")


def quat(rx, ry, rz):
    """XYZ 顺序的欧拉角（度）→ 四元数 (x,y,z,w)。"""
    hx, hy, hz = map(math.radians, (rx / 2, ry / 2, rz / 2))
    cx, sx = math.cos(hx), math.sin(hx)
    cy, sy = math.cos(hy), math.sin(hy)
    cz, sz = math.cos(hz), math.sin(hz)
    return np.array([
        sx * cy * cz + cx * sy * sz,
        cx * sy * cz - sx * cy * sz,
        cx * cy * sz + sx * sy * cz,
        cx * cy * cz - sx * sy * sz,
    ], np.float32)


# ── 基准姿势基底：T-pose 绑定下，手臂必须放下才自然（绕 X 轴放下，左右镜像）──
BASE = {
    "LeftShoulder": (0, 0, -4), "RightShoulder": (0, 0, 4),
    "LeftArm": (82, 0, 0), "RightArm": (-82, 0, 0),          # 上臂下垂 ≈82°（更贴身自然）
    "LeftForeArm": (8, -12, 0), "RightForeArm": (-8, -12, 0),  # 肘部微屈 + 略内收
    "LeftHand": (4, 0, 0), "RightHand": (-4, 0, 0),
    "Spine": (2, 0, 0), "Spine1": (1, 0, 0), "Neck": (-2, 0, 0),
    "LeftLeg": (0, 0, -3), "RightLeg": (0, 0, -3),             # 膝盖微屈（不锁死）
}


def add_pose(pose):
    """把动作增量叠到基准姿势上（角度相加，逐轴）。"""
    out = {}
    keys = set(BASE) | {k for k in pose if not k.endswith("X") and not k.endswith("Y") and not k.startswith("_")}
    for k in keys:
        b = BASE.get(k, (0, 0, 0))
        d = pose.get(k, (0, 0, 0)) if not k.endswith("X") and not k.endswith("Y") else (0, 0, 0)
        out[k] = (b[0] + d[0], b[1] + d[1], b[2] + d[2])
    for k, v in pose.items():          # HipsX/HipsY/_stride 等标量原样带回
        if k.endswith("X") or k.endswith("Y") or k.startswith("_"):
            out[k] = v
    return out


def ease(t, p):
    """平滑插值曲线（呼吸/重心用 cos 更自然；四关键帧走路用 smoothstep）。"""
    if p == "smooth":
        return t * t * (3 - 2 * t)
    if p == "cos":
        return 0.5 - 0.5 * math.cos(math.pi * t)
    return t


# ── ① 走路：标准四关键帧 + 镜像（1.10s / 一个完整步幅 = 左右各一步）────────
def walk_pose(phase):
    """phase: 0=左脚触地 0.25=左支撑过顶 0.5=右脚触地 0.75=右支撑过顶"""
    fwd, _ = 24.0, -20.0
    if phase in (0.0, 0.5):
        s = 1 if phase == 0.0 else -1
        return {
            "Hips": (0, 9 * s, -4 * s), "HipsY": -0.028, "HipsX": None,
            "Spine": (0, -4 * s, 2.5), "Spine1": (0, -3 * s, 1.5), "Neck": (0, 2 * s, 0),
            "Head": (0, 2 * s, -1),
            "LeftUpLeg": (0, 0, fwd * s), "LeftLeg": (0, 0, -6 - 6 * (1 - s) / 2),
            "RightUpLeg": (0, 0, -fwd * s), "RightLeg": (0, 0, -30 - 8 * (1 + s) / 2),
            "LeftFoot": (0, 0, -8 * s), "RightFoot": (0, 0, 6 * s),
            "LeftArm": (0, -20 * s, 0), "RightArm": (0, 20 * s, 0),
            "LeftForeArm": (0, -14, 0), "RightForeArm": (0, -14, 0),
        }
    s = 1 if phase == 0.25 else -1
    return {
        "Hips": (0, 4 * s, -2 * s), "HipsY": 0.018, "HipsX": None,
        "Spine": (0, -1 * s, 1.0), "Spine1": (0, -1 * s, 0.5), "Neck": (0, 0.5 * s, 0), "Head": (0, 1 * s, 0),
        "LeftUpLeg": (0, 0, 3 * s), "LeftLeg": (0, 0, -46 if s < 0 else -10),
        "RightUpLeg": (0, 0, -3 * s), "RightLeg": (0, 0, -10 if s < 0 else -46),
        "LeftFoot": (0, 0, 4), "RightFoot": (0, 0, 4),
        "LeftArm": (0, -9 * s, 0), "RightArm": (0, 9 * s, 0),
        "LeftForeArm": (0, -12, 0), "RightForeArm": (0, -12, 0),
    }


# ── ② idle：三层叠加（呼吸 4s · 重心漂移 8s · 头部扫视）─────────────────
IDLE_LEN, IDLE_FPS = 8.0, 12


def idle_at(t):
    br = math.sin(2 * math.pi * t / 4.0)                 # 呼吸
    ws = math.sin(2 * math.pi * t / 8.0)                 # 重心漂移
    sacc = [1.5, 3.8, 6.2]                               # 扫视时刻
    hy = sum(3.0 * math.exp(-((t - s) ** 2) / 0.06) for s in sacc)
    hn = 1.2 * math.sin(2 * math.pi * t / 4.0)   # 周期必须整除 8s，否则循环不闭合
    return {
        "Hips": (1.2 * ws, 1.0 * ws, -0.8 * ws), "HipsY": -0.004 * abs(ws), "HipsX": 0.010 * ws,
        "Spine": (0, -0.6 * ws, 0.5 * ws), "Spine1": (-0.9 * br, 0, 0.6 * br),
        "Neck": (0, -0.5 * ws, 0), "Head": (0, hy, hn),
        "LeftShoulder": (0, 0, -0.6 * br), "RightShoulder": (0, 0, -0.6 * br),
        "LeftArm": (0, -1.0 * ws, -1.2 * br), "RightArm": (0, -1.0 * ws, -1.2 * br),
        "LeftForeArm": (0, -1.5, 0.8 * br), "RightForeArm": (0, -1.5, 0.8 * br),
        "LeftUpLeg": (0, 0, 0.5 * ws), "RightUpLeg": (0, 0, -0.5 * ws),
        "LeftLeg": (0, 0, -1.0), "RightLeg": (0, 0, -1.0),
        "LeftFoot": (0, 0, 0.5), "RightFoot": (0, 0, 0.5),
    }


def walk_at(t, T=1.10):
    p = (t % T) / T
    keys = [(0.0, walk_pose(0.0)), (0.25, walk_pose(0.25)), (0.5, walk_pose(0.5)), (0.75, walk_pose(0.75)), (1.0, walk_pose(0.0))]
    for i in range(len(keys) - 1):
        a, pa = keys[i]
        b, pb = keys[i + 1]
        if a <= p <= b:
            u = ease((p - a) / (b - a), "smooth")
            out = {}
            for k in pa:
                if k.endswith("X") or k.endswith("Y"):
                    out[k] = (pa[k] or 0) * (1 - u) + (pb[k] or 0) * u
                else:
                    out[k] = tuple(pa[k][c] * (1 - u) + pb[k][c] * u for c in range(3))
            out["_stride"] = (t / T) * 0.55                 # 根位移：每步幅 0.55（模型高=1.0 时的自然步长）
            return out
    return keys[0][1]


# ── ③ 采样 + 建通道 ─────────────────────────────────────────────────────
def build_clip(name, length, fps, fn, root_motion):
    times = np.arange(0, length + 1e-6, 1.0 / fps, np.float32)
    if times[-1] < length:
        times = np.append(times, np.float32(length))
    per_bone = {}
    hips_pos = []
    for t in times:
        pose = add_pose(fn(float(t)))
        for b in bone_node:
            per_bone.setdefault(b, []).append(quat(*pose.get(b, (0, 0, 0))))
        hips_pos.append([pose.get("HipsX", 0.0) or 0.0, pose.get("HipsY", 0.0) or 0.0, pose.get("_stride", 0.0) if root_motion else 0.0])
    samplers, channels = [], []

    def add(inp, out, node, path):
        samplers.append({"input": inp, "output": out, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
    while len(BIN) % 4:
        BIN.append(0)
    ti = len(BIN)
    BIN.extend(times.tobytes())
    J["bufferViews"].append({"buffer": 0, "byteOffset": ti, "byteLength": times.nbytes})
    J["accessors"].append({"bufferView": len(J["bufferViews"]) - 1, "componentType": 5126, "count": len(times), "type": "SCALAR",
                           "min": [float(times.min())], "max": [float(times.max())]})
    tin = len(J["accessors"]) - 1
    for b, quats in per_bone.items():
        arr = np.array(quats, np.float32)
        while len(BIN) % 4:
            BIN.append(0)
        o = len(BIN)
        BIN.extend(arr.tobytes())
        J["bufferViews"].append({"buffer": 0, "byteOffset": o, "byteLength": arr.nbytes})
        J["accessors"].append({"bufferView": len(J["bufferViews"]) - 1, "componentType": 5126, "count": len(arr), "type": "VEC4"})
        add(tin, len(J["accessors"]) - 1, bone_node[b], "rotation")
    if root_motion:
        arr = np.array(hips_pos, np.float32)
        while len(BIN) % 4:
            BIN.append(0)
        o = len(BIN)
        BIN.extend(arr.tobytes())
        J["bufferViews"].append({"buffer": 0, "byteOffset": o, "byteLength": arr.nbytes})
        J["accessors"].append({"bufferView": len(J["bufferViews"]) - 1, "componentType": 5126, "count": len(arr), "type": "VEC3"})
        add(tin, len(J["accessors"]) - 1, bone_node["Hips"], "translation")
    # 闭合误差：首末关键帧四元数最大差（旋转须闭合，位移按 stride 故意不闭合）
    closes = [float(np.abs(np.array(v[0]) - np.array(v[-1])).max()) for v in per_bone.values()]
    return {"name": name, "channels": channels, "samplers": samplers}, float(max(closes)), float(times[-1]), float(hips_pos[-1][2]) if root_motion else 0.0


clips = []
c1, close1, len1, stride = build_clip("idle", IDLE_LEN, IDLE_FPS, idle_at, root_motion=False)
clips.append(c1)
c2, close2, len2, stride2 = build_clip("walk", 1.10, 24, lambda t: walk_at(t), root_motion=True)
clips.append(c2)
J["animations"] = clips
J["buffers"][0]["byteLength"] = len(BIN)


def write_glb(path, jj, bb):
    body = bytearray(bb)
    while len(body) % 4:
        body.append(0)
    jj["buffers"][0]["byteLength"] = len(body)
    js = json.dumps(jj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    while len(js) % 4:
        js += b" "
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js) + 8 + len(body))
                     + struct.pack("<I", len(js)) + b"JSON" + js
                     + struct.pack("<I", len(body)) + b"BIN\x00" + bytes(body))


write_glb(OUT, J, BIN)
print(f"idle: {len1:.1f}s · 通道 {len(c1['channels'])} · 循环闭合误差 {close1:.5f}（应≈0）")
print(f"walk: {len2:.2f}s · 通道 {len(c2['channels'])} · 循环闭合误差 {close2:.5f} · 根位移/步幅 {stride2:.3f}（模型高=1.0）")
print(f"输出 {OUT.name} · {OUT.stat().st_size/1024/1024:.2f} MB · 动画 {len(J['animations'])} 段")
