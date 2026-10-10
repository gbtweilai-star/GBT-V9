import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import json, sys, time
from pathlib import Path
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))
import urllib.request as U

PAGES = ['/native', '/fleet-live', '/digital-human', '/dh-input']
ASSETS = ['/avatar-asset/pose-idle-front.png', '/avatar-asset/pose-point-front.png']
FAIL = []


def get(url, timeout=20):
    with U.urlopen('http://127.0.0.1:8800' + url, timeout=timeout) as r:
        return r.status, r.read()


def check(name, ok, reading):
    print(('  ✅ ' if ok else '  ❌ ') + name + ' —— ' + str(reading))
    if not ok:
        FAIL.append(name)


print('== 全站化身验收 ==')
for p in PAGES:
    try:
        st, body = get(p)
        t = body.decode('utf-8', 'replace')
        check('页面带化身 ' + p, st == 200 and 'dh/overlay.js' in t, 'HTTP %s · 注入=%s' % (st, 'dh/overlay.js' in t))
    except Exception as e:
        check('页面带化身 ' + p, False, type(e).__name__)
for a in ASSETS:
    try:
        st, body = get(a)
        check('真身图可取 ' + a, st == 200 and body[:8] == b'\x89PNG\r\n\x1a\n', 'HTTP %s · %d 字节' % (st, len(body)))
    except Exception as e:
        check('真身图可取 ' + a, False, type(e).__name__)

# 指认几何 + 气泡不遮目标（用她自己的浏览器在真实页面里算）
print()
print('== 指认几何（真实页面里算 err 与遮挡）==')
try:
    from core import browser_plug as BP
    BP.pilot('t001', 'open', url='http://127.0.0.1:8800/native')
    time.sleep(4)
    page = (BP._BROWSER or {}).get('page')
    js = ('(function(){'
          ' var el=document.querySelector(".card")||document.querySelector("a")||document.querySelector("h1")||document.body;'
          ' var r=el.getBoundingClientRect();'
          ' var res=window.dhPoint(".card","主人，这里填错了，改这一栏");'
          ' var sb=document.getElementById("dh-say").getBoundingClientRect();'
          ' var ov=Math.max(0,Math.min(sb.right,r.right)-Math.max(sb.left,r.left))*Math.max(0,Math.min(sb.bottom,r.bottom)-Math.max(sb.top,r.top));'
          ' return JSON.stringify({err:res.err,flip:res.flip,x:res.x,y:res.y,tipX:res.tipX,tipY:res.tipY,targetX:res.targetX,targetY:res.targetY,sayOverlap:Math.round(ov),el:el.tagName+"."+(el.className||"").toString().slice(0,20)});'
          '})()')
    got = page.evaluate(js)
    d = json.loads(got) if isinstance(got, str) else got
    print('   读数:', json.dumps(d, ensure_ascii=False))
    check('指尖对准目标（err ≤ 4px）', (d.get('err') or 999) <= 4, 'err=%s px · flip=%s' % (d.get('err'), d.get('flip')))
    check('气泡不遮目标（重叠=0）', (d.get('sayOverlap') or 0) == 0, '重叠面积 %s px²' % d.get('sayOverlap'))
    page.screenshot(path=str(ROOT / 'render' / 'avatar' / '她在班上指错处.png'))
    print('   实证图: render/avatar/她在班上指错处.png')
except Exception as e:
    check('浏览器内指认几何', False, type(e).__name__ + ': ' + str(e)[:80])

print()
if FAIL:
    print('结论：❌ 未通过 %d 项 —— %s' % (len(FAIL), '、'.join(FAIL)))
    raise SystemExit(1)
print('结论：✅ 全站化身通过（四页在场 · 真身图可取 · 指尖 err≤4px · 气泡不遮目标）')
