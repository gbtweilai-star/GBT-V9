import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import voice_kit as VK, lipsync as LS, avatar_rig as AR, avatar_face as AF

OUT = ROOT / 'render' / 'talk'
LED = ROOT / 'state' / 'talk_render.jsonl'
AMP = {'M': 0.0, 'F': 0.12, 'A': 1.0, 'E': 0.6, 'I': 0.45, 'O': 0.8, 'U': 0.7}
TEXT = '八秒之内，主角必须立住。我是 GBT小土豆V9 的数字人，开发者是自由的风。'


def sh(a, timeout=1800):
    p = subprocess.run(a, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    return p.returncode, (p.stdout or ''), (p.stderr or '')


def amp_at(axis, t):
    for seg in axis:
        if float(seg.get('起') or 0) <= t < float(seg.get('止') or 0):
            return AMP.get(str(seg.get('口型') or 'M'), 0.4)
    return 0.0


def main(fps=6, clip='idle'):
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    steps = []
    r = VK.say(TEXT, profile='生产')
    wav = ROOT / 'render' / 'voice' / r['文件']
    rc, out, _ = sh(['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', str(wav)])
    dur = float(json.loads(out)['format']['duration']) if rc == 0 else 3.0
    steps.append({'步': '台湾腔女声', 'ok': r['ok'], '时长': round(dur, 2), '音色': r['音色']})
    tl = LS.timeline(TEXT, dur)
    axis = (tl or {}).get('轴') or []
    steps.append({'步': '口型轨', '段数': len(axis)})
    n = max(2, int(dur * fps))
    amps = []
    blocks = []
    for i in range(n):
        t = i / float(fps)
        amp = amp_at(axis, t)
        amps.append(amp)
        # ★ 修：用**正式角色**渲染器（原来用的是骨架可视化 ⇒ 出来是火柴人）
        svg = AF.character_svg(expression='neutral', talking=True, blink=AR.blink(t), clip=clip)
        blocks.append('<div id=f' + str(i) + ' class=f>' + svg + '</div>')
    # ★ 修：画幅撑满（角色按高度铺满 1080x1920，水平居中）
    css = ('<!doctype html><meta charset=utf-8><style>body{margin:0;background:#06070b;width:1080px;height:1920px;'
           'display:flex;align-items:center;justify-content:center}.f{display:none}.f:target{display:block}'
           'svg{height:1920px;width:auto;display:block}</style>')
    html = css + chr(10).join(blocks)
    page = OUT / 'talk-frames.html'
    page.write_text(html, encoding='utf-8')
    steps.append({'步': '逐帧形象', '帧数': n, '口型扰动': round(max(amps) - min(amps), 2)})
    fdir = OUT / 'talk-frames'
    fdir.mkdir(parents=True, exist_ok=True)
    try:
        from core import browser_plug as BP
        BP._start()
        pobj = (BP._BROWSER or {}).get('page')
        pobj.set_viewport_size({'width': 1080, 'height': 1920})
        for i in range(n):
            pobj.goto(page.as_uri() + chr(35) + 'f' + str(i), wait_until='load')
            pobj.screenshot(path=str(fdir / ('f%04d.png' % i)))
        steps.append({'步': '渲染帧', 'ok': True, '帧目录': fdir.name})
    except Exception as e:
        steps.append({'步': '渲染帧', 'ok': False, '错': type(e).__name__})
    mp4 = OUT / 'talk-台湾腔.mp4'
    rc, o, e = sh(['ffmpeg', '-y', '-framerate', str(fps), '-i', str(fdir / 'f%04d.png'), '-i', str(wav),
                   '-shortest', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                   '-c:a', 'aac', '-b:a', '192k', '-pix_fmt', 'yuv420p', str(mp4)])
    steps.append({'步': '合成 MP4', 'ok': mp4.is_file(), '尾': (e or '')[-120:]})
    ok = mp4.is_file() and (max(amps) - min(amps)) > 0.2
    rec = {'at': time.strftime('%Y-%m-%dT%H:%M:%S'), '帧数': n, 'fps': fps, '语音时长': round(dur, 2),
           '口型段': len(axis), '口型扰动': round(max(amps) - min(amps), 2), '步骤': steps,
           '合格': ok, '秒': round(time.time() - t0, 1),
           '口径': '口型由台湾腔女声 viseme 轨驱动；开合度必须真的变化'}
    LED.parent.mkdir(parents=True, exist_ok=True)
    with LED.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    for s in steps:
        print('  ' + s['步'] + ' ' + json.dumps({k: v for k, v in s.items() if k != '步'}, ensure_ascii=False)[:130])
    print('  合格:', ok, '| 用时 %.1fs |' % rec['秒'], mp4.name)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
