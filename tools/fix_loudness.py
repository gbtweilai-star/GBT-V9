# tools/fix_loudness.py —— 两遍法 loudnorm：先量后套，把响度真正归一到 -14 LUFS
import json, re, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
R = Path(__file__).resolve().parent.parent
FILM = R / 'render' / 'film'
base = sys.argv[1] if len(sys.argv) > 1 else '样板对齐-S01E01'
src = FILM / (base + '-无配乐母版.mp4')
stems = FILM / (base + '-stems')
out = FILM / (base + '-响度归一.mp4')
mix = FILM / (base + '-mix.wav')


def sh(a, t=3600):
    p = subprocess.run(a, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=t)
    return p.returncode, (p.stdout or ''), (p.stderr or '')


print('素材:', src.name, src.is_file(), '| stems:', stems.is_dir())
rc, o, e = sh(['ffmpeg', '-y', '-i', str(stems / 'dx.m4a'), '-i', str(stems / 'mx.m4a'), '-i', str(stems / 'fx.m4a'),
               '-filter_complex', 'amix=inputs=3:duration=longest:normalize=0', '-c:a', 'pcm_s16le', str(mix)])
print('① 混三轨 → mix.wav:', mix.is_file())

rc, o, e = sh(['ffmpeg', '-hide_banner', '-i', str(mix), '-af', 'loudnorm=I=-14:TP=-1:LRA=11:print_format=json', '-f', 'null', '-'])
m = re.search(r'\{[^{}]*input_i[^{}]*\}', e, re.S)
d = json.loads(m.group(0)) if m else {}
print('② 第一遍量得: I=%s TP=%s LRA=%s thresh=%s' % (d.get('input_i'), d.get('input_tp'), d.get('input_lra'), d.get('input_thresh')))

af = ('loudnorm=I=-14:TP=-1:LRA=11:measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:offset=%s:linear=true'
      % (d.get('input_i'), d.get('input_tp'), d.get('input_lra'), d.get('input_thresh'), d.get('target_offset', '0.0')))
rc, o, e = sh(['ffmpeg', '-y', '-i', str(src), '-i', str(mix), '-filter_complex', '[1:a]' + af + '[a]',
               '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', str(out)])
print('③ 第二遍套用 → 成品:', out.name, out.is_file(), round(out.stat().st_size / 1048576, 2) if out.is_file() else 0, 'MB')
print('   (ffmpeg 尾)', (e or '')[-120:].replace(chr(10), ' '))
