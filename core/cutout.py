# core/cutout.py —— 抠图：把参考图的背景去掉再喂给三维重建（本地优先，零成本）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人指出（2026-10-07）："你不把图片背景去掉这么打磨？"
#   对——先前喂给云端的参考图都带着深色背景，背景会跟着重建进网格与贴图里（边缘发黑、贴图发脏）。
#   本模块先把主体抠出来（带 alpha），再送去重建。
#
# 两条路，本地优先：
#   ① 神经网络抠图：本机若已有 rembg + 模型缓存（onnx），优先用它（发丝/半透明边缘最好）
#   ② 背景色差法：不需要任何下载 —— 从四边估背景色，按 Lab 色差取主体、
#      保留最大连通块、边缘羽化（这些参考图是**纯深灰底**，色差法就够干净）
# 纪律：不下载任何东西也不出网；结果落 state/tripo/cut/；返回覆盖率等真读数便于核对。
from core.swallow import swallow as _swallow
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "state" / "tripo" / "cut"


def _try_rembg(src: Path, dest: Path) -> dict | None:
    """有缓存模型才用神经网络抠图（无缓存会尝试下载 → 失败就返回 None，不阻塞）。"""
    try:
        import os
        os.environ.setdefault("REMBG_NO_DOWNLOAD", "1")     # 不许它偷偷下载
        from rembg import remove                            # noqa: PLC0415
        from PIL import Image
        im = Image.open(src).convert("RGBA")
        got = remove(im)
        got.save(dest, "PNG")
        a = got.split()[-1]
        cov = sum(1 for px in a.getdata() if px > 32) / float(max(1, a.width * a.height))
        return {"方法": "神经网络(rembg)", "覆盖率": round(cov, 3)}
    except Exception:                                       # noqa: BLE001
        return None


def _color_cut(src: Path, dest: Path, *, tol: float = 26.0, feather: int = 2) -> dict:
    """背景色差法：从四边估背景 → Lab 色差取主体 → 最大连通块 → 羽化。纯本地、零下载。"""
    import numpy as np
    from PIL import Image, ImageFilter
    from skimage import color as skcolor
    from skimage.measure import label as sklabel
    from skimage.morphology import binary_closing, remove_small_holes

    im = Image.open(src).convert("RGB")
    a = np.array(im)
    h, w, _ = a.shape
    band = 12
    border = np.concatenate([a[:band].reshape(-1, 3), a[-band:].reshape(-1, 3),
                             a[:, :band].reshape(-1, 3), a[:, -band:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    lab = skcolor.rgb2lab(a.astype(np.float32) / 255.0)
    bg_lab = skcolor.rgb2lab((bg[None, None, :] / 255.0).astype(np.float32))[0, 0]
    dist = np.linalg.norm(lab - bg_lab[None, None, :], axis=2)
    mask = dist > tol
    # 去噪 + 只留最大连通块（避免零散背景噪点）
    mask = binary_closing(mask, np.ones((3, 3), bool))
    lb = sklabel(mask)
    if lb.max() > 0:
        sizes = np.bincount(lb.ravel())
        sizes[0] = 0
        mask = lb == int(np.argmax(sizes))
    mask = remove_small_holes(mask, area_threshold=max(64, (h * w) // 4000))
    alpha = Image.fromarray((mask * 255).astype(np.uint8), "L").filter(
        ImageFilter.GaussianBlur(feather))
    out = im.convert("RGBA")
    out.putalpha(alpha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, "PNG")
    arr = np.array(alpha)
    return {"方法": "背景色差法(本地)", "背景色": [int(x) for x in bg],
            "覆盖率": round(float((arr > 32).mean()), 3),
            "边缘像素": int(((arr > 8) & (arr < 248)).sum())}


def cut(path: str | Path, *, out_name: str = "", prefer_nn: bool = True) -> dict:
    """抠一张图：出带 alpha 的 PNG（同时给一份白底版，喂云端更稳）。"""
    src = Path(path)
    if not src.is_file():
        return {"ok": False, "reason": f"找不到图：{src}"}
    OUT.mkdir(parents=True, exist_ok=True)
    name = out_name or (src.stem + "_cut")
    dest = OUT / f"{name}.png"
    white = OUT / f"{name}_white.png"
    got = None
    if prefer_nn:
        got = _try_rembg(src, dest)
    if got is None:
        got = _color_cut(src, dest)
    try:                                                    # 白底版：重建时更不容易把背景吃进去
        from PIL import Image
        im = Image.open(dest).convert("RGBA")
        canvas = Image.new("RGB", im.size, (250, 250, 250))
        canvas.paste(im, (0, 0), im)
        canvas.save(white, "PNG")
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": True, "源": src.name, "抠图": str(dest), "白底": str(white), **got,
            "口径": "先把主体抠出来再重建：背景不会被烘进网格与贴图"}


def cut_many(paths: list, *, prefer_nn: bool = True) -> dict:
    """批量抠图（四视图一次做完）。"""
    rows = []
    for p in paths:
        r = cut(p, prefer_nn=prefer_nn)
        rows.append(r)
    ok = [r for r in rows if r.get("ok")]
    return {"ok": bool(ok), "张数": len(rows), "成功": len(ok), "明细": rows,
            "目录": str(OUT)}


def status() -> dict:
    have_nn = False
    try:
        import os
        os.environ.setdefault("REMBG_NO_DOWNLOAD", "1")
        import rembg  # noqa: F401,PLC0415
        from pathlib import Path as _P
        have_nn = any((_P.home() / ".u2net").glob("*.onnx")) if (_P.home() / ".u2net").is_dir() else False
    except Exception:                                       # noqa: BLE001
        have_nn = False
    return {"神经网络抠图": have_nn,
            "说明": ("有本地模型就用 rembg；没有就用背景色差法（零下载、纯本地）"),
            "用过的图": len(list(OUT.glob("*.png"))) if OUT.is_dir() else 0,
            "目录": str(OUT)}


__all__ = ["cut", "cut_many", "status", "OUT"]
