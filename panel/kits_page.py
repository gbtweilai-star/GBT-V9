# panel/kits_page.py —— 三套免费工具 → 云插件部署页（内置：步骤 / 配置校验 / 部署登记 / 变更日志）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 全站内：不做任何外跳。数据全部来自 core.kit_packs 与 core.deploy_ledger 的真实读数。
# "不混乱"的可视化：配置校验结果（白名单键/类型/插件族）直接打在页面上；有错就不许固化。
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse
from common.db import get_db

from core import deploy_ledger as J
from core import kit_packs as K
from core import solidify as S
from skills.ui_design import inject

router = APIRouter()
STYLE = """<style>
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th,.tbl td{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;
  vertical-align:top}
.muted{color:var(--muted)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.kind{display:inline-block;padding:1px 6px;border-radius:999px;border:1px solid var(--border);
  font-size:12px}
.k-scan{color:var(--accent)} .k-add{color:var(--ok)} .k-modify{color:var(--warn)}
.k-deploy{color:var(--accent-2)} .k-solidify{color:var(--accent-3)}
/* 按键样式统一在 skills/ui_design（勿在此另起一套） */
details{margin:4px 0} summary{cursor:pointer}
</style>"""


def _steps_table(pack) -> str:
    rows = []
    for s in pack.步骤:
        state = ("<span class=muted>已接（替代实现）</span>" if s.缺口
                 else "已部署到云插件")
        ex = K.ALT_EXECUTOR.get(s.id, "")
        rows.append(
            f'<tr><td class=mono>{s.id}</td><td>{s.名称}</td>'
            f'<td class=muted>{s.来源工具}</td>'
            f'<td class=mono>{s.插件}</td><td class=mono>{s.库槽}</td>'
            f'<td class=muted>{s.细节}</td><td class=mono>{ex or s.本地备用}</td>'
            f'<td>{s.门禁}</td><td>{state}'
            + (f'<div class=muted>{s.缺口}<br>待接：{s.待接槽}</div>' if s.缺口 else "")
            + "</td></tr>")
    return ('<table class=tbl><thead><tr><th>步</th><th>名称</th><th>来自工具</th>'
            '<th>云插件（主管道）</th><th>结果库</th><th>这一步做什么</th>'
            '<th>执行器（替代实现落地代码）</th><th>门禁</th><th>状态</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def _journal_table(rows) -> str:
    if not rows:
        return '<p class=muted>还没有变更记录</p>'
    body = "".join(
        f'<tr><td class=muted>{r.get("iso") or ""}</td>'
        f'<td><span class="kind k-{r.get("kind")}">{r.get("kind")}</span></td>'
        f'<td class=mono>{r.get("where") or ""}</td>'
        f'<td class=muted>{json.dumps(r.get("detail"), ensure_ascii=False)[:160] if r.get("detail") else ""}</td>'
        f'<td>{"成功" if r.get("ok") else "未成"}</td>'
        f'<td class=muted>{r.get("reason") or ""}</td></tr>' for r in rows)
    return ('<table class=tbl><thead><tr><th>时间</th><th>类型</th><th>哪里</th><th>详情</th>'
            f'<th>结果</th><th>备注</th></tr></thead><tbody>{body}</tbody></table>')


def _page(st: dict, jr: dict, jsum: dict) -> str:
    au = st.get("审计", {})
    cards = []
    for p in K.PACKS:
        info = [x for x in st["包"] if x["id"] == p.id][0]
        facts = "".join(f"<li>{x}</li>" for x in info["环境事实"])
        cards.append(
            f'<div class=card><h2>{p.名称} <span class=muted>（{p.工具}）</span></h2>'
            f'<p><b>免费依据</b>：{p.免费依据}</p>'
            f'<p><b>云端形态</b>：{p.云端形态}</p>'
            f'<p class=muted>步骤 {info["步骤数"]} · 已部署到云插件 {info["已部署到云插件"]} · '
            f'待接 {info["待接"]} · 闸门步骤 {info["闸门步骤"]} · {p.闸门}</p>'
            f'<details><summary>环境事实（避免后人搞混）</summary><ul>{facts}</ul></details>'
            f'{_steps_table(p)}</div>')
    gaps = "".join(
        f'<li class=mono>{g["哪里"]} → {g["缺口"]}（待接：{g.get("待接") or "目录无此族"}）</li>'
        for g in au.get("缺口", []))
    cfg = st.get("配置校验", {})
    return f"""{STYLE}
<h1>三套免费工具 · 云插件部署（剪映 / Qwen-Image / ComfyUI 式工作流）</h1>
<div class=card>
  <p>审计：包 <b>{au.get("包")}</b> · 步骤 <b>{au.get("步骤总数")}</b> ·
     映射问题 <b>{len(au.get("问题") or [])}</b> ·
     待接 <b>{len(au.get("缺口") or [])}</b></p>
  <p>配置校验：<b>{'通过' if cfg.get("ok") else "未通过"}</b>
     （包数 {cfg.get("包数")} · 不合格 {len(cfg.get("不合格") or [])}）·
     口径：白名单键 + 类型校验 + 插件族白名单，出现未知键直接拒收，不许固化
     {("<div class=bad>" + json.dumps(cfg.get("不合格"), ensure_ascii=False) + "</div>") if cfg.get("不合格") else ""}</p>
  <details open><summary>待接（如实：目录里确实没有这些能力）</summary>
    <ul>{gaps or "<li class=muted>无</li>"}</ul></details>
  <div class=row>
    <button onclick="act('deploy')">重新部署并登记</button>
    <button onclick="act('scan')">扫描并记录变更</button>
    <button onclick="act('solidify')">固化配置</button>
    <button onclick="act('rollback')">回滚上一版</button>
    <span id=out class=muted></span>
  </div>
  <p class=muted>固化：{S.latest("kit_packs").get("rev") or "尚未固化"}
     （共 {len(S.history("kit_packs"))} 版）· 变更日志：{jsum.get("path")} ·
     快照 scope：{", ".join(jsum.get("scopes") or []) or "—"}</p>
</div>
{''.join(cards)}
<div class=card>
  <h2>变更日志（扫描 / 新增 / 修改 / 部署 / 固化）</h2>
  <p class=muted>共 {jsum.get("total")} 条（扫描 {jsum["by_kind"]["scan"]} · 新增 {jsum["by_kind"]["add"]} ·
     修改 {jsum["by_kind"]["modify"]} · 部署 {jsum["by_kind"]["deploy"]} ·
     固化 {jsum["by_kind"]["solidify"]}）· 最近 {jsum.get("latest_iso")}</p>
  {_journal_table(jr.get("rows") or [])}
</div>
<script>
async function act(kind){{
  var out=document.getElementById('out'); out.textContent='…';
  var url={{deploy:'/api/kits/deploy',scan:'/api/kits/scan',
            solidify:'/api/kits/solidify',rollback:'/api/kits/rollback'}}[kind];
  try{{
    var r=await (await fetch(url,{{method:'POST'}})).json();
    out.textContent = kind+': '+JSON.stringify(r).slice(0,180);
    setTimeout(function(){{location.reload()}},900);
  }}catch(e){{ out.textContent=kind+' 失败：'+e; }}
}}
</script>"""


@router.get("/kits", response_class=HTMLResponse)
async def kits_page(db=Depends(get_db)):
    st = K.status()
    page = ("<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>GBT小土豆V9 · 三套工具云插件部署</title></head><body><main id=main>"
            + _page(st, J.recent(60), J.summary()) + "</main></body></html>")
    return inject(page, "/kits")


@router.get("/api/kits/catalog")
async def api_catalog():
    return K.catalog()


@router.get("/api/kits/status")
async def api_status():
    return K.status()


@router.get("/api/kits/config/{pack_id}")
async def api_config(pack_id: str):
    got = K.build_config(pack_id)
    if not got.get("ok"):
        raise HTTPException(404, got.get("reason") or "not_found")
    return {**got["config"], "校验": K.validate_config(got["config"])}


@router.post("/api/kits/validate")
async def api_validate(payload: dict = Body(...)):
    """把一份配置丢进来做严格校验（未知键/类型错/插件族不对都会被打回）。"""
    return K.validate_config(payload)


@router.get("/api/kits/journal")
async def api_journal(limit: int = 40):
    return J.recent(limit)


@router.post("/api/kits/deploy")
async def api_deploy():
    return K.deploy_all()


@router.post("/api/kits/scan")
async def api_scan():
    return K.scan()


@router.post("/api/kits/solidify")
async def api_solidify():
    return K.register(note="面板点击固化三套配置")


@router.post("/api/kits/rollback")
async def api_rollback():
    r = S.rollback("kit_packs")
    J.record("solidify", "rollback:kit_packs", detail=r, ok=bool(r.get("ok")),
             reason=r.get("reason", ""))
    return r
