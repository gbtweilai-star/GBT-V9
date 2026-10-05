# actuator/editor_actions.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from actuator.editor_touch import ActionDef, Step, Control

PLAYHEAD  = Control("playhead", uia="Timeline/Playhead", anchor=("ruler", 0, 0))
RULER     = Control("ruler", uia="Timeline/Ruler", template="tpl/ruler.png", min_conf=0.9)
SPLIT_BTN = Control("split", uia="Toolbar/Split", ocr="分割")
CLIP      = Control("clip", uia="Timeline/Clip[selected]", min_conf=0.9)
SPEED_MENU= Control("speed", ocr="变速")
REVERSE   = Control("reverse", ocr="倒放")
DELETE    = Control("delete", uia="Timeline/Clip/ContextMenu/Delete", ocr="删除")


def _post_split(t):     # 后置：出现新边界（用吞噬能看片段数/接缝）
    async def check(op): return (await t.devour.clip_count()) > op.get("_pre", 0)
    return check


def build_split(t, op):
    at = op["at_s"]
    return [
        Step(desc="聚焦编辑窗", do=lambda t,o: t.focus_window(),
             expect=lambda t,o: t.executor.is_foreground(t.window_title), safe_retry=True),
        Step(desc=f"播放头定位 {at}s", do=lambda t,o: t.seek_to(at),
             expect=lambda t,o: t.executor.playhead_seconds_near(at, tol=0.05)),
        Step(desc="选中片段", do=lambda t,o: t.click(CLIP),
             expect=lambda t,o: t.executor.is_selected(CLIP)),
        Step(desc="点击分割", do=lambda t,o: t.click(SPLIT_BTN),
             expect=lambda t,o: _post_split(t)(o)),
    ]


def build_cut(t, op):
    """cut(at,to) 定义 = 删除 [at,to) 区间：两端各分割一次→选区间→删除→确认消失。"""
    a, b = op["at_s"], op["to_s"]
    steps  = build_split(t, {"at_s": a}) + build_split(t, {"at_s": b})
    steps += [
        Step(desc="选中被删区间", do=lambda t,o: t.click(CLIP),
             expect=lambda t,o: t.executor.is_selected(CLIP)),
        Step(desc="删除区间", do=lambda t,o: t.click(DELETE),
             expect=lambda t,o: t.devour.interval_gone(a, b)),
    ]
    return steps


def build_speed(t, op):
    r = op["rate"]
    return [Step(desc=f"变速 ×{r}", do=lambda t,o:(t.click(SPEED_MENU), t.executor.set_speed(r)),
                 expect=lambda t,o: t.executor.clip_speed_is(r))]


ACTIONS = [
    ActionDef("editor.timeline.split",  pre=lambda t,o: t.executor.window_ok(), build=build_split),
    ActionDef("editor.timeline.cut",    pre=lambda t,o: t.executor.window_ok(), build=build_cut),
    ActionDef("editor.timeline.speed",  pre=lambda t,o: t.executor.is_selected(CLIP), build=build_speed),
    ActionDef("editor.timeline.reverse",pre=lambda t,o: t.executor.is_selected(CLIP),
              build=lambda t,o: [Step(desc="倒放", do=lambda t,o:(t.click(REVERSE),),
                                      expect=lambda t,o: t.executor.reversed_is(True))]),
]
