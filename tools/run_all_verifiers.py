# tools/run_all_verifiers.py —— 全量跑闭环：逐个验收器亲跑，出红绿清单 + 落账
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json, subprocess, sys, time
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
R = Path(__file__).resolve().parent.parent
LED = R / 'state' / 'all_verifiers.jsonl'
verifiers = sorted((R / 'tools').glob('verify_*.py'))
print('待跑验收器 %d 个' % len(verifiers))
rows, tally = [], {}
for v in verifiers:
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, '-u', str(v)], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=420, cwd=str(R))
        out, err, rc = p.stdout or '', p.stderr or '', p.returncode
        verdict = 'PASS' if rc == 0 else 'FAIL'
    except subprocess.TimeoutExpired:
        out = err = ''; rc = None; verdict = 'TIMEOUT'
    if rc == 0 and ('❌' in out):
        verdict = 'FAIL(有红项)'
    tail = [l.strip() for l in (out or err).splitlines() if l.strip()]
    reds = [l for l in tail if l.startswith('❌') or ' ❌' in l][:3]
    row = {'验收器': v.name, '判': verdict, '码': rc, '秒': round(time.time() - t0, 1),
           '读数': (tail[-1][:160] if tail else ''), '红项': [r[:120] for r in reds]}
    rows.append(row)
    tally[verdict] = tally.get(verdict, 0) + 1
    mark = 'OK ' if verdict == 'PASS' else '!! '
    print('  %s%-28s %s · %ss · %s' % (mark, v.name[:28], verdict, row['秒'], row['读数'][:80]))
LED.parent.mkdir(parents=True, exist_ok=True)
with LED.open('a', encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + chr(10))
(R / 'state' / 'all_verifiers.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
print()
print('汇总:', json.dumps(tally, ensure_ascii=False))
bad = [r for r in rows if r['判'] != 'PASS']
if bad:
    print('未通过清单:')
    for r in bad:
        print('  %-28s %s' % (r['验收器'], r['判']))
        for x in r['红项']:
            print('       ', x)
