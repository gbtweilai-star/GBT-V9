# core/tentacle_fleet.py —— 触手编队：统一密钥 + 指挥闸门（≥100 根，全部听 V9 指挥）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人本轮指令的两条硬约束，这里逐条用代码锁死：
#   ① 统一密钥：编队里每根触手**与主脑同一把** LLM 密钥（只从环境变量/密钥服务读，
#      源码里零字面量；对外只暴露指纹 key_id，永不回显原文）。
#      诚实标注：同 key 会失去"按 key 隔离配额"的能力 —— 所以隔离改由
#      ② 承担，并保留 mode="isolated"（旧 TentacleKeyRing 逐触手发钥匙）按需切换。
#   ② 指挥闸门：触手不许自己发起 LLM 调用。每根触手每次驱动，都必须携带
#      **指挥官（GBT小土豆V9）签发的一次性工单**（HMAC 签名 + 限时 + 一次性）。
#      没有工单 → 直接拒绝；工单被篡改/过期/重放 → 直接拒绝；每次驱动落账可查。
from core.swallow import swallow as _swallow
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path

from senses.sqldialect import is_pg, txn

FLEET_DEFAULT_N = int(os.environ.get("GBT_FLEET_SIZE", "100"))
DEFAULT_RPM = int(os.environ.get("GBT_FLEET_RPM", "30"))
DEFAULT_MODEL = os.environ.get("GBT_FLEET_MODEL", os.environ.get("BRAIN_MODELS", "gpt-4o-mini").split(",")[0])
ORDER_TTL = int(os.environ.get("GBT_ORDER_TTL", "300"))
# 统一密钥的候选环境变量：第一个有值的生效（与主脑 core/brain.py 同一把）
KEY_ENV_NAMES = tuple(os.environ.get("GBT_KEY_ENV", "OPENAI_API_KEY,GBT_LLM_API_KEY").split(","))
COMMANDER_NAME = os.environ.get("GBT_COMMANDER_NAME", "GBT小土豆V9")

# 网关可用模型的**偏好序**：从便宜/快的通用对话模型开始挑（能返回 JSON 的）
MODEL_PREFERENCE = tuple(
    m.strip() for m in os.environ.get(
        "GBT_MODEL_PREFERENCE",
        "deepseek-v4-flash,deepseek-flash,glm-5.3-flash,gemini-3.5-flash,gpt-5.4-mini,"
        "claude-sonnet-5,deepseek-v4-pro,glm-5.3,gpt-5.4").split(",") if m.strip())
_MODEL_CACHE: dict = {"at": 0.0, "ids": [], "error": ""}
_MODEL_TTL = float(os.environ.get("GBT_MODEL_TTL", "600"))


def gateway_models(base_url: str, key: str | None, *, force: bool = False) -> dict:
    """取网关真实可用模型清单（GET /v1/models），带 TTL 缓存。

    真机踩过的坑：默认写死 gpt-4o-mini，而网关**不支持**该模型 →
    每次驱动都 400 model_not_supported，日志里就是一堆"驱动失败"。
    所以模型名必须**从网关本身**解析，而不是靠猜。
    """
    now = time.time()
    if not force and _MODEL_CACHE["ids"] and (now - _MODEL_CACHE["at"]) < _MODEL_TTL:
        return {"ok": True, "ids": list(_MODEL_CACHE["ids"]), "cached": True}
    if not key:
        return {"ok": False, "ids": [], "error": "无密钥：取不到模型清单"}
    try:
        from openai import OpenAI
        cli = OpenAI(api_key=key, base_url=base_url)
        got = cli.models.list()
        ids = sorted(str(x.id) for x in (got.data or []))
    except Exception as exc:                              # noqa: BLE001
        _MODEL_CACHE.update({"at": now, "ids": [], "error": type(exc).__name__})
        return {"ok": False, "ids": [], "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    _MODEL_CACHE.update({"at": now, "ids": ids, "error": ""})
    return {"ok": True, "ids": ids, "cached": False}


def pick_model(base_url: str, key: str | None, *, ids=None) -> dict:
    """按偏好序挑一个**网关确实支持**的模型；挑不到就如实返回失败（不硬编）。"""
    if ids is None:
        got = gateway_models(base_url, key)
        if not got["ok"]:
            return {"ok": False, "model": "", "reason": got["error"], "ids": []}
        ids = got["ids"]
    for want in MODEL_PREFERENCE:
        if want in ids:
            return {"ok": True, "model": want, "reason": "按偏好序命中", "ids": ids}
    if ids:
        return {"ok": True, "model": ids[0], "reason": "偏好序都没命中，取清单第一个",
                "ids": ids}
    return {"ok": False, "model": "", "reason": "网关返回空清单", "ids": []}


# ═══════════ 可用通道探测（免费通道兜底；真机根因修复的落点）═══════════
# 为什么需要"通道"这一层：真机实测主网关（OpenAI 兼容）**账户余额为 0**，
# 12 个候选模型全部 insufficient_balance → 驱动器永远失败。代码修不了账务，
# 但可以：① 把根因说清楚；② 自动找**本机免费通道**顶上（Ollama 是 OpenAI 兼容接口）。
# 写法纪律：本机端口用 socket.connect_ex 探活（字面量 host/port），
#          模型清单交给 OpenAI SDK（base_url 是字面量常量，不做动态拼 URL）。
LOCAL_CHANNELS = (
    ("ollama", "http://127.0.0.1:11434/v1", 11434, "本机 Ollama（OpenAI 兼容，免费本地推理）"),
    ("local-proxy", "http://127.0.0.1:8317/v1", 8317, "本机免费代理（FreeLLMAPI/agnes 一类）"),
)
# 本机通道的模型偏好：**非思考型 instruct 模型优先**（思考型在 OpenAI 兼容层常返空）
LOCAL_MODEL_PREF = tuple(
    m.strip() for m in os.environ.get(
        "GBT_LOCAL_MODEL_PREF",
        "qwen2.5:1.5b-instruct,qwen2.5:3b-instruct,llama3.2:3b-instruct,gemma2:2b,"
        "qwen2.5:7b-instruct").split(",") if m.strip())
_PROBE_TIMEOUT = float(os.environ.get("GBT_PROBE_TIMEOUT", "2.5"))
# 本机服务不校验凭据，这里只是 SDK 要求的占位串，**不是**任何真实密钥
_LOCAL_PLACEHOLDER = "local-no-credential"


def pick_local_model(models: list) -> dict:
    """从本机可用模型里挑一个：偏好序优先，且**优先非思考型**（名字里没 qwen3 的）。"""
    ids = [str(m) for m in (models or []) if m]
    if not ids:
        return {"ok": False, "model": "", "reason": "本机没有可用模型"}
    for want in LOCAL_MODEL_PREF:
        if want in ids:
            return {"ok": True, "model": want, "reason": "按本机偏好序命中（非思考型）"}
    non_think = [m for m in ids if "qwen3" not in m.lower()]
    if non_think:
        return {"ok": True, "model": non_think[0], "reason": "取一个非思考型模型"}
    return {"ok": True, "model": ids[0], "reason": "只有思考型模型（输出可能为空）"}


class _LocalOllamaClient:
    """本机 Ollama 的**原生 API** 适配器（形状与 OpenAI SDK 一致，编队代码无需改动）。

    为什么要它（真机根因）：走 OpenAI 兼容层时，AMD 核显上的 Ollama 会尝试 GPU 卸载导致
    退化解码 → 500「token repeat limit reached」；换成原生 API 并**强制纯 CPU**（num_gpu=0）
    后，1.4 秒就返回可解析 JSON。这是本机免费通道能不能当生产通道用的关键开关。
    """

    class _Msg:
        def __init__(self, content): self.content = content

    class _Choice:
        def __init__(self, content): self.message = _LocalOllamaClient._Msg(content)

    class _Usage:
        def __init__(self, n): self.total_tokens = int(n or 0)

    class _Resp:
        def __init__(self, content, tokens=0):
            self.choices = [_LocalOllamaClient._Choice(content)]
            self.usage = _LocalOllamaClient._Usage(tokens)

    class _Completions:
        def __init__(self, outer): self.outer = outer

        def create(self, *, model, messages, **kw):
            import ollama as _ol
            opts = {"num_gpu": int(os.environ.get("GBT_LOCAL_NUM_GPU", "0")),
                    "temperature": float(kw.get("temperature", 0.8)),
                    "top_p": float(os.environ.get("GBT_LOCAL_TOP_P", "0.9")),
                    "repeat_penalty": float(os.environ.get("GBT_LOCAL_REPEAT_PENALTY", "1.1")),
                    "num_predict": int(kw.get("max_tokens") or 256)}
            r = _ol.chat(model=model, messages=messages, options=opts)
            msg = (r.get("message") or {})
            return _LocalOllamaClient._Resp(msg.get("content") or "",
                                            r.get("eval_count") or 0)

    class _Chat:
        def __init__(self, outer): self.completions = _LocalOllamaClient._Completions(outer)

    def __init__(self):
        self.chat = _LocalOllamaClient._Chat(self)


def local_client():
    """本机通道用的客户端：能用 ollama 原生包就走原生（强制纯 CPU），否则退回不返回。"""
    try:
        import ollama as _ol                                   # noqa: F401
        return _LocalOllamaClient()
    except Exception:                                          # noqa: BLE001
        return None


def _port_up(port: int) -> bool:
    """本机端口是否在监听（固定回环地址，不做任何 URL 拼装）。"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(_PROBE_TIMEOUT)
            return s.connect_ex(("127.0.0.1", int(port))) == 0
    except OSError:
        return False


def probe_local_channels() -> list:
    """探测本机免费通道：端口在听 + 能列出模型，才算可用（不通就如实标原因）。"""
    out = []
    for name, url, port, note in LOCAL_CHANNELS:
        item = {"通道": name, "base_url": url, "说明": note, "up": False,
                "models": [], "reason": ""}
        if not _port_up(port):
            item["reason"] = "端口未监听"
            out.append(item)
            continue
        try:
            from openai import OpenAI
            cli = OpenAI(api_key=_LOCAL_PLACEHOLDER, base_url=url, timeout=_PROBE_TIMEOUT)
            ids = [str(x.id) for x in (cli.models.list().data or [])]
            item["models"] = [m for m in ids if m]
            item["up"] = bool(item["models"])
            if not item["up"]:
                item["reason"] = "接口通但没列出模型"
        except Exception as exc:                          # noqa: BLE001
            item["reason"] = f"{type(exc).__name__}"
        out.append(item)
    return out


def try_call(base_url: str, api_key: str, model: str, *, timeout: float = 30.0) -> dict:
    """真发一次最小请求，返回是否成功 + 网关原文的错误类型。

    通道判定必须以**真实调用**为准：真机实测过"能列出 47 个模型、但一调用就
    insufficient_balance"的情况 —— 只看清单会把不可用通道判成可用。
    """
    try:
        from openai import OpenAI
        cli = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        r = cli.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=4)
        return {"ok": True, "error_type": "", "message":
                (r.choices[0].message.content or "")[:40], "model": model}
    except Exception as exc:                              # noqa: BLE001
        resp = getattr(exc, "response", None)
        body = getattr(resp, "text", "") or str(exc)
        etype = ""
        try:
            etype = json.loads(body).get("error", {}).get("type", "")
        except (ValueError, AttributeError):
            etype = ""
        return {"ok": False, "error_type": etype or type(exc).__name__,
                "message": str(body)[:200], "model": model}


def resolve_channel(fleet=None) -> dict:
    """选一条**真能用**的通道：主通道优先；主通道真实调用不通就用本机免费通道。

    返回 {mode, base_url, model, 说明, 主通道原因, 免费通道, 网关模型数}
    """
    key = (fleet.key if fleet is not None else UnifiedKey())
    local = probe_local_channels()
    picked = pick_model(key.base_url, getattr(key, "_key", None))
    report = {"mode": "blocked", "base_url": key.base_url, "model": "",
              "说明": "", "主通道原因": "", "免费通道": local,
              "网关模型数": len(picked.get("ids") or []), "主通道模型": picked.get("model", ""),
              "主通道校验": {}}
    if picked.get("ok") and picked.get("model"):
        check = try_call(key.base_url, getattr(key, "_key", "") or "", picked["model"])
        report["主通道校验"] = check
        if check["ok"]:
            report.update({"mode": "primary", "model": picked["model"],
                           "说明": f"主通道可用（{picked.get('reason', '')}）"})
            return report
        report["主通道原因"] = check["error_type"]
    else:
        report["主通道原因"] = picked.get("reason", "解析失败")
    free = next((c for c in local if c["up"] and c["models"]), None)
    if free:
        # 以**真实调用**逐个试模型：优先非思考型（qwen2.5 系），思考型常返空内容。
        ordered = []
        pick = pick_local_model(free["models"])
        if pick.get("ok") and pick["model"]:
            ordered.append(pick["model"])
        ordered += [m for m in free["models"] if m not in ordered][:2]
        tried = []
        for cand in ordered[:3]:
            fc = try_call(free["base_url"], _LOCAL_PLACEHOLDER, cand)
            tried.append({"model": cand, "ok": fc["ok"], "error_type": fc.get("error_type", "")})
            if fc["ok"]:
                report["免费通道校验"] = fc
                report["免费通道试过"] = tried
                report.update({"mode": "free-local", "base_url": free["base_url"],
                               "model": cand,
                               "说明": f"主通道真实调用不通（{report['主通道原因']}）→ "
                                       f"改用本机免费通道 {free['通道']}:{cand}"
                                       f"（原生 API + 纯 CPU，已真实调用验证）"})
                return report
        report["免费通道试过"] = tried
        report["免费通道原因"] = "; ".join(
            f"{t['model']}={t['error_type']}" for t in tried) or "无可用模型"
    report["说明"] = (f"主通道不通（{report['主通道原因']}），本机免费通道也不可用"
                    f"（{report.get('免费通道原因', '无可用通道')}）")
    return report


def _strip_fence(text: str) -> str:
    """剥掉 ```json ... ``` 代码围栏（本机小模型爱套围栏，不剥就永远解析不了 JSON）。"""
    t = str(text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


# ═══════════ ① 统一密钥（与主脑同步，只暴露指纹）═══════════
def key_fingerprint(key: str | None) -> str:
    """密钥指纹：只用来核对"是不是同一把"，不可逆推原文。"""
    if not key:
        return ""
    return hashlib.sha256(("fleet-key:" + key).encode()).hexdigest()[:16]


class UnifiedKey:
    """编队统一密钥：与主脑同一把。缺失即明确不可用，绝不伪造占位 key 充数。"""

    def __init__(self, key: str | None = None, base_url: str | None = None,
                 env_names=KEY_ENV_NAMES):
        self._key = key or self._from_env(env_names)
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL",
                                                   "http://127.0.0.1:8317/v1")
        self.source = "param" if key else (self._src or "missing")

    @staticmethod
    def _from_env(names) -> str | None:
        for n in names:
            n = n.strip()
            if n and os.environ.get(n):
                UnifiedKey._src = n
                return os.environ[n]
        UnifiedKey._src = None
        return None

    _src = None

    @property
    def loaded(self) -> bool:
        return bool(self._key)

    @property
    def key_id(self) -> str:
        return key_fingerprint(self._key)

    def pick_model(self) -> dict:
        """挑一个网关支持的模型（显式环境变量优先，其次问网关）。"""
        explicit = (os.environ.get("GBT_FLEET_MODEL")
                    or os.environ.get("BRAIN_MODELS", "").split(",")[0].strip())
        if explicit:
            return {"ok": True, "model": explicit, "reason": "环境变量显式指定", "ids": []}
        return pick_model(self.base_url, self._key)

    def client_for(self, tentacle_id: str):
        """给某根触手一个客户端 —— 用的是**同一把**统一密钥（不是每触手一把）。"""
        if not self.loaded:
            raise RuntimeError("统一密钥缺失：请设 " + "/".join(KEY_ENV_NAMES)
                               + "（只从环境变量读，源码零凭据）")
        from openai import OpenAI
        return OpenAI(api_key=self._key, base_url=self.base_url)

    def same_as(self, other: "UnifiedKey") -> bool:
        return bool(self.key_id) and self.key_id == other.key_id


# ═══════════ ② 指挥闸门：一次性工单 ═══════════
class OrderError(RuntimeError):
    pass


class _ModelUnreliable(RuntimeError):
    """模型本身不可靠（例如本机 0.6b 陷重复循环被中止）——重试无意义，直接如实上报。"""


class OrderGate:
    """工单签发/校验。签名密钥：显式 secret → env → **落盘持久**（跨进程共用一把）。"""

    def __init__(self, secret: str | None = None):
        # ★2026-10-08 修「假未授权」：原先没 env 就进程内临时生成 ⇒ 跨进程工单必验失败。
        #   现在落到 state/order.secret（env GBT_ORDER_SECRET 仍优先），面板与触手共用一把。
        if secret:
            self._secret = str(secret).encode()
            self._ephemeral = False
        else:
            from core.local_secret import local_secret
            got = local_secret("order", "GBT_ORDER_SECRET")
            self._secret = got["key"]
            self._ephemeral = got["source"] == "memory"
        self._used: set = set()
        self._lock = threading.Lock()

    def issue(self, tentacle_id: str, task: str, *, ttl: int = ORDER_TTL,
              trace_id: str | None = None, issued_by: str = COMMANDER_NAME) -> dict:
        order = {"order_id": uuid.uuid4().hex[:16], "tentacle_id": tentacle_id,
                 "task": (task or "")[:2000], "issued_by": issued_by,
                 "issued_at": int(time.time()), "expires_at": int(time.time()) + int(ttl),
                 "trace_id": trace_id or uuid.uuid4().hex[:12]}
        order["sig"] = self._sign(order)
        return order

    def _sign(self, order: dict) -> str:
        body = json.dumps({k: v for k, v in order.items() if k != "sig"},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()

    def verify(self, order: dict | None, *, tentacle_id: str | None = None) -> tuple[bool, str]:
        """返回 (是否放行, 原因)。任一硬条件不过 → 不放行（fail closed）。"""
        if not isinstance(order, dict) or not order.get("sig"):
            return False, "missing_order（触手必须由指挥官签发工单才能驱动）"
        if order.get("sig") != self._sign(order):
            return False, "bad_signature（工单签名不符：伪造或篡改）"
        if order.get("issued_by") != COMMANDER_NAME:
            return False, f"not_from_commander（签发者 {order.get('issued_by')!r} 不是 {COMMANDER_NAME}）"
        if tentacle_id and order.get("tentacle_id") != tentacle_id:
            return False, "order_for_other_tentacle"
        if int(order.get("expires_at", 0)) < int(time.time()):
            return False, "order_expired"
        with self._lock:
            if order.get("order_id") in self._used:
                return False, "order_replayed（工单一次性，用过即废）"
        return True, "ok"

    def consume(self, order: dict) -> None:
        with self._lock:
            self._used.add(order.get("order_id"))
            if len(self._used) > 10000:                  # 防无界增长
                self._used = set(list(self._used)[-5000:])


# ═══════════ 触手 ═══════════
class Tentacle:
    """一根触手：统一密钥 + 自己的 rpm 桶；每次 LLM 调用必须带合法工单。"""

    def __init__(self, tid: str, *, key: UnifiedKey, model: str, rpm: int,
                 role: str = "worker", client_factory=None, profession: str = "",
                 model_source: str = ""):
        self.id, self.key, self.model, self.rpm = tid, key, model, int(rpm)
        self.role = role
        # ── 「完整 LLM」的三件自己的东西（2026-10-08 补）──
        # 会话：一根触手一条连续会话（不是每次裸拼 messages 的无状态调用）
        self.session_id = f"sess-{tid}"
        self.turns = 0
        self.profession = profession        # 职业（见 core/tentacle_profession.py）
        self.model_source = model_source    # 这个模型是哪来的（env/map/fleet-default）
        self.memory_file = None             # 懒建：state/tentacle_memory/<tid>.jsonl
        self.drives = 0
        self.tokens = 0
        self.failures = 0
        self.retries = 0
        self.json_downgraded = False
        self._window: list = []
        self._client = None
        self._client_factory = client_factory      # 测试注入假客户端
        self._lock = threading.Lock()

    # ── 配额闸门（同 key 下唯一能做的自律：本地 rpm 桶）──
    def allow(self) -> bool:
        with self._lock:
            now = time.time()
            self._window = [t for t in self._window if now - t < 60]
            if len(self._window) >= self.rpm:
                return False
            self._window.append(now)
            return True

    # ── 自己的记忆（逐触手一个文件，互不可见；主脑有全量视图）──
    def _memory_path(self):
        if self.memory_file is None:
            root = Path(__file__).resolve().parent.parent / "state" / "tentacle_memory"
            root.mkdir(parents=True, exist_ok=True)
            self.memory_file = root / f"{self.id}.jsonl"
        return self.memory_file

    def remember(self, text: str, *, kind: str = "note") -> bool:
        """记一笔到**自己**的记忆文件。写不进返回 False（不假装记住）。"""
        try:
            with self._memory_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.time(), "kind": kind,
                                    "text": str(text)[:400]}, ensure_ascii=False) + "\n")
            return True
        except OSError:
            return False

    def recall(self, *, limit: int = 3) -> list:
        """读回**自己**最近的几笔记忆（读不到就给空表，不编）。"""
        try:
            lines = self._memory_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines[-max(1, int(limit)):]:
            try:
                out.append(str(json.loads(ln).get("text", "")))
            except json.JSONDecodeError:
                continue
        return out

    def client(self):
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory(self)
            elif str(getattr(self.key, "base_url", "")).startswith("http://127.0.0.1"):
                # 本机通道：走原生 API（强制纯 CPU）—— 真机根因：OpenAI 兼容层 + AMD 核显
                # 卸载会退化解码（token repeat limit），原生 API 才稳
                self._client = local_client() or self.key.client_for(self.id)
            else:
                self._client = self.key.client_for(self.id)
        return self._client

    def chat(self, order: dict, messages: list, *, gate: "OrderGate",
             json_mode: bool = True, model: str | None = None) -> dict:
        """带工单的 LLM 调用。无工单 → OrderError（触手绝不自作主张）。"""
        ok, why = gate.verify(order, tentacle_id=self.id)
        if not ok:
            self.failures += 1
            raise OrderError(why)
        if not self.allow():
            self.failures += 1
            raise OrderError(f"rpm_exceeded（{self.id} 已用满 {self.rpm}/分钟）")
        kw = {"model": model or self.model, "messages": messages}
        if json_mode:
            kw["response_format"] = {"type": "json_object"}
        # 输出上限：防小模型绕圈把补全拖长（本机 0.6b 会撞上 Ollama 的重复中止）
        kw["max_tokens"] = int(os.environ.get("GBT_DRIVE_MAX_TOKENS", "384"))
        # 本机回环通道（Ollama 一类）：给一点温度，显著降低重复循环概率
        if str(getattr(self.key, "base_url", "")).startswith("http://127.0.0.1"):
            kw["temperature"] = float(os.environ.get("GBT_LOCAL_TEMPERATURE", "0.3"))
            # 关掉思考：思考型模型的思考会吃光预算 → 返回空内容（真机实测：不开思考
            # qwen3 系要么空要么 55s；开了思考 0.6b 2.9s 就有内容）。Ollama 认这个参数。
            if str(os.environ.get("GBT_LOCAL_THINK", "0")).lower() in ("0", "false", "no"):
                kw["extra_body"] = {"think": False}
        attempts = 0
        while True:
            try:
                r = self.client().chat.completions.create(**kw)
                break
            except Exception as exc:                      # noqa: BLE001
                attempts += 1
                status = getattr(getattr(exc, "response", None), "status_code", None)
                # ① 通道（如本机 Ollama）不吃 response_format → 摘掉参数重试一次，
                #    并把"降级过"如实带回（不假装一直是严格 JSON 模式）。
                if json_mode and "response_format" in kw and status in (400, 404, 415, 422):
                    kw.pop("response_format", None)
                    self.json_downgraded = True
                    continue
                # ② 5xx/网关抖动（本机 CPU 通道并发时偶发 500）→ 退避重试，最多两次。
                #    但"小模型陷重复循环被中止"这种**不是抖动**，重试无意义 → 快速失败。
                body = str(getattr(getattr(exc, "response", None), "text", "") or exc)
                if "repeat" in body.lower() or "repeat limit" in body.lower():
                    raise _ModelUnreliable(
                        f"local_model_unreliable（本机模型陷重复循环被中止：{body[:120]}）")
                if status in (500, 502, 503, 504) and attempts <= 2:
                    self.retries += 1
                    time.sleep(min(1.5 * attempts, 3.0))
                    continue
                raise
        gate.consume(order)                              # 一次性：调用成功即作废
        self.drives += 1
        try:
            usage = getattr(r, "usage", None)
            self.tokens += int(getattr(usage, "total_tokens", 0) or 0)
        except Exception as e:
            _swallow(__file__, e)
        text = r.choices[0].message.content or ""
        text = _strip_fence(text)
        if not str(text).strip():
            # ★不许把空输出当成功：本机思考型小模型（qwen3 系）经 OpenAI 兼容层
            # 常见"200 + 空内容"，如果算成功，台账就会虚报成功数（真机上踩过）。
            self.failures += 1
            raise _ModelUnreliable(
                "empty_output（模型返回空内容 —— 本机思考型小模型常见；不作为成功）")
        if not json_mode:
            return {"text": text}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text, "parse_error": "model_returned_non_json"}

    def status(self) -> dict:
        return {"id": self.id, "role": self.role, "model": self.model, "rpm": self.rpm,
                "drives": self.drives, "tokens": self.tokens, "failures": self.failures,
                "key_id": self.key.key_id}


# ═══════════ 编队 ═══════════
ROLE_CYCLE = ("scan", "devour", "voice", "scan", "guard", "code", "memory", "mesh")


class TentacleFleet:
    """≥100 根触手，统一密钥，全部经指挥官工单驱动。"""

    def __init__(self, ledger=None, brain=None, commander=None, *, n=None,
                 model=None, rpm=None, mode="unified", key=None, gate=None,
                 client_factory=None):
        self.led, self.brain, self.commander = ledger, brain, commander
        self.mode = mode
        self.gate = gate or OrderGate()
        self.key = key or UnifiedKey()
        self.client_factory = client_factory
        self.model, self.rpm = model or DEFAULT_MODEL, int(rpm or DEFAULT_RPM)
        self.channel_report: dict = {"mode": "given", "说明": "调用方显式指定或注入假客户端"}
        self.model_resolution = {"ok": True, "model": self.model,
                                 "reason": "调用方显式指定" if model else "默认值"}
        if model is None and client_factory is None:
            # 没显式给模型、也没注入假客户端 → 解析**真能用**的通道与模型
            # （真机根因链：写死模型不被网关支持 → 换模型又撞上账户余额为 0 → 用本机免费通道顶上）
            try:
                rep = resolve_channel(self)
                self.channel_report = rep
                if rep.get("mode") == "free-local":
                    self.key = UnifiedKey(key=_LOCAL_PLACEHOLDER, base_url=rep["base_url"])
                    self.model = rep["model"]
                    self.model_resolution = {"ok": True, "model": rep["model"],
                                             "reason": rep.get("说明", ""), "ids": []}
                elif rep.get("mode") == "primary" and rep.get("model"):
                    self.model = rep["model"]
                    self.model_resolution = {"ok": True, "model": rep["model"],
                                             "reason": rep.get("说明", ""),
                                             "ids": []}
                else:
                    self.model_resolution = {"ok": False, "model": self.model,
                                             "reason": rep.get("说明", "无可用通道")}
            except Exception as exc:                      # noqa: BLE001
                self.model_resolution = {"ok": False, "model": self.model,
                                         "reason": f"{type(exc).__name__}"}
        self.tentacles: dict[str, Tentacle] = {}
        self._init_table()
        self.build(n if n is not None else FLEET_DEFAULT_N)

    def deep_probe(self) -> dict:
        """深探：主通道与选中通道各发一次最小请求，取回**网关原文的错误类型**。

        这是"追根因"用的：例如主通道返回 insufficient_balance（账户余额不足），
        那就是账务级阻塞，不是代码问题 —— 面板要如实这么写。
        """
        rep = self.channel_report or {}
        out = {"主通道": {"base_url": rep.get("base_url", getattr(self.key, "base_url", "")),
                          "model": rep.get("主通道模型", ""), "ok": False,
                          "error_type": "", "message": ""},
               "选中通道": {"base_url": getattr(self.key, "base_url", ""),
                            "model": self.model, "ok": False,
                            "error_type": "", "message": ""}}
        try:
            out["主通道"].update(try_call(getattr(self.key, "base_url", ""),
                                          getattr(self.key, "_key", "") or "",
                                          rep.get("主通道模型") or self.model))
        except Exception as exc:                          # noqa: BLE001
            out["主通道"].update({"ok": False, "error_type": type(exc).__name__,
                                  "message": str(exc)[:160]})
        try:
            out["选中通道"].update(try_call(getattr(self.key, "base_url", ""),
                                            getattr(self.key, "_key", "") or "", self.model))
        except Exception as exc:                          # noqa: BLE001
            out["选中通道"].update({"ok": False, "error_type": type(exc).__name__,
                                    "message": str(exc)[:160]})
        out["根因"] = (rep.get("主通道原因") or out["主通道"].get("error_type") or "")
        return out

    def preflight(self) -> dict:
        """生产预检：密钥 + 通道 + 模型是否可用。不可用就把**真实原因**说清楚。"""
        key_ok = bool(getattr(self.key, "loaded", False))
        picked = self.model_resolution or {}
        mode = (self.channel_report or {}).get("mode", "")
        return {"密钥已配置": key_ok, "密钥来源": getattr(self.key, "source", ""),
                "密钥指纹": getattr(self.key, "key_id", ""),
                "通道": mode, "base_url": getattr(self.key, "base_url", ""),
                "模型": self.model, "模型解析": picked.get("reason", ""),
                "主通道模型": (self.channel_report or {}).get("主通道模型", ""),
                "主通道原因": (self.channel_report or {}).get("主通道原因", ""),
                "网关模型数": (self.channel_report or {}).get("网关模型数", 0),
                "模型已确认可用": bool(picked.get("ok")),
                "通道说明": (self.channel_report or {}).get("说明", ""),
                "可驱动": bool(key_ok and picked.get("ok", True))}

    # ── 编队装配：每根一根自己的钥匙（找不到独立凭据就如实回退并标出，不假装）──
    def build(self, n: int) -> dict:
        n = max(1, int(n))
        self.tentacles.clear()
        from core.tentacle_keys import TentacleKeyBook, TentacleKeyView
        self.key_book = TentacleKeyBook(shared=getattr(self.key, "_key", None),
                                        base_url=getattr(self.key, "base_url", None))
        profs: dict = {}
        try:                                    # 职业名册（未立专业的触手如实为空）
            from core import tentacle_profession as _TP
            profs = {r["tentacle"]: r for r in (_TP.roster(n=n)["行"] or [])}
        except Exception:                       # noqa: BLE001
            profs = {}
        for i in range(1, n + 1):
            tid = f"t{i:03d}"
            view = TentacleKeyView(tid, self.key_book, mode=self.mode)
            m = self.key_book.model_for(tid, self.model)
            self.tentacles[tid] = Tentacle(
                tid, key=view, model=m["model"], rpm=self.rpm,
                role=ROLE_CYCLE[(i - 1) % len(ROLE_CYCLE)],
                client_factory=self.client_factory,
                profession=str((profs.get(tid) or {}).get("专业") or ""),
                model_source=m["source"])
        return self.status()

    def _init_table(self):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS fleet_drive ("
                            "order_id TEXT PRIMARY KEY, trace_id TEXT, tentacle_id TEXT,"
                            " task TEXT, ok INTEGER, reason TEXT, tokens INTEGER,"
                            " ms INTEGER, key_id TEXT, issued_by TEXT, at TEXT)")
        except Exception as e:
            _swallow(__file__, e)

    def _key_id_of(self, tid) -> str:
        """这条驱动到底用的哪把钥匙 —— 统一密钥时代所有行都是同一指纹，分辨不出来。"""
        t = self.tentacles.get(str(tid or ""))
        return getattr(getattr(t, "key", None), "key_id", "") or self.key.key_id

    def _log(self, order, *, ok, reason, tokens=0, ms=0) -> bool:
        """落审计行。返回是否真写进去 —— 写不进去必须能被调用方看见，不许静默。"""
        if self.led is None:
            return False
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO fleet_drive (order_id, trace_id, tentacle_id,"
                            " task, ok, reason, tokens, ms, key_id, issued_by, at)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            ((order or {}).get("order_id"), (order or {}).get("trace_id"),
                             (order or {}).get("tentacle_id"),
                             ((order or {}).get("task") or "")[:400],
                             1 if ok else 0, (reason or "")[:200], int(tokens), int(ms),
                             self._key_id_of((order or {}).get("tentacle_id")),
                             (order or {}).get("issued_by") or "",
                             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            return True
        except Exception:
            return False

    # ── 指挥官签单（"每次都要 V9 指挥执行"的落点）──
    def order(self, tentacle_id: str, task: str, **kw) -> dict:
        if tentacle_id not in self.tentacles:
            raise OrderError(f"unknown_tentacle:{tentacle_id}")
        return self.gate.issue(tentacle_id, task, **kw)

    # ── 驱动：无工单则当场由指挥官签发（而不是让触手自己发起）──
    def drive(self, tentacle_id: str, task: str, *, messages=None, order=None,
              auto_order=True, json_mode=True) -> dict:
        t0 = time.time()
        if tentacle_id not in self.tentacles:
            return {"ok": False, "reason": "unknown_tentacle", "tentacle": tentacle_id}
        # 预检拦截：真的一条可用通道都没有时，**不再空转**（否则账本里只会堆满
        # 一模一样的失败样本，看着像"处处报错"，其实根因只有一个）。
        # 注：注入了假客户端的测试路径不受影响（client_factory 存在就跳过预检）。
        # ★2026-10-08 修「假卡点永久化」（主人：「停止不动说遇到卡点了」）：
        #   原先只在 `_preflight_blocked is None` 时探一次，写进去以后**再不复检** ——
        #   通道后来恢复了，这里照样一路 `preflight_blocked` 停着不动。
        #   现在给失败结论一个 TTL（默认 60s），过期就重探；**真不可用照样如实拦**。
        pf_ttl = float(os.environ.get("GBT_PREFLIGHT_TTL", "60"))
        _now = time.time()
        _fresh = (_now - float(getattr(self, "_pf_at", 0.0))) < pf_ttl
        if self.client_factory is None and not _fresh:
            pf = self.preflight()
            self._preflight_blocked = "" if pf.get("可驱动") else str(
                pf.get("主通道原因") or pf.get("通道说明") or "no_usable_channel")
            self._pf_at = _now
        if self.client_factory is None and self._preflight_blocked:
            return {"ok": False, "reason": f"preflight_blocked:{self._preflight_blocked}",
                    "tentacle": tentacle_id, "重探秒数": pf_ttl,
                    "hint": "主通道与免费通道都不可用：先解决凭据/余额，或启动本机免费通道"}
        if order is None:
            if not auto_order:
                self._log(None, ok=False, reason="no_order")
                return {"ok": False, "reason": "no_order",
                        "hint": "触手必须由指挥官签发工单（OrderGate.issue / fleet.order）"}
            order = self.order(tentacle_id, task)        # ← V9 现场指挥
        ok, why = self.gate.verify(order, tentacle_id=tentacle_id)
        if not ok:
            self._log(order, ok=False, reason=why, ms=int((time.time() - t0) * 1000))
            return {"ok": False, "reason": why, "order": order.get("order_id")}
        tent = self.tentacles[tentacle_id]
        prof = f"你的职业：{tent.profession}；" if tent.profession else ""
        msgs = messages or [
            {"role": "system", "content": f"你是 GBT小土豆V9 的触手 {tentacle_id}，"
                                          f"{prof}只执行指挥官下达的这一步任务，严格返回 JSON。"},
            {"role": "user", "content": task}]
        # 唤回**这根触手自己的**记忆（不是别人的；只有主脑看得到全部）
        mem = tent.recall(limit=3)
        if mem:
            msgs = msgs[:1] + [{"role": "system",
                                "content": f"你（{tentacle_id}）的近期记忆："
                                           + " | ".join(mem)}] + msgs[1:]
        try:
            out = tent.chat(order, msgs, gate=self.gate,
                            json_mode=json_mode)
        except OrderError as exc:
            self._log(order, ok=False, reason=str(exc), ms=int((time.time() - t0) * 1000))
            return {"ok": False, "reason": str(exc), "order": order["order_id"]}
        except _ModelUnreliable as exc:
            # 模型本身靠不住（小模型绕圈）：把**原文原因**入账，别糊成 llm_error
            self._log(order, ok=False, reason=str(exc), ms=int((time.time() - t0) * 1000))
            return {"ok": False, "reason": str(exc), "order": order["order_id"],
                    "tentacle": tentacle_id, "channel": self.key.base_url,
                    "hint": "本机免费通道的小模型不适合结构化任务：换更大的本地模型，"
                            "或给主通道充值/换有余额的 key（那是唯一稳的生产通道）"}
        except Exception as exc:                          # noqa: BLE001
            self._log(order, ok=False, reason=f"llm_error:{type(exc).__name__}",
                      ms=int((time.time() - t0) * 1000))
            return {"ok": False, "reason": f"llm_error:{type(exc).__name__}",
                    "order": order["order_id"]}
        ms = int((time.time() - t0) * 1000)
        logged = self._log(order, ok=True, reason="ok",
                           tokens=self.tentacles[tentacle_id].tokens, ms=ms)
        # 会话 + 记忆：一根触手一条自己的会话（不是无状态裸调用）
        tent.turns += 1
        remembered = tent.remember(f"任务:{str(task)[:100]} → {str(out)[:180]}", kind="turn")
        session_logged = self._session_turn(tent, order, task, out)
        return {"ok": True, "tentacle": tentacle_id, "order": order["order_id"],
                "trace_id": order["trace_id"], "ms": ms, "logged": logged,
                "session_id": tent.session_id, "turn": tent.turns,
                "remembered": remembered, "session_logged": session_logged,
                "key_id": getattr(tent.key, "key_id", ""),
                "model": tent.model, "profession": tent.profession,
                "output": out}

    def _session_turn(self, tent, order, task, out) -> bool:
        """把这一回合写进触手自己的会话表（写不进不吞，返回 False，由调用方看见）。"""
        if self.led is None:
            return False
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS tentacle_session ("
                            "session_id TEXT, tentacle_id TEXT, turn INTEGER, ts REAL,"
                            " task TEXT, reply TEXT, model TEXT, key_id TEXT,"
                            "profession TEXT, trace_id TEXT)")
                cur.execute("INSERT INTO tentacle_session VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (tent.session_id, tent.id, int(tent.turns), time.time(),
                             str(task)[:400],
                             json.dumps(out, ensure_ascii=False, default=str)[:1500],
                             tent.model, getattr(tent.key, "key_id", ""),
                             tent.profession, (order or {}).get("trace_id") or ""))
            return True
        except Exception:                                     # noqa: BLE001
            return False

    def drive_many(self, task_of, *, limit=None) -> dict:
        """批量驱动（每根仍然逐单过闸门）。task_of(tid) → 该触手这一步的具体任务。"""
        tids = sorted(self.tentacles)[: int(limit or len(self.tentacles))]
        out = []
        for tid in tids:
            out.append(self.drive(tid, task_of(tid)))
        ok_n = sum(1 for r in out if r.get("ok"))
        return {"driven": len(out), "ok": ok_n, "failed": len(out) - ok_n, "results": out}

    def status(self) -> dict:
        key_ids = {t.key.key_id for t in self.tentacles.values() if t.key.key_id}
        own = [t for t in self.tentacles.values() if getattr(t.key, "own", False)]
        models = {t.model for t in self.tentacles.values()}
        sessions = {t.session_id for t in self.tentacles.values()}
        return {"n": len(self.tentacles), "mode": self.mode, "model": self.model,
                "rpm": self.rpm, "key_id": self.key.key_id,
                "key_source": self.key.source, "key_loaded": self.key.loaded,
                "same_key": len(key_ids) == 1,          # 统一密钥时：指纹只有一个
                # ── 「每根触手都是完整 LLM」三件套的真读数（2026-10-08 补）──
                "own_key_tentacles": len(own),          # 有独立凭据的触手数
                "distinct_key_ids": len(key_ids),        # 实际用到的不同钥匙数
                "distinct_models": len(models),          # 实际用到的不同模型数
                "distinct_sessions": len(sessions),      # 每根一条会话
                "完整三件": {
                    "凭据": "独立" if own and len(own) == len(self.tentacles) else
                            (f"部分独立({len(own)}/{len(self.tentacles)})" if own else "共享回退"),
                    "模型": "独立" if len(models) > 1 else "编队统一",
                    "会话": "每根一个 session_id（sess-<tid>）",
                    "记忆": "state/tentacle_memory/<tid>.jsonl（逐触手隔离）"},
                "commander": COMMANDER_NAME,
                "order_secret_ephemeral": self.gate._ephemeral,
                "drives": sum(t.drives for t in self.tentacles.values()),
                "tokens": sum(t.tokens for t in self.tentacles.values()),
                "failures": sum(t.failures for t in self.tentacles.values()),
                "ledger": self.led is not None}

    def table(self, limit: int = 20) -> list[dict]:
        if self.led is None:
            return []
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT tentacle_id, task, ok, reason, tokens, ms,"
                            " key_id, issued_by, trace_id, at"
                            " FROM fleet_drive ORDER BY at DESC LIMIT ?", (int(limit),))
                cols = [d[0] for d in (cur.description or [])]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            return []


__all__ = ["UnifiedKey", "OrderGate", "OrderError", "Tentacle", "TentacleFleet",
           "key_fingerprint", "KEY_ENV_NAMES", "COMMANDER_NAME", "FLEET_DEFAULT_N"]
