# core/llm_roles.py —— 角色→模型 钉死表 + 变更登记（主脑/黑客大脑/触手 各认各的脑子）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人 2026-10-09：「我的黑客大脑一直用的是专属模型和密钥，我说的是**主脑**接入 GLM。」
#   —— 查证结论（V8 源码为凭）：
#     · 主脑认 GBT_LLM_MODEL / GBT_LLM_BASE_URL / GBT_LLM_API_KEY（freellmapi.py:275），
#       默认值是 **deepseek-flash**（caps/design_brain/run.py:25）；
#     · GLM 是从 NVIDIA 网关接进来的（key_gateway.py:155/159 的 z-ai/glm-5.3、glm-5.3-flash）；
#     · 无输出的三条实测记录（key_gateway.py:141/151/179）：
#         ① glm-5.3 **冷启动 >240s**（超时⇒空）② **reasoning_content 有、content 空**（思考吃掉正文）
#         ③ free 版「一律回」。
#   所以"能力被阉割"是**脑子被换**：不是文件被删。本表把"谁该用哪个脑子"钉死，谁也别悄悄换。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "llm_role_changes.jsonl"

ROLES: tuple = (
    dict(角色="主脑", env=("GBT_LLM_MODEL", "GBT_LLM_BASE_URL", "GBT_LLM_API_KEY"),
         默认模型="deepseek-flash", 允许降级=False,
         口径="主脑的脑子由 env 指定；**换模型=换脑子**，必须先登记并做能力体检",
         已知坑="接 GLM 后：glm-5.3 冷启动>240s / reasoning_content 有而 content 空 / free 版一律回"),
    dict(角色="黑客大脑", env=("ABLITERATION_API_KEY",), 默认模型="abliterated-model-large-v2",
         允许降级=False,
         口径="专属模型+专属密钥；源码明写「不做降级、不混用通用 LLM 通道」",
         已知坑="经统一门调 sec_llm 必抛（api.py:316-321）⇒ 只能直连自己的通道"),
    dict(角色="触手", env=("TENTACLE_<Tnnn>_KEY", "TENTACLE_<Tnnn>_MODEL"), 默认模型="fleet-default",
         允许降级=True,
         口径="逐触手钥匙/模型；拿不到独立凭据要**如实标 shared-fallback**"),
    dict(角色="本地/离线", env=("GBT_LOCAL_LLM_MODEL",), 默认模型="qwen3:latest", 允许降级=True,
         口径="本地方案（主人已定：不做本地大模型，仅留兜底口径）"),
)


def current() -> dict:
    """当前每个角色实际指到哪（现读 env；不猜）。"""
    import os
    rows = []
    for r in ROLES:
        got = {e: ("已配" if os.environ.get(e) else "未配") for e in r["env"]}
        model = os.environ.get(r["env"][0]) or r["默认模型"] if r["env"] else r["默认模型"]
        rows.append({"角色": r["角色"], "模型": model, "env": got, "允许降级": r["允许降级"]})
    return {"行": rows}


def check() -> dict:
    """**排除厂商检查**：任何角色若指向 GLM/Kimi，明确报警（这正是'主脑接 GLM'的发生口）。"""
    from core.excluded_models import is_excluded
    alarms, rows = [], current()["行"]
    for r in rows:
        v = is_excluded(r["模型"])
        r["被排除厂商"] = bool(v["排除"])
        if v["排除"]:
            alarms.append({"角色": r["角色"], "模型": r["模型"], "厂商": v["厂商"],
                           "理由": v["理由"], "处置": "改回该角色的默认模型，或明确登记为临时试验"})
    return {"角色数": len(rows), "行": rows, "报警": alarms,
            "口径": "被排除厂商出现在任何角色上都要报警——**静默切换就是能力被阉割的来源**"}


def note_change(role: str, from_model: str, to_model: str, *, by: str = "未记", why: str = "") -> dict:
    """记一次模型变更（谁改的/从什么到什么/为什么）。**这就是以后举证用的那条记录。**"""
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "角色": role, "从": from_model, "到": to_model,
           "谁改的": by, "为什么": why}
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        _swallow(__file__, e)
    return {"ok": True, **rec, "台账": str(LEDGER)}


def history(limit: int = 30) -> dict:
    if not LEDGER.is_file():
        return {"ok": True, "条数": 0, "行": [], "说明": "还没登记过模型变更"}
    rows = [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()][-limit:]
    return {"ok": True, "条数": len(rows), "行": rows}


__all__ = ["ROLES", "current", "check", "note_change", "history", "LEDGER"]
