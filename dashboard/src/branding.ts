# tools/brand_assets.py —— 图4 源图 → Octop 全套品牌资源
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 用法: python tools/brand_assets.py 图4.png gbt-octop/dashboard/public/branding
import sys
from pathlib import Path
from PIL import Image

SIZES = {
    "favicon.ico": [16, 32, 48, 64],
    "logo-32.png": [32], "logo-64.png": [64],
    "logo-128.png": [128], "logo-192.png": [192],
    "logo-256.png": [256], "logo-512.png": [512],
    "apple-touch-icon.png": [180],
}

def square(im):
    w, h = im.size; s = min(w, h)
    return im.crop(((w - s)//2, (h - s)//2, (w + s)//2, (h + s)//2))

def main(src, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    base = square(Image.open(src).convert("RGBA"))
    ico = base.resize((64, 64), Image.LANCZOS)
    ico.save(out / "favicon.ico", sizes=[(s, s) for s in SIZES["favicon.ico"]])
    for name, sizes in SIZES.items():
        if name == "favicon.ico": continue
        for s in sizes:
            base.resize((s, s), Image.LANCZOS).save(out / name)
    # 深色底版（护目镜发光在深色上更亮）
    dark = Image.new("RGBA", base.size, (11, 16, 32, 255))
    dark.alpha_composite(base)
    dark.save(out / "logo-dark.png")
    base.save(out / "logo.svg.png")   # 占位；SVG 建议用矢量描一遍
    print("生成完毕:", sorted(p.name for p in out.iterdir()))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
