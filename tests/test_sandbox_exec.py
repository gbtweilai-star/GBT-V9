# tests/test_sandbox_exec.py —— 受限执行 / 写时复制 / 回滚 / 闭环验收（蒸馏 MuseWork 理念）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import sys

import pytest

from core.sandbox_exec import (CowWorkspace, SandboxExec, SandboxPolicy, run_loop,
                               run_plan)


@pytest.fixture
def proj(tmp_path):
    tgt = tmp_path / "proj"
    tgt.mkdir()
    (tgt / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tgt / "data.txt").write_text("keep", encoding="utf-8")
    return tgt


@pytest.fixture
def ws(proj, tmp_path):
    return CowWorkspace(proj, root=tmp_path / "sbx")


# ═══ 策略 ═══
def test_policy_modes_and_env_whitelist(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "should-not-leak")
    with pytest.raises(ValueError):
        SandboxPolicy(mode="godmode")
    env = SandboxPolicy(mode="cow").child_env()
    assert "SECRET_TOKEN" not in env                     # 凭据不默认进沙箱
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert SandboxPolicy(mode="cow").as_dict()["secrets_forwarded"] is False


def test_readonly_refuses_writes(ws):
    r = ws.write_text("app.py", "x", policy=SandboxPolicy(mode="readonly"))
    assert r["ok"] is False and "readonly" in r["reason"]
    assert (ws.target / "app.py").read_text(encoding="utf-8").count("a - b") == 1


# ═══ 写时复制：改副本，原件不动 ═══
def test_cow_writes_shadow_and_leaves_original_untouched(ws):
    ws.stage()
    r = ws.write_text("app.py", "def add(a, b):\n    return a + b\n",
                      policy=SandboxPolicy(mode="cow"))
    assert r["ok"] is True and "shadow" in r["path"]
    assert "a - b" in (ws.target / "app.py").read_text(encoding="utf-8")   # 原件未变
    assert "a + b" in (ws.read_path("app.py")).read_text(encoding="utf-8")  # 读副本优先


def test_diff_reports_before_and_after(ws):
    ws.stage()
    ws.write_text("app.py", "NEW", policy=SandboxPolicy())
    ws.write_text("extra.txt", "new file", policy=SandboxPolicy())
    d = ws.diff()
    kinds = {c["kind"] for c in d["changes"]}
    assert kinds == {"modified", "added"}
    mod = next(c for c in d["changes"] if c["kind"] == "modified")
    assert mod["sha_before"] and mod["sha_after"] and mod["sha_before"] != mod["sha_after"]


def test_rollback_discards_copies_and_keeps_target(ws):
    ws.stage()
    ws.write_text("app.py", "CHANGED", policy=SandboxPolicy())
    rb = ws.rollback()
    assert rb["shadow_removed"] is True and rb["target_intact"] is True
    assert not ws.shadow.exists()
    assert "a - b" in (ws.target / "app.py").read_text(encoding="utf-8")


# ═══ 受限执行 ═══
def test_shell_metachars_are_refused(ws):
    ex = SandboxExec(ws, SandboxPolicy())
    for bad in ("echo hi; rm -rf /", "a && b", "x | y", "`whoami`", "a > b"):
        with pytest.raises(ValueError, match="元字符"):
            ex.run(bad)


def test_run_records_evidence(ws):
    ws.stage()
    ex = SandboxExec(ws, SandboxPolicy())
    r = ex.run([sys.executable, "-c", "print(6*7)"])
    assert r["rc"] == 0 and "42" in r["out"]
    assert r["out_sha"] and r["ms"] >= 0 and r["timed_out"] is False
    assert ex.history[-1]["cmd"][0] == sys.executable


def test_timeout_is_reported_not_hidden(ws):
    ws.stage()
    ex = SandboxExec(ws, SandboxPolicy(timeout_s=2))
    r = ex.run([sys.executable, "-c", "import time; time.sleep(5)"])
    assert r["rc"] == 124 and r["timed_out"] is True


# ═══ 闭环：动手 → 证据 → 验收（不过就回滚）═══
def test_loop_passes_with_acceptance_evidence(ws):
    steps = [{"name": "验证改动生效",
              "cmd": [sys.executable, "-c",
                      "import pathlib,sys;sys.exit(0 if 'a + b' in "
                      "pathlib.Path('app.py').read_text() else 1)"]}]
    ws.write_text("app.py", "def add(a, b):\n    return a + b\n", policy=SandboxPolicy())
    out = run_loop(steps, ws, accept=lambda ev: (ev["diff"]["count"] > 0, "必须有改动"))
    assert out["ok"] is True and out["evidence"]["artifacts"] == ["app.py"]


def test_loop_rolls_back_when_acceptance_fails(ws):
    ws.write_text("app.py", "def add(a, b):\n    return a + b\n", policy=SandboxPolicy())
    steps = [{"name": "跑一下", "cmd": [sys.executable, "-c", "print('ok')"]}]
    out = run_loop(steps, ws, accept=lambda ev: (False, "验收判据没过"))
    assert out["ok"] is False and "验收判据没过" in out["reason"]
    assert out["rollback"]["shadow_removed"] is True
    assert not ws.shadow.exists()                       # 全撤干净


def test_loop_rolls_back_on_command_failure(ws):
    steps = [{"name": "故意失败", "cmd": [sys.executable, "-c", "raise SystemExit(3)"]}]
    out = run_loop(steps, ws, policy=SandboxPolicy())
    assert out["ok"] is False and out["failed_at"] == 1 and "rc=3" in out["reason"]
    assert out["rollback"]["target_intact"] is True


# ═══ 无头入口（对应 muse serve 的可驱动性）═══
def test_run_plan_headless_entry(proj, tmp_path):
    out = run_plan({"target": str(proj), "root": str(tmp_path / "sbx3"),
                    "mode": "readonly",
                    "steps": [{"name": "只读检查", "cmd": [sys.executable, "-c", "print(1+1)"]}]})
    assert out["ok"] is True and out["policy"]["mode"] == "readonly"
    assert "2" in out["steps"][0]["out"]
