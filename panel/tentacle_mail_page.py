# panel/tentacle_mail_page.py —— 触手邮箱页（**内容可见**：她到底做没做，一眼看得到）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人要求（2026-10-09）：「还有各个页面忘记设计了，就是触手邮箱显示……邮箱内容必须用户可以看到
#   她到底有没有做。」
# 本页三块：① 编队（100 个专属地址/模式/可用性）② **邮件留痕含正文**（方向/触手/对方/主题/正文/结果/时间）
#   ③ 发信（未配 SMTP 时如实标"演练未真发"，不假装发出去）。
from __future__ import annotations

import json
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from skills.ui_design import inject

router = APIRouter()


def _fleet():
    from audit.ledger_factory import make_ledger
    from core.mailbox_fleet import MailboxFleet
    return MailboxFleet(make_ledger(), n=100)


@router.get("/tentacle-mail", response_class=HTMLResponse)
async def tentacle_mail_page() -> HTMLResponse:
    html = """<!doctype html><meta charset=utf-8><title>触手邮箱</title>
<style>
 body{margin:0;background:#0b0e14;color:#e6edf3;font:14px/1.6 system-ui,"Microsoft YaHei"}
 .wrap{padding:18px 22px;max-width:1280px;margin:0 auto}
 h1{font-size:19px;margin:6px 0 4px} h3{font-size:15px;margin:16px 0 6px;color:#ffd479}
 .card{background:#0f131c;border:1px solid #1e2635;border-radius:12px;padding:14px 16px;margin:12px 0}
 table{width:100%;border-collapse:collapse;font-size:13px} th,td{border-bottom:1px solid #1b2230;padding:6px 8px;text-align:left;vertical-align:top}
 th{color:#8fb2d9;font-weight:600} .muted{color:#7d8da4;font-size:12px}
 .ok{color:#57d38c}.bad{color:#ef6b6b}.warn{color:#e9c46a}
 pre{white-space:pre-wrap;word-break:break-word;background:#0b0f17;border:1px solid #1b2230;border-radius:8px;padding:8px;margin:4px 0 0}
 input,textarea{background:#0d1017;color:#e6edf3;border:1px solid #263041;border-radius:8px;padding:7px;font-family:inherit}
 .btn{background:#1f6feb;border:0;color:#fff;border-radius:8px;padding:8px 14px;cursor:pointer}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
</style>
<div class=wrap>
 <h1>📮 触手邮箱 · 内容可见</h1>
 <div class=muted>一格一根触手；**邮件正文留痕**（她到底发了什么、收到什么，你能直接看）。未配 SMTP/IMAP 时如实标"演练未真发"，不假装。</div>
 <div class=card id=hd>加载编队状态…</div>
 <div class=card>
   <h3>① 编队（100 个专属地址）</h3>
   <div id=fleet class=muted>加载中…</div>
 </div>
 <div class=card>
   <h3>② 邮件留痕（含正文 —— 这就是"她到底做没做"的证据）</h3>
   <div class=row><input id=fbox placeholder="按触手格过滤，如 GBT-D1" style="width:180px">
     <select id=fdir style="background:#0d1017;color:#e6edf3;border:1px solid #263041;border-radius:8px;padding:7px">
       <option value="">全部方向</option><option value="send">发出</option><option value="recv">收到</option></select>
     <button class=btn onclick="load()">刷新</button></div>
   <div id=msgs class=muted>加载中…</div>
 </div>
 <div class=card>
   <h3>③ 发信（走框架邮箱通道）</h3>
   <div class=row>
     <input id=tn value="1" style="width:70px" title="触手编号">
     <input id=to placeholder="收件人" style="width:230px">
     <input id=subj placeholder="主题" style="width:260px">
   </div>
   <textarea id=body rows=3 style="width:100%;margin-top:8px" placeholder="正文（会留痕，主人可见）"></textarea>
   <div class=row style="margin-top:8px"><button class=btn onclick="send()">发送</button>
     <label class=muted><input type=checkbox id=dry checked> 演练（只留痕不真发）</label>
     <span id=snd class=muted></span></div>
 </div>
</div>
<script>
async function j(u,o){const r=await fetch(u,o);return await r.json()}
function esc(s){return (s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}
async function head(){const d=await j('/api/tentacle-mail/status');
 const s=d.状态||{}; const av=d.可用||[];
 document.getElementById('hd').innerHTML='<b>编队</b> '+s.boxes_total+' 个地址 · 模式 <b>'+d.模式+'</b> · 域名 '+
  (d.域名||'<span class=bad>未配</span>')+' · 发信 '+((av[0])?'<span class=ok>可用</span>':'<span class=warn>'+esc(av[1]||'未配')+'</span>');
 const rows=(s.boxes||[]).slice(0,100).map(b=>'<tr><td>'+esc(b.local)+'</td><td>'+esc(b.address||'<未配域名>')+'</td><td>'+esc(b.remark||b.role||'-')+'</td><td>'+esc(b.state)+'</td></tr>').join('');
 document.getElementById('fleet').innerHTML='<table><tr><th>格</th><th>地址</th><th>备注/职责</th><th>状态</th></tr>'+rows+'</table>';
}
async function load(){const b=document.getElementById('fbox').value, dr=document.getElementById('fdir').value;
 const d=await j('/api/tentacle-mail/messages?limit=80&box='+encodeURIComponent(b)+'&direction='+dr);
 const rows=(d.邮件||[]).map(m=>'<tr><td class=muted>'+esc((m.at||'').replace('T',' ').slice(0,19))+'</td><td>'+esc(m.direction)+'</td><td>'+esc(m.box)+'</td><td>'+esc(m.peer||'')+'</td><td>'+esc(m.subject||'')+'</td><td>'+(m.ok?'<span class=ok>成</span>':'<span class=bad>败</span>')+' '+esc(m.detail||'')+'</td></tr><tr><td colspan=6><pre>'+esc(m.body||'(无正文留痕)')+'</pre></td></tr>').join('');
 document.getElementById('msgs').innerHTML= d.条数? '<table><tr><th>时间</th><th>方向</th><th>格</th><th>对方</th><th>主题</th><th>结果</th></tr>'+rows+'</table>' : '<div class=muted>还没有邮件留痕（她还没发过/收过）</div>';
}
async function send(){const r=await j('/api/tentacle-mail/send',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({tentacle:+document.getElementById('tn').value, to:document.getElementById('to').value,
 subject:document.getElementById('subj').value, body:document.getElementById('body').value,
 dry_run:document.getElementById('dry').checked})});
 document.getElementById('snd').textContent=JSON.stringify(r).slice(0,220); load();}
head();load();
</script>"""
    return inject(html, "/tentacle-mail")


@router.get("/api/tentacle-mail/status")
async def tm_status() -> dict:
    f = _fleet()
    s = f.status()
    try:
        avail = list(f.mail_available())
    except Exception as e:  # noqa: BLE001
        avail = [False, "%s: %s" % (type(e).__name__, e)]
    return {"状态": s, "可用": avail, "模式": f.mode, "域名": f.domain or ""}


@router.get("/api/tentacle-mail/messages")
async def tm_messages(limit: int = 100, box: str = "", direction: str = "") -> dict:
    rows = _fleet().messages(limit=limit, box=box, direction=direction)
    return {"条数": len(rows), "邮件": rows}


@router.post("/api/tentacle-mail/send")
async def tm_send(payload: dict) -> dict:
    b = payload or {}
    f = _fleet()
    n = int(b.get("tentacle") or 1)
    args = (n, str(b.get("to") or ""), str(b.get("subject") or ""), str(b.get("body") or ""))
    try:
        return f.send(*args, dry_run=bool(b.get("dry_run", True)))
    except TypeError:
        return f.send(*args)
