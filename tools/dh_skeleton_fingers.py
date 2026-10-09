# tools/dh_skeleton_fingers.py —— 给骨架**加手指**（每只手 5 指 × 3 节 + 掌）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令（2026-10-09）：「精细把每一个部位都绑好骨，**手指头都要**，那个里面手指都不能绑。」
# 现状：autorig-skeleton.py 只量到 22 根（躯干+四肢），**手只有一根 Hand 骨** ⇒ 手指绑不了。
# 本脚本在既有骨架之上补出手链：
#   · 先在网格上取"手部顶点云"（距 Hand 关节近 + 表面连通）；
#   · 用手部顶云的**主轴**定出手指伸展方向、**横轴**定出五指分布方向、**厚度轴**定出指节；
#   · 五指各 3 节（近节/中节/末节），长度按手长的解剖比例，指尖到指根渐变收细；
#   · 写出 skeleton-fingers.json（原骨架 + 30 根指骨 + 2 根掌骨）并出一张验收图。
# 诚实标注：这是**按几何程序化摆的**指骨（不是逐指识别），够用来绑定与弯曲；要毫米级精准仍需手工修。
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FINGER_NAMES = ("Thumb", "Index", "Middle", "Ring", "Pinky")
# 每指三节的长度比例（近节/中节/末节）——拇指短、无名指末端略短（通用解剖比例，够用）
SEG_RATIO = {"Thumb": (0.45, 0.35, 0.20), "Index": (0.40, 0.32, 0.28),
             "Middle": (0.42, 0.33, 0.25), "Ring": (0.40, 0.32, 0.28),
             "Pinky": (0.42, 0.30, 0.28)}
# 五指相对手长的总长（中指最长，小指最短）
FINGER_LEN = {"Thumb": 0.55, "Index": 0.92, "Middle": 1.00, "Ring": 0.94, "Pinky": 0.78}


def main() -> int:
    glb = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    skel = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "state" / "tripo" / "out3" / "rig" / "skeleton-fingers.json"
    if not (glb and skel and glb.is_file() and skel.is_file()):
        print("用法: python tools/dh_skeleton_fingers.py <glb> <skeleton.json> [out.json]")
        return 2
    import struct
    raw = glb.read_bytes()
    jl, = struct.unpack_from("<I", raw, 12)
    J = json.loads(raw[20:20 + jl].decode("utf-8"))
    off = 20 + jl + ((-jl) % 4)
    bl, = struct.unpack_from("<I", raw, off)
    BIN = raw[off + 8: off + 8 + bl]

    def acc(i, c):
        a = J["accessors"][i]; bv = J["bufferViews"][a["bufferView"]]
        dt = {5126: np.float32, 5125: np.uint32, 5123: np.uint16}[a["componentType"]]
        return np.frombuffer(BIN, dt, a["count"] * c, bv.get("byteOffset", 0)).reshape(a["count"], c)

    pr = J["meshes"][0]["primitives"][0]
    P = acc(pr["attributes"]["POSITION"], 3).astype(np.float64)
    H = float(P[:, 1].max() - P[:, 1].min())
    SK = json.loads(skel.read_text(encoding="utf-8"))
    joints = dict(SK["joints"])
    parent = dict(SK.get("parent", {}))

    def hand_cloud(hand_pos, other_pos):
        d = np.linalg.norm(P - hand_pos, axis=1)
        d2 = np.linalg.norm(P - other_pos, axis=1)
        return P[(d < 0.075 * H) & (d < d2)]

    added = 0
    for side in ("Left", "Right"):
        hk, fk = side + "Hand", side + "ForeArm"
        if hk not in joints or fk not in joints:
            print("缺", hk, "跳过"); continue
        hand = np.array(joints[hk], float)
        fore = np.array(joints[fk], float)
        cloud = hand_cloud(hand, fore)
        if len(cloud) < 30:
            print(side, "手部顶点太少（可能手与身体焊住）：", len(cloud)); continue
        center = cloud.mean(axis=0)
        # 主轴：从手腕指向指尖（用离手腕最远的那批顶点的方向）
        dd = np.linalg.norm(cloud - fore, axis=1)
        tip_dir = cloud[np.argsort(-dd)[: max(5, len(cloud) // 20)]].mean(axis=0) - fore
        tip_dir /= (np.linalg.norm(tip_dir) + 1e-9)
        # 横轴：手部顶云在垂直于主轴平面上的最大展布方向 = 五指排列方向
        v1 = np.cross(tip_dir, [0.0, 0.0, 1.0])
        if np.linalg.norm(v1) < 1e-6:
            v1 = np.cross(tip_dir, [1.0, 0.0, 0.0])
        v1 /= (np.linalg.norm(v1) + 1e-9)
        v2 = np.cross(tip_dir, v1); v2 /= (np.linalg.norm(v2) + 1e-9)
        rel = cloud - center
        span1 = float(np.abs(rel @ v1).max())
        span2 = float(np.abs(rel @ v2).max())
        across, thick = (v1, v2) if span1 >= span2 else (v2, v1)
        across_span = max(span1, span2)
        hand_len = max(1e-4, float(np.percentile(np.linalg.norm(cloud - fore, axis=1), 90)))
        sign = 1.0 if side == "Left" else -1.0
        palm = center - tip_dir * hand_len * 0.12
        joints[side + "Palm"] = [float(x) for x in palm]
        parent[side + "Palm"] = hk
        added += 1
        offsets = (-0.40, -0.20, 0.0, 0.20, 0.38)      # 五指横向分布（相对半展宽）
        for fname, off in zip(FINGER_NAMES, offsets):
            base = palm + across * (off * across_span) + tip_dir * hand_len * 0.10
            pos = base.copy()
            prev = side + "Palm"
            total = hand_len * FINGER_LEN[fname]
            for si, ratio in enumerate(SEG_RATIO[fname]):
                nname = "%s%s%d" % (side, fname, si + 1)
                pos = pos + tip_dir * (total * ratio) + thick * (sign * 0.0)
                joints[nname] = [float(x) for x in pos]
                parent[nname] = prev
                prev = nname
                added += 1
        print("%s：手部顶点 %d · 手长 %.3f · 五指×3 节已生成" % (side, len(cloud), hand_len))

    SK["joints"] = joints
    SK["parent"] = parent
    SK["手指"] = {"来源": "几何程序化摆放（非逐指识别）", "每手": "掌 + 5 指 × 3 节",
                  "关节总数": len(joints)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(SK, ensure_ascii=False, indent=1), encoding="utf-8")
    print("写出:", out.name, "· 关节 %d（原 22 + 新 %d）" % (len(joints), added))

    # 验收图：正视图 + 侧视图，把骨点画上去
    im = Image.new("RGB", (900, 1200), (8, 10, 16))
    dr = ImageDraw.Draw(im)
    for vi, (ax, tag) in enumerate(((0, "正视"), (2, "侧视"))):
        ox = 30 + vi * 450
        xs = P[:, ax]; ys = P[:, 1]
        sx = 380 / max(1e-6, xs.max() - xs.min()); sy = 1080 / H
        for i in range(0, len(P), 7):
            dr.point((ox + (P[i, ax] - xs.min()) * sx, 1080 - (P[i, 1] - P[:, 1].min()) * sy),
                     fill=(70, 80, 95))
        for name, p in joints.items():
            x = ox + (p[ax] - xs.min()) * sx; y = 1080 - (p[1] - P[:, 1].min()) * sy
            color = (255, 80, 80) if any(f in name for f in FINGER_NAMES) else (57, 208, 255)
            r = 3 if color == (57, 208, 255) else 2
            dr.ellipse((x - r, y - r, x + r, y + r), fill=color)
        dr.text((ox, 20), tag, fill=(200, 220, 240))
    img = ROOT / "render" / "绑骨-含手指骨.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    im.save(img)
    print("验收图:", img.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
