# tools/fix_swallow.py —— 把"静默吞异常"按官方手续改造：except 体只有 pass/continue ⇒ 先 _swallow(__file__, e)
import ast, py_compile, sys
from pathlib import Path
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
R = Path(__file__).resolve().parent.parent
changed, skipped = [], []

for p in sorted((R / 'core').glob('*.py')):
    src = p.read_text(encoding='utf-8')
    try:
        tree = ast.parse(src)
    except Exception:
        continue
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body = [b for b in node.body if not isinstance(b, ast.Expr) or not isinstance(getattr(b, 'value', None), ast.Constant)]
            if len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Continue)):
                targets.append(node)
    if not targets:
        continue
    lines = src.splitlines()
    edits = []
    for node in targets:
        # 该 handler 的第一条语句行（1-based）
        ln = node.body[0].lineno
        indent = len(lines[ln - 1]) - len(lines[ln - 1].lstrip())
        edits.append((ln, indent, node))
    # 从后往前插，避免行号漂移
    for ln, indent, node in sorted(edits, key=lambda x: -x[0]):
        need_bind = not isinstance(node.name, str) or not node.name
        if need_bind:
            old = lines[node.lineno - 1]
            lines[node.lineno - 1] = old.rstrip() + ' as _e_swallow'
            var = '_e_swallow'
        else:
            var = node.name
        lines.insert(ln - 1, ' ' * indent + '_swallow(__file__, %s)' % var)
    new = chr(10).join(lines) + chr(10)
    if '_swallow' in src and 'from core.swallow import' not in new:
        new = 'from core.swallow import swallow as _swallow' + chr(10) + new
    elif 'from core.swallow import' not in new:
        # 插到最后一个 import 之后
        ls = new.splitlines()
        last = max((i for i, l in enumerate(ls) if l.startswith('import ') or l.startswith('from ')), default=0)
        ls.insert(last + 1, 'from core.swallow import swallow as _swallow')
        new = chr(10).join(ls) + chr(10)
    bak = p.read_text(encoding='utf-8')
    p.write_text(new, encoding='utf-8')
    try:
        py_compile.compile(str(p), doraise=True, cfile=str(R / 'state' / '_cs.k'))
        changed.append('%s(%d处)' % (p.name, len(targets)))
    except Exception as e:
        p.write_text(bak, encoding='utf-8')
        skipped.append(p.name + ':' + type(e).__name__)
print('已改造 %d 个文件:' % len(changed), changed)
print('跳过(编译不过已回滚):', skipped or '无')
