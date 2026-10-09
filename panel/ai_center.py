# panel/ai_center.py —— AI 指挥中心（/command）+ 全站 AI 停靠坞（/api/ai/ask）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 分工：本模块只做"装配与渲染"，审美与组件全走 skills/ui_design（设计令牌），
#      口语→术语全走 skills/terminology（她听得懂人话，也教主人怎么说）。
# 纪律：面板只读；问询走只读工具（不写库）；答不上来就说答不上来，不给假答案。
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from common.db import get_db, fetch_all

router = APIRouter()          # 自带 /api 与 /command 前缀（下面各自标注）


class Ask(BaseModel):
    text: str = ""
    say: bool = False


# ═══════════ 口语 → 术语 → 只读读数（或给"怎么说"的建议）═══════════
@router.post("/api/ai/ask")
async def ai_ask(body: Ask, db=Depends(get_db)):
    """一句话进来：能听懂就转术语并（只读地）取数；听不懂就教主人怎么说。"""
    from skills.terminology import suggest, translate
    t = translate(body.text)
    out: dict = {"raw": body.text, "translation": t}
    if not t.get("matched"):
        out.update({"understood": False,
                    "say": "我没听懂这句。你可以这样说：",
                    "suggestions": suggest(body.text, limit=3)})
        return out
    out.update({"understood": True, "term": t["term"], "skill": t["skill"],
                "intent": t["intent"], "say_back": t.get("say_back")})
    # 只读域：直接给出读数（与卡片、数字人同源：body_read_snapshots）
    if t["intent"] == "read_snapshot":
        from body.tools.view import read_all
        try:
            views = await read_all(db)
            facts = {d: {"revision": v["revision"], "stale": v["stale"],
                         "sentence": v["safe_sentence"]} for d, v in views.items()}
            out["facts"] = facts
            out["say"] = "；".join(v["safe_sentence"] for v in views.values())
        except Exception as exc:                              # noqa: BLE001
            out["say"] = f"读数取不到（{type(exc).__name__}）——这次不编数"
    elif t["skill"] == "obsidian":
        from body.obsidian import ObsidianVault, TentacleMemory, pick_vault
        try:
            tm = TentacleMemory(ObsidianVault(pick_vault()["root"]))
            if t["intent"] == "vault_recall":
                hits = tm.search_all(body.text, limit=5)
                out["say"] = (f"永久记忆里找到 {len(hits)} 条相关记录"
                              if hits else "永久记忆里没有相关记录")
                out["facts"] = {"hits": hits, "notes": tm.read_all()["notes"]}
            else:
                out["say"] = (f"永久记忆共 {tm.read_all()['notes']} 篇触手笔记；"
                              "要记什么请直接说内容")
        except Exception as exc:                              # noqa: BLE001
            out["say"] = f"Obsidian 记忆暂时读不到（{type(exc).__name__}）"
    elif t["skill"] == "biz":
        from core.biz_ops import OrderBook
        try:
            from panel.deps import db as body_db
            st = OrderBook(ledger=body_db).status()
            out["facts"] = st
            out["say"] = (f"在谈 {st.get('in_talk_amount', 0):.0f} 元 · "
                          f"在手 {st.get('in_hand_amount', 0):.0f} 元 · "
                          f"已回款 {st.get('collected', 0):.0f} 元 · "
                          f"逾期 {st.get('overdue_amount', 0):.0f} 元")
        except Exception as exc:                              # noqa: BLE001
            out["say"] = f"业务账本读不到（{type(exc).__name__}）"
    else:
        out["say"] = (f"我听懂是「{t['term']}」，由 {t['skill']} 负责实现。"
                      f"要不要现在做？（{'、'.join(t.get('params', {}).keys()) or '无参数'}）")
    return out


# ═══════════ 指挥中心页面（审美与组件走 skills/ui_design）═══════════
def _command_page() -> str:
    from core.cognition_system import GROUPS, MODULES
    from skills.ui_design import Page

    groups_html = []
    for g, meta in GROUPS.items():
        ms = [m for m in MODULES if m.group == g]
        chips = "".join(
            f'<span class=badge title="{m.duty}">{m.id}'
            + ('<span class=muted>·planned</span>' if m.planned else "")
            + "</span> " for m in ms)
        groups_html.append(f'<div class="card"><b>{meta["cn"]}</b> '
                           f'<span class=muted>{meta["job"]}（{len(ms)} 项）</span>'
                           f'<div style="margin-top:8px">{chips}</div></div>')
    body = f"""
<div class=row>
  <div class="card" style="flex:2;min-width:420px">
    <b>对话操控台</b> <span class=muted>· 说人话就行；听不懂她会教你怎么说</span>
    <div class=row style="margin-top:8px">
      <input id=box placeholder="例如：鼠标放上去那张大一点 / 现在什么情况 / 报价"
             style="flex:1;background:var(--bg);border:1px solid var(--border);
                    color:var(--text);border-radius:var(--r-sm);padding:8px 10px;font:inherit">
      <button class="btn primary" onclick="ask2()">发送</button>
      <button class=btn onclick="quick('read_snapshot')">报读数</button>
      <button class=btn onclick="quick('vault_recall')">翻记忆</button>
    </div>
    <pre id=aiout class=muted style="white-space:pre-wrap;margin-top:12px;
         background:var(--surface_2);border-radius:var(--r-sm);padding:10px">
（她只转述读数：stale 会说无法确认，拿不到就说拿不到）</pre>
  </div>
  <div class="card" style="flex:1;min-width:280px">
    <b>她的状态</b>
    <div id=brainstat class=muted>加载中…</div>
  </div>
  <div class="card" style="flex:1;min-width:280px">
    <b>元认知（她怎么看自己）</b>
    <div id=metacog class=muted>加载中…</div>
  </div>
</div>

<h2>触手矩阵（100 根 · 统一密钥 · 永久记忆）</h2>
<div class=card id=fleetbox class=muted>加载中…</div>

<h2>能力全景（{len(MODULES)} 个认知模块 · {len(GROUPS)} 组）</h2>
{"".join(groups_html)}
"""
    js = """
async function askText(q){
  var out=document.getElementById('aiout'); out.textContent='…';
  try{
    var r=await fetch('/api/ai/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:q})});
    var d=await r.json();
    out.textContent = (d.say ? d.say + '\\n\\n' : '') + JSON.stringify(
      d.understood===false ? {没听懂:d.say,suggestions:d.suggestions} :
      {我听懂的是:d.term, 由谁实现:d.skill, 参数:d.translation&&d.translation.params,
       facts:d.facts}, null, 1);
    if(d.say) speak(d.say);
  }catch(e){ out.textContent='问询失败：'+e.message; }
}
function ask2(){ var q=document.getElementById('box').value.trim(); if(q) askText(q); }
function quick(k){
  var m={read_snapshot:'现在什么情况', vault_recall:'翻一下以前关于扫描的记录'};
  document.getElementById('box').value=m[k]||''; askText(m[k]||'');
}
function speak(t){ try{ var u=new SpeechSynthesisUtterance(t); u.lang='zh-TW';
  u.rate=0.9; u.pitch=1.05; speechSynthesis.speak(u); }catch(e){} }
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&document.activeElement.id==='box')ask2();});
async function loadFleet(){
  var el=document.getElementById('fleetbox');
  try{
    var f=await (await fetch('/api/fleet/status')).json(); var c=f.config||{}, r=f.drives||{};
    var mem=await (await fetch('/api/ai/ask',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:'翻一下以前关于扫描的记录'})})).json();
    el.innerHTML='<div class=row>'+
      '<div class=card><div class=muted>编队</div><b>'+((c.n>=100)?'<span class=ok>':('<span class=warn>'))
        +(c.n||0)+' 根</span></b><div class=muted>指挥官 '+(c.commander||'-')+'</div></div>'+
      '<div class=card><div class=muted>统一密钥指纹</div><b>'+(c.key_id||'未配置')+'</b>'+
        '<div class=muted class="'+(c.same_key?'ok':'bad')+'">'+
        (c.same_key?'全编队同一把':'<span class=bad>不统一</span>')+'</div></div>'+
      '<div class=card><div class=muted>驱动审计</div><b>'+(r.drives||0)+' 次</b>'+
        '<div class=muted>用过 '+(r.tentacles_used||0)+' 根</div></div>'+
      '<div class=card><div class=muted>永久记忆（Obsidian）</div><b>'+
        ((mem.facts&&mem.facts.notes)||0)+' 篇</b><div class=muted>'+(mem.say||'')+'</div></div></div>';
  }catch(e){ el.textContent='编队读数取不到：'+e.message; }
}
async function loadStat(){
  var el=document.getElementById('brainstat');
  try{
    var e2=await (await fetch('/api/voice/emotion')).json();
    el.innerHTML='<b>情绪</b> '+(e2.mood||'-')+'（强度 '+((e2.intensity||0).toFixed(2))+
      '）<br><span class=muted>口音 '+(e2.style||'-')+'</span><br>'+
      '<b>与主人</b> '+(e2.rapport&&e2.rapport[0]?
        ('熟悉 '+e2.rapport[0].familiarity+' · 信任 '+e2.rapport[0].trust):'-')+
      '<br><div class=muted style="margin-top:8px">最近一句：<br>'+
      ((e2.recent_lines&&e2.recent_lines[0]&&e2.recent_lines[0].text)||'（还没说话）')+'</div>';
  }catch(e){ el.textContent='状态取不到'; }
}
async function loadMeta(){
  var el=document.getElementById('metacog'); if(!el) return;
  var pct=v=>(v==null?'—':Math.round(v*100)+'%');
  try{
    var m=await (await fetch('/api/brain/metacog')).json();
    var c=m['校准']||{}, n=m['计数']||{};
    el.innerHTML='<div class=muted>'+(m['自我陈述']||'（还没有可陈述的）')+'</div>'+
      '<div class=kv><span>记忆</span><b>'+(c['记忆数']??'—')+'</b></div>'+
      '<div class=kv><span>记忆被用到</span><span>'+pct(c['用到率'])+'</span></div>'+
      '<div class=kv><span>答不上来</span><span>'+pct(c['未答率'])+'</span></div>'+
      '<div class=kv><span>问过</span><span>'+(c['问过']??'—')+'</span></div>'+
      '<div class=muted style="margin-top:6px">该做的：'+((m['该做的']||[])[0]||'—')+'</div>';
  }catch(e){ el.textContent='元认知取不到：'+e.message; }
}
loadFleet(); loadStat(); loadMeta();
setInterval(loadStat, 15000);
setInterval(loadMeta, 60000);   // reflect 是现算重活，低频拉，别跟着 15s 轮
"""
    return Page(title="AI 指挥中心 · GBT小土豆V9", body=body, current="/command",
                extra_js=js, dock=False).render()


@router.get("/api/terms")
async def terms_catalog():
    """术语目录（口语 → 术语 → 实现）：指挥中心页按键绑定用的就是这个接口。"""
    try:
        from skills.terminology import catalog
        got = catalog() or []
        return {"ok": True, "count": len(got), "terms": got}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "count": 0, "terms": [], "reason": type(exc).__name__}


@router.get("/api/frames/count")
async def frames_count():
    """5 个专用读帧插件各报各的帧数（不跨源求和；读不到就说读不到）。"""
    from senses.frame_readers import catalog, count_all
    led = None
    try:
        led = get_ledger_safe()
    except Exception:                                          # noqa: BLE001
        led = None
    return {"plugins": catalog(), "result": count_all(scan_ledger=led)}


def get_ledger_safe():
    """队列读帧插件需要一个账本句柄；拿不到就让它如实报"没有句柄"。"""
    try:
        import panel.server as S
        return S.get_ledger()
    except Exception:                                          # noqa: BLE001
        return None


@router.get("/command", response_class=HTMLResponse)
async def command_page() -> str:
    return _command_page()
