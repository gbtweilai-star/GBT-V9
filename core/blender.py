# core/blender.py —— blender 原生能力位：把我们的骨架与 32 动作搬进真 3D
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人 2026-10-07："可以"（批准建 blender 能力位 + 落盘授权 + 不出网）。
#
# 为什么走这条路（而不是"图生3D"）：
#   本机 torch 是 CPU 版、显卡是 AMD RX 6500M 4GB（无 CUDA），TripoSR/SF3D/Hunyuan3D
#   这类图生3D 在这台机器上跑不动；而我们**已经有一套验证过的 FK 骨架 + 32 个全肢体动作**，
#   所以直接用它生成 Blender 骨架 + 逐帧 K 帧 + EEVEE 渲染 + 导出 glTF：
#   零下载、零联网、零花费，且 2D 形象与 3D 动作共用同一套真值（不会两边打架）。
#
# 纪律：argv 全字面量 + shell=False；只在 state/blender/ 下落盘；不出网；每一步过钩子。
from core.swallow import swallow as _swallow
import json
import math
import subprocess
import time
from pathlib import Path

from core import avatar_motion as M
from core import avatar_rig as R

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "state" / "blender"
PREVIEW_DIR = ROOT / "state" / "blender" / "preview"
# 本机 Blender（只认已装好的路径；找不到就如实报，不猜）
CANDIDATES = (
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
)
SCALE = 0.02          # 我们的像素骨长 → Blender 单位（整体约 5.2 单位高，约真实身高比例）
PALETTE = {           # 照原图配色
    "suit": (0.42, 0.44, 0.49, 1.0),
    "skirt": (0.36, 0.38, 0.44, 1.0),
    "skin": (0.94, 0.88, 0.86, 1.0),
    "hair": (0.70, 0.70, 0.78, 1.0),
    "white": (0.95, 0.95, 0.97, 1.0),
    "shirt": (0.97, 0.97, 0.98, 1.0),
    "tie": (0.45, 0.46, 0.52, 1.0),
}


def blender_exe() -> str:
    """本机 Blender 可执行（找不到返回空串，调用方如实报缺）。"""
    for p in CANDIDATES:
        if Path(p).is_file():
            return p
    return ""


def status() -> dict:
    """只读状态：blender 在不在、版本、有没有 Rigify、本机 3D 路线可行性。"""
    exe = blender_exe()
    out = {"blender": exe or "（未找到）", "可用": bool(exe)}
    if exe:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               timeout=60, shell=False)
            out["版本"] = (r.stdout or "").splitlines()[0].strip() if r.stdout else ""
        except Exception as exc:                               # noqa: BLE001
            out["版本"] = f"取不到：{type(exc).__name__}"
        rig = Path(exe).parent / "4.2" / "scripts" / "addons_core" / "rigify"
        out["Rigify"] = rig.is_dir()
    out["路线"] = "bpy 生成骨架 + 逐帧 K 帧 + EEVEE 渲染 + 导出 glTF（零下载/零联网）"
    out["不在册"] = ("图生3D 本地跑不动（torch 为 CPU 版 + AMD 显卡无 CUDA）"
                    "→ 已改走云端 Tripo OpenAPI，见 core/tripo.py")
    out["产物目录"] = str(OUT)
    return out


# 材质表：逼真靠参数（皮肤次表面散射 / 布料绒面 / 皮鞋高光 / 指甲与眼睛与唇）
MATS: dict = {
    "skin":  {"base": (0.86, 0.72, 0.66), "rough": 0.42, "sss": 0.18, "spec": 0.45,
              "noise": 220.0, "bump": 0.10},
    "suit":  {"base": (0.32, 0.33, 0.37), "rough": 0.78, "sheen": 0.20,
              "noise": 300.0, "bump": 0.16},
    "skirt": {"base": (0.27, 0.28, 0.32), "rough": 0.80, "sheen": 0.18,
              "noise": 320.0, "bump": 0.16},
    "shirt": {"base": (0.92, 0.92, 0.94), "rough": 0.58, "sheen": 0.10,
              "noise": 420.0, "bump": 0.10},
    "tie":   {"base": (0.42, 0.43, 0.48), "rough": 0.48, "sheen": 0.25},
    "hair":  {"base": (0.60, 0.60, 0.68), "rough": 0.30, "spec": 0.65},
    "white": {"base": (0.90, 0.90, 0.92), "rough": 0.22, "spec": 0.60},
    "nails": {"base": (0.84, 0.78, 0.78), "rough": 0.14, "spec": 0.70},
    "eye_w": {"base": (0.95, 0.95, 0.96), "rough": 0.10, "spec": 0.60},
    "eye_d": {"base": (0.10, 0.10, 0.13), "rough": 0.06, "spec": 0.80},
    "lash":  {"base": (0.08, 0.07, 0.08), "rough": 0.35},
    "lips":  {"base": (0.72, 0.42, 0.45), "rough": 0.34, "spec": 0.55},
    "inner": {"base": (0.85, 0.62, 0.64), "rough": 0.50},
    "floor": {"base": (0.16, 0.17, 0.20), "rough": 0.70},
}
TEMPLATE = ROOT / "templates" / "blender_scene.tmpl"


def _render_script(payload: dict, *, out_png: str = "", out_glb: str = "",
                   mode: str = "still", out_dir: str = "", res=(420, 640),
                   angles: int = 8, strips: int = 8) -> str:
    """按占位符把模板渲染成可跑的自包含 bpy 脚本（模板独立成文件，避免 f-string 大括号地狱）。"""
    tpl = TEMPLATE.read_text(encoding="utf-8")
    return (tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
               .replace("__OUT_PNG__", out_png)
               .replace("__OUT_GLB__", out_glb)
               .replace("__OUT_DIR__", out_dir)
               .replace("__MODE__", mode)
               .replace("__W__", str(res[0]))
               .replace("__H__", str(res[1]))
               .replace("__ANGLES__", str(angles))
               .replace("__STRIPS__", str(strips)))


def _wrap180(deg: float) -> float:
    """把角度差归一化到 (-180, 180]：否则跨 ±180 时会绕远路（手臂会整圈抡过去）。"""
    d = (float(deg) + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def _rest_and_frames(clip: str, frames: int) -> dict:
    """算出静止姿态关节位置 + 每帧每个关节绕 Y 轴的旋转增量（Blender 侧只负责摆）。

    我们只有 2D 正向运动学（x 右 / y 下）→ 映射到 Blender 的 XZ 平面（X 右 / Z 上）。
    每个关节的旋转 = 该骨段当前方向角 − 静止方向角（同一条骨链算出来，天然一致）。
    """
    name = clip if clip in M.CLIPS else "idle"
    rest = R.joints({}, origin=(0.0, 0.0))

    def angle(p, c):
        return math.atan2(c[1] - p[1], c[0] - p[0])

    def glob_delta(pos):
        """每根骨头**世界方向**相对静止的变化量。"""
        out = {}
        for j, meta in R.JOINTS.items():
            p = meta["parent"]
            if not p:
                continue
            out[j] = _wrap180(math.degrees(angle(pos[p], pos[j])
                                           - angle(rest[p], rest[j])))
        return out

    per_frame = []
    for f in range(max(2, int(frames))):
        t = f / float(max(2, int(frames)))
        g = glob_delta(R.joints(R.pose(name, t), origin=(0.0, 0.0)))
        rots = {}
        for j, meta in R.JOINTS.items():
            p = meta["parent"]
            if not p:
                continue
            # ★必须减去父骨的旋转：Blender 里 pose 是**相对父骨**的，
            #   直接灌世界增量会让整条骨链叠加（头/手会叠到一起，踩过）
            rots[j] = _wrap180(g[j] - (g.get(p, 0.0) if p != "hip" else 0.0))
        per_frame.append(rots)
    bones = []
    for j, meta in R.JOINTS.items():
        x, y = rest[j]
        pj = meta["parent"]
        px, py = rest[pj] if pj else (x, y)
        bones.append({"name": j, "parent": pj or "",
                      "len": meta["len"] * SCALE,
                      "x": x * SCALE, "z": -y * SCALE,
                      "px": px * SCALE, "pz": -py * SCALE})
    return {"clip": name, "bones": bones, "frames": per_frame, "palette": PALETTE,
            "mat": MATS, "scale": SCALE}


def _bpy_script(payload: dict, *, out_png: str, out_glb: str, res=(420, 640)) -> str:
    return _render_script(payload, out_png=out_png, out_glb=out_glb, mode="still", res=res)


def preview(clip: str = "idle", *, angles: int = 8, strips: int = 8,
            frames: int = 24, timeout: float = 900.0) -> dict:
    """渲染 3D 预览：转台（N 角度）+ 动作序列（N 帧）。一次 Blender 跑完，零客户端依赖。"""
    from core import hooks as H
    exe = blender_exe()
    g = H.Guard("GBT小土豆V9·3D 预览渲染", must_steps=("环境核验", "脚本生成",
                                                      "blender 运行", "产物核验"))
    with g.step("环境核验", expect="本机 blender 可执行存在") as s:
        if not exe:
            raise H.HookError("本机找不到 blender.exe")
        s.evidence(blender=exe, clip=clip, fingerprint=H.fingerprint(exe, clip))
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    name = clip if clip in M.CLIPS else "idle"
    py = PREVIEW_DIR / f"_preview_{name}.py"
    with g.step("脚本生成", expect="一次跑完转台 + 动作序列的脚本") as s:
        payload = _rest_and_frames(name, frames)
        py.write_text(_render_script(payload, mode="preview", out_dir=str(PREVIEW_DIR),
                                     angles=angles, strips=strips, res=(360, 540)),
                      encoding="utf-8")
        s.evidence(帧数=len(payload["frames"]), 角度=angles, 序列=strips,
                   fingerprint=H.fingerprint(len(payload["frames"]), angles, strips, name))
    with g.step("blender 运行", expect="blender 打印 OK") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        if "V9_BLENDER_OK" not in (r.stdout or ""):
            raise H.HookError(f"预览渲染失败：rc={r.returncode} {(r.stderr or (r.stdout or ''))[-300:]}")
        s.evidence(rc=r.returncode, fingerprint=H.fingerprint(r.returncode))
    with g.step("产物核验", expect="转台与序列图都非空") as s:
        turntable = sorted(PREVIEW_DIR.glob(f"{name}_a*.png"))
        strip = sorted(PREVIEW_DIR.glob(f"{name}_f*.png"))
        if len(turntable) < angles or len(strip) < strips:
            raise H.HookError(f"图不全：转台 {len(turntable)}/{angles}，序列 {len(strip)}/{strips}")
        if min(x.stat().st_size for x in turntable + strip) < 800:
            raise H.HookError("有图过小，渲染可能没出内容")
        s.evidence(转台=len(turntable), 序列=len(strip),
                   fingerprint=H.fingerprint(len(turntable), len(strip)))
    a = g.finish()
    out = {"ok": True, "动作": name, "转台": [x.name for x in turntable],
           "序列": [x.name for x in strip], "目录": str(PREVIEW_DIR),
           "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "Blender 离线渲染的转台与动作序列；页面拖拽/播放即看 3D，零客户端依赖"}
    try:
        from core import solidify as S
        S.solidify("blender_preview", {"动作": name, "转台": len(turntable), "序列": len(strip)},
                   note="3D 预览（转台 + 动作序列，离线渲染）")
    except Exception as e:
        _swallow(__file__, e)
    return out


STUDIO_DIR = ROOT / "state" / "blender" / "studio"
GLB_VIEW_TEMPLATE = ROOT / "templates" / "blender_glb_view.tmpl"
# 验收常用视角：正面 / 四分之三 / 左 / 右 / 背面
GLB_VIEWS = (("front", 0.0, 6.0), ("hero", 30.0, 8.0), ("left", 90.0, 6.0),
             ("right", -90.0, 6.0), ("back", 180.0, 6.0))


def view_glb(glb, *, views=None, res=(760, 1140), out_dir=None,
             timeout: float = 1800.0) -> dict:
    """把外部 GLB 导进 Blender 出多角度验收图（零下载 / 零联网）。

    为什么需要它：云端生成的模型只有"打开看"才知道对不对——耳朵/尾巴这类
    特征有没有丢掉，必须**渲出来亲眼看**，不能只看面数。
    """
    from core import hooks as H
    exe = blender_exe()
    src = Path(glb).resolve()          # 必须是绝对路径：Blender 的 CWD 不是本项目根
    od = Path(out_dir).resolve() if out_dir else STUDIO_DIR
    vs = [(n, float(a), float(e)) for n, a, e in (views or GLB_VIEWS)]
    g = H.Guard("GBT小土豆V9·GLB 多角度出图", must_steps=("环境核验", "脚本生成",
                                                          "blender 运行", "产物核验"))
    with g.step("环境核验", expect="blender 在、GLB 在且非空") as s:
        if not exe:
            raise H.HookError("本机找不到 blender.exe（不猜路径）")
        if not src.is_file() or src.stat().st_size < 1024:
            raise H.HookError(f"GLB 不存在或过小：{src}")
        s.evidence(blender=exe, glb=src.name, 字节=src.stat().st_size,
                   fingerprint=H.fingerprint(exe, src.stat().st_size))
    od.mkdir(parents=True, exist_ok=True)
    py = od / f"_view_{src.stem}.py"
    with g.step("脚本生成", expect="按占位符渲染出自包含 bpy 脚本") as s:
        if not GLB_VIEW_TEMPLATE.is_file():
            raise H.HookError("缺模板 templates/blender_glb_view.tmpl")
        code = (GLB_VIEW_TEMPLATE.read_text(encoding="utf-8")
                .replace("__GLB__", str(src))
                .replace("__OUT_DIR__", str(od))
                .replace("__W__", str(res[0]))
                .replace("__H__", str(res[1]))
                .replace("__VIEWS__", json.dumps([[n, a, e] for n, a, e in vs])))
        py.write_text(code, encoding="utf-8")
        s.evidence(视角=[n for n, _, _ in vs], 分辨率=f"{res[0]}x{res[1]}",
                   fingerprint=H.fingerprint(len(vs), src.name, res))
    with g.step("blender 运行", expect="blender 打印 DONE") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        if "DONE" not in (r.stdout or ""):
            raise H.HookError(f"出图失败：rc={r.returncode} "
                              f"{(r.stderr or (r.stdout or ''))[-400:]}")
        s.evidence(rc=r.returncode, fingerprint=H.fingerprint(r.returncode, src.stat().st_size))
    with g.step("产物核验", expect="每张图都存在且不是空图") as s:
        shots = [od / f"{n}.png" for n, _, _ in vs]
        bad = [p.name for p in shots if not p.is_file() or p.stat().st_size < 5000]
        if bad:
            raise H.HookError(f"图缺失或过小（可能没渲出内容）：{bad}")
        s.evidence(张数=len(shots), 最小字节=min(p.stat().st_size for p in shots),
                   fingerprint=H.fingerprint(len(shots), sum(p.stat().st_size for p in shots)))
    a = g.finish()
    out = {"ok": True, "glb": str(src), "目录": str(od),
           "图": [{"视角": n, "路径": str(od / f"{n}.png")} for n, _, _ in vs],
           "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "本地 Blender EEVEE 离线渲染；用来看清模型到底长什么样"}
    try:
        from core import solidify as S
        S.solidify("blender_glb_view", {"glb": src.name, "张数": len(shots)},
                   note="外部 GLB 多角度验收图")
    except Exception as e:
        _swallow(__file__, e)
    return out


MIXAMO_TEMPLATE = ROOT / "templates" / "blender_mixamo_anim.tmpl"
MIXAMO_CLIPS = ("idle", "wave", "bow", "turn", "salute", "armsx", "think", "nod", "shake", "shy", "cheer")


STILLS_TEMPLATE = ROOT / "templates" / "blender_avatar_stills.tmpl"


def avatar_stills(fbx, label: str = "stylized4", *, res=(640, 960),
                  out_dir=None, timeout: float = 2400.0, retire_old: bool = True) -> dict:
    """给数字人重渲"卡片三件套"：<label>_turn00..07.png + _hero + _face（透明背景）。

    旧的同标签文件（上一代模型的渲染）会被送回收站 —— 新件落地就清旧件（主人铁律）。
    """
    from core import hooks as H
    exe = blender_exe()
    src = Path(fbx).resolve()
    od = Path(out_dir).resolve() if out_dir else (ROOT / "state" / "tripo" / "render")
    label = str(label or "stylized4")
    g = H.Guard("GBT小土豆V9·数字人卡片三件套", must_steps=("环境核验", "脚本生成",
                                                            "blender 运行", "产物核验"))
    with g.step("环境核验", expect="blender 在、FBX 在") as s:
        if not exe:
            raise H.HookError("本机找不到 blender.exe")
        if not src.is_file():
            raise H.HookError(f"找不到模型：{src}")
        s.evidence(blender=exe, fbx=src.name, 标签=label,
                   fingerprint=H.fingerprint(exe, src.stat().st_size, label))
    od.mkdir(parents=True, exist_ok=True)
    old = sorted(od.glob(f"{label}_*.png"))
    py = od / f"_stills_{label}.py"
    with g.step("脚本生成", expect="按占位符渲染 bpy 脚本") as s:
        code = (STILLS_TEMPLATE.read_text(encoding="utf-8")
                .replace("__FBX__", str(src)).replace("__OUT__", str(od))
                .replace("__LABEL__", label).replace("__W__", str(res[0]))
                .replace("__H__", str(res[1])))
        py.write_text(code, encoding="utf-8")
        s.evidence(标签=label, 分辨率=f"{res[0]}x{res[1]}", 旧件=len(old),
                   fingerprint=H.fingerprint(label, res, len(old)))
    with g.step("blender 运行", expect="blender 打印 V9_STILLS_OK") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        out = r.stdout or ""
        if "V9_STILLS_OK" not in out:
            raise H.HookError(f"出图失败：rc={r.returncode} {(r.stderr or out)[-400:]}")
        s.evidence(rc=r.returncode, fingerprint=H.fingerprint(r.returncode, label))
    with g.step("产物核验", expect="转台 8 帧 + 主图 + 特写都在且非空") as s:
        want = [od / f"{label}_turn{i:02d}.png" for i in range(8)] +                [od / f"{label}_hero.png", od / f"{label}_face.png"]
        bad = [p.name for p in want if not p.is_file() or p.stat().st_size < 8000]
        if bad:
            raise H.HookError(f"图缺失或过小：{bad}")
        s.evidence(张数=len(want), 最小字节=min(p.stat().st_size for p in want),
                   fingerprint=H.fingerprint(len(want), label))
    a = g.finish()
    retired = 0
    if retire_old and old:
        try:                                   # 新一套已就位 → 旧的同标签图送回收站
            from core import retire as R
            keep = {p.name for p in want}
            olds = [p for p in old if p.name not in keep]
            if olds:
                R._recycle([str(p) for p in olds], note=f"{label} 新一套已就位")
                retired = len(olds)
        except Exception as e:
            _swallow(__file__, e)
    return {"ok": True, "标签": label, "目录": str(od), "张数": len(want),
            "旧件已退役": retired, "钩子": {"通过": a["通过"], "步数": a["步数"]},
            "口径": "透明背景定妆图（转台 8 帧 + 主图 + 脸部特写带景深）"}


def _frames_to_alpha_webm(seq_dir: Path, out: Path, *, fps: int) -> dict:
    """PNG 序列（RGBA）→ 带 Alpha 的 VP9 WebM（页面里只显示数字人，不要背景）。

    Blender 直接输出 WebM 不带 alpha；透明必须 yuva420p，Chromium 认 VP9 的 alpha ✓。
    """
    import shutil as _sh
    frames = sorted(seq_dir.glob("f_*.png"))
    if len(frames) < 4:
        return {"ok": False, "reason": f"帧数不足：{len(frames)}"}
    ff = _sh.which("ffmpeg")
    if not ff:
        return {"ok": False, "reason": "缺 ffmpeg"}
    cmd = [ff, "-y", "-loglevel", "error", "-framerate", str(int(fps)),
           "-i", str(seq_dir / "f_%04d.png"),
           "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
           "-b:v", "0", "-crf", "32", "-auto-alt-ref", "0", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, shell=False)
    ok = r.returncode == 0 and out.is_file() and out.stat().st_size > 20000
    return {"ok": ok, "rc": r.returncode,
            "字节": out.stat().st_size if out.is_file() else 0,
            "原因": "" if ok else (r.stderr or "")[-200:]}


def render_mixamo(fbx, clip: str = "idle", *, seconds: float = 4.0, fps: int = 24,
                  res=(512, 768), out_dir=None, still: bool = False, alpha: bool = False,
                  timeout: float = 2400.0) -> dict:
    """驱动 Mixamo 骨骼渲染循环 WebM（数字人页面用）。需要已绑骨的 FBX/GLB。

    still=True 只渲"保持姿态"那一帧 PNG —— 用来快速核对姿势对不对，别整段白渲。
    """
    from core import hooks as H
    exe = blender_exe()
    src = Path(fbx).resolve()
    od = Path(out_dir).resolve() if out_dir else (ROOT / "state" / "blender" / "mixamo")
    name = clip if clip in MIXAMO_CLIPS else "idle"
    g = H.Guard("GBT小土豆V9·Mixamo 骨骼动画渲染",
                must_steps=("环境核验", "脚本生成", "blender 运行", "产物核验"))
    with g.step("环境核验", expect="blender 在、FBX 在") as s:
        if not exe:
            raise H.HookError("本机找不到 blender.exe")
        if not src.is_file():
            raise H.HookError(f"找不到绑骨模型：{src}")
        s.evidence(blender=exe, fbx=src.name, 字节=src.stat().st_size,
                   fingerprint=H.fingerprint(exe, src.stat().st_size))
    od.mkdir(parents=True, exist_ok=True)
    py = od / f"_anim_{name}{'_pose' if still else ''}.py"
    with g.step("脚本生成", expect="按占位符渲染 bpy 脚本") as s:
        code = (MIXAMO_TEMPLATE.read_text(encoding="utf-8")
                .replace("__FBX__", str(src)).replace("__OUT_DIR__", str(od))
                .replace("__CLIP__", name).replace("__SECONDS__", repr(float(seconds)))
                .replace("__FPS__", str(int(fps))).replace("__W__", str(res[0]))
                .replace("__H__", str(res[1]))
                .replace("__STILL__", "True" if still else "False")
                .replace("__ALPHA__", "True" if alpha else "False"))
        py.write_text(code, encoding="utf-8")
        s.evidence(动作=name, 秒数=seconds, 帧率=fps, 只出姿态图=still, 透明背景=alpha,
                   fingerprint=H.fingerprint(name, seconds, fps, still, alpha))
    with g.step("blender 运行", expect="blender 打印 V9_MIXAMO_OK") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        out = (r.stdout or "")
        if "V9_MIXAMO_OK" not in out:
            raise H.HookError(f"渲染失败：rc={r.returncode} {(r.stderr or out)[-400:]}")
        bones = ""
        for line in out.splitlines():
            if line.startswith("BONES"):
                bones = line.strip()
        s.evidence(rc=r.returncode, 骨骼=bones[:80], fingerprint=H.fingerprint(r.returncode, bones[:40]))
    with g.step("产物核验", expect="WebM/PNG 非空（透明模式还要合成出带 Alpha 的 WebM）") as s:
        if alpha and not still:
            seq = od / f"frames_{name}"
            webm = od / f"{name}.webm"
            conv = _frames_to_alpha_webm(seq, webm, fps=fps)
            if not conv.get("ok"):
                raise H.HookError(f"透明合成失败：{conv.get('原因')}")
            art = webm
            # 新件（webm）已落地 → 顺手把中间帧送回收站（主人铁律：新件落地就清旧件）
            try:
                from core import retire as _R
                _R._recycle([str(seq)], note=f"{name}.webm 已合成，中间帧退役")
            except Exception as e:
                _swallow(__file__, e)
        else:
            art = od / (f"{name}_pose.png" if still else f"{name}.webm")
        if not art.is_file() or art.stat().st_size < 20000:
            raise H.HookError(f"产物缺失或过小：{art}")
        s.evidence(路径=str(art), 字节=art.stat().st_size, 透明=alpha,
                   fingerprint=H.fingerprint(name, art.stat().st_size, alpha))
    a = g.finish()
    out = {"ok": True, "动作": name, "只出姿态图": still, "透明背景": alpha,
           "文件": str(od / (f"{name}_pose.png" if still else f"{name}.webm")),
           "目录": str(od), "骨骼": bones, "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "本地 Blender EEVEE 驱动 Mixamo 骨骼渲染" + ("（透明背景）" if alpha else "")}
    if not still:
        out["webm"] = out["文件"]
    try:
        from core import solidify as S
        S.solidify("blender_mixamo", {"动作": name, "只出姿态图": still,
                                      "字节": Path(out["文件"]).stat().st_size},
                   note="Mixamo 骨骼动画渲染")
    except Exception as e:
        _swallow(__file__, e)
    return out


def frame_path(name: str):
    """预览图的安全取用：只允许 preview 目录下的 <clip>_[af]NN.png，防目录穿越。"""
    import re
    n = str(name or "")
    if not re.fullmatch(r"[a-z_]+_[af]\d{2}\.png", n):
        return None
    p = (PREVIEW_DIR / n).resolve()
    try:
        p.relative_to(PREVIEW_DIR.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def build(clip: str = "idle", *, frames: int = 24, render: bool = True,
          timeout: float = 600.0) -> dict:
    """跑一次 3D 构建：骨架 → 网格 → K 帧 → 渲染 → 导出 glTF。过钩子防偷懒。"""
    from core import hooks as H
    exe = blender_exe()
    g = H.Guard("GBT小土豆V9·blender 3D 构建", must_steps=("环境核验", "脚本生成",
                                                          "blender 运行", "产物核验"))
    with g.step("环境核验", expect="本机 blender 可执行存在") as s:
        if not exe:
            raise H.HookError("本机找不到 blender.exe（不猜路径）")
        s.evidence(blender=exe, clip=clip, fingerprint=H.fingerprint(exe, clip))
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"{clip if clip in M.CLIPS else 'idle'}_{time.strftime('%H%M%S')}"
    png, glb = OUT / f"{name}.png", OUT / f"{name}.glb"
    py = OUT / f"_build_{name}.py"
    with g.step("脚本生成", expect="自包含 bpy 脚本 + 逐帧关节数据") as s:
        payload = _rest_and_frames(clip, frames)
        script = _bpy_script(payload, out_png=str(png), out_glb=str(glb))
        py.write_text(script, encoding="utf-8")
        s.evidence(骨数=len(payload["bones"]), 帧数=len(payload["frames"]),
                   fingerprint=H.fingerprint(len(payload["bones"]), len(payload["frames"])))
    with g.step("blender 运行", expect="blender 后台真的跑完并打印 OK") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio",
                            "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        tail = (r.stdout or "")[-400:]
        if "V9_BLENDER_OK" not in (r.stdout or ""):
            raise H.HookError(f"blender 未成功：rc={r.returncode} {(r.stderr or tail)[-300:]}")
        s.evidence(rc=r.returncode, ok=True, fingerprint=H.fingerprint(r.returncode))
    with g.step("产物核验", expect="PNG 与 GLB 都真的落盘且非空") as s:
        if not png.is_file() or png.stat().st_size < 2000:
            raise H.HookError("渲染图没出来（或过小）")
        if not glb.is_file() or glb.stat().st_size < 2000:
            raise H.HookError("glb 没导出（或过小）")
        s.evidence(png=png.stat().st_size, glb=glb.stat().st_size,
                   fingerprint=H.fingerprint(png.stat().st_size, glb.stat().st_size))
    a = g.finish()
    out = {"ok": True, "动作": payload["clip"], "帧数": len(payload["frames"]),
           "渲染": str(png), "模型": str(glb), "blender": exe,
           "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "骨架/动作来自我们已验证的 FK 与动作库；零下载零联网"}
    try:
        from core import solidify as S
        S.solidify("blender_pipeline", {"动作": out["动作"], "帧数": out["帧数"],
                                        "渲染": png.name, "模型": glb.name},
                   note="blender 原生能力位：bpy 骨架 + 动作 K 帧 + EEVEE 渲染 + glTF")
    except Exception as e:
        _swallow(__file__, e)
    return out


def deploy() -> dict:
    """能力位自检 + 固化（status 只读，不落盘）。"""
    st = status()
    if not st["可用"]:
        return {"ok": False, "reason": "本机没有 blender", "状态": st}
    return {"ok": True, "状态": st}


__all__ = ["status", "build", "preview", "deploy", "blender_exe", "OUT",
           "PREVIEW_DIR", "frame_path"]
