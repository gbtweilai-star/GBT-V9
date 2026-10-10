# tools/dsh_skills.py —— 把**公司技能（DSH skill 目录）**接进 V9：可列、可查、可跑脚本
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 用法：
#   python tools/dsh_skills.py index                 # 扫所有技能根，写 state/dsh_skills.json
#   python tools/dsh_skills.py list [关键词]          # 列出技能（按名字/描述过滤）
#   python tools/dsh_skills.py show <技能名>          # 打印 SKILL.md 头 60 行
#   python tools/dsh_skills.py scripts <技能名>       # 列出该技能自带的脚本
#   python tools/dsh_skills.py run <技能名> <脚本> [参数...]   # 跑它自带的脚本（只跑技能目录内的脚本）
import json, re, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HOME = Path.home()
ROOTS = [HOME / '.openclaw-autoclaw' / 'skills', HOME / '.agents' / 'skills',
         Path('C:/Users/ADMIN/Desktop/GBT小土豆V8/.agents/skills'),
         Path('C:/Users/ADMIN/Desktop/GBT小土豆V8/.claude/skills')]
V9 = Path(__file__).resolve().parent.parent
INDEX = V9 / 'state' / 'dsh_skills.json'


def parse_skill(d: Path):
    f = d / 'SKILL.md'
    if not f.is_file():
        return None
    t = f.read_text(encoding='utf-8', errors='ignore')
    name = d.name
    m = re.search(r'^name:\s*([^\n]+)', t, re.M)
    if m:
        name = m.group(1).strip().strip('"').strip("'")
    desc = ''
    m = re.search(r'^description:\s*([^\n]+)', t, re.M)
    if m:
        desc = m.group(1).strip()
    if not desc:
        for line in t.splitlines():
            s = line.strip()
            if s and not s.startswith('#') and not s.startswith('---'):
                desc = s
                break
    scripts = [str(p.relative_to(d)).replace(chr(92), '/') for p in d.rglob('*')
               if p.is_file() and p.suffix.lower() in ('.py', '.ps1', '.sh', '.mjs', '.js', '.cmd', '.bat')]
    return {'名': name, '目录': str(d), '根': str(d.parent), '描述': desc[:200],
            '脚本数': len(scripts), '脚本': scripts[:40]}


def build_index(verbose=True):
    idx = {}
    for root in ROOTS:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            rec = parse_skill(d)
            if rec:
                idx.setdefault(rec['名'], rec)
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding='utf-8')
    if verbose:
        print('技能根 %d 个 · 收录技能 %d 个 → %s' % (len([r for r in ROOTS if r.is_dir()]), len(idx), INDEX.name))
    return idx


def load():
    if INDEX.is_file():
        return json.loads(INDEX.read_text(encoding='utf-8'))
    return build_index()


def main():
    a = sys.argv[1:]
    if not a or a[0] == 'index':
        idx = build_index()
        return 0
    idx = load()
    cmd = a[0]
    if cmd == 'list':
        kw = (a[1] if len(a) > 1 else '').lower()
        rows = [(k, v) for k, v in sorted(idx.items())
                if not kw or kw in k.lower() or kw in str(v.get('描述', '')).lower()]
        print('命中 %d / 共 %d 个公司技能' % (len(rows), len(idx)))
        for k, v in rows[:40]:
            print('  %-28s 脚本%-3s %s' % (k, v.get('脚本数', 0), str(v.get('描述', ''))[:64]))
        return 0
    if cmd == 'show':
        v = idx.get(a[1]) if len(a) > 1 else None
        if not v:
            print('没有这个技能:', a[1:]); return 2
        f = Path(v['目录']) / 'SKILL.md'
        print('=== %s ===\n%s' % (a[1], chr(10).join(f.read_text(encoding='utf-8', errors='replace').splitlines()[:60])))
        return 0
    if cmd == 'scripts':
        v = idx.get(a[1]) if len(a) > 1 else None
        if not v:
            print('没有这个技能:', a[1:]); return 2
        print('=== %s 自带脚本 %d 个 ===' % (a[1], v.get('脚本数', 0)))
        for s in v.get('脚本', []):
            print('  ', s)
        return 0
    if cmd == 'run':
        if len(a) < 3:
            print('用法: run <技能名> <脚本> [参数...]'); return 2
        v = idx.get(a[1])
        if not v:
            print('没有这个技能:', a[1]); return 2
        target = (Path(v['目录']) / a[2]).resolve()
        if not str(target).startswith(str(Path(v['目录']).resolve())) or not target.is_file():
            print('拒绝：只能跑该技能目录内的脚本 →', a[2]); return 3
        args = [sys.executable, str(target)] + a[3:] if target.suffix == '.py' else [str(target)] + a[3:]
        p = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
        print((p.stdout or '')[-2500:] or (p.stderr or '')[-1200:])
        return p.returncode
    print(__doc__)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
