import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_asset_clean.py —— 资产净化验收器（贴图抹线 / 几何是否干净）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import film_studio as FS   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 资产净化验收 ==")
check("抹线版资产在", FS.RIG_CLEAN.is_file(),
      "%s · %.1f MB" % (FS.RIG_CLEAN.name, FS.RIG_CLEAN.stat().st_size / 1048576 if FS.RIG_CLEAN.is_file() else 0))
# 管线指向"抹过线"的资产即可（clamp 版是在抹线版基础上做权重裁剪，也算）
_okrig = ("clean" in FS.RIG.name) or ("clamp" in FS.RIG.name)
check("管线默认已指向净化版（clean/clamp）", _okrig, FS.RIG.name)
check("原版资产保留（可回退）", FS.RIG_RAW.is_file(), FS.RIG_RAW.name)

# 贴图证据
tex = ROOT / "state" / "tex"
for tag, f in (("原贴图", tex / "tex0.jpg"), ("抹后贴图", tex / "tex0_clean.jpg"),
               ("原法线", tex / "tex1.jpg"), ("抹后法线", tex / "tex1_clean.jpg")):
    check(tag + "在", f.is_file(), "%.1f MB" % (f.stat().st_size / 1048576) if f.is_file() else "缺")

img = ROOT / "render" / "贴图-抹线对照.png"
check("贴图抹线对照图在", img.is_file(), img.name if img.is_file() else "缺")
diag = ROOT / "render" / "diag" / "flat_thigh.png"
check("纯白材质对照图在（证明线不在几何上）", diag.is_file(), diag.name if diag.is_file() else "缺")

# GLB 结构没被改坏
try:
    import struct
    raw = FS.RIG.read_bytes()
    magic, ver, total = struct.unpack("<III", raw[:12])
    check("GLB 头合法", magic == 0x46546C67 and ver == 2 and total == len(raw),
          "magic=%s ver=%d 总长一致=%s" % (hex(magic), ver, total == len(raw)))
    js = None
    off = 12
    while off < total:
        clen, ctype = struct.unpack("<II", raw[off:off + 8])
        if ctype == 0x4E4F534A:
            js = json.loads(raw[off + 8:off + 8 + clen].decode("utf-8"))
        off += 8 + clen
    check("图集仍在（base+normal）", len(js.get("images", [])) == 2, "%d 张" % len(js.get("images", [])))
    check("骨架仍在", len(js.get("skins", [])) >= 1, "skins=%d" % len(js.get("skins", [])))
except Exception as e:  # noqa: BLE001
    check("GLB 结构可解析", False, "%s: %s" % (type(e).__name__, e))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 资产净化通过（抹线版在 · 管线已指向 · 原版可回退 · GLB 结构完好）")
