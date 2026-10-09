# panel/hub_page.py —— 总控台数据中枢 /hub（唯一数据源 · 自主层 · 镜像 · 信息素 · 调度）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-07）：把蒸馏出来的自主能力**融进 V9 本体**：
#   主动汇报（四类/四维打分/不打扰门）· 心跳调度 · 长任务（租约/检查点/续作）·
#   镜像排练（落地过闸门）· 信息素（只追加）· 总线（页面/能力图/部件读数/布局/盲区）。
# 纪律：每格都是真读数；取不到写原因，不编数字；布局拖拽结果落盘。
import asyncio as _a
import json

from fastapi import APIRouter, Body, HTTPException
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
from fastapi.responses import HTMLResponse

from core import console_hub as HUB
from core import longrun as LR
from core import mirror as MR
from core import proactive as PA
from core import sched as SCH
from core import stigmergy as SG
from skills.ui_design import Page

router = APIRouter()


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if v is not None else "")


# ───────────────────────── 接口 ─────────────────────────
@router.post("/api/hub/deploy")
async def api_hub_deploy():
    """总装自主层：补齐默认心跳 + 推进一次 + 固化登记（走防偷懒钩子）。"""
    return await _a.to_thread(HUB.deploy)


@router.get("/api/hub/snapshot")
async def api_hub_snapshot():
    """总控台唯一数据源：页面 / 能力图 / 部件读数 / 布局 / 盲区，一次给全。"""
    return await _a.to_thread(HUB.snapshot)


@router.get("/api/hub/pages")
async def api_hub_pages():
    return await _a.to_thread(HUB.pages)


@router.get("/api/hub/widgets")
async def api_hub_widgets():
    return await _a.to_thread(HUB.widgets)


@router.get("/api/hub/scan")
async def api_hub_scan():
    return await _a.to_thread(HUB.scan)


@router.get("/api/hub/layout")
async def api_hub_layout():
    return await _a.to_thread(HUB.layout)


@router.post("/api/hub/layout")
async def api_hub_layout_save(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(HUB.save_layout, payload or {})


@router.get("/api/sched/status")
async def api_sched_status():
    return await _a.to_thread(SCH.status)


@router.post("/api/sched/ensure")
async def api_sched_ensure():
    return await _a.to_thread(SCH.ensure_defaults)


@router.post("/api/sched/tick")
async def api_sched_tick(limit: int = 5):
    return await _a.to_thread(SCH.tick, limit=max(1, min(20, limit)))


@router.post("/api/sched/define")
async def api_sched_define(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(SCH.define, str(p.get("name") or ""),
                              float(p.get("every_s") or 0),
                              handler=str(p.get("handler") or ""),
                              note=str(p.get("note") or ""))


@router.get("/api/proactive/feed")
async def api_proactive_feed(limit: int = 20, only_said: int = 1):
    return {"记录": await _a.to_thread(PA.feed, limit=max(1, min(100, limit)),
                                       only_said=bool(only_said))}


@router.post("/api/proactive/consider")
async def api_proactive_consider(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(PA.consider, str(p.get("说") or ""),
                              kind=str(p.get("kind") or "新奇发现"),
                              相关=float(p.get("相关") or 0.6), 紧急=float(p.get("紧急") or 0.5),
                              可信=float(p.get("可信") or 0.8), 价值=float(p.get("价值") or 0.6),
                              key=str(p.get("key") or ""), evidence=str(p.get("evidence") or ""))


@router.post("/api/proactive/scan")
async def api_proactive_scan():
    return await _a.to_thread(PA.scan_once)


@router.get("/api/longrun/status")
async def api_longrun_status(task: str = ""):
    return await _a.to_thread(LR.status, task)


@router.post("/api/longrun/submit")
async def api_longrun_submit(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(LR.submit, str(p.get("目标") or ""),
                              steps=tuple(p.get("steps") or []),
                              owner=str(p.get("owner") or "main"))


@router.post("/api/longrun/checkpoint")
async def api_longrun_ckpt(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(LR.checkpoint, str(p.get("任务") or ""), str(p.get("步") or ""),
                              evidence=str(p.get("evidence") or ""), note=str(p.get("note") or ""))


@router.post("/api/longrun/resume")
async def api_longrun_resume(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(LR.resume, str((payload or {}).get("任务") or ""))


@router.post("/api/longrun/heartbeat")
async def api_longrun_hb(payload: dict = Body(default_factory=dict)):
    return await _a.to_thread(LR.heartbeat, str((payload or {}).get("任务") or ""))


@router.post("/api/longrun/escalate")
async def api_longrun_esc(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(LR.escalate, str(p.get("任务") or ""), str(p.get("原因") or ""))


@router.get("/api/mirror/status")
async def api_mirror_status(space: str = ""):
    return await _a.to_thread(MR.status, space)


@router.post("/api/mirror/open")
async def api_mirror_open(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(MR.open_space, str(p.get("目标") or ""),
                              约束=tuple(p.get("约束") or []))


@router.post("/api/mirror/step")
async def api_mirror_step(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    act = str(p.get("op") or "step")
    if act == "propose":
        return await _a.to_thread(MR.propose, str(p.get("空间") or ""), str(p.get("假设") or ""),
                                  依据=str(p.get("依据") or ""), 信心=float(p.get("信心") or 0.5))
    if act == "digest":
        return await _a.to_thread(MR.digest, str(p.get("空间") or ""))
    if act == "promote":
        return await _a.to_thread(MR.promote, str(p.get("空间") or ""),
                                  evidence=str(p.get("evidence") or ""))
    return await _a.to_thread(MR.step, str(p.get("空间") or ""), str(p.get("推演") or ""),
                              代价=str(p.get("代价") or ""), 风险=str(p.get("风险") or ""))


@router.get("/api/tripo/anim")
async def api_tripo_anim(name: str = ""):
    """数字人的**动作视频**（骨骼驱动的连续动画，无缝循环）。只放行 render 目录下的白名单文件名。"""
    from fastapi.responses import FileResponse
    import re as _re
    d = (_ROOT / "state" / "tripo" / "render").resolve()
    n = str(name or "")
    if not _re.fullmatch(r"[A-Za-z0-9_]+(_poster)?\.(webm|mp4|png)", n):
        raise HTTPException(status_code=404, detail="没有这段动作")
    p = (d / n).resolve()
    try:
        p.relative_to(d)
    except ValueError:
        raise HTTPException(status_code=404, detail="没有这段动作")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="没有这段动作")
    media = ("video/webm" if n.endswith(".webm")
             else ("video/mp4" if n.endswith(".mp4") else "image/png"))
    return FileResponse(str(p), media_type=media)


@router.get("/api/tripo/anims")
async def api_tripo_anims():
    """列出已有的动作视频（页面用它选播哪一段）。"""
    import glob as _glob
    d = _ROOT / "state" / "tripo" / "render"
    best: dict = {}
    for f in sorted(_glob.glob(str(d / "*.mp4"))) + sorted(_glob.glob(str(d / "*.webm"))):
        p = Path(f)
        if p.stat().st_size < 20000:                 # 空/失败的产物不算
            continue
        cur = best.get(p.stem)
        # 同一动作有多份时优先 webm（VP9 免版税，Chromium 通用；H.264 在开源构建里没有）
        if cur is None or (p.suffix == ".webm" and cur.suffix != ".webm"):
            best[p.stem] = p
    out = []
    for name, p in best.items():
        poster = p.with_name(p.stem + "_poster.png")
        out.append({"名": name, "文件": p.name, "大小KB": round(p.stat().st_size / 1024),
                    "海报": poster.name if poster.is_file() else ""})
    out.sort(key=lambda x: x["名"])
    return {"动作": out, "口径": "骨骼驱动的连续动作视频（首尾同相无缝循环），不是静态图轮播"}


@router.get("/api/stigmergy/status")
async def api_stig_status(kind: str = "", limit: int = 30):
    st = await _a.to_thread(SG.status)
    return {**st, "最近": await _a.to_thread(SG.read, kind=kind, limit=max(1, min(200, limit)))}


@router.post("/api/stigmergy/deposit")
async def api_stig_deposit(payload: dict = Body(default_factory=dict)):
    p = payload or {}
    return await _a.to_thread(SG.deposit, str(p.get("kind") or "痕迹"),
                              p.get("payload") or {}, tags=tuple(p.get("tags") or []),
                              strength=float(p.get("strength") or 1.0),
                              by=str(p.get("by") or "main"), key=str(p.get("key") or ""),
                              evidence=str(p.get("evidence") or ""))


# ───────────────────────── 页面 ─────────────────────────
JS = """
async function hubLoad(){
  const q=await (await fetch('/api/hub/snapshot')).json();
  const w=q.widgets||{}, wb=w.部件||{};
  document.getElementById('widg').innerHTML = Object.keys(wb).map(function(k){
    const it=wb[k]||{}, r=it.读数;
    const ok = r && (r.可用===undefined ? true : r.可用);
    const txt = r ? Object.keys(r).filter(function(x){return x!=='来源';})
        .map(function(x){return x+' '+JSON.stringify(r[x]);}).join(' · ') : (it.原因||'取不到');
    return '<div class=card><div class=muted>'+k+'</div>'+
           '<div class="'+(ok?'ok':'warn')+'" style="font-size:12px;word-break:break-all">'+txt+'</div></div>';
  }).join('');
  const pg=q.pages||{}, sc=q.scan||{};
  document.getElementById('pgsum').innerHTML='<b>页面 '+pg.页面数+'</b> · 分组 '+
    Object.keys(pg.分组||{}).map(function(g){return g+':'+pg.分组[g];}).join(' / ');
  document.getElementById('gaps').innerHTML=(sc.盲区||[]).map(function(x){return '<div class=warn>· '+x+'</div>';}).join('');
  const pv=await (await fetch('/api/proactive/feed?limit=6')).json();
  document.getElementById('pv').innerHTML=(pv.记录||[]).map(function(r){
    return '<div class=card><div class=ok>'+r.kind+' · 分 '+r.综合分+'</div><div>'+r['说']+'</div>'+
           '<div class=muted style="font-size:11px">'+r.证据+'</div></div>';
  }).join('') || '<div class=muted>还没有主动汇报（过不了不打扰门就不说）</div>';
  const lt=await (await fetch('/api/longrun/status')).json();
  document.getElementById('lr').innerHTML=(lt.任务||[]).map(function(t){
    return '<div class=card><b>'+t.目标+'</b><div class=muted>'+t.状态+' · 进度 '+t.进度+
      ' · 下一步 '+t.下一步+'</div></div>';
  }).join('') || '<div class=muted>没有长任务</div>';
  const sc2=await (await fetch('/api/sched/status')).json();
  document.getElementById('sch').innerHTML='<div class=muted>任务数 '+sc2.任务数+' · 到期 '+((sc2.到期||[]).length)+
    '</div>'+(sc2.任务||[]).map(function(j){return '<div class=card><b>'+j.任务+'</b><div class=muted>每 '+
    j.每s+'s · 跑过 '+j.跑过+' · 迟到 '+j.迟到次数+'</div><div class=dim style="font-size:11px">'+(j.上次结果||'')+'</div></div>';}).join('');
  const mg=await (await fetch('/api/mirror/status')).json();
  document.getElementById('mg').innerHTML=(mg.空间||[]).map(function(s){
    return '<div class=card><b>'+s.目标+'</b><div class=muted>'+s.状态+' · 假设 '+s.假设+' · 推演 '+s.推演+
      (s.已落地?' · <span class=ok>已落地</span>':'')+'</div></div>';
  }).join('') || '<div class=muted>镜像里还没有排练</div>';
  const sg=await (await fetch('/api/stigmergy/status?limit=6')).json();
  document.getElementById('sg').innerHTML='<div class=muted>痕迹 '+sg.痕迹总数+'</div>'+
    (sg.最近||[]).map(function(r){return '<div class=card><b>'+r.kind+'</b> <span class=muted>强度 '+
      r.有效强度+'</span><div style="font-size:12px">'+JSON.stringify(r.payload||{}).slice(0,80)+'</div></div>';}).join('');
}
async function hubTick(){ await fetch('/api/sched/tick?limit=3',{method:'POST'}); hubLoad(); }
async function hubScan(){ const d=await (await fetch('/api/proactive/scan',{method:'POST'})).json();
  alert('主动扫了一遍：说了 '+(d.主动说了||[]).length+' 条，被门拦下 '+(d.被门拦下||[]).length+' 条'); hubLoad(); }
hubLoad(); setInterval(hubLoad, 15000);
"""


@router.get("/hub", response_class=HTMLResponse)
async def hub_page():
    body = f"""
<div class=wrap>
  <div class=card style="flex:1 1 100%">
    <b>总控台数据中枢</b>
    <div class=muted style="font-size:12px">所有页面的数字都从这里来（页面/能力图/部件读数/布局/盲区）。
      取不到就写原因，不编。</div>
    <div class=row style="gap:8px;margin-top:6px">
      <button class=btn onclick="hubTick()">推进一次心跳</button>
      <button class=btn onclick="hubScan()">扫一遍该主动说的事</button>
      <span id=pgsum class=muted></span>
    </div>
    <div id=gaps style="margin-top:6px"></div>
  </div>
  <div class=card style="flex:1 1 100%"><b>部件读数</b>
    <div class=row id=widg style="gap:8px;margin-top:6px"></div></div>
  <div class=card style="flex:1 1 48%"><b>主动汇报</b><div id=pv style="margin-top:6px"></div></div>
  <div class=card style="flex:1 1 48%"><b>长任务</b><div id=lr style="margin-top:6px"></div></div>
  <div class=card style="flex:1 1 48%"><b>心跳调度</b><div id=sch style="margin-top:6px"></div></div>
  <div class=card style="flex:1 1 48%"><b>镜像排练</b><div id=mg style="margin-top:6px"></div></div>
  <div class=card style="flex:1 1 100%"><b>信息素场地</b><div id=sg style="margin-top:6px"></div></div>
</div>
"""
    return HTMLResponse(Page(title="总控台中枢 · 唯一数据源 · 自主层", body=body,
                             current="/hub", extra_js=JS).render())


__all__ = ["router"]
