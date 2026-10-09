# skills/native.py —— 原生能力内核：统一契约 + 注册表 + 账本审计
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计（经复核）:
#   规则与工作流归她自己(内核); CLI/LLM/图像后端只是可替换适配器
#   每次调用写账本: 能力/版本/触手/任务ID/耗时/结果/产物哈希/错误(敏感脱敏)
#   适配器不可用 → 明确降级, 绝不伪报成功
import os, time, json, uuid, hashlib
from dataclasses import dataclass, field, asdict
from typing import Protocol, runtime_checkable, Any
from senses.sqldialect import txn
from audit.ddl import run_script
from core.swallow import swallow as _swallow


@dataclass
class Availability:
    ok: bool
    reason: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class SkillContext:
    tentacle_id: str = "brain"
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    workspace: str = "."
    ledger: Any = None
    brain: Any = None
    devour: Any = None
    timeout: int = 300
    dry_run: bool = False


@dataclass
class SkillResult:
    ok: bool
    output: Any = None
    artifacts: list = field(default_factory=list)   # [{"path","sha256","kind"}]
    warnings: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    error: str = ""


def _sha(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                h.update(b)
        return h.hexdigest()
    except Exception:
        return ""


def _redact(obj, keys=("api_key", "authorization", "token", "password", "secret")):
    if isinstance(obj, dict):
        return {k: ("***" if any(s in k.lower() for s in keys) else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


@runtime_checkable
class NativeSkill(Protocol):
    name: str
    version: str
    def probe(self) -> Availability: ...
    def run(self, ctx: SkillContext, request: dict) -> SkillResult: ...


class SkillRegistry:
    """能力注册表: 发现 / 健康 / 超时 / 降级 / 审计"""
    def __init__(self, ledger=None, brain=None, devour=None, panel=None):
        self.skills, self.led, self.brain, self.devour = {}, ledger, brain, devour
        self.panel = panel
        self._health = {}
        self._init_table()

    def _init_table(self):
        if not self.led:
            return
        d = getattr(self.led, "dialect", "sqlite")
        pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if d == "sqlite" else "BIGSERIAL PRIMARY KEY"
        ts = "REAL" if d == "sqlite" else "DOUBLE PRECISION"
        ddl = f"""CREATE TABLE IF NOT EXISTS skill_calls(
            id {pk}, ts {ts} NOT NULL, skill TEXT, version TEXT, tentacle TEXT,
            task_id TEXT, ok INTEGER, ms INTEGER, engine TEXT,
            request TEXT, result TEXT, artifacts TEXT, warnings TEXT, error TEXT);"""
        with txn(self.led) as cur:
            run_script(cur, ddl, d)

    def register(self, skill):
        self.skills[skill.name] = skill
        return skill

    def probe_all(self) -> dict:
        for n, s in self.skills.items():
            try:
                self._health[n] = s.probe()
            except Exception as e:
                self._health[n] = Availability(False, f"探测异常: {e}")
        return self._health

    def get(self, name):
        return self.skills.get(name)

    def call(self, name, request, ctx: SkillContext | None = None) -> SkillResult:
        s = self.skills.get(name)
        if not s:
            return SkillResult(False, error=f"未注册能力: {name}")
        ctx = ctx or SkillContext()
        ctx.ledger = ctx.ledger or self.led
        ctx.brain = ctx.brain or self.brain
        ctx.devour = ctx.devour or self.devour
        t0 = time.time()
        try:
            av = s.probe()
            r = (SkillResult(False, error=f"能力不可用: {av.reason}") if not av.ok
                 else s.run(ctx, request))
        except Exception as e:
            r = SkillResult(False, error=f"{type(e).__name__}: {e}")
        ms = int((time.time() - t0) * 1000)
        r.usage.setdefault("ms", ms)
        self._audit(s, ctx, request, r, ms)
        return r

    def _audit(self, s, ctx, req, r, ms):
        if not self.led:
            return
        ph = "?" if getattr(self.led, "dialect", "sqlite") == "sqlite" else "%s"
        cols = "(ts,skill,version,tentacle,task_id,ok,ms,engine,request,result,artifacts,warnings,error)"
        sql = f"INSERT INTO skill_calls{cols} VALUES({','.join([ph]*13)})"
        try:
            with txn(self.led) as cur:
                cur.execute(sql, (
                    time.time(), s.name, s.version, ctx.tentacle_id, ctx.task_id,
                    1 if r.ok else 0, ms, r.usage.get("engine", ""),
                    json.dumps(_redact(req), ensure_ascii=False)[:4000],
                    json.dumps(r.output, ensure_ascii=False, default=str)[:4000],
                    json.dumps(r.artifacts, ensure_ascii=False)[:2000],
                    json.dumps(r.warnings, ensure_ascii=False)[:1000], r.error))
        except Exception as e:
            _swallow(__file__, e)

        if self.panel:
            try:
                self.panel.push_skill(s.name, r.ok, ms)
            except Exception as e:
                _swallow(__file__, e)


    def status(self):
        return {n: {"version": s.version,
                    "probe": asdict(self._health.get(n) or Availability(False, "未探测"))}
                for n, s in self.skills.items()}
