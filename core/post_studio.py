# core/post_studio.py —— 后期制作层：灯光师 · 调色师 · 混音师 · 质检
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「补光师，修饰影片质量的没有嘛？」
#   ⇒ 影视线原来只做到"多机位剪辑"，缺前期灯光与后期修饰。本层补齐四个岗位，
#     每个岗位都**可判定**（给读数），并作为技能挂到她的 WorkflowEngine 上。
#
#   灯光师 post_light ：三点布光配方（主光/辅光/轮廓光）+ 色温（暖主冷轮廓）+ 环境光；
#                       配方按机位（宽/中/近）与镜头序号给出，渲染前注入。
#   调色师 post_grade ：电影感调色链（对比/曲线/分离调色/锐化/暗角/颗粒）+ 可选 LUT。
#   混音师 post_mix   ：EBU R128 响度标准化（-14 LUFS 平台口径）+ 限幅 + 轻压缩。
#   质检   post_qc    ：亮度均值/峰值/饱和度/时长/响度/峰值电平 —— 给读数，不靠感觉。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST = ROOT / "render" / "post"
W, H, FPS = 1080, 1920, 30

# ── 灯光师：三点布光配方（按机位与镜头序号）────────────────────────────────
LIGHT_RECIPES: tuple = (
    dict(名="宽·暖主光", 主光=4.2, 主光角=(52, 0, -28), 辅光=1.5, 轮廓=2.6, 轮廓角=(118, 0, 196),
         环境=0.85, 主光色=(255, 236, 208), 轮廓色=(178, 205, 255),
         用于机位="宽", 说明="全身：暖主光偏左前，冷轮廓勾边，环境略亮看清衣着细节"),
    dict(名="中·柔主光", 主光=3.6, 主光角=(58, 0, -34), 辅光=1.9, 轮廓=2.2, 轮廓角=(116, 0, 200),
         环境=0.78, 主光色=(255, 240, 216), 轮廓色=(170, 200, 255),
         用于机位="中", 说明="半身：主光更柔，辅光抬高降低对比，肤色不发灰"),
    dict(名="近·硬主光", 主光=3.0, 主光角=(64, 0, -40), 辅光=1.2, 轮廓=3.0, 轮廓角=(112, 0, 204),
         环境=0.62, 主光色=(255, 232, 200), 轮廓色=(160, 195, 255),
         用于机位="近", 说明="近景：主光更硬、压环境，轮廓光拉强做出眼神光与发丝光"),
)


def light_recipe(cam_name: str, index: int = 0) -> dict:
    """给某一镜挑灯光配方（同机位内再按序号微调强度，避免四镜一模一样）。"""
    base = next((r for r in LIGHT_RECIPES if r["用于机位"] == cam_name), LIGHT_RECIPES[1])
    k = 1.0 + 0.06 * ((index % 3) - 1)
    return {**base, "主光": round(base["主光"] * k, 3), "轮廓": round(base["轮廓"] * k, 3),
            "序号": index}


# ── 调色师：电影感调色链 ───────────────────────────────────────────────────
GRADE_CHAINS = {
    # 🔴 2026-10-09 修正：原链用 brightness+medium_contrast+强锐化 ⇒ 高光爆成死白（实测该区域
    #    亮度均值 190.5 / 过曝像素大增），她衣服上印的裂纹图案被衬成"假撕裂"。
    #    改成 filmic 曲线：**只抬中间调、白点仍压回 0.98**，再配轻降噪+轻锐化+轻颗粒。
    "电影感": ("hqdn3d=1.5:1.5:4:4,"
             "curves=all='0/0.02 0.30/0.40 0.55/0.62 0.80/0.84 1/0.98',"
             "eq=contrast=1.04:saturation=1.10,"
             "colorbalance=rs=-0.015:bs=0.02:rm=0.008:bm=0.008:rh=0.012:bh=-0.015,"
             "unsharp=3:3:0.35:3:3:0.0,vignette=PI/6,noise=alls=2:allf=t"),
    "清透": ("eq=contrast=1.04:brightness=0.02:saturation=1.10:gamma=1.03,"
           "curves=preset=lighter,unsharp=5:5:0.5:5:5:0.0,vignette=PI/6"),
    "港式冷绿": ("hqdn3d=1.5:1.5:4:4,"
              "curves=all='0/0.02 0.3/0.38 0.55/0.60 0.8/0.84 1/0.97',"
              "eq=contrast=1.09:saturation=0.82:gamma=1.06,"
              "colorbalance=gs=0.045:bs=0.055:gm=0.02:bm=0.035,"
              "unsharp=3:3:0.4:3:3:0.0,vignette=PI/4.4,noise=alls=6:allf=t"),
    "义庄暖黄": ("hqdn3d=1.5:1.5:4:4,"
              "curves=all='0/0.04 0.3/0.42 0.55/0.62 0.8/0.85 1/0.97',"
              "eq=contrast=1.06:saturation=0.92:gamma=1.04,"
              "colorbalance=rs=0.05:gs=0.02:bs=-0.04:rm=0.03:bm=-0.02:rh=0.02,"
              "unsharp=3:3:0.35:3:3:0.0,vignette=PI/4.8,noise=alls=5:allf=t"),
    "霓虹冷蓝": ("hqdn3d=1.5:1.5:4:4,"
              "curves=all='0/0.02 0.3/0.37 0.55/0.60 0.8/0.84 1/0.98',"
              "eq=contrast=1.12:saturation=1.05:gamma=1.03,"
              "colorbalance=bs=0.06:bm=0.04:rh=0.03:bh=-0.03,"
              "unsharp=3:3:0.45:3:3:0.0,vignette=PI/4.2,noise=alls=7:allf=t+u"),
    "冷峻": ("eq=contrast=1.10:brightness=0.005:saturation=0.94:gamma=0.99,"
           "colorbalance=bs=0.05:bm=0.02:bh=-0.03,curves=preset=strong_contrast,"
           "unsharp=5:5:0.7:5:5:0.0,vignette=PI/4.6,noise=alls=6:allf=t+u"),
}


def target_gain(measured_lufs: float, target: float = -14.0) -> float:
    """按实测 LUFS 算增益（专业混音就是这么干的：先测再补）。"""
    return float(target) - float(measured_lufs)


def grade(src: Path, out: Path | None = None, style: str = "电影感", loudnorm: bool = False) -> dict:
    """调色 + （可选）响度标准化 → 新成片。返回读数（含调色前后亮度/饱和度对比）。"""
    POST.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    if not src.is_file():
        return {"ok": False, "error": "源片不存在"}
    out = Path(out or (POST / (src.stem + "-调色.mp4")))
    # ★补光/曝光先按**实测**来：源片偏暗就把亮度与 gamma 抬上去（不凭感觉加暗角）
    q0 = qc(src)
    y0 = q0.get("亮度均值") or 0.0
    need = max(0.0, (105.0 - y0) / 255.0)
    # 🔴 第二次修正：全局 brightness 会把"本来就亮"的区域（腿/皮肤）推到过曝（实测那块 202.8/4.66%）。
    #    改成**只抬暗部**：白点仍钉在 1.0，暗部抬 0.06~0.16，中间调轻柔上移。
    lift = round(min(0.16, 0.06 + need * 0.55), 4)
    bright = 0.0
    gam = round(1.0 + min(0.22, need * 0.75), 4)
    sat = 1.16 if y0 < 70 else 1.10
    chain = GRADE_CHAINS.get(style, GRADE_CHAINS["电影感"])
    # 暗部抬升曲线：0→+lift，0.5 只轻抬，1.0 仍是 1.0（高光不炸）
    lift_curve = "curves=all='0/%.4f 0.25/%.4f 0.5/%.4f 0.75/%.4f 1/1.0'," % (
        lift, 0.25 + lift * 0.5, 0.5 + lift * 0.28, 0.75 + lift * 0.10)
    chain = lift_curve + ("eq=contrast=1.04:gamma=%.4f:saturation=%.2f," % (gam, sat)) + chain
    vf = chain + ",format=yuv420p"
    # ★混音按实测补：单遍 loudnorm 实测只挪 2 LUFS，改成"测→算增益→压缩→限幅"
    qa = qc(src)
    cur = qa.get("响度LUFS") or -20.0
    gain = max(-6.0, min(18.0, target_gain(cur)))
    af = []
    if loudnorm:
        af = ["-af", "volume=%.2fdB,acompressor=threshold=-16dB:ratio=2.5:attack=8:release=180,"
              "alimiter=limit=0.95" % gain]
    cmd = (["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf] + af +
           ["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(out)])
    code, log = (lambda r: (r.returncode, (r.stdout or b"") + (r.stderr or b"")))(
        subprocess.run(cmd, capture_output=True, timeout=1800))
    ok = out.is_file() and out.stat().st_size > 10000
    return {"ok": ok and code == 0, "文件": str(out.relative_to(ROOT)),
            "风格": style, "链": chain.split(",")[0] + " …+" + str(len(chain.split(",")) - 1) + " 道",
            "响度标准化": bool(loudnorm), "字节": out.stat().st_size if ok else 0,
            "日志尾部": log.decode("utf-8", "replace")[-160:] if code else ""}


def mix(src: Path, out: Path | None = None, target_lufs: float = -14.0) -> dict:
    """混音师（标准两遍法）：**先测 → 带 measured_* 再用 linear 应用**。

    细节与教训（都实测过）：
      · 单加 volume 会被限幅削平（+8.5dB 只换 +4.4 LUFS）；
      · 自己叠压缩再叠增益会互相打架（历史 -18.7 → -20.9 → -19.0 上下乱跳）；
      · **loudnorm 自己就是干这个的**：一遍测量（拿到 input_i/tp/lra/thresh），
        二遍带 measured_* + linear=true 应用，实测可稳落 -14 LUFS / TP -1.5。
    """
    POST.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    if not src.is_file():
        return {"ok": False, "error": "源片不存在"}
    out = Path(out or (POST / (src.stem + "-混音.mp4")))
    # ── 第一遍：测量 ──
    r1 = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af",
                         "loudnorm=I=%.1f:TP=-1.5:LRA=11:print_format=json" % target_lufs,
                         "-f", "null", "-"], capture_output=True, timeout=900)
    log1 = (r1.stdout + r1.stderr).decode("utf-8", "replace")
    hits = re.findall(r"\{[^{}]*input_i[^{}]*\}", log1, re.S)
    meas = {}
    if hits:
        try:
            meas = json.loads(hits[-1])
        except Exception:  # noqa: BLE001
            meas = {}
    if not meas:
        return {"ok": False, "error": "第一遍测量失败（拿不到 loudnorm JSON）",
                "日志尾部": log1[-160:]}
    # ── 第二遍：带测量值 + linear 应用（不加私货增益，避免和它打架）──
    af = ("loudnorm=I=%.1f:TP=-1.5:LRA=11:measured_I=%s:measured_TP=%s:measured_LRA=%s:"
          "measured_thresh=%s:offset=%s:linear=true:print_format=summary"
          % (target_lufs, meas["input_i"], meas["input_tp"], meas["input_lra"],
             meas["input_thresh"], meas.get("target_offset", "0")))
    code, log2 = (lambda r: (r.returncode, (r.stdout or b"") + (r.stderr or b"")))(
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:v", "copy",
                        "-af", af, "-c:a", "aac", "-b:a", "192k", str(out)],
                       capture_output=True, timeout=1200))
    q = qc(out) if out.is_file() else {}
    final_l, final_p = q.get("响度LUFS"), q.get("峰值dBFS")
    ok = bool(out.is_file()) and code == 0 and final_l is not None and abs(final_l - target_lufs) <= 1.5
    return {"ok": ok, "文件": str(out.relative_to(ROOT)), "目标LUFS": target_lufs,
            "最终LUFS": final_l, "采样峰值dBFS": final_p, "测量": {k: meas.get(k) for k in
            ("input_i", "input_tp", "input_lra", "input_thresh")}, "方式": "loudnorm 两遍（linear）",
            "字节": out.stat().st_size if out.is_file() else 0,
            "日志尾部": log2.decode("utf-8", "replace")[-120:] if code else ""}


def qc(src: Path) -> dict:
    """质检：亮度均值/峰值/饱和度（signalstats）+ 响度/峰值（ebur128）—— 全读数。"""
    src = Path(src)
    if not src.is_file():
        return {"ok": False, "error": "文件不存在"}
    # 🔴 实测：lavfi 的 movie= 只吃**相对路径**（绝对路径带 C: 一律 Failed to avformat_open_input）
    try:
        mp = str(src.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        mp = str(src).replace("\\", "/")
    r = subprocess.run(["ffprobe", "-v", "error", "-f", "lavfi",
                        "-i", "movie=%s,signalstats" % mp,
                        "-show_entries", "frame_tags=lavfi.signalstats.YAVG,lavfi.signalstats.YMAX,"
                        "lavfi.signalstats.SATAVG", "-of", "csv=p=0", "-read_intervals", "%+1"],
                       capture_output=True, cwd=str(ROOT))
    rows = [x for x in r.stdout.decode("utf-8", "replace").splitlines() if x.strip()]
    vals = []
    for row in rows[:200]:
        parts = row.split(",")
        if len(parts) >= 3:
            try:
                vals.append(tuple(float(p) for p in parts[:3]))
            except Exception:
                continue
    import numpy as np
    y_avg = y_max = sat = 0.0
    if vals:
        arr = np.array(vals)
        y_avg, y_max, sat = float(arr[:, 0].mean()), float(arr[:, 1].max()), float(arr[:, 2].mean())
    r2 = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af", "ebur128=peak=true",
                         "-f", "null", "-"], capture_output=True)
    a = (r2.stdout + r2.stderr).decode("utf-8", "replace")
    lufs = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", a)
    peak = re.findall(r"Peak:\s*(-?\d+\.\d+)\s*dBFS", a)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(src)], capture_output=True)
    try:
        d = float(json.loads(dur.stdout.decode())["format"]["duration"])
    except Exception:
        d = 0.0
    # 过曝/暗部比例（示波器思维）：高光爆掉会把纹理衬成"假撕裂"
    clip = dark = clip_worst = 0.0
    try:
        from PIL import Image as _I
        import numpy as _np
        g = _np.asarray(_I.open(src).convert("L")).astype(float)
        clip = float((g > 250).mean())
        dark = float((g < 12).mean())
        # 局部过曝（示波器思维）：全局 0% 会掩盖"某块区域全白"（实测腿那块 4.66% 被全局 0% 藏住）
        hh, ww = g.shape
        worst = 0.0
        for i in range(5):
            for j in range(5):
                t = g[i * hh // 5:(i + 1) * hh // 5, j * ww // 5:(j + 1) * ww // 5]
                if t.size:
                    worst = max(worst, float((t > 250).mean()))
        clip_worst = worst
    except Exception as e:
        _swallow(__file__, e)
    # 运动量：抽 8 帧看相邻差异均值（"站桩"必须被拦下 —— 我上次就是被"不动"骗过一次）
    motion = None      # None = 测不到（必须报出来，不许装通过）
    try:
        import subprocess as _sp, tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            _sp.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "fps=1/2,scale=160:-1",
                     os.path.join(td, "m%02d.png")], capture_output=True, timeout=300)
            import glob as _g
            from PIL import Image as _I2
            import numpy as _np2
            fr = sorted(_g.glob(os.path.join(td, "m*.png")))
            gg = [_np2.asarray(_I2.open(x).convert("L")).astype(float) for x in fr]
            if len(gg) > 1:
                _dl = [float(_np2.abs(gg[i] - gg[i - 1]).mean()) for i in range(1, len(gg))]
                # 用**中位数**：硬切镜头会产生大差，均值会被切镜抬高，掩盖"站桩"
                motion = float(_np2.median(_dl))
    except Exception as e:
        _swallow(__file__, e)
    issues = []
    if motion is None:
        issues.append("运动量测不到（测量失败，不许当通过）")
    elif motion < 2.0:
        issues.append("画面几乎不动（运动量 %.2f，阈值 2.0）—— 会变成站桩幻灯片" % motion)
    if clip_worst > 0.08:
        issues.append("局部过曝（最差区块 >250 占 %.1f%%，应 <8%%）——会把纹理衬成假撕裂" % (clip_worst * 100))
    if y_avg < 60:
        issues.append("画面偏暗（亮度均值 %.1f）" % y_avg)
    if y_avg > 190:
        issues.append("画面过曝（亮度均值 %.1f）" % y_avg)
    notes = []
    if sat and sat < 12:
        # signalstats 的 SATAVG 是 8bit UV 均值：灰背景场景天然偏低（实测本片 5.1 但观感正常）
        # ⇒ 降级为**提示**，不阻塞（第一版当红灯打，是我的判据错）
        notes.append("饱和度均值偏低 %.1f（灰底场景常见，仅供参考）" % sat)
    if lufs and float(lufs[-1]) < -17:
        issues.append("整体偏轻（%.1f LUFS，平台口径约 -14）" % float(lufs[-1]))
    return {"ok": not issues, "文件": str(src.relative_to(ROOT)) if src.is_absolute() else str(src),
            "亮度均值": round(y_avg, 2), "亮度峰值": round(y_max, 2), "饱和度均值": round(sat, 2),
            "响度LUFS": float(lufs[-1]) if lufs else None, "峰值dBFS": float(peak[-1]) if peak else None,
            "时长": round(d, 2), "问题": issues, "提示": notes, "取样帧": len(vals),
            "过曝比例": round(clip * 100, 2), "局部最差过曝": round(clip_worst * 100, 2),
            "运动量": (round(motion, 2) if motion is not None else None),
            "暗部比例": round(dark * 100, 2)}


def polish(src: Path, style: str = "电影感", target_lufs: float = -14.0) -> dict:
    """一条后期流水：调色 → 混音 → 质检（三个岗位顺序走完）。"""
    t0 = time.time()
    o = qc(Path(src))          # 先留原始读数：无论后面成败都回给调用方
    g = grade(src, style=style, loudnorm=False)
    if not g["ok"]:
        return {"ok": False, "卡在": "调色", "详情": g, "原始质检": o}
    m = mix(ROOT / g["文件"], target_lufs=target_lufs)
    if not m["ok"]:
        return {"ok": False, "卡在": "混音", "详情": m, "原始质检": o, "调色": g}
    q = qc(ROOT / m["文件"])
    return {"ok": bool(q["ok"]), "原始质检": qc(Path(src)), "调色": g, "混音": m, "质检": q,
            "成片": m["文件"], "耗时秒": round(time.time() - t0, 1)}


__all__ = ["LIGHT_RECIPES", "GRADE_CHAINS", "light_recipe", "grade", "mix", "qc", "polish"]
