# tools/normalize_film.py —— 迭代两遍法 loudnorm + 限幅，落成正式名（stems 同名目录已在）
import json, re, shutil, subprocess, sys
from pathlib import Path
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
R = Path(__file__).resolve().parent.parent
FILM = R / 'render' / 'film'
base = '样板对齐-S01E01'
src = FILM / (base + '-无配乐母版.mp4')
stems = FILM / (base + '-stems')
out = FILM / (base + '.mp4')
mix = FILM / (base + '-mix.wav')


def sh(a, t=3600):
    p = subprocess.run(a, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=t)
    return p.returncode, (p.stdout or ''), (p.stderr or '')


def measure(f):
    rc, o, e = sh(['ffmpeg', '-hide_banner', '-i', str(f), '-af', 'loudnorm=I=-14:TP=-1:LRA=11:print_format=json', '-f', 'null', '-'])
    m = re.search(r'\{[^{}]*input_i[^{}]*\}', e, re.S)
    return json.loads(m.group(0)) if m else {}


rc, o, e = sh(['ffmpeg', '-y', '-i', str(stems / 'dx.m4a'), '-i', str(stems / 'mx.m4a'), '-i', str(stems / 'fx.m4a'),
               '-filter_complex', 'amix=inputs=3:duration=longest:normalize=0', '-c:a', 'pcm_s16le', str(mix)])
d = measure(mix)
print('量得 I=%s TP=%s LRA=%s' % (d.get('input_i'), d.get('input_tp'), d.get('input_lra')))

cur = mix
name = base
for it in range(1, 4):
    m = measure(cur)
    i_meas = float(m.get('input_i') or -99)
    af = ('loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:offset=%s:linear=true,'
          'alimiter=limit=0.891' % (m.get('input_i'), m.get('input_tp'), m.get('input_lra'), m.get('input_thresh'), m.get('target_offset', '0.0')))
    tmp = FILM / (base + '-it%d.wav' % it)
    rc, o, e = sh(['ffmpeg', '-y', '-i', str(cur), '-af', af, '-c:a', 'pcm_s16le', str(tmp)])
    cur = tmp
    mm = measure(cur)
    print('  第%d轮: 从 %s → %s LUFS · TP %s' % (it, m.get('input_i'), mm.get('input_i'), mm.get('input_tp')))
    if abs(float(mm.get('input_i') or -99) + 14.0) <= 1.0:
        break

rc, o, e = sh(['ffmpeg', '-y', '-i', str(src), '-i', str(cur), '-map', '0:v:0', '-map', '1:a',
               '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', str(out)])
mm = measure(out)
print('成品 %s · %.2f MB · 终读 I=%s LUFS TP=%s' % (out.name, out.stat().st_size / 1048576, mm.get('input_i'), mm.get('input_tp')))
