# panel/capability_page.py —— 总能力图表 + 连接状态 + 云插件/数据库精准用量 + 固化回滚
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径：数字来自 core.capability_map（每格标 source=observed/computed/unavailable，不报假 0）；
#      算力通道来自 core.compute_router（云主管道 / 本地备用，本地 0 显存）；
#      固化与回滚来自 core.solidify（版本 + sha256，一键回到上一版）。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time as _time
import os as _os_mod
import asyncio as _asyncio

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse
from common.db import get_db
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

from core import capability_map as cm
from core import compute_router as cr
from core import production_gate as pg
from common.ttl_cache import TTLCache as _TTL
from skills.ui_design import inject

router = APIRouter()

# 面板重活缓存：生产闸门审计/闭环状态/闭环图各自要几秒，而页面每次刷新都会问一次
# （页面的轮询也会问）。这些是**只读状态视图**，用"过期先给旧值 + 后台刷新"的
# 缓存既不影响准确性，又不会让人打开页面就等半分钟（真机实测：不加缓存首次 39 秒）。
_DASH_TTL = float(_os_mod.environ.get("V9_DASH_CACHE_TTL", "60"))
_CACHE = _TTL(ttl=_DASH_TTL, name="dashboard")


def _cached(key: str, fn, *, ttl: float | None = None):
    return _CACHE.get(key, fn)


def _drop_cache(*keys: str) -> None:
    _CACHE.drop(*keys)

STYLE = """<style>
.graphbox{padding:8px 0;overflow:auto}
.graphbox svg{max-width:100%;height:auto;border-radius:12px;
  border:1px solid rgba(255,255,255,.07);
  background:linear-gradient(180deg,rgba(255,255,255,.02),rgba(255,255,255,.005))}

.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th,.tbl td{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left}
.tbl td.num{text-align:right;font-variant-numeric:tabular-nums}
.row{display:grid;grid-template-columns:150px 1fr 170px 1fr;gap:var(--s2,8px);
  align-items:center;margin:6px 0}
.bar{background:var(--bg);border:1px solid var(--border);border-radius:999px;height:12px;
  overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent);transition:width .4s ease}
.bar.none i{background:#666}
.nums{text-align:right;font-variant-numeric:tabular-nums}
.src{font-size:12px}
/* 按键样式不再在这里写：全站统一在 skills/ui_design（.btn 家族 + skin 归一） */
</style>"""


def _led():
    try:
        import panel.server as srv
        return srv.get_ledger()
    except Exception:                                     # noqa: BLE001
        return None


def _cell(v) -> str:
    """一格读数：None → 「无法确认（原因）」；绝不写 0。"""
    if v is None:
        return '<span class=muted>—</span>'
    if isinstance(v, dict):
        if v.get("value") is None:
            return (f'<span class=muted title="{v.get("why", "")}">无法确认'
                    f'（{v.get("why", "")}）</span>')
        return str(v.get("value"))
    return str(v)


def _usage_html(u: dict) -> str:
    out = [f'<p class=muted>口径：{u.get("口径", "")}</p>']
    for title, block in (("云插件用量", u.get("云插件用量", {})),
                         ("数据库用量", u.get("数据库用量", {})),
                         ("驱动器用量", u.get("驱动用量", {}))):
        rows = "".join(
            f'<tr><td>{k}</td><td class=num>{_cell(v)}</td>'
            f'<td class=muted>{v.get("source", "") if isinstance(v, dict) else ""}</td>'
            f'<td class=muted>{(v.get("where") or v.get("why") or "") if isinstance(v, dict) else ""}'
            f'</td></tr>' for k, v in block.items())
        out.append(f'<h3>{title}</h3><table class=tbl><thead><tr><th>指标</th><th>值</th>'
                   f'<th>来源</th><th>依据</th></tr></thead><tbody>{rows}</tbody></table>')
    return "".join(out)


def _chart_html(c: dict, u: dict, s: dict, d: dict | None = None,
                g: dict | None = None, pgst: dict | None = None,
                lp: dict | None = None, graph_svg: str = "") -> str:
    bars = []
    for x in c.get("子系统", []):
        pct, total, on = x.get("条形占比"), x.get("总数"), x.get("已接通")
        denom = x.get("分母") or total
        w = f"{max(2, int((pct or 0) * 100))}%" if pct is not None else "2%"
        cls = "" if pct is not None else " none"
        label = (f"{on} / {denom}" if (on is not None and denom is not None)
                 else (f"{total}（无绑定关系）" if total is not None else "无法确认"))
        bars.append(f'<div class=row><div>{x["名称"]}</div>'
                    f'<div class="bar{cls}"><i style="width:{w}"></i></div>'
                    f'<div class=nums>{label}</div>'
                    f'<div class="src muted">{x["证据"]}</div></div>')
    conn = "".join(
        f'<tr><td>{r["子系统"]}</td><td class=num>{_cell(r["总数"])}</td>'
        f'<td class=num>{_cell(r["已连接"])}</td><td>{r["状态"]}</td>'
        f'<td class=muted>{r["证据"]}</td></tr>' for r in c.get("连接状态", []))
    pol = c.get("通道", {})
    return f"""{STYLE}
<div class=card><h2>总能力图表</h2>
  <p class=muted>条形 = 已接通 / 能力总数；「无绑定关系」表示该子系统按设计不绑定（不是缺数据）。</p>
  {''.join(bars)}
</div>
<div class=card><h2>连接状态</h2>
  <table class=tbl><thead><tr><th>链路</th><th>总数</th><th>已连接</th><th>状态</th>
  <th>证据表</th></tr></thead><tbody>{conn}</tbody></table>
</div>
<div class=card><h2>算力通道（本地 0 显存）</h2>
  <p>当前通道：<b>{pol.get('channel')}</b> ·
     云插件为主管道：<b>{'是' if pol.get('cloud_primary') else '否'}</b> ·
     本地预留显存：<b>{pol.get('local_vram_mb')} MB</b> ·
     本地备用保留：<b>{'是' if pol.get('local_backup_kept') else '否'}</b> ·
     拒绝本地 GPU 活：<b>{'是' if pol.get('refuse_local_gpu') else '否'}</b></p>
  <div id=computetbl class=muted>加载中…</div>
</div>
<div class=card><h2>精准用量</h2><div id=usage>{_usage_html(u)}</div></div>
<div class=card><h2>内置明细（读帧插件 / 算力活 / 只读工具）</h2>
  <p class=muted>全部读的是本机真实模块与真实表；读不到写「无法确认（原因）」，不报假 0。
     这些能力都**内置在 V9 站内**，页面不做外跳。</p>
  <div id=detail>{detail_html(d or {})}</div>
</div>
<div class=card><h2>闭环流程图（节点图）</h2>
  <p class=muted>触发 → 闭环验证器 → 每条闭环 → 成功/失败分支；下方虚线是依赖节点。
     节点颜色即状态（绿=已跑通 / 黄=部分 / 红=阻塞 / 灰=依赖或未验）。全部内联 SVG，零外链。</p>
  <div class=row><button onclick="verifyLoops()">真验一次（含真入队 + 真扫蓝牙）</button>
    <span id=loopout class=muted></span></div>
  <div class=graphbox>{graph_svg}</div>
</div>
<div class=card><h2>闭环清单（全部真验，给证据）</h2>
  <p class=p muted>同上的表格版：拿不到写「无法确认 + 原因」，是阻塞就写清阻塞与解锁动作。</p>
  <div id=loops>{loops_html(lp)}</div>
</div>
<div class=card><h2>按键 ↔ 能力 双向绑定</h2>
  <p class=muted>12 个按键都登记在页面注册表里，并与资源做**双向绑定**（页面→资源 / 资源→页面 两向齐全才算成）；
     审计逐个确认路由与接口真实存在（只读本进程路由表，不对外发请求）。</p>
  <div id=pages>{pages_html(pgst)}</div>
</div>
<div class=card><h2>生产就绪度 · 根因台账</h2>
  <p class=muted>面板上每一处"未达标/待接/无法确认/失败"都在这里给出：现状 → 根因 → 归类
     （已修复 / 阻塞·账务 / 阻塞·凭据 / 阻塞·硬件 / 按设计 / 待厂商目录）→ 修复动作。改不了的就写改不了。</p>
  <div id=gate>{gate_html(g)}</div>
</div>
<div class=card><h2>固化与回滚保护</h2>
  <p class=muted>把当前读数定格成带 sha256 的版本；回滚先校验哈希，不符则拒绝。</p>
  <button onclick="solidify()">固化当前图表</button>
  <button onclick="rollback()">回滚上一版</button>
  <div id=solid>{_solid_html(s)}</div>
</div>
<script>
function save(id,t){{var e=document.getElementById(id); if(e) e.innerHTML=t;}}
async function load(){{
  try{{
    var c=await (await fetch('/api/capability/chart')).json();
    save('usage', renderUsage(c['用量']||{{}}));
    save('solid', renderSolid(await (await fetch('/api/capability/solid')).json()));
  }}catch(e){{save('usage','无法确认（'+e+'）');}}
  try{{
    var d=await (await fetch('/api/capability/detail')).json();
    save('detail', renderDetail(d));
  }}catch(e){{save('detail','无法确认（'+e+'）');}}
  try{{
    var r=await (await fetch('/api/compute/route')).json();
    var t='<table class=tbl><thead><tr><th>算力活</th><th>用途</th><th>本地需显存</th>'+
          '<th>云插件（主管道）</th><th>结果库</th><th>本地备用</th></tr></thead><tbody>';
    (r['table']||[]).forEach(function(x){{t+='<tr><td>'+x['活']+'</td><td>'+x['用途']+
      '</td><td class=num>'+x['本地需显存MB']+' MB</td><td>'+x['云插件（主管道）']+
      '</td><td>'+x['结果库']+'</td><td class=muted>'+x['本地备用']+'</td></tr>';}});
    save('computetbl', t+'</tbody></table><p class=muted>本地预留 '+r['local_vram_reserved_mb']+
      ' MB（16 个活全本地需 '+r['local_vram_if_all_local_mb']+' MB，已省 '+
      r['saved_local_vram_mb']+' MB）；审计问题 '+((r['problems']||[]).length)+' 个</p>');
  }}catch(e){{save('computetbl','无法确认（'+e+'）');}}
}}
function renderUsage(u){{
  function block(t,b){{var s='<h3>'+t+'</h3><table class=tbl><tbody>';
    for(var k in (b||{{}})){{var v=b[k];
      var val=(v.value===null||v.value===undefined)?
        ('<span class=muted>无法确认（'+(v.why||'')+'）</span>'):v.value;
      s+='<tr><td>'+k+'</td><td class=num>'+val+'</td><td class=muted>'+v.source+
         '</td><td class=muted>'+((v.where||v.why)||'')+'</td></tr>';}}
    return s+'</tbody></table>';}}
  return block('云插件用量',u['云插件用量'])+block('数据库用量',u['数据库用量'])+
         block('驱动器用量',u['驱动用量']);
}}
function renderSolid(s){{
  var h='<p>档案：<span class=muted>'+((s&&s.archive)||'')+'</span> · 保留 '+
        ((s&&s.keep)||'')+' 版</p><table class=tbl><thead><tr><th>固化名</th>'+
        '<th>当前版本</th></tr></thead><tbody>';
  var L=(s&&s.latest)||{{}}; var any=false;
  for(var k in L){{any=true; h+='<tr><td>'+k+'</td><td class=muted>'+L[k]+'</td></tr>';}}
  if(!any) h+='<tr><td colspan=2 class=muted>尚未固化</td></tr>';
  return h+'</tbody></table>';
}}
function renderDetail(d){{
  function tbl(cols, rows){{
    var h='<table class=tbl><thead><tr>'; cols.forEach(function(c){{h+='<th>'+c+'</th>';}});
    h+='</tr></thead><tbody>';
    if(!rows.length) h+='<tr><td colspan='+cols.length+' class=muted>无法确认</td></tr>';
    rows.forEach(function(r){{h+='<tr>'; r.forEach(function(v){{h+='<td>'+v+'</td>';}}); h+='</tr>';}});
    return h+'</tbody></table>';
  }}
  var out='';
  var f=(d['读帧插件']||{{}});
  out+='<h3>5 个读帧插件（真实读数）</h3>'+tbl(['插件','名称','读成功','原因','读数'],
    (f['行']||[]).map(function(r){{return [r['插件'], r['名称'],
      (r.ok===true?'是':(r.ok===false?'否':'无法确认')), (r['原因']||''),
      (r['读数']?JSON.stringify(r['读数']).slice(0,120):'')];}}));
  var w=(d['算力活']||{{}});
  out+='<h3>16 项算力活（云插件主管道 / 本地备用）</h3>'+
    tbl(['算力活','用途','本地需显存','云插件（主管道）','结果库','本地备用'],
    (w['行']||[]).map(function(x){{return [x.id, x.use, x.vram_mb+' MB', x.plugin, x.db,
      '<span class=muted>'+x.local_impl+'</span>'];}}));
  var t=(d['只读工具']||{{}});
  var calls={{}}; (t['调用统计']||[]).forEach(function(r){{ if(r&&r.domain) calls[r.domain]=r.n; }});
  out+='<h3>只读工具（调用次数来自 read_tool_audit）</h3>'+
    tbl(['工具','域','历史调用'], (t['行']||[]).map(function(r){{
      return [r['工具'], r['域'], (calls[r['域']]!==undefined?calls[r['域']]:'—')];}}));
  return out;
}}
async function solidify(){{
  var r=await (await fetch('/api/capability/solidify',{{method:'POST'}})).json();
  save('solid', r.ok? ('已固化：'+r.rev+' · sha256 '+String(r.sha256).slice(0,12)+'…')
                    : ('固化失败：'+r.reason));
  setTimeout(load,800);
}}
async function rollback(){{
  var r=await (await fetch('/api/capability/rollback',{{method:'POST'}})).json();
  save('solid', r.ok? ('已回滚到 '+r.rolled_back_to+'（哈希校验通过）') : ('回滚被拒：'+r.reason));
  setTimeout(load,800);
}}
load(); setInterval(load,15000);
</script>"""


def _solid_html(s: dict) -> str:
    L = (s or {}).get("latest") or {}
    rows = "".join(f'<tr><td>{k}</td><td class=muted>{v}</td></tr>' for k, v in L.items())
    if not rows:
        rows = '<tr><td colspan=2 class=muted>尚未固化</td></tr>'
    return (f'<p>档案：<span class=muted>{(s or {}).get("archive", "")}</span> · '
            f'保留 {(s or {}).get("keep", "")} 版</p>'
            f'<table class=tbl><thead><tr><th>固化名</th><th>当前版本</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def detail_html(d: dict) -> str:
    """内置明细三表：读帧插件 / 算力活（云主管道）/ 只读工具（含真实调用次数）。"""
    fr = (d or {}).get("读帧插件", {}) or {}
    fr_rows = "".join(
        f'<tr><td>{r["插件"]}</td><td>{r["名称"]}</td>'
        f'<td>{"是" if r.get("ok") else ("否" if r.get("ok") is False else "无法确认")}</td>'
        f'<td class=muted>{(r.get("原因") or "")}</td>'
        f'<td class=muted>{(json.dumps(r.get("读数"), ensure_ascii=False)[:120] if r.get("读数") else "")}'
        f'</td></tr>' for r in fr.get("行", []))
    wl = (d or {}).get("算力活", {}) or {}
    wl_rows = "".join(
        f'<tr><td>{w["id"]}</td><td>{w["use"]}</td><td class=num>{w["vram_mb"]} MB</td>'
        f'<td>{w["plugin"]}</td><td>{w["db"]}</td>'
        f'<td class=muted>{w["local_impl"]}</td></tr>' for w in wl.get("行", []))
    rt = (d or {}).get("只读工具", {}) or {}
    calls = {}
    for row in (rt.get("调用统计") or []):
        if isinstance(row, dict) and "domain" in row:
            calls[row["domain"]] = row.get("n")
    rt_rows = "".join(
        f'<tr><td>{r["工具"]}</td><td>{r["域"]}</td>'
        f'<td class=num>{(calls.get(r["域"], "—") if r["域"] in calls else "—")}</td></tr>'
        for r in rt.get("行", []))
    bl = (d or {}).get("蓝牙", {}) or {}
    bl_rows = "".join(
        f'<tr><td><code>{x.get("mac") or "—"}</code></td><td>{x.get("name") or "（无名）"}</td>'
        f'<td>{x.get("kind") or ""}</td>'
        f'<td class=num>{x.get("rssi") if x.get("rssi") is not None else "—"}</td>'
        f'<td class=muted>{",".join(x.get("sources") or [])}</td></tr>'
        for x in bl.get("设备", []))
    bl_err = "；".join(json.dumps(e, ensure_ascii=False) for e in (bl.get("来源失败") or []))
    return f"""
<h3>5 个读帧插件（真实读数）</h3>
<table class=tbl><thead><tr><th>插件</th><th>名称</th><th>读成功</th><th>原因</th>
<th>读数</th></tr></thead><tbody>{fr_rows or '<tr><td colspan=5 class=muted>无法确认</td></tr>'}
</tbody></table>
<h3>17 项算力活（云插件主管道 / 本地备用）</h3>
<table class=tbl><thead><tr><th>算力活</th><th>用途</th><th>本地需显存</th>
<th>云插件（主管道）</th><th>结果库</th><th>本地备用实现</th></tr></thead>
<tbody>{wl_rows or '<tr><td colspan=6 class=muted>无法确认</td></tr>'}</tbody></table>
<h3>只读工具（按需调用，调用次数来自 read_tool_audit）</h3>
<table class=tbl><thead><tr><th>工具</th><th>域</th><th>历史调用</th></tr></thead>
<tbody>{rt_rows or '<tr><td colspan=3 class=muted>无法确认</td></tr>'}</tbody></table>
<h3>蓝牙（AI 操控；写操作需授权）</h3>
<p class=muted>适配器 {bl.get("适配器数") if bl.get("适配器数") is not None else "无法确认"} ·
   设备 {bl.get("设备数") if bl.get("设备数") is not None else "无法确认"} ·
   GATT 服务行 {bl.get("GATT服务行") if bl.get("GATT服务行") is not None else "—"} ·
   来源 {",".join(bl.get("来源") or []) or "—"} · 来源失败：{bl_err or "无"} ·
   写操作需要 {bl.get("写操作需要")} · 页内可操作：<a href="/ble">蓝牙操控页（内置）</a></p>
<table class=tbl><thead><tr><th>MAC</th><th>名称</th><th>类型</th><th>RSSI</th>
<th>来源</th></tr></thead><tbody>
{bl_rows or '<tr><td colspan=5 class=muted>暂时没有设备（或读不到——见来源失败）</td></tr>'}
</tbody></table>
"""


def pg_status() -> dict:
    """页面注册表状态（按键 + 双向绑定 + 审计）。失败如实返回空，不编。"""
    try:
        from core import page_registry as PR
        return PR.status()
    except Exception as exc:                              # noqa: BLE001
        return {"按键": [], "按键数": 0, "审计": {"ok": False,
                                                 "错误": type(exc).__name__},
                "绑定对样例": 0}


def graph_svg(verify: dict | None = None) -> str:
    """闭环流程图的内联 SVG（失败给空串，页面显示占位）。

    verify 传进来就复用已算好的闭环状态 —— 不然「清单」和「流程图」各算一遍，白等约 8 秒。
    """
    try:
        from core import loop_graph as LG
        return LG.status(verify=verify).get("svg") or ""
    except Exception:                                     # noqa: BLE001
        return ""


def loops_status() -> dict:
    """闭环清单（轻量，不真跑）：失败如实给空表 + 原因。"""
    try:
        from core import loop_verifier as LV
        return LV.summary()
    except Exception as exc:                              # noqa: BLE001
        return {"闭环": [], "闭环总数": 0, "已跑通": 0, "部分": 0, "阻塞": 0,
                "就绪度": None, "阻塞清单": [], "错误": type(exc).__name__}


def gate_html(g: dict) -> str:
    """生产就绪度 + 根因台账表。"""
    if not g:
        return '<p class=muted>无法确认（审计未运行）</p>'
    st = g.get("统计", {})
    rows = "".join(
        f'<tr><td>{i["面板"]}</td><td>{i["项目"]}</td><td class=muted>{i["现状"]}</td>'
        f'<td class=muted>{i["根因"]}</td>'
        f'<td class="{"ok" if str(i["归类"]).startswith("已修复") else ("warn" if str(i["归类"]).startswith("阻塞") else "muted")}">{i["归类"]}</td>'
        f'<td class=muted>{i["修复"]}</td></tr>' for i in g.get("项", []))
    blocks = "".join(f'<li>{b["项目"]}：{b["根因"]}</li>' for b in g.get("阻塞清单", []))
    return (f'<p><b>生产就绪度 {g.get("生产就绪度")}%</b> · 已修复 {st.get("已修复")} · '
            f'阻塞（非代码）{st.get("阻塞（非代码）")} · 按设计 {st.get("按设计")} · '
            f'待厂商目录 {st.get("待厂商目录")} · 合计 {st.get("合计")}</p>'
            f'<p class=muted>驱动通道：{g.get("通道") or "—"} {g.get("通道说明") or ""}</p>'
            f'<details><summary>阻塞清单（只有你能清的那些）</summary><ul>{blocks or "<li>无</li>"}</ul></details>'
            f'<table class=tbl><thead><tr><th>面板</th><th>项目</th><th>现状</th><th>根因</th>'
            f'<th>归类</th><th>修复动作</th></tr></thead><tbody>{rows}</tbody></table>')


def loops_html(lp: dict) -> str:
    """闭环表：闭环 / 状态 / 证据摘要 / 阻塞与解锁。"""
    if not lp:
        return '<p class=muted>无法确认（闭环验证未运行）</p>'
    rows = ""
    for x in lp.get("闭环", []):
        st = x.get("状态", "")
        cls = "ok" if st.startswith("已跑通") else ("bad" if st == "阻塞" else "warn")
        rows += (f'<tr><td>{x.get("闭环")}</td><td class={cls}>{st}</td>'
                 f'<td class=muted>{json.dumps(x.get("证据"), ensure_ascii=False)[:150]}</td>'
                 f'<td class=muted>{(x.get("阻塞") or "")[:120]}</td>'
                 f'<td class=muted>{(x.get("解锁") or "")[:120]}</td></tr>')
    bl = "".join(f'<li><b>{b["闭环"]}</b>：{b["原因"]} <span class=muted>→ {b["解锁"]}</span></li>'
                 for b in lp.get("阻塞清单", []))
    return (f'<p><b>闭环就绪度 {lp.get("就绪度")}%</b> · 总数 {lp.get("闭环总数")} · '
            f'已跑通 {lp.get("已跑通")} · 部分 {lp.get("部分")} · 阻塞 {lp.get("阻塞")} · '
            f'校验时间 {lp.get("at")}</p>'
            f'<details open><summary>阻塞/待解锁</summary><ul>{bl or "<li>无</li>"}</ul></details>'
            f'<table class=tbl><thead><tr><th>闭环</th><th>状态</th><th>证据</th>'
            f'<th>阻塞</th><th>解锁</th></tr></thead><tbody>{rows}</tbody></table>'
            '<script>async function verifyLoops(){var o=document.getElementById("loopout");'
            'o.textContent="真验中…（会真入队一次 + 真扫蓝牙，约 10 秒）";'
            'try{var r=await (await fetch("/api/loops/verify",{method:"POST"})).json();'
            'o.textContent="就绪度 "+r["就绪度"]+"% · 已跑通 "+r["已跑通"]+"/"+r["闭环总数"];'
            'setTimeout(function(){location.reload()},900);}catch(e){o.textContent="失败："+e;}}'
            '</script>')


def pages_html(st: dict) -> str:
    """12 个按键的双向绑定表：按键 / 路由 / 分组 / 接口数 / 资源数 / 绑定方向。"""
    if not st:
        return '<p class=muted>无法确认（注册表未加载）</p>'
    au = st.get("审计", {})
    rows = "".join(
        f'<tr><td>{p["图标"]} {p["标题"]}</td><td class=mono>{p["路由"]}</td>'
        f'<td class=muted>{p["分组"]}</td><td class=muted>{p["一句说明"]}</td>'
        f'<td class=num>{p["接口数"]}</td><td class=num>{p["资源数"]}</td>'
        f'<td>{"双向 ✓" if p["绑定向"] == "双向" else "单向"}</td></tr>'
        for p in st.get("按键", []))
    return (f'<p><b>按键 {st.get("按键数")} 个</b> · 绑定对 {st.get("绑定对样例")} · '
            f'分组 {"、".join(au.get("分组", []))} · '
            f'路由问题 {len(au.get("路由问题") or [])} · 接口缺失 {len(au.get("接口缺失") or [])} · '
            f'不对称绑定 {len(au.get("不对称绑定") or [])} · 审计 {"通过" if au.get("ok") else "未通过"}</p>'
            f'<table class=tbl><thead><tr><th>按键</th><th>路由</th><th>分组</th><th>说明</th>'
            f'<th>接口数</th><th>资源数</th><th>绑定</th></tr></thead><tbody>{rows}</tbody></table>'
            + (f'<details><summary>接口缺失明细</summary><ul>'
               + "".join(f'<li class=mono>{x["页面"]} → {x["接口"]}</li>'
                         for x in (au.get("接口缺失") or [])[:20]) + "</ul></details>"
               if au.get("接口缺失") else ""))


def panels_html() -> str:
    """逐项能力控制面板对齐表（五查：面板/状态源/操作/证据/验收）+ 本轮补齐清单。"""
    try:
        from core import capability_panels as CP
        a = CP.audit()
        u = CP.ui_unify()
    except Exception as exc:                                  # noqa: BLE001
        return f'<div class=card><h2>能力控制面板对齐</h2><p class=bad>审计不可用：{type(exc).__name__}</p></div>'
    rows = "".join(
        f'<tr><td>{r["组"]}</td><td><b>{r["能力"]}</b></td><td>{r["面板"]}</td>'
        f'<td class=muted>{r["状态源"]}</td><td class=muted>{r["操作"]}</td>'
        f'<td class=muted>{r["证据"]}</td><td class=muted>{r["验收"]}</td></tr>'
        for r in a["项"])
    fixes = "".join(f'<li><b>{x["项"]}</b>：{x["说明"]}</li>' for x in a["本轮补齐"])
    uirows = "".join(
        f'<tr><td>{x["页面"]}</td><td class="{"ok" if x["在体系内"] else "bad"}">'
        f'{"在体系内" if x["在体系内"] else "体系外"}</td>'
        f'<td class=muted>{x["入口"]}</td><td class=muted>{x["自带 button 规则"]}</td></tr>'
        for x in u["行"])
    return (
        '<div class=card><h2>能力控制面板 · 逐项对齐</h2>'
        f'<p class=muted>{a["口径"]}　已对齐 <b class=ok>{a["已对齐"]}/{a["总数"]}</b>'
        f'（{a["对齐率"] * 100:.0f}%）缺项 {len(a["缺项"])}。</p>'
        '<table><tr><th>组</th><th>能力</th><th>面板</th><th>状态源</th><th>操作/验证</th>'
        '<th>证据</th><th>验收标准</th></tr>' + rows + '</table></div>'
        '<div class=card><h2>本轮补齐（逐项点名）</h2><ul>' + fixes + '</ul></div>'
        '<div class=card><h2>按键统一（静态核对）</h2>'
        f'<p class=muted>{u["口径"]}　在体系内 <b class=ok>{u["在体系内"]}/{u["页面数"]}</b>'
        f'，残留自写 button 规则 {u["残留自写 button 规则合计"]} 条。</p>'
        '<table><tr><th>页面</th><th>体系</th><th>入口</th><th>自写 button 规则</th></tr>'
        + uirows + '</table></div>')


@router.get("/capability", response_class=HTMLResponse)
async def capability_page(db=Depends(get_db)):
    led = _led()
    c = await cm.chart(db, led)
    d = await cm.detail(db, led)
    # 这三件是同步重活（各 6~8 秒）：丢线程 + 缓存，别让一次缓存未命中把整个事件循环堵住
    # （堵住的表现就是别的页面一起卡 —— 真机看到的就是"一停一停的"）。
    g = await _asyncio.to_thread(_cached, "gate", pg.summary)
    pgst = await _asyncio.to_thread(_cached, "gate_status", pg_status)
    lp = await _asyncio.to_thread(_cached, "loops", loops_status)
    gsvg = await _asyncio.to_thread(_cached, "loops_graph_svg", lambda: graph_svg(lp))
    body = ("<h1>总能力 · 连接状态 · 精准用量</h1>"
            "<p class=muted>全部数字来自真实查询，逐格标注来源；查不到的写「无法确认」并给原因，"
            "绝不报假 0。所有能力页都在 V9 站内，不做外跳。</p>"
            + _chart_html(c, c.get("用量", {}), cm.solidify_status(), d, g, pgst, lp, gsvg)
            + await _asyncio.to_thread(_cached, "panels", panels_html))
    page = ("<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>GBT小土豆V9 · 总能力</title></head><body><main id=main>"
            + body + "</main></body></html>")
    return inject(page, "/capability")


# ═══════════ API ═══════════
@router.get("/api/panels")
async def api_panels():
    """能力控制面板逐项对齐审计（五查）。"""
    from core import capability_panels as CP
    return CP.audit()


@router.get("/api/panels/ui")
async def api_panels_ui():
    """按键统一静态核对：13 个页面是否都在统一体系内。"""
    from core import capability_panels as CP
    return CP.ui_unify()


@router.get("/api/capability/chart")
async def api_chart(db=Depends(get_db)):
    return await cm.chart(db, _led())


@router.get("/api/capability/usage")
async def api_usage(db=Depends(get_db)):
    return await cm.usage(db, _led())


@router.get("/api/capability/detail")
async def api_detail(db=Depends(get_db)):
    return await cm.detail(db, _led())


@router.get("/api/capability/solid")
async def api_solid():
    st = cm.solidify_status()
    st["history"] = {n: cm.chart_history() if n == cm.CHART_NAME else [] for n in st["names"]}
    return st


@router.post("/api/capability/solidify")
async def api_solidify(db=Depends(get_db)):
    return await cm.solidify_chart(db, _led(), note="面板点击固化")


@router.post("/api/capability/rollback")
async def api_rollback():
    return cm.chart_rollback()


# ═══════════ 能力面板总览（panel/capability.html 的两个口）═══════════
# 真机病因（2026-10-08 逐行复核）：capability.html:25 / :39 轮询 /api/capability/overview 与
# /api/capability/calls，而全仓从没有这两个路由 ⇒ 页面 JS 一开就抛，看着就是「能力全不见了」。
# 这里按该 html 期望的形状补上：只读、走 60s 缓存（不把事件循环堵住）、数字全是真读数。
@router.get("/api/capability/overview")
async def api_capability_overview():
    def build() -> dict:
        import shutil as _shutil
        from skills import NATIVE_CAPABILITIES
        from senses.sqldialect import txn as _txn
        led = _led()
        caps_rows: dict = {}
        health: dict = {}
        stats: dict = {}
        caps_n = 0
        try:
            from skills.caps.registry import build_caps_registry
            reg = build_caps_registry(ledger=led)
            caps_n = len(reg.caps)
            for cid in reg.caps:                       # cls 写成 octop:<id>，好让 html 取到 health
                caps_rows[cid] = {"cls": "octop:" + cid, "kind": "octop/offline",
                                  "backend": None}
            for cid, row in (reg.probe_all() or {}).items():
                if isinstance(row, dict):
                    health[cid] = {"ok": bool(row.get("done")),
                                   "reason": row.get("detail") or ""}
        except Exception as exc:                        # noqa: BLE001
            health["__probe__"] = {"ok": False,
                                   "reason": f"{type(exc).__name__}: {exc}"}
        for name, meta in NATIVE_CAPABILITIES.items():
            caps_rows[name] = dict(meta)
            cls = meta["cls"].split(":")[1]
            h = health.get(name) or health.get(cls) or {"ok": False, "reason": "未探测"}
            health[name] = h
            health.setdefault(cls, h)
        if led is not None:
            try:
                ph = "?" if getattr(led, "dialect", "sqlite") == "sqlite" else "%s"
                with _txn(led) as cur:
                    cur.execute("SELECT skill, COUNT(*) n, SUM(ok) ok, AVG(ms) ms "
                                "FROM skill_calls GROUP BY skill")
                    for r in cur.fetchall():
                        key = str(r[0])
                        stats[key.split("@")[0]] = {
                            "name": key, "calls": r[1], "ok": r[2],
                            "avg_ms": round(r[3] or 0),
                            "rate": (round((r[2] or 0) / r[1], 3) if r[1] else None)}
            except Exception as e:
                _swallow(__file__, e)
        return {
            "generated_at": _time.time(),
            "backends": {"ledger": bool(led),
                         "codex": bool(_shutil.which("codex")),
                         "archify": bool(_shutil.which("archify")),
                         "octop-offline": caps_n},
            "capabilities": caps_rows, "health": health, "stats": stats,
            "caps_total": caps_n, "native_total": len(NATIVE_CAPABILITIES),
        }
    try:
        return await _asyncio.to_thread(_cached, "cap_overview", build)
    except Exception as e:                                # noqa: BLE001
        return {"generated_at": _time.time(), "error": f"{type(e).__name__}: {e}",
                "backends": {}, "capabilities": {}, "health": {}, "stats": {}}


@router.get("/api/capability/calls")
async def api_capability_calls(limit: int = 50):
    def build() -> list:
        from senses.sqldialect import txn as _txn
        led = _led()
        if led is None:
            return []
        try:
            ph = "?" if getattr(led, "dialect", "sqlite") == "sqlite" else "%s"
            with _txn(led) as cur:
                cur.execute("SELECT ts, skill, ok, ms, tentacle, error FROM skill_calls "
                            f"ORDER BY ts DESC LIMIT {ph}", (int(limit),))
                rows = cur.fetchall()
            return [{"ts": r[0], "skill": r[1], "ok": bool(r[2]), "ms": r[3],
                     "tentacle": r[4], "error": r[5]} for r in rows]
        except Exception:                                 # noqa: BLE001
            return []
    try:
        return await _asyncio.to_thread(_cached, "cap_calls", build)
    except Exception:                                     # noqa: BLE001
        return []


@router.get("/api/compute/route")
async def api_route():
    return cr.report()


@router.get("/api/production")
async def api_production(deep: int = 0):
    """生产就绪度与根因台账。deep=1 时顺带做一次驱动深探（会真发一次请求）。"""
    if deep:
        r = await _asyncio.to_thread(pg.audit)
        _drop_cache("gate", "gate_status")
        return r
    return _cached("gate", pg.summary)


# ═══════════ 页面注册表 / 按键双向绑定 ═══════════
@router.get("/api/witness/plan")
async def api_witness_plan():
    """见证开通计划：每个候选 id 缺哪些环境变量（只读，不打印任何值）。"""
    from core import witness_onboard as WO
    return WO.plan()


@router.get("/api/witness/status")
async def api_witness_status(db=Depends(get_db)):
    """见证就绪度：登记数/有效票/要求数 + 下一步（面板与语音同口径）。"""
    from core import witness_onboard as WO
    return await WO.status(db)


@router.post("/api/witness/onboard")
async def api_witness_onboard(dry_run: int = 1, db=Depends(get_db)):
    """一键开通见证：对已配置的 id 逐个七步 dry-run，全绿才登记（dry_run=0 真登记）。"""
    from core import witness_onboard as WO
    return await WO.onboard(db, dry_run=bool(dry_run))


@router.get("/api/avatar/state")
async def api_avatar_state(clip: str = "idle", t: float = 0.0, speaking: int = 0, amp: float = 0.5,
                           mood: str = "", combo: str = ""):
    """数字人某一刻的姿态（内联 SVG）。**角色形象（全身绑骨）**，骨架版仅留作调试。

    combo 非空时走组合动作（一串动作按时相依次播放），否则按单动作。
    """
    from core import avatar_face as AF
    from core import avatar_rig as AR
    if combo:
        st = AR.sequence_state(combo, t, speaking=bool(speaking), amp=amp, mood=mood)
    else:
        st = AR.speak_state(clip=clip or AR.clip_for_mood(mood), t=t,
                            speaking=bool(speaking), amp=amp, mood=mood)
    eff = st.get("clip") or clip or "idle"
    svg = await _asyncio.to_thread(AF.character_svg, expression=(
        "happy" if st.get("speaking") else "neutral"), talking=bool(st.get("speaking")),
        blink=float(st.get("blink") or 0.0), clip=eff, t=float(t or 0.0))
    return {**st, "svg": svg, "关节数": len(AR.JOINTS),
            "形象": "占位示意图（avatar_face.character_svg：代码画的 UI 占位件）",
            "真身图": "/avatar-real.png",
            "真身资产": "state/tripo/out3/anime_girl_3d_model.glb（42 关节 · Blender 实机渲染）",
            "口径": "真身是 Tripo 的 3D 模型；character_svg 只做姿态示意图，不许当真身用"}


@router.get("/api/tripo/status")
async def api_tripo_status():
    """云上图生3D 能力位状态（只读）：CLI、密钥来源、余额、路线。"""
    from core import tripo as TP
    return await _asyncio.to_thread(TP.status)


@router.post("/api/tripo/make")
async def api_tripo_make(image: str = "", then: str = "texture,rig",
                         model: str = "tripo-v3.1"):
    """图（本地路径）→ 云上 3D（可选贴图/绑骨）。要跑几分钟，跑完返回模型路径。"""
    from core import tripo as TP
    if not image:
        raise HTTPException(status_code=400, detail="缺少 image 参数")
    p = (Path(image) if Path(image).is_absolute() else (_ROOT / image)).resolve()
    root = _ROOT.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="图片必须在项目目录内")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="找不到图片")
    return await _asyncio.to_thread(TP.make, p, then=then, model=model)


@router.post("/api/tripo/render")
async def api_tripo_render(glb: str = "", label: str = "model", angles: int = 8):
    """把云上模型拉进本地 Blender 影棚渲染（主图 + 转台 + 脸部特写）。"""
    from core import tripo as TP
    if not glb:
        raise HTTPException(status_code=400, detail="缺少 glb 参数")
    p = (Path(glb) if Path(glb).is_absolute() else (_ROOT / glb)).resolve()
    root = _ROOT.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="模型必须在项目目录内")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="找不到模型")
    return await _asyncio.to_thread(TP.render_asset, p, label=label,
                                    angles=max(4, min(16, angles)))


@router.get("/api/tripo/frame")
async def api_tripo_frame(name: str = ""):
    """取云模型的渲染图：只允许 state/tripo/render 下的 <label>_(hero|face|turnNN).png。"""
    from fastapi.responses import FileResponse
    import re as _re
    from core import tripo as TP
    n = str(name or "")
    if not _re.fullmatch(r"[A-Za-z0-9_]+_(hero|face|turn\d{2})\.png", n):
        raise HTTPException(status_code=404, detail="没有这张图")
    d = (_ROOT / "state" / "tripo" / "render").resolve()
    p = (d / n).resolve()
    try:
        p.relative_to(d)
    except ValueError:
        raise HTTPException(status_code=404, detail="没有这张图")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="没有这张图")
    return FileResponse(str(p), media_type="image/png")


@router.get("/api/tripo/labels")
async def api_tripo_labels():
    """列出已经渲染好的 3D 预览标签（页面用它切换转台图）。"""
    import glob as _glob
    import re as _re
    d = _ROOT / "state" / "tripo" / "render"
    labels = set()
    for f in _glob.glob(str(d / "*.png")):
        m = _re.match(r"([A-Za-z0-9_]+)_(hero|face|turn\d{2})\.png$", Path(f).name)
        if m:
            labels.add(m.group(1))
    return {"标签": sorted(labels)[::-1][:20]}


@router.get("/api/tripo/models")
async def api_tripo_models():
    """列出已经生成好的云模型（供页面挑一个来渲染/展示）。"""
    import glob as _glob
    out = []
    for f in sorted(_glob.glob(str(_ROOT / "state" / "tripo" / "out*" / "**" / "model.glb"),
                              recursive=True), key=lambda x: -__import__("os").path.getsize(x)):
        try:
            rel = str(Path(f).resolve().relative_to(_ROOT.resolve())).replace("\\", "/")
        except ValueError:
            continue
        out.append({"路径": rel, "大小MB": round(__import__("os").path.getsize(f) / 1048576, 1)})
    return {"模型": out[:12]}


@router.get("/api/blender/status")
async def api_blender_status():
    """blender 能力位状态（只读）：可执行、版本、Rigify、路线、不在册的能力。"""
    from core import blender as B
    st = await _asyncio.to_thread(B.status)
    st["动作"] = list(__import__("core.avatar_motion", fromlist=["CLIPS"]).CLIPS)
    return st


@router.post("/api/blender/preview")
async def api_blender_preview(clip: str = "wave", angles: int = 8, strips: int = 8):
    """渲染一个动作的 3D 预览（转台 + 动作序列）。跑一次 Blender，约半分钟。"""
    from core import blender as B
    return await _asyncio.to_thread(B.preview, clip, angles=max(4, min(24, angles)),
                                    strips=max(4, min(24, strips)), frames=20)


@router.get("/api/blender/frame")
async def api_blender_frame(name: str = ""):
    """取预览图：只允许 state/blender/preview 下的 <动作>_[af]NN.png（防目录穿越）。"""
    from fastapi.responses import FileResponse
    from core import blender as B
    p = B.frame_path(name)
    if p is None:
        raise HTTPException(status_code=404, detail="没有这张预览图")
    return FileResponse(str(p), media_type="image/png")


@router.get("/api/avatar/status")
async def api_avatar_status():
    from core import avatar_rig as AR
    return AR.status()


@router.get("/api/voice/status")
async def api_voice_status():
    """语音通道状态：OmniVoice(3900) / 本机 SAPI / 台湾腔落地方式。"""
    from core import voice_control as VC
    from senses import voice_sapi as VS
    return {"通道": VC.status(), "SAPI": VS.status()}


@router.post("/api/voice/say")
async def api_voice_say(payload: dict = Body(...)):
    """说一句（真播放）：优先 OmniVoice，退 SAPI。可带韵律样式（人格档位）。"""
    from core import voice_control as VC
    p = payload or {}
    st = str(p.get("style") or "").strip() or None
    return VC.speak(str(p.get("text") or ""), taiwan=bool(p.get("taiwan", True)), style=st)


@router.post("/api/pulse/selftest")
async def api_pulse_selftest():
    """万能插脉冲自检：真插插座 + 真分发 + 真落账（"调用 N 次"必须来自真动作）。"""
    import asyncio as _asyncio
    from core.pulse import Pulse

    def _run():
        led = None
        try:
            from panel.server import get_ledger
            led = get_ledger()
        except Exception as e:
            _swallow(__file__, e)
        return Pulse(ledger=led).selftest()

    return await _asyncio.to_thread(_run)


@router.post("/api/asr/selftest")
async def api_asr_selftest(payload: dict = Body(default_factory=dict)):
    """听写自证（免费离线）：TTS 合成 → ASR 转写回来 → 比对，并**真落一笔账**。

    落账的意义：面板上的"转写 N 次"必须来自真实发生过的转写，不能是装饰数字。
    """
    import asyncio as _asyncio
    from senses import voice_sapi as VS
    text = str((payload or {}).get("text") or "今天天气不错，请帮我看看这个专案。")
    r = await _asyncio.to_thread(VS.selftest, text)
    try:
        from panel.server import get_ledger
        led = get_ledger()
        if led is not None:
            with led._tx() as c:
                c.execute("""CREATE TABLE IF NOT EXISTS mic_segments(
                    seg_id TEXT PRIMARY KEY, tentacle_id TEXT, seq INTEGER,
                    t_start REAL, t_end REAL, ms INTEGER,
                    text TEXT, keywords TEXT, status TEXT, error TEXT,
                    audio_file TEXT, UNIQUE(tentacle_id, seq))""")
                c.execute("INSERT OR REPLACE INTO mic_segments"
                          "(seg_id,tentacle_id,seq,t_start,t_end,ms,text,keywords,status,error,audio_file)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          ("selftest-" + str(int(_time.time())), "selftest", 0,
                           _time.time(), _time.time(), r.get("ms") or 0,
                           str(r.get("转写") or ""), "", "done" if r.get("ok") else "failed",
                           str(r.get("reason") or ""), str(r.get("wav_bytes") or "")))
    except Exception as exc:                                  # noqa: BLE001
        r["落账失败"] = f"{type(exc).__name__}"
    try:
        from core import deploy_ledger as DL
        DL.record("scan", "asr:selftest", f"听写自证 命中率={r.get('命中率')}",
                  before="", after=str(r.get("转写"))[:120], ok=bool(r.get("ok")),
                  reason=str(r.get("reason") or ""))
    except Exception as e:
        _swallow(__file__, e)
    return r


@router.post("/api/voice/ref")
async def api_voice_ref(payload: dict = Body(...)):
    """设定台湾参考音（克隆用）：给一段台湾国语音频（5~20 秒）的路径。"""
    from pathlib import Path as _P
    import os as _os
    pth = str((payload or {}).get("path") or "").strip()
    if not pth or not _P(pth).is_file():
        return {"ok": False, "reason": "路径不存在；请给一段台湾国语音频文件的绝对路径"}
    ptr = _P(__file__).resolve().parent.parent.joinpath("state", "voice_ref.txt")
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(pth, encoding="utf-8")
    return {"ok": True, "参考音": pth, "生效": "下一次合成即用该腔调克隆（服务端热取）"}


@router.get("/api/loops")
async def api_loops():
    """闭环清单（轻量：不真跑驱动，只读证据）。带缓存：页面轮询不再反复跑重活。"""
    from core import loop_verifier as LV
    return _cached("loops", lambda: LV.summary())


@router.get("/api/loops/graph")
async def api_loops_graph():
    """闭环流程图（节点数据 + 内联 SVG，零外链）。带缓存。

    注意缓存键与页面那份**分开**：页面要的是 SVG 字符串（loops_graph_svg），
    这个接口要的是完整数据（graph/svg/节点数/连线数）。共用一个键会互相顶掉 ——
    真踩过：接口返回了一串 SVG，拿 d['节点数'] 的调用方全拿到 None。
    """
    from core import loop_graph as LG
    return _cached("loops_graph_data", lambda: LG.status())


@router.post("/api/loops/verify")
async def api_loops_verify():
    """逐条闭环**真验**一次（含真入队一次媒体任务、真扫一次蓝牙、真读驱动账本）。"""
    from core import loop_verifier as LV
    r = await _asyncio.to_thread(LV.verify_all, True)
    _drop_cache("loops", "loops_graph_svg", "loops_graph_data")   # 真验过 → 缓存作废，页面看到新结论
    return r


@router.get("/api/pages/registry")
async def api_pages_registry():
    from core import page_registry as PR
    return PR.catalog()


@router.get("/api/pages/status")
async def api_pages_status():
    from core import page_registry as PR
    return PR.status()


@router.get("/api/pages/audit")
async def api_pages_audit():
    from core import page_registry as PR
    return PR.audit()


@router.get("/api/pages/bindings")
async def api_pages_bindings():
    from core import page_registry as PR
    return {"bindings": PR.bindings(), "count": len(PR.bindings())}


@router.post("/api/pages/bind")
async def api_pages_bind():
    """执行按键↔能力双向绑定登记 + 固化（可回滚）。"""
    from core import page_registry as PR
    return PR.bind_all()


@router.post("/api/pages/scan")
async def api_pages_scan():
    from core import page_registry as PR
    return PR.scan()


@router.get("/api/compute/vram")
async def api_vram():
    """本地显存读数：云模式下预留恒 0（这是我们自己的预算账，不是 GPU 观测值）。"""
    pol = cr.policy()
    return {"channel": pol["channel"], "local_vram_reserved_mb": pol["local_vram_mb"],
            "requested_mb_env": cr.local_vram_reserved_mb(),
            "saved_local_vram_mb": cr.audit()["saved_local_vram_mb"],
            "workloads": len(cr.WORKLOADS),
            "口径": "本地预留 = 我们自己的显存预算账（云模式下恒 0）；"
                    "真实 GPU 观测另见 /api/media/vram"}
