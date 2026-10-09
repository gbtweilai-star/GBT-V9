# panel/chat_page.py —— APP 独立多功能对话面板（会话 · 多智能体 · 五种模式）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：补一个"APP 独立多功能对话面板"，可借用 Octop 的对话设计与形态，
#   但**品牌必须统一 GBT小土豆V9**。
# 五种模式（都走我们自己的真件，不假装）：
#   日常对话 → Octop 智能体名册里点名对话（core.agent_chat，V9 驱动链）
#   大脑记忆 → 原生大脑召回（core.memory.brain.ask，带出处、答不上来就说）
#   指挥读数 → 只读读数问答（/api/ai/ask 同一条链）
#   工作流   → 工作流状态/验收/推进（core.workflows，闸门未开会被拦）
#   工具     → 只读工具面（body.tools，按域问询）
import asyncio as _a

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse

from core import chat_sessions as CS
from skills.ui_design import Page

router = APIRouter()
BRAND = CS.BRAND


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if v is not None else "")


def _sess_html(rows: list, cur: str) -> str:
    out = []
    for r in rows:
        cls = "sess on" if r["id"] == cur else "sess"
        out.append(f'<div class="{cls}" onclick="pick(\'{_esc(r["id"])}\')">'
                   f'<div><b>{_esc(r["title"])}</b></div>'
                   f'<div class=muted style="font-size:11px">{_esc(r["mode"])} · '
                   f'{_esc(r.get("agent") or "默认智能体")} · {r.get("count") or 0} 条</div></div>')
    return "".join(out) or '<div class=muted>还没有会话 → 点「新会话」</div>'


def _page(rows: list, cur: str, mode: str) -> str:
    modes = "".join(
        f'<button class="{"on" if m == mode else ""}" onclick="setmode(\'{_esc(m)}\')">{_esc(m)}</button>'
        for m in CS.MODES)
    return (
        '<div class=card><h2>💬 ' + BRAND + ' · 独立多功能对话</h2>'
        '<p class=muted>会话留得住（追加式落盘）、智能体可点名（Octop 名册）、'
        '五种模式各走各的真件。品牌与归属统一为 <b>' + BRAND + '</b>。</p>'
        '<div class=btnbar><button class="btn primary" onclick="newSess()">新会话</button>'
        '<div class=seg id=modeseg>' + modes + '</div>'
        '<input id=agent placeholder="点名智能体（留空＝默认）" style="min-width:200px">'
        '<button class=btn onclick="renameSess()">重命名</button>'
        '<button class="btn ghost" onclick="dropSess()">移除</button>'
        '<span id=cmsg class=muted></span></div></div>'
        '<div class=chatwrap style="margin-top:12px">'
        '<div class="card sesslist" style="margin:0"><b>会话</b>'
        f'<div style="margin-top:8px">{_sess_html(rows, cur)}</div></div>'
        '<div class=card style="margin:0"><div class=msgs id=msgs></div>'
        '<div class=termline><span class=p>&gt;</span>'
        '<input id=cin placeholder="说点什么…（回车发送）">'
        '<button class="btn primary" onclick="send()">发送</button></div>'
        '<div class=muted style="font-size:12px;margin-top:6px" id=tip></div></div></div>')


JS = """
let CUR=null, MODE='日常对话';
async function boot(){
  const d=await (await fetch('/api/chat/sessions')).json();
  if(!d.rows.length){ const n=await post('/api/chat/new',{mode:MODE}); CUR=n.session.id; }
  else CUR=d.current||d.rows[0].id;
  MODE=(d.rows.find(r=>r.id===CUR)||{}).mode||'日常对话';
  await loadMsgs();
}
async function pick(id){ CUR=id; const d=await (await fetch('/api/chat/sessions')).json();
  MODE=(d.rows.find(r=>r.id===id)||{}).mode||MODE; await loadMsgs(); }
async function newSess(){ const d=await post('/api/chat/new',{mode:MODE,agent:val('agent')});
  CUR=d.session.id; await reload(); }
async function renameSess(){ const t=prompt('新标题'); if(!t) return;
  await post('/api/chat/rename',{id:CUR,title:t}); await reload(); }
async function dropSess(){ if(!confirm('移除这个会话？（文件改名留底，不是立即删除）')) return;
  await post('/api/chat/drop',{id:CUR}); CUR=null; await boot(); }
async function setmode(m){ MODE=m; await post('/api/chat/mode',{id:CUR,mode:m,agent:val('agent')});
  document.querySelectorAll('#modeseg button').forEach(b=>b.classList.toggle('on',b.textContent===m));
  document.getElementById('tip').textContent='已切到「'+m+'」'; }
function val(i){ const e=document.getElementById(i); return e? e.value.trim():''; }
function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
async function loadMsgs(){
  const d=await (await fetch('/api/chat/history?id='+encodeURIComponent(CUR))).json();
  const box=document.getElementById('msgs'); box.innerHTML='';
  (d.rows||[]).forEach(m=>add(m.role,m.text,m.meta));
  box.scrollTop=box.scrollHeight;
}
function add(role,text,meta){
  const box=document.getElementById('msgs');
  const who=role==='user'?'你':(role==='assistant'?'她('+(meta&&meta.agent||'主脑')+')':role);
  box.insertAdjacentHTML('beforeend','<div class=role>'+esc(who)+'</div>'
    +'<div class="bubble'+(role==='user'?' me':'')+'" style="white-space:pre-wrap">'+esc(text)+'</div>');
  box.scrollTop=box.scrollHeight;
}
async function send(){
  const t=val('cin'); if(!t||!CUR) return;
  document.getElementById('cin').value=''; add('user',t);
  const d=await post('/api/chat/send',{id:CUR,text:t,mode:MODE,agent:val('agent')});
  if(d.ok){ add('assistant',d.reply,d.meta||{}); }
  else { add('assistant','（没成：'+(d.reason||'')+'）'); }
  document.getElementById('tip').innerHTML = d.hint? ('<span class=warn>'+esc(d.hint)+'</span>') : '';
  reload();
}
async function reload(){ const d=await (await fetch('/api/chat/sessions')).json();
  const box=document.querySelector('.sesslist'); if(box) box.innerHTML='<b>会话</b><div style="margin-top:8px">'
    + d.rows.map(r=>'<div class="sess'+(r.id===CUR?' on':'')+'" onclick="pick(\\''+r.id+'\\')"><div><b>'
      +esc(r.title)+'</b></div><div class=muted style="font-size:11px">'+esc(r.mode)+' · '
      +esc(r.agent||'默认智能体')+' · '+(r.count||0)+' 条</div></div>').join('')+'</div>'; }
async function post(p,b){ const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(b||{})}); return r.json(); }
document.addEventListener('keydown',e=>{ if(e.key==='Enter'&&e.target&&e.target.id==='cin') send(); });
boot();
"""


def _reply(sid: str, text: str, *, mode: str, agent: str, owner: str = "main") -> dict:
    """按模式取一条真答复（同步实现，由接口丢线程调用）。"""
    m = mode or "日常对话"
    if m == "大脑记忆":
        from core.memory import brain as B
        r = B.ask(text, owner=owner, all_owners=True)
        return {"reply": r.get("answer") or "", "meta": {"agent": "原生大脑", "确定": r.get("confident"),
                "出处数": len(r.get("sources") or []), "路子": r.get("strategies")},
                "hint": r.get("unknown_reason") or ""}
    if m == "指挥读数":
        from core import action_loop as AL
        try:
            r = AL.handle(text, dry_run=True) if "dry_run" in AL.handle.__code__.co_varnames \
                else AL.handle(text)
        except Exception:                                     # noqa: BLE001
            r = {}
        say = (r or {}).get("say") or (r or {}).get("reply") or "（指挥链没给出可读的话）"
        return {"reply": say, "meta": {"agent": "AI 指挥中心"}, "hint": ""}
    if m == "工作流":
        from core import workflows as W
        st = W.status()
        low = text.lower()
        wf = next((r for r in st["清单"]
                   if r["id"] in low or r["名称"] in text), None)
        if wf:
            g = wf["调研闸门"]
            return {"reply": (f"{wf['名称']}：{wf['阶段数']} 段，"
                              f"调研闸门{'已放行' if g['allowed'] else '未开'}（{g.get('reason') or ''}）；"
                              f"验收 {wf['验收']['已通过']}/{wf['验收']['标准数']} 已通过、"
                              f"{wf['验收']['待采证']} 待采证。"),
                    "meta": {"agent": "工作流"}, "hint": "" if g["allowed"] else "闸门未开时生产段会被拒"}
        return {"reply": "可用工作流：" + "、".join(r["名称"] for r in st["清单"]),
                "meta": {"agent": "工作流"}, "hint": "直接说工作流名字（如「短视频」）看它的状态"}
    if m == "工具":
        from body.tools.base import TOOLS, call_tool
        led = None
        try:
            from panel.server import get_ledger
            led = get_ledger()
        except Exception:                                     # noqa: BLE001
            led = None
        name = next((n for n in TOOLS if n in text), "")
        if not name:
            return {"reply": "只读工具：" + "、".join(sorted(TOOLS)),
                    "meta": {"agent": "工具面"}, "hint": "说工具名（如 media.queue）取它的读数"}
        if led is None:
            # 没有账本也要给真答复（面板不该因此哑掉）——如实说清缺什么
            return {"reply": f"工具 {name} 需要账本连接才能取数；当前账本未就绪。"
                             f"可用工具：{'、'.join(sorted(TOOLS))}",
                    "meta": {"agent": f"工具 {name}"},
                    "hint": "账本就绪后再问一次；这条不是失败，是缺少读数来源"}
        try:
            out = call_tool(led, name, "chat", {}) or {}
        except Exception as exc:                              # noqa: BLE001
            return {"reply": f"工具 {name} 取数失败：{type(exc).__name__}",
                    "meta": {"agent": f"工具 {name}"}, "hint": "看账本/快照是否就绪"}
        return {"reply": str(out.get("sentence") or out)[:800], "meta": {"agent": f"工具 {name}"},
                "hint": "只读读数；拿不到会明说"}
    # 日常对话：点名 Octop 智能体，走 V9 驱动链
    from core import agent_chat as AC
    r = AC.ask(text, agent_key=agent or "")
    return {"reply": r.get("reply") or r.get("answer") or "（没拿到回复）",
            "meta": {"agent": r.get("agent") or agent or "默认智能体",
                     "通道": r.get("channel") or ""},
            "hint": "" if r.get("ok") else (r.get("reason") or "")}


@router.get("/chat", response_class=HTMLResponse)
async def chat_page():
    rows = await _a.to_thread(CS.sessions)
    cur = rows[0]["id"] if rows else ""
    mode = (rows[0]["mode"] if rows else CS.MODES[0])
    return Page(title=f"{BRAND} · 独立多功能对话", body=_page(rows, cur, mode),
                current="/chat", extra_js=JS).render()


# ═══════════════ API ═══════════════
@router.get("/api/chat/sessions")
async def api_sessions():
    rows = await _a.to_thread(CS.sessions)
    return {"ok": True, "品牌": BRAND, "模式可选": list(CS.MODES), "rows": rows,
            "current": rows[0]["id"] if rows else ""}


@router.post("/api/chat/new")
async def api_new(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    r = await _a.to_thread(CS.new, title=str(p.get("title") or ""),
                           mode=str(p.get("mode") or CS.MODES[0]),
                           agent=str(p.get("agent") or ""))
    return {**r, "品牌": BRAND}


@router.get("/api/chat/history")
async def api_history(id: str = "", limit: int = 200):
    return {"ok": True, "id": id, "rows": await _a.to_thread(CS.history, id, limit=limit)}


@router.post("/api/chat/send")
async def api_send(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    sid = str(p.get("id") or "")
    text = str(p.get("text") or "").strip()
    if not sid or not text:
        return {"ok": False, "reason": "缺会话 id 或内容"}
    await _a.to_thread(CS.append, sid, "user", text, meta={"mode": p.get("mode")})
    try:
        got = await _a.to_thread(_reply, sid, text, mode=str(p.get("mode") or ""),
                                 agent=str(p.get("agent") or ""))
        r = {"ok": True, **got}
    except Exception as exc:                                   # noqa: BLE001
        r = {"ok": False, "reply": f"（调用失败：{type(exc).__name__}）",
             "reason": f"{type(exc).__name__}: {exc}"}
    await _a.to_thread(CS.append, sid, "assistant", r.get("reply") or "", meta=r.get("meta"))
    return r


@router.post("/api/chat/rename")
async def api_rename(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(CS.rename, str(p.get("id") or ""), str(p.get("title") or ""))


@router.post("/api/chat/mode")
async def api_mode(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(CS.set_mode, str(p.get("id") or ""),
                              mode=str(p.get("mode") or ""), agent=str(p.get("agent") or ""))


@router.post("/api/chat/drop")
async def api_drop(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(CS.drop, str((payload or {}).get("id") or ""))


@router.get("/api/chat/status")
async def api_status():
    return await _a.to_thread(CS.status)


__all__ = ["router"]
