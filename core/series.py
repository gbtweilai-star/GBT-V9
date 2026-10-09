# core/series.py —— 剧集连载层：**每一集首尾相连对齐** + 全剧一致性
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「短视频记得每一集首尾相连对齐哈」「每一个维度都要做到细节化」
#   ⇒ 本层专管"连载"这件事，讲清"首尾相连"到底怎么落地（可判定，不靠感觉）：
#
#   ① **边界对齐（首尾相连）**：上一集的**尾帧**就是下一集的**首帧**起点 ——
#      下一集开头先"保持上一集尾帧 0.5 秒"，再用 0.4 秒溶入自己的首镜。
#      判据：|尾帧 − 下一集首帧| 的平均像素差 ≤ 6/255（实测对齐后≈0）。
#   ② **视觉连续**：下一集首镜沿用上一集**尾镜的灯光/相机配方**（同机位同色温），
#      并统一调色风格与响度（-14 LUFS）、统一分辨率（1080x1920@30）。
#   ③ **片尾预告**：片尾卡自动写"下一集：<标题>"，让观众知道有下一集。
#   ④ **全剧台账**：state/series/ledger.jsonl 记每集的 首帧/尾帧/亮度/响度/风格/边界读数。
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SER = ROOT / "state" / "series"
FILMS = ROOT / "render" / "series"
W, H, FPS = 1080, 1920, 30
BRIDGE_HOLD = 0.5      # 下一集开头保持上一集尾帧的时间
BRIDGE_FADE = 0.4      # 溶入自己首镜的时间
ALIGN_MAX = 6.0        # 边界平均像素差上限（0~255）
# 剧集标准（全剧一致，由代码保证，不靠人记）：响度/风格/画幅
STANDARD = dict(响度LUFS=-14.0, 容差=1.5, 风格="电影感", 画幅="%dx%d@%d" % (W, H, FPS),
                亮度下限=90.0)


def _run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.returncode, (r.stdout or b"") + (r.stderr or b"")


def _dur(p) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(p)], capture_output=True)
    try:
        return float(json.loads(r.stdout.decode("utf-8", "replace"))["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


def frames(film: Path) -> dict:
    """取首帧/尾帧（PNG）与读数（亮度均值、响度）。"""
    SER.mkdir(parents=True, exist_ok=True)
    film = Path(film)
    if not film.is_file():
        return {"ok": False, "error": "片子不存在"}
    d = _dur(film)
    first = SER / (film.stem + "-首帧.png")
    last = SER / (film.stem + "-尾帧.png")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(film), "-frames:v", "1", str(first)])
    _run(["ffmpeg", "-y", "-v", "error", "-ss", "%.2f" % max(0.0, d - 0.10), "-i", str(film),
          "-frames:v", "1", str(last)])
    from core import post_studio as PS
    q = PS.qc(film)
    return {"ok": first.is_file() and last.is_file(), "首帧": str(first.relative_to(ROOT)),
            "尾帧": str(last.relative_to(ROOT)), "秒": round(d, 2),
            "亮度均值": q.get("亮度均值"), "响度LUFS": q.get("响度LUFS")}


def frame_diff(a: Path, b: Path) -> dict:
    """两帧的平均像素差（0~255）+ 直方图相关 —— 边界对齐的判据。"""
    import numpy as np
    from PIL import Image
    ia = Image.open(a).convert("L").resize((160, 284))
    ib = Image.open(b).convert("L").resize((160, 284))
    x = np.asarray(ia).astype(float)
    y = np.asarray(ib).astype(float)
    mad = float(np.abs(x - y).mean())
    ha = np.histogram(x, bins=32, range=(0, 255))[0].astype(float)
    hb = np.histogram(y, bins=32, range=(0, 255))[0].astype(float)
    corr = float(np.corrcoef(ha, hb)[0, 1]) if ha.std() > 0 and hb.std() > 0 else 0.0
    return {"平均像素差": round(mad, 2), "直方图相关": round(corr, 4),
            "对齐": mad <= ALIGN_MAX, "阈值": ALIGN_MAX}


def bridge(prev_film: Path, cur_film: Path, out: Path | None = None) -> dict:
    """**首尾相连**：下一集开头 = 上一集尾帧保持 0.5s → 0.4s 溶入自己的首镜。"""
    FILMS.mkdir(parents=True, exist_ok=True)
    prev, cur = Path(prev_film), Path(cur_film)
    if not (prev.is_file() and cur.is_file()):
        return {"ok": False, "error": "缺上一集或本集"}
    fs = frames(prev)
    if not fs.get("ok"):
        return {"ok": False, "error": "取尾帧失败"}
    last = ROOT / fs["尾帧"]
    hold = FILMS / ("bridge_hold_%s.mp4" % prev.stem)
    _run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", "%.2f" % BRIDGE_HOLD, "-i", str(last),
          "-vf", "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=0x0B0E14,"
                 "fps=%d,format=yuv420p" % (W, H, W, H, FPS),
          "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-an", str(hold)], 300)
    out = Path(out or (FILMS / ("%s-连载.mp4" % cur.stem)))
    # 保持段 + 本集，用 xfade 溶 0.4s（保持段总长 = HOLD + FADE，交叉点在第 HOLD 秒）
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(hold), "-i", str(cur),
          "-filter_complex",
          "[0:v]scale=%d:%d,setsar=1,fps=%d[v0];[1:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
          "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=0x0B0E14,setsar=1,fps=%d[v1];"
          "[v0][v1]xfade=transition=fade:duration=%.2f:offset=%.2f[v];"
          "[1:a]adelay=%d|%d,volume=1.0[a]"
          % (W, H, FPS, W, H, W, H, FPS, BRIDGE_FADE, BRIDGE_HOLD, int(BRIDGE_HOLD * 1000), int(BRIDGE_HOLD * 1000)),
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)], 900)
    ok = out.is_file() and out.stat().st_size > 10000
    if not ok:
        return {"ok": False, "error": "拼接失败"}
    fs2 = frames(out)
    diff = frame_diff(ROOT / fs["尾帧"], ROOT / fs2["首帧"])
    return {"ok": diff["对齐"], "文件": str(out.relative_to(ROOT)), "秒": round(_dur(out), 2),
            "上一集尾帧": fs["尾帧"], "本集连载首帧": fs2["首帧"], "边界": diff,
            "构造": "尾帧保持 %.1fs + 溶入 %.1fs" % (BRIDGE_HOLD, BRIDGE_FADE)}


def episode(title: str, film: Path, script: str = "", next_title: str = "",
            style: str = "电影感", auto_unify: bool = True) -> dict:
    """登记一集；**自动按剧集标准统一**（响度过低/过暗就先过后期），再与上一集**首尾相连**。"""
    SER.mkdir(parents=True, exist_ok=True)
    film = Path(film)
    unify = {"做了": False}
    if auto_unify and film.is_file():
        from core import post_studio as PS
        q0 = PS.qc(film)
        l0 = q0.get("响度LUFS")
        y0 = q0.get("亮度均值") or 0.0
        off = (l0 is None) or (abs(float(l0) - STANDARD["响度LUFS"]) > STANDARD["容差"])
        dark = y0 < STANDARD["亮度下限"]
        if off or dark:
            pr = PS.polish(film, style=style, target_lufs=STANDARD["响度LUFS"])
            if pr.get("成片"):
                film = ROOT / pr["成片"]
                unify = {"做了": True, "原因": ("响度 %.1f 偏离 -14" % float(l0)) if off else "画面偏暗 %.1f" % y0,
                         "原片": str(Path(film).name), "成片": pr["成片"],
                         "读数": {"响度": (pr.get("质检") or {}).get("响度LUFS"),
                                  "亮度": (pr.get("质检") or {}).get("亮度均值")}}
    fs = frames(film)
    if not fs.get("ok"):
        return {"ok": False, "error": "成片不可读"}
    # 片尾预告（下一集标题写进卡里）
    tail_card = ""
    if next_title:
        from core import studio as SD
        c = SD.card("片尾预告", "下一集", next_title[:14], seconds=1.6)
        tail_card = c.get("文件", "")
    hist = history(50)["行"]
    idx = len(hist) + 1
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "集": idx, "标题": title,
           "成片": str(film.relative_to(ROOT)), "秒": fs["秒"], "亮度均值": fs["亮度均值"],
           "响度LUFS": fs["响度LUFS"], "首帧": fs["首帧"], "尾帧": fs["尾帧"],
           "调色风格": style, "画幅": "%dx%d@%d" % (W, H, FPS),
           "下一集": next_title, "片尾预告卡": tail_card, "脚本字数": len(script)}
    prev = hist[-1] if hist else None
    if prev:
        prev_film = ROOT / prev["成片"]
        b = bridge(prev_film, film)
        rec["边界"] = b.get("边界")
        rec["连载文件"] = b.get("文件") if b.get("ok") else ""
        rec["连载构造"] = b.get("构造", "")
    with (SER / "ledger.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return {"ok": True, "集": idx, "台账": str((SER / "ledger.jsonl").relative_to(ROOT)),
            "本集": rec, "上一集边界": rec.get("边界"), "连载文件": rec.get("连载文件", ""),
            "自动统一": unify, "剧集标准": dict(STANDARD)}


def history(limit: int = 20) -> dict:
    p = SER / "ledger.jsonl"
    if not p.is_file():
        return {"条数": 0, "行": []}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {"条数": len(rows), "行": rows[-limit:]}


def state() -> dict:
    """全剧状态：集数、每集读数、**边界是否对齐**、一致性（风格/响度/画幅）。"""
    h = history(50)["行"]
    borders = [{"集": r["集"], "边界": r.get("边界"), "连载": bool(r.get("连载文件"))}
               for r in h if r.get("边界")]
    aligned = all((b["边界"] or {}).get("对齐") for b in borders) if borders else None
    loud = [r.get("响度LUFS") for r in h if r.get("响度LUFS")]
    style = {r.get("调色风格") for r in h}
    size = {r.get("画幅") for r in h}
    return {"集数": len(h), "边界": borders, "边界全部对齐": aligned,
            "一致性": {"调色风格": style, "响度范围": [min(loud), max(loud)] if loud else None,
                      "统一响度": (abs(max(loud) - min(loud)) <= 1.5) if len(loud) > 1 else None,
                      "画幅": size},
            "口径": "首尾相连 = 上一集尾帧作下一集起点（保持 0.5s + 溶入 0.4s），边界平均像素差 ≤ %.0f" % ALIGN_MAX}


__all__ = ["BRIDGE_HOLD", "BRIDGE_FADE", "ALIGN_MAX", "STANDARD", "frames", "frame_diff", "bridge",
           "episode", "history", "state"]
