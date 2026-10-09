# workflows/engine.py —— 声明式工作流 DAG：校验 → 拓扑执行 → 追踪
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 关键防线:
#   - 无环校验(拓扑排序); 输入引用必须指向上游节点
#   - 节点类型白名单(skill|branch|map|gate); 禁止任意代码节点
#   - 密钥只能用 $secret:NAME 引用; 含明文凭据的 flow 直接拒绝
from core.swallow import swallow as _swallow
import json, re, time, uuid
from collections import deque
from senses.sqldialect import txn

NODE_TYPES = {"skill", "branch", "map", "gate"}
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|"
                       r"-----BEGIN [A-Z ]*PRIVATE KEY-----)")
_REF = re.compile(r"^\$ref:([A-Za-z0-9_\-]+)\.(output|ok)$")
_SEC = re.compile(r"^\$secret:([A-Z0-9_]+)$")


class WorkflowError(Exception):
    pass


class WorkflowEngine:
    def __init__(self, registry, ledger=None, brain=None, gate=None):
        self.reg, self.led, self.brain = registry, ledger, brain
        self.gate = gate                 # callable(node)->bool  人工闸门

    # ── 校验 ──
    def validate(self, flow: dict) -> list:
        errs = []
        nodes = {n["id"]: n for n in flow.get("nodes", [])}
        if not nodes:
            errs.append("flow 无节点")
        for n in nodes.values():
            if n.get("type") not in NODE_TYPES:
                errs.append(f"节点 {n.get('id')} 类型非法: {n.get('type')}"
                            "（禁止任意代码节点）")
            if n.get("type") == "skill" and n.get("skill") not in \
                    getattr(self.reg, "skills", {}):
                errs.append(f"节点 {n['id']} 引用了未注册能力: {n.get('skill')}")
        # 明文密钥
        blob = json.dumps(flow, ensure_ascii=False)
        if SECRET_RE.search(blob):
            errs.append("flow 含明文凭据，必须改用 $secret:NAME 引用")
        # 边 + 环检测
        edges = [(e[0], e[1]) for e in flow.get("edges", [])]
        for a, b in edges:
            if a not in nodes or b not in nodes:
                errs.append(f"边引用不存在的节点: {a}->{b}")
        order = self._topo(nodes, edges)
        if order is None:
            errs.append("flow 存在环，无法执行")
        # 引用校验：$ref 必须指向上游
        if order:
            pos = {nid: i for i, nid in enumerate(order)}
            for n in nodes.values():
                for var, val in (n.get("inputs") or {}).items():
                    m = _REF.match(str(val))
                    if m:
                        src = m.group(1)
                        if src not in nodes:
                            errs.append(f"{n['id']}.{var} 引用不存在的节点 {src}")
                        elif pos.get(src, 9e9) >= pos.get(n["id"], -1):
                            errs.append(f"{n['id']}.{var} 引用了非上游节点 {src}")
                # pipe：把某个上游节点的整份输出当本节点请求体 —— 能力串联的正门
                # （没有它，调用方就得替每个能力猜入参键名；这些离线能力多数未声明 schema）
                p = n.get("pipe")
                if p:
                    if p not in nodes:
                        errs.append(f"{n['id']}.pipe 引用不存在的节点 {p}")
                    elif pos.get(p, 9e9) >= pos.get(n["id"], -1):
                        errs.append(f"{n['id']}.pipe 引用了非上游节点 {p}")
        return errs

    def _topo(self, nodes, edges):
        indeg = {k: 0 for k in nodes}
        adj = {k: [] for k in nodes}
        for a, b in edges:
            if a in nodes and b in nodes:
                adj[a].append(b); indeg[b] += 1
        q = deque([k for k, v in indeg.items() if v == 0])
        out = []
        while q:
            cur = q.popleft(); out.append(cur)
            for nx in adj[cur]:
                indeg[nx] -= 1
                if indeg[nx] == 0:
                    q.append(nx)
        return out if len(out) == len(nodes) else None

    # ── 执行 ──
    name, version = "workflow", "dag-1.0"

    def spec(self) -> dict:
        return {
            "inputs": {
                "flow": {"type": "object", "required": True,
                         "help": "声明式 DAG（nodes/edges，节点类型白名单）"},
                "inputs": {"type": "object", "help": "flow 入参"},
                "trace_id": {"type": "string"},
            },
            "outputs": {"ok": {"type": "boolean"}, "trace_id": {"type": "string"},
                        "steps": {"type": "array"},
                        "outputs": {"type": "object"}},
            "idempotent": False,
            "risk": "high",       # 按 flow 执行任意已注册能力 → 人工闸门
        }

    def run(self, flow, inputs=None, *, trace_id=None, ctx=None):
        from skills.native import SkillContext
        errs = self.validate(flow)
        if errs:
            return {"ok": False, "errors": errs, "outputs": {}}
        nodes = {n["id"]: n for n in flow["nodes"]}
        edges = [(e[0], e[1]) for e in flow["edges"]]
        order = self._topo(nodes, edges)
        trace_id = trace_id or uuid.uuid4().hex[:12]
        results, outputs = [], {}
        ctx = ctx or SkillContext()
        ctx.task_id = trace_id

        for nid in order:
            n = nodes[nid]
            nt = n["type"]
            try:
                if nt == "gate":
                    ok = bool(self.gate(n)) if self.gate else False
                    outputs[nid] = {"ok": ok, "output": None}
                    if not ok:
                        results.append({"node": nid, "ok": False,
                                        "error": "人工闸门未通过"})
                        break
                    continue
                if nt == "branch":
                    cond = self._resolve(n.get("when"), outputs, inputs)
                    outputs[nid] = {"ok": bool(cond), "output": bool(cond)}
                    continue
                if nt == "map":
                    src = self._resolve(n.get("over"), outputs, inputs) or []
                    agg = []
                    for item in (src if isinstance(src, list) else []):
                        agg.append(self._call_skill(n.get("skill"),
                                                    {"item": item}, ctx))
                    outputs[nid] = {"ok": True, "output": agg}
                    continue
                # skill
                req = {k: self._resolve(v, outputs, inputs)
                       for k, v in (n.get("inputs") or {}).items()}
                pipe = n.get("pipe")             # 上一步输出 → 本步请求体（显式 inputs 覆盖它）
                if pipe:
                    prev = (outputs.get(pipe) or {}).get("output")
                    if isinstance(prev, dict):
                        req = {**prev, **req}
                    elif prev is not None:
                        req = {"input": prev, **req}
                _t0 = time.time()
                res = self._call_skill(n["skill"], req, ctx)
                outputs[nid] = {"ok": bool(res.get("ok")),
                                "output": res.get("output")}
                results.append({"node": nid, "ok": res.get("ok"),
                                "error": res.get("error", ""),
                                "ms": int((time.time() - _t0) * 1000)})   # 编辑器回显耗时
            except Exception as e:
                outputs[nid] = {"ok": False, "output": None}
                results.append({"node": nid, "ok": False,
                                "error": f"{type(e).__name__}: {e}", "ms": 0})
        ok = all(r.get("ok") for r in results) if results else True
        self._audit(trace_id, flow.get("id"), ok, results)
        return {"ok": ok, "trace_id": trace_id, "steps": results,
                "outputs": {k: v.get("output") for k, v in outputs.items()}}

    def _call_skill(self, name, request, ctx):
        r = self.reg.call(name, request, ctx)
        return {"ok": r.ok, "output": r.output, "error": r.error}

    def _resolve(self, val, outputs, inputs):
        if isinstance(val, str):
            m = _REF.match(val)
            if m:
                return (outputs.get(m.group(1)) or {}).get(m.group(2))
            s = _SEC.match(val)
            if s:
                import os
                return os.environ.get(s.group(1), "")
        if isinstance(val, dict):
            return {k: self._resolve(v, outputs, inputs) for k, v in val.items()}
        if isinstance(val, list):
            return [self._resolve(v, outputs, inputs) for v in val]
        return val

    def _audit(self, trace_id, flow_id, ok, results):
        if not self.led:
            return
        try:
            d = self.led.dialect
            ph = "?" if d == "sqlite" else "%s"
            with txn(self.led) as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS workflow_runs(
                    trace_id TEXT, flow_id TEXT, ts {"REAL" if d=='sqlite' else 'DOUBLE PRECISION'},
                    ok INTEGER, steps TEXT)""")
                cur.execute(f"INSERT INTO workflow_runs VALUES({','.join([ph]*5)})",
                            (trace_id, flow_id, time.time(), 1 if ok else 0,
                             json.dumps(results, ensure_ascii=False)[:4000]))
        except Exception as e:
            _swallow(__file__, e)
