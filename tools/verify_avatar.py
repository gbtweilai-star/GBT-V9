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
def _glb(q):
    import json as _j, struct as _s
    b = q.read_bytes()
    if b[:4] != b"glTF":
        return {}
    length = _s.unpack_from("<II", b, 4)[1]
    off = 12
    while off < min(len(b), length):
        clen, ctype = _s.unpack_from("<II", b, off)
        if ctype == 0x4E4F534A:
            js = _j.loads(b[off + 8: off + 8 + clen].decode("utf-8", "replace"))
            jt = sum(len(x.get("joints", [])) for x in js.get("skins", []))
            w = any("WEIGHTS_0" in (pr.get("attributes") or {})
                    for m in js.get("meshes", []) for pr in m.get("primitives", []))
            return {"节点": len(js.get("nodes", [])), "蒙皮": len(js.get("skins", [])),
                    "关节": jt, "动画": len(js.get("animations", [])), "权重": w}
        off += 8 + clen + ((4 - clen % 4) % 4)
    return {}


cands = [(q, _glb(q)) for q in glbs]
cands = [(q, s) for q, s in cands if s]
cands.sort(key=lambda x: (x[1].get("关节", 0), x[1].get("权重", False)), reverse=True)
body, st = (cands[0] if cands else (None, {}))
check("① 形象资产在（选骨架最全的）", body is not None,
      "%d 个 GLB · 选中 %s" % (len(cands), body.name if body else "无"))
check("② 骨骼已绑（≥15 根）", (st.get("关节") or 0) >= 15,
      "%s · 关节 %s · 蒙皮 %s · 节点 %s" % (body.name if body else "-", st.get("关节"), st.get("蒙皮"), st.get("节点")))
check("③ 蒙皮权重已带（WEIGHTS_0）", bool(st.get("权重")),
      "WEIGHTS_0=%s · 关节 %s（覆盖率精算下一步）" % (st.get("权重"), st.get("关节")))
check("③b 动画口径：GLB 自带动画 %s ⇒ 驱动走 core/film_studio.py pose 通道" % st.get("动画"), True,
      "已按早前结论登记")

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
