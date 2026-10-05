# tests/test_e2e.py —— 端到端闭环测试
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import sys, json, time
from pathlib import Path
import pytest

from tests.fakes import FakeBrain, FakeDevourer, FakePyAutoGUI


# ═══════════════════════════════════════════════════════════
# ① 账本闭环：写 → 读 → 覆盖率 → 对账报告
# ═══════════════════════════════════════════════════════════
def test_ledger_closed_loop(ledger, tmp_path):
    from scan.report import reconciliation_report

    targets = {"a.py", "b.py", "c.py", "d.py"}
    ledger.log("t1", "a.py", "scanned", "")
    ledger.log("t1", "b.py", "vuln", "发现硬编码密钥")
    ledger.log("t2", "c.py", "scanned", "")
    # d.py 故意漏扫

    assert ledger.coverage(targets) == pytest.approx(0.75)
    counts = ledger.counts()
    assert counts.get("scanned") == 2
    assert counts.get("vuln") == 1
    assert ledger.scanned_by("a.py") == ["t1"]

    rep = reconciliation_report(ledger, targets, ["t1", "t2"],
                                out_path=str(tmp_path / "rep.md"))
    assert "75.0%" in rep                 # 覆盖率真读数
    assert "`d.py`" in rep                # 漏扫被点名
    assert "发现硬编码密钥" in rep          # 漏洞进清单


# ═══════════════════════════════════════════════════════════
# ② 工程师大脑闭环：计划 → 写码 → 测试通过
# ═══════════════════════════════════════════════════════════
def test_coder_success_loop(ledger, workdir):
    from core.coder import Coder

    plan = {"plan": ["建模块"], "tests": "pytest",
            "risks": [],
            "files": [{"path": "hello.py", "content": "def hi():\n    return 'ok'\n"}]}
    brain = FakeBrain(plan=plan)
    coder = Coder(brain, workdir=str(workdir),
                  test_cmd="python -c \"import hello; assert hello.hi()=='ok'\"")
    r = coder.build("创建 hello 模块")
    assert r["ok"] is True
    assert r["iters"] == 1
    assert "hello.py" in r["files"]
    assert (workdir / "hello.py").exists()
    assert (workdir / "hello.py").read_text().startswith("def hi")


def test_coder_fix_loop(workdir):
    """测试先失败 → 触发修复 → 第二轮通过"""
    from core.coder import Coder
    from tests.fakes import FakeBrain

    # 第一版写坏文件（语法错），修复版写对
    bad = {"plan": [], "tests": "", "risks": [],
           "files": [{"path": "x.py", "content": "def f(:\n  pass\n"}]}
    good = {"diagnosis": "语法错误", "explain": "修正括号",
            "patch": [{"path": "x.py", "content": "def f():\n    return 1\n"}]}
    brain = FakeBrain(plan=bad, fix=good)
    coder = Coder(brain, workdir=str(workdir),
                  test_cmd="python -c \"import x; assert x.f()==1\"")
    r = coder.build("创建 x 模块")
    assert r["ok"] is True
    assert r["iters"] == 2                       # 第1轮失败，第2轮通过
    assert any(h.get("action") == "fix" for h in r["history"])


def test_coder_no_testcmd_reports_unverifiable(workdir):
    """没有 TEST_CMD → 如实返回 ok=None，不假装成功"""
    from core.coder import Coder
    from tests.fakes import FakeBrain
    coder = Coder(FakeBrain(), workdir=str(workdir), test_cmd=None)
    r = coder.build("随便")
    assert r["ok"] is None
    assert "TEST_CMD" in r["reason"]


# ═══════════════════════════════════════════════════════════
# ③ 执行层闭环：定位 → 执行 → 验证 → 落账
# ═══════════════════════════════════════════════════════════
def test_actuator_action_loop(ledger):
    from core.actuator import Actuator, Action
    fake_gui = FakePyAutoGUI()
    act = Actuator(ledger, FakeBrain(), devour=None, pyautogui=fake_gui)
    a = Action(primitive="click", target={"coords": [100, 200]},
               args={"button": "left"})
    r = act.run_action(a)
    assert r["ok"] is True
    assert r["layer"] == "coords"                # 走坐标兜底层
    assert ("click", (100, 200), {"button": "left", "clicks": 1}) in fake_gui.actions

    rows = ledger.all_rows()
    # action_log 是独立表，用连接直查
    cur = ledger.conn.execute("SELECT primitive,layer,ok FROM action_log")
    logged = cur.fetchall()
    assert logged and logged[0][0] == "click" and logged[0][1] == "coords"


def test_actuator_risk_gate(ledger, monkeypatch):
    """高风险动作默认走确认门，不执行"""
    from core.actuator import Actuator, Action
    fake_gui = FakePyAutoGUI()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_gui)
    brain = FakeBrain()
    act = Actuator(ledger, brain, devour=None, auto_confirm=False)

    a = Action(primitive="click", target={"coords": [1, 1]},
               args={"note": "删除所有文件"})
    r = act.run_action(a)
    assert r["ok"] is False
    assert r["needs_confirm"] is True
    assert fake_gui.actions == []                 # 未执行
    assert brain.asks                            # 已上报大脑


def test_actuator_locate_priority(ledger):
    """定位优先级：coords 命中时直接返回坐标层"""
    from core.actuator import Actuator
    act = Actuator(ledger, FakeBrain(), devour=None)
    loc = act._locate({"coords": [5, 6]})
    assert loc["layer"] == "coords" and loc["x"] == 5


def test_actuator_task_done(ledger):
    """run_task：大脑说 done 就立即收工"""
    from core.actuator import Actuator
    act = Actuator(ledger, FakeBrain(action={"done": True}), devour=None)
    r = act.run_task("随便一个目标")
    assert r["ok"] is True
    assert r["steps"] == 0


# ═══════════════════════════════════════════════════════════
# ④ 吞噬能闭环：落帧 → 索引 → 零丢帧审计
# ═══════════════════════════════════════════════════════════
def test_devour_zero_loss(ledger, tmp_path):
    from senses.devour import Devour
    dev = Devour(frame_dir=str(tmp_path / "dev"), fps=30,
                 ledger=ledger, source="synthetic")   # 合成真 PNG，不碰真实屏幕

    for _ in range(10):
        assert dev.capture_one() is not None      # 顺序吞 10 帧

    assert dev.audit_gaps() == []                 # 无断档 = 零丢帧

    # 帧文件与索引都在
    assert len(list(Path(dev.frames).glob("f*.png"))) == 10
    idx = (Path(dev.frames) / "index.jsonl").read_text().splitlines()
    assert len(idx) == 10


def test_devour_gap_detected(ledger, tmp_path):
    """故意丢帧（drop=True）→ 审计必须报断档"""
    from senses.devour import Devour
    dev = Devour(frame_dir=str(tmp_path / "dev"), fps=30,
                 ledger=ledger, source="synthetic")
    dev.capture_one()
    dev.capture_one(drop=True)                    # 模拟丢一帧（审计演练）
    dev.capture_one()

    gaps = dev.audit_gaps()
    assert len(gaps) == 1                         # 丢帧必被发现


# ═══════════════════════════════════════════════════════════
# ⑤ 全链路：真扫出密钥 → 落账 → 吞噬 → 对账
# ═══════════════════════════════════════════════════════════
def test_full_pipeline_secret_scan(ledger, workdir, tmp_path):
    from scan.scanner import enumerate_targets
    from scan.scan_rules import scan
    from scan.report import reconciliation_report
    from senses.devour import Devour

    # 造一个有真漏洞的项目
    (workdir / "config.py").write_text('AWS = "AKIAIOSFODNN7EXAMPLE"\n')
    (workdir / "safe.py").write_text("print('hello')\n")
    (workdir / "sub").mkdir()
    (workdir / "sub" / "danger.py").write_text("import os\nos.system(cmd)\n")

    targets = enumerate_targets(str(workdir))
    assert len(targets) >= 3                      # 顶层起，无死角

    # 并发扫描落账
    vulns = 0
    for t in sorted(targets):
        finding = scan(t)
        if finding:
            vulns += 1
            ledger.log("t1", t, "vuln", str(finding))
        else:
            ledger.log("t1", t, "scanned", "")
    assert vulns >= 2                             # 密钥 + 危险API 都抓到

    # 吞噬并行跑一小段
    from senses.devour import Devour
    dev = Devour(frame_dir=str(tmp_path / "dev"), fps=30,
                 ledger=ledger, source="synthetic")
    for _ in range(5):
        dev.capture_one()
    assert dev.audit_gaps() == []

    # 对账：覆盖率与漏洞都在
    rep = reconciliation_report(ledger, targets, ["t1"], out_path=None)
    assert "100.0%" in rep
    assert "AKIA" in rep or "密钥" in rep


# ═══════════════════════════════════════════════════════════
# ⑥ 隔离总线：配额闸门 + 记忆隔离
# ═══════════════════════════════════════════════════════════
def test_isolated_bus(ledger):
    from core.isolated_bus import TentacleKeyRing, MemoryStore

    TentacleKeyRing.issue("tA", rpm=2)
    TentacleKeyRing.issue("tB", rpm=2)
    assert TentacleKeyRing.allow("tA") is True
    assert TentacleKeyRing.allow("tA") is True
    assert TentacleKeyRing.allow("tA") is False   # 第3次超 rpm=2
    assert TentacleKeyRing.allow("tB") is True    # tB 不受影响 → 配额隔离
    assert TentacleKeyRing.key_of("tA") != TentacleKeyRing.key_of("tB")

    mem = MemoryStore()
    mem.remember("tA", "k", "A的秘密")
    mem.remember("tB", "k", "B的秘密")
    assert mem.recall("tA", "k")["value"] == "A的秘密"
    assert mem.recall("tB", "k")["value"] == "B的秘密"
    assert set(mem.inspect_all().keys()) == {"tA", "tB"}   # 主脑可跨读


# ═══════════════════════════════════════════════════════════
# ⑦ mesh 全互联：任意节点互通
# ═══════════════════════════════════════════════════════════
def test_mesh_interconnect():
    from core.mesh import Mesh
    m = Mesh()
    for i in range(1, 6):
        m.register(f"D{i}", handler=lambda p: p)  # 全互联：注册即可 O(1) 直达
    assert m.nodes() == ["D1", "D2", "D3", "D4", "D5"]
    r = m.send("D5", {"from": "D1", "msg": "hi"})
    assert r["ok"] is True and r["result"] == {"from": "D1", "msg": "hi"}  # D1 直达 D5
