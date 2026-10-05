# core/orchestration/team.py —— 多智能体议事：分解→派发→复核→终止→人工接管
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 只蒸馏 AutoGen 的"协作模式", 不引入它的依赖(AutoGen 已进入维护模式)。
# 终止条件必须显式: 达成一致 / 轮数上限 / 截止时间 / 人工接管。
# 与 mesh/触手的边界: 主脑编排, 触手干活; 只有一套调度器, 不重复派工。
import os, time, uuid, threading
from dataclasses import dataclass, field

SYS_DECOMPOSE = ('你是编排者。把任务拆成子任务, 只输出JSON: '
                 '{"subtasks":[{"id":"s1","goal":"...","owner":"t1|skill:x|any"}],'
                 '"done_when":"可判定的完成条件"}')
SYS_REVIEW = ('你是复核者。针对子任务结果只输出JSON: '
              '{"ok":true|false,"issues":["..."],"verdict":"continue|revise|done"}')


@dataclass
class TeamResult:
    ok: bool
    rounds: int = 0
    subtasks: list = field(default_factory=list)
    transcripts: list = field(default_factory=list)
    verdict: str = ""
    stopped_by: str = ""          # consensus|max_turns|deadline|human_gate|error
    error: str = ""


class Orchestrator:
    """主脑编排 + 触手执行；关键动作可在 human_gate 处暂停交人工"""
    def __init__(self, brain, registry=None, mesh=None, ledger=None):
        self.brain, self.reg, self.mesh, self.led = brain, registry, mesh, ledger

    # ── ① 分解 ──
    def decompose(self, task):
        r = self.brain.chat([{"role": "system", "content": SYS_DECOMPOSE},
                             {"role": "user", "content": task}])
        subs = (r or {}).get("subtasks") or []
        return subs, (r or {}).get("done_when", "")

    # ── ② 派发：优先能力注册表, 其次 mesh/触手 ──
    def dispatch(self, sub, ctx):
        owner = sub.get("owner") or "any"
        if owner.startswith("skill:"):
            name = owner.split(":", 1)[1]
            res = self.reg.call(name, sub.get("request", {}), ctx) if self.reg else None
            return {"owner": name, "ok": bool(res and res.ok),
                    "output": (res.output if res else None),
                    "error": (res.error if res else "无注册表")}
        if self.mesh:                              # 交给触手/mesh
            try:
                out = self.mesh.dispatch(owner, sub.get("goal", ""), ctx)
                return {"owner": owner, "ok": bool(out.get("ok")),
                        "output": out.get("output"), "error": out.get("error", "")}
            except Exception as e:
                return {"owner": owner, "ok": False, "output": None,
                        "error": f"{type(e).__name__}: {e}"}
        return {"owner": owner, "ok": False, "output": None, "error": "无执行通道"}

    # ── ③ 复核 ──
    def review(self, task, results):
        r = self.brain.chat([{"role": "system", "content": SYS_REVIEW},
                             {"role": "user", "content":
                              f"任务: {task}\n结果: {results}"}])
        return r or {"ok": False, "verdict": "continue", "issues": ["复核无输出"]}

    # ── ④ 主循环 ──
    name, version = "orchestration", "team-1.0"

    def spec(self) -> dict:
        return {
            "inputs": {
                "task": {"type": "string", "required": True},
                "max_turns": {"type": "integer", "default": 3,
                              "help": "轮数上限（终止条件之一）"},
                "deadline_s": {"type": "number", "default": 600,
                               "help": "截止时间（秒，终止条件之一）"},
            },
            "outputs": {"ok": {"type": "boolean"}, "rounds": {"type": "integer"},
                        "stopped_by": {"type": "string"},
                        "verdict": {"type": "string"},
                        "transcripts": {"type": "array"}},
            "idempotent": False, "risk": "low",
        }

    def run(self, task, tentacles=None, *, max_turns=3, deadline_s=600,
            human_gate=None, budget_s=None, ctx=None):
        t0 = time.time()
        subs, done_when = self.decompose(task)
        if not subs:
            return TeamResult(False, error="分解未产出子任务", stopped_by="error")
        transcripts, rounds, stopped = [], 0, "max_turns"
        deadline = t0 + float(deadline_s)

        while rounds < max_turns:
            rounds += 1
            if time.time() > deadline:
                stopped = "deadline"; break
            if budget_s and (time.time() - t0) > budget_s:
                stopped = "deadline"; break

            results = []
            for sub in subs:
                # 高风险/需要确认的动作 → 人工闸门
                if human_gate and human_gate(sub):
                    stopped = "human_gate"
                    return TeamResult(False, rounds, subs, transcripts,
                                      "需人工确认", stopped,
                                      error=f"子任务 {sub.get('id')} 触发人工闸门")
                results.append(self.dispatch(sub, ctx))
            transcripts.append({"round": rounds, "results": results})

            rev = self.review(task, results)
            if rev.get("verdict") == "done" and rev.get("ok"):
                stopped = "consensus"
                return TeamResult(True, rounds, subs, transcripts, "done", stopped)
            # revise: 把问题回灌成下一轮的子任务补充
            if rev.get("verdict") == "revise" and rev.get("issues"):
                subs = subs + [{"id": f"fix{rounds}-{i}", "goal": iss, "owner": "any"}
                               for i, iss in enumerate(rev["issues"][:3])]
        return TeamResult(False, rounds, subs, transcripts,
                          "未在轮数内达成一致", stopped)
