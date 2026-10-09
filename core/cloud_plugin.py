# core/cloud_plugin.py —— 云插件：**一根触手一份**，一份能调很多面
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「说了使用云插件，但是云插件这次要装在**每根触手**上，
#   配置一个云插件可调用很多东西。」
# 本件把云插件做成**编队级登记**（不是全局一份）：
#   · install(tentacle)  给某根触手装一份云插件（登记槽位 + 独立配额 + 审计）
#   · install_fleet(100) 全编队装齐
#   · catalog()          一份插件能调哪些"面"（文本/视觉/图像/向量/语音…取自 registry 实测）
#   · invoke(...)        走 cloud_runner.run_plugin（**独立配额，不占别根的**）
#   · status()           一屏：谁装上了 · 今日各用多少 · 上限多少
# 凭据缺失时**如实报未配**，绝不假装能调（cloud_runner.credentials 是唯一判据）。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "cloud_plugins.json"      # 编队级：触手 → 插件槽
AUDIT = ROOT / "state" / "cloud_plugin_audit.jsonl"  # 每次装卸/调用

# 一个插件能调的"面"：面 → 说明（真实模型名从 cloud_runner 的注册表现读，不写死）
FACES: dict = {
    "文本": "对话/写作/推理（Workers AI 文本模型）",
    "视觉": "看图/识图/OCR（多模态模型）",
    "图像": "文生图/图生图",
    "向量": "文本嵌入（检索/聚类/去重）",
    "语音": "语音识别/合成（若注册表内有）",
    "翻译": "多语翻译",
    "代码": "代码生成/解释",
}


def _load() -> dict:
    if LEDGER.is_file():
        try:
            return json.loads(LEDGER.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save(d: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _audit(rec: dict) -> None:
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception as e:
        _swallow(__file__, e)


def catalog() -> dict:
    """一份云插件能调什么：面 + （真实）注册表就绪读数。"""
    from core import cloud_runner as CR
    ready = {}
    try:
        ready = CR.registry_readiness() or {}
    except Exception as e:  # noqa: BLE001
        ready = {"error": "%s: %s" % (type(e).__name__, e)}
    creds = {}
    try:
        creds = CR.credentials() or {}
    except Exception as e:
        _swallow(__file__, e)
    return {"面": [{"面": k, "说明": v} for k, v in FACES.items()],
            "注册表就绪": ready, "凭据": {k: creds.get(k) for k in ("有token", "有account", "已配置", "建议") if k in creds} or creds,
            "口径": "面是能力分类；真能不能调看『注册表就绪』与『凭据』，缺就照实说未配"}


def install(tentacle: str, *, plugin: str = "gbt-cloud-ai", note: str = "") -> dict:
    """给**某一根**触手装一份云插件（登记槽位；幂等）。"""
    d = _load()
    slot = {"tentacle": tentacle, "plugin": plugin, "装于": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "独立配额": "V9_TENTACLE_NEURON_CAP（不占别根的）", "备注": note}
    fresh = tentacle not in d
    d[tentacle] = slot
    _save(d)
    _audit({"at": slot["装于"], "动作": "install" if fresh else "reinstall", "tentacle": tentacle, "plugin": plugin})
    return {"ok": True, "tentacle": tentacle, "新装": fresh, "槽": slot}


def install_fleet(n: int = 100, *, plugin: str = "gbt-cloud-ai") -> dict:
    ok = 0
    for i in range(1, n + 1):
        try:
            install("t%03d" % i, plugin=plugin)
            ok += 1
        except Exception as e:
            _swallow(__file__, e)
    return {"ok": ok == n, "装齐": ok, "共": n, "台账": str(LEDGER.relative_to(ROOT))}


FACE_FAMILY: dict = {"文本": "text-generation", "翻译": "text-generation", "代码": "text-generation",
               "视觉": "image-to-text", "图像": "text-to-image",
               "向量": "text-embeddings", "语音": "automatic-speech-recognition"}


def pick_plugin(face: str = "文本") -> dict:
    """面 → 真实插件键（族:模型#槽）。键形如 text-generation:llama-3.2-1b-instruct#1。"""
    from core import cloud_runner as CR
    fam = FACE_FAMILY.get(face, "text-generation")
    for probe_key in (fam, fam + ":auto"):
        try:
            r = CR.resolve(probe_key)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}
        if r.get("ok"):
            return {"ok": True, "键": r.get("key") or r.get("插件") or probe_key, "原始": r}
        cand = r.get("候选") or []
        if cand:
            return {"ok": True, "键": cand[0], "候选数": len(cand)}
    return {"ok": False, "reason": "该面没有可用插件键", "面": face}


def invoke(tentacle: str, prompt: str, *, face: str = "文本", plugin: str = "", timeout: float = 40.0) -> dict:
    """调这根触手的云插件（走 cloud_runner.run_plugin ⇒ 独立配额 + 排除厂商检查）。"""
    from core import cloud_runner as CR
    d = _load()
    if tentacle not in d:
        return {"ok": False, "reason": "这根触手还没装云插件（先 install）", "tentacle": tentacle}
    # 插件键必须是"族:模型#槽"真键；不给就按面现挑一个（原版传 gbt-cloud-ai 是错的，实测 resolve 会拒）
    if plugin:
        pl = plugin
    else:
        pick = pick_plugin(face)
        if not pick.get("ok"):
            return {"ok": False, "reason": pick.get("reason"), "tentacle": tentacle, "面": face}
        pl = pick["键"]
    t0 = time.time()
    try:
        r = CR.run_plugin(pl, prompt, tentacle=tentacle, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        r = {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}
    out = {"ok": bool(r.get("ok")), "tentacle": tentacle, "面": face, "plugin": pl,
           "结果": r, "ms": int((time.time() - t0) * 1000)}
    _audit({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "invoke", "tentacle": tentacle,
            "面": face, "plugin": pl, "ok": out["ok"], "为什么": str(r.get("reason") or "")[:160]})
    return out


def status() -> dict:
    """一屏：谁装上了 · 今日各用多少 · 上限多少 · 总调用。"""
    d = _load()
    from core import cloud_runner as CR
    q = {}
    try:
        q = CR.splice_report() or {}
    except Exception as e:
        _swallow(__file__, e)
    rows = []
    for t, slot in sorted(d.items()):
        per = {}
        try:
            per = (q.get("每根") or {}).get(t) or {}
        except Exception:  # noqa: BLE001
            per = {}
        rows.append({"tentacle": t, "plugin": slot.get("plugin"), "今日已用": per.get("已用"),
                     "上限": per.get("上限"), "超出": per.get("超限")})
    calls = 0
    if AUDIT.is_file():
        for line in AUDIT.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("动作") == "invoke":
                    calls += 1
            except Exception as e:
                _swallow(__file__, e)
    return {"装了几根": len(d), "明细": rows, "总调用": calls, "配额源": "cloud_runner.splice_report()"}


def run(op: str = "status", **kw) -> dict:
    if op in ("status", "report", ""):
        return status()
    if op == "catalog":
        return catalog()
    if op == "install":
        return install(kw.get("tentacle", "t001"), plugin=kw.get("plugin", "gbt-cloud-ai"))
    if op == "install_fleet":
        return install_fleet(int(kw.get("n") or 100))
    if op == "invoke":
        return invoke(kw.get("tentacle", "t001"), kw.get("prompt", ""), face=kw.get("face", "文本"))
    return {"ok": False, "error": "unknown operation: %s" % op}


__all__ = ["FACES", "FACE_FAMILY", "catalog", "install", "install_fleet", "invoke", "status",
           "pick_plugin", "run", "LEDGER"]
