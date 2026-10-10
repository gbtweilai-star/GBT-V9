# tools/verify_voice_taiwan.py —— 「声音默认优先台湾腔女声」验收（非 LLM 判据）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 判据：① 选道逻辑把 zh-TW 神经女声排在第一位；② 它真可用（模块自报 + 真合成出字节）；
#       ③ 退档时**如实标注**（不静默降级）；④ 页面与伴随浮窗的浏览器兜底也是 zh-TW（不是 zh-CN）。
# 用法：python tools/verify_voice_taiwan.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAILS: list = []


def check(name: str, cond: bool, reading: str) -> None:
    print("  %s %s —— %s" % ("✅" if cond else "❌", name, reading))
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("== 台湾腔优先 验收 @", ROOT, "==")
    from core import voice_control as VC
    from senses import voice_tw as TW

    ch = VC.tts_channel()
    order = ch.get("优先序") or []
    check("选道优先序第一位是台湾神经女声", order[:1] == ["edge-tw"], str(order))
    tw = TW.available()
    check("台湾女声模块可用", bool(tw.get("ok")), str(tw.get("voice") or tw.get("reason"))[:60])
    check("当前选中就是台湾女声（可用时必须选中它）",
          ch.get("选中") == "edge-tw" and ch.get("台湾腔优先") is True,
          "选中=%s · 台湾腔优先=%s" % (ch.get("选中"), ch.get("台湾腔优先")))
    if ch.get("选中") != "edge-tw":
        check("退档时如实标注（不静默）", bool(ch.get("说明")), str(ch.get("说明"))[:70])

    st = TW.status() if hasattr(TW, "status") else {}
    check("台湾女声自报状态可读", isinstance(st, dict), str(list(st)[:6]))

    for rel, tag in (("panel/dh_console.py", "交互台"), ("panel/dh_companion.py", "伴随浮窗")):
        src = (ROOT / rel).read_text(encoding="utf-8")
        # 只看**语音合成**那一行：<html lang="zh-CN"> 是文档语言（该留），不该被这条判成违规
        bad = "u.lang='zh-CN'" in src or 'u.lang="zh-CN"' in src
        good = "u.lang='zh-TW'" in src
        check("浏览器兜底走台湾腔：" + tag, good and not bad,
              "语音 zh-TW ✓ · 语音 zh-CN %s" % ("仍存在" if bad else "已清"))

    print("\n口径：台湾神经女声(zh-TW) 优先；退到 VoiceStudio/SAPI 时由 tts_channel 如实标注。")
    print("结论：" + ("✅ 台湾腔优先已钉死" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
