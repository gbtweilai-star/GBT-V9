import json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LED = ROOT / 'state' / 'dh_presence.jsonl'

OVERLAY_JS = r'''
(function () {
  if (window.__dhPresence) return; window.__dhPresence = true;
  var W = 100, H = 220, FX = 0.03, FY = 0.19;
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
    var roomRight = innerWidth - (cx + r.width / 2);
    var flip = !(roomRight >= W + 24);
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
      if (!d) return;
      var t = d.target || d['\u76ee\u6807'] || '';
      var s = d.say || d['\u8bf4'] || '';
      if (t || s) window.dhPoint(t, s);
    }).catch(function () { });
  }, 4000);
})();
'''


def _log(rec):
    LED.parent.mkdir(parents=True, exist_ok=True)
    with LED.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def point(selector='', line='', *, page=''):
    rec = {'at': time.strftime('%Y-%m-%dT%H:%M:%S'), '抓': 'dh.point', '页面': page, '目标': selector, '说': line[:200]}
    _log(rec)
    return rec


def next_hint():
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
    return {'在场': '全站（ui_design.inject 每页注入）',
            '真身': 'assets/avatar/pose-idle-front.png · pose-point-front.png',
            '指向': '指尖对准目标中心（err 自报）'}


__all__ = ['OVERLAY_JS', 'point', 'next_hint', 'status', 'LED']
