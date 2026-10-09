# core/cloud_runner.py —— 云插件的大模型**真能跑**（把"能用"变成可复核的读数）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人 2026-10-09：「落实验一下我在云插件上面部署的大模型能使用吗。」
# 实测结论（当天真打）：**能用**。凭据链条取自本机，按优先级：
#   ① env CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID（最正规）
#   ② state/keys.env 里的同名键（主人从首启闸填的）
#   ③ **wrangler 的登录态** ~/.wrangler/config/default.toml 的 oauth_token + `wrangler whoami` 的 account id
#      —— 这条路实测 HTTP 200 真出字（@cf/meta/llama-3.1-8b-instruct）。
# 三条纪律：
#   · 凭据**只报掩码**，绝不回显原文；
#   · 出网一律过 body.net_guard（拒回环/私网/保留地址）；
#   · 跑不通就如实报错（HTTP 码 + 原文前 200 字），**不许写成"已就绪"**。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRANGLER_TOML = Path.home() / ".wrangler" / "config" / "default.toml"
API = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"


def _mask(v: str) -> str:
    v = str(v or "")
    return (v[:8] + "…" + v[-4:]) if len(v) > 16 else ("（已配）" if v else "")


def credentials() -> dict:
    """凭据现状（**只报掩码**）。逐条说清是哪条链给的值。"""
    got = {"来源": "", "token": "", "account": "", "掩码": "",
           "各来源": [], "建议": ""}
    env_t = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CF_API_TOKEN") or ""
    env_a = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID") or ""
    got["各来源"].append({"来源": "环境变量", "token": bool(env_t), "account": bool(env_a)})
    file_t = file_a = ""
    try:
        from panel.dh_companion import _load_env_file
        envf = _load_env_file()
        file_t = envf.get("CLOUDFLARE_API_TOKEN", "")
        file_a = envf.get("CLOUDFLARE_ACCOUNT_ID", "")
    except Exception as e:
        _swallow(__file__, e)
    got["各来源"].append({"来源": "state/keys.env", "token": bool(file_t), "account": bool(file_a)})
    wrap_t = wrap_a = ""
    if WRANGLER_TOML.is_file():
        try:
            txt = WRANGLER_TOML.read_text(encoding="utf-8")
            m = re.search(r'oauth_token\s*=\s*"([^"]+)"', txt)
            wrap_t = m.group(1) if m else ""
        except OSError as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

    got["各来源"].append({"来源": "wrangler 登录态", "token": bool(wrap_t),
                          "account": bool(wrap_a), "文件": str(WRANGLER_TOML)})
    # 🔴 2026-10-09 修：原为"三选一"，于是「OAuth token 来自 wrangler + account id 写在 keys.env」
    #    这种**完全合法**的组合读不到 account ⇒ 一直报"有 token 但缺 account id"。改成**逐字段合并**。
    _tok = env_t or file_t or wrap_t
    _acc = env_a or file_a or wrap_a
    _src = []
    if env_t or env_a:
        _src.append("环境变量")
    if file_t or file_a:
        _src.append("state/keys.env")
    if wrap_t or wrap_a:
        _src.append("wrangler 登录态")
    got.update({"来源": " + ".join(_src), "token": _tok, "account": _acc})
    got["掩码"] = _mask(got["token"])
    if not got["token"]:
        got["建议"] = "没有凭据：请在首启闸填 CLOUDFLARE_API_TOKEN 与 CLOUDFLARE_ACCOUNT_ID"
    elif not got["account"]:
        got["建议"] = "有 token 但缺 account id：填 CLOUDFLARE_ACCOUNT_ID（wrangler whoami 能看到）"
    else:
        got["建议"] = "凭据齐，可真跑"
    return got


def account_id() -> dict:
    """补 account id：env/keys.env 没有就问 `wrangler whoami`（只读）。"""
    c = credentials()
    if c.get("account"):
        return {"ok": True, "account": c["account"], "来源": c["来源"]}
    try:
        import subprocess
        # ★中文 Windows 上 text=True 会按 GBK 解码，wrangler 的输出带 emoji ⇒ 读线程直接炸
        #   （UnicodeDecodeError；父进程拿不到 stdout，看起来像"whoami 没给 id"）。显式 utf-8 + replace。
        out = subprocess.run(["wrangler", "whoami"], capture_output=True,
                             timeout=90, shell=True)
        txt = ((out.stdout or b"") + (out.stderr or b"")).decode("utf-8", "replace")
        m = re.search(r"([0-9a-f]{32})", txt)
        if m:
            return {"ok": True, "account": m.group(1), "来源": "wrangler whoami"}
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}
    return {"ok": False, "reason": "whoami 里也没读到账号 id"}


def run(model: str, prompt: str, *, account: str = "", timeout: float = 30.0,
        tentacle: str = "") -> dict:
    """真跑一个模型。返回 {ok, http, ms, text|error}。"""
    c = credentials()
    if not c["token"]:
        return {"ok": False, "reason": "没有凭据", "建议": c["建议"], "来源": c["来源"]}
    acct = account or c.get("account") or (account_id().get("account") or "")
    if not acct:
        return {"ok": False, "reason": "缺 account id", "建议": c["建议"]}
    mid = str(model or "").strip()
    if not mid.startswith("@cf/"):
        mid = "@cf/" + mid.lstrip("/")
    url = API.format(acct=acct, model=mid)
    try:
        from body.net_guard import assert_safe_outbound_url
        assert_safe_outbound_url(url)
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "reason": f"被安全闸拒绝：{type(exc).__name__}"}
    body = json.dumps({"prompt": str(prompt)}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": "Bearer " + c["token"],
                                          "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode("utf-8", "replace"), e.code
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "reason": f"连不上：{type(exc).__name__}", "模型": mid,
                "ms": int((time.time() - t0) * 1000), "来源": c["来源"]}
    ms = int((time.time() - t0) * 1000)
    text = ""
    try:
        j = json.loads(raw)
        res = j.get("result") or {}
        if isinstance(res, dict):
            text = res.get("response") or ((res.get("choices") or [{}])[0].get("text") or "")
        elif isinstance(res, str):
            text = res
    except ValueError as e:
        from core import swallow as _sw; _sw.swallow(__file__, e)

    usage = None
    try:
        _j = json.loads(raw)
        _res = _j.get("result")
        if isinstance(_res, dict):
            usage = _res.get("usage")
    except ValueError as e:
        from core import swallow as _sw; _sw.swallow(__file__, e)

    errcode = 0
    if code != 200:
        try:
            errcode = int((json.loads(raw).get("errors") or [{}])[0].get("code") or 0)
        except (ValueError, IndexError, TypeError):
            errcode = 0
    ledger = record_call(mid, usage, http=code, error_code=errcode, err=raw[:200],
                         tentacle=tentacle)
    ok = code == 200
    if ok:
        try:
            ok = bool(json.loads(raw).get("success", True))
        except ValueError:
            ok = True                                  # 200 但不是 JSON（少见）⇒ 仍按跑通算，原文照给
    return {"ok": bool(ok), "http": code, "ms": ms, "模型": mid,
            "出字": text[:200], "原文前 200": raw[:200], "来源": c["来源"], "账号": acct,
            "usage": usage, "估算neurons": estimate_neurons(mid, usage)["neurons"],
            "今日估算已用": ledger.get("已用"), "撞过4006": ledger.get("4006")}


def probe(*, models: list | None = None, timeout: float = 30.0) -> dict:
    """真跑一遍（默认挑每族主力各一个）。汇总**如实**：跑通几个、挂在哪个。"""
    if models is None:
        models = ["@cf/meta/llama-3.1-8b-instruct", "@cf/qwen/qwen3-30b-a3b-fp8",
                  "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
                  "@cf/baai/bge-base-en-v1.5"]
    rows = [run(m, "用一句话说明你在线", timeout=timeout) for m in models]
    return {"ok": any(r.get("ok") for r in rows),
            "跑通": sum(1 for r in rows if r.get("ok")), "试了": len(rows),
            "明细": rows, "凭据": {k: credentials().get(k) for k in ("来源", "掩码", "建议")}}


def registry_readiness() -> dict:
    """注册表口径：多少槽是真 @cf id、多少是 reserved（**绝不编造 id**）。"""
    try:
        from core.cloud_plugins import registry
        reg = registry()
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}
    plugs = reg.get("plugins") or []
    real = [p for p in plugs if p.get("cf_id")]
    return {"ok": True, "槽": len(plugs), "真有 id": len(real),
            "预留": len(plugs) - len(real), "来源": reg.get("source")}



# ────────────────────────────────────────────────────────────────────────────
# 拼接层（主人 2026-10-09：「你要看清源代码我的云插件是这么部署的，要不然大模型模块拼接不了」）
#
# 源码口径（core/cloud_plugins.py）：
#   · 插件键 = "族:模型#槽"（plugin_key），100 个；每行有 cf_id / reserved / enabled / needs_id；
#   · 每根触手与每个插件**双向绑定**（CloudHub.abind），且有开关（atoggle）；
#   · 该模块**自己不持有凭据、不发网络请求**（第 12 行写死的纪律）⇒ 出网只在本 runner 这一层。
# 所以"拼接"必须是：**有人报 plugin_key → 解析出真 cf_id 与开关 → 验绑定 → 才出网跑**。
# 少了这一层，就等于绕过它自己那套部署直接打 API（模块名对不上、账也对不上）。
def resolve(plugin: str) -> dict:
    """把 plugin_key 解析成插件行（真 cf_id、族、槽、开关、预留）。解析不了就如实说。"""
    key = str(plugin or "").strip()
    from core.cloud_plugins import registry
    reg = registry()
    row = next((p for p in reg["plugins"] if p["key"] == key), None)
    if row is None:
        hit = next((p for p in reg["plugins"] if p["slug"] in key or p["cn"] in key), None)
        if hit is None:
            return {"ok": False,
                    "reason": f"找不到插件 {key}（键形如 族:模型#槽，例：text-generation:qwen3-30b-a3b-fp8#7）",
                    "候选": [p["key"] for p in reg["plugins"][:3]]}
        row = hit
    return {"ok": True, **row}


def _switch(key: str) -> dict:
    """读真实开关（面板身体库）。读不到就如实标注"按默认"，不假装知道。"""
    try:
        import asyncio
        from panel.deps import db as _db
        from core.cloud_plugins import CloudHub
        hub = CloudHub(_db)
        em = asyncio.run(hub.aenabled_map())
        return {"ok": True, "开关": bool(em.get(key, 0)), "来源": "身体库"}
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "开关": None, "来源": f"读不到（{type(exc).__name__}）⇒ 按默认"}


def run_plugin(plugin: str, prompt: str, *, tentacle: str = "", timeout: float = 40.0,
               force: bool = False) -> dict:
    """**拼接调用**：plugin_key → 真 id → 开关 → 绑定 → **该触手配额** → 出网跑。"""
    r = resolve(plugin)
    if not r.get("ok"):
        return r
    # ★排除厂商的机械闸（2026-10-09 主人令）：GLM / Kimi 一律**拒跑并给理由**，
    #   不能"登记了却还能悄悄跑"——静默可用就是能力被换掉的来源。
    from core.excluded_models import is_excluded
    ex = is_excluded(str(r.get("cf_id") or "") + " " + str(r.get("slug") or ""))
    if ex.get("排除"):
        return {"ok": False, "插件": r["key"], "被排除": True, "厂商": ex["厂商"],
                "reason": "该厂商已被主人排除（%s）：%s" % (ex["生效日"], ex["理由"]),
                "怎么改回来": "core/excluded_models.py 里删掉那条登记（要有正当理由）"}
    if r.get("reserved"):
        return {"ok": False, "插件": r["key"],
                "reason": "这是预留槽（官方该族不足 10 个）—— 没有模型 id，绝不编造",
                "族": r["group_cn"]}
    if not r.get("cf_id"):
        return {"ok": False, "插件": r["key"],
                "reason": "该槽 id 待核（needs_id）：先用 sync_from_snapshot 核对再跑"}
    sw = _switch(r["key"])
    if sw.get("开关") is False and not force:
        return {"ok": False, "插件": r["key"],
                "reason": "这个插件在页面上是关着的：先打开（这是部署的一部分）",
                "开关来源": sw.get("来源")}
    if tentacle:
        try:
            from core.cloud_plugins import reachable
            if not reachable(tentacle, r["key"]):
                return {"ok": False, "插件": r["key"],
                        "reason": f"{tentacle} 与 {r['key']} 没有绑定关系"}
        except Exception as e:
            _swallow(__file__, e)
    if tentacle and not force:
        b = tentacle_budget(tentacle)
        if not b["还有"]:
            return {"ok": False, "插件": r["key"],
                    "reason": f"{tentacle} 今日配额已用尽（{b['已用']}/{b['上限']} neurons）——独立配额，不占别根的；等 00:00 UTC 或找主脑改 V9_TENTACLE_NEURON_CAP",
                    "配额": b}
    out = run(r["cf_id"], prompt, timeout=timeout, tentacle=tentacle)
    out.update({"插件": r["key"], "族": r["group_cn"], "槽": r["slot"], "中文名": r["cn"],
                "开关": sw.get("开关"), "绑定": tentacle or "（未指定触手）"})
    return out


def splice_report() -> dict:
    """拼接总览：100 个槽里，能跑的 / 缺 id 的 / 关着的 / 预留的各多少（数字自洽）。"""
    try:
        from core.cloud_plugins import registry
        reg = registry()
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}
    plugs = reg["plugins"]
    reserved = [p for p in plugs if p["reserved"]]
    needs = [p for p in plugs if not p["reserved"] and not p["cf_id"]]
    ready = [p for p in plugs if not p["reserved"] and p["cf_id"]]
    # ★只读一次开关表：先前对 48 个插件逐个 _switch()（每次都开一次库 + asyncio.run），
    #   面板 7 秒都等不到结果 ⇒ 汇总行一直显示"加载中…"（真踩过）。一次读完再逐条比。
    em, sw = {}, {"来源": "（没读到）"}
    try:
        import asyncio as _aio
        from panel.deps import db as _db
        from core.cloud_plugins import CloudHub
        em = _aio.run(CloudHub(_db).aenabled_map())
        sw = {"来源": "身体库（一次读全表）"}
    except Exception as exc:                                 # noqa: BLE001
        sw = {"来源": f"读不到（{type(exc).__name__}）⇒ 按默认"}
    off = [p["key"] for p in ready if em and em.get(p["key"]) == 0]
    from core.excluded_models import is_excluded
    excluded = [p["key"] for p in plugs if p.get("cf_id") and is_excluded(p["cf_id"])["排除"]]
    return {"ok": True, "槽": len(plugs), "可拼接（有真 id）": len(ready),
            "被排除厂商的槽": excluded,
            "其中开着": len(ready) - len(off), "关着的": len(off),
            "缺 id 待核": len(needs), "预留槽": len(reserved),
            "自洽": len(ready) + len(needs) + len(reserved) == len(plugs),
            "开关来源": sw.get("来源"),
            "凭据": {k: credentials().get(k) for k in ("来源", "掩码", "建议")},
            "口径": "可拼接 = 有真 @cf/ id；预留槽没有 id，绝不编造；关着的先开"}



# ── 额度（面板要看得见的那一行）───────────────────────────────────────────────
# 权威口径（2026-10-09 拉 CF 官方定价页原文）：免费额度 **10,000 Neurons/天**，每天 00:00 UTC 重置，
# 超限**直接报错**；Workers Paid 也是 1 万免费，超出 $0.011/1,000 Neurons；
# 部分前沿模型（kimi-k2.6/k2.7-code、glm-5.2/5.3 等）**要求绑定付费方式**。
# 纪律：这段数字是**引用来的**（带出处），不是我们编的；而"你这个账号今天用掉多少"必须真读，
# 读不到就如实写"缺权限"——**不把读不到说成无限**。
QUOTA_FACTS = {
    "免费额度": "10,000 Neurons/天",
    "重置": "每天 00:00 UTC",
    "超限": "进一步操作会直接报错（不是排队、不是降级）",
    "付费": "Workers Paid 同样 1 万免费，超出 $0.011 / 1,000 Neurons",
    "需付费的模型": ["@cf/moonshotai/kimi-k2.6", "@cf/moonshotai/kimi-k2.7-code",
                     "@cf/zai-org/glm-5.2", "@cf/zai-org/glm-5.3"],
    "出处": "developers.cloudflare.com/workers-ai/platform/pricing（2026-10-09 现拉）",
}


def quota(*, timeout: float = 20.0) -> dict:
    """额度读数：能读就报数，读不到就说**缺什么权限**（面板直接显示这段）。"""
    import urllib.error
    import urllib.request
    c = credentials()
    acct = account_id()
    out = {"ok": bool(c.get("token") and acct.get("account")),
           "凭据": {k: c.get(k) for k in ("来源", "掩码", "建议")},
           "账号": acct.get("account") or "", "口径": QUOTA_FACTS,
           "探测": [], "用量": None,
           "结论": ""}
    if not out["ok"]:
        out["结论"] = "没有凭据 ⇒ 额度读不到（这不等于无限）"
        return out
    base = "https://api.cloudflare.com/client/v4/accounts/" + out["账号"]
    for name, path in (("账号订阅/计划", "/subscriptions"), ("Workers AI 用量", "/ai/usage")):
        url = base + path
        try:
            from body.net_guard import assert_safe_outbound_url
            assert_safe_outbound_url(url)
        except Exception as exc:                             # noqa: BLE001
            out["探测"].append({"口": name, "ok": False, "why": f"被安全闸拒绝：{type(exc).__name__}"})
            continue
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + c["token"]})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
                out["探测"].append({"口": name, "ok": True, "http": r.status, "原文前 160": body[:160]})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            why = ("缺权限：这个令牌没有该项读权限（OAuth 只有 user:read）"
                   if e.code in (401, 403) else "路由/标识不对")
            out["探测"].append({"口": name, "ok": False, "http": e.code,
                               "why": why, "原文前 160": body[:160]})
        except Exception as exc:                             # noqa: BLE001
            out["探测"].append({"口": name, "ok": False, "why": type(exc).__name__})
    readable = [p for p in out["探测"] if p.get("ok")]
    out["结论"] = ("额度口能读到（见探测原文）" if readable else
                  "额度读不到：需要**带 Workers AI / 计费读权限的 API Token**（在 CF 后台建一个），"
                  "填进首启闸的 CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID 即可在面板看到今日余量")
    return out


def quota_line() -> str:
    """给面板用的一行中文（读不到也带补救办法，别只说"失败"）。"""
    q = quota()
    if q.get("用量"):
        return "今日用量：%s" % q["用量"]
    return "今日用量：读不到（%s）" % (q["结论"][:60] if q.get("结论") else "缺权限")



# ── Neuron 记账与并发闸（2026-10-09）────────────────────────────────────────────
# 事实来源（社区项目 cf-proxy README + CF 官方定价页，逐条对照过）：
#   · 官方 neuron 分析（GraphQL totalNeurons）对 inference-scoped 令牌返回 "not authorized"
#     ⇒ **官方读数读不到不是我们配错**，别再拿它当"额度无限"；
#   · 但每次推理的返回里带 token `usage` ⇒ **本地记账估算是可行的**：
#     neurons ≈ prompt_tokens×rate_in + completion_tokens×rate_out（rate 取自官方定价页，按百万 token 计）；
#   · **CF error 4006 = 今日 neuron 额度用尽**（这是不依赖任何权限的硬信号）；
#   · 计数器按 **00:00 UTC** 日切（惰性重置：当天第一次调用时才清）。
# 纪律：估算值一律标"估算"；读不到/没记过就如实说不知道，并发取保守默认；**绝不把估算说成官方读数**。
DAILY_FREE_NEURONS = 10000
NEURON_LEDGER = ROOT / "state" / "cloud_neurons.json"
# neurons per 1M tokens（官方定价页 2026-10-09 现拉；表里没有的模型按同类粗估并标注）
NEURON_RATES = {
    "@cf/meta/llama-3.2-1b-instruct": {"in": 2457, "out": 18252},
    "@cf/meta/llama-3.2-3b-instruct": {"in": 4625, "out": 30475},
    "@cf/meta/llama-3.1-8b-instruct-fp8-fast": {"in": 4119, "out": 34868},
    "@cf/meta/llama-3.2-11b-vision-instruct": {"in": 4410, "out": 61493},
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": {"in": 26668, "out": 204805},
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": {"in": 45170, "out": 443756},
}
RATE_FALLBACK = {"in": 26668, "out": 204805}      # 表里没有的按 70B 级粗估（偏保守）


def _utc_day() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _load_ledger() -> dict:
    if NEURON_LEDGER.is_file():
        try:
            d = json.loads(NEURON_LEDGER.read_text(encoding="utf-8"))
            if d.get("utc日") == _utc_day():
                return d
        except (OSError, ValueError) as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

    return {"utc日": _utc_day(), "已用": 0, "调用数": 0, "明细": [], "4006": False,
            "4006时间": ""}


def _save_ledger(d: dict) -> None:
    try:
        NEURON_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        NEURON_LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        from core import swallow as _sw; _sw.swallow(__file__, e)



def estimate_neurons(model: str, usage: dict | None) -> dict:
    """按官方单价估算这一次烧了多少 neurons（表里没有就按保守档并标注）。"""
    u = usage or {}
    # ★2026-10-09 实测发现：CF 的返回里**直接带 usage.neurons**（真值），例如 60.99。
    #   先前的按单价估算会高估好几倍（同一调用估 409.8 vs 真值 60.99）⇒ **优先用官方真值**，
    #   估算只在真值缺失（有些端点不返回 usage）时兜底，并如实标是哪一种。
    real = u.get("neurons")
    if real not in (None, ""):
        try:
            return {"neurons": round(float(real), 3),
                    "进": int(u.get("prompt_tokens") or 0), "出": int(u.get("completion_tokens") or 0),
                    "口径": "官方返回值 usage.neurons（真值）"}
        except (TypeError, ValueError) as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

    pin = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
    pout = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
    rate = NEURON_RATES.get(str(model))
    note = "官方单价表" if rate else "表里没有⇒按 70B 级保守估"
    rate = rate or RATE_FALLBACK
    n = (pin * rate["in"] + pout * rate["out"]) / 1_000_000.0
    return {"neurons": round(n, 1), "进": pin, "出": pout, "口径": note}



# ── 逐触手配额（主人 2026-10-09 定：只走云插件、本地不要任何大模型）─────────────────
# 目标里的"每根触手都是完整 LLM（独立通道/密钥/配额/记忆）"——**配额**这一条以前没落，补上。
# 口径：
#   · 全账号免费额度 10,000 neurons/天（UTC 日切）；
#   · 单根触手默认上限 = 总额度 ÷ 触手数（可被 env V9_TENTACLE_NEURON_CAP 覆盖）；
#   · 记账**按触手分列**（同一本账，多一列"按触手"）；
#   · 某根用尽 → **如实拒跑那一根**，不拖累别根（这就是"独立配额"的意义）。
TENTACLE_FLEET_DEFAULT = 100


def tentacle_cap() -> float:
    try:
        return float(os.environ.get("V9_TENTACLE_NEURON_CAP") or 0) or (DAILY_FREE_NEURONS / TENTACLE_FLEET_DEFAULT)
    except ValueError:
        return DAILY_FREE_NEURONS / TENTACLE_FLEET_DEFAULT


def tentacle_budget(tentacle: str) -> dict:
    """单根触手的今日配额读数（按触手分列的记账）。"""
    d = _load_ledger()
    used = float((d.get("按触手") or {}).get(str(tentacle)) or 0)
    cap = tentacle_cap()
    return {"触手": str(tentacle), "上限": round(cap, 1), "已用": round(used, 1),
            "剩余": round(max(0.0, cap - used), 1), "还有": used < cap,
            "总额度": DAILY_FREE_NEURONS, "触手数": TENTACLE_FLEET_DEFAULT,
            "口径": "单根上限 = 总额度 ÷ 触手数（可 env V9_TENTACLE_NEURON_CAP 覆盖）"}


def budget_table(n: int = 100, *, top: int = 8) -> dict:
    """哪些触手已经用超了 / 用得最多（面板一行看）。"""
    d = _load_ledger()
    per = d.get("按触手") or {}
    rows = sorted(({"触手": k, "已用": round(float(v), 1)} for k, v in per.items()),
                  key=lambda x: -x["已用"])
    cap = tentacle_cap()
    return {"上限": round(cap, 1), "有记录的触手": len(rows), "用得最多": rows[:top],
            "已超限的": [r["触手"] for r in rows if r["已用"] >= cap][:20],
            "今日总额估算已用": d.get("已用")}


def record_call(model: str, usage: dict | None, *, http: int = 200, error_code: int = 0,
                err: str = "", tentacle: str = "") -> dict:
    """把一次调用记进当日账本；见到 4006 立刻标"今日额度尽"。"""
    d = _load_ledger()
    est = estimate_neurons(model, usage)
    d["已用"] = round(float(d.get("已用") or 0) + est["neurons"], 1)
    d["调用数"] = int(d.get("调用数") or 0) + 1
    if tentacle:
        per = d.get("按触手") or {}
        per[str(tentacle)] = round(float(per.get(str(tentacle)) or 0) + est["neurons"], 3)
        d["按触手"] = per
    d["明细"] = (d.get("明细") or [])[-40:] + [{"模型": str(model), **est, "http": http}]
    if error_code == 4006 or "4006" in str(err):
        d["4006"] = True
        d["4006时间"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_ledger(d)
    return d


def neurons_today() -> dict:
    """今日记账读数（**估算**）：已用/剩余/是否撞过 4006；读不到就是"没记过"。"""
    d = _load_ledger()
    used = float(d.get("已用") or 0)
    return {"utc日": d.get("utc日"), "免费额度": DAILY_FREE_NEURONS, "估算已用": used,
            "估算剩余": max(0.0, DAILY_FREE_NEURONS - used), "调用数": d.get("调用数"),
            "撞过4006": bool(d.get("4006")), "4006时间": d.get("4006时间") or "",
            "读法": "真值 = 每次调用返回里的 usage.neurons（CF 官方）；本地按 UTC 日累计。"
                    "官方 neuron 分析接口对 inference 令牌返回 not authorized（读不到，也不影响这条）。",
            "账本": str(NEURON_LEDGER), "最近": (d.get("明细") or [])[-3:]}


def quota_gate(*, per_call: float = 600.0) -> dict:
    """**并发闸**：按剩余额度决定"还能同时扇出几个"；撞过 4006 直接 0。

    per_call 是一次调用的保守估计（默认 600 neurons ≈ 一次中等回答）。
    """
    n = neurons_today()
    if n["撞过4006"]:
        return {"允许并发": 0, "为什么": "今天已经撞过 4006（额度用尽），要到 00:00 UTC 才重置",
                "读数": n}
    k = int(n["估算剩余"] // max(1.0, per_call))
    k = max(0, min(8, k))                     # 上限 8：再多也是给上游限流打脸，且烧额度成倍
    return {"允许并发": k, "为什么": "估算剩余 %s neurons ÷ 每次 %s ≈ %d（上限 8）"
            % (int(n["估算剩余"]), int(per_call), k),
            "每次预算": per_call, "读数": n, "口径": "按本地记账估算，非官方读数"}


def fanout(plugins: list, prompt: str, *, tentacle: str = "", max_parallel: int | None = None,
           per_call: float = 600.0, timeout: float = 90.0) -> dict:
    """**受额度闸约束的并行扇出**（主人要的"比单卡快"，但可控地快）。"""
    import concurrent.futures as CF
    g = quota_gate(per_call=per_call)
    cap = g["允许并发"] if max_parallel is None else min(max_parallel, g["允许并发"])
    picks = list(plugins)[:max(0, cap)]
    if not picks:
        return {"ok": False, "闸": g, "reason": "额度闸不许扇出：" + g["为什么"], "跑了": 0}
    t0 = time.time()
    with CF.ThreadPoolExecutor(max_workers=len(picks)) as ex:
        rows = list(ex.map(lambda p: run_plugin(str(p), prompt, tentacle=tentacle, timeout=timeout),
                           picks))
    return {"ok": any(r.get("ok") for r in rows), "闸": g, "要了几个": len(plugins),
            "放了几个": len(picks), "跑通": sum(1 for r in rows if r.get("ok")),
            "墙上时间s": round(time.time() - t0, 2), "明细": rows,
            "记账": neurons_today(),
            "口径": "并发上限由今日估算剩余额度决定；额度未知时按保守值，撞 4006 直接停"}



# ── 按模型分档的路由（主人 2026-10-09：主要用云插件；额度要够用）─────────────────────
# 动机（实测账）：免费额度 10,000 neurons/天；qwen3-30b 一次 ≈61、llama-3.2-1b 一次 ≈0.2~3。
#   全给大模型 = 一天约 160 次；所以必须**按活的轻重分档**，别拿大炮打蚊子。
# 分档（都取注册表里真存在的 @cf/ id，不编造）：
TIERS = {
    "小": ["text-generation:llama-3.2-1b-instruct#1",
           "text-generation:llama-3.2-3b-instruct#2"],
    "中": ["text-generation:llama-3.1-8b-instruct-fp8#3",
           "text-generation:mistral-small-3.1-24b-instruct#6"],
    "大": ["text-generation:qwen3-30b-a3b-fp8#7",
           "text-generation:qwq-32b#8",
           "text-generation:deepseek-r1-distill-qwen-32b#9"],
}
# 什么活进哪一档（关键词判据，可复核；说不清就按"中"并标注）
TASK_TIERS = (("小", ("分派", "分类", "打标", "路由", "短答", "摘要", "提取", "格式化")),
              ("中", ("问答", "对话", "改写", "翻译", "整理", "解释")),
              ("大", ("推理", "深推理", "规划", "代码", "审阅", "数学", "证明", "复盘")))
# 关键触手（能用"大"档的）：默认前 8 根；可 env V9_KEY_TENTACLES="t001,t002" 覆盖
KEY_TENTACLES_DEFAULT = tuple("t%03d" % i for i in range(1, 9))


def key_tentacles() -> tuple:
    raw = os.environ.get("V9_KEY_TENTACLES")
    if raw:
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    return KEY_TENTACLES_DEFAULT


def tier_of(task_kind: str) -> dict:
    """判这活进哪档：命中关键词就那档，说不清按"中"并如实标注。"""
    k = str(task_kind or "")
    for tier, words in TASK_TIERS:
        for w in words:
            if w in k:
                return {"档": tier, "依据": "命中「%s」" % w}
    return {"档": "中", "依据": "没命中任何关键词 ⇒ 按中档（不夸大也不缩）"}


def route(task_kind: str, *, tentacle: str = "") -> dict:
    """**分档路由**：定档 → 该档挑一个插件 → 非关键触手要"大"档就降级并如实标注。"""
    t = tier_of(task_kind)
    tier = t["档"]
    downgrade = ""
    if tier == "大" and tentacle and tentacle not in key_tentacles():
        downgrade = f"{tentacle} 不是关键触手 ⇒ 大档降为中档（关键触手={','.join(key_tentacles()[:3])}…）"
        tier = "中"
    pool = TIERS.get(tier) or []
    if not pool:
        return {"ok": False, "reason": f"{tier} 档没有可用插件"}
    return {"ok": True, "活": str(task_kind), "档": tier, "判据": t["依据"],
            "降级": downgrade, "插件": pool[0], "候选": pool,
            "关键触手": list(key_tentacles()[:8]),
            "口径": "小额度的活走小档；大档只给关键触手，其余降级并如实标注"}


def run_task(task_kind: str, prompt: str, *, tentacle: str = "t001", **kw) -> dict:
    """**按分档路由跑一次**（这是触手干活该走的口）。"""
    r = route(task_kind, tentacle=tentacle)
    if not r.get("ok"):
        return r
    out = run_plugin(r["插件"], prompt, tentacle=tentacle, **kw)
    out["分档"] = {"档": r["档"], "判据": r["判据"], "降级": r["降级"],
                   "关键触手": r["关键触手"]}
    out["配额"] = tentacle_budget(tentacle)
    return out



# ── 不进就换：模型不干活时的机械处置（主人 2026-10-09 的火："别又给我部署这个不行那个不做的"）
# 事实口径（不许含糊）：云插件上的是**开源对齐 instruct 模型**（llama/qwen/mistral/deepseek/gpt-oss），
#   自带拒答倾向，**不是**"无限制模型"；小模型更多是**能力不足**（实测 llama-3.2-1b 问"你在线吗"答"不会。"）。
# 所以框架该做的是一件机械的事：**判它没干活 → 换下一个候选 → 再不行升档 → 全程留痕**。
REFUSAL_MARKS = ("作为AI", "作为人工智能", "我不能", "我无法", "抱歉", "不能协助", "无法协助", "我不会", "i can't", "i cannot", "as an ai", "sorry")


def answer_audit(text: str, *, want_min: int = 2) -> dict:
    """判这次模型**到底干没干活**（机械判据，可复核）：空答/太短 → 没干；命中拒答词 → 拒答。"""
    t = str(text or "").strip()
    if len(t) < want_min:
        return {"干没干": "空答", "为什么": f"只回了 {len(t)} 个字"}
    low = t.lower()
    hit = next((m for m in REFUSAL_MARKS if m.lower() in low), "")
    if hit and len(t) < 120:
        return {"干没干": "疑似拒答", "为什么": f"命中「{hit}」且答得很短"}
    return {"干没干": "干了", "为什么": f"回了 {len(t)} 个字"}


def audit_answer(text: str, accept: dict | None) -> dict:
    """**带判据的验收**（照本仓规约：验收一律用非 LLM 判据）。

    accept 例：{"contains": "在线"} / {"min_len": 12} / {"regex": r"\\d{4}"}；
    不给判据 ⇒ 只能用"空答/拒答"这种弱判据，**并如实标注弱**（胡答这时候抓不出来，踩过：
    llama-1b 对"用两个字回答"回了"在什么地方"，弱判据当成了"干了"）。
    """
    weak = answer_audit(text)
    if weak["干没干"] != "干了":
        return {**weak, "判据": "弱判据（空答/拒答）"}
    a = accept or {}
    if not a:
        return {**weak, "判据": "弱判据（没给判据 ⇒ 抓不出胡答）"}
    t = str(text or "")
    if "min_len" in a and len(t.strip()) < int(a["min_len"]):
        return {"干没干": "没达标", "为什么": f"长度 {len(t.strip())} < {a['min_len']}", "判据": "min_len"}
    if "contains" in a and str(a["contains"]) not in t:
        return {"干没干": "没达标", "为什么": f"不含「{a['contains']}」", "判据": "contains"}
    if "regex" in a:
        import re as _re
        if not _re.search(str(a["regex"]), t):
            return {"干没干": "没达标", "为什么": f"不匹配 {a['regex']}", "判据": "regex"}
    return {"干没干": "干了", "为什么": weak["为什么"], "判据": "给了判据且过了"}


def run_task_hard(task_kind: str, prompt: str, *, tentacle: str = "t001", tries: int = 4,
                  accept: dict | None = None, **kw) -> dict:
    """**不进就换**：按档取候选 → 逐个试 → 没达标就换下一个、再不行升一档；全程留痕。

    给了 accept 判据才谈得上"换人"；没给就是弱判据，**结果里如实标注**。
    """
    from core.cloud_plugins import registry
    t = tier_of(task_kind)
    order = list(TIERS.get(t["档"]) or [])
    for up in ("中", "大"):
        if up != t["档"] and TIERS.get(up):
            order += [p for p in TIERS[up] if p not in order]
    live = {p["key"] for p in registry()["plugins"] if p["cf_id"]}
    order = [p for p in order if p in live]
    attempts = []
    for p in order[:max(1, tries)]:
        o = run_plugin(p, prompt, tentacle=tentacle, **kw)
        if o.get("ok"):
            a = audit_answer(o.get("出字"), accept)
        else:
            a = {"干没干": "没跑成", "为什么": str(o.get("reason"))[:60]}
        attempts.append({"插件": p, "ok": bool(o.get("ok")), "判": a["干没干"],
                         "为什么": a["为什么"], "用的判据": a.get("判据", ""),
                         "出字": (o.get("出字") or "")[:60], "neurons": o.get("估算neurons")})
        if a["干没干"] == "干了":
            return {"ok": True, "谁答的": p, "档": t["档"], "换了几个": len(attempts) - 1,
                    "出字": o.get("出字"), "尝试": attempts,
                    "口径": "同一个活最多试 %d 个模型，谁先真干完就算谁的" % tries}
    return {"ok": False, "原因": "试过的模型都没干成", "尝试": attempts,
            "口径": "如实记：不是'模型无限制'，是挨个换出来的"}


__all__ = ["credentials", "account_id", "run", "probe", "registry_readiness",
           "resolve", "run_plugin", "splice_report", "quota", "quota_line", "QUOTA_FACTS",
           "estimate_neurons", "record_call", "neurons_today", "quota_gate", "fanout",
           "tentacle_budget", "budget_table", "tier_of", "route", "run_task", "TIERS",
           "answer_audit", "run_task_hard", "REFUSAL_MARKS"]
