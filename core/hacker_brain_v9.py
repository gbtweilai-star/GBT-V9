# core/hacker_brain_v9.py —— 黑客大脑（**GBT小土豆V9 专属部署**，不与 V8 混）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「黑客大脑以及黑客全能力你重新部署备注好 GBT小土豆V9，别跟 V8 的混到一起，
#   大模型和密钥使用同一个没事」+ 给了接入文档 https://docs.abliteration.ai/integrations/claude-code
#
# 文档原文要点（我照抄，不臆测）：
#   · ANTHROPIC_BASE_URL   = https://api.abliteration.ai
#   · ANTHROPIC_AUTH_TOKEN = ak_...（推荐；ANTHROPIC_API_KEY 亦可，按 x-api-key 发）
#   · 模型：abliterated-model-large-v2[1m]（1M 上下文，纯文本，官方推荐）
#           abliterated-model（262K，图文）· abliterated-model-large[1m]（1M）
#   · 兼容 OpenAI- 与 Anthropic-compatible 两套 API
# 本件与 V8 的界线（硬）：
#   · 每条记录都带 归属="GBT小土豆V9"；出现 V8 字样立刻标 mixed=True 并拒绝当成本 V9 的部署；
#   · 配置落在 state/hacker_brain_v9.json（**V9 自己的文件**），不读不写 V8 的任何 state 文件。
# 护栏（本仓红线，不松）：安全域能力位挂 external_llm 作用域 ⇒ **要主人在终端授权**；AI 不许给自己发授权。
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "state" / "hacker_brain_v9.json"
LEDGER = ROOT / "state" / "hacker_brain_v9_ledger.jsonl"
OWNER = "GBT小土豆V9"
V8_MARKS = ("v8", "V8", "小土豆V8")

# 官方文档给的接入参数（原样登记，便于复核）
API = {
    "base_url": "https://api.abliteration.ai",
    "auth_env": ("ABLITERATION_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "默认模型": "abliterated-model-large-v2",
    "模型表": [
        {"名": "abliterated-model-large-v2", "上下文": 1000000, "输入": "纯文本", "定位": "官方推荐·编码"},
        {"名": "abliterated-model", "上下文": 262144, "输入": "文本+图像", "定位": "图文"},
        {"名": "abliterated-model-large", "上下文": 1000000, "输入": "纯文本", "定位": "上一代大号"},
    ],
    "来源": "docs.abliteration.ai/integrations/claude-code（2026-10-09 现读）",
}

# 黑客全能力（安全域）在 V9 的登记：能力位 → 干什么 → 门
CAPS = (
    {"能力位": "sec_surface", "干什么": "攻击面清点（端口/服务/暴露面）", "门": "只读"},
    {"能力位": "sec_scan", "干什么": "漏洞扫描（依赖/配置/已知 CVE 比对）", "门": "只读"},
    {"能力位": "sec_chain", "干什么": "攻击链推演（把散点串成路径，用于**防御**加固）", "门": "只读"},
    {"能力位": "sec_defenses", "干什么": "防御验证（挡不挡得住、有没有盲区）", "门": "只读"},
    {"能力位": "sec_llm", "干什么": "黑客大脑问答（由**本件这颗脑子**驱动）", "门": "external_llm（要主人授权）"},
)


def key() -> tuple:
    for e in API["auth_env"]:
        v = os.environ.get(e, "").strip()
        if v:
            return v, e
    return "", ""


# 判定"混入 V8"只看**部署实体字段**：我不能把自己说明文字里的 "V8" 也算成混入（那是误报）
MIX_SCAN_KEYS = ("本件", "路径", "文件", "配置", "产物", "落点", "来源", "凭据文件", "能力位路径")


def mark(rec: dict) -> dict:
    """给记录盖 V9 章；只在**实体字段**里出现 V8 才判 mixed（说明文字不算）。"""
    rec["归属"] = OWNER
    blob = json.dumps({k: v for k, v in rec.items() if k in MIX_SCAN_KEYS}, ensure_ascii=False)
    rec["mixed"] = any(m in blob for m in V8_MARKS)
    rec["mixed判据"] = "只扫实体字段 %s（说明文字不算，避免自误报）" % list(MIX_SCAN_KEYS)
    return rec


def deploy(*, note: str = "黑客大脑·GBT小土豆V9 独立部署") -> dict:
    k, src = key()
    rec = mark({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "本件": "core/hacker_brain_v9.py",
        "说明": note,
        "API": API,
        "能力位": list(CAPS),
        "凭据": {"已配": bool(k), "来源": src or "未配",
                 "掩码": (k[:6] + "…") if k else ""},
        "护栏": "安全域 external_llm 要主人授权；AI 不许自己发授权",
        "与V8的界线": "配置只落 state/hacker_brain_v9.json；记录带 V9 归属；含 V8 字样即标 mixed",
    })
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": rec["at"], "动作": "deploy", "归属": OWNER,
                            "凭据已配": bool(k)}, ensure_ascii=False) + chr(10))
    return rec


def probe(*, timeout: float = 25.0) -> dict:
    """真探这颗脑子：① 端点可达性 ② 凭据 ③ 用它的 OpenAI 兼容口要一句话。"""
    k, src = key()
    out = mark({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "端点": API["base_url"],
                "凭据": {"已配": bool(k), "来源": src or "未配",
                        "掩码": (k[:6] + "…") if k else ""}})
    try:
        req = urllib.request.Request(API["base_url"] + "/v1/models",
                                     headers={"Authorization": "Bearer " + (k or "none")})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        ids = [m.get("id") for m in (d.get("data") or [])][:10]
        out["端点可达"] = True
        out["模型列表"] = ids
        out["秒"] = round(time.time() - t0, 1)
    except urllib.error.HTTPError as e:
        out["端点可达"] = e.code not in (404, 502, 503)
        out["http"] = e.code
        out["原文"] = e.read()[:150].decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        out["端点可达"] = False
        out["失败"] = "%s: %s" % (type(e).__name__, str(e)[:90])
    if not k:
        out["结论"] = "端点参数已登记；**凭据未配**（ABLITERATION_API_KEY / ANTHROPIC_AUTH_TOKEN 都没设）⇒ 只能到这一步"
    elif not out.get("端点可达"):
        out["结论"] = "凭据有，但端点没通（见失败/HTTP 读数）"
    else:
        out["结论"] = "凭据 + 端点都就绪，可跑 sec_llm（仍要主人授权 external_llm）"
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": out["at"], "动作": "probe", "归属": OWNER,
                            "端点可达": out.get("端点可达"), "凭据已配": bool(k)},
                           ensure_ascii=False) + chr(10))
    return out


def status() -> dict:
    conf = {}
    if CONF.is_file():
        try:
            conf = json.loads(CONF.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            conf = {}
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-6:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"归属": OWNER, "部署档": str(CONF.relative_to(ROOT)) if CONF.is_file() else "未部署",
            "API": API["base_url"], "默认模型": API["默认模型"],
            "凭据已配": bool(key()[0]), "能力位": [c["能力位"] for c in CAPS],
            "最近": rows, "mixed": conf.get("mixed", False),
            "口径": "V9 独立部署（不与 V8 混）；安全域 external_llm 要主人授权"}


__all__ = ["OWNER", "API", "CAPS", "CONF", "LEDGER", "key", "deploy", "probe", "status"]
