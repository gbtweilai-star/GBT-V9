# tools/verify_avatar.py —— 数字人形象验收（机检：资产/骨骼/权重/三支动画/棚拍图/口型轨/语音绑定）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 数字人形象验收 ==")
# ① 形象资产（优先绑骨后的 GLB）
glbs = [p for p in ROOT.rglob("*.glb") if "desktop" not in str(p) and "_archive" not in str(p)]
rigged = [p for p in glbs if "rig" in p.name.lower()] or glbs
check("① 形象资产在（GLB 存在）", bool(glbs), "%d 个 GLB · 首选 %s" % (len(glbs), rigged[0].name if rigged else "无"))

# ② 骨骼数 + ③ 权重覆盖（复用 bone_binding 的 stdlib GLB 解析）
bones, weights = 0, None
try:
    from core import bone_binding as BB
    if rigged:
        sc = BB.scan(str(rigged[0]))
        bones = int(sc.get("骨") or sc.get("关节") or 0)
        weights = sc.get("权重覆盖") or sc.get("蒙皮") or None
except Exception as e:  # noqa: BLE001
    print("   （骨骼解析异常：%s）" % type(e).__name__)
check("② 骨骼已绑（≥15 根）", bones >= 15, "%d 根骨" % bones)
check("③ 蒙皮权重有读数", weights is not None, weights if weights is not None else "解析里没有权重字段（待补）")

# ④ 三支常驻动画
anim = {}
for w in ("idle", "talk", "walk"):
    hits = [p for p in ROOT.rglob(w + ".mp4") if "desktop" not in str(p)]
    anim[w] = hits[0] if hits else None
missing = [k for k, v in anim.items() if not v]
check("④ idle/talk/walk 三支动画齐", not missing,
      " · ".join("%s=%s" % (k, (v.name + " " + str(round(v.stat().st_size / 1024)) + "KB") if v else "缺") for k, v in anim.items()))

# ⑤ 棚拍验收图
stills = [p for p in ROOT.rglob("*.png") if ("render" in str(p) or "shots" in str(p)) and "desktop" not in str(p)][:6]
check("⑤ 棚拍/验收图（≥3 张）", len(stills) >= 3, "%d 张（例：%s）" % (len(stills), stills[0].name if stills else "无"))

# ⑥ 口型轨（viseme/口型）
lip = [p for p in ROOT.rglob("*") if ("viseme" in p.name.lower() or "口型" in p.name or "lipsync" in p.name.lower())][:3]
check("⑥ 口型轨存在（viseme/lipsync）", bool(lip), lip[0].name if lip else "缺（要补：口型轨或音素对齐表）")

# ⑦ 语音绑定：默认音色必须是台湾腔女声
try:
    va = json.loads((ROOT / "state" / "voice_assets.json").read_text(encoding="utf-8"))
    d = va.get("默认", {})
    ok7 = d.get("性别") == "女" and "台湾" in str(d.get("口音"))
    check("⑦ 默认语音＝女声·台湾腔", ok7, json.dumps(d, ensure_ascii=False))
except Exception as e:  # noqa: BLE001
    check("⑦ 默认语音＝女声·台湾腔", False, "读不到 voice_assets.json（%s）" % type(e).__name__)

print()
if FAIL:
    print("结论：❌ 形象未达标 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 数字人形象通过（资产/骨骼/权重/三动画/棚拍图/口型轨/台湾腔女声绑定）")
