# tests/test_video_edit_touch.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import pytest
from actuator.editor_touch import EditorTouch, EditorActionRegistry
from actuator.editor_actions import ACTIONS

class FakeExecutor:
    def __init__(self): self.calls = []
    async def run_verified(self, plan, *, idempotency_key):
        for s in plan:                       # 按序执行, 全部后置条件为真
            self.calls.append((idempotency_key, s.desc))
            assert await s.expect(self, {}) is not False
        return {"state": "verified"}

class FakeDevour:
    async def frame_diff(self, a, b): return 0.42        # 有变化
    async def interval_gone(self, a, b): return True

@pytest.mark.asyncio
async def test_split_compiles_in_order():
    reg = EditorActionRegistry()
    for a in ACTIONS: reg.register(a)
    ex = FakeExecutor()
    touch = EditorTouch(ex, FakeDevour(), reg)
    res = await touch.act("editor.timeline", {"op": "split", "at_s": 3.0},
                          idempotency_key="p:0")
    assert res["state"] == "verified"
    descs = [d for _, d in ex.calls]
    assert descs == ["聚焦编辑窗", "播放头定位 3.0s", "选中片段", "点击分割"]

@pytest.mark.asyncio
async def test_ambiguous_timeout_does_not_reclick():
    class Halt(FakeExecutor):
        async def run_verified(self, plan, *, idempotency_key):
            return {"state": "blocked", "error_code": "timeout_ambiguous"}
    touch = EditorTouch(Halt(), FakeDevour(), _reg())
    res = await touch.act("editor.timeline", {"op": "split", "at_s": 1.0}, idempotency_key="p:1")
    assert res["state"] == "blocked"          # 卡点回报, 不重复点
