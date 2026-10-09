# core/retire.py —— 旧件退役：新件一落地，旧件顺手进回收站（不许留垃圾把系统搞乱）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）：
#   "以后只要模块/系统能力设计新的，就把旧的顺手拉到回收站；自己制造垃圾把自己弄迷糊；
#    让她养好细节习惯，别重启之后把旧的当新的导致系统混乱。"
#
# 口径：
#   · **进回收站**（可恢复），不是永久删 —— 走 Windows 的 Shell 回收站 ✓；
#   · 退役要有据：写清"谁被谁取代"（replaced_by），并进账本；
#   · 开机自动扫一遍（panel 启动时调用 sweep）→ 重启后不可能把旧件当新件；
#   · 只动**本项目 state/ 下的已知垃圾类**，绝不碰源码、不碰别的项目（V8 一根汗毛都不碰）。
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
FEED = STATE / "retire.jsonl"

# 已知垃圾类：正则式（相对 state/）——只在明确"已被取代/已是中间产物"时清
JUNK_PATTERNS: tuple = (
    ("_asar", "解包//重打包 App 的临时目录"),
    ("_asar_bak", "解包//重打包 App 的临时目录"),
    ("_asar_check", "解包//重打包 App 的临时目录"),
    ("_app_new*", "重打包 App 的暂存目录"),
    ("_pytest_tmp", "测试临时目录"),
    ("blender/mixamo/frames_*", "透明动画的中间帧（同名的 .webm 已合成即是垃圾）"),
    ("tripo/render/_prev_svg", "上一代（SVG 时期）动作视频，已被骨骼动画取代"),
    ("preview/_look*.png", "过程截图（只留最近几张）"),
    ("preview/_see*.png", "过程截图（只留最近几张）"),
    ("preview/_desk*.png", "过程截图（只留最近几张）"),
    ("preview/_app*.png", "过程截图（只留最近几张）"),
    ("preview/_page_alpha.png", "过程截图（只留最近几张）"),
    ("preview/_p", "过程截图/日志（只留最近几张）"),
    ("preview/_m_", "过程截图/日志（只留最近几张）"),
    ("preview/_n", "过程截图/日志（只留最近几张）"),
    ("preview/_k", "过程截图/日志（只留最近几张）"),
    ("preview/_boot_all*.json", "自检中间结果"),
    ("preview/_vcs*.json", "语音状态中间结果"),
    ("preview/_*.log", "过程日志（只留最近 5 份）"),
    # ── 旧模型宇宙：已被当前那套取代（stylized4 定妆图 + bunny 绑骨件 + 11 支混音动画）──
    ("tripo/render/final*", "上一代模型的渲染（已被 stylized4 取代）"),
    ("tripo/render/sharp*", "上一代模型的渲染（已被 stylized4 取代）"),
    ("tripo/render/mv_*", "上一代模型的渲染（已被 stylized4 取代）"),
    ("tripo/render/mv.*", "上一代模型的渲染（已被 stylized4 取代）"),
    ("tripo/render/cy_c_*", "上一代模型的渲染（已被 stylized4 取代）"),
    ("tripo/render/stylized.", "旧标签 stylized（当前是 stylized4）"),
    ("tripo/render/stylized2*", "旧标签 stylized2（当前是 stylized4）"),
    ("tripo/render/stylized3*", "旧标签 stylized3（当前是 stylized4）"),
    ("tripo/render/stylized4ui*", "旧标签 stylized4ui（当前是 stylized4）"),
    ("tripo/render/*_wm_poster.png", "旧版动作海报"),
    ("tripo/render/DELIVERY*.png", "旧交付图"),
    ("tripo/render/FINAL_*.png", "旧交付图"),
    ("tripo/render/FACE_UPGRADE.png", "旧交付图"),
    ("tripo/render/POLISH_COMPARE*.png", "旧打磨对比图"),
    ("tripo/render/UI_PREVIEW.png", "旧界面预览图"),
    ("tripo/render/_anim_*.py", "旧动作生成脚本（现由 core/blender 承担）"),
    ("tripo/out", "上一代流水线产物目录"),
    ("tripo/final", "上一代流水线产物目录"),
    ("tripo/head", "上一代流水线产物目录"),
    ("tripo/sharp3d", "上一代流水线产物目录"),
    ("tripo/studio/mv_model.glb", "上一代模型（多体+缺耳，已被 bunny 绑骨件取代）"),
    ("tripo/studio/girl_rigged.glb", "上一代模型（拼版图生成，已被 bunny 绑骨件取代）"),
    ("blender/studio", "旧模型的多角度验收渲染 + 探查脚本"),
    ("blender/preview", "旧预览图"),
    ("blender/*.glb", "旧 glTF 构建产物（现用 FBX + WebM）"),
    ("_pytest_tmp", "测试临时目录"),
    ("pytest-pipe-tmp", "测试管道临时目录"),
    ("preview/POSES*.png", "姿势探针图（临时看姿势用）"),
    ("preview/MOTION_check.png", "动作校验图"),
    ("preview/ENV_check.png", "包络校验图"),
    ("preview/_check_*.png", "过程校验图"),
    ("preview/_alpha.png", "过程校验图"),
    ("preview/_foot*.png", "过程截图"),
    ("preview/_stills*.log", "过程日志"),
)
KEEP_NEWEST = 4            # 每个前缀保留最近几张，其余进回收站


def _recycle(paths: list, *, note: str = "") -> dict:
    """把一批路径送进**回收站**（可恢复）。用 Windows Shell API，不经 shell 拼接。"""
    ps = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "foreach($p in $env:V9_RETIRE_LIST -split \"`n\"){ "
        "  if([string]::IsNullOrWhiteSpace($p)){continue}; "
        "  try{ "
        "    if(Test-Path -LiteralPath $p -PathType Container){ "
        "      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p,'OnlyErrorDialogs','SendToRecycleBin') } "
        "    elseif(Test-Path -LiteralPath $p){ "
        "      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p,'OnlyErrorDialogs','SendToRecycleBin') } "
        "    Write-Output ('OK ' + $p) } "
        "  catch{ Write-Output ('FAIL ' + $p + ' ' + $_.Exception.Message) } }"
    )
    env = dict(os.environ)
    env["V9_RETIRE_LIST"] = "\n".join(str(p) for p in paths)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=180, shell=False, env=env)
        # 中文路径会让 stdout 按 utf-8 解码失败 → 一律 errors="replace" 再数 OK，
        # 否则"明明清成功却报 0/179"（踩过：计数不可信比不做还坏）。
        out = (r.stdout or b"").decode("utf-8", "replace") if isinstance(r.stdout, bytes) \
            else (r.stdout or "")
        lines = [x for x in out.splitlines() if x.strip()]
        ok = sum(1 for x in lines if x.startswith("OK "))
        return {"ok": ok == len(paths), "成功": ok, "总数": len(paths),
                "失败": [x for x in lines if x.startswith("FAIL ")][:5], "备注": note}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "原因": type(exc).__name__, "总数": len(paths)}


def _log(rec: dict) -> None:
    try:
        FEED.parent.mkdir(parents=True, exist_ok=True)
        with FEED.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        _swallow(__file__, e)



def _is_junk(p: Path, pattern: str) -> bool:
    from fnmatch import fnmatch
    rel = p.relative_to(STATE).as_posix()
    return fnmatch(rel, pattern) or fnmatch(rel + "/", pattern)


def candidates() -> list:
    """列出"该退役"的东西（真扫盘，不猜）。中间帧只有当同名 webm 已存在才算垃圾。"""
    out = []
    for pattern, why in JUNK_PATTERNS:
        for p in STATE.glob(pattern):
            if pattern.startswith("blender/mixamo/frames_"):
                webm = p.parent / (p.name.replace("frames_", "") + ".webm")
                if not (webm.is_file() and webm.stat().st_size > 20000):
                    continue                       # webm 还没合成好 → 帧还有用，先留着
            out.append({"路径": str(p), "相对": p.relative_to(STATE).as_posix(),
                        "为什么": why, "是目录": p.is_dir(),
                        "大小MB": (round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                                        / 1048576, 1) if p.is_dir() else
                                   round(p.stat().st_size / 1048576, 3)),
                        "改于": time.strftime("%Y-%m-%dT%H:%M", time.localtime(p.stat().st_mtime))})
    # 同类里保留最近 KEEP_NEWEST 个 —— **只对"过程截图/日志"这类证据文件**这么做；
    # 结构性垃圾（临时目录 / 已合成动画的中间帧 / 上一代产物）该全清，别被这条吃掉（踩过）。
    from collections import defaultdict
    groups = defaultdict(list)
    for row in out:
        if "只留最近" in row["为什么"]:
            head = row["相对"].split("_")[0] if "_" in row["相对"] else row["相对"].split("/")[0]
            groups[head].append(row)
        else:
            groups[row["相对"]].append(row)
    keep = []
    for head, rows in groups.items():
        if "只留最近" not in rows[0]["为什么"]:
            keep += rows                      # 结构性垃圾：全退役
            continue
        rows.sort(key=lambda r: r["改于"], reverse=True)
        keep += rows[KEEP_NEWEST:]
    return sorted(keep, key=lambda r: r["相对"])


def sweep(*, dry_run: bool = False, note: str = "") -> dict:  # 主人令：默认真动手（要演练请显式传 True）
    """扫一遍已知垃圾类 → 进回收站（dry_run=True 只报告不动手）。"""
    rows = candidates()
    if not rows:
        return {"ok": True, "动作": "sweep", "候选": 0, "总MB": 0.0, "干跑": dry_run}
    paths = [r["路径"] for r in rows]
    res = {"ok": True, "动作": "sweep", "候选": len(rows), "干跑": dry_run,
           "清单": rows[:15], "总MB": round(sum(r["大小MB"] for r in rows), 1)}
    if not dry_run:
        r = _recycle(paths, note=note or "旧件退役（新件已就位）")
        res.update(r)
        res["ok"] = bool(r.get("ok"))
        _log({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "动作": "sweep",
              "件数": len(paths), "成功": r.get("成功"), "总MB": res["总MB"]})
        try:
            from core import deploy_ledger as J
            J.record("modify", "retire:sweep",
                     detail={"件数": len(paths), "总MB": res["总MB"], "成功": r.get("成功")})
        except Exception as e:
            _swallow(__file__, e)
    return res


def supersede(new_path, *, older: list | None = None, note: str = "") -> dict:
    """新件落地时顺手退役旧件（显式给 older，或按同名前缀推断）。"""
    new = Path(new_path).resolve()
    olds = [Path(x).resolve() for x in (older or [])]
    if not olds and new.is_file():
        stem = new.stem
        for sib in new.parent.glob(stem + ".*"):
            if sib.resolve() != new and sib.suffix != new.suffix:
                olds.append(sib.resolve())
    olds = [p for p in olds if p.exists() and p != new]
    if not olds:
        return {"ok": True, "动作": "supersede", "退役": 0, "新件": str(new)}
    r = _recycle([str(p) for p in olds], note=note or f"被 {new.name} 取代")
    _log({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "动作": "supersede",
          "新件": str(new), "退役": len(olds), "成功": r.get("成功")})
    try:
        from core import deploy_ledger as J
        J.record("modify", "retire:supersede",
                 detail={"新件": str(new), "退役": [str(p) for p in olds][:6]},
                 before=", ".join(p.name for p in olds[:4]), after=new.name)
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": bool(r.get("ok")), "动作": "supersede", "退役": len(olds), "新件": str(new),
            "详情": r}


def running_panels() -> list:
    """本机在监听的面板端口（多开就是"旧的还在跑"的典型，重启混乱的根源之一）。"""
    out = []
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-NetTCPConnection -State Listen -EA SilentlyContinue | "
                            "Where-Object { $_.LocalPort -ge 8700 -and $_.LocalPort -le 8800 } | "
                            "Select-Object -ExpandProperty LocalPort"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, shell=False)
        ports = sorted({int(x) for x in (r.stdout or "").split() if x.strip().isdigit()})
    except Exception:                                           # noqa: BLE001
        ports = []
    for p in ports:
        out.append({"端口": p, "探测": f"http://127.0.0.1:{p}/api/health"})
    return out


def status() -> dict:
    rows = candidates()
    return {"规矩": "设计新件时顺手把旧件送回收站（可恢复）；开机自动扫一遍，重启不许把旧的当新的",
            "待退役": len(rows), "待退役总MB": round(sum(r["大小MB"] for r in rows), 1),
            "前几件": rows[:6], "在跑的面板端口": [x["端口"] for x in running_panels()],
            "回收站口径": "走 Windows 回收站，可还原；不是永久删除",
            "记录": str(FEED)}


__all__ = ["candidates", "sweep", "supersede", "running_panels", "status", "JUNK_PATTERNS"]
