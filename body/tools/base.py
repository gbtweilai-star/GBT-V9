# body/tools/base.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 只读; 参数只做绑定, 绝不拼 SQL/表名/路径; 采样缺失 = unknown, 不是 0;
#       数字来自 body_read_snapshots, 工具不重算; 每次调用必须落审计
from __future__ import annotations
import hashlib, hmac, json, os, time, uuid
from dataclasses import dataclass, asdict

DOMAINS = ("witness", "devour", "scan", "queue")
DEFAULT_TTL_MULT = 3                                   # ttl = sample_period × 乘数
STALE_PREFIX = "当前状态暂时无法确认"


def unknown_sentence(reason: str, age_text: str = "") -> str:
    """统一措辞：所有域共用，不许各域自编。"""
    return (f"{STALE_PREFIX}；最近一次核验：{age_text or '较早前'}。"
            f"原因：{reason}。我只能转述上次读数，不作为当前状态。")


def _iso(ts): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
def _parse(value) -> float:
    """快照时间戳 → epoch。时间口径统一走 common.timeutil（naive 按 UTC）。"""
    try:
        from common.timeutil import to_epoch
        return float(to_epoch(value))
    except Exception:
        return 0.0
def _age_text(sec: int) -> str:
    if sec < 90: return f"{int(sec)} 秒前"
    if sec < 5400: return f"{int(sec // 60)} 分钟前"
    return f"{int(sec // 3600)} 小时前"
def digest(obj) -> str:
    key = os.environ.get("BODY_WITNESS_FP_KEY", "tool-audit").encode()
    return hmac.new(key, json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode(), hashlib.sha256).hexdigest()[:16]


@dataclass
class ToolResult:
    tool: str
    domain: str
    revision: int | None = None
    observed_at: str | None = None
    stale: bool = False
    facts: dict = None
    evidence_ref: list = None
    unknown_reason: str | None = None
    safe_sentence: str = ""

    def as_dict(self):
        d = asdict(self)
        d["facts"] = d["facts"] or {}
        d["evidence_ref"] = d["evidence_ref"] or []
        return d


class ReadTool:
    """只读工具基类。子类只实现 spec / sentence；取数与判定走基类。"""
    name = ""
    domain = ""
    description = ""
    params: dict = {}                    # JSON-Schema；额外键一律拒绝
    intent_hint = ""                     # 给 LLM 的意图路由提示

    def spec(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description":
                             f"{self.description}（{self.intent_hint}）",
                             "parameters": {"type": "object", "additionalProperties": False,
                                            "properties": self.params}}}

    def sentence(self, facts: dict) -> str:      # 子类实现
        raise NotImplementedError

    # ── 统一取数：读快照 + 判新鲜度 ──
    async def run(self, ledger, *, params=None, now_fn=time.time,
                  ttl_mult=DEFAULT_TTL_MULT) -> ToolResult:
        snap = await ledger.fetch_one(
            "SELECT * FROM body_read_snapshots WHERE domain=?", (self.domain,))
        if snap is None:                                     # ★采样缺失 ≠ 无异常
            return ToolResult(tool=self.name, domain=self.domain,
                              unknown_reason="no_snapshot",
                              safe_sentence=unknown_sentence("该域还没有任何快照"))
        age = max(0.0, now_fn() - _parse(snap["observed_at"]))
        ttl = snap["sample_period_s"] * ttl_mult
        stale = age > ttl
        facts = json.loads(snap["payload_json"])
        sentence = (unknown_sentence("读数已超出有效期", _age_text(int(age)))
                    if stale else self.sentence(facts))
        return ToolResult(tool=self.name, domain=self.domain, revision=snap["revision"],
                          observed_at=snap["observed_at"], stale=stale, facts=facts,
                          evidence_ref=json.loads(snap["evidence_json"]),
                          unknown_reason=("stale" if stale else None),
                          safe_sentence=sentence)


TOOLS: dict[str, ReadTool] = {}
_registered = False


def ensure_registered() -> None:
    """惰性注册四个域工具（避免 base↔domains 循环导入，同时保证 call_tool 一定有工具）。"""
    global _registered
    if _registered:
        return
    _registered = True
    from body.tools import domains     # noqa: F401  ← 导入即 @register

def register(tool):
    """注册工具。支持装饰类（自动实例化）—— TOOLS 里永远是实例，调用方不必 new。"""
    if isinstance(tool, type):
        tool = tool()
    assert tool.name and tool.domain in DOMAINS, f"bad tool: {tool.name}"
    TOOLS[tool.name] = tool
    return tool


def validate_params(tool: ReadTool, params: dict | None):
    params = params or {}
    extra = set(params) - set(tool.params)
    if extra:                                             # ★白名单：多余键即拒
        raise ValueError(f"unknown_param:{sorted(extra)}")
    for k, v in params.items():
        if not isinstance(v, (str, int, float, bool)) or isinstance(v, str) and len(v) > 64:
            raise ValueError(f"bad_param_type:{k}")
    return params


async def call_tool(ledger, name: str, *, session_id: str, params: dict | None = None,
                    now_fn=time.time) -> ToolResult:
    """功能入口：校验 → 取数 → 审计。参数永远只走绑定，不参与 SQL 拼接。"""
    ensure_registered()
    t0 = time.perf_counter()
    tool = TOOLS.get(name)
    if tool is None:
        res = ToolResult(tool=name, domain="?", unknown_reason="unknown_tool",
                         safe_sentence=unknown_sentence("没有这个工具"))
        await _audit(ledger, session_id, name, params, res, "unknown_tool", t0, now_fn)
        return res
    try:
        validate_params(tool, params)
        res = await tool.run(ledger, params=params, now_fn=now_fn)
        code = res.unknown_reason
    except ValueError as e:
        res = ToolResult(tool=name, domain=tool.domain, unknown_reason="invalid_params",
                         safe_sentence=unknown_sentence(f"参数不合法（{e}）"))
        code = f"invalid_params:{e}"
    await _audit(ledger, session_id, name, params, res, code, t0, now_fn)
    return res


async def _audit(ledger, session_id, name, params, res, code, t0, now_fn):
    ms = int((time.perf_counter() - t0) * 1000)
    try:
        await ledger.execute(
            """INSERT INTO read_tool_audit (call_id, session_id, tool, domain, params_digest,
                   revision, observed_at, stale, facts_digest, evidence_json, spoken_text,
                   ok, error_code, duration_ms, at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uuid.uuid4().hex, session_id, name, res.domain, digest(params or {}),
             res.revision, res.observed_at, int(res.stale), digest(res.facts or {}),
             json.dumps(res.evidence_ref or []), res.safe_sentence,
             int(code is None), code, ms, _iso(now_fn())))
    except Exception:
        # ★审计写不进去必须喊出来, 不许静默当作成功
        await ledger.record_alert("read_tool_audit_failed",
                                  {"tool": name, "session": session_id},
                                  level="warning", bypass_freeze=True)
