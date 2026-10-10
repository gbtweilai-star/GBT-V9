import json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import avatar_face as AF
from PIL import Image

OUT = ROOT / 'render' / 'avatar'
OUT.mkdir(parents=True, exist_ok=True)
LED = ROOT / 'state' / 'avatar_shots.jsonl'
VIEWS = {'全身': 1.0, '半身': 2.0, '特写': 4.0, '侧面': 1.0}
EXPRS = ['neutral', 'focused', 'fear', 'angry', 'happy', 'transform']


def html_for(svg, zoom, focus_y):
    # 单帧单文档（防重复 id）；用 transform 做多机位取景
    return ('<!doctype html><meta charset=utf-8><style>html,body{margin:0;background:#06070b;'
            'width:1080px;height:1920px;overflow:hidden}'
            '.stage{width:1080px;height:1920px;display:flex;align-items:center;justify-content:center}'
            'svg{height:1920px;width:auto;transform:scale(' + str(zoom) + ') translateY(' + str(focus_y) + 'px);'
            'transform-origin:center center}</style><div class=stage>' + svg + '</div>')


def shoot(name, svg, zoom=1.0, focus_y=0.0):
    from core import browser_plug as BP
    BP._start()
    page = (BP._BROWSER or {}).get('page')
    page.set_viewport_size({'width': 1080, 'height': 1920})
    tmp = OUT / '_frame.html'
    tmp.write_text(html_for(svg, zoom, focus_y), encoding='utf-8')
    page.goto(tmp.as_uri(), wait_until='load')
    png = OUT / (name + '.png')
    page.screenshot(path=str(png))
    return png


def metrics(png):
    im = Image.open(png).convert('RGB')
    w, h = im.size
    px = list(im.getdata())
    n = len(px)
    skin = sum(1 for r, g, b in px if r > 200 and 185 < g < 245 and 175 < b < 240)
    # 人形连通域（非背景）粗略：按行找非背景像素
    rows = []
    for y in range(0, h, 8):
        row = px[y * w:(y + 1) * w:10]
        if any(abs(r - 6) + abs(g - 7) + abs(b - 11) > 40 for r, g, b in row):
            rows.append(y)
    body_h = (max(rows) - min(rows)) / h if rows else 0.0
    foot = False
    for y in range(int(h * 0.8), h, 4):
        row = px[y * w:(y + 1) * w:10]
        if any(abs(r - 6) + abs(g - 7) + abs(b - 11) > 40 for r, g, b in row):
            foot = True
            break
    return {'肤色占比': round(skin / n * 100, 2), '人形高度占比': round(body_h * 100, 1), '腿脚在画内': foot}


def main():
    t0 = time.time()
    shots = {}
    # 四机位（neutral）
    for v, z in VIEWS.items():
        svg = AF.character_svg(expression='neutral', talking=False, blink=0.2, clip='idle')
        shots['机位-' + v] = str(shoot('view-' + v, svg, zoom=z, focus_y=(0 if v != '特写' else 260)))
    # 表情表
    for e in EXPRS:
        svg = AF.character_svg(expression=e, talking=True, blink=0.2, clip='idle')
        shots['表情-' + e] = str(shoot('expr-' + e, svg, zoom=1.6))
    m = metrics(OUT / 'view-全身.png')
    rec = {'at': time.strftime('%Y-%m-%dT%H:%M:%S'), '图数': len(shots), '图': shots, '全身读数': m,
           '秒': round(time.time() - t0, 1)}
    LED.parent.mkdir(parents=True, exist_ok=True)
    with LED.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    print('图数:', len(shots))
    for k in shots:
        print('  ', k, Path(shots[k]).name)
    print('全身读数:', json.dumps(m, ensure_ascii=False))
    print('用时 %.1fs' % rec['秒'])


if __name__ == '__main__':
    main()
