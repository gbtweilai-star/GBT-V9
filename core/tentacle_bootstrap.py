# core/tentacle_bootstrap.py —— 启动自举：密钥配好 → 触手收到信号 → 自己把自己配好
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人设计（2026-10-08）：「在用户下载好启动 APP 之后配置好大模型密钥，触手会接收到启动
#   自动自我配置这些。」
#
# 于是整条启动链是：
#   ① 她出现（/api/setup/keys 落 state/keys.env）→ ② **触发本模块** →
#   ③ 每根触手：立自己的专业（没立的才立）→ ④ 按专业自配装备与账号 →
#   ⑤ 绑云终端（免费算力，本地 0 显存）→ ⑥ 出报告入账（缺什么如实说，不假装配好）。
#
# 三条纪律：
#   · **幂等**：跑第二遍不会把已立的专业重挑、不会重复占位（对比 before/after 计数）；
#   · **如实**：没配成的逐条列出来（缺依赖/缺授权/库空），报告里不许出现"全部就绪"这种话；
#   · **可查**：最近一次报告落 state/tentacle_bootstrap.json，面板与验收器都能读。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "state" / "tentacle_bootstrap.json"
DEFAULT_N = 10                                     # 一次自举几根（100 根也能跑，但这台机器上会很久）


def _save(rec: dict) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        _swallow(__file__, e)


def status() -> dict:
    """读最近一次自举报告（没跑过就如实说没跑过）。"""
    if not REPORT.is_file():
        return {"ok": False, "跑了没": False, "reason": "还没跑过自举", "报告": str(REPORT)}
    try:
        d = json.loads(REPORT.read_text(encoding="utf-8"))
        d["跑了没"] = True
        return d
    except (OSError, ValueError) as exc:
        return {"ok": False, "跑了没": True, "reason": f"报告读不了：{type(exc).__name__}"}


def run(*, n: int = DEFAULT_N, equip: bool = True, cloud: bool = True,
        force: bool = False, dedupe: bool = True) -> dict:
    """跑一次启动自举。**幂等**：已立的专业不会重挑（除非 force=True）。"""
    t0 = time.time()
    from core import tentacle_profession as TP
    from core import tentacle_equip as TE

    before = TP.roster(n=max(n, 100))
    ids = [r["tentacle"] for r in before["行"][:n]]
    assigned, skipped, failed = [], [], []
    for tid in ids:
        row = next(r for r in before["行"] if r["tentacle"] == tid)
        if row["state"] != "未立" and not force:
            skipped.append({"tentacle": tid, "已有": row["专业"]})
            continue
        a = TP.auto_assign(tid)
        (assigned if a.get("ok") else failed).append(a)

    # 去重：主人的口径是「每根触手**自己的**专业」。历史遗留的撞车这里就地纠正（只重挑撞车的那根）。
    fixed = []
    if dedupe:
        seen: dict = {}
        for r in TP.roster(n=max(n, 100))["行"]:
            if r["state"] == "未立":
                continue
            if r["专业"] in seen:
                # force=True 是**故意**的：assign 默认拒绝改已立专业（防误改），而这里正是要纠撞车
                a = TP.auto_assign(r["tentacle"], force=True)
                fixed.append({"tentacle": r["tentacle"], "原来撞": r["专业"],
                              "改成": a.get("专业"), "ok": bool(a.get("ok"))})
            else:
                seen[r["专业"]] = r["tentacle"]

    equipped = []
    if equip:
        for tid in ids:
            try:
                e = TE.equip(tid, dry_run=False)
                equipped.append({"tentacle": tid, "ok": bool(e.get("ok")),
                                 "已齐": e.get("已齐") or e.get("summary") or ""})
            except Exception as exc:                            # noqa: BLE001
                equipped.append({"tentacle": tid, "ok": False, "reason": type(exc).__name__})

    cloud_r = {}
    if cloud:
        try:
            from core import cloud_terminal as CT
            auto = CT.auto() or {}
            cloud_r = {k: auto.get(k) for k in ("装了", "失败", "要主人做的", "本地显存")}
        except Exception as exc:                                # noqa: BLE001
            cloud_r = {"ok": False, "reason": type(exc).__name__}

    # ③ 云插件「一人一个」排布（主人 2026-10-09：按云插件排布，各自一人一个）
    cloud_bind_r = {}
    if cloud:
        try:
            from core import cloud_bind as CB
            cloud_bind_r = {k: v for k, v in CB.apply(n=max(n, 100)).items()
                            if k in ("绑成功", "独享绑成功", "共享绑成功", "用到的插件数（去重）",
                                     "真插件", "为什么不是 100 独享")}
        except Exception as exc:                            # noqa: BLE001
            cloud_bind_r = {"ok": False, "reason": f"{type(exc).__name__}"}

    after = TP.roster(n=max(n, 100))
    rec = {"ok": True, "时间": time.strftime("%Y-%m-%dT%H:%M:%S"), "n": n,
           "自举前已立": before["已立"], "自举后已立": after["已立"],
           "这次新立": len(assigned), "跳过（本来就立了）": len(skipped), "立失败": len(failed),
           "装备": {"跑了": len(equipped), "齐了": sum(1 for x in equipped if x.get("ok"))},
           "云终端": cloud_r,
           "云插件一人一个": cloud_bind_r,
           "去重纠正": len(fixed),
           "明细": {"新立": assigned, "跳过": skipped, "失败": failed, "装备": equipped,
                    "去重": fixed},
           "ms": int((time.time() - t0) * 1000),
           "口径": "自举只做本仓侧（专业/装备/账号位/云终端槽）；触手自己执行，缺什么如实列"}
    _save(rec)
    return rec


def _cloud_bind_check(n: int) -> dict:
    """云插件「一人一个」排布：**纯本仓侧、不吃任何密钥** ⇒ 每次启动都该校验。"""
    try:
        from core import cloud_bind as CB
        r = CB.apply(n=max(n, 100))
        return {k: v for k, v in r.items()
                if k in ("绑成功", "独享绑成功", "共享绑成功", "用到的插件数（去重）",
                         "真插件", "为什么不是 100 独享")}
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}"}


def _remember(cb: dict) -> None:
    """把这次启动校验写进报告（没有报告就建一份最小的）。"""
    rec = {}
    if REPORT.is_file():
        try:
            rec = json.loads(REPORT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = {}
    rec["云插件一人一个"] = cb
    rec["启动校验"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(rec)


def ensure_started(*, n: int = DEFAULT_N) -> dict:
    """启动时调。**两个闸分开**（2026-10-09 纠）：

    · 云插件「一人一个」排布：纯本仓侧、不吃密钥 ⇒ **每次启动都校验**（幂等 upsert）；
    · 专业/装备/账号自举：要吃大模型 ⇒ 没配密钥就等她出现（这就是设计顺序）。
    """
    cb = _cloud_bind_check(n)      # 先做不吃密钥那一步
    _remember(cb)
    try:
        from panel.dh_companion import status as setup_status
        st = setup_status()
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": f"读不到密钥状态：{type(exc).__name__}"}
    if st.get("需要配置"):
        return {"ok": True, "跑了没": False, "云插件一人一个": cb,
                "reason": "密钥未配：专业自举按设计顺序等她出现；**云插件一人一排布已在本启动校验**",
                "必须先配": st.get("必须先配")}
    prev = status()
    if prev.get("跑了没") and prev.get("ok"):
        # 专业那步跳过（已立过）；云插件排布在函数开头已经重新校验过了（cb）。
        return {"ok": True, "跑了没": False,
                "reason": "启动时已有自举报告：专业跳过（幂等），云插件排布已按一人一个重新校验",
                "云插件一人一个": cb,
                "上次": {k: prev.get(k) for k in ("时间", "自举后已立", "这次新立")}}
    return run(n=n)


__all__ = ["run", "status", "ensure_started", "REPORT", "DEFAULT_N"]
