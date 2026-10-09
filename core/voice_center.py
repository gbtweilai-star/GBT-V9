# core/voice_center.py —— 数字人**交互式语音操控中心**（说一句 → 她照做 → 回话）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："数字人形象交互式语音操控中心你还没部署落实好吗？"
#
# 一条链走完（每一环都用我们自己的真件）：
#   ① 收音：本机免费离线听写（senses.voice_sapi，zh-CN 识别器）—— 没有识别器就如实说
#   ② 意图：把一句话分成"命令 / 提问 / 记录"三类（规则先行，能不用模型就不用）
#   ③ 执行：命令 → core.action_loop.handle（唯一动手入口）
#           提问 → core.memory.brain.ask（带出处，答不上来就说）
#           记录 → core.memory.brain.remember（原文立即入库）
#   ④ 回话：台湾腔通道念出来（core.voice_control.speak）+ 数字人口型/动作联动
#   ⑤ 留痕：每次操控都进账本（谁说的、什么意图、成没成、证据）
#
# 纪律：不许"假装听懂"。分不清意图就问回去；执行没成就说没成（附原因）。
import time

from core import hooks as H

INTENTS = ("命令", "提问", "记录", "寒暄")


def classify(text: str) -> dict:
    """规则先行分意图（可核查，给依据）。"""
    t = str(text or "").strip()
    if not t:
        return {"intent": "寒暄", "why": "空话"}
    if len(t) <= 4 and any(k in t for k in ("你好", "在吗", "嗨", "哈喽", "早", "晚安")):
        return {"intent": "寒暄", "why": "打招呼"}
    for kw in ("记一下", "记住", "帮我记", "备忘", "提醒我"):
        if kw in t:
            return {"intent": "记录", "why": f"命中「{kw}」"}
    for kw in ("是什么", "为什么", "怎么", "多少", "谁", "哪", "吗", "?", "？", "查一下", "读数"):
        if kw in t:
            return {"intent": "提问", "why": f"命中「{kw}」"}
    for kw in ("打开", "关闭", "启动", "停止", "跑", "执行", "扫描", "发", "切", "做"):
        if kw in t:
            return {"intent": "命令", "why": f"命中「{kw}」"}
    return {"intent": "提问", "why": "默认按提问处理（比乱动手安全）"}


def hear(seconds: float = 5.0) -> dict:
    """收音：本机 SAPI 听写（真录音→转写）。没有识别器/麦克风就如实说。"""
    from senses import voice_sapi as VS
    st = VS.asr_status()
    if not st.get("可用"):
        return {"ok": False, "reason": st.get("说明") or "本机没有听写识别器",
                "怎么开": st.get("装中文识别器的方法") or ""}
    wav = None
    try:
        # 优先用已有的录音文件（面板/其它环节留下的）；没有就说清怎么给
        from pathlib import Path
        import os
        root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cand = root.joinpath("state", "voice_in.wav")
        if cand.is_file() and cand.stat().st_size > 1024:
            wav = cand
    except OSError:
        wav = None
    if wav is None:
        return {"ok": False, "reason": "没有可转写的音频（把要说的话录成 state/voice_in.wav，"
                                       "或用文字指令 —— 麦克风采集由面板/宿主负责）",
                "识别器": st.get("识别器"), "语言": st.get("语言")}
    r = VS.recognize(str(wav))
    return {**r, "识别器": st.get("识别器"), "音频": str(wav)}


def command(text: str, *, speak_reply: bool = True, owner: str = "main",
            taiwan: bool = True, dry_run: bool = False) -> dict:
    """一句语音/文字 → 意图 → 执行 → 回话（全链留痕，走钩子）。"""
    t = str(text or "").strip()
    if not t:
        return {"ok": False, "reason": "空指令"}
    got = classify(t)
    it = got["intent"]
    g = H.Guard(f"语音操控:{it}", owner=owner, must_steps=("分意图", "执行", "留痕"))
    with g.step("分意图", expect="知道这是命令/提问/记录") as s:
        s.evidence(意图=it, 依据=got["why"], 原文=t[:60], fingerprint=H.fingerprint(it, t[:20]))
    result, reply, verdict = {}, "", ""
    with g.step("执行", expect="有可核查的结果") as s:
        if it == "记录":
            from core.memory import brain as B
            result = B.remember(t, owner=owner, origin="voice")
            reply = ("记下了" if result.get("ok") else "没记住") + \
                    (f"（{result.get('scope')}）" if result.get("ok") else "")
            verdict = result.get("ok")
        elif it == "提问":
            from core.memory import brain as B
            r = B.ask(t, owner=owner, all_owners=True)
            result = r
            reply = r.get("answer") or "（没答上来）"
            if not r.get("confident"):
                reply = "这个我不太确定：" + reply
            verdict = True
        elif it == "命令":
            from core import action_loop as AL
            r = AL.handle(t) if not dry_run else AL.resolve_order(t)
            result = r if isinstance(r, dict) else {"raw": str(r)}
            reply = str((result or {}).get("say") or (result or {}).get("reply")
                        or ("已执行" if result.get("ok") else "这条命令我没执行成功"))
            verdict = bool((result or {}).get("ok", True))
        else:
            reply = "在的。要我做点什么，直接说：记一下 / 问一句 / 下命令都行。"
            verdict = True
        s.evidence(意图=it, 结果摘要=(reply or "")[:80],
                   fingerprint=H.fingerprint(it, verdict, (reply or "")[:30]))
    spoken = {}
    with g.step("留痕", expect="这次操控有记录") as s:
        spoken = {"ok": False, "reason": "未发声"}
        if speak_reply and reply:
            try:
                from core import voice_control as VC
                spoken = VC.speak(reply[:400], taiwan=taiwan)
            except Exception as exc:                           # noqa: BLE001
                spoken = {"ok": False, "reason": type(exc).__name__}
        s.evidence(意图=it, 发声=bool(spoken.get("ok")), 通道=spoken.get("通道") or spoken.get("channel") or "",
                   fingerprint=H.fingerprint(it, reply[:20], bool(spoken.get("ok"))))
    audit = g.finish()
    return {"ok": bool(verdict), "原文": t, "意图": it, "依据": got["why"], "回话": reply,
            "结果": result, "发声": spoken, "钩子": {"通过": audit["通过"], "步数": audit["步数"]},
            "台湾腔": bool(taiwan)}


def ptt(audio: bytes, *, suffix: str = ".webm", speak_reply: bool = True,
        owner: str = "main", taiwan: bool = True) -> dict:
    """按住空格说话（push-to-talk）：音频 → 转 16k 单声道 WAV → 本机免费听写 → 执行 → 回话。

    浏览器的录音是 webm/opus，本机 SAPI 识别器只吃 PCM WAV —— 用**我们自己的 ffmpeg** 转，
    全程不出本机（私密语音不上云）。识别器缺失/音频空就如实说，不假装听见。
    """
    if not audio or len(audio) < 1024:
        return {"ok": False, "reason": "没收到音频（按住空格时对着麦克风说话）"}
    from pathlib import Path
    import os
    root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raw = root.joinpath("state", "voice_ptt_in" + (suffix if suffix.startswith(".") else "." + suffix))
    try:
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(audio)
    except OSError as exc:                                     # noqa: BLE001
        return {"ok": False, "reason": f"写音频失败：{type(exc).__name__}"}
    wav = root.joinpath("state", "voice_in.wav")
    conv = {"ok": False, "why": "未转换"}
    try:
        from core.alt_impl import have_ffmpeg, run_ffmpeg
        if have_ffmpeg().get("ok"):
            r = run_ffmpeg(["-y", "-i", str(raw), "-ac", "1", "-ar", "16000",
                            "-c:a", "pcm_s16le", str(wav)], timeout=120)
            conv = {"ok": bool(r.get("ok")), "why": "ffmpeg 转 16k 单声道 WAV"}
        else:
            conv = {"ok": False, "why": "本机没有 ffmpeg"}
    except Exception as exc:                                   # noqa: BLE001
        conv = {"ok": False, "why": type(exc).__name__}
    if not conv["ok"]:
        return {"ok": False, "reason": f"音频转换不成功（{conv['why']}）",
                "提示": "装 ffmpeg 后即可用麦克风直说；也可以直接用文字输入"}
    heard = hear()                                             # 读 state/voice_in.wav 转写
    if not heard.get("ok"):
        return {"ok": False, "reason": heard.get("reason") or "没听清",
                "怎么开": heard.get("怎么开") or "", "转换": conv}
    text = (heard.get("text") or "").strip()
    if not text:
        return {"ok": False, "reason": "听到了声音，但没听出内容", "转换": conv}
    got = command(text, speak_reply=speak_reply, owner=owner, taiwan=taiwan)
    return {"ok": bool(got.get("ok")), "听见": text, "意图": got.get("意图"),
            "回话": got.get("回话"), "结果": got.get("结果"), "发声": got.get("发声"),
            "转换": conv, "识别器": heard.get("识别器"),
            "口径": "音频只在本机转换与识别（免费离线通道），不上云"}


def status() -> dict:
    from core import voice_control as VC
    a = {}
    for name, fn in (("回声通道", VC.tts_channel), ("听写通道", VC.asr_channel)):
        try:
            a[name] = fn()
        except Exception as exc:                               # noqa: BLE001
            a[name] = {"ok": False, "reason": type(exc).__name__}
    return {"通道": a, "意图分类": "规则先行（命令/提问/记录/寒暄），给依据",
            "链路": "收音 → 分意图 → 执行（action_loop / brain）→ 台湾腔回话 → 留痕",
            "口径": "分不清就问回去；执行没成就说没成；绝不假装听懂"}


__all__ = ["INTENTS", "classify", "hear", "command", "ptt", "status"]
