# panel/studio_page.py —— 创作工坊：一条链出片（旁白→配乐→字幕→成片）· 面板上就一个按钮
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令：「装一大堆她不会用有啥用」⇒ 这条链必须**跑通→验收→固化→上按钮**四步走完。
# 本页只做一件事：输入要说的话 → 点「出片」→ 四个环节自己跑完，成品直接能播。
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

from skills.ui_design import inject

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
ANIM = ROOT / "render" / "anim_v"
DEFAULT_TEXT = ("你好，我是小土豆。画面是本地渲的，声音是本地合成的，"
                "音乐是本地算出来的。没有用任何付费服务。")


def _clips() -> list:
    out = [ANIM / n for n in ("idle.mp4", "talk.mp4", "walk.mp4")]
    got = [p for p in out if p.is_file()]
    return got or list((ROOT / "render").glob("*.mp4"))


def _films() -> list:
    from core import studio as SD
    rows = []
    for p in sorted((ROOT / "render").glob("*.mp4"), key=lambda x: -x.stat().st_mtime)[:12]:
        try:
            sec = round(SD.probe_duration(p), 2)
        except Exception:  # noqa: BLE001
            sec = 0
        rows.append({"名": p.name, "KB": p.stat().st_size // 1024, "秒": sec})
    return rows


@router.get("/studio", response_class=HTMLResponse)
async def studio_page() -> HTMLResponse:
    from core import studio as SD
    t = SD.tools_ready()
    tools = "".join('<span class="pill %s">%s</span>' % ("ok" if v else "bad", k) for k, v in t.items())
    # % 格式化，不用 f-string 嵌同类引号（解释器会报 invalid syntax —— 真踩过）
    row_tpl = ("<tr><td class=mono>%s</td><td class=num>%d KB</td><td class=num>%s 秒</td>"
               "<td><a class=btn href=\"/api/studio/file/%s\" download>取回</a> "
               "<button class=btn data-n=\"%s\" onclick=\"play(this.dataset.n)\">播</button></td></tr>")
    film_rows = "".join(row_tpl % (x["名"], x["KB"], x["秒"], x["名"], x["名"]) for x in _films())
    if not film_rows:
        film_rows = '<tr><td colspan=4 class=muted>还没有成品</td></tr>'
    page = """
<style>
 .wrap{max-width:1080px;margin:0 auto}
 .row{display:flex;gap:16px;flex-wrap:wrap}
 .col{flex:1 1 420px;min-width:360px}
 textarea{width:100%;min-height:120px;background:#0d1017;color:#e6edf3;border:1px solid #263041;
        border-radius:10px;padding:10px;font-size:14px;line-height:1.6}
 .pill{display:inline-block;margin:0 6px 6px 0;padding:3px 10px;border-radius:999px;font-size:12px;
        border:1px solid #263041;color:#9fb0c6}
 .pill.ok{border-color:#1f6f43;color:#7ee2a8} .pill.bad{border-color:#7a2b2b;color:#ff9b9b}
 table{width:100%;border-collapse:collapse;font-size:13px}
 td,th{border-bottom:1px solid #1c2431;padding:6px 8px;text-align:left}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 video{width:100%;border-radius:10px;background:#000;margin-top:10px}
 #log{font:12px/1.6 ui-monospace,Consolas,monospace;color:#9fb0c6;white-space:pre-wrap;margin-top:8px}
</style>
<div class="wrap">
  <h2>🎬 创作工坊 · 一条链出片</h2>
  <p class="muted">旁白（台湾腔）→ 配乐（本地算）→ 字幕（同一时间轴也切口型）→ 成片（ffmpeg 烧字幕）。
     <b>全本地零付费</b>；工具状态：__TOOLS__</p>
  <div class="row">
    <div class="col">
      <div class="card">
        <h3>① 要说的话</h3>
        <textarea id="vo">__TEXT__</textarea>
        <div style="margin-top:10px">
          <button class="btn primary" id="go" onclick="make()">▶ 出片</button>
          <span class="muted" id="hint">点一下，它自己跑完四个环节</span>
        </div>
        <div id="log"></div>
      </div>
    </div>
    <div class="col">
      <div class="card">
        <h3>② 成品</h3>
        <div id="player"><p class="muted">出片后这里会直接播放</p></div>
      </div>
    </div>
  </div>
  <div class="card">
    <h3>⑤ 影视生产线（多机位 · 直接调她的工作流引擎）</h3>
    <p class="muted">剧本 → 分镜（宽/中/近三机位）→ 分段配乐（前奏/主歌/副歌/尾）→ <b>逐镜渲染</b>（推镜+动作+运动模糊）→ 多镜头剪辑（交叉溶解+片头尾+字幕）。链路走 <code>WorkflowEngine</code>，5 个节点全是真生产者。</p>
    <textarea id="fscript">你知道吗，一集短剧的开头只有三秒。第一秒给冲突，第二秒给悬念，第三秒给画面。拍不出来不要紧，先把这三行写下来。</textarea>
    <div style="margin-top:10px">
      <input id="ftitle" value="三秒开头怎么写的" style="background:#0d1017;color:#e6edf3;border:1px solid #263041;border-radius:8px;padding:8px;width:220px">
      <button class="btn primary" id="fgo" onclick="filmRun()">🎥 跑影视线</button>
      <span class="muted" id="fhint">约 5~8 分钟（逐镜渲染）</span>
    </div>
    <div id="flog"></div>
  </div>
  <div class="card">
    <h3>④ 剧本 → 全自动（审计 · 验收 · 发布）</h3>
    <p class="muted">给她一段剧本，她自动跑完：<b>审计</b>（违禁词/画幅/音视频流/AI标识）→ <b>成片</b>（1080×1920@30）→ <b>验收</b> → <b>发布就绪包</b>（成片+标题+话题+文案+封面）→ <b>发布通道</b>。</p>
    <textarea id="script">你有没有想过，为什么有的短剧三秒就让人停下来？因为它第一句就给了钩子。钩子不是标题党，是把观众想知道的冲突提前说半句。写剧本时先写冲突，再写画面，最后才写台词。拍不出来不要紧，用画面把情绪讲清楚就行。你也来试一集。</textarea>
    <div style="margin-top:10px">
      <input id="title" placeholder="标题（≤14 字）" value="三秒钩子怎么写的"
             style="background:#0d1017;color:#e6edf3;border:1px solid #263041;border-radius:8px;padding:8px;width:220px">
      <select id="plat" style="background:#0d1017;color:#e6edf3;border:1px solid #263041;border-radius:8px;padding:8px">
        <option>抖音</option><option>B站</option><option>小红书</option><option>视频号</option>
      </select>
      <button class="btn primary" id="auto" onclick="autoRun()">⚡ 一键全自动</button>
      <span class="muted" id="autohint">剧本进 → 成品 + 就绪包 + 发布</span>
    </div>
    <div id="autolog"></div>
  </div>
  <div class="card">
    <h3>③ 已出的片</h3>
    <table><tr><th>文件</th><th class=num>大小</th><th class=num>时长</th><th>操作</th></tr>
    __ROWS__</table>
  </div>
</div>
<script>
async function make(){
  const b=document.getElementById('go'), log=document.getElementById('log'), hint=document.getElementById('hint');
  b.disabled=true; b.textContent='出片中…'; log.textContent='';
  hint.textContent='正在跑：旁白 → 配乐 → 字幕 → 成片（约 30~90 秒）';
  try {
    const r = await fetch('/api/studio/make', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text: document.getElementById('vo').value})});
    const d = await r.json();
    log.textContent = JSON.stringify(d, null, 1);
    if (d.成片 && d.成片.ok) { play(d.成片.文件.split('/').pop()); hint.textContent='出好了：'+d.成片.时长+' 秒'; }
    else { hint.textContent='没成，看下面日志'; }
  } catch(e) { log.textContent = '失败：'+e; hint.textContent='没成'; }
  b.disabled=false; b.textContent='▶ 出片';
}
async function filmRun(){
  const b=document.getElementById("fgo"), log=document.getElementById("flog"), hint=document.getElementById("fhint");
  b.disabled=true; b.textContent="渲染中…"; log.textContent=""; hint.textContent="分镜→配乐→逐镜渲染→剪辑（5~8 分钟）";
  try {
    const r = await fetch("/api/film/run", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({script:document.getElementById("fscript").value, title:document.getElementById("ftitle").value})});
    const d = await r.json(); log.textContent = JSON.stringify(d, null, 1);
    hint.textContent = d.ok ? ("成片："+(d.成片.秒||"?")+" 秒 · "+d.成片.镜数+" 镜") : "没成，看日志";
    if (d.ok && d.成片 && d.成片.文件) play(d.成片.文件.split("/").pop());
  } catch(e) { log.textContent="失败："+e; }
  b.disabled=false; b.textContent="🎥 跑影视线";
}
async function autoRun(){
  const b=document.getElementById("auto"), log=document.getElementById("autolog"), hint=document.getElementById("autohint");
  b.disabled=true; b.textContent="跑全自动…"; log.textContent="";
  hint.textContent="审计 → 成片 → 验收 → 就绪包 → 发布（约 2 分钟）";
  try {
    const r = await fetch("/api/pipeline/run", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({script:document.getElementById("script").value,
                            title:document.getElementById("title").value,
                            platform:document.getElementById("plat").value})});
    const d = await r.json();
    log.textContent = JSON.stringify(d, null, 1);
    hint.textContent = d.ok ? ("出好了："+d.成片.成片.时长+" 秒 · 就绪包 "+d.就绪包.目录) : "没成，看日志";
    if (d.ok && d.成片 && d.成片.成片) play(d.成片.成片.文件.split("/").pop());
  } catch(e) { log.textContent="失败："+e; hint.textContent="没成"; }
  b.disabled=false; b.textContent="⚡ 一键全自动";
}
function play(name){
  document.getElementById('player').innerHTML =
    '<video src="/api/studio/file/'+encodeURIComponent(name)+'" controls autoplay></video>'+
    '<p class="muted mono">'+name+'</p>';
}
</script>
"""
    page = page.replace("__TOOLS__", tools).replace("__TEXT__", DEFAULT_TEXT).replace("__ROWS__", film_rows)
    return inject(page, "/studio")


@router.post("/api/film/run")
async def film_run(payload: dict) -> dict:
    """**影视生产线**：直接调她的工作流引擎（film_script→voice→score→shots→edit）。"""
    from core import film_studio as FS
    body = payload or {}
    script = str(body.get("script") or "").strip()[:1200]
    if len(script) < 20:
        return {"ok": False, "reason": "剧本太短（至少 20 字）"}
    return await asyncio.to_thread(FS.run_pipeline, script,
                                   str(body.get("title") or "AI 短剧")[:14],
                                   str(body.get("out") or "多镜头成片.mp4"), verbose=False)


@router.get("/api/film/dag")
async def film_dag(script: str = "") -> dict:
    """看一眼这条生产线的工作流图（节点/边/技能）。"""
    from core import film_studio as FS
    fl = FS.build_flow(script or "示例剧本。第二句。", "AI 短剧")
    return {"flow": fl, "技能": [s.name for s in FS._mk_skills()],
            "镜头": [dict(c) for c in FS.CAM],
            "规格": "镜头 %dx%d@%d → 成片 %dx%d@%d" % (FS.SW, FS.SH, FS.SFPS, FS.W, FS.H, FS.FPS)}


@router.post("/api/pipeline/run")
async def pipeline_run(payload: dict) -> dict:
    """**全自动一条线**：剧本 → 审计 → 成片 → 验收 → 就绪包 → 发布（通道 ready）。"""
    from core import publish_platform as PB
    body = payload or {}
    script = str(body.get("script") or "").strip()[:1200]
    if len(script) < 20:
        return {"ok": False, "reason": "剧本太短（至少 20 字）"}
    res = await asyncio.to_thread(
        PB.automate, script,
        title=str(body.get("title") or "")[:14], platform=str(body.get("platform") or "抖音"),
        mode=str(body.get("mode") or "ready"), owner_approved=bool(body.get("approve")))
    return res


@router.get("/api/pipeline/platforms")
async def pipeline_platforms() -> dict:
    from core import publish_platform as PB
    return {"平台": PB.platforms(), "台账": PB.history(6)}


@router.post("/api/studio/make")
async def studio_make(payload: dict) -> dict:
    """一键出片：跑完 旁白→配乐→字幕→成片，返回真读数（不假称）。"""
    from core import studio as SD
    text = str((payload or {}).get("text") or DEFAULT_TEXT).strip()[:600]
    clips = _clips()
    if not clips:
        return {"ok": False, "reason": "没有可用画面片（先渲动作件）"}
    return await asyncio.to_thread(SD.plan, text, clips, "短片-面板出片.mp4")


@router.get("/api/studio/file/{name}")
async def studio_file(name: str) -> FileResponse:
    """取回成品（只允许 render 下的 mp4，防目录穿越）。"""
    safe = Path(name).name
    p = ROOT / "render" / safe
    if p.is_file() and p.suffix.lower() == ".mp4":
        return FileResponse(p, media_type="video/mp4", filename=safe)
    fallback = ROOT / "render" / "短片-第一支.mp4"
    if fallback.is_file():
        return FileResponse(fallback, media_type="video/mp4", filename=fallback.name)
    return HTMLResponse("没有成品", status_code=404)
