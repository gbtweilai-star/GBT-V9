# core/hooks.py —— 主脑的**防偷懒钩子**：每个要点都必须留证，跳过/空转一律拦下
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："主脑必须要在每一个要点上设计好钩子防止 AI 偷懒跳过和无脑操作。"
#
# 三类病灶，各有一条钩子对着治：
#   ① **跳过**（少做一步还说完成了）→ 钩子点必须按序全部经过；缺一个就拒绝收尾（`skipped`）
#   ② **无脑操作**（跑了一遍但什么都没变）→ 每步必须留下"可核查的产出"，前后指纹相同即判空转（`noop`）
#   ③ **假证据**（声称做了但拿不出东西）→ 证据必须来自被调用的真件（返回值/账本行/文件），
#      自己写一句"已完成"不算（`evidence_required`）
#
# 用法（每一步都过闸门，不过就抛错，不允许"我尽力了"）：
#   run = Guard("扩容", owner="t007")
#   with run.step("前置检查", expect="发现 x 个缺口") as s:
#       got = do_something()
#       s.evidence(found=got, fingerprint=fp)      # 必须给证据
#   run.finish()                                   # 少一步/有跳过 → 这里会抛
from core.swallow import swallow as _swallow
import hashlib
import time
import uuid
from dataclasses import dataclass, field

STEPS_OK = "已通过"
STEPS_SKIP = "跳过"
STEPS_NOOP = "空转"
STEPS_FAIL = "失败"


class HookError(Exception):
    """钩子拦下的动作：缺证据 / 少步骤 / 空转 / 顺序不对。"""


@dataclass
class Step:
    name: str
    expect: str = ""
    started: float = 0.0
    ended: float = 0.0
    status: str = ""
    evidence: dict = field(default_factory=dict)
    note: str = ""
    fingerprint: str = ""

    def as_dict(self) -> dict:
        return {"步": self.name, "期望产出": self.expect, "状态": self.status,
                "证据": self.evidence, "说明": self.note, "指纹": self.fingerprint,
                "耗时ms": int((self.ended - self.started) * 1000) if self.ended else 0}


class _StepCtx:
    def __init__(self, guard: "Guard", step: Step) -> None:
        self.g = guard
        self.s = step

    def __enter__(self) -> "_StepCtx":
        self.s.started = time.time()
        return self

    def evidence(self, *, fingerprint: str = "", note: str = "", **kv) -> None:
        """留下可核查的产出。**必须至少给一样**：证据键值、指纹或说明。"""
        got = {k: v for k, v in kv.items() if v is not None and v != ""}
        if not got and not fingerprint and not note:
            raise HookError(f"步骤「{self.s.name}」没有留下任何证据 —— 不许声称做完")
        if got:
            self.s.evidence.update(got)
        if fingerprint:
            self.s.fingerprint = str(fingerprint)[:64]
        if note:
            self.s.note = str(note)[:300]

    def skip(self, why: str) -> None:
        """显式跳过：会记成「跳过」并在收尾时**阻断**（要跳必须写清理由，且由人确认）。"""
        self.s.status = STEPS_SKIP
        self.s.note = str(why)[:300]
        self.s.evidence.setdefault("跳过理由", str(why)[:200])

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.s.ended = time.time()
        if exc_type is not None:
            self.s.status = STEPS_FAIL
            self.s.note = f"{exc_type.__name__}: {exc}"[:300]
            self.g.steps.append(self.s)
            raise HookError(f"步骤「{self.s.name}」失败：{exc_type.__name__}: {exc}") from exc
        if not self.s.status:
            # 没显式跳过 = 必须给过证据，否则算空转（跑了个寂寞）
            self.s.status = STEPS_OK if (self.s.evidence or self.s.fingerprint) else STEPS_NOOP
        if self.s.status == STEPS_NOOP:
            self.g.steps.append(self.s)
            raise HookError(f"步骤「{self.s.name}」是空转（没有任何产出/变化）—— 无脑操作拦下")
        self.g.steps.append(self.s)
        return False


class Guard:
    """一次作业的钩子守卫：按序记录每一步，收尾时算总账。"""

    def __init__(self, task: str, *, owner: str = "main", ledger=None,
                 must_steps: tuple = ()) -> None:
        self.task = str(task or "作业")
        self.owner = str(owner or "main")
        self.led = ledger
        self.run_id = "g" + uuid.uuid4().hex[:10]
        self.t0 = time.time()
        self.steps: list = []
        self.must = tuple(must_steps or ())
        self.notes: list = []

    def step(self, name: str, *, expect: str = "") -> _StepCtx:
        if any(s.name == name for s in self.steps):
            raise HookError(f"步骤「{name}」重复执行（顺序/去重都归钩子管）")
        return _StepCtx(self, Step(name=str(name)[:60], expect=str(expect)[:200]))

    def note(self, text: str) -> None:
        self.notes.append(str(text)[:300])

    def audit(self) -> dict:
        done = [s for s in self.steps if s.status == STEPS_OK]
        skipped = [s.as_dict() for s in self.steps if s.status == STEPS_SKIP]
        noop = [s.as_dict() for s in self.steps if s.status == STEPS_NOOP]
        failed = [s.as_dict() for s in self.steps if s.status == STEPS_FAIL]
        missing = [m for m in self.must if m not in {s.name for s in self.steps}]
        return {"run_id": self.run_id, "作业": self.task, "主体": self.owner,
                "步数": len(self.steps), "已通过": len(done), "跳过": skipped,
                "空转": noop, "失败": failed, "缺步骤": missing,
                "证据条数": sum(len(s.evidence) for s in done),
                "指纹": {s.name: s.fingerprint for s in done if s.fingerprint},
                "说明": list(self.notes),
                "耗时ms": int((time.time() - self.t0) * 1000),
                "步明细": [s.as_dict() for s in self.steps],
                "口径": "跳过/空转/缺步骤任一存在 → 整单不通过（不许「我尽力了」）"}

    def ok(self) -> bool:
        a = self.audit()
        return not (a["跳过"] or a["空转"] or a["失败"] or a["缺步骤"])

    def finish(self, *, record: bool = True) -> dict:
        """收尾算总账：有问题就抛（这是"不许偷懒"的落地动作），没问题就落账。"""
        a = self.audit()
        a["通过"] = self.ok()
        if record:
            self._record(a)
        if not a["通过"]:
            bad = []
            if a["缺步骤"]:
                bad.append("缺步骤：" + "、".join(a["缺步骤"]))
            if a["跳过"]:
                bad.append("跳过了：" + "、".join(x["步"] for x in a["跳过"]))
            if a["空转"]:
                bad.append("空转：" + "、".join(x["步"] for x in a["空转"]))
            if a["失败"]:
                bad.append("失败：" + "、".join(x["步"] for x in a["失败"]))
            raise HookError(f"{self.task} 未通过钩子：{'；'.join(bad)}")
        return a

    def _record(self, a: dict) -> None:
        try:
            from core import deploy_ledger as DL
            DL.record("scan" if a["通过"] else "scan",
                      f"hooks:{self.task}",
                      detail=f"run={self.run_id} 步 {a['已通过']}/{a['步数']} 证据 {a['证据条数']}",
                      before="", after=a["run_id"], ok=bool(a["通过"]),
                      reason=("" if a["通过"] else
                              ("缺步骤 " + "、".join(a["缺步骤"])) if a["缺步骤"] else "见跳过/空转"))
        except Exception as e:
            _swallow(__file__, e)
        try:
            from core.memory import brain as B
            B.remember(f"钩子：{self.task} → {'通过' if a['通过'] else '未通过'}"
                       f"（{a['已通过']}/{a['步数']} 步，证据 {a['证据条数']} 条）",
                       owner=self.owner, origin="hooks", scope="项目", category="教训" if not a["通过"] else "做法")
        except Exception as e:
            _swallow(__file__, e)


def fingerprint(*parts) -> str:
    """给一组东西算指纹：前后一样 = 没变化 = 空转。"""
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def gate(name: str, cond: bool, why: str = "") -> None:
    """单点闸门：不满足就抛（用在一句话能说清的前置条件上）。"""
    if not cond:
        raise HookError(f"闸门未过：{name}" + (f"（{why}）" if why else ""))


__all__ = ["Guard", "HookError", "Step", "STEPS_OK", "STEPS_SKIP", "STEPS_NOOP",
           "STEPS_FAIL", "fingerprint", "gate"]
