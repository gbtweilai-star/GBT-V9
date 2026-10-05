# tests/test_infra_capabilities.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import os, json, time, pytest
from core.orchestration.team import Orchestrator, TeamResult
from workflows.engine import WorkflowEngine
from build.model_optimize.pipeline import ModelBuildPipeline


class FakeBrain:
    def __init__(self, decompose=None, review=None):
        self._d, self._r = decompose, review
    def chat(self, messages, **kw):
        sys_msg = messages[0]["content"] if messages else ""
        if "编排者" in sys_msg:
            return self._d or {"subtasks": [], "done_when": ""}
        if "复核者" in sys_msg:
            return self._r or {"ok": True, "issues": [], "verdict": "done"}
        return {}


class FakeRegistry:
    def __init__(self, fail=False):
        self.skills = {"diagram@1": object(), "engineering.rules@1": object()}
        self.fail = fail
        self.calls = []
    def call(self, name, request, ctx=None):
        from skills.native import SkillResult
        self.calls.append(name)
        return SkillResult(not self.fail, output={"ran": name},
                           error="boom" if self.fail else "")


# ── 编排 ──
def test_orchestration_terminates_on_consensus():
    fb = FakeBrain(decompose={"subtasks": [{"id":"s1","goal":"a","owner":"skill:diagram@1"}],
                              "done_when":"done"},
                   review={"ok": True, "issues": [], "verdict": "done"})
    orch = Orchestrator(fb, registry=FakeRegistry())
    r = orch.run("做件事", max_turns=3)
    assert r.ok and r.stopped_by == "consensus" and r.rounds == 1


def test_orchestration_max_turns_cap():
    fb = FakeBrain(decompose={"subtasks": [{"id":"s1","goal":"a","owner":"skill:diagram@1"}]},
                   review={"ok": False, "issues": ["再改"], "verdict": "revise"})
    orch = Orchestrator(fb, registry=FakeRegistry())
    r = orch.run("做件事", max_turns=2)
    assert not r.ok and r.rounds == 2 and r.stopped_by == "max_turns"


def test_orchestration_human_gate_stops():
    fb = FakeBrain(decompose={"subtasks": [{"id":"s1","goal":"删库","owner":"any"}]})
    orch = Orchestrator(fb, registry=FakeRegistry())
    r = orch.run("x", human_gate=lambda sub: "删" in sub["goal"], max_turns=3)
    assert r.stopped_by == "human_gate" and not r.ok


# ── 工作流 ──
def _flow():
    return {"id": "f1", "nodes": [
        {"id": "a", "type": "skill", "skill": "diagram@1", "inputs": {"x": 1}},
        {"id": "b", "type": "skill", "skill": "engineering.rules@1",
         "inputs": {"files": "$ref:a.output"}}],
        "edges": [["a", "b"]]}


def test_workflow_validates_and_runs():
    reg = FakeRegistry()
    wfe = WorkflowEngine(reg)
    assert wfe.validate(_flow()) == []
    out = wfe.run(_flow())
    assert out["ok"] and reg.calls == ["diagram@1", "engineering.rules@1"]


def test_workflow_rejects_cycle():
    f = _flow(); f["edges"] = [["a","b"], ["b","a"]]
    errs = WorkflowEngine(FakeRegistry()).validate(f)
    assert any("环" in e for e in errs)


def test_workflow_rejects_arbitrary_code_node():
    f = _flow(); f["nodes"].append({"id":"c","type":"code","src":"import os"})
    errs = WorkflowEngine(FakeRegistry()).validate(f)
    assert any("类型非法" in e for e in errs)


def test_workflow_rejects_plaintext_secret():
    f = _flow()
    f["nodes"][0]["inputs"]["key"] = "sk-abcdefghijklmnop1234"
    errs = WorkflowEngine(FakeRegistry()).validate(f)
    assert any("明文凭据" in e for e in errs)


def test_workflow_rejects_forward_ref():
    f = {"id":"f","nodes":[
        {"id":"a","type":"skill","skill":"diagram@1","inputs":{"x":"$ref:b.output"}},
        {"id":"b","type":"skill","skill":"engineering.rules@1","inputs":{}}],
        "edges":[["a","b"]]}
    errs = WorkflowEngine(FakeRegistry()).validate(f)
    assert any("非上游" in e or "不存在" in e for e in errs)


# ── 模型构建 ──
def test_modelopt_recipe_gate():
    mb = ModelBuildPipeline(workdir="/tmp/mb_test", require_approval=False)
    ok, why = mb.validate_recipe("int4_awq", "some/model")     # 不在 vLLM 已验证集合
    assert not ok and any("不可部署" in w for w in why)
    ok2, why2 = mb.validate_recipe("prune", "some/model")      # 需训练
    assert not ok2 and any("训练" in w for w in why2)


def test_modelopt_requires_approval():
    mb = ModelBuildPipeline(workdir="/tmp/mb_test", require_approval=True)
    r = mb.build("m", "fp8", approval=None)
    assert not r["ok"] and "审批" in r["error"]


def test_modelopt_artifact_must_be_deployable():
    mb = ModelBuildPipeline(workdir="/tmp/mb_test", require_approval=False)
    d = "/tmp/mb_test/fake"; os.makedirs(d, exist_ok=True)
    ok, why = mb.validate_artifact(d)
    assert not ok and any("hf_quant_config" in w for w in why)
    json.dump({"quantization": {"quant_algo": "FP8"}},
              open(f"{d}/hf_quant_config.json", "w"))
    open(f"{d}/model.safetensors", "wb").write(b"x")
    ok2, why2 = mb.validate_artifact(d)
    assert ok2, why2
