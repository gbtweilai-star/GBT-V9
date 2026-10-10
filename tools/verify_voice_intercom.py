# tools/verify_voice_intercom.py —— 语音对讲验收（**真跑音频**，不是看代码）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 真机病因（2026-10-08 实测）：说/听各自都有本机免费通道（SAPI 离线 TTS+ASR、edge 神经女声），
#   断点在**报告层与主链被绑死在从未启动、且不含 ASR 路由的 3900** 上 ⇒ 一律判「能说不能听」。
# 本验收把「跑通」变成可判定读数：① 通道自报双向；② 离线合成→听写**关键词命中**；
#   ③ 走完整对讲链（ptt：音频→转码→听写→意图→执行→回话）能听见且给回话。
# 用法：python tools/verify_voice_intercom.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from core.swallow import swallow as _swallow

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
    print("== 语音对讲验收 @", ROOT, "==")
    from senses import voice_sapi as VS
    from core import voice_control as VC
    from core import voice_center as VN

    # ── ① 通道：说、听、对讲可用度 ──
    tts = VC.tts_channel()
    asr = VC.asr_channel()
    st = VC.status()
    check("说：有可用回声通道", bool(tts.get("选中")),
          "选中=%s（台湾女声=%s / SAPI=%s）" % (
              tts.get("选中"), (tts.get("台湾女声") or {}).get("ok"),
              (tts.get("本机SAPI") or {}).get("ok")))
    check("听：报告层认到**本机**识别器（不再只认 3900）",
          bool(asr.get("ok")) and asr.get("选中") == "sapi-local",
          "选中=%s · %s" % (asr.get("选中"), str((asr.get("本机SAPI") or {}).get("识别器"))))
    check("对讲可用度 = 双向", st.get("对讲可用度") == "双向",
          "%s（下一步：%s）" % (st.get("对讲可用度"), st.get("下一步") or "无"))
    check("SAPI 识别器就位", bool(VS.asr_status().get("可用")),
          str(VS.asr_status().get("识别器")))

    # ── ② 离线往返：合成 → 听写（全程本机，不上云）──
    probe = ROOT / "state" / "_voice_verify.wav"
    say = "今天天气不错，请帮我看看这个专案。"
    w = VS.tts_to_wav(say, out=probe)
    check("合成出 WAV（真音频字节）", bool(w.get("ok")) and probe.is_file()
          and probe.stat().st_size > 1024,
          "%s 字节 · %s" % (probe.stat().st_size if probe.is_file() else 0,
                             (w.get("voice") or w.get("通道") or "")))
    rec = VS.recognize(probe) if probe.is_file() else {"ok": False, "text": ""}
    heard = (rec.get("text") or "").strip()
    check("听回来有字", bool(heard), "听懂：%s" % (heard or "（空）"))
    hit = [k for k in ("天气", "看看", "专案", "今天", "帮我") if k in heard]
    check("关键词命中（不是空转）", bool(hit), "命中=%s" % (hit or "无"))

    # ── ③ 完整对讲链：音频 → 转码 → 听写 → 意图 → 执行 → 回话 ──
    audio = probe.read_bytes() if probe.is_file() else b""
    full = VN.ptt(audio, suffix=".wav", speak_reply=False) if audio else {}
    check("对讲闭环：听见了", bool(full.get("听见")),
          "听见=%s（转换=%s）" % (full.get("听见"), (full.get("转换") or {}).get("why")))
    check("对讲闭环：给出了回话", full.get("回话") is not None and full.get("意图") is not None,
          "意图=%s · 回话=%s" % (full.get("意图"), str(full.get("回话"))[:60]))
    check("口径：音频只在本机转换识别（不上云）",
          "不上云" in str(full.get("口径") or ""), str(full.get("口径"))[:40])

    try:                                                       # 清掉本次探针音频
        probe.unlink(missing_ok=True)
    except OSError as e:
        _swallow(__file__, e)

    print("\n说明：本验收**真跑了音频**（合成→听写→对讲链）；回话那一步用 speak_reply=False，")
    print("      免得测试时突然出声 —— 出声通道本身已由①单独验过。")
    print("结论：" + ("✅ 语音对讲已跑通" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
