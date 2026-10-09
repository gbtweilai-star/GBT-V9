# panel/brain_page.py —— 原生大脑（捕捉 · 提问 · 生命起源存档 · 元认知 · 提醒 · 隐私）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：把主脑记忆全部统一；设计大脑生命起源存档并把触手记忆也归档分类；
#   以及主脑的元认知。
# 页面口径：所有数字来自 core.memory 的真读数；答不上来就显示"我不确定"，绝不编。
import asyncio as _a

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse

from core.memory import brain as B
from core.memory import consolidate as C
from core.memory import encoder as E
from core.memory import inform as I
from core.memory import life as L
from core.memory import metacog as M
from core.memory import store as S
from core.memory import worker as W
from skills.ui_design import Page

router = APIRouter()


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if v is not None else "")


def _fmt_ts(ts) -> str:
    try:
        import time as _t
        return _t.strftime("%Y-%m-%d %H:%M", _t.localtime(float(ts)))
    except (TypeError, ValueError):
        return "—"


def _kpis(st: dict) -> str:
    u = st["统一记忆"]
    cal = st["元认知"]
    return ('<div class=row>' + "".join(
        f'<div class=card style="min-width:140px;margin:0"><div class=muted>{k}</div>'
        f'<div class=kpi>{v}</div><div class=muted style="font-size:12px">{n}</div></div>'
        for k, v, n in (
            ("统一记忆", u.get("记忆"), f"含并入 {u.get('被并入的')} 条"),
            ("主体", len(st["主体"] or {}), "主脑 / 触手 / 用户 / 系统"),
            ("记忆域", len(st["记忆域"] or {}), "分类归档"),
            ("待编码", (st["编码"] or {}).get("待编码"), "理解还在后台跑"),
            ("向量通道", len(st.get("向量通道") or {}), str(st.get("向量通道") or "—")),
            ("用到率", cal.get("用到率"), f"未答率 {cal.get('未答率')}"),
            ("生平条目", (st["生命起源存档"] or {}).get("生平条数"), "生命起源存档"),
        )) + '</div>')


def _capture_box() -> str:
    return (
        '<div class=card><h2>📥 捕捉（写完就完了）</h2>'
        '<p class=muted>原文立即入库、立刻可检索；理解（分类/实体/向量）在后台慢慢做，'
        '所以这个框永远不让你等。</p>'
        '<div class=btnbar>'
        '<input id=btext placeholder="记一句…（回车即存）" style="flex:1;min-width:260px">'
        '<select id=bowner><option value="main">主脑</option>'
        '<option value="user">用户</option>'
        '<option value="t001">触手 t001</option></select>'
        '<label class=muted style="font-size:12px"><input type=checkbox id=bprivate '
        'style="width:auto"> 私密（绝不外发）</label>'
        '<button class="btn primary" onclick="bcap()">记住</button>'
        '<button class=btn onclick="bspeak()">用台湾腔念给我听</button>'
        '<span id=bmsg class=muted></span></div></div>')


def _ask_box() -> str:
    return (
        '<div class=card><h2>❓ 提问（只从存过的里答，带出处）</h2>'
        '<p class=muted>三路一起找：按措辞、按时间、按联想（"它会来找我"那种二跳）。'
        '答不上来会明说「我不确定」，不编。</p>'
        '<div class=btnbar><input id=bq placeholder="问一句…（回车）" style="flex:1;min-width:280px">'
        '<label class=muted style="font-size:12px"><input type=checkbox id=ball '
        'style="width:auto"> 含全部触手记忆（主脑全量视图）</label>'
        '<button class="btn primary" onclick="bask()">问</button></div>'
        '<div id=banswer style="margin-top:8px"></div></div>')


def _memories(st: dict) -> str:
    rows = S.store().list(all_owners=True, limit=40)
    body = "".join(
        f'<tr><td class=muted>{_esc(_fmt_ts(m.get("t_event")))}</td>'
        f'<td>{"主脑" if m.get("owner") == "main" else _esc(m.get("owner"))}</td>'
        f'<td>{_esc(m.get("category") or "未分类")}</td>'
        f'<td>{_esc(m.get("scope") or "—")}</td>'
        f'<td>{_esc((m.get("raw") or "")[:70])}</td>'
        f'<td class=muted>{m.get("heat")} <span style="font-size:11px">'
        f'({_esc(m.get("tier"))})</span></td>'
        f'<td>{m.get("hits") or 0}</td></tr>'
        for m in rows)
    return ('<div class=card><h2>🗂️ 统一记忆（最新 40 条）</h2>'
            f'<p class=muted>热度＝衰减×使用×重要度；褪色只让它更难被撞见，**从不删除**。'
            f'　分类分布 {_esc(st["分类"])}　记忆域 {_esc(st["记忆域"])}</p>'
            '<table><tr><th>时间</th><th>谁的</th><th>分类</th><th>记忆域</th><th>原文</th>'
            '<th>热度(层)</th><th>用到</th></tr>' + body + '</table></div>')


def _life(st: dict) -> str:
    lf = st["生命起源存档"] or {}
    born = lf.get("出生") or {}
    rows = "".join(
        f'<tr><td class=muted>{_esc(_fmt_ts(r.get("ts")))}</td>'
        f'<td>{_esc(r.get("category") or "—")}</td><td><b>{_esc(r.get("title"))}</b></td>'
        f'<td class=muted>{_esc((r.get("detail") or "")[:90])}</td></tr>'
        for r in (lf.get("编年") or [])[:20])
    miles = "".join(f'<li><b>{_esc(k)}</b>：{_esc((v or {}).get("原文") or (v or {}).get("说明") or "")}'
                    f' <span class=muted>{_esc(_fmt_ts((v or {}).get("时间")))}</span></li>'
                    for k, v in (lf.get("里程碑") or {}).items())
    return ('<div class=card><h2>🌱 大脑生命起源存档</h2>'
            + (f'<p class=muted>出生：<b>{_esc(born.get("标题"))}</b>（{_esc(_fmt_ts(born.get("时间")))}）'
               f'　第一句话：<i>{_esc(born.get("第一句话") or "—")}</i></p>'
               if born else '<p class=muted>还没有出生记录 → 点下面「写入出生记录」</p>')
            + f'<p class=muted>{_esc((lf or {}).get("口径") or "")}</p>'
            + (f'<h2>里程碑</h2><ul>{miles}</ul>' if miles else '')
            + '<h2>生平编年（分类归档）</h2>'
            + ('<table><tr><th>时间</th><th>分类</th><th>事件</th><th>说明</th></tr>' + rows
               + '</table>' if rows else '<p class=muted>还没有生平事件</p>')
            + '<div class=btnbar><button class=btn onclick="bborn()">写入出生记录</button>'
              '<button class=btn onclick="bonboard()">冷启动回填（按真实已发生的事）</button>'
              '<button class=btn onclick="bunify()">一键统一（导入触手/Obsidian/对话并分类）</button>'
              '<span id=lifemsg class=muted></span></div></div>')


def _metacog(st: dict) -> str:
    m = st["元认知"] or {}
    todo = "".join(f'<li>{_esc(x)}</li>' for x in (m.get("该做的") or []))
    return ('<div class=card><h2>🪞 元认知（对自己知道什么、不知道什么）</h2>'
            f'<p>{_esc(m.get("自我陈述") or "")}</p>'
            f'<p class=muted>用到率 {_esc(m.get("用到率"))} · 未答率 {_esc(m.get("未答率"))} · '
            f'空分类 {_esc(m.get("空分类"))}</p>'
            f'<h2>该做的</h2><ul>{todo}</ul></div>')


def _nudges(st: dict) -> str:
    n = st["通知"] or {}
    inbox = I.inbox()
    rows = "".join(
        f'<tr><td>{_esc(_fmt_ts(x.get("ts")))}</td><td>{_esc(x.get("kind"))}</td>'
        f'<td class=muted>{_esc(x.get("text"))}</td><td>{_esc(x.get("score"))}</td>'
        f'<td><button class="btn sm" onclick="bnudge(\'{_esc(x.get("nudge_id"))}\',\'done\')">处理了</button>'
        f'<button class="btn sm ghost" onclick="bnudge(\'{_esc(x.get("nudge_id"))}\',\'dismiss\')">'
        f'不需要</button></td></tr>'
        for x in (inbox.get("项") or [])[:12])
    return ('<div class=card><h2>🔔 提醒收件箱（轻推，不是垃圾）</h2>'
            f'<p class=muted>{_esc(n.get("口径") or "")}　静默时段 {_esc(n.get("静默时段"))}'
            f'　现在{"静默中" if n.get("现在静默") else "可打扰"}　待处理 {_esc(inbox.get("待处理"))}</p>'
            + ('<table><tr><th>时间</th><th>类</th><th>内容</th><th>分</th><th>表态</th></tr>'
               + rows + '</table>' if rows else '<p class=muted>暂无待处理提醒</p>')
            + '<div class=btnbar><button class=btn onclick="bemit()">跑一次打分（产提醒）</button>'
              '<span id=nmsg class=muted></span></div></div>')


def _privacy_footer(st: dict) -> str:
    bin_ = S.store().recycle_bin()
    rows = "".join(
        f'<tr><td class=muted>{_esc(_fmt_ts(x.get("ts")))}</td>'
        f'<td>{_esc((x.get("raw") or "")[:50])}</td>'
        f'<td class=muted>{_esc(_fmt_ts(x.get("purge_after")))}</td>'
        f'<td><button class="btn sm" onclick="brestore(\'{_esc(x.get("memory_id"))}\')">复原</button></td>'
        '</tr>' for x in bin_[:10])
    return ('<div class=card><h2>🔒 隐私与回收站</h2>'
            '<p class=muted>记忆按主体严格隔离（别人看不到别人的，主脑可见全量统一视图）；'
            '标了「私密」的记忆**绝不外发**（不走云向量、不出本机）；'
            '删除是**宽限期回收站**，不是立即粉碎，期间随时能复原。</p>'
            + ('<table><tr><th>删除时间</th><th>原文</th><th>宽限到</th><th>操作</th></tr>'
               + rows + '</table>' if rows else '<p class=muted>回收站是空的</p>')
            + '<div class=btnbar><button class=btn onclick="bforget()">清理宽限到期的</button>'
              '<button class=btn onclick="bconsolidate()">跑一次夜间整理（预览）</button>'
              '<button class=btn onclick="bencode()">把待编码的补完</button>'
              '<span id=pmsg class=muted></span></div></div>')


JS = """
async function post(p, body){ const r=await fetch(p,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); return r.json(); }
async function bcap(){
  const t=document.getElementById('btext'); if(!t.value.trim()) return;
  const d=await post('/api/brain/capture',{text:t.value,owner:document.getElementById('bowner').value,
    private:document.getElementById('bprivate').checked});
  document.getElementById('bmsg').innerHTML = d.ok
    ? '<span class=ok>已记住（'+d.id+'·'+d.scope+'）</span>' : ('<span class=bad>'+d.reason+'</span>');
  t.value=''; setTimeout(poll,600);
}
async function bspeak(){
  const t=document.getElementById('btext'); if(!t.value.trim()) return;
  const d=await post('/api/voice/say',{text:t.value,taiwan:true});
  document.getElementById('bmsg').innerHTML = d.ok
    ? ('<span class=ok>已念（'+d['通道']+'）</span>') : ('<span class=warn>没念成：'+(d.reason||'')+'</span>');
}
async function bask(){
  const q=document.getElementById('bq').value; if(!q.trim()) return;
  const d=await post('/api/brain/ask',{query:q,all_owners:document.getElementById('ball').checked});
  let h='<div class="'+(d.confident?'ok':'warn')+'" style="margin-bottom:6px">'
    +(d.confident?'找到了':'我不太确定')+'</div><pre style="white-space:pre-wrap">'+d.answer+'</pre>';
  if((d.sources||[]).length){
    h+='<table><tr><th>出处</th><th>谁</th><th>分类</th><th>热度</th><th>打分</th><th>命中</th></tr>'
      + d.sources.map(s=>'<tr><td class=muted>'+s.id+'</td><td>'+(s['谁']=='main'?'主脑':s['谁'])
      +'</td><td>'+(s['分类']||'—')+'</td><td>'+s['热度']+'</td><td>'+s['打分']
      +'</td><td class=muted>'+(s['命中路子']||[]).join('/')+'</td></tr>').join('')+'</table>';
  }
  document.getElementById('banswer').innerHTML=h;
}
async function bborn(){ const d=await post('/api/brain/life/born',{first_words:'我醒过来了，从今天开始记事。'});
  document.getElementById('lifemsg').textContent = d.already? '已有出生记录（不覆盖）':'已写入出生记录'; poll(); }
async function bonboard(){ const d=await post('/api/brain/life/onboard',{}); poll();
  document.getElementById('lifemsg').textContent='冷启动回填完成'; }
async function bunify(){ const d=await post('/api/brain/unify',{encode:true}); poll();
  document.getElementById('lifemsg').textContent='统一完成：'+JSON.stringify(d['统一后']||{}).slice(0,80); }
async function bnudge(id,action){ const d=await post('/api/brain/nudge',{nudge_id:id,action:action}); poll();
  document.getElementById('nmsg').textContent = d['退避']||''; }
async function bemit(){ const d=await post('/api/brain/nudges/emit',{}); poll();
  document.getElementById('nmsg').textContent='已推 '+(d['已推']||[]).length+' · 压着 '+(d['压着']||[]).length
    +' · 晨报 '+(d['晨报条数']||0); }
async function brestore(id){ await post('/api/brain/restore',{id:id}); poll(); }
async function bforget(){ const d=await post('/api/brain/forget',{}); poll();
  document.getElementById('pmsg').textContent='清理 '+d['清理']+' 条'; }
async function bconsolidate(){ const d=await post('/api/brain/consolidate',{dry_run:true});
  const m=(d['合并']||{}), k=(d['知识']||{});
  document.getElementById('pmsg').textContent='可合并 '+(m['可合并分组']||0)+' 组 · 可成知识 '
    +(k['可成知识的簇']||[]).length+' 簇（预览，未改动）'; }
async function bencode(){ const d=await post('/api/brain/encode',{}); poll();
  document.getElementById('pmsg').textContent='本次编码 '+d['本次编码']+' · 剩余 '+d['剩余待编码']; }
async function poll(){ const d=await (await fetch('/api/brain/status')).json();
  const box=document.getElementById('bkpi'); if(box && d.kpi) box.innerHTML=d.kpi;
  const l=document.getElementById('bmods'); if(l && d.modules) l.innerHTML=d.modules; }
document.addEventListener('keydown',e=>{ if(e.key!=='Enter') return;
  if(e.target.id==='btext') bcap(); if(e.target.id==='bq') bask(); });
"""


@router.get("/brain", response_class=HTMLResponse)
async def brain_page():
    st = await _a.to_thread(B.status)
    body = (_kpis(st) + _capture_box() + _ask_box() + _memories(st)
            + _life(st) + _metacog(st) + _nudges(st) + _privacy_footer(st))
    return Page(title="原生大脑 · 统一记忆 · 生命起源存档 · 元认知",
                body=f'<div id=bkpi></div><div id=bmods></div>' + body,
                current="/brain", extra_js=JS).render()


# ═══════════════ API ═══════════════
@router.get("/api/brain/status")
async def api_status():
    st = await _a.to_thread(B.status)
    return {**st, "kpi": _kpis(st)}


@router.post("/api/brain/capture")
async def api_capture(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(B.remember, str(p.get("text") or ""),
                              owner=str(p.get("owner") or "main"),
                              private=bool(p.get("private")))


@router.post("/api/brain/ask")
async def api_ask(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(B.ask, str(p.get("query") or ""),
                              owner=str(p.get("owner") or "main"),
                              all_owners=bool(p.get("all_owners")))


@router.get("/api/brain/memories")
async def api_memories(owner: str = "", category: str = "", scope: str = "",
                       all_owners: int = 1, limit: int = 50):
    return {"项": S.store().list(owner=owner or "main", category=category, scope=scope,
                                 all_owners=bool(all_owners), limit=limit)}


@router.get("/api/brain/heat")
async def api_heat(id: str = ""):
    return B.heat(id)


@router.get("/api/brain/life")
async def api_life(category: str = "", limit: int = 60):
    return await _a.to_thread(B.life, category=category, limit=limit)


@router.get("/api/brain/life/at")
async def api_life_at(ts: float = 0.0):
    import time as _t
    return await _a.to_thread(B.life_at, ts or _t.time())


@router.post("/api/brain/life/born")
async def api_born(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(B.born, first_words=str((payload or {}).get("first_words") or ""))


@router.post("/api/brain/life/onboard")
async def api_onboard():
    return await _a.to_thread(L.onboard)


@router.post("/api/brain/life/note")
async def api_life_note(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(L.note, str(p.get("kind") or "能力"), str(p.get("title") or ""),
                              detail=str(p.get("detail") or ""),
                              category=str(p.get("category") or ""))


@router.post("/api/brain/unify")
async def api_unify(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(B.unify, encode=bool((payload or {}).get("encode", True)))


@router.get("/api/brain/unify/status")
async def api_unify_status():
    from core.memory import unify as U
    return await _a.to_thread(U.status)


@router.get("/api/brain/metacog")
async def api_metacog():
    return await _a.to_thread(M.reflect)


@router.get("/api/brain/coverage")
async def api_coverage():
    return await _a.to_thread(M.coverage)


@router.get("/api/brain/calibration")
async def api_calibration():
    return await _a.to_thread(M.calibration)


@router.get("/api/brain/nudges")
async def api_nudges():
    return await _a.to_thread(I.inbox)


@router.post("/api/brain/nudges/emit")
async def api_nudges_emit():
    return await _a.to_thread(I.emit)


@router.post("/api/brain/nudge")
async def api_nudge(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(B.nudge_act, str(p.get("nudge_id") or ""),
                              action=str(p.get("action") or "done"))


@router.post("/api/brain/consolidate")
async def api_consolidate(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(B.consolidate, dry_run=bool(p.get("dry_run", True)),
                              purge=bool(p.get("purge")))


@router.post("/api/brain/encode")
async def api_encode():
    return await _a.to_thread(W.ensure_done)


@router.post("/api/brain/reembed")
async def api_reembed():
    """重算陈旧向量标签（口径变过，老行要按现在的口径重新落一次）。"""
    return await _a.to_thread(E.reembed)


@router.post("/api/brain/recycle")
async def api_recycle(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(B.recycle, str(p.get("id") or ""),
                              grace_days=float(p.get("grace_days") or 7.0),
                              reason=str(p.get("reason") or ""))


@router.post("/api/brain/restore")
async def api_restore(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(B.restore, str((payload or {}).get("id") or ""))


@router.get("/api/brain/bin")
async def api_bin():
    return {"项": S.store().recycle_bin()}


@router.post("/api/brain/forget")
async def api_forget():
    return await _a.to_thread(B.forget)


@router.get("/api/brain/modules")
async def api_modules():
    return {"编码": E.explain("示例：下周三要交季度报告，记得带上数据"),
            "热度": {"半衰期天": 4.0, "层": ["hot", "warm", "cold", "frozen"]},
            "整理": C.status(), "通知": I.status(), "口径": "全部真读数"}


__all__ = ["router"]
