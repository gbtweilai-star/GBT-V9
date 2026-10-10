# tools/sweep_company_skills.py —— 公司技能逐项实测（亲自跑、留读数、做登记）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 判据（只看事实，不看感觉）：
#   可跑   = 脚本 --help / -h 退出码 0（或输出里含 usage）
#   缺依赖 = stderr 含 ModuleNotFoundError / ImportError / No module named
#   缺文件 = stderr 含 FileNotFoundError / No such file
#   超时   = 超过 --timeout 秒
#   需宿主 = .ps1/.sh/.mjs/.js/.cmd/.bat（不在本器里执行，只登记）
# 产出：state/skill_sweep.jsonl（逐脚本）+ 控制台汇总
import argparse, json, subprocess, sys, time
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

V9 = Path(__file__).resolve().parent.parent
IDX = V9 / 'state' / 'dsh_skills.json'
LED = V9 / 'state' / 'skill_sweep.jsonl'
PYMAP = {'.ps1': None, '.sh': None, '.mjs': None, '.js': None, '.cmd': None, '.bat': None}


def classify(p, out, err, rc, timed_out):
    if timed_out:
        return '超时'
    low = (out + err).lower()
    if 'modulenotfounderror' in low or 'no module named' in low or 'importerror' in low:
        return '缺依赖'
    if 'filenotfounderror' in low or 'no such file' in low or 'cannot find' in low:
        return '缺文件'
    if rc == 0 or 'usage' in low:
        return '可跑'
    return '报错'


def probe(skill, rec, timeout):
    rows = []
    base = Path(rec['目录'])
    for s in rec.get('脚本', []):
        p = base / s
        ext = p.suffix.lower()
        row = {'技能': skill, '脚本': s, '扩展': ext}
        if ext in PYMAP:
            row.update({'判': '需宿主', '码': None, '读数': ext + ' 需对应宿主（本器不执行）'})
            rows.append(row); continue
        cmd = [sys.executable, str(p), '--help'] if ext == '.py' else [str(p), '--help']
        t0 = time.time(); timed_out = False
        try:
            pr = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=timeout, cwd=str(base))
            out, err, rc = pr.stdout or '', pr.stderr or '', pr.returncode
        except subprocess.TimeoutExpired:
            out = err = ''; rc = None; timed_out = True
        except Exception as e:
            out, err, rc = '', type(e).__name__ + ': ' + str(e)[:120], 1
        verdict = classify(p, out, err, rc, timed_out)
        head = (out or err).strip().splitlines()
        row.update({'判': verdict, '码': rc, '秒': round(time.time() - t0, 2),
                    '读数': (head[0][:150] if head else '（无输出）')})
        rows.append(row)
    LED.parent.mkdir(parents=True, exist_ok=True)
    with LED.open('a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + chr(10))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--cluster', default='')
    ap.add_argument('--timeout', type=int, default=40)
    a = ap.parse_args()
    idx = json.loads(IDX.read_text(encoding='utf-8')) if IDX.is_file() else {}
    names = [k for k in sorted(idx) if (not a.cluster or a.cluster.lower() in k.lower())]
    names = names[:a.limit]
    print('待测技能 %d 个（共 %d）· 超时 %ds' % (len(names), len(idx), a.timeout))
    tally = {}
    for n in names:
        rec = idx[n]
        rows = probe(n, rec, a.timeout)
        for r in rows:
            tally[r['判']] = tally.get(r['判'], 0) + 1
        if rows:
            v = ' / '.join('%s=%s' % (r['脚本'].split('/')[-1], r['判']) for r in rows[:3])
            print('  %-30s %s' % (n[:30], v))
        else:
            print('  %-30s （无脚本，纯文档技能）' % n[:30])
    print()
    print('汇总:', json.dumps(tally, ensure_ascii=False))
    print('账本: state/skill_sweep.jsonl')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
