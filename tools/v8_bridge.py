# tools/v8_bridge.py —— 把原生能力（V8）接进 V9：数字人流水线 / 万能插能力位 / DSH 插件总线
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 用法：
#   python tools/v8_bridge.py probe                 # 探针：关键件在不在、解释器在不在
#   python tools/v8_bridge.py status                # 数字人流水线全链门 S0-S13
#   python tools/v8_bridge.py map                   # 阶段地图（输入/输出/命令/回退）
#   python tools/v8_bridge.py run S9                # 单跑某阶段（工具+门）
#   python tools/v8_bridge.py cap <能力位> [json]    # 万能插能力位（five-dim.py cap）
#   python tools/v8_bridge.py bus                   # DSH 插件总线（158 插件面）
import json, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

V9 = Path(__file__).resolve().parent.parent
V8 = Path('C:/Users/ADMIN/Desktop/GBT小土豆V8')
PY = r'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.14-64\python.exe'
PIPE = V8 / 'tools' / 'digihuman' / 'pipeline.py'
FIVE = V8 / 'tools' / 'five-dim.py'
BUS = V8 / 'tools' / 'codex-scripts' / 'dsh-bus.py'
RIG = V8 / 'holo_pet' / 'assets' / 'digital-human' / 'raw' / 'rig'


def _run(args, timeout=1800):
    p = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    return p.returncode, (p.stdout or ''), (p.stderr or '')


def probe():
    items = {'V8 根目录': V8.is_dir(), 'python3.14': Path(PY).is_file(),
             '流水线 pipeline.py': PIPE.is_file(), '万能插 five-dim.py': FIVE.is_file(),
             'DSH 总线 dsh-bus.py': BUS.is_file(),
             '真身 avatar-hd.glb': (RIG / 'avatar-hd.glb').is_file(),
             '弹簧链 model-springs-chains.glb': (RIG / 'model-springs-chains.glb').is_file(),
             '表情 model-expressions.glb': (RIG / 'model-expressions.glb').is_file(),
             'avatar.vrm': (RIG / 'avatar.vrm').is_file()}
    for k, v in items.items():
        print(('  [OK] ' if v else '  [--] ') + k)
    return items


def status():
    return _run([PY, str(PIPE), 'status'])


def stage_map():
    return _run([PY, str(PIPE), 'map'])


def run_stage(name):
    s = name.upper()
    if not s.startswith('S'):
        s = 'S' + s
    return _run([PY, str(PIPE), 'run', s])


def cap(kind, payload=None):
    args = [PY, str(FIVE), 'cap', kind]
    if payload:
        args.append(json.dumps(payload, ensure_ascii=False))
    return _run(args)


def bus():
    return _run([PY, str(BUS)])


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 0
    cmd = a[0]
    if cmd == 'probe':
        return 0 if all(probe().values()) else 1
    if cmd == 'status':
        rc, o, e = status(); print((o or e)[-1600:]); return rc
    if cmd == 'map':
        rc, o, e = stage_map(); print((o or e)[:3000]); return rc
    if cmd == 'run':
        rc, o, e = run_stage(a[1] if len(a) > 1 else 'S9'); print((o or e)[-1200:]); return rc
    if cmd == 'cap':
        payload = json.loads(a[2]) if len(a) > 2 else None
        rc, o, e = cap(a[1], payload); print((o or e)[:2000]); return rc
    if cmd == 'bus':
        rc, o, e = bus(); print((o or e)[:2000]); return rc
    print('未知子命令:', cmd)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
