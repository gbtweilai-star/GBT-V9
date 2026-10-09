# panel/pipelines_page.py —— 流水线分类部署页（内置：步骤细节 / 部署登记 / 变更日志 / 固化回滚）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 全站内纪律：本页不做任何外跳；数据全部来自 core.pipelines 与 core.deploy_ledger 的真实读数；
# 目录里没有的能力写「待接（无模型）」并给出兜底通道，绝不假装已部署。
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from common.db import get_db

from core import deploy_ledger as J
from core import pipelines as P
from core import solidify as S
from skills.ui_design import inject

router = APIRouter()
STYLE = """<style>
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th,.tbl td{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;
  vertical-align:top}
.tbl td.num{text-align:right;font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.kind{display:inline-block;padding:1px 6px;border-radius:999px;border:1px solid var(--border);
  font-size:12px}
.k-scan{color:var(--accent)} .k-add{color:var(--ok)} .k-modify{color:var(--warn)}
.k-deploy{color:var(--accent-2)} .k-solidify{color:var(--accent-3)}
/* 按键样式统一在 skills/ui_design（勿在此另起一套） */
details{margin:4px 0}
summary{cursor:pointer}
</style>"""


def _steps_table(p) -> str:
    rows = []
    for s in p.步骤:
        state = ("<span class=muted>已接（替代实现）</span>" if s.缺口
                 else "已部署到云插件")
        ex = P.ALT_EXECUTOR.get(s.id, "")
        rows.append(
            f'<tr><td class=mono>{s.id}</td><td>{s.名称}</td>'
            f'<td class=mono>{s.插件}</td><td class=mono>{s.库槽}</td>'
            f'<td class=muted>{s.细节}</td>'
            f'<td class=mono>{(ex or s.本地备用)}</td>'
            f'<td>{s.门禁}</td><td>{state}'
            + (f'<div class=muted>{s.缺口}<br>待接：{s.待接槽}</div>' if s.缺口 else "")
            + "</td></tr>")
    return ('<table class=tbl><thead><tr><th>步</th><th>名称</th><th>云插件（主管道）</th>'
            '<th>结果库</th><th>这一步做什么</th><th>执行器（替代实现落地代码）</th>'
            '<th>门禁</th><th>状态</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def _journal_table(rows) -> str:
    if not rows:
        return '<p class=muted>还没有变更记录</p>'
    body = "".join(
        f'<tr><td class=muted>{r.get("iso") or ""}</td>'
        f'<td><span class="kind k-{r.get("kind")}">{r.get("kind")}</span></td>'
        f'<td class=mono>{r.get("where") or ""}</td>'
        f'<td class=muted>{json.dumps(r.get("detail"), ensure_ascii=False)[:200] if r.get("detail") else ""}</td>'
        f'<td>{"成功" if r.get("ok") else "未成"}</td>'
        f'<td class=muted>{r.get("reason") or ""}</td></tr>' for r in rows)
    return ('<table class=tbl><thead><tr><th>时间</th><th>类型</th><th>哪里</th><th>详情</th>'
            f'<th>结果</th><th>备注</th></tr></thead><tbody>{body}</tbody></table>')


def _page(st: dict, jr: dict, jsum: dict) -> str:
    au = st.get("审计", {})
    tent = st.get("触手分配", {})
    cards = []
    for p in P.PIPELINES:
        info = [x for x in st["流水线"] if x["id"] == p.id][0]
        t = tent.get(p.id, {})
        cards.append(
            f'<div class=card><h2>{p.名称} <span class=muted>（{p.目标}）</span></h2>'
            f'<p class=muted>步骤 {info["步骤数"]} · 已分类部署 {info["已分类部署"]} · '
            f'待接（无模型）{info["待接（无模型）"]} · 执行触手 {t.get("区间")}'
            f'（{len(t.get("触手") or [])} 根）· 门禁步骤 {info["门禁步骤"]}</p>'
            f'{_steps_table(p)}</div>')
    pend = "".join(f'<li class=mono>{x["哪里"]} → {x["待接登记"]}；当前兜底：{x["当前兜底"]}</li>'
                   for x in au.get("待接登记", []))
    return f"""{STYLE}
<h1>流水线分类部署（短视频 / 电影 / 音乐 / 专业编程）</h1>
<div class=card>
  <p>审计：流水线 <b>{au.get("流水线")}</b> · 步骤 <b>{au.get("步骤总数")}</b> ·
     映射问题 <b>{len(au.get("问题") or [])}</b> ·
     待接（目录无模型）<b>{len(au.get("待接登记") or [])}</b></p>
  <details><summary>待接登记（如实：Cloudflare Workers AI 目录里确实没有这些族）</summary>
    <ul>{pend or "<li class=muted>无</li>"}</ul></details>
  <div class=row>
    <button onclick="act('deploy')">重新执行分类部署</button>
    <button onclick="act('scan')">扫描并记录变更</button>
    <button onclick="act('solidify')">固化登记表</button>
    <button onclick="act('rollback')">回滚到上一版</button>
    <span id=out class=muted></span>
  </div>
  <p class=muted>固化：{st.get("固化", {}).get("registry")}（共 {st.get("固化", {}).get("versions")} 版）·
     登记表文件：{S.latest("pipeline_registry").get("name") or "pipeline_registry"} ·
     变更日志：{jsum.get("path")}</p>
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
  var url={{deploy:'/api/pipelines/deploy',scan:'/api/pipelines/scan',
            solidify:'/api/pipelines/solidify',rollback:'/api/pipelines/rollback'}}[kind];
  try{{
    var r=await (await fetch(url,{{method:'POST'}})).json();
    out.textContent = kind+': '+JSON.stringify(r).slice(0,160);
    setTimeout(function(){{location.reload()}},900);
  }}catch(e){{ out.textContent=kind+' 失败：'+e; }}
}}
</script>"""


@router.get("/pipelines", response_class=HTMLResponse)
async def pipelines_page(db=Depends(get_db)):
    st = P.status()
    jr = J.recent(80)
    page = ("<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>GBT小土豆V9 · 流水线分类部署</title></head><body><main id=main>"
            + _page(st, jr, J.summary()) + "</main></body></html>")
    return inject(page, "/pipelines")


@router.get("/api/pipelines/catalog")
async def api_catalog():
    return P.catalog()


@router.get("/api/pipelines/status")
async def api_status():
    return {**P.status(), "变更日志": None, "日志概览": J.summary()}


@router.get("/api/pipelines/journal")
async def api_journal(limit: int = 50):
    return J.recent(limit)


@router.get("/api/pipelines/step/{pipeline_id}/{step_id}")
async def api_step(pipeline_id: str, step_id: str):
    r = P.resolve_step(pipeline_id, step_id)
    if not r.get("ok"):
        raise HTTPException(404, r.get("reason") or "not_found")
    return r


@router.post("/api/pipelines/deploy")
async def api_deploy():
    return P.deploy_all()


@router.post("/api/pipelines/scan")
async def api_scan():
    return P.scan()


@router.post("/api/pipelines/solidify")
async def api_solidify():
    return P.register(note="面板点击固化")


@router.post("/api/pipelines/rollback")
async def api_rollback():
    r = S.rollback("pipeline_registry")
    J.record("solidify", "rollback:pipeline_registry", detail=r, ok=bool(r.get("ok")),
             reason=r.get("reason", ""))
    return r
