import json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LED = ROOT / 'state' / 'dh_presence.jsonl'

# 全站化身浮层脚本（不固定在任何页面：由 skills/ui_design.inject 注入到每一页）
OVERLAY_JS = r'''
(function () {
  if (window.__dhPresence) return; window.__dhPresence = true;
  var W = 100, H = 220;            /* 浮层盒尺寸（与 img 高度一致） */
  var FX = 0.03, FY = 0.19;        /* 指认精灵图里**指尖**相对盒子的比例（实测 pose-point-front） */
  var wrap = document.createElement('div');
  wrap.id = 'dh-presence';
  wrap.style.cssText = 'position:fixed;z-index:2147483000;left:0;top:0;pointer-events:none;'
    + 'transition:transform .45s cubic-bezier(.2,.8,.2,1);transform:translate(-400px,-400px)';
  wrap.innerHTML = '<img id="dh-body" src="/avatar-asset/pose-idle-front.png" '
    + 'style="width:100px;height:220px;object-fit:contain;display:block"/>'
    + '<div id="dh-say" style="position:absolute;left:0;top:0;max-width:300px;background:rgba(10,14,20,.95);'
    + 'border:1px solid #2a6cb0;border-radius:10px;padding:8px 10px;color:#e6edf3;font:13px/1.6 system-ui;'
    + 'white-space:pre-wrap;opacity:0;transition:opacity .25s;transform:translateY(-105%)"></div>';
  function mount() { if (document.body && !document.getElementById('dh-presence')) document.body.appendChild(wrap); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
  function say(t) { var b = document.getElementById('dh-say'); if (!b) return; b.textContent = t || ''; b.style.opacity = t ? '1' : '0'; }
  window.dhPoint = function (sel, line) {
    var el = sel ? document.querySelector(sel) : null;
    var r = el ? el.getBoundingClientRect() : { left: innerWidth / 2, top: innerHeight / 2, width: 0, height: 0 };
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    var roomRight = innerWidth - (cx + r.width / 2), roomLeft = cx - r.width / 2;
    var flip = !(roomRight >= W + 24);                 /* 右侧放不下就站到左边并镜像 */
    var fingerX = flip ? W * (1 - FX) : W * FX;
    var x = Math.round(cx - fingerX), y = Math.round(cy - H * FY);
    x = Math.max(6, Math.min(innerWidth - W - 6, x));
    y = Math.max(28, Math.min(innerHeight - H - 6, y));
    wrap.style.transform = 'translate(' + x + 'px,' + y + 'px)';
    var img = document.getElementById('dh-body');
    var pt = (line && line.length) ? 'pose-point-front.png' : 'pose-idle-front.png';
    if (img.getAttribute('data-src') !== pt) { img.src = '/avatar-asset/' + pt; img.setAttribute('data-src', pt); }
    img.style.transform = flip ? 'scaleX(-1)' : 'none';
    if (line) say(line);
    var tip = { x: x + fingerX, y: y + H * FY };
    window.__dhLastPoint = { x: x, y: y, tipX: tip.x, tipY: tip.y, targetX: cx, targetY: cy,
                             err: Math.round(Math.hypot(tip.x - cx, tip.y - cy) * 100) / 100, flip: flip };
    return window.__dhLastPoint;
  };
  setInterval(function () {
    fetch('/api/dh/presence/next').then(function (r) { return r.json(); }).then(function (d) {
      if (d && (d['目标'] || d['说'])) window.dhPoint(d['目标'] || '', d['说'] || '');
    }).catch(function () { });
  }, 4000);
})();
'''
(function () {
  if (window.__dhPresence) return; window.__dhPresence = true;
  var wrap = document.createElement('div');
  wrap.id = 'dh-presence';
  wrap.style.cssText = 'position:fixed;z-index:2147483000;left:0;top:0;pointer-events:none;'
    + 'transition:transform .45s cubic-bezier(.2,.8,.2,1);transform:translate(-200px,-200px)';
  wrap.innerHTML = '<div id="dh-body" style="position:relative;width:120px;height:180px">'
    + '<svg viewBox="0 0 120 180" width="120" height="180">'
    + '<ellipse cx="60" cy="172" rx="30" ry="6" fill="rgba(0,0,0,.35)"/>'
    + '<circle cx="60" cy="34" r="22" fill="#f7e9e6" stroke="#2a3546" stroke-width="2"/>'
    + '<path d="M38 30 q22 -20 44 0 q-6 -16 -22 -16 q-16 0 -22 16z" fill="#2b2f3a"/>'
    + '<rect x="44" y="56" width="32" height="58" rx="12" fill="#12161f" stroke="#3a4a63" stroke-width="2"/>'
    + '<path id="dh-arm" d="M76 70 L104 66 L128 74" stroke="#12161f" stroke-width="8" fill="none" stroke-linecap="round"/>'
    + '<circle id="dh-finger" cx="128" cy="74" r="5" fill="#f7e9e6"/>'
    + '<rect x="48" y="112" width="11" height="46" rx="5" fill="#12161f"/>'
    + '<rect x="61" y="112" width="11" height="46" rx="5" fill="#12161f"/>'
    + '</svg></div>'
    + '<div id="dh-say" style="position:absolute;left:130px;top:8px;max-width:280px;background:rgba(10,14,20,.94);'
    + 'border:1px solid #2a6cb0;border-radius:10px;padding:8px 10px;color:#e6edf3;'
    + 'font:13px/1.6 system-ui;white-space:pre-wrap;opacity:0;transition:opacity .25s"></div>';
  document.addEventListener('DOMContentLoaded', function () { document.body.appendChild(wrap); });
  if (document.body && !document.getElementById('dh-presence')) document.body.appendChild(wrap);

  function say(text) {
    var b = document.getElementById('dh-say');
    if (!b) return;
    b.textContent = text || ''; b.style.opacity = text ? '1' : '0';
  }
  // 站到某个元素前，并用手指指过去
  window.dhPoint = function (selector, line) {
    var el = selector ? document.querySelector(selector) : null;
    var r = el ? el.getBoundingClientRect() : { left: innerWidth / 2 - 60, top: innerHeight / 2 - 90, width: 120, height: 40 };
    var x = Math.max(8, r.left - 96);
    var y = Math.min(innerHeight - 190, Math.max(8, r.top + r.height / 2 - 90));
    wrap.style.transform = 'translate(' + x + 'px,' + y + 'px)';
    var arm = document.getElementById('dh-arm'), fin = document.getElementById('dh-finger');
    var dx = (r.left + r.width / 2) - (x + 76), dy = (r.top + r.height / 2) - (y + 70);
    var ang = Math.atan2(dy, dx), len = Math.min(160, Math.hypot(dx, dy));
    var ex = 76 + Math.cos(ang) * len, ey = 70 + Math.sin(ang) * len;
    if (arm) arm.setAttribute('d', 'M76 70 Q' + (76 + (ex - 76) / 2) + ' ' + (70 + (ey - 70) / 2 - 14) + ' ' + ex + ' ' + ey);
    if (fin) { fin.setAttribute('cx', ex); fin.setAttribute('cy', ey); }
    if (line) say(line);
    return { x: x, y: y, 指到: { x: r.left + r.width / 2, y: r.top + r.height / 2 } };
  };
  // 定期问服务端"我现在该在哪、该说什么"（无指令时不打扰）
  setInterval(function () {
    fetch('/api/dh/presence/next').then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.目标 || d && d.说) window.dhPoint(d.目标 || '', d.说 || '');
    }).catch(function () { });
  }, 5000);
})();
'''


def _log(rec):
    LED.parent.mkdir(parents=True, exist_ok=True)
    with LED.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def point(selector='', line='', *, page=''):
    """让她到指定元素前并指过去（服务端只登记指令；实现由页面浮层做）。"""
    rec = {'at': time.strftime('%Y-%m-%dT%H:%M:%S'), '抓': 'dh.point', '页面': page,
           '目标': selector, '说': line[:200]}
    _log(rec)
    return rec


def next_hint():
    """页面轮询：取最近一条未消费的指令（用户不点就不会有）。"""
    rows = []
    if LED.is_file():
        for line in LED.read_text(encoding='utf-8').splitlines()[-20:]:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    last = rows[-1] if rows else {}
    return {'目标': last.get('目标', ''), '说': last.get('说', ''), '页面': last.get('页面', '')}


def status():
    rows = []
    if LED.is_file():
        for line in LED.read_text(encoding='utf-8').splitlines()[-5:]:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return {'在场': '全站（由 skills/ui_design.inject 注入每一页）', '能力': ['站在用户面前一比一指导', '手指指向出错的地方'],
            '最近指令': rows, '口径': '她不固定在某个页面；页面轮询指令，无指令不打扰'}


__all__ = ['OVERLAY_JS', 'point', 'next_hint', 'status', 'LED']
