# panel/terminal_page.py —— AI 终端对话面板（命令行形态，全品牌 GBT小土豆V9）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：补一个"AI 终端对话面板" —— 形态是终端（提示符 + 命令 + 回显），
#   品牌统一 GBT小土豆V9。
# 命令走**服务端白名单派发**（不碰 shell、不 eval、不拼命令），每条都落到真件：
#   help / status / whoami / life / reflect
#   ask <话>        → AI 指挥读数链
#   brain <话>      → 原生大脑召回（带出处与“不确定”）
#   remember <话>   → 捕捉进大脑
#   recall <话>     → 大脑召回（只列出处）
#   agents [词]     → Octop 名册点名
#   workflows [名]  → 工作流状态 / 闸门 / 验收
#   tools           → 只读工具面
#   clear           → 清屏（本地）
from core.swallow import swallow as _swallow
import asyncio as _a
import time

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse

from skills.ui_design import Page

router = APIRouter()
BRAND = "GBT小土豆V9"

HELP = (
    "可用命令：\n"
    "  help                     看这份说明\n"
    "  status                   一屏状态（大脑 / 编队 / 闭环 / 固化）\n"
    "  whoami                   我是谁（品牌与归属）\n"
    "  ask <话>                 AI 指挥读数链（只转述读数）\n"
    "  brain <话>               原生大脑召回（带出处；答不上来会说）\n"
    "  remember <话>            把一句话记进大脑（原文立即入库）\n"
    "  recall <话>              大脑召回（只列出处与打分）\n"
    "  agents [关键词]          Octop 智能体名册点名\n"
    "  workflows [名字]         工作流：闸门 / 验收 / 阶段\n"
    "  tools                    只读工具面清单\n"
    "  clear                    清屏"
)


def _t(text: str, cls: str = "") -> dict:
    return {"text": str(text), "cls": cls}


def _run(cmd: str, arg: str, *, owner: str = "main") -> list:
    """白名单派发。返回一组 {text, cls} 行，供终端着色打印。"""
    c = (cmd or "").strip().lower()
    if c in ("", "help", "?", "h"):
        return [_t(HELP, "dim")]
    if c == "whoami":
        return [_t(f"{BRAND} · 原生大脑 + 指挥链终端", "ok"),
                _t("会话归属：主人的指挥台；数据只在本机（私密记忆不外发）", "dim")]
    if c == "clear":
        return [{"clear": True}]
    if c == "status":
        out = []
        try:
            from core.memory import brain as B
            st = B.status()
            out.append(_t(f"大脑：记忆 {st['统一记忆']['记忆']} 条 · 生平 {st['生命起源存档']['生平条数']} 条 · "
                          f"向量通道 {st.get('向量通道')}", "ok"))
            out.append(_t(f"主体：{st['主体']} · 分类：{st['分类']}", "dim"))
        except Exception as exc:                               # noqa: BLE001
            out.append(_t(f"大脑读数失败：{type(exc).__name__}", "err"))
        try:
            from core import loop_verifier as LV
            s = LV.summary()
            out.append(_t(f"闭环：{s.get('已跑通')}/{s.get('闭环总数')} 就绪度 {s.get('就绪度')}", "ok"))
        except Exception as exc:                               # noqa: BLE001
            out.append(_t(f"闭环读数失败：{type(exc).__name__}", "err"))
        try:
            from core import solidify as S
            out.append(_t(f"固化组：{len(S.status().get('names') or [])} 组（可回滚）", "dim"))
        except Exception as e:
            _swallow(__file__, e)
        try:
            from core import deploy_ledger as DL
            out.append(_t(f"台账：{DL.summary().get('by_kind')}", "dim"))
        except Exception as e:
            _swallow(__file__, e)
        return out
    if c == "ask":
        if not arg:
            return [_t("用法：ask <你要问的话>", "warn2")]
        from core import action_loop as AL
        try:
            r = AL.handle(arg)
        except Exception as exc:                               # noqa: BLE001
            return [_t(f"指挥链调用失败：{type(exc).__name__}: {exc}", "err")]
        say = (r or {}).get("say") or (r or {}).get("reply") or "（没给出可读的话）"
        return [_t(str(say), "ok")]
    if c == "brain":
        if not arg:
            return [_t("用法：brain <你要问的话>", "warn2")]
        from core.memory import brain as B
        r = B.ask(arg, owner=owner, all_owners=True)
        lines = [_t(r.get("answer") or "（空）", "ok" if r.get("confident") else "warn2")]
        for s in (r.get("sources") or [])[:5]:
            lines.append(_t(f"  ↳ {s['id']} · {s['谁']} · {s['分类'] or '未分类'} · "
                            f"打分 {s['打分']} · 命中 {'/'.join(s['命中路子'] or [])}", "dim"))
        return lines
    if c == "remember":
        if not arg:
            return [_t("用法：remember <要记的话>", "warn2")]
        from core.memory import brain as B
        r = B.remember(arg, owner=owner)
        if not r.get("ok"):
            return [_t(f"没记住：{r.get('reason')}", "err")]
        return [_t(f"已记住 {r['id']}（{r['scope']}）—— 理解在后台进行", "ok")]
    if c == "recall":
        if not arg:
            return [_t("用法：recall <关键词>", "warn2")]
        from core.memory import recall as R, store as S
        got = R.ask(arg, owner=owner, all_owners=True, limit=8, st=S.store())
        if not got.get("sources"):
            return [_t("没有相关记忆（" + (got.get("unknown_reason") or "没存过") + "）", "warn2")]
        return [_t(f"命中 {len(got['sources'])} 条：", "ok")] + [
            _t(f"  ↳ {s['id']} · {s['原文片段'][:40]} · {s['谁']} · 打分 {s['打分']}", "dim")
            for s in got["sources"]]
    if c == "agents":
        from core import agent_chat as AC
        if arg:
            p = AC.pick_agent(arg)
            return [_t(f"点名：{p.get('agent')}" if p.get("ok") else f"没找到「{arg}」", "ok" if p.get("ok") else "warn2")]
        ro = AC.roster()
        c_ = ro.get("counts") or {}
        names = [a.get("name") for a in (ro.get("agents") or [])[:12]]
        return [_t(f"名册：{c_.get('divisions')} 部门 / {c_.get('agents')} 智能体 / {c_.get('experts')} 专家", "ok"),
                _t("样例：" + "、".join(str(n) for n in names), "dim")]
    if c in ("workflows", "wf"):
        from core import workflows as W
        st = W.status()
        if arg:
            hit = next((r for r in st["清单"] if r["id"] in arg.lower() or arg in r["名称"]), None)
            if not hit:
                return [_t(f"没有这条工作流；可用：{'、'.join(r['名称'] for r in st['清单'])}", "warn2")]
            g = hit["调研闸门"]
            return [_t(f"{hit['名称']}（{hit['阶段数']} 段）", "ok"),
                    _t(f"  调研闸门：{'已放行' if g['allowed'] else '未开'} · {g.get('reason') or ''}",
                       "ok" if g["allowed"] else "warn2"),
                    _t(f"  验收：已通过 {hit['验收']['已通过']} / 待采证 {hit['验收']['待采证']} / "
                       f"标准 {hit['验收']['标准数']}", "dim")]
        return [_t(f"工作流 {st['工作流数']} 条（放行 {st['放行数']} / 阻塞 {st['阻塞数']}）", "ok")] + [
            _t(f"  ↳ {r['名称']} · {r['阶段数']} 段 · 闸门{'开' if r['调研闸门']['allowed'] else '关'}", "dim")
            for r in st["清单"]]
    if c == "tools":
        from body.tools.base import TOOLS
        return [_t("只读工具：" + "、".join(sorted(TOOLS)), "ok"),
                _t("用法：在 /chat 面板的「工具」模式里按名字问读数", "dim")]
    return [_t(f"不认识的命令：{cmd}（help 看用法）", "err")]


def _command(line: str) -> dict:
    parts = str(line or "").strip().split(None, 1)
    cmd = parts[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    return {"cmd": cmd, "arg": arg}


def _history_path():
    from pathlib import Path
    import os
    root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return root.joinpath("state", "terminal_history.jsonl")


def _log(line: str, ok: bool) -> None:
    try:
        import json
        p = _history_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "line": str(line)[:2000], "ok": bool(ok)},
                               ensure_ascii=False) + "\n")
    except OSError as e:
        _swallow(__file__, e)


def _history(limit: int = 60) -> list:
    import json
    p = _history_path()
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if v is not None else "")


JS = """
const H=[];
function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function line(text,cls){ H.push({text:text,cls:cls||''}); render(); }
function render(){
  const box=document.getElementById('term');
  box.innerHTML=H.map(h=>h.cls?('<div class="'+h.cls+'">'+esc(h.text)+'</div>')
    :('<div>'+esc(h.text)+'</div>')).join('')
    +'<div class=termline><span class=p>'+BRANDPROMPT+'&gt;</span>'
    +'<input id=tin placeholder="输入命令…（help 看用法；↑↓ 翻历史）" autofocus>'
    +'<button class="btn primary" onclick="run()">执行</button></div>';
  box.scrollTop=box.scrollHeight;
  const inp=document.getElementById('tin');
  inp.addEventListener('keydown',e=>{
    if(e.key==='Enter') run();
    if(e.key==='ArrowUp'){ e.preventDefault(); if(HIDX>0){HIDX--; inp.value=HIST[HIDX]||'';} }
    if(e.key==='ArrowDown'){ e.preventDefault(); if(HIDX<HIST.length-1){HIDX++; inp.value=HIST[HIDX]||'';} else {HIDX=HIST.length; inp.value='';} } });
}
let HIST=[],HIDX=0; const BRANDPROMPT='gbt@v9';
async function boot(){
  const d=await (await fetch('/api/terminal/history?limit=40')).json();
  line(BRANDPROMPT+' 终端就绪 · '+d['品牌'],'ok');
  line('输入 help 看命令；这里只做真事，不编数。','dim');
  (d.rows||[]).slice(-8).forEach(r=>{ line((BRANDPROMPT+'> ')+r.line,'dim'); });
  HIST=(d.rows||[]).map(r=>r.line); HIDX=HIST.length;
}
async function run(){
  const inp=document.getElementById('tin'); const v=(inp.value||'').trim(); if(!v) return;
  line(BRANDPROMPT+'> '+v,'p'); HIST.push(v); HIDX=HIST.length;
  inp.value='';
  const d=await fetch('/api/terminal/run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({line:v})}).then(r=>r.json());
  (d.lines||[]).forEach(l=>{ if(l.clear){ H.length=0; render(); return; } line(l.text,l.cls); });
}
document.addEventListener('keydown',e=>{ if(e.key==='Enter'&&e.target&&e.target.id==='tin') run(); });
boot();
"""


@router.get("/terminal", response_class=HTMLResponse)
async def terminal_page():
    body = (
        '<div class=card><h2>🖥 ' + BRAND + ' · AI 终端</h2>'
        '<p class=muted>终端形态的指挥入口：命令走**服务端白名单派发**（不碰 shell / 不 eval / 不拼命令），'
        '每条都落到真件 —— 大脑召回、指挥读数、工作流闸门、只读工具。答不上来会直说。</p>'
        '<div class=term id=term></div>'
        '<div class=row style="margin-top:8px">'
        '<button class=btn onclick="run2(\'help\')">help</button>'
        '<button class=btn onclick="run2(\'status\')">status</button>'
        '<button class=btn onclick="run2(\'agents\')">agents</button>'
        '<button class=btn onclick="run2(\'workflows\')">workflows</button>'
        '<button class=btn onclick="run2(\'tools\')">tools</button>'
        '<button class=btn onclick="run2(\'whoami\')">whoami</button></div></div>')
    extra = JS + "\nfunction run2(c){ const i=document.getElementById('tin'); i.value=c; run(); }\n"
    return Page(title=f"{BRAND} · AI 终端", body=body, current="/terminal",
                extra_js=extra).render()


# ═══════════════ API ═══════════════
@router.post("/api/terminal/run")
async def api_run(payload: dict = Body(default_factory=dict)):
    line = str((payload or {}).get("line") or "").strip()
    if not line:
        return {"ok": False, "reason": "空命令", "lines": []}
    parsed = _command(line)
    try:
        lines = await _a.to_thread(_run, parsed["cmd"], parsed["arg"])
    except Exception as exc:                                   # noqa: BLE001
        lines = [_t(f"命令执行失败：{type(exc).__name__}: {exc}", "err")]
    await _a.to_thread(_log, line, bool(lines) and not (lines[0].get("cls") == "err"))
    return {"ok": True, "品牌": BRAND, "cmd": parsed["cmd"], "lines": lines}


@router.get("/api/terminal/history")
async def api_history(limit: int = 60):
    return {"ok": True, "品牌": BRAND, "rows": await _a.to_thread(_history, limit)}


@router.get("/api/terminal/help")
async def api_help():
    return {"ok": True, "品牌": BRAND, "help": HELP}


__all__ = ["router"]
