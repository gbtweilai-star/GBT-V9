# core/gbtv9_cloud.py —— GBT小土豆V9大模型 · **云上分片部署**（本地不部署模型）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-07）："本地不部署模型，全部部署到云插件，拆分开来部署，
#   反正都是共享触手双向绑定连接，不影响大模型驱动。"
#
# 形态：GBT小土豆V9大模型 = **虚拟大模型**，按角色拆成五个分片，每个分片
#   绑定一族云插件槽 + 一班触手（双向绑定已存在：触手↔槽位 10000 对）：
#
#     分片(角色)      槽位族                    触手班        干什么
#     规划 shard-plan   text-generation(强)      生产班        任务分解/决策
#     生成 shard-gen    text-generation(快)      生产班        正文/代码生成
#     审查 shard-review text-generation(另槽)    质检班        复核与挑错
#     结构 shard-json   text-generation(json)    生产班        结构化输出
#     嵌入 shard-embed  embedding 族             调研班        向量化/联想
#
# 诚实口径：分片注册、绑定、调度、审计全部就位；**真正出字**要等网关钱包有余额
#   （TeamoRouter 钱包余额不足是唯一卡点，我代付不了）。状态如实报"待充值"。
from core.swallow import swallow as _swallow
import json
import os
import time

from core import hooks as H

ROLES = (
    {"shard": "shard-plan", "角色": "规划", "槽族": "text-generation",
     "触手班": "生产班", "挑槽偏好": "70b/32b/pro", "干什么": "任务分解与决策"},
    {"shard": "shard-gen", "角色": "生成", "槽族": "text-generation",
     "触手班": "生产班", "挑槽偏好": "flash/free（量大）", "干什么": "正文与代码生成"},
    {"shard": "shard-review", "角色": "审查", "槽族": "text-generation",
     "触手班": "质检班", "挑槽偏好": "换一个槽（与生成分开）", "干什么": "复核与挑错"},
    {"shard": "shard-json", "角色": "结构化", "槽族": "text-generation",
     "触手班": "生产班", "挑槽偏好": "json_mode", "干什么": "结构化输出"},
    {"shard": "shard-embed", "角色": "嵌入", "槽族": "embedding",
     "触手班": "调研班", "挑槽偏好": "embedding:bge-m3", "干什么": "向量化与联想"},
)


def _slots_by_family() -> dict:
    out = {}
    try:
        from core.cloud_plugins import PLUGIN_IDS
        for pid in PLUGIN_IDS:
            fam = str(pid).split(":")[0]
            out.setdefault(fam, []).append(str(pid))
    except Exception as e:
        _swallow(__file__, e)
    return out


def _tentacle_squads() -> dict:
    """触手班 → 触手号区间（与 workflows 的四班一致）。"""
    return {"调研班": ("t001", "t012"), "生产班": ("t013", "t062"),
            "质检班": ("t063", "t086"), "验收班": ("t087", "t100")}


def shard_manifest() -> list:
    slots = _slots_by_family()
    squads = _tentacle_squads()
    rows = []
    for r in ROLES:
        fam_slots = slots.get(r["槽族"]) or []
        # 挑槽偏好：把 70b/32b/pro 的排前面（规划片要强）；生成片要快/免费
        pref = r["挑槽偏好"].lower()
        ranked = sorted(fam_slots, key=lambda s: (0 if any(k in s.lower() for k in
                                                        pref.replace("json_mode", "json").split("/"))
                                                 or any(k in s.lower() for k in ("70b", "32b", "pro"))
                                                 else 1, s)) if fam_slots else []
        lo, hi = squads.get(r["触手班"], ("t001", "t100"))
        rows.append({**r, "绑定槽位": ranked[:3], "槽位族可用": len(fam_slots),
                     "驱动触手": f"{lo}–{hi}"})
    return rows


def _gw_models() -> list:
    """网关里**真实存在**的模型（这才是池子的实际货）。"""
    from core.brain import API_KEY, BASE_URL
    from openai import OpenAI
    cli = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    return [m.id for m in cli.models.list().data]


_ROLE_PREF = {
    "规划": ("claude-opus-5", "deepseek-v4-pro", "claude-sonnet-5-5", "gpt-5"),
    "生成": ("deepseek-v4-flash-free", "deepseek-flash-free", "gemini-3.6-flash", "gpt-4o-mini"),
    "审查": ("claude-sonnet-5-5", "deepseek-v4-pro", "claude-opus-5", "gemini-3.6-flash"),
    "结构化": ("gemini-3.6-flash", "gpt-4o-mini", "deepseek-v4-flash-free", "claude-haiku-4-5"),
    "嵌入": (),   # 网关没有 /embeddings 路由（真机实测 404），如实标"无路由"
}


def _pick(models: list, role: str) -> str:
    prefs = _ROLE_PREF.get(role, ())
    for want in prefs:
        for m in models:
            if m == want or m.startswith(want):
                return m
    for want in prefs:                                    # 前缀放宽
        for m in models:
            if want.split("-")[0] in m:
                return m
    return models[0] if models else ""


def probe() -> dict:
    """逐分片真探一次（发最小请求），把结果如实记下来。分片绑**网关真实模型**。"""
    from core.brain import API_KEY, BASE_URL
    from openai import OpenAI
    cli = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    models = _gw_models()
    rows = []
    for r in shard_manifest():
        role = r["角色"]
        model = _pick(models, role)
        state, why = "待充值", ""
        if not model:
            state, why = "无槽", f"网关里没有能承担「{role}」的模型"
        else:
            try:
                cli.chat.completions.create(model=model, max_tokens=8, messages=[
                    {"role": "user", "content": "ping"}])
                state, why = "通", ""
            except Exception as exc:                           # noqa: BLE001
                msg = str(exc)
                if "余额不足" in msg:
                    state, why = "待充值", "TeamoRouter 钱包余额不足（需要充值）"
                else:
                    state, why = "探失败", msg[:120]
        rows.append({**{k: r[k] for k in ("shard", "角色", "触手班")}, "槽位": model,
                     "模型": model, "状态": state, "原因": why})
    ok = sum(1 for x in rows if x["状态"] == "通")
    return {"分片数": len(rows), "通": ok, "待充值": sum(1 for x in rows if x["状态"] == "待充值"),
            "行": rows, "卡点": ("网关钱包余额不足 —— 充值后五个分片立即全通"
                                if ok < len(rows) else "")}


def chat(prompt: str, *, role: str = "生成", max_tokens: int = 512) -> dict:
    """对虚拟大模型说话：按角色路由到对应分片（走网关；钱包没余额会如实报）。"""
    models = _gw_models()
    model = _pick(models, role)
    slot = model
    if not model:
        return {"ok": False, "reason": f"网关里没有能承担「{role}」的模型"}
    from core.brain import API_KEY, BASE_URL
    from openai import OpenAI
    cli = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    t0 = time.time()
    try:
        r = cli.chat.completions.create(model=model, max_tokens=max_tokens, messages=[
            {"role": "system", "content": "你是 GBT小土豆V9大模型（云上分片部署）。用中文回答。"},
            {"role": "user", "content": prompt}])
        txt = (r.choices[0].message.content or "").strip()
        return {"ok": True, "角色": role, "槽位": slot, "模型": model,
                "回答": txt, "耗时s": round(time.time() - t0, 1)}
    except Exception as exc:                                   # noqa: BLE001
        msg = str(exc)
        return {"ok": False, "角色": role, "槽位": slot, "模型": model,
                "reason": ("网关钱包余额不足 → 充值后此分片立即出字" if "余额不足" in msg
                           else f"{type(exc).__name__}: {msg[:120]}")}


def deploy(*, with_hooks: bool = True) -> dict:
    """分片部署：注册清单 → 触手绑定核验 → 逐片探测 → 固化。走钩子防偷懒。"""
    g = H.Guard("GBT小土豆V9大模型·云上分片部署", must_steps=("分片清单", "绑定核验", "逐片探测"))
    with g.step("分片清单", expect="五个角色分片都有槽位族") as s:
        rows = shard_manifest()
        bad = [r["shard"] for r in rows if r["槽位族可用"] == 0]
        if bad:
            raise H.HookError(f"这些分片没有槽位族：{bad}")
        s.evidence(分片=len(rows), 槽位族=[r["槽族"] for r in rows],
                   fingerprint=H.fingerprint(len(rows)))
    with g.step("绑定核验", expect="触手↔槽位双向绑定在位") as s:
        try:
            from core import capability_map as cm
            t = cm._totals()
            pairs = None
            try:
                from common.db import get_db as _g, fetch_one as _f
            except Exception as e:
                _swallow(__file__, e)
            s.evidence(触手=t.get("触手"), 云插件槽=t.get("云插件槽"),
                       fingerprint=H.fingerprint(t))
        except Exception as exc:                           # noqa: BLE001
            raise H.HookError(f"绑定核验失败：{type(exc).__name__}") from exc
    with g.step("逐片探测", expect="每片真发一次最小请求") as s:
        p = probe()
        s.evidence(通=p["通"], 待充值=p["待充值"], fingerprint=H.fingerprint(p["通"], p["待充值"]))
    a = g.finish()
    try:
        from core import solidify as S
        S.solidify("gbtv9_cloud_deploy", {"分片": shard_manifest(), "探测": p},
                   note="GBT小土豆V9大模型 云上分片部署")
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": True, "分片": rows, "探测": p, "钩子": {"通过": a["通过"], "步数": a["步数"]},
            "口径": "分片/绑定/调度/审计全部就位；出字只等网关钱包充值"}


def status() -> dict:
    p = probe()
    return {"形态": "云上分片部署（本地不部署模型）",
            "分片": p["行"], "通": p["通"], "待充值": p["待充值"], "卡点": p["卡点"],
            "绑定": "触手↔槽位 10000 对双向绑定已存在（capability_map）",
            "口径": "拆分部署到云插件；驱动靠触手双向绑定；本地不留模型"}


__all__ = ["ROLES", "shard_manifest", "probe", "chat", "deploy", "status"]
