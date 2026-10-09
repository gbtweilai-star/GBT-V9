# core/tentacle_keys.py —— 逐触手凭据册：让「每根触手一个完整 LLM」在凭据层真成立
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 真机病因（2026-10-08 逐行复核）：
#   · tentacle_fleet.py:619-620 build() 给每根触手传的都是**同一把** self.key；
#   · :339-345 UnifiedKey.client_for() 用回自己那把 _key（只是每根一个客户端对象）；
#   · :8 注释声称保留 mode='isolated'，而 :534 只存 self.mode，build()/client() 从不分支；
#   · config.yaml:7-16 声明了 tentacle-t1/t2 各自 key，**全仓没有任何 .py 读它**。
#   ⇒ 所以「每触手一把钥匙」是注释，不是代码。本模块把凭据解析做出来。
#
# 口径（严格照本仓纪律）：
#   · 凭据**只从环境变量/本机落盘读**，源码零字面量，本模块只回指纹不回显原文；
#   · 找不到独立凭据时**不假装有**：回退到统一密钥时如实标 source=shared-fallback / own=False；
#   · mode='isolated' 下没有独立凭据的触手 → 明确不可用（loaded=False），**不静默回退**；
#   · 不引入新的第三方依赖（不读 yaml，走环境变量 + JSON 映射）。
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_FILE = Path(os.environ.get("V9_TENTACLE_KEYS_FILE", str(ROOT / "state" / "tentacle_keys.json")))
MAP_ENV = "GBT_TENTACLE_KEYS"          # JSON: {"t001": "sk-...", "t002": "..."}
MODEL_MAP_ENV = "GBT_TENTACLE_MODELS"  # JSON: {"t001": "qwen2.5:3b-instruct"}
DEFAULT_KEY_ENV = ("OPENAI_API_KEY", "GBT_LLM_API_KEY")


def _fingerprint(key: str | None) -> str:
    if not key:
        return ""
    import hashlib
    return hashlib.sha256(("fleet-key:" + key).encode()).hexdigest()[:16]


def _load_map(env_name: str, path: Path | None = None) -> dict:
    """先读环境变量里的 JSON，再读落盘 JSON；都坏了就当空（不抛、不编）。"""
    raw = os.environ.get(env_name, "").strip()
    if not raw and path is not None and path.is_file():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
    if not raw:
        return {}
    try:
        got = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


def env_key_name(tid: str) -> str:
    return f"TENTACLE_{str(tid).upper()}_KEY"


def env_model_name(tid: str) -> str:
    return f"TENTACLE_{str(tid).upper()}_MODEL"


class TentacleKeyBook:
    """逐触手凭据册：一根触手一把钥匙（找得到就用，找不到就如实说没有）。"""

    def __init__(self, shared: str | None = None, base_url: str | None = None,
                 shared_names=DEFAULT_KEY_ENV, key_map: dict | None = None,
                 model_map: dict | None = None):
        self.shared_key = shared if shared is not None else self._from_env(shared_names)
        self.shared_source = "param" if shared else (self._src or "missing")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL",
                                                  "http://127.0.0.1:8317/v1")
        self.key_map = dict(key_map) if key_map is not None else _load_map(MAP_ENV, MAP_FILE)
        self.model_map = (dict(model_map) if model_map is not None
                          else _load_map(MODEL_MAP_ENV, None))

    @staticmethod
    def _from_env(names) -> str | None:
        for n in names:
            n = str(n).strip()
            if n and os.environ.get(n):
                TentacleKeyBook._src = n
                return os.environ[n]
        TentacleKeyBook._src = None
        return None

    _src = None

    def resolve(self, tid: str) -> dict:
        """这根触手的凭据：逐触手环境变量 → 映射表 → 统一密钥（回退，带标记）。"""
        tid = str(tid)
        own = os.environ.get(env_key_name(tid), "").strip()
        src = f"env:{env_key_name(tid)}"
        if not own:
            own = str(self.key_map.get(tid, "")).strip()
            src = f"map:{MAP_ENV}" if own else ""
        if own:
            return {"key": own, "source": src, "key_id": _fingerprint(own),
                    "own": True, "loaded": True}
        return {"key": self.shared_key, "source": f"shared-fallback:{self.shared_source}",
                "key_id": _fingerprint(self.shared_key), "own": False,
                "loaded": bool(self.shared_key)}

    def model_for(self, tid: str, default: str = "") -> dict:
        tid = str(tid)
        own = os.environ.get(env_model_name(tid), "").strip()
        src = f"env:{env_model_name(tid)}"
        if not own:
            own = str(self.model_map.get(tid, "")).strip()
            src = f"map:{MODEL_MAP_ENV}" if own else ""
        if own:
            return {"model": own, "source": src, "own": True}
        return {"model": default, "source": "fleet-default", "own": False}

    def report(self, tids) -> dict:
        rows = []
        for tid in list(tids):
            r = self.resolve(tid)
            rows.append({"tentacle": tid, "own_key": r["own"], "key_id": r["key_id"],
                         "source": r["source"]})
        own_n = sum(1 for r in rows if r["own_key"])
        ids = {r["key_id"] for r in rows if r["key_id"]}
        return {"tentacles": len(rows), "own_keys": own_n,
                "shared_fallback": len(rows) - own_n,
                "distinct_key_ids": len(ids),
                "every_tentacle_own_key": own_n == len(rows) and len(rows) > 0,
                "凭据映射文件": str(MAP_FILE),
                "支持的来源": ["环境变量 TENTACLE_Tnnn_KEY",
                                "环境变量 GBT_TENTACLE_KEYS(JSON)",
                                f"落盘 {MAP_FILE.name}"],
                "未接线": ["config.yaml client-keys（需要 yaml 依赖，未引入）"],
                "行": rows[:200]}


class TentacleKeyView:
    """一根触手看到的钥匙。接口与 UnifiedKey 对齐，Tentacle.client() 无需改动。"""

    def __init__(self, tid: str, book: TentacleKeyBook, *, mode: str = "auto"):
        self.id = tid
        self.book = book
        self.mode = str(mode or "auto")
        self.resolved = book.resolve(tid)
        self.own = bool(self.resolved["own"])
        self.source = self.resolved["source"]
        self.base_url = book.base_url
        # isolated：没有独立凭据就直接不可用（不静默借用别人的钥匙）
        self.loaded = bool(self.resolved["loaded"]) and (
            self.own if self.mode == "isolated" else True)

    @property
    def key_id(self) -> str:
        return self.resolved["key_id"] if self.loaded else ""

    def client_for(self, tentacle_id: str | None = None):
        if not self.loaded:
            raise RuntimeError(
                f"{self.id} 没有独立凭据，且 fleet.mode=isolated："
                f"请设环境变量 {env_key_name(self.id)}（本模块只回指纹、不回显原文）")
        from openai import OpenAI
        return OpenAI(api_key=self.resolved["key"], base_url=self.base_url)

    def same_as(self, other) -> bool:
        return bool(self.key_id) and self.key_id == getattr(other, "key_id", "")

    def as_row(self) -> dict:
        return {"tentacle": self.id, "own_key": self.own, "key_id": self.key_id,
                "source": self.source, "loaded": self.loaded, "mode": self.mode}


__all__ = ["TentacleKeyBook", "TentacleKeyView", "env_key_name", "env_model_name",
           "MAP_ENV", "MAP_FILE", "MODEL_MAP_ENV"]
