# panel/tentacle_accounts_page.py —— 触手个人账户与密钥页（四服务位 + 独立钥匙，只回指纹）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人要求（2026-10-09）：「存放触手个人账户以及密钥的地方。」
# 纪律（本仓既定）：凭据**只报已配/未配与前若干位指纹**，绝不回显原文；无独立钥匙就如实标"没有"。
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from skills.ui_design import inject

router = APIRouter()
SERVICES = ("邮箱", "云插件", "数据库", "开源仓库")


def _rows(n: int = 100) -> dict:
    from core import tentacle_identity as TI
    out = {"服务位": list(SERVICES), "触手": [], "密钥总览": {}}
    try:
        allrows = TI.rows()
    except Exception:  # noqa: BLE001
        allrows = []
    by_t: dict = {}
    for r in allrows:
        by_t.setdefault(r.get("tentacle"), {})[r.get("service")] = r
    book = None
    try:
        from core.tentacle_keys import TentacleKeyBook, TentacleKeyView
        book = TentacleKeyBook()
    except Exception:  # noqa: BLE001
        book = None
    own = borrow = none = 0
    for i in range(1, n + 1):
        t = "t%03d" % i
        svc = {}
        for s in SERVICES:
            r = (by_t.get(t) or {}).get(s)
            svc[s] = {"状态": (r or {}).get("state", "待配"),
                      "标识": (r or {}).get("handle", "") or "",
                      "环境键": (r or {}).get("env_key", "") or ""}
        key = {"来源": "未配", "指纹": "", "独立": False}
        if book is not None:
            try:
                from core.tentacle_keys import TentacleKeyView as _V
                key = _V(t, book).as_row()
                key = {"来源": key.get("source"), "指纹": key.get("key_id") or "", "独立": bool(key.get("own_key"))}
            except Exception as e:  # noqa: BLE001
                key = {"来源": "读取失败: %s" % type(e).__name__, "指纹": "", "独立": False}
        if key.get("独立"):
            own += 1
        elif key.get("指纹"):
            borrow += 1
        else:
            none += 1
        out["触手"].append({"tentacle": t, "服务位": svc, "钥匙": key})
    out["密钥总览"] = {"有独立钥匙": own, "有钥匙但非独立": borrow, "没有钥匙": none, "共": n}
    return out


@router.get("/tentacle-accounts", response_class=HTMLResponse)
async def tentacle_accounts_page() -> HTMLResponse:
    html = """<!doctype html><meta charset=utf-8><title>触手账户与密钥</title>
<style>
 body{margin:0;background:#0b0e14;color:#e6edf3;font:14px/1.6 system-ui,"Microsoft YaHei"}
 .wrap{padding:18px 22px;max-width:1320px;margin:0 auto}
 h1{font-size:19px;margin:6px 0 4px} h3{font-size:15px;margin:16px 0 6px;color:#ffd479}
 .card{background:#0f131c;border:1px solid #1e2635;border-radius:12px;padding:14px 16px;margin:12px 0}
 table{width:100%;border-collapse:collapse;font-size:13px} th,td{border-bottom:1px solid #1b2230;padding:6px 8px;text-align:left}
 th{color:#8fb2d9;font-weight:600} .muted{color:#7d8da4;font-size:12px}
 .ok{color:#57d38c}.bad{color:#ef6b6b}.warn{color:#e9c46a} code{color:#a5d6ff}
 input{background:#0d1017;color:#e6edf3;border:1px solid #263041;border-radius:8px;padding:7px}
 .btn{background:#1f6feb;border:0;color:#fff;border-radius:8px;padding:8px 14px;cursor:pointer}
</style>
<div class=wrap>
 <h1>🔐 触手个人账户与密钥</h1>
 <div class=muted>四个服务位：邮箱 / 云插件 / 数据库 / 开源仓库。密钥**只回指纹**（不回显原文）；没有独立钥匙的如实标"没有"，不假装。</div>
 <div class=card id=ov>加载中…</div>
 <div class=card>
  <h3>逐根明细</h3>
  <div class=muted>过滤：<input id=q placeholder="t001 / 职业关键字" style="width:200px"><button class=btn onclick="load()">刷新</button></div>
  <div id=tbl class=muted style="margin-top:8px">加载中…</div>
 </div>
</div>
<script>
async function j(u){const r=await fetch(u);return await r.json()}
function esc(s){return (s||'').toString().replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}
function cell(v){const s=(v||'').toString(); if(s.indexOf('已配')===0||s.indexOf('绑定')>=0) return '<span class=ok>'+esc(s)+'</span>';
 if(s.indexOf('待配')>=0||s.indexOf('未')===0) return '<span class=bad>'+esc(s)+'</span>'; return '<span class=warn>'+esc(s)+'</span>'}
async function load(){const d=await j('/api/tentacle-accounts'); const k=d.密钥总览||{};
 document.getElementById('ov').innerHTML='<b>密钥总览</b> 有独立钥匙 <span class=ok>'+k.有独立钥匙+'</span> · 有钥匙非独立 <span class=warn>'+k.有钥匙但非独立+'</span> · 没有钥匙 <span class=bad>'+k.没有钥匙+'</span> / 共 '+k.共;
 const q=(document.getElementById('q').value||'').trim().toLowerCase();
 const rows=(d.触手||[]).filter(t=>!q||t.tentacle.indexOf(q)>=0).map(t=>{
   const s=t.服务位||{};
   return '<tr><td>'+esc(t.tentacle)+'</td>'+
    Object.keys(s).map(n=>'<td>'+cell(s[n].状态)+'<div class=muted>'+esc(s[n].标识||'-')+'</div></td>').join('')+
    '<td>'+esc(t.钥匙.来源||'-')+'<div class=muted>'+(t.钥匙.指纹?('指纹 '+esc(t.钥匙.指纹)):(t.钥匙.独立?'独立':'无'))+'</div></td></tr>'}).join('');
 document.getElementById('tbl').innerHTML='<table><tr><th>触手</th><th>邮箱</th><th>云插件</th><th>数据库</th><th>开源仓库</th><th>钥匙（指纹）</th></tr>'+rows+'</table>';
}
load();
</script>"""
    return inject(html, "/tentacle-accounts")


@router.get("/api/tentacle-accounts")
async def tentacle_accounts() -> dict:
    return _rows(100)


@router.get("/api/tentacle-accounts/{tentacle}")
async def tentacle_account_one(tentacle: str) -> dict:
    d = _rows(100)
    row = next((r for r in d["触手"] if r["tentacle"] == tentacle), None)
    return row or {"ok": False, "reason": "没有这根触手: %s" % tentacle}
