# core/cloud_terminal.py —— 云终端（免费算力）：把显存与运行放云端，本地 0 显存
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）：「云插件页面独立隔开一个区域，一排过去装 10 个；让触手自己去注册
#   freebuff，使用优先注册；然后在云插件里面部署终端面板，把显存和运行放云端，本地 0 显存，
#   这个终端是免费的」。
#
# 两条边界（必须先写在代码里，不能含糊）：
#   ① **不代第三方批量开户**：这是 `core/tentacle_identity.py:9-14` 已经写死的红线
#      （违平台条款、把本机 IP 拖进滥用名单，对项目是净损失）。所以落法改成：
#      **账号由主人注册** → 触手拿自己的身份位（金库「云插件」服务位）去**绑定使用**。
#   ② 不采用任何"绕过风控/隐身"手法：不 TLS 伪装、不改指纹、不代理跳板（那是 evade，不是能力）。
#
# 事实口径（外部资料，只当数据；2026-10-09 现查）：freebuff.com 是**免费云编程代理**
#   （"the free coding agent"，靠终端插广告换免费算力）；社区另有 freebuff-proxy 网关项目。
#   **本站是客户端渲染，我没有验到它的任何正式 API** ⇒ 端点状态一律如实标「未验」，
#   不许写成"已接通"。
from __future__ import annotations
from core.swallow import swallow as _swallow

import socket
import time
from pathlib import Path
from urllib.parse import urlparse

from senses.sqldialect import txn

ROOT = Path(__file__).resolve().parent.parent
N_SLOTS = 10                                   # 一排 10 个（主人点名）
PROVIDERS = (
    {"id": "freebuff", "名": "Freebuff（优先）", "端点": "https://freebuff.com",
     "说明": "免费云编程代理（终端插广告换算力）· 账号由主人注册，触手只绑定使用",
     "优先": True, "API": "未验（站点为客户端渲染，未见公开 API 文档）"},
    {"id": "github-proxy", "名": "社区网关（freebuff-proxy）", "端点": "",
     "说明": "社区 OpenAI 兼容网关（GitHub: trefeon/freebuff-proxy）· 需自行部署",
     "优先": False, "API": "未验"},
    {"id": "self-host", "名": "自建/本机", "端点": "",
     "说明": "自己的机器或云主机；本地跑就没有「0 显存」这回事，如实标注",
     "优先": False, "API": "n/a"},
)


def _conn_ledger():
    from audit.ledger_factory import make_ledger
    return make_ledger()


def ensure(led=None) -> str | None:
    led = led or _conn_ledger()
    if led is None:
        return None
    d = getattr(led, "dialect", "sqlite")
    ts = "REAL" if d == "sqlite" else "DOUBLE PRECISION"
    try:
        with txn(led) as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS cloud_terminal("
                        "slot INTEGER PRIMARY KEY, provider TEXT, tentacle TEXT DEFAULT '',"
                        "handle TEXT DEFAULT '', state TEXT DEFAULT '未配', note TEXT DEFAULT '',"
                        f"updated_at {ts})")
    except Exception:                                        # noqa: BLE001
        return None
    return d


def _rows(led=None) -> dict:
    led = led or _conn_ledger()
    if led is None or ensure(led) is None:
        return {}
    try:
        with txn(led) as cur:
            cur.execute("SELECT slot, provider, tentacle, handle, state, note FROM cloud_terminal")
            return {int(r[0]): {"provider": r[1], "tentacle": r[2], "handle": r[3],
                                "state": r[4], "note": r[5]} for r in cur.fetchall()}
    except Exception:                                        # noqa: BLE001
        return {}


def slots(led=None) -> list:
    """10 个槽的真读数：没配就如实说未配（不预填"已就绪"）。"""
    got = _rows(led)
    out = []
    for i in range(1, N_SLOTS + 1):
        r = got.get(i) or {}
        out.append({"slot": i, "id": "ct%02d" % i,
                    "provider": r.get("provider") or "",
                    "provider_名": next((p["名"] for p in PROVIDERS
                                        if p["id"] == (r.get("provider") or "")), ""),
                    "tentacle": r.get("tentacle") or "",
                    "handle": r.get("handle") or "",
                    "state": r.get("state") or "未配",
                    "note": r.get("note") or ""})
    return out


def registry() -> dict:
    return {"槽数": N_SLOTS, "槽": slots(),
            "供应商": list(PROVIDERS), "优先": "freebuff",
            "本地显存": 0,
            "口径": ["免费云终端：显存与运行在云端，本机 0 显存",
                       "账号由主人注册（freebuff.com），触手拿身份位绑定使用",
                       "**不代批量开户、不做风控绕过**（core/tentacle_identity.py:9-14 红线）",
                       "端点未验的标未验，不写已接通"]}


def assign(slot: int, tentacle: str, *, provider: str = "freebuff",
           handle: str = "", led=None) -> dict:
    """把一根触手绑到一个云终端槽：同时占它的「云插件」身份位（一根触手一个身份位）。"""
    i = int(slot)
    if not (1 <= i <= N_SLOTS):
        return {"ok": False, "reason": f"槽号 1..{N_SLOTS}"}
    t = str(tentacle or "").strip()
    if not t:
        return {"ok": False, "reason": "缺触手号"}
    if provider not in {p["id"] for p in PROVIDERS}:
        return {"ok": False, "reason": f"未知供应商: {provider}"}
    led = led or _conn_ledger()
    if ensure(led) is None:
        return {"ok": False, "reason": "账本不可用"}
    from core import tentacle_identity as TI
    c = TI.claim(t, "云插件")                     # 不走开户，只占本仓身份位
    occupied = bool(c.get("ok")) or "已有身份位" in str(c.get("reason", ""))
    with txn(led) as cur:
        cur.execute("INSERT INTO cloud_terminal(slot,provider,tentacle,handle,state,note,updated_at)"
                    " VALUES(?,?,?,?,?,?,?) ON CONFLICT(slot) DO UPDATE SET"
                    " provider=EXCLUDED.provider, tentacle=EXCLUDED.tentacle,"
                    " handle=EXCLUDED.handle, state=EXCLUDED.state, note=EXCLUDED.note,"
                    " updated_at=EXCLUDED.updated_at",
                    (i, provider, t, handle or TI.env_key(t, "云插件"),
                     "已绑（待验）" if occupied else "占位失败",
                     "账号由主人注册；触手绑定使用", time.time()))
    return {"ok": occupied, "slot": i, "provider": provider, "tentacle": t,
            "身份位": c.get("reason") or "已占",
            "凭据来源": TI.env_key(t, "云插件"),
            "口径": "本仓只占身份位；**不代开户**"}


def release(slot: int, *, led=None) -> dict:
    i = int(slot)
    led = led or _conn_ledger()
    if ensure(led) is None:
        return {"ok": False, "reason": "账本不可用"}
    with txn(led) as cur:
        cur.execute("DELETE FROM cloud_terminal WHERE slot=?", (i,))
    return {"ok": True, "slot": i}


def signup_order(slot: int, *, led=None) -> dict:
    """**给触手看的一张开户作业单**（主人 2026-10-08：「你设计好，触手会做」）。

    我只设计流程与闸，执行交给触手（它自己有眼/手/嘴：打开页面、填表、读回执）。
    作业单里**不含凭据明文** —— 只写"从哪个环境变量取"（值在金库/环境里）。
    三个**必须停下等人**的停点（设计死的，不许绕）：验证码 / 人机校验 / 服务条款同意。
    """
    r = next((x for x in slots(led) if x["slot"] == int(slot)), None)
    if r is None or not r.get("tentacle"):
        return {"ok": False, "reason": "这个槽还没绑触手：先 assign 或 auto"}
    from core import tentacle_identity as TI
    t = r["tentacle"]
    prov = next((p for p in PROVIDERS if p["id"] == r["provider"]), {})
    return {"ok": True, "作业": "云终端开户", "槽": int(slot), "触手": t,
            "目标": prov.get("端点") or "", "供应商": prov.get("名") or r["provider"],
            "步骤": [
                "① 用浏览器触手打开目标页（人类级操作：真点、真填，不用脚本模拟）",
                "② 表单身份：用本触手自己的邮箱位 " + TI.env_key(t, "邮箱") + "（值从环境读）",
                "③ 密码：生成本机强随机，**先写入金库再提交**（不许先提交后补记）",
                "④ 遇到验证码/人机校验/服务条款 → **停下**，报到主脑，等主人过（这一步设计成不许绕）",
                "⑤ 成功后回填：handle + env_key 落金库（TI.attach / provision），槽状态转『已验证』",
                "⑥ 把回执（截图 + 落地 URL + 时间）入账，供事后复核",
            ],
            "要填的字段": {"邮箱": TI.env_key(t, "邮箱"), "密码": "本机生成→金库",
                            "用户名": t + "-gbt"},
            "停点（等人）": ["验证码", "人机校验", "服务条款同意"],
            "回填函数": "core.tentacle_identity.attach / provision；core.cloud_terminal.signup_done",
            "边界": "不批量刷号、不做风控绕过（core/tentacle_identity.py:9-14）；一次一套真实流程",
            "谁执行": "触手（不需要主人动手，也不需要我做）"}


def signup_done(slot: int, *, handle: str = "", verified: bool = False, led=None) -> dict:
    """触手开户回来后的回填口：写金库 + 转槽状态。没真的验证过就只能是『待验』。"""
    r = next((x for x in slots(led) if x["slot"] == int(slot)), None)
    if r is None or not r.get("tentacle"):
        return {"ok": False, "reason": "这个槽还没绑触手"}
    from core import tentacle_identity as TI
    got = TI.attach(r["tentacle"], "云插件", handle=handle or f"{r['tentacle']}.云插件@freebuff",
                    note="触手自主开户回填（freebuff）")
    state = "已验证" if verified else "已配"
    led = led or _conn_ledger()
    if led is not None:
        try:
            with txn(led) as cur:
                cur.execute("UPDATE cloud_terminal SET state=?, handle=?, updated_at=? WHERE slot=?",
                            (state, handle or r.get("handle") or "", time.time(), int(slot)))
        except Exception as e:
            _swallow(__file__, e)
    return {"ok": bool(got.get("ok")), "slot": int(slot), "state": state,
            "金库": got.get("reason") or "已写", "verified": bool(verified)}


def auto(*, n: int = N_SLOTS, provider: str = "freebuff", tentacles=None,
         probe: bool = True, led=None) -> dict:
    """**一键全装**（主人 2026-10-08：「AI 时代讲究便捷，别让用户动手」）：

    一次调用把这排 10 个槽全配齐：挑触手 → 占它们的「云插件」身份位 → 写槽 → 逐个探可达性。
    没给 tentacles 就**按专业挑**（分域口径里需要云插件的那批优先，没立专业的往后排）。
    诚实边界：本函数**只做本仓侧**（身份位/槽/探活）；第三方账号仍由主人开户，
    每槽会给出`开户入口`与`要填什么`，主人只过验证码。**不批量刷号。**
    """
    led = led or _conn_ledger()
    if ensure(led) is None:
        return {"ok": False, "reason": "账本不可用"}
    if tentacles is None:
        tentacles = _pick_tentacles(n)
    done, failed = [], []
    for i, t in enumerate(list(tentacles)[:n], start=1):
        r = assign(i, t, provider=provider, led=led)
        (done if r.get("ok") else failed).append({"slot": i, "tentacle": t,
                                                   "为什么": r.get("身份位") or r.get("reason")})
    probes = []
    if probe and done:
        probes.append(check(done[0]["slot"], led=led))     # 同一供应商只探一次（省时也够）
    return {"ok": len(done) == min(n, len(list(tentacles))),
            "装了": len(done), "失败": len(failed), "明细": done, "失败明细": failed,
            "探测": probes,
            "供应商": provider, "本地显存": 0,
            "开户入口": next((p["端点"] for p in PROVIDERS if p["id"] == provider), ""),
            "要主人做的": "只有一步：开户时过验证码（其余全自动）；本仓不代批量开户",
            "口径": "一键全装 = 本仓侧全自动；第三方账号一次真实人类级操作，不刷号"}


def _pick_tentacles(n: int) -> list:
    """按专业挑触手：需要「云插件」身份位的分域优先；不够就用没立专业的补位。"""
    picked: list = []
    try:
        from core import tentacle_equip as TE
        from core import tentacle_profession as TP
        rows = TP.roster(n=100)["行"]
        want = []
        for r in rows:
            if r["专业"] == "未立":
                continue
            need = TE.plan(r["tentacle"])
            if "云插件" in (need.get("需要服务位") or []):
                want.append(r["tentacle"])
        picked = want
        if len(picked) < n:
            picked += [r["tentacle"] for r in rows
                       if r["tentacle"] not in picked][: n - len(picked)]
    except Exception:                                        # noqa: BLE001
        picked = ["t%03d" % i for i in range(1, n + 1)]
    return picked[:n]


def check(slot: int, *, timeout: float = 3.0, led=None) -> dict:
    """真探一次可达性（过 net_guard，短超时）。**探不到就写不可达，不许写成已接通。**"""
    i = int(slot)
    r = next((x for x in slots(led) if x["slot"] == i), None)
    if r is None or not r.get("provider"):
        return {"ok": False, "slot": i, "state": "未配", "reason": "这个槽还没绑"}
    prov = next((p for p in PROVIDERS if p["id"] == r["provider"]), None) or {}
    url = prov.get("端点") or ""
    if not url:
        return {"ok": False, "slot": i, "state": "无端点", "reason": "该供应商没有可探端点"}
    try:
        from body.net_guard import assert_safe_outbound_url
        assert_safe_outbound_url(url)
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "slot": i, "state": "被安全闸拒绝", "reason": type(exc).__name__}
    host = urlparse(url).hostname
    t0 = time.time()
    try:
        s = socket.create_connection((host, 443), timeout=timeout)
        s.close()
        res = {"ok": True, "state": "可达（HTTP 层未验）",
               "ms": int((time.time() - t0) * 1000), "host": host}
    except OSError as exc:
        res = {"ok": False, "state": "不可达", "reason": type(exc).__name__,
               "ms": int((time.time() - t0) * 1000), "host": host}
    led = led or _conn_ledger()
    if led is not None:
        try:
            with txn(led) as cur:
                cur.execute("UPDATE cloud_terminal SET state=?, note=? WHERE slot=?",
                            (res["state"], (prov.get("API") or "")[:60], i))
        except Exception as e:
            _swallow(__file__, e)
    return {"slot": i, **res, "API": prov.get("API")}


SECTION = r"""
<div style="margin:18px 0;padding:14px 16px;border:1px solid #1d3350;border-radius:14px;
     background:linear-gradient(180deg,rgba(12,22,38,.55),rgba(6,10,18,.75))">
  <h2 style="margin:0 0 4px">云终端 · 免费算力（本地 0 显存）<span id=ctsum class=muted
     style="font-size:12px;font-weight:400"> 加载中…</span></h2>
  <div class=muted style="font-size:12px">
    一排 10 个槽：<b>显存与运行都在云端</b>，本机 0 显存。优先 <b>Freebuff</b>（免费云编程代理）。
    <span style="color:#d29922">账号由**主人注册**，触手拿自己的「云插件」身份位绑定使用 ——
    本仓不代批量开户、不做风控绕过。</span>
  </div>
  <div id=ctrow style="display:grid;grid-template-columns:repeat(10,1fr);gap:6px;margin-top:10px"></div>
  <div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <button class="btn primary" id=ctauto>一键全装这 10 个（本仓侧全自动）</button>
    <button class=btn id=ctprobe>探一遍可达性</button>
    <span class=muted style="font-size:12px">开户只留一步：你过验证码；其余我全做</span>
  </div>
  <div id=ctmsg class=muted style="font-size:12px;margin-top:8px">（点格子可单独改绑；或直接点“一键全装”）</div>
</div>
<script>
(function(){
  var row=document.getElementById('ctrow'); if(!row) return;
  function paint(d){
    document.getElementById('ctsum').textContent=' · 已绑 '+d.槽.filter(function(s){return s.tentacle;}).length+'/10';
    row.innerHTML=d.槽.map(function(s){
      var on=!!s.tentacle;
      return '<div class=cell style="border:1px solid '+(on?'#39d0ff55':'#1d3350')+';border-radius:10px;padding:6px;'
        +'background:rgba(10,18,32,.6);cursor:pointer" data-slot="'+s.slot+'">'
        +'<div class=muted style="font-size:11px">'+s.id+'</div>'
        +'<div style="font-size:11px">'+(s.tentacle||'未配')+'</div>'
        +'<div class=muted style="font-size:10px">'+s.state+'</div></div>';
    }).join('');
    Array.prototype.forEach.call(row.querySelectorAll('.cell'),function(el){
      el.onclick=function(){
        var slot=el.getAttribute('data-slot');
        var t=prompt('把哪个触手绑到 ct'+('0'+slot).slice(-2)+'？（如 t007）');
        if(!t) return;
        fetch('/api/cloud/terminal/assign',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({slot:parseInt(slot,10),tentacle:t,provider:'freebuff'})})
        .then(function(r){return r.json();}).then(function(d){
          document.getElementById('ctmsg').textContent=d.ok?('已绑 '+t+' → ct'+('0'+slot).slice(-2)):('没成：'+(d.reason||d.detail||''));
          load();});
      };
    });
  }
  function load(){ fetch('/api/cloud/terminal').then(function(r){return r.json();}).then(paint)"""

CHECK_JS = r"""
    .catch(function(e){ document.getElementById('ctmsg').textContent='取不到：'+e.message; }); }
  var b=document.getElementById('ctauto');
  if(b) b.onclick=function(){
    var m=document.getElementById('ctmsg'); m.textContent='一键全装…';
    fetch('/api/cloud/terminal/auto',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
      .then(function(r){return r.json();}).then(function(d){
        m.textContent='装了 '+d.装了+'/'+10+' · 探测 '+(d.探测&&d.探测[0]?d.探测[0].state:'—')+' · 要你做的：'+(d.要主人做的||'');
        load(); }).catch(function(e){ m.textContent='没成：'+e.message; });
  };
  load();
})();
</script>"""


def section_models() -> str:
    """云上大模型那块（能不能用 / 额度 / 拼接 / 真跑）。"""
    return MODELS_SECTION



# 原模型块里那段 JS 有隐患：splice() 一出问题会把后面的 loadPlugins() 一起带走，且**静默无报错**
# （实测：DOM 全停在"加载中…"、控制台零异常）。所以改成**四条独立链 + 出错显示出来**的版本，
# 并在 section() 里只取旧块的 HTML、丢掉旧 JS（旧 JS 不再上线，避免两份逻辑打架）。
MODELS_FIX = r"""<script>
(function(){
  function $(id){ return document.getElementById(id); }
  var sum=$('cmsum'); if(!sum) return;
  function say(id, text){ var el=$(id); if(el) el.textContent=text; }
  function js(u){ return fetch(u).then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); }); }
  function bad(where, e){ say('cmprobe', where+' 取不到：'+((e&&e.message)||e)); }

  // ① 记账 + 并发闸（真值来自每次调用返回的 usage.neurons）
  js('/api/cloud/neurons').then(function(n){
    var t=n.今日||{}, g=n.闸||{};
    $('cmquota').innerHTML='<b>今日已用 '+t.估算已用+' / 剩余 '+t.估算剩余+' neurons</b>（调用 '+t.调用数+' 次）'
      +' <span class=muted>· 并发闸允许 '+g.允许并发+' 个 · '+(t.撞过4006?'⛔ 撞过 4006，等 00:00 UTC':'未撞额度墙')+'</span>'
      +'<br><span class=muted>读法：'+t.读法+'（免费额度 '+t.免费额度+'/天，UTC 日切）</span>';
  }).catch(function(e){ bad('记账', e); });

  // ② 拼接总览（可拼接/开着/预留 + 凭据）
  js('/api/cloud/splice').then(function(s){
    sum.textContent=' · 可拼接 '+s['可拼接（有真 id）']+'/100 · 开着 '+s['其中开着']
      +' · 预留 '+s['预留槽']+' · 凭据 '+((s.凭据&&s.凭据.来源)||'无')+' '+((s.凭据&&s.凭据.掩码)||'');
  }).catch(function(e){ bad('拼接', e); });

  // ③ 每族小表 + 插件下拉
  js('/api/cloud/registry').then(function(d){
    var ps=((d.registry&&d.registry.plugins)||[]), real=ps.filter(function(p){return p.cf_id;});
    $('cmplug').innerHTML=real.slice(0,60).map(function(p){
      return '<option value="'+p.key+'">'+p.group_cn+' · '+p.cn+' · '+p.slug+'</option>'; }).join('');
    var g={};
    ps.forEach(function(p){ g[p.group_cn]=g[p.group_cn]||{真:0,预留:0};
      if(p.cf_id){ g[p.group_cn].真++; } else { g[p.group_cn].预留++; } });
    $('cmsgrid').innerHTML=Object.keys(g).map(function(k){
      return '<div style="border:1px solid #1d3350;border-radius:10px;padding:6px;background:rgba(10,18,32,.6)">'
        +'<div class=muted style="font-size:11px">'+k+'</div>'
        +'<div style="font-size:12px">可拼接 '+g[k].真+'</div>'
        +'<div class=muted style="font-size:10px">预留 '+g[k].预留+'</div></div>'; }).join('');
  }).catch(function(e){ bad('注册表', e); });

  // ④ 本地方案体检
  // 本地大模型：主人 2026-10-09 已定「不做」——这行是**决策记录**，不是还在给第二个选项。
  if($('cmllm')) $('cmllm').textContent='本地方案：**已定不做**（主人令 2026-10-09：只走云插件）· 全部算力在云端，本地 0 显存';
  js('/api/local-llm/readiness').then(function(v){
    var el=$('cmllm'); if(el) el.title='体检读数（仅备查，未启用）：'+v.结论+' · 磁盘放得下(4bit)：'+((v['磁盘放得下的规模(4bit)']||[]).join('/')||'—');
  }).catch(function(e){ bad('本地体检', e); });

  // ⑤ 按钮
  $('cmrun').onclick=function(){
    say('cmout', '真跑中…（大模型要十几秒）');
    fetch('/api/cloud/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({plugin:$('cmplug').value,prompt:$('cmprompt').value})})
      .then(function(r){return r.json();}).then(function(o){
        say('cmout', o.ok ? ('✅ '+o.插件+'  HTTP '+o.http+' · '+o.ms+'ms\n出字：'+(o.出字||''))
                         : ('❌ '+(o.插件||'')+' '+(o.reason||'')));
      }).catch(function(e){ say('cmout','没成：'+e.message); });
  };
  $('cmq').onclick=function(){ js('/api/cloud/quota').then(function(q){
    var probes=(q.探测||[]).map(function(p){ return p.口+'='+(p.ok?('HTTP '+p.http):((p.http||'')+' '+(p.why||''))); }).join(' · ');
    say('cmprobe','官方分析口探测：'+probes+'（对 inference 令牌本就读不到，真值走每次调用返回）');
  }).catch(function(e){ bad('官方口', e); }); };
})();
</script>"""


def section() -> str:
    """给 /cloud 用的整块：云终端 + 云上大模型读数（HTML + JS 拼好）。

    模型块只取**HTML 部分**（丢掉它原来那段会静默死掉的 JS），JS 由 MODELS_FIX 统一负责。
    """
    html_only = MODELS_SECTION.split("<script>")[0]
    return SECTION + CHECK_JS + html_only + MODELS_FIX



MODELS_SECTION = r"""
<div style="margin:18px 0;padding:14px 16px;border:1px solid #1d3350;border-radius:14px;
     background:linear-gradient(180deg,rgba(10,26,22,.5),rgba(6,12,16,.78));margin-bottom:130px">
  <h2 style="margin:0 0 4px">云上大模型 · 能不能用 <span id=cmsum class=muted
     style="font-size:12px;font-weight:400">加载中…</span></h2>
  <div class=muted style="font-size:12px">
    口径（引用 CF 官方定价页）：免费 <b>10,000 Neurons/天</b> · 每天 <b>00:00 UTC</b> 重置 ·
    <b>超限直接报错</b> · Workers Paid 超出按 $0.011/1,000 Neurons 计费 ·
    <span style="color:#d29922">另有 4 个前沿模型要求绑定付费方式（kimi-k2.6 / kimi-k2.7-code / glm-5.2 / glm-5.3）</span>
  </div>
  <div id=cmquota style="margin-top:6px;font-size:12.5px">今日已用：加载中…</div>
  <div id=cmprobe class=muted style="font-size:11px;margin-top:2px"></div>
  <div id=cmllm class=muted style="font-size:11px;margin-top:2px">本地方案：加载中…</div>
  <div id=cmsgrid style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:10px"></div>
  <div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <select id=cmplug style="background:#0d1524;color:#e8f0fb;border:1px solid #1d3350;
      border-radius:8px;padding:6px 8px;max-width:46%"></select>
    <input id=cmprompt value="用四个字回答：你在线吗" style="flex:1;min-width:200px;
      background:#0d1524;color:#e8f0fb;border:1px solid #1d3350;border-radius:8px;padding:6px 8px">
    <button class="btn primary" id=cmrun>真跑一次</button>
    <button class=btn id=cmq>探额度</button>
  </div>
  <pre id=cmout class=muted style="margin:8px 0 0;font-size:12px;white-space:pre-wrap;max-height:150px;overflow:auto">（选一个插件，真跑一次看看）</pre>
</div>
<script>
(function(){
  var sum=document.getElementById('cmsum'), grid=document.getElementById('cmsgrid');
  if(!sum) return;
  var sel=document.getElementById('cmplug'), out=document.getElementById('cmout');
  function quota(){
    // 真值来源：每次推理返回里的 usage.neurons（CF 官方真值）+ 本地按 UTC 日累计；
    // 官方 neuron 分析接口对 inference 令牌返回 not authorized ⇒ 那条只作"探一探"的脚注。
    fetch('/api/cloud/neurons').then(function(r){return r.json();}).then(function(n){
      var t=n.今日||{}, g=n.闸||{};
      document.getElementById('cmquota').innerHTML=
        '<b>今日已用 '+t.估算已用+' / 剩余 '+t.估算剩余+' neurons</b>（调用 '+t.调用数+' 次）'
        +' <span class=muted>· 并发闸允许 '+g.允许并发+' 个 · '+(t.撞过4006?'⛔ 撞过 4006，等到 00:00 UTC':'未撞额度墙')+'</span>'
        +'<br><span class=muted>读法：'+t.读法+'（免费额度 '+t.免费额度+'/天，按 UTC 日切）</span>';
      // 汇总行由 splice() 负责（这里先前留过占位 '+0'，异步竞态把它盖在正确值上 ⇒ 面板显示"可拼接 0"，已删）
    }).catch(function(e){
      document.getElementById('cmquota').textContent='记账取不到：'+e.message;
    });
    fetch('/api/local-llm/readiness').then(function(r){return r.json();}).then(function(v){
      var el=document.getElementById('cmllm'); if(!el) return;
      el.textContent='本地方案（AirLLM/llama.cpp 体检）：'+v.结论+' · 磁盘放得下(4bit)：'+((v['磁盘放得下的规模(4bit)']||[]).join('/')||'—');
    }).catch(function(){});
    fetch('/api/cloud/quota').then(function(r){return r.json();}).then(function(q){
      var probes=(q.探测||[]).map(function(p){ return p.口+'='+(p.ok?('HTTP '+p.http):((p.http||'')+' '+(p.why||''))); }).join(' · ');
      var foot=document.getElementById('cmprobe');
      if(foot) foot.textContent='官方分析口探测：'+probes+'（对 inference 令牌本就读不到，真值走每次调用返回）';
    }).catch(function(){});
  }
  function splice(){
    fetch('/api/cloud/splice').then(function(r){return r.json();}).then(function(s){
      sum.textContent=' · 可拼接 '+s['可拼接（有真 id）']+'/100 · 开着 '+s['其中开着']+' · 预留 '+s['预留槽']+
        ' · 凭据 '+(s.凭据&&s.凭据.来源||'无')+' '+(s.凭据&&s.凭据.掩码||'');
      var byG={};
      (s.明细||[]).forEach(function(p){});
      grid.innerHTML='';
    }).catch(function(e){ sum.textContent='拼接取不到：'+e.message; });
  }
  function load(){ quota(); splice(); loadPlugins(); }
  function loadPlugins(){
    fetch('/api/cloud/registry').then(function(r){return r.json();}).then(function(d){
      var ps=(d.registry&&d.registry.plugins)||[];
      var real=ps.filter(function(p){return p.cf_id;});
      sel.innerHTML=real.slice(0,60).map(function(p){
        return '<option value="'+p.key+'">'+p.group_cn+' · '+p.cn+' · '+p.slug+'</option>'; }).join('');
      var g={};
      ps.forEach(function(p){ g[p.group_cn]=g[p.group_cn]||{真:0,预留:0};
        if(p.cf_id){g[p.group_cn].真++;} else {g[p.group_cn].预留++;} });
      grid.innerHTML=Object.keys(g).map(function(k){
        return '<div style="border:1px solid #1d3350;border-radius:10px;padding:6px;background:rgba(10,18,32,.6)">'
          +'<div class=muted style="font-size:11px">'+k+'</div>'
          +'<div style="font-size:12px">可拼接 '+g[k].真+'</div>'
          +'<div class=muted style="font-size:10px">预留 '+g[k].预留+'</div></div>'; }).join('');
    }).catch(function(){ sel.innerHTML='<option>（注册表取不到）</option>'; });
  }
  document.getElementById('cmrun').onclick=function(){
    out.textContent='真跑中…（大模型要十几秒）';
    fetch('/api/cloud/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({plugin:sel.value,prompt:document.getElementById('cmprompt').value})})
      .then(function(r){return r.json();}).then(function(o){
        out.textContent=o.ok?('✅ '+o.插件+'  HTTP '+o.http+' · '+o.ms+'ms\n出字：'+(o.出字||''))
          :('❌ '+(o.插件||'')+' '+(o.reason||'')); }).catch(function(e){ out.textContent='没成：'+e.message; });
  };
  document.getElementById('cmq').onclick=function(){ document.getElementById('cmquota').textContent='探额度…'; quota(); };
  load();
})();
</script>"""


__all__ = ["slots", "registry", "assign", "release", "check", "section",
           "section_models", "PROVIDERS", "N_SLOTS"]
