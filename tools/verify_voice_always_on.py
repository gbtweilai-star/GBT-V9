# tools/verify_voice_always_on.py —— 「常开耳朵/嘴」三处收尾验收（非 LLM 判据，可复跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 对应诊断 P3（2026-10-08）：① 面板只构造 VoiceAdapter **不 start** ⇒ 队列空转；
#   ② voice_bus 把"入队成功"当"已说" ⇒ 本机 SAPI 兜底永远轮不到；
#   ③ body/witness_runtime.py:303 `from body.voice_bus import announce` 是**空引用**。
# 用法：python tools/verify_voice_always_on.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations

import asyncio
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
    print("== 常开嘴/耳收尾验收 @", ROOT, "==")
    from senses.voice import VoiceAdapter
    from senses import voice_sapi as VS
    from body.voice_bus import VoiceBus, announce, install_bus, _INSTALLED

    # 别在验收里真出声：把 SAPI 换成一个只记通道的假实现
    played = {"n": 0}
    real_speak = VS.speak

    def fake_speak(text, **kw):
        played["n"] += 1
        return {"ok": True, "通道": "sapi(fake)"}

    VS.speak = fake_speak
    try:
        # ── ① 队列必须 start 才真跑 ──
        a = VoiceAdapter()
        check("新建的队列是没跑的（started=False）", a.started is False, "started=%s" % a.started)
        a.start()
        check("start() 之后才算真跑", a.started is True, "started=%s" % a.started)
        a.start()
        check("重复 start 不再起第二条 drain 线程", a.started is True, "started=%s" % a.started)

        # ── ② 入队不算已说：没 start 的适配器必须落到本机 SAPI ──
        idle = VoiceAdapter()
        bus1 = VoiceBus(idle, ledger=None, on_page_event=None)
        asyncio.run(bus1._speak_one("测试一句", False))
        check("没 start 的队列：不算已说，退回本机 SAPI",
              bus1.last_channel == "sapi" and played["n"] == 1,
              "通道=%s 出声次数=%d" % (bus1.last_channel, played["n"]))
        check("入队确实没被当成成功",
              getattr(idle, "stats", {}).get("done", 0) == 0,
              "队列 stats.done=%s" % idle.stats.get("done"))

        # ── ③ announce 真的存在且可用（witness_runtime 那行不再空引用）──
        _INSTALLED.pop("bus", None)
        r1 = asyncio.run(announce("见证异常：w1", priority=0))
        check("announce 无 bus 时退本机 SAPI（并标出通道）",
              r1.get("ok") and r1.get("通道") == "sapi",
              "通道=%s" % r1.get("通道"))

        class FakeBus:
            def say(self, text, critical=False):
                return {"spoken": True, "critical": critical, "text": text}

        install_bus(FakeBus())
        r2 = asyncio.run(announce("见证异常：w2", priority=0))
        check("announce 有 bus 时交给 bus（不再只剩写日志）",
              r2.get("ok") and str(r2.get("通道", "")).startswith("voice_bus"),
              "通道=%s" % r2.get("通道"))
        check("空文本不假装播过", asyncio.run(announce(""))["ok"] is False, "ok=False")
        _INSTALLED.pop("bus", None)

        # ── ④ 面板接线（静态守卫：两处都得起队列）──
        srv = (ROOT / "panel" / "server.py").read_text(encoding="utf-8")
        check("面板两处 VoiceAdapter 都调了 start()",
              srv.count("VoiceAdapter()") == srv.count("adapter.start()") + srv.count("tts.start()"),
              "VoiceAdapter()×%d · start()×%d" % (
                  srv.count("VoiceAdapter()"),
                  srv.count("adapter.start()") + srv.count("tts.start()")))
        check("面板把 bus 登记给 body（install_bus）", "install_bus(" in srv, "已登记")
    finally:
        VS.speak = real_speak

    print("\n说明：验收里把本机 SAPI 换成假实现，避免测试时突然出声；真出声由 verify_voice_intercom 单验。")
    print("结论：" + ("✅ 常开嘴/耳三处已收尾" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
