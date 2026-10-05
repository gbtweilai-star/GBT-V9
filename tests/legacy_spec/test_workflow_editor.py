# tests/test_workflow_editor.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import json, pytest
from workflows.catalog import build_catalog, spec_of
from workflows.store import save_flow, get_flow, list_flows, VersionConflict
from skills.spec import check_secret_field, scan_flow_secrets


class Reg:
    def __init__(self):
        self.skills = {"diagram@1": _Declared(), "legacy@1": _Undeclared()}
        self._health = {}
    def get(self, n): return self.skills.get(n)


class _Declared:
    version = "1.0"
    def spec(self):
        return {"inputs": {"ir": {"type":"object","required":True},
                           "key": {"type":"string","secret":True}},
                "outputs": {"html": {"type":"string"}},
                "idempotent": True, "risk": "low"}


class _Undeclared:
    version = "0.1"


def test_catalog_marks_undeclared():
    cat = build_catalog(Reg())["nodes"]
    d = next(n for n in cat if n["id"]=="diagram@1")
    u = next(n for n in cat if n["id"]=="legacy@1")
    assert d["schema_declared"] is True and d["risk"]=="low"
    assert u["schema_declared"] is False          # 诚实标注，不做类型校验
    # 内置控制节点在内
    assert {c["id"] for c in cat} >= {"branch","map","gate"}


def test_spec_lookup_includes_control_nodes():
    s = spec_of(Reg(), "gate")
    assert s["kind"] == "gate" and s["risk"] == "high"


def test_secret_field_only_accepts_ref():
    assert check_secret_field("$secret:MY_KEY")[0] is True
    assert check_secret_field("")[0] is True
    ok, why = check_secret_field("sk-abcdefghijklmnop1234")
    assert not ok and "明文" in why


def test_scan_flow_rejects_plaintext():
    assert scan_flow_secrets({"a":{"k":"sk-abcdefghijklmnop1234"}})
    assert not scan_flow_secrets({"a":{"k":"$secret:MY_KEY"}})


def test_flow_crud_with_optimistic_lock(ledger):
    d = {"id":"f1","nodes":[],"edges":[]}
    r1 = save_flow(ledger, "f1", "测试", d)
    assert r1["version"] == 1
    r2 = save_flow(ledger, "f1", "测试", d, expect_version=1)
    assert r2["version"] == 2
    with pytest.raises(VersionConflict):
        save_flow(ledger, "f1", "测试", d, expect_version=1)   # 过期版本
    got = get_flow(ledger, "f1")
    assert got["definition"]["id"] == "f1" and got["version"] == 2
    assert any(f["id"]=="f1" for f in list_flows(ledger))


def test_edge_rules_via_validate():
    """编辑器本地规则与服务端 validate 一致：自连/成环/类型不匹配"""
    from workflows.engine import WorkflowEngine
    reg = Reg()
    f = {"id":"f","nodes":[
        {"id":"a","type":"skill","skill":"diagram@1","inputs":{}},
        {"id":"b","type":"skill","skill":"legacy@1","inputs":{}}],
        "edges":[["a","b"],["b","a"]]}
    errs = WorkflowEngine(reg).validate(f)
    assert any("环" in e for e in errs)


def test_run_records_ms_and_node(ledger):
    from workflows.engine import WorkflowEngine
    from skills.native import SkillResult

    class R2(Reg):
        def __init__(self):
            super().__init__()
            self.calls = []
        def call(self, name, request, ctx=None):
            self.calls.append(name)
            return SkillResult(True, output={"ok": True})
    reg = R2()
    f = {"id":"f","nodes":[
        {"id":"a","type":"skill","skill":"diagram@1","inputs":{"ir":{}}},
        {"id":"b","type":"skill","skill":"legacy@1","inputs":{"x":"$ref:a.output"}}],
        "edges":[["a","b"]]}
    out = WorkflowEngine(reg, ledger=ledger).run(f)
    assert out["ok"] and [s["node"] for s in out["steps"]] == ["a","b"]
    assert all("ms" in s for s in out["steps"])     # 编辑器回显耗时
