# ~/.octop/plugins/gbt-potato-v9/main.py —— GBT小土豆V9 能力内核 → Octop 工具
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 融合点：把 V9 的 53 项离线能力与面板证据链挂成 Octop 工具，Octop 的 Agent 可直接发现与调用。
# 所有配置来自环境变量（GBT_V9_HOME / GBT_DB_PATH / V9_LEDGER_DB / V9_WORKDIR），源码零凭据。
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from octop_harness.plugins import PluginContext

V9_HOME = Path(os.environ.get("GBT_V9_HOME", r"C:\Users\ADMIN\gbt-potato-v9"))
if str(V9_HOME) not in sys.path:
    sys.path.insert(0, str(V9_HOME))

PANEL_DB = Path(os.environ.get("GBT_DB_PATH", str(V9_HOME / "data" / "gbt_v9.sqlite3")))


# ── 内核惰性装配（首次用才 import，失败给出可读原因）──
def _ledger():
    from audit.ledger import Ledger

    db = os.environ.get("V9_LEDGER_DB") or str(V9_HOME / "data" / "tentacle_ledger.db")
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    led = Ledger(db=db)
    # 病因（2026-10-07）：本插件只读工具（v9_coverage 读 scan_coverage、v9_evidence 等）
    # 依赖"别人先跑过 ensure_schema"才有表 —— 单跑本插件会报 no such table。
    # 这里自己幂等补一遍离线能力账本表，让每项能力独立可用（CREATE TABLE IF NOT EXISTS，零风险）。
    try:
        from skills.native_capabilities_shim import ensure_schema
        ensure_schema(led)
    except Exception:  # noqa: BLE001  缺 shim 也不该让只读工具挂掉
        pass
    return led


def _registry():
    from skills.caps.registry import build_caps_registry

    wd = os.environ.get("V9_WORKDIR") or str(V9_HOME / "data" / "native")
    Path(wd).mkdir(parents=True, exist_ok=True)
    return build_caps_registry(ledger=_ledger(), workdir=wd)


def _panel_conn():
    if not PANEL_DB.exists():
        return None
    return sqlite3.connect(f"file:{PANEL_DB}?mode=ro", uri=True)


def _fmt(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


# ── 工具 1：能力清单 ──
async def v9_capabilities() -> str:
    """列出 GBT小土豆V9 的全部离线能力（53 项，按域分组）。"""
    try:
        reg = _registry()
        groups: dict[str, list[str]] = {}
        for cap in reg.caps.values():
            groups.setdefault(cap.id.split(".", 1)[0], []).append(cap.id.split(".", 1)[1])
        lines = [f"GBT小土豆V9 能力内核：{len(reg.caps)} 项"]
        for domain, names in sorted(groups.items()):
            lines.append(f"  [{domain}] {', '.join(names)}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"❌ 能力内核不可用：{type(exc).__name__}: {exc}"


# ── 工具 2：能力自查 ──
async def v9_probe(cap_id: str = "") -> str:
    """对一项或全部能力做离线自查（probe）。cap_id 例：scan.cross_review；留空 = 全部。"""
    try:
        reg = _registry()
        if cap_id:
            cap = reg.get(cap_id)
            if cap is None:
                return f"❌ 未注册能力：{cap_id}"
            a = cap.probe()
            return f"{'✅' if a.done else '❌'} {cap_id} — {a.detail}"
        out = reg.probe_all()
        bad = [k for k, v in out.items() if not v["done"]]
        return f"probe {len(out) - len(bad)}/{len(out)} 通过" + (f"；失败：{bad}" if bad else " ✅")
    except Exception as exc:  # noqa: BLE001
        return f"❌ probe 失败：{type(exc).__name__}: {exc}"


# ── 工具 3：执行能力 ──
async def v9_run(cap_id: str, args_json: str = "{}") -> str:
    """执行一项能力（离线档案）。args_json 为该能力的入参 JSON，默认 {}。"""
    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError as exc:
        return f"❌ args_json 不是合法 JSON：{exc}"
    try:
        reg = _registry()
        r = reg.run(cap_id, args)
        head = "✅" if r.ok else "❌"
        payload = {"ok": r.ok, "output": r.output, "artifacts": r.artifacts,
                   "warnings": r.warnings, "error": r.error, "usage": r.usage}
        return f"{head} {cap_id}\n{_fmt(payload)}"
    except Exception as exc:  # noqa: BLE001
        return f"❌ 执行失败：{type(exc).__name__}: {exc}"


# ── 工具 4：跑整册能力验收 ──
async def v9_acceptance() -> str:
    """按 native-capabilities 清单跑一遍全量验收（独立进程，返回摘要）。"""
    script = V9_HOME / "tools" / "accept_octop_offline.py"
    if not script.exists():
        return f"❌ 验收脚本不存在：{script}"
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180, env=env,
                              cwd=str(V9_HOME))
        tail = (proc.stdout or "").strip().splitlines()[-8:]
        return "\n".join(tail) or f"(exit={proc.returncode})"
    except Exception as exc:  # noqa: BLE001
        return f"❌ 验收执行失败：{type(exc).__name__}: {exc}"


# ── 工具 5/6/7：面板证据链只读面（直连 V9 面板 SQLite，无重依赖）──
async def v9_evidence(limit: int = 5) -> str:
    """最近的帧验证证据行（frame_verification_evidence）。"""
    conn = _panel_conn()
    if conn is None:
        return f"❌ 面板库不存在：{PANEL_DB}（先启动 V9 面板跑迁移）"
    try:
        rows = conn.execute(
            "SELECT id, ref, verdict_and_spec, created_epoch FROM frame_verification_evidence "
            "ORDER BY created_epoch DESC LIMIT ?", (int(limit),)).fetchall()
        return _fmt({"count": len(rows), "rows": [
            {"id": r[0], "ref": r[1], "verdict": r[2], "created_epoch": r[3]} for r in rows]})
    except sqlite3.Error as exc:
        return f"❌ 读取失败：{exc}"
    finally:
        conn.close()


async def v9_coverage() -> str:
    """覆盖率快照（coverage_snapshots）与最近扫描覆盖（账本 scan_coverage）。"""
    out: dict = {}
    conn = _panel_conn()
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT backend, overall_percent, threshold, passed, generated_epoch "
                "FROM coverage_snapshots ORDER BY generated_epoch DESC LIMIT 4").fetchall()
            out["panel_snapshots"] = [{"backend": r[0], "overall": r[1], "threshold": r[2],
                                       "passed": bool(r[3]), "at": r[4]} for r in rows]
        except sqlite3.Error:
            out["panel_snapshots"] = []
        finally:
            conn.close()
    try:
        led = _ledger()
        with led._tx() as c:
            rows = c.execute("SELECT root, coverage, total_targets, missing_targets "
                             "FROM scan_coverage ORDER BY id DESC LIMIT 3").fetchall()
        out["scan_coverage"] = [{"root": r[0], "coverage": r[1], "total": r[2], "missing": r[3]}
                                for r in rows]
    except Exception as exc:  # noqa: BLE001
        out["scan_coverage_error"] = f"{type(exc).__name__}: {exc}"
    return _fmt(out)


async def v9_calibrations(limit: int = 5) -> str:
    """最近校准（devour_calibrations：ΔE 噪声门限）。"""
    conn = _panel_conn()
    if conn is None:
        return f"❌ 面板库不存在：{PANEL_DB}"
    try:
        rows = conn.execute(
            "SELECT project_id, op_kind, algorithm_version, noise_threshold, sample_count, expires_at "
            "FROM devour_calibrations ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
        return _fmt({"count": len(rows), "rows": [
            {"project": r[0], "op": r[1], "algo": r[2], "noise_threshold": r[3],
             "samples": r[4], "expires_at": r[5]} for r in rows]})
    except sqlite3.Error as exc:
        return f"❌ 读取失败：{exc}"
    finally:
        conn.close()


# ── 工具 8：语音播报文本（SSML）──
async def v9_voice_say(text: str, rate: str = "medium") -> str:
    """把一段话转成 SSML 播报文本（离线，不碰音频设备）。"""
    try:
        reg = _registry()
        r = reg.run("voice.tts", {"text": text, "rate": rate})
        return "✅ " + str((r.output or {}).get("ssml", "")) if r.ok else f"❌ {r.error}"
    except Exception as exc:  # noqa: BLE001
        return f"❌ {type(exc).__name__}: {exc}"


# ── 工具 9：总状态 ──
async def v9_status() -> str:
    """V9 内核 / 面板库 / 账本 三处状态一览。"""
    out = {"v9_home": str(V9_HOME), "v9_home_exists": V9_HOME.exists(),
           "panel_db": str(PANEL_DB), "panel_db_exists": PANEL_DB.exists()}
    try:
        reg = _registry()
        out["capabilities"] = len(reg.caps)
        out["probe"] = "ok" if all(v["done"] for v in reg.probe_all().values()) else "degraded"
    except Exception as exc:  # noqa: BLE001
        out["capabilities_error"] = f"{type(exc).__name__}: {exc}"
    try:
        led = _ledger()
        with led._tx() as c:
            out["ledger_tables"] = c.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        out["ledger_error"] = f"{type(exc).__name__}: {exc}"
    return _fmt(out)


# ── 工具 10：触手编队（规模 / 统一密钥指纹 / 驱动审计；只读）──
async def v9_fleet(limit: int = 10) -> str:
    """触手编队状态：规模、是否与主脑同一把钥匙（只有指纹）、最近驱动审计。"""
    try:
        from core.tentacle_fleet import TentacleFleet
        f = TentacleFleet(ledger=None)          # 只装配不驱动（插件侧零副作用）
        return _fmt({"status": f.status(), "recent": f.table(int(limit))})
    except Exception as exc:  # noqa: BLE001
        return f"❌ 编队不可用：{type(exc).__name__}: {exc}"


# ── 工具 11：专属永久邮箱 GBT-D1…GBT-D100（地址表 + 备注；只读）──
async def v9_mailboxes() -> str:
    """触手专属永久邮箱地址表（GBT-D1…GBT-D100）与备注；未配 MAIL_DOMAIN 会如实说明。"""
    try:
        from core.mailbox_fleet import MailboxFleet
        f = MailboxFleet(ledger=None)
        st = f.status()
        return _fmt({"status": st,
                     "head": [b.as_dict() for b in list(f.boxes.values())[:3]],
                     "tail": [f.boxes[max(f.boxes)].as_dict()] if f.boxes else []})
    except Exception as exc:  # noqa: BLE001
        return f"❌ 邮箱编队不可用：{type(exc).__name__}: {exc}"


# ── 工具 12：原生视觉逐帧账（真采集一次，返回可复核读数）──
async def v9_vision(seconds: float = 1.0, fps: str = "auto") -> str:
    """原生视觉自检：校准 + 采集一小段，返回 on_time/no_loss/dropped 与帧账。"""
    try:
        from senses.frame_lock import FrameLock
        target = "auto" if str(fps) == "auto" else float(fps)
        fl = FrameLock(str(V9_HOME / "devoured" / "vision"), fps=target, source="screen")
        return _fmt(fl.watch(float(seconds)))
    except Exception as exc:  # noqa: BLE001
        return f"❌ 原生视觉不可用：{type(exc).__name__}: {exc}"


# ── 工具 13：自助工具坞（白名单 + 已装/审计；装/跑需授权令牌）──
async def v9_toolbay(grant: str = "") -> str:
    """触手自助工具坞：白名单目录 + 最近审计。给了授权令牌才允许装/跑。"""
    try:
        from core.tool_bay import CATALOG, ToolBay
        bay = ToolBay(ledger=_ledger())
        return _fmt({"catalog": {k: {"kind": v.kind, "target": v.target,
                                     "desc": v.desc, "risk": v.risk}
                                 for k, v in CATALOG.items()},
                     "grant_present": bool(grant), "recent": bay.list(limit=10)})
    except Exception as exc:  # noqa: BLE001
        return f"❌ 工具坞不可用：{type(exc).__name__}: {exc}"


# ── 工具 14：数字人动作（32 动作 + 6 组合，纯计算零副作用）──
async def v9_avatar(combo: str = "", clip: str = "idle", t: float = 0.35) -> str:
    """数字人姿态：给 combo（如「问候」）或 clip（如 wave），返回姿态 SVG 与关节角。"""
    try:
        from core import avatar_rig as AR
        st = (AR.sequence_state(combo, float(t)) if combo
              else AR.speak_state(clip or "idle", float(t)))
        return _fmt({"clip": st["clip"], "combo": st.get("combo"), "step": st.get("step"),
                     "blink": st.get("blink"), "关节数": len(AR.JOINTS),
                     "动作数": len(AR.CLIPS), "组合": list(AR.sequences()),
                     "svg": st["svg"]})
    except Exception as exc:  # noqa: BLE001
        return f"❌ 骨架不可用：{type(exc).__name__}: {exc}"


# ── 工具 15：听写（本机免费离线 ASR；只读输入文件）──
async def v9_asr(wav: str = "", text: str = "") -> str:
    """听写：给 wav 路径就转写它；留空则用 TTS 合成再听回来（闭环自证）。"""
    try:
        from senses import voice_sapi as VS
        if not VS.asr_status()["可用"]:
            return "❌ " + str(VS.asr_status().get("说明") or "本机没有中文听写识别器")
        r = VS.recognize(wav) if wav else VS.selftest(text or "今天天气不错")
        return _fmt({k: r.get(k) for k in ("ok", "text", "转写", "原文", "命中率", "置信",
                                           "识别器", "ms", "reason") if k in r})
    except Exception as exc:  # noqa: BLE001
        return f"❌ 听写不可用：{type(exc).__name__}: {exc}"


# ── 工具 16：工作流（调研前置闸门 + 逐段验收；只读 + 推进自己会拦）──
async def v9_workflows(flow_id: str = "", stage_id: str = "") -> str:
    """工作流清单/某条详情；给了 flow_id+stage_id 就**尝试推进**（调研未过闸门会被拒）。"""
    try:
        from core import workflows as W
        if not flow_id:
            st = W.status()
            return _fmt({"工作流数": st["工作流数"], "放行": st["放行数"], "阻塞": st["阻塞数"],
                         "触手班": st["触手班"], "纪律": st["纪律"],
                         "清单": [{"id": r["id"], "名称": r["名称"], "阶段数": r["阶段数"],
                                   "闸门": r["调研闸门"]["allowed"],
                                   "结论": r["调研闸门"].get("结论"),
                                   "验收": f'{r["验收"]["已通过"]}/{r["验收"]["标准数"]}'}
                                  for r in st["清单"]]})
        if stage_id:
            return _fmt(W.advance(flow_id, stage_id, by="octop"))
        f = W.flow(flow_id)
        if f is None:
            return f"❌ 没有这条工作流：{flow_id}（可用：{[x.id for x in W.WORKFLOWS]}）"
        return _fmt({"id": f.id, "名称": f.名称, "目标": f.目标, "题材": f.题材,
                     "调研闸门": W.research_gate(f.id), "验收": W.acceptance(f.id),
                     "阶段": [{"id": s.id, "名称": s.名称, "段": s.段, "负责": list(s.负责),
                               "触手": s.触手, "门禁": s.门禁, "验收": list(s.验收),
                               "证据": s.证据} for s in f.阶段]})
    except Exception as exc:  # noqa: BLE001
        return f"❌ 工作流不可用：{type(exc).__name__}: {exc}"


# ── 工具 17：市场调研（方案 / 本机真读数 / 闸门；提交会按纪律拒收）──
async def v9_market_research(topic: str = "", findings_json: str = "") -> str:
    """市场调研：留空 topic 给维度口径；给 topic 看闸门；给 findings_json 提交（无来源会被拒）。"""
    try:
        from core import market_research as MR
        if not topic:
            return _fmt({"维度数": len(MR.DIMENSIONS),
                         "维度": [{"名称": d.名称, "来源类型": d.来源类型,
                                   "判定线": d.判定线} for d in MR.DIMENSIONS],
                         "闸门口径": MR.status()["闸门口径"]})
        if not findings_json:
            return _fmt({"题材": topic, "闸门": MR.gate(topic),
                         "本机真读数": MR.local_evidence(topic)})
        try:
            arr = json.loads(findings_json)
        except Exception as exc:  # noqa: BLE001
            return f"❌ findings_json 不是合法 JSON：{type(exc).__name__}"
        return _fmt(MR.submit(topic, arr if isinstance(arr, list) else [], by="octop"))
    except Exception as exc:  # noqa: BLE001
        return f"❌ 调研不可用：{type(exc).__name__}: {exc}"


# ── 工具 18：原生大脑（统一记忆 / 生命起源存档 / 元认知）──
async def v9_brain(action: str = "status", text: str = "", query: str = "") -> str:
    """原生大脑：status 看一屏 / capture 记一条 / ask 提问（带出处）/ life 生平 / reflect 元认知。

    ask 只从真存过的记忆里答，答不上来会明说；capture 立即返回，理解在后台。
    """
    try:
        from core.memory import brain as B
        a = str(action or "status").lower()
        if a == "capture":
            if not str(text).strip():
                return "❌ capture 需要 text（要记的内容）"
            r = B.remember(text)
            return _fmt({"ok": r.get("ok"), "id": r.get("id"), "记忆域": r.get("scope"),
                         "说明": "已记住；分类/实体/向量在后台补上"})
        if a == "ask":
            if not str(query).strip():
                return "❌ ask 需要 query（要问的话）"
            r = B.ask(query, all_owners=True)
            return _fmt({"确定": r.get("confident"), "回答": r.get("answer"),
                         "出处": [{"id": s["id"], "谁": s["谁"], "分类": s["分类"],
                                   "打分": s["打分"], "命中": s["命中路子"]}
                                  for s in (r.get("sources") or [])],
                         "为什么不确定": r.get("unknown_reason") or ""})
        if a == "life":
            return _fmt(B.life())
        if a == "reflect":
            r = B.reflect()
            return _fmt({"自我陈述": r.get("自我陈述"), "该做的": r.get("该做的"),
                         "用到率": r["校准"].get("用到率"), "未答率": r["校准"].get("未答率")})
        return _fmt({k: v for k, v in B.status().items()
                     if k in ("统一记忆", "主体", "记忆域", "分类", "分层",
                              "生命起源存档", "元认知", "库位置")})
    except Exception as exc:  # noqa: BLE001
        return f"❌ 大脑不可用：{type(exc).__name__}: {exc}"


# ── 工具 19：触手身份金库（一根触手一个身份；主脑全量可查） ──
async def v9_identity(action: str = "summary", tentacle: str = "", service: str = "") -> str:
    """触手身份：summary 看总分 / rows 看明细（主脑全量）/ provision 接凭据 / bind 绑定。"""
    try:
        from core import tentacle_identity as TI
        a = str(action or "summary").lower()
        if a == "rows":
            return _fmt({"行": TI.rows(tentacle=tentacle, service=service),
                         "口径": "主脑全量视图（不传 tentacle 即全部）"})
        if a == "provision":
            return _fmt(TI.provision(tentacle, service))
        if a == "summary":
            return _fmt(TI.summary())
        return _fmt({"可用动作": ["summary", "rows", "provision", "bind"],
                     "边界": "凭据只从环境变量读；V9 不向第三方批量开户"})
    except Exception as exc:  # noqa: BLE001
        return f"❌ 身份金库不可用：{type(exc).__name__}: {exc}"


# ── 工具 20：自动扩容 + 蓝图状态（走钩子防偷懒跳过与空转） ──
async def v9_expand(action: str = "scan", n: int = 100, dry_run: bool = True) -> str:
    """自动扩容：scan 看缺口 / plan 看计划 / run 真跑（过钩子）/ blueprint 看蓝图状态。"""
    try:
        from core import expand as E
        a = str(action or "scan").lower()
        if a == "plan":
            return _fmt(E.plan(n=int(n)))
        if a == "run":
            return _fmt(E.run(n=int(n), dry_run=bool(dry_run)))
        if a == "blueprint":
            from core import blueprint as BP
            return _fmt(BP.status())
        return _fmt(E.scan(n=int(n)))
    except Exception as exc:  # noqa: BLE001
        return f"❌ 扩容不可用：{type(exc).__name__}: {exc}"


def setup(ctx: PluginContext) -> None:
    """把 V9 能力注册为 Octop 工具。"""
    ctx.tool("v9_capabilities", v9_capabilities,
             description="列出 GBT小土豆V9 全部离线能力（53 项，按域分组）")
    ctx.tool("v9_probe", v9_probe, description="能力自查：cap_id 留空查全部")
    ctx.tool("v9_run", v9_run, description="执行一项 V9 能力（cap_id + args_json）")
    ctx.tool("v9_acceptance", v9_acceptance, description="按清单跑全量能力验收并返回摘要")
    ctx.tool("v9_evidence", v9_evidence, description="最近的帧验证证据行（帧证据链）")
    ctx.tool("v9_coverage", v9_coverage, description="覆盖率快照 + 最近扫描覆盖率")
    ctx.tool("v9_calibrations", v9_calibrations, description="最近的 ΔE 校准记录")
    ctx.tool("v9_voice_say", v9_voice_say, description="一段话 → SSML 播报文本")
    ctx.tool("v9_status", v9_status, description="V9 内核/面板库/账本状态一览")
    # 本轮新增：编队 / 邮箱 / 原生视觉 / 自助工具坞（只读面，装与跑仍需授权令牌）
    ctx.tool("v9_fleet", v9_fleet,
             description="触手编队：规模、统一密钥指纹、最近驱动审计")
    ctx.tool("v9_mailboxes", v9_mailboxes,
             description="触手专属永久邮箱 GBT-D1…GBT-D100 地址表与备注")
    ctx.tool("v9_vision", v9_vision,
             description="原生视觉逐帧账：校准 + 采集 seconds 秒，返回是否掉帧")
    ctx.tool("v9_toolbay", v9_toolbay,
             description="自助工具坞白名单与审计；装/跑需授权令牌")
    # 本轮新增：数字人动作 / 免费离线听写（零显存）
    ctx.tool("v9_avatar", v9_avatar,
             description="数字人：32 动作 + 6 组合姿态（返回姿态 SVG 与关节角）")
    ctx.tool("v9_asr", v9_asr,
             description="听写（本机免费离线 SAPI）：给 wav 转写，留空则 TTS→ASR 自证")
    # 本轮新增：工作流（调研前置 + 逐段验收）/ 市场调研闸门
    ctx.tool("v9_workflows", v9_workflows,
             description="工作流：清单/详情；给 flow_id+stage_id 尝试推进（调研未过闸门会被拒）")
    ctx.tool("v9_market_research", v9_market_research,
             description="市场调研：维度口径/闸门/本机真读数；提交无来源的条目会被拒收")
    # 本轮新增：原生大脑（统一记忆 / 生命起源存档 / 元认知）
    ctx.tool("v9_brain", v9_brain,
             description="原生大脑：status/capture/ask（带出处）/life（生平）/reflect（元认知）")
    # 本轮新增：触手身份金库 / 自动扩容 + 蓝图状态
    ctx.tool("v9_identity", v9_identity,
             description="触手身份：summary/rows（主脑全量）/provision（读环境变量）/bind")
    ctx.tool("v9_expand", v9_expand,
             description="自动扩容：scan/plan/run（走钩子防偷懒）；action=blueprint 看蓝图状态")

