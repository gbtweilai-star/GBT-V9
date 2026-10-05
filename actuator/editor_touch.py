# actuator/editor_touch.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: video.edit 只发命名动作, 由本层编译成 actuator 步骤;
#       控件定位【只认】UIA 选择器/OCR/图像匹配/标定锚点, 找不到就报卡点,
#       绝不退回"猜屏幕坐标盲点"; 每步都必须有可验证的后置条件。
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


# ── 控件定位：按可信度排序，低置信度直接抛，不降级成盲点 ──
@dataclass
class Control:
    name: str
    uia: str | None = None            # 无障碍/UIA 选择器（最稳）
    template: str | None = None       # 模板图路径（图像匹配）
    ocr: str | None = None            # OCR 文本
    anchor: tuple[str, float, float] | None = None   # (锚点控件, dx, dy) 标定相对坐标
    min_conf: float = 0.86


@dataclass
class Step:
    do: Callable[["EditorTouch", dict], Awaitable[None]]   # focus/seek/click/drag/key...
    expect: Callable[["EditorTouch", dict], Awaitable[bool]]  # 后置条件（真读数）
    desc: str
    timeout_s: float = 6.0
    safe_retry: bool = False          # 只有纯准备工作才允许重试


@dataclass
class ActionDef:
    name: str
    pre: Callable[["EditorTouch", dict], Awaitable[bool]]   # 前置条件
    build: Callable[["EditorTouch", dict], list[Step]]       # 编译成步骤


class EditorActionRegistry:
    def __init__(self): self._defs: dict[str, ActionDef] = {}
    def register(self, d: ActionDef): self._defs[d.name] = d
    def get(self, name: str) -> ActionDef:
        if name not in self._defs:
            raise KeyError(f"未注册的编辑动作: {name}")
        return self._defs[name]


# ── 适配器：唯一把命名动作落到 actuator 的地方 ──
class EditorTouch:
    def __init__(self, executor, devour, registry: EditorActionRegistry,
                 window_title: str = "剪映 / Jianying / CapCut"):
        self.executor, self.devour, self.reg = executor, devour, registry
        self.window_title = window_title

    async def act(self, target: str, op: dict, *, idempotency_key: str) -> dict:
        """target 目前只支持 editor.timeline; 返回 {state, steps, error_code}"""
        action = op.get("op")
        d = self.reg.get(f"editor.timeline.{action}")
        # 1) 前置条件：窗口在前台 + 版本/布局匹配
        if not await d.pre(self, op):
            return {"state": "blocked", "error_code": "precondition_failed",
                    "steps": [], "op": op}
        # 2) 编译成步骤（时间→屏幕是由可见标尺锚点推导，不是写死像素）
        plan = d.build(self, op)
        # 3) 交给 actuator 带验证执行（去重键保证同一步不重复点）
        res = await self.executor.run_verified(plan, idempotency_key=idempotency_key)
        return {"state": res["state"], "steps": [s.desc for s in plan],
                "error_code": res.get("error_code"), "op": op}

    # ── 基础原语（actuator 的薄封装；供 Step.do 调用）──
    async def focus_window(self) -> bool:
        return await self.executor.focus(self.window_title)      # 前台 + 校验进程名

    async def locate(self, c: Control) -> tuple[int, int] | None:
        """按 UIA→图像→OCR→锚点 顺序定位；全部低于 min_conf 返回 None。"""
        for finder in (self.executor.locate_uia, self.executor.locate_image,
                       self.executor.locate_ocr, self.executor.locate_anchor):
            pt, conf = await finder(c)
            if pt and conf >= c.min_conf:
                return pt
        return None

    async def click(self, c: Control): 
        p = await self.locate(c)
        if not p: raise RuntimeError(f"control_not_found:{c.name}")
        await self.executor.click(*p)

    async def drag(self, c: Control, dx: int): 
        p = await self.locate(c)
        if not p: raise RuntimeError(f"control_not_found:{c.name}")
        await self.executor.drag(*p, dx, 0)

    async def seek_to(self, seconds: float):
        """由可见标尺锚点算 time→x 映射后再拖播放头（DPI/多屏原点已由 executor 归一）。"""
        x = await self.executor.time_to_x(seconds)
        await self.executor.drag_playhead(x)

    async def frame_diff_over(self, a_s: float, b_s: float) -> float:
        """吞噬能取两段帧做差异，用来确认"编辑真的改了这一段"。"""
        return await self.devour.frame_diff(a_s, b_s)
