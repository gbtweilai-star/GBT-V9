# tests/test_actuator.py —— 执行层闭环：定位→执行→验证→落账 + 风险确认门
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 全部用假鼠标（FakePyAutoGUI 注入）和脚本化大脑，不碰真实屏幕/键鼠，离线秒跑。
from pathlib import Path

import pytest

from core.actuator import Actuator, Action, _as_cmd_list
from tests.fakes import FakeBrain, FakePyAutoGUI


def _act(ledger, **kw):
    gui = FakePyAutoGUI()
    act = Actuator(ledger, FakeBrain(), devour=None, pyautogui=gui, **kw)
    return act, gui


# ═══ 定位 → 执行 → 验证 → 落账 ═══
def test_actuator_action_loop(ledger):
    act, gui = _act(ledger)
    a = Action(primitive="click", target={"coords": [100, 200]},
               args={"button": "left"})
    r = act.run_action(a)
    assert r["ok"] is True
    assert r["layer"] == "coords"                    # 走坐标兜底层
    assert ("click", (100, 200), {"button": "left", "clicks": 1}) in gui.actions

    logged = ledger.conn.execute(
        "SELECT primitive,layer,ok,verified FROM action_log").fetchall()
    assert logged and logged[0][0] == "click" and logged[0][1] == "coords"
    assert logged[0][2] in (1, True)                 # sqlite 存 1 / pg 存 true


# ═══ 高风险动作默认走确认门，不执行 ═══
def test_actuator_risk_gate(ledger):
    act, gui = _act(ledger, auto_confirm=False)
    brain = act.brain
    a = Action(primitive="click", target={"coords": [1, 1]},
               args={"note": "删除所有文件"})
    r = act.run_action(a)
    assert r["ok"] is False
    assert r["needs_confirm"] is True
    assert gui.actions == []                         # 未执行
    assert brain.asks                                # 已上报大脑


# ═══ AUTO_CONFIRM=1 时放行（主人自己的开关）═══
def test_actuator_auto_confirm_bypasses_gate(ledger):
    act, gui = _act(ledger, auto_confirm=True)
    a = Action(primitive="click", target={"coords": [2, 2]},
               args={"note": "删除所有文件"})
    r = act.run_action(a)
    assert r["ok"] is True                           # 明确放行后照常执行
    assert len(gui.actions) == 1


# ═══ 定位优先级：coords 命中直接返回坐标层 ═══
def test_actuator_locate_priority(ledger):
    act, _ = _act(ledger)
    loc = act._locate({"coords": [5, 6]})
    assert loc["layer"] == "coords" and loc["x"] == 5


# ═══ 任务级：大脑说 done 就立即收工 ═══
def test_actuator_task_done(ledger):
    gui = FakePyAutoGUI()
    act = Actuator(ledger, FakeBrain(action={"done": True}),
                   devour=None, pyautogui=gui)
    r = act.run_task("随便一个目标")
    assert r["ok"] is True
    assert r["steps"] == 0


# ═══ launch/api 探针统一参数列表（无 shell 拼接）═══
def test_as_cmd_list():
    assert _as_cmd_list(["python", "-V"]) == ["python", "-V"]
    assert _as_cmd_list("python -V") == ["python", "-V"]
    assert _as_cmd_list("python  -V") == ["python", "-V"]      # 多空格也安全


# ═══ 共用夹具：每测试一个干净的 SQLite 账本 ═══
@pytest.fixture
def ledger(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from audit.ledger import Ledger
    led = Ledger(db=str(tmp_path / "act_ledger.db"))
    yield led
    led.close()
