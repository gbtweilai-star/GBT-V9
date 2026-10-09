# panel/dh_companion.py —— 她的"任意页面"伴随件 + 首启密钥闸
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）：「数字人页面要设计在下载 APP 之后跳出配置密钥，然后就是数字人出现，
#   数字人可以出现在任意页面协助用户」。
# 落地方式（不新造注入机制）：仓里 `skills/ui_design.inject()` 已经在**每一个页面**注入她的
#   页面执行器（id=v9ctl）与问一句 dock（id=aiask）—— 本模块就挂在同一处，于是：
#   ① 任一页面右下角都有她（脸 + 按住说话 + 展开到交互台）；
#   ② 首次启动（还没配密钥）先弹「配置密钥」闸，配完她才出现 —— 顺序就是主人要的那句。
# 凭据纪律（照 core/local_secret 与 tentacle_identity 的口径，不破）：
#   · 面板**不回显**任何密钥原文，只报「已配 / 未配」与前 4 位掩码；
#   · 落盘在 state/keys.env（state/ 已 gitignore，权限尽力 0600），不是仓库里、不是源码里。
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Body
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
KEYS_ENV = ROOT / "state" / "keys.env"
# 首启要配的几把钥匙：名字 → 说明（真名以 .env.example 与 core/local_secret 为准）
NEEDED = (
    ("OPENAI_API_KEY", "主脑/触手的 LLM 通道（也可指向本机 8317 网关）"),
    ("OPENAI_BASE_URL", "网关地址（默认 http://127.0.0.1:8317/v1）"),
    ("GUI_GRANT_SECRET", "授权令牌签名密钥（跨进程要用同一把；留空则自动落盘生成）"),
    ("CLOUDFLARE_API_TOKEN", "云插件/Cloudflare 用（可留空，留空即未配）"),
    ("TENTACLE_T001_KEY", "触手 t001 自己的钥匙（留空则回退统一密钥，如实标注）"),
)

router = APIRouter()


def _load_env_file() -> dict:
    out = {}
    if KEYS_ENV.is_file():
        try:
            for ln in KEYS_ENV.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
        except OSError as e:
            _swallow(__file__, e)

    return out


def apply_env() -> dict:
    """把落盘的钥匙装进当前进程环境（启动时调一次；env 已有值不覆盖）。"""
    got = _load_env_file()
    applied = []
    for k, v in got.items():
        if v and not os.environ.get(k):
            os.environ[k] = v
            applied.append(k)
    return {"ok": True, "装了": applied, "文件": str(KEYS_ENV)}


def _mask(v: str) -> str:
    v = str(v or "")
    return (v[:4] + "…" + v[-2:]) if len(v) > 8 else ("（已配）" if v else "")


def status() -> dict:
    """首启闸的判据：**从不回显原文**，只报已配/未配与前 4 位掩码。"""
    file_env = _load_env_file()
    rows, missing = [], []
    for k, why in NEEDED:
        v = os.environ.get(k) or file_env.get(k) or ""
        rows.append({"键": k, "说明": why, "已配": bool(v), "掩码": _mask(v)})
        if k != "OPENAI_BASE_URL" and k != "CLOUDFLARE_API_TOKEN" and k != "TENTACLE_T001_KEY" and not v:
            missing.append(k)          # 只有主通道与签名密钥算"必须先配"
    need = [r for r in rows if r["键"] in ("OPENAI_API_KEY", "GUI_GRANT_SECRET") and not r["已配"]]
    return {"需要配置": bool(need), "必须先配": [r["键"] for r in need],
            "行": rows, "文件": str(KEYS_ENV),
            "口径": "密钥只落 state/keys.env（gitignore），面板不回显原文，只给掩码"}


@router.get("/api/setup/status")
async def setup_status() -> dict:
    return status()


@router.post("/api/setup/keys")
async def setup_keys(payload: dict = Body(default={})):
    """把主人填的钥匙落 state/keys.env（0600）并装进当前进程。空值不写、不删既有。"""
    items = {k: str(v).strip() for k, v in (payload or {}).items()
             if k in dict(NEEDED) and str(v).strip()}
    if not items:
        return {"ok": False, "reason": "没收到任何键值"}
    cur = _load_env_file()
    cur.update(items)
    KEYS_ENV.parent.mkdir(parents=True, exist_ok=True)
    KEYS_ENV.write_text("\n".join(f"{k}={v}" for k, v in sorted(cur.items())) + "\n",
                        encoding="utf-8")
    try:
        os.chmod(KEYS_ENV, 0o600)
    except OSError as e:
        _swallow(__file__, e)

    for k, v in items.items():
        os.environ[k] = v
    # ★主人设计：密钥配好那一刻 → 触手收到启动信号，自己把自己配好（专业/装备/账号/云终端）
    boot = {"ok": False, "reason": "未触发"}
    try:
        import asyncio as _aio
        from core import tentacle_bootstrap as TB
        # ★必须丢到线程里跑：自举内部要 asyncio.run（云插件绑定是异步端口），
        #   在 async 处理函数里直接跑会 "asyncio.run() cannot be called from a running event loop"。
        boot = await _aio.to_thread(TB.run, TB.DEFAULT_N)
    except Exception as exc:                                # noqa: BLE001
        boot = {"ok": False, "reason": f"自举没跑成：{type(exc).__name__}"}
    return {"ok": True, "写了": sorted(items), "文件": str(KEYS_ENV),
            "下一步": "她已就位：右下角点她即可说话，或打开 /digital-human/console",
            "触手自举": {k: boot.get(k) for k in
                        ("自举后已立", "这次新立", "跳过（本来就立了）", "立失败", "云终端")} if boot.get("ok") else boot}


CSS = """<style>
#dhc{position:fixed;right:18px;bottom:18px;z-index:9998;display:flex;align-items:center;gap:10px;
  font:13px/1.5 system-ui,"Microsoft YaHei",sans-serif}
#dhc .face{width:56px;height:56px;border-radius:50%;overflow:hidden;cursor:pointer;
  border:1px solid rgba(57,208,255,.45);box-shadow:0 0 18px rgba(57,208,255,.25);flex:0 0 auto;
  background:#0a1220;transition:box-shadow .25s ease}
#dhc .face img{width:100%;height:100%;object-fit:cover;display:block}
#dhc.on .face{box-shadow:0 0 34px rgba(57,208,255,.6)}
#dhc .box{max-width:min(46vw,420px);background:rgba(8,14,24,.94);border:1px solid #16263a;
  border-radius:12px;padding:8px 10px;color:#dbe7f5;display:none}
#dhc.open .box{display:block}
#dhc .box .t{font-size:12.5px;color:#9fb2cc;max-height:84px;overflow:auto}
#dhc .box a{color:#39d0ff;text-decoration:none;font-size:12px}
#setupgate{position:fixed;inset:0;background:rgba(3,6,12,.86);z-index:9999;display:none;
  align-items:center;justify-content:center;font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}
#setupgate.on{display:flex}
#setupgate .card{width:min(560px,92vw);max-height:86vh;overflow:auto;background:#0a1220;
  border:1px solid #1d3350;border-radius:16px;padding:20px 22px;color:#dbe7f5}
#setupgate h2{margin:0 0 6px;font-size:17px}
#setupgate p{color:#8fa3bf;font-size:12.5px;margin:0 0 12px}
#setupgate label{display:block;font-size:12px;color:#8fa3bf;margin:10px 0 3px}
#setupgate input{width:100%;background:#0d1524;color:#e8f0fb;border:1px solid #1d3350;
  border-radius:8px;padding:7px 9px;font:inherit}
#setupgate .row{display:flex;gap:10px;align-items:center;margin-top:14px}
</style>"""

JS = r"""<script>
(function(){
  var root=document.getElementById('dhc');
  if(!root) return;
  var face=root.querySelector('.face'), box=root.querySelector('.box');
  var sayEl=document.getElementById('dhc-say');
  var talking=false;
  function glow(on){ talking=on; root.classList.toggle('on',!!on); }
  face.addEventListener('click',function(){ root.classList.toggle('open'); });
  var ptt=document.getElementById('dhc-ptt'), REC=null, CH=[], ON=false;
  function start(){ if(ON) return;
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(s){
      CH=[]; REC=new MediaRecorder(s);
      REC.ondataavailable=function(e){ if(e.data.size) CH.push(e.data); };
      REC.onstop=function(){ s.getTracks().forEach(function(t){t.stop();}); send(); };
      REC.start(); ON=true; root.classList.add('open'); glow(true); sayEl.textContent='（我在听…）';
    }).catch(function(e){ sayEl.textContent='麦克风拿不到：'+e.message; });
  }
  function stop(){ if(!ON) return; ON=false; try{ REC.stop(); }catch(e){} }
  function send(){ if(!CH.length){ glow(false); return; }
    fetch('/api/voice/ptt',{method:'POST',headers:{'Content-Type':'audio/webm'},
      body:new Blob(CH,{type:'audio/webm'})}).then(function(r){return r.json();}).then(function(d){
      var t=[]; if(d['听见']) t.push('你：'+d['听见']); if(d['回话']) t.push('她：'+d['回话']);
      sayEl.textContent=t.join('　')||('没成：'+(d.reason||''));
      if(d['回话']){ try{ var u=new SpeechSynthesisUtterance(d['回话']); u.lang='zh-TW';
        var vs=speechSynthesis.getVoices()||[];
        var v=vs.find(function(x){return /zh[-_]TW/i.test(x.lang);});
        if(v) u.voice=v; u.rate=0.95; u.pitch=1.05; speechSynthesis.speak(u);}catch(e){} }
      glow(false);
    }).catch(function(e){ sayEl.textContent='对讲失败：'+e.message; glow(false); });
  }
  if(ptt){ ptt.addEventListener('mousedown',start); ptt.addEventListener('mouseup',stop);
    ptt.addEventListener('mouseleave',stop); }
  try{ var es=new EventSource('/api/digital-human/stream');
    es.addEventListener('witness',function(e){ var m=JSON.parse(e.data);
      if(m.type==='speaking'||m.type==='spoken'){ glow(m.type==='speaking'); }
      if(m.type==='speaking'&&m.text){ root.classList.add('open'); sayEl.textContent='她：'+m.text; } });
  }catch(e){}
})();
(function(){
  var g=document.getElementById('setupgate');
  if(!g) return;
  fetch('/api/setup/status').then(function(r){return r.json();}).then(function(d){
    if(!d['需要配置']) return;
    g.classList.add('on');
    var need={}; (d['必须先配']||[]).forEach(function(k){ need[k]=1; });
    (d['行']||[]).forEach(function(row){
      if(row['已配']) return;
      var id='sk-'+row['键'];
      var lab=document.createElement('label'); lab.textContent=row['键']+' · '+row['说明'];
      var inp=document.createElement('input'); inp.id=id; inp.placeholder='粘贴后点保存（不会回显）';
      inp.type=(row['键'].indexOf('KEY')>=0||row['键'].indexOf('TOKEN')>=0||row['键'].indexOf('SECRET')>=0)?'password':'text';
      g.querySelector('.fields').appendChild(lab); g.querySelector('.fields').appendChild(inp);
    });
  }).catch(function(){});
  g.querySelector('.save').addEventListener('click',function(){
    var body={};
    (g.querySelectorAll('input')).forEach(function(i){ if(i.value.trim()) body[i.id.slice(3)]=i.value.trim(); });
    fetch('/api/setup/keys',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json();}).then(function(d){
        if(d.ok){ location.reload(); } else { alert(d.reason||'保存失败'); }
      }).catch(function(e){ alert('保存失败：'+e.message); });
  });
  g.querySelector('.skip').addEventListener('click',function(){ g.classList.remove('on'); });
})();
</script>"""


def widget() -> str:
    """浮窗 + 首启闸（由 skills/ui_design.inject 注入到**每一个页面**）。"""
    return (CSS +
            '<div id=dhc><div class="face" title="她（点我展开）">'
            '<img src="/api/tripo/frame?name=face_curious.png" alt="她"></div>'
            '<div class=box><div class=t id=dhc-say>（点脸展开，按住说话或直接问她）</div>'
            '<div style="margin-top:6px;display:flex;gap:8px;align-items:center">'
            '<button class="btn primary" id=dhc-ptt>按住说话</button>'
            '<a href="/digital-human/console">展开交互台 →</a></div></div></div>' +
            '<div id=setupgate><div class=card><h2>先配密钥，她才出现</h2>'
            '<p>密钥只落 <code>state/keys.env</code>（已 gitignore，0600），面板不回显原文；'
            '留空即未配，她会如实说"拿不到"。配完自动刷新。</p>'
            '<div class=fields></div>'
            '<div class=row><button class="btn primary save">保存并让她出现</button>'
            '<button class=btn skip>稍后再说</button>'
            '<span class=muted style="font-size:12px">未配主通道时，她仍可用本机离线通道说话/听写</span>'
            '</div></div></div>' + JS)


__all__ = ["router", "widget", "status", "apply_env", "KEYS_ENV", "CSS", "JS"]
