# tests/test_color_effect_verify.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import pytest
from actuator.editor_verify import build_color

class FakeDevour:
    def __init__(self, applied=True, changed_all=False): self.applied, self.changed_all = applied, changed_all
    async def snapshot_at(self, pid, sec):
        return {"ref": (pid, sec), "source_frame_id": f"f{sec}|{self.applied}"}  # 动作后帧一致
    async def frame_diff_refs(self, a, b): return 0.0            # 同源帧无噪
    async def color_metrics_delta(self, a, b):
        return {"targeted": 0.5 if self.applied else 0.0, "any": 0.5 if (self.applied or self.changed_all) else 0.0}
    def targeted_color_score(self, x): return x["targeted"]
    def color_score(self, x):          return x["any"]

class FakeExec:
    def window_ok(self): return True
    async def apply_color(self, op): pass

def _run(op, dev):
    class T: pass
    t = T(); t.devour = dev; t.executor = FakeExec()
    steps = build_color(t, op)
    return steps

@pytest.mark.asyncio
async def test_color_applied_localized_passes():
    dev = FakeDevour(applied=True)
    st = _run({"project_id":"p","start_s":1.0,"end_s":3.0,"filter":"warm"}, dev)
    # 跑基线步骤
    await st[0].do(None, None); assert await st[0].expect(None, None)
    assert await st[1].expect(None, None) is True       # 区间内变、区间外不变 → 通过

@pytest.mark.asyncio
async def test_color_no_change_fails():
    st = _run({"project_id":"p","start_s":1.0,"end_s":3.0}, FakeDevour(applied=False))
    await st[0].do(None, None)
    assert await st[1].expect(None, None) is False      # 完全没变 → 失败

@pytest.mark.asyncio
async def test_color_changed_whole_clip_fails():
    st = _run({"project_id":"p","start_s":1.0,"end_s":3.0}, FakeDevour(applied=False, changed_all=True))
    await st[0].do(None, None)
    assert await st[1].expect(None, None) is False      # 整段都变 → outside 超限 → 失败
