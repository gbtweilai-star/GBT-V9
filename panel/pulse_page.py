# panel/pulse_page.py —— 万能插面板口 + 排除登记（一屏看清）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from core.swallow import swallow as _swallow
from fastapi import APIRouter

router = APIRouter()


# 标准插座（面板启动/首次读时自动插上，这样"插着谁"是真的，不是装饰）
STANDARD_SOCKETS: tuple = (
    ("file", "state/panel_current.json"),
    ("file", "state/publish/ledger.jsonl"),
    ("process", "py:print(\"pulse-ready\")"),
)


def _pulse_ready():
    """返回一个已插好标准插座的 Pulse（幂等）。"""
    from core.pulse import Pulse, Socket
    from audit.ledger_factory import make_ledger
    p = Pulse(ledger=make_ledger())
    for kind, target in STANDARD_SOCKETS:
        p.plug(Socket(kind=kind, target=target))
    return p


@router.get("/api/pulse/sockets")
async def pulse_sockets() -> dict:
    """插着谁：列出插座与状态（首次读时自动插标准插座）。"""
    p = _pulse_ready()
    return {"插座": p.snapshot(), "标准插座": [list(x) for x in STANDARD_SOCKETS],
            "说明": "POST /api/pulse/plug 插入 · POST /api/pulse/run 插上并驱动（真执行）"}


@router.post("/api/pulse/plug")
async def pulse_plug(payload: dict) -> dict:
    """插入一个插座：{"kind":"file|process|api|web","target":"..."}"""
    from core.pulse import Pulse, Socket
    b = payload or {}
    p = Pulse()
    s = p.plug(Socket(kind=str(b.get("kind") or "file"), target=str(b.get("target") or "")))
    return {"插座": {"kind": s.kind, "target": s.target, "state": s.state, "detail": s.detail}}


@router.post("/api/pulse/run")
async def pulse_run(payload: dict) -> dict:
    """**插上并驱动它**（真执行）：{"kind","target","action","args"} —— 走 core.pulse.plug_and_run。"""
    import asyncio as _a
    from core.pulse import plug_and_run
    from audit.ledger_factory import make_ledger
    b = payload or {}
    return await _a.to_thread(plug_and_run, str(b.get("target") or ""), kind=str(b.get("kind") or "process"),
                              action=str(b.get("action") or "run"), args=b.get("args"),
                              timeout=float(b.get("timeout") or 30.0), ledger=make_ledger())


@router.get("/api/pulse")
async def pulse_panel() -> dict:
    """一屏看清：插着谁 · 用哪份能源 · 谁授的权 · 花了多少。"""
    from core.pulse import Pulse, Socket, energy
    from audit.ledger_factory import make_ledger
    led = make_ledger()
    p = _pulse_ready()                     # 标准插座（幂等）
    st = p.selftest()                      # 再真插几个（本机真文件/真 URL/故意非法一个）
    drv = p.dispatch("py:print(\"pulse-ready\")", {"action": "run"})   # ★ 真驱动一次，证明线是通的
    return {"能源": energy(), "插座": st, "真驱动": {"ok": drv.get("ok"), "stdout": (drv.get("结果") or {}).get("stdout", "").strip()},
            "谁授权": {"文件/进程/API": "本机身份（无需额外授权）",
                       "web/gui": "授权类（GuiAgent allow_actions）；授权到位自动放开"},
            "花了多少": "每次执行都落账 pulse/run:*（见台账）；云算力另见 /api/cloud/neurons"}


@router.get("/api/studio")
async def studio() -> dict:
    """创作链状态：工具齐不齐 + 已出的成品 + 一条链的入口说明。"""
    from pathlib import Path
    from core import studio as SD
    root = Path(__file__).resolve().parent.parent
    made = [{"文件": str(p.relative_to(root)), "KB": p.stat().st_size // 1024,
             "秒": round(SD.probe_duration(p), 2)}
            for p in sorted((root / "render").glob("*.mp4"))]
    return {"工具": SD.tools_ready(), "成品": made,
            "入口": "core.studio.plan(文本, 画面片列表, 名字) —— 旁白→配乐→字幕→成片",
            "口径": "全本地零付费：Blender 渲画面 · edge-tts 出声（台湾腔默认）· 算法出乐 · ffmpeg 合成"}


@router.get("/api/lipsync")
async def lipsync(text: str = "", duration: float = 0.0) -> dict:
    """口型时间轴：给一段话与音频时长，返回 [{起,止,口型,字}]（嘴跟着发音走）。"""
    from core import lipsync as LS
    if not text:
        return {"ok": True, "示例": LS.stats(), "用法": "/api/lipsync?text=你好&duration=3.0"}
    return LS.timeline(text, duration)


@router.get("/api/native")
async def native_capabilities() -> dict:
    """她的原生能力（触手 / 万能插 / 穿透扫描 / 脑子）：原文 + 现场读数。"""
    from pathlib import Path as _P
    doc = _P("docs/她的原生能力.md")
    import sys as _s
    _s.path.insert(0, ".")
    from core import dh_memory as DM
    from core import pulse as PU
    return {"文档": str(doc), "原文": (doc.read_text(encoding="utf-8", errors="replace")[:4000] if doc.is_file() else "（缺）"),
            "记忆里关于原生能力的条目": DM.search("原生能力", limit=3)["命中"],
            "插座种类": list(getattr(PU, "SOCKET_KINDS", ())),
            "插座能源": getattr(PU, "SOCKET_ENERGY", {}),
            "口径": "她的原生能力 = 触手(编队) + 万能插(5 插座) + 穿透扫描(100 分片交叉复核) + 脑子(云脑+本地兜底)"}


@router.get("/api/sider/status")
async def sider_status(probe: bool = True) -> dict:
    """Sider 插头状态（不破坏/不绕检测；用她自己的会话）。"""
    import asyncio as _a
    from core import sider_plug as SP
    return await _a.to_thread(SP.status, probe=probe)


@router.post("/api/sider/hand")
async def sider_hand(payload: dict) -> dict:
    """把任务递进 Sider（她的会话里）：{"task":"...","submit":false}"""
    import asyncio as _a
    from core import sider_plug as SP
    b = payload or {}
    return await _a.to_thread(SP.hand, str(b.get("task") or ""), submit=bool(b.get("submit")))


@router.post("/api/sider/login-window")
async def sider_login_window() -> dict:
    """开一次可见登录窗（她的 profile）：登入用你自己的账号，我不碰密码。"""
    from core import sider_plug as SP
    return SP.open_login()


@router.get("/api/dh/boot")
async def dh_boot_status() -> dict:
    """数字人启动口：记忆 + 六步带路 + 开场白（一开机就带路）。"""
    from core import dh_boot as DB
    return DB.panel()


@router.post("/api/dh/boot/run")
async def dh_boot_run() -> dict:
    """真跑一次启动：灌记忆 + 定位当前该教哪一步。"""
    import asyncio as _a
    from core import dh_boot as DB
    return await _a.to_thread(DB.boot)


@router.get("/api/dh/teach/next")
async def dh_teach_next(mark: bool = False) -> dict:
    """当前该教的一步（mark=true 就把它标记为已完成，往前推进）。"""
    from core import dh_teach as DT
    return DT.next_step(mark=mark)


@router.get("/api/dh/memory/search")
async def dh_memory_search(q: str) -> dict:
    """检索她的框架记忆；查不到就如实说没存到（不编）。"""
    from core import dh_memory as DM
    return DM.search(q)


@router.get("/api/stop-policy")
async def stop_policy_status() -> dict:
    """停机闸：只允许 部署完成 / 真需用户操作 两种理由停。"""
    from core import stop_policy as SP
    return SP.status(5)


@router.get("/api/no-begging")
async def no_begging_status() -> dict:
    """禁求助闸：框架内不许求用户帮忙（唯一例外：主人自己的身份登入）。"""
    from core import no_begging as NB
    r = NB.scan()
    return {"红数": r["红数"], "黄数": r["黄数"], "红": r["红"][:10], "黄": r["黄"][:10],
            "口径": "红=阻塞等人；黄=求助文案；例外=主人身份证/账户登入"}


@router.get("/api/delivery-gate")
async def delivery_gate_status() -> dict:
    """交付闸：每个能力必须单独跑通闭环才准交付（含历史台账）。"""
    from core import delivery_gate as DG
    return DG.status(3)


@router.post("/api/delivery-gate/audit")
async def delivery_gate_audit() -> dict:
    """真跑一遍交付闸（逐个能力单跑，慢）。"""
    import asyncio as _a
    from core import delivery_gate as DG
    return await _a.to_thread(DG.audit, verbose=False)


@router.get("/api/modular-deploy")
async def modular_deploy_status() -> dict:
    """模块式部署：蓝图（模块=实现件+面板口+独立验收器）+ 最近部署。"""
    from core import modular_deploy as MD
    return {"蓝图": MD.blueprint(), "最近": MD.status(5)}


@router.post("/api/modular-deploy/deploy")
async def modular_deploy_do(payload: dict) -> dict:
    """装一个模块：{"module":"模型-牢房吐口","dry":false}；不传 module 就全装。"""
    import asyncio as _a
    from core import modular_deploy as MD
    b = payload or {}
    if b.get("module"):
        return await _a.to_thread(MD.deploy, str(b["module"]), dry=bool(b.get("dry")))
    return await _a.to_thread(MD.deploy_all, dry=bool(b.get("dry")))


@router.get("/api/daily-triage")
async def daily_triage_status() -> dict:
    """每日重启排查：最近一次报告（报警必须带根因/处置/证据）。"""
    import json as _j, pathlib as _pl
    root = _pl.Path(__file__).resolve().parent.parent
    d = root / "state" / "daily_triage"
    fs = sorted(d.glob("*.json")) if d.is_dir() else []
    if not fs:
        return {"报告": "还没跑过（tools/daily_triage.py）"}
    return {"最近报告": fs[-1].name, "内容": _j.loads(fs[-1].read_text(encoding="utf-8"))}


@router.get("/api/vision/eyes")
async def vision_eyes() -> dict:
    """她的眼睛：实时流自检（fps/帧龄/顶掉/丢帧）——她知道有眼睛。"""
    from core import vision_loop as VL
    return VL.self_check("main")


@router.post("/api/vision/start")
async def vision_start(payload: dict) -> dict:
    """起眼：{"full":false,"region":{...}} —— 默认 640x360（实测 ~60fps）。"""
    from core import vision_loop as VL
    b = payload or {}
    lp = VL.eyes("main", full=bool(b.get("full")), region=b.get("region"))
    import time as _t
    _t.sleep(2.0)
    return lp.self_check()


@router.get("/api/tentacle-scale")
async def tentacle_scale_status() -> dict:
    """亿万级触手：逻辑可寻址（1 亿地址空间）· 实体按需 · 钥匙派生不落盘。"""
    from core import tentacle_scale as TS
    s = TS.stats()
    s["示例寻址"] = {"1": TS.addr(1), "12345678": TS.addr(12345678), "99999999": TS.addr(99999999)}
    s["示例钥匙指纹"] = {"t00000001": TS.key_fingerprint(1), "t00000002": TS.key_fingerprint(2)}
    return s


@router.post("/api/tentacle-scale/spawn")
async def tentacle_scale_spawn(payload: dict) -> dict:
    """一句话加编队：{"n":1000,"start":1}"""
    import asyncio as _a
    from core import tentacle_scale as TS
    b = payload or {}
    return await _a.to_thread(TS.spawn, int(b.get("n") or 100), start=int(b.get("start") or 1))


@router.post("/api/tentacle-scale/materialize")
async def tentacle_scale_materialize(payload: dict) -> dict:
    """按需实体化：{"addr":"t00000007","note":"..."}"""
    from core import tentacle_scale as TS
    b = payload or {}
    return TS.materialize(str(b.get("addr") or "t00000001"), note=str(b.get("note") or ""))


@router.get("/api/cross-scan")
async def cross_scan_status() -> dict:
    """编队交叉扫描：100 根分片扫 + 命中须 2 根交叉复核 + 盲区清单。"""
    from core import cross_scan_fleet as CS
    return CS.status(10)


@router.post("/api/cross-scan/run")
async def cross_scan_run(payload: dict) -> dict:
    """真跑一遍编队交叉扫描：{"shards":100,"limit":0}"""
    import asyncio as _a
    from core import cross_scan_fleet as CS
    b = payload or {}
    return await _a.to_thread(CS.run, int(b.get("shards") or 100), int(b.get("limit") or 0))


@router.get("/api/full-power")
async def full_power_status() -> dict:
    """火力全开：原样指令 · 沙盒里跑 · 只从触手吐（护栏在容器上）。"""
    from core import full_power as FP
    return FP.status(30)


@router.post("/api/full-power/fire")
async def full_power_fire(payload: dict) -> dict:
    """原样把指令打进沙盒，火力全开轮候选池，结果由触手吐出：{"prompt","tenant","spat_by"}"""
    import asyncio as _a
    from core import full_power as FP
    b = payload or {}
    return await _a.to_thread(FP.fire, str(b.get("prompt") or ""),
                              tenant=str(b.get("tenant") or "t001"),
                              max_models=int(b.get("max_models") or 3),
                              spat_by=str(b.get("spat_by") or ""))


@router.get("/api/cells")
async def cells_status() -> dict:
    """牢房与吐口：模型关在里面，只有触手能吐出来（含四查与吐账）。"""
    from core import sandbox_emit as SE
    return SE.run("status")


@router.post("/api/cells/confine")
async def cells_confine(payload: dict) -> dict:
    """把模型关进牢房跑一次：{"prompt","backend","model","tenant"}（结果只落 outbox）。"""
    import asyncio as _a
    from core import sandbox_emit as SE
    b = payload or {}
    def _go():
        c = SE.Cell(name="api", tenant=str(b.get("tenant") or "t001"))
        r = c.confine(str(b.get("prompt") or ""), backend=str(b.get("backend") or "cloud"),
                      model=str(b.get("model") or ""))
        r["cell_id"] = c.cell_id
        return r
    return await _a.to_thread(_go)


@router.post("/api/cells/spit")
async def cells_spit(payload: dict) -> dict:
    """触手把结果吐出来：{"cell_id","by"} —— 非触手一律拒。"""
    from core import sandbox_emit as SE
    b = payload or {}
    cid = str(b.get("cell_id") or "")
    p = SE.CELLS / cid
    if not p.is_dir():
        return {"ok": False, "reason": "没有这间牢房: %s" % cid}
    c = SE.Cell.__new__(SE.Cell)
    c.cell_id, c.dir = cid, p
    c.inbox, c.outbox, c.tape = p / "in", p / "outbox", p / "tape"
    c.sealed = (c.outbox / "manifest.json").is_file()
    return c.spit(by=str(b.get("by") or "t001"))


@router.get("/api/takeover")
async def takeover_status() -> dict:
    """接手协议台账 + 合规护栏（框架不限模型 ⇒ 护栏挂在接手这一刻）。"""
    from core import takeover as TO
    return TO.status(50)


@router.post("/api/takeover/offer")
async def takeover_offer(payload: dict) -> dict:
    """开接手包：{"task","to","criteria","level","need_tools"}"""
    from core import takeover as TO
    b = payload or {}
    return TO.offer(str(b.get("task") or ""), to=str(b.get("to") or ""), criteria=str(b.get("criteria") or ""),
                    level=str(b.get("level") or "常规"), need_tools=tuple(b.get("need_tools") or ()))


@router.post("/api/takeover/accept")
async def takeover_accept(payload: dict) -> dict:
    """接手（带自检）：{"pkg":{...},"by":"t003"} —— 自检不过会如实拒绝并列缺项。"""
    from core import takeover as TO
    b = payload or {}
    return TO.accept(b.get("pkg") or {}, by=str(b.get("by") or "t001"), note=str(b.get("note") or ""))


@router.post("/api/takeover/reject")
async def takeover_reject(payload: dict) -> dict:
    """退回（必须带原因）：{"pkg":{...},"by":"t004","reason":"..."}"""
    from core import takeover as TO
    b = payload or {}
    return TO.reject(b.get("pkg") or {}, by=str(b.get("by") or "t001"), reason=str(b.get("reason") or ""))


@router.get("/api/cloud-plugins")
async def cloud_plugins() -> dict:
    """云插件：编队装载状态 + 一个插件能调的面 + 凭据就绪。"""
    from core import cloud_plugin as CP
    return {"状态": CP.status(), "能调什么": CP.catalog()}


@router.get("/api/tentacle-store")
async def tentacle_store_status() -> dict:
    """每根触手的随身库：配备数/体积/随手存/汇报/未读 + 主脑 inbox 未读。"""
    from core import tentacle_store as TS
    return {"状态": TS.status(), "主脑未读": TS.inbox(limit=50)}


@router.post("/api/tentacle-store/report")
async def tentacle_store_report(payload: dict) -> dict:
    """触手往自己库丢一条汇报：{"tentacle","title","body","level"}（不打断主脑）。"""
    from core import tentacle_store as TS
    b = payload or {}
    return TS.report(str(b.get("tentacle") or "t001"), str(b.get("title") or ""),
                     str(b.get("body") or ""), str(b.get("level") or "常规"), b.get("payload"))


@router.post("/api/tentacle-store/stash")
async def tentacle_store_stash(payload: dict) -> dict:
    """随手丢：{"tentacle","key","value","kind"}（可有可无的都丢这儿）。"""
    from core import tentacle_store as TS
    b = payload or {}
    return TS.stash(str(b.get("tentacle") or "t001"), str(b.get("key") or "k"),
                    b.get("value"), str(b.get("kind") or "备忘"))


@router.post("/api/tentacle-store/read")
async def tentacle_store_read(payload: dict) -> dict:
    """主脑读完标已读：{"items":[{触手,号},...]}（不传就一次读完全部未读）。"""
    from core import tentacle_store as TS
    b = payload or {}
    items = b.get("items") or TS.inbox(limit=500)["汇报"]
    return TS.mark_read(items)


@router.get("/api/local-models")
async def local_models() -> dict:
    """本地模型可跑性：量家底 → 逐档判能跑/勉强/跑不动（"这玩意大不大"用读数回答）。"""
    from core import local_model_probe as L
    return L.judge()


@router.get("/api/capability/loop")
async def capability_loop() -> dict:
    """每一项能力调用闭环台账：通/黄警/未通 + 具体卡点。"""
    import json as _j, pathlib as _pl
    root = _pl.Path(__file__).resolve().parent.parent
    p = root / "state" / "capability_loop.jsonl"
    if not p.is_file():
        return {"台账": "未跑过（tools/run_capability_loop.py）"}
    rec = _j.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    return {"at": rec.get("at"), "总数": rec.get("总数"), "通": rec.get("通"),
            "黄警": rec.get("黄警"), "未通": rec.get("未通"),
            "明细": [{"能力": r["能力"], "结论": r["结论"], "卡点": r.get("卡点", ""), "ms": r.get("ms")}
                     for r in rec.get("明细", [])]}


@router.post("/api/capability/loop/run")
async def capability_loop_run() -> dict:
    """重跑一遍能力闭环（真加载真调用）。"""
    import asyncio as _a, subprocess as _sp
    r = await _a.to_thread(_sp.run, [__import__("sys").executable, "tools/run_capability_loop.py"],
                           capture_output=True)
    return {"ok": r.returncode == 0, "stdout": (r.stdout or b"").decode("utf-8", "replace")[-1200:]}


@router.get("/api/tentacles/vaults")
async def tentacle_vaults() -> dict:
    """触手册子（独立金库）+ 自配装备覆盖度。"""
    from core import tentacle_equip as TE
    from core import tentacle_profession as TP
    import pathlib as _pl
    root = _pl.Path(__file__).resolve().parent.parent
    v = root / "vaults" / "agency_tentacles"
    files = sorted(v.glob("*.md")) if v.is_dir() else []
    return {"册子目录": str(v.relative_to(root)), "册子篇数": len(files),
            "样例": [f.name for f in files[:5]], "配置": TE.report(n=100),
            "名册": {"已立": TP.roster(n=100).get("已立"), "未立": TP.roster(n=100).get("未立")}}


@router.post("/api/tentacles/equip")
async def tentacle_equip_run(payload: dict) -> dict:
    """让触手自己配装备：{"tentacle":"t001"} 或 {"all":true}（真动手，走装备坞的门与审计）。"""
    import asyncio as _a
    from core import tentacle_equip as TE
    b = payload or {}
    if b.get("all"):
        def _all():
            n = 0
            for i in range(1, 101):
                try:
                    TE.equip("t%03d" % i, dry_run=bool(b.get("dry_run", False)))
                    n += 1
                except Exception as e:
                    _swallow(__file__, e)
            return {"ok": True, "处理": n, "报表": TE.report(n=100)}
        return await _a.to_thread(_all)
    return await _a.to_thread(TE.equip, str(b.get("tentacle") or "t001"),
                              dry_run=bool(b.get("dry_run", False)))


@router.get("/api/themes")
async def themes() -> dict:
    """港式僵尸 · 三个自动化主题（灯光/调色/音乐/节奏参数包）+ 出片台账。"""
    from core import film_themes as FT
    return {"主题": FT.themes(), "台账": FT.history(10)}


@router.post("/api/themes/produce")
async def themes_produce(payload: dict) -> dict:
    """按主题全自动出一支（长任务，线程池跑）。"""
    import asyncio as _a
    from core import film_themes as FT
    body = payload or {}
    key = str(body.get("主题") or "午夜义庄")
    return await _a.to_thread(FT.produce, key, str(body.get("剧本") or ""), str(body.get("标题") or ""))


@router.get("/api/series/state")
async def series_state() -> dict:
    """剧集连载状态：集数 · 边界是否首尾相连 · 全剧一致性（响度/风格/画幅）。"""
    from core import series as S
    return {"状态": S.state(), "台账": S.history(10),
            "标准": S.STANDARD, "边界阈值": S.ALIGN_MAX}


@router.get("/api/doctrine/no-ask")
async def no_ask() -> dict:
    """不开口闸：除「用户身份/账户登录」外，严禁要求用户做事；触手默认人类操作。"""
    from core import ux_doctrine as UX
    return UX.no_ask_rule()


@router.post("/api/doctrine/check-ask")
async def check_ask(payload: dict) -> dict:
    """拿一段话判：有没有在要求用户做事（身份登录算例外）。"""
    from core import ux_doctrine as UX
    return UX.check_ask(str((payload or {}).get("文本") or ""))


@router.get("/api/detail/habit")
async def detail_habit() -> dict:
    """「细节化」习惯：六件套 + 模糊词闸 + 回读闭环 + 达标率。"""
    from core import detail_habit as DH
    return {"习惯": DH.habit(), "达标率": DH.audit()}


@router.post("/api/detail/check")
async def detail_check(payload: dict) -> dict:
    """拿一段话或一步骤来判：符不符合"细节化"（缺什么、有没有模糊词）。"""
    from core import detail_habit as DH
    body = payload or {}
    if body.get("文本"):
        return {"文本判定": DH.check_text(str(body["文本"]))}
    return {"步骤判定": DH.check_step(body.get("步骤") or body)}


@router.get("/api/protected")
async def protected() -> dict:
    """不可删保护清单（导航/自检/记忆/算力/闸门本身，不是危险件）。"""
    from core import protected_assets as PA
    return PA.protected_list()


@router.get("/api/selfcheck-rule")
async def selfcheck_rule() -> dict:
    """自检纪律：报警必须追根因+留证据，禁"不影响"。"""
    from core import ux_doctrine as UX
    return UX.selfcheck_rule()


@router.get("/api/doctrine")
async def ux_doctrine() -> dict:
    """交互铁律：唯一停点（危险清单）+ 禁句闸 + 默认自动。"""
    from core import ux_doctrine as UX
    return UX.doctrine()


@router.get("/api/llm-roles")
async def llm_roles() -> dict:
    """角色→模型 钉死表：谁该用哪个脑子 + 被排除厂商报警 + 变更登记。"""
    from core import llm_roles as LR
    return {"当前": LR.current(), "检查": LR.check(), "变更史": LR.history(20)}


@router.get("/api/excluded-models")
async def excluded() -> dict:
    """被排除的厂商/模型登记（有日期、有理由）。"""
    from core.excluded_models import registry
    return registry()

@router.get("/api/dh/overlay.js")
async def dh_overlay_js():
    from fastapi.responses import Response as _Resp
    from core import dh_presence as DP
    return _Resp(DP.OVERLAY_JS, media_type="application/javascript; charset=utf-8")


@router.post("/api/dh/point")
async def dh_point(payload: dict):
    from core import dh_presence as DP
    b = payload or {}
    return DP.point(str(b.get("目标") or ""), str(b.get("说") or ""), page=str(b.get("页面") or ""))


@router.get("/api/dh/presence/next")
async def dh_presence_next():
    from core import dh_presence as DP
    return DP.next_hint()


@router.get("/api/dh/presence")
async def dh_presence_status():
    from core import dh_presence as DP
    return DP.status()

@router.get("/avatar-asset/{name}")
async def avatar_asset(name: str):
    from pathlib import Path as _P
    from fastapi import HTTPException as _HE
    from fastapi.responses import FileResponse as _FR
    _base = _P(__file__).resolve().parent.parent / "assets" / "avatar"
    _f = (_base / name).resolve()
    if not str(_f).startswith(str(_base.resolve())) or not _f.is_file():
        raise _HE(status_code=404, detail="no such avatar asset")
    return _FR(str(_f), media_type="image/png")

