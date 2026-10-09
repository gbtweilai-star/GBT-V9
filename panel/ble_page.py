# panel/ble_page.py —— 蓝牙操控页（内置在 V9 站内：扫描 / 读 / 写 / 审计回放）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律：页面本身不做任何外跳；写操作必须持 Grant（授权=唯一闸门），默认走演练模式；
#      一切结果（含被拒）都进 core.ble_audit 的追加式审计，可在本页回放。
from __future__ import annotations

import asyncio as _asyncio
import json

from fastapi import APIRouter, Body, Depends
from fastapi.responses import HTMLResponse
from common.db import get_db

from core import ble_control as bc
from senses import ble as sb
from skills.ui_design import inject

router = APIRouter()
STYLE = """<style>
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th,.tbl td{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left}
.tbl td.num{text-align:right;font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
input,select{background:var(--bg);border:1px solid var(--border);color:var(--text);
  border-radius:var(--r-sm);padding:6px 8px;font:inherit}
/* 按键样式统一在 skills/ui_design（勿在此另起一套） */
</style>"""


def _devices_table(rows) -> str:
    if not rows:
        return '<p class=muted>暂时没有设备（或读不到：见下方来源与原因）</p>'
    body = "".join(
        f'<tr><td><code>{r.get("mac") or "—"}</code></td><td>{r.get("name") or "（无名）"}</td>'
        f'<td>{r.get("kind") or ""}</td><td class=num>{r.get("rssi") if r.get("rssi") is not None else "—"}</td>'
        f'<td>{r.get("vendor") or "未知"}</td>'
        f'<td class=muted>{",".join(r.get("sources") or [])}</td></tr>' for r in rows)
    return ('<table class=tbl><thead><tr><th>MAC</th><th>名称</th><th>类型</th><th>RSSI</th>'
            f'<th>厂商</th><th>来源</th></tr></thead><tbody>{body}</tbody></table>')


def _ops_table(rows) -> str:
    if not rows:
        return '<p class=muted>还没有操作记录</p>'
    body = "".join(
        f'<tr><td class=muted>{r.get("iso") or ""}</td><td>{r.get("action") or ""}</td>'
        f'<td><code>{r.get("target") or "—"}</code></td>'
        f'<td>{"成功" if r.get("ok") else "失败"}</td>'
        f'<td class=muted>{r.get("decided_by") or ""}</td>'
        f'<td class=muted>{r.get("reason") or ""}</td></tr>' for r in rows)
    return ('<table class=tbl><thead><tr><th>时间</th><th>动作</th><th>目标</th><th>结果</th>'
            f'<th>决策来源</th><th>原因</th></tr></thead><tbody>{body}</tbody></table>')


def _page(rep: dict, ops: dict) -> str:
    st = rep.get("status", {}) or {}
    ad = st.get("适配器", {}) or {}
    rf = st.get("射频扫描", {}) or {}
    dev = rep.get("devices", {}) or {}
    au = rep.get("audit", {}) or {}
    errs = dev.get("errors") or []
    err_txt = ("；".join(json.dumps(e, ensure_ascii=False) for e in errs)
               if errs else "无")
    return f"""{STYLE}
<h1>蓝牙操控（AI 决策 · 授权执行 · 全程留痕）</h1>
<div class=card>
  <b>状态</b>
  <p>平台：{st.get("平台")} · 适配器：{ad.get("count") if ad.get("count") is not None else "无法确认"}
     （{", ".join(x.get("name", "") for x in (ad.get("list") or [])) or "—"}）·
     射频扫描可用：{"是" if rf.get("ok") else "否"} {rf.get("reason") or ""} ·
     写操作需要：{st.get("写操作需")}</p>
  <p class=muted>来源失败：{err_txt} · 审计文件：{au.get("path")} ·
     记录 {au.get("total")} 条（成功 {au.get("success")} / 失败 {au.get("failed")}）</p>
</div>
<div class=card>
  <b>设备（多源合并：注册表 / PnP / 射频）</b>
  {_devices_table(dev.get("rows") or [])}
</div>
<div class=card>
  <b>下指令（自然语言 → AI 决策 → 授权后执行）</b>
  <div class=row>
    <input id=intent size=42 placeholder="例：扫一下周边蓝牙 / 给 AA:BB:CC:DD:EE:FF 开灯">
    <select id=act>
      <option value="">按 AI 决策</option>
      <option value="scan">只扫描</option>
      <option value="status">只看状态</option>
      <option value="read">读特征</option>
      <option value="write">写特征</option>
    </select>
    <label class=muted><input type=checkbox id=dry checked> 演练（不真发）</label>
    <button onclick="go()">执行</button>
  </div>
  <p class=muted>写操作没有 Grant 一律拒绝；演练模式只校验不发送。授权=唯一闸门。</p>
  <pre id=out class=muted style="white-space:pre-wrap;font-size:12px"></pre>
</div>
<div class=card>
  <b>审计回放（追加式，只增不改）</b>
  {_ops_table(ops.get("rows") or [])}
</div>
<script>
async function go(){{
  var el=document.getElementById('out'); el.textContent='…';
  var body={{intent:document.getElementById('intent').value,
             action:document.getElementById('act').value,
             dry_run:document.getElementById('dry').checked}};
  try{{
    var r=await fetch('/api/ble/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify(body)}});
    var d=await r.json(); el.textContent=JSON.stringify(d,null,1);
    setTimeout(function(){{location.reload()}},1200);
  }}catch(e){{ el.textContent='失败：'+e; }}
}}
</script>"""


@router.get("/ble", response_class=HTMLResponse)
async def ble_page(db=Depends(get_db)):
    # 起 PowerShell 枚举 + 3 秒射频扫描：**必须丢到线程**，否则事件循环被堵住，
    # 同一进程里别的页面（总控台/总能力）也跟着卡（真机表现：一停一停）。
    rep = await _asyncio.to_thread(bc.report)
    ops = await _asyncio.to_thread(bc.history, 20)
    page = ("<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>GBT小土豆V9 · 蓝牙操控</title></head><body><main id=main>"
            + _page(rep, ops) + "</main></body></html>")
    return inject(page, "/ble")


@router.get("/api/ble/status")
async def ble_status():
    st = await _asyncio.to_thread(sb.status)
    au = await _asyncio.to_thread(bc.summary)
    return {"status": st, "audit": au}


@router.get("/api/ble/scan")
async def ble_scan(duration: float = 5.0, filter_name: str = "", rf: bool = True):
    # 用户显式要求"现在扫一次" → 不吃缓存；但也不许堵住事件循环。
    # 注意：senses.ble.scan 的 filter_name/rf 是**关键字专用参数**，按位置传会 TypeError → 500
    #（真踩过：面板上一按"执行"就 500，而函数本身没问题）。这里用 lambda 保关键字。
    res = await _asyncio.to_thread(
        lambda: sb.scan(duration=duration, filter_name=(filter_name or None), rf=rf))
    await _asyncio.to_thread(bc.audit, {"action": "scan", "target": "", "decided_by": "api",
                                        "ok": bool(res.get("ok")),
                                        "reason": "" if res.get("ok") else "扫描失败"})
    return res


@router.get("/api/ble/ops")
async def ble_ops(limit: int = 20):
    return bc.history(limit)


@router.post("/api/ble/command")
async def ble_command(payload: dict = Body(...)):
    """自然语言/指定动作 → 决策 → 授权 → 执行 → 审计。写操作需 Grant（此处无 Grant → 拒绝）。"""
    intent = str(payload.get("intent") or "")
    forced = str(payload.get("action") or "")
    dry = bool(payload.get("dry_run", True))
    plan = bc.decide(intent) if intent else {"ok": True, "action": None,
                                             "decided_by": "api(无意图)"}
    if forced:
        plan = {**plan, "action": forced, "decided_by": (plan.get("decided_by") or "") +
                "+api.override"}
    if not plan.get("action"):
        return {"ok": False, "reason": plan.get("reason") or "没听懂要做什么", "plan": plan}
    res = bc.apply_plan(plan, dry_run=dry)
    return {**res, "plan": plan, "dry_run": dry}


@router.get("/api/ble/report")
async def ble_report():
    return bc.report()
