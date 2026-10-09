# core/senses_gate.py —— 四觉闭环 + 视觉钉死（GBT小土豆V9）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-09）：「我设计没错测试也没错，为什么她的视觉每次都要跟手分开去用传统的瞎子操作？
#   而且是**无脑操作** —— 把视觉钉死在上面，别再使用传统瞎子操作。」「还有耳、嘴。」
#
# 钉死口径（缺一步就拒动，不许"先动再看"）：
#   ① 眼：必须有一次**新鲜**的取景（帧龄 ≤ max_age_ms，默认 400ms）→ 否则拒动
#   ② 脑：必须带**决策记录**（目标 + 理由 + 预期的画面变化）→ 否则拒动（这就是"无脑操作"的解药）
#   ③ 手：动作由调用方给的可调用对象执行，本闸只放行
#   ④ 验：动作后**抓下一帧复核**（复用 vision_loop.act_and_verify）→ 没验到变化要如实报
#   ⑤ 耳/嘴：听（asr）/说（tts）也走同一套纪律；耳没权限/没设备时如实说"听不到"，不假装
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "senses_gate.jsonl"
DEFAULT_MAX_AGE_MS = 400.0


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def ear(*, seconds: float = 0.0) -> dict:
    """听：真读本机录音端点；没有端点/没权限就**如实**说听不到。"""
    out = {"抓": "ear", "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        from core import mic_io
        names = []
        try:
            names = [str(x) for x in (mic_io.list_endpoints() if hasattr(mic_io, "list_endpoints") else [])]
        except Exception:  # noqa: BLE001
            names = []
        out.update({"设备": names[:3], "可听": bool(names),
                    "判": "有录音端点" if names else "无录音端点（如实：听不到）"})
    except Exception as e:  # noqa: BLE001
        out.update({"设备": [], "可听": False, "判": "mic_io 不可用: %s" % type(e).__name__})
    _log(out)
    return out


def mouth(text: str, *, speak: bool = False) -> dict:
    """说：走本机免费离线 TTS（SAPI）；speak=False 只登记文本不发声。"""
    out = {"抓": "mouth", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "文本": (text or "")[:200],
           "说了": False}
    if speak:
        try:
            from senses import voice_sapi as VS
            r = VS.say(text) if hasattr(VS, "say") else None
            out.update({"说了": True, "回执": str(r)[:120]})
        except Exception as e:  # noqa: BLE001
            out.update({"说了": False, "判": "TTS 不可用: %s" % type(e).__name__})
    _log(out)
    return out


def require_eye(*, max_age_ms: float = DEFAULT_MAX_AGE_MS, wait_ms: float = 1200.0) -> dict:
    """**动手前必须调它**：等一次新鲜取景；取不到就返回 ok=False（调用方必须据此拒动）。"""
    from core import vision_loop as VL
    lp = VL.eyes("main")
    waited, age = 0.0, None
    while waited < wait_ms:
        age = (lp.look() or {}).get("帧龄ms")
        if age is not None and age <= max_age_ms:
            return {"ok": True, "帧龄ms": age, "等了ms": waited}
        time.sleep(0.1)
        waited += 100.0
    return {"ok": False, "帧龄ms": age, "等了ms": waited,
            "判": "没有新鲜取景 ⇒ 不许动手（禁传统瞎子操作）"}


def act(action, *, decision: dict, max_age_ms: float = DEFAULT_MAX_AGE_MS,
        eyes_name: str = "main", expect_change: bool = True, verify: str = "screen") -> dict:
    """**唯一的动手口**：眼→脑→手→验；缺任一步拒动。"""
    t0 = time.time()
    from core import vision_loop as VL
    lp = VL.eyes(eyes_name)                       # 常驻眼（起不来就在下面判）
    # 🔴 修：冷眼首帧可能还没到 ⇒ 等一会儿再判，不是立刻拒（否则动作永远动不了）
    look, age, _waited = lp.look(), None, 0.0
    while _waited < 1500:
        look = lp.look()
        age = look.get("帧龄ms")
        if age is not None and age <= max_age_ms:
            break
        time.sleep(0.1)
        _waited += 100.0
    steps = {"眼": False, "脑": False, "手": False, "验": False}
    # ① 眼
    if age is None or age > max_age_ms:
        steps["眼"] = False
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "act", "拒动": True,
               "在哪一步": "①眼", "读数": "帧龄 %s ms（要求 ≤ %.0f）" % (age, max_age_ms),
               "口径": "没有新鲜取景 ⇒ 不许动手（这就是传统瞎子操作被禁的地方）"}
        _log(rec)
        return rec
    steps["眼"] = True
    # ② 脑
    d = decision or {}
    if not (d.get("目标") and d.get("理由")):
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "act", "拒动": True,
               "在哪一步": "②脑", "读数": "decision 缺 目标/理由（收到 %s）" % list(d),
               "口径": "无脑操作被禁：必须先写清目标与理由"}
        _log(rec)
        return rec
    steps["脑"] = True
    # ③ 手 + ④ 验（复用 vision_loop 的"手打完抓下一帧复核"）
    # ④验 两种口径：screen=看屏幕像素变化；return=用**动作自己的回执**（自家 headless 浏览器不改屏幕，硬看像素会误判成没做成）
    _ret = {}

    def _wrapped():
        out = action()
        _ret["值"] = out
        return out

    r = lp.act_and_verify(_wrapped, verify_ms=float(d.get("复核等待ms") or 150.0))
    steps["手"] = bool((r.get("动作") or {}).get("做没做"))
    changed = r.get("画面变化")
    if verify == "return":
        steps["验"] = bool(_ret.get("值")) and bool((_ret.get("值") or {}).get("ok", True))
    else:
        steps["验"] = bool(r.get("复核帧")) and (changed is not None)
    ok = all(steps.values()) and ((changed is not False) if (expect_change and verify == "screen") else True)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "act", "拒动": not ok,
           "步骤": steps, "眼": look, "脑": d.get("目标"), "理由": d.get("理由"),
           "真响应ms": r.get("真响应ms"), "复核帧": r.get("复核帧"), "画面变化": changed,
           "验的口径": verify, "动作回执": (_ret.get("值") if isinstance(_ret.get("值"), dict) else None),
           "变化量": r.get("变化量"), "总ms": round((time.time() - t0) * 1000, 1),
           "口径": "眼→脑→手→验 四步全绿才算动手；画面没变要如实报（不假装做成了）"}
    _log(rec)
    return rec


def blind_spots() -> dict:
    """**扫瞎子缝**：V9 里哪些动手口没走本闸（静态判，给文件行号）。"""
    import re
    suspects = []
    for rel in ("core/browser_plug.py", "core/gui_agent.py", "core/action_loop.py",
                "core/desktop_control.py", "core/native_browser.py", "core/uia_control.py"):
        p = ROOT / rel
        if not p.is_file():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        # 认两种过闸法：接 senses_gate，或把眼钉在自己身上（引用 vision_loop 并读帧龄）
        uses_gate = ("senses_gate" in src) or ("vision_loop" in src and "帧龄" in src)
        # 找"能真动手"的调用：click/type/press/moveTo/keyDown 之类
        hits = []
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"\b(click|type_text|type\(|press|key_down|keyDown|moveTo|write_text|"
                         r"fill\(|mouse_|send_text|invoke\()", line):
                hits.append(i)
        suspects.append({"文件": rel, "过闸": uses_gate, "动作点数": len(hits), "行样本": hits[:6]})
    gaps = [s["文件"] for s in suspects if s["动作点数"] and not s["过闸"]]
    return {"瞎子缝": gaps, "缝隙数": len(gaps), "明细": suspects,
            "口径": "有动手点却没走 senses_gate ⇒ 瞎子缝；要么接闸，要么在闸里注册为已覆盖"}


def status(limit: int = 5) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"最近": rows, "瞎子缝": blind_spots(), "耳": ear(),
            "口径": "眼→脑→手→验 四步闭环 + 耳/嘴；缺一步拒动；瞎子缝静态扫出来点名"}


__all__ = ["DEFAULT_MAX_AGE_MS", "act", "ear", "mouth", "blind_spots", "status", "LEDGER"]
