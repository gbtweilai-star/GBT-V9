# core/cadgen.py —— 文字/脚本 → CAD（STEP/STL/GLB/3MF）+ 渲染看图 + 可制造性检查
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 来源：主人 2026-10-08 要求接入 text-to-cad（github.com/earthtojake/text-to-cad，
# PyPI 包 cadgen，CAD 内核 OCP/build123d）。接入点做成本项目的能力位：
#   建模脚本（build123d）→ STEP/STL/GLB；再用 cadgen 渲染成图，用眼睛验收。
#
# 纪律：argv 全字面量 + shell=False；产物只落 state/cad/ 下；不出网（首启下载运行时除外，需主人同意）；
#       每一步过钩子，防"没跑就说完成"。
from core.swallow import swallow as _swallow
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAD_DIR = ROOT / "state" / "cad"
PIN = "cadgen==0.7.15"          # 与 skill 的 pin 对齐，避免版本漂移导致产物不可复现
PY_VER = "3.13"


def _exe() -> str:
    """优先用 uv 装好的 cadgen；没有就退回 uvx（skill 的官方调用法）。"""
    p = shutil.which("cadgen")
    if p:
        return p
    uvx = shutil.which("uvx")
    return uvx or ""


def _cadgen_cmd(*args: str) -> list:
    """返回可执行的 cadgen 调用 argv（绝对路径, 无 shell）。"""
    exe = _exe()
    if not exe:
        return []
    if Path(exe).name.lower().startswith("uvx"):
        return [exe, "--no-config", "--managed-python", "--python", PY_VER,
                "--from", PIN, "cadgen", *args]
    return [exe, *args]


def _py_cmd(script: Path) -> list:
    """跑建模脚本：用 cadgen 自带 python（保证 build123d/OCP 一致）。"""
    uvx = shutil.which("uvx")
    if not uvx:
        return []
    return [uvx, "--no-config", "--managed-python", "--python", PY_VER,
            "--from", PIN, "python", str(script)]


def status() -> dict:
    """只读状态：cadgen 在不在、内核能不能加载、skill 装没装。"""
    out = {"cadgen": _exe() or "（未找到）", "可用": bool(_exe()),
           "pin": PIN, "产物目录": str(CAD_DIR)}
    if out["可用"]:
        try:
            r = subprocess.run(_cadgen_cmd("doctor"), capture_output=True, text=True,
                               timeout=180, shell=False)
            txt = (r.stdout or "") + (r.stderr or "")
            out["doctor"] = txt.strip().splitlines()[:6]
            out["内核"] = "OK" if "kernel   OK" in txt or "kernel OK" in txt else "未知"
        except Exception as exc:                                   # noqa: BLE001
            out["doctor"] = f"取不到：{type(exc).__name__}"
            out["内核"] = "未知"
    sk = Path.home() / ".zcode" / "skills" / "cad" / "SKILL.md"
    out["skill"] = str(sk) if sk.is_file() else "（未装）"
    out["能力"] = "建模脚本 → STEP/STL/GLB/3MF；snapshot 渲图验收；DFM/DfAM 检查"
    return out


def build(model_py, *, timeout: float = 1800.0) -> dict:
    """跑一个 build123d 建模脚本，产出 STEP/STL/GLB（脚本里用 @step/@stl/@glb 声明）。

    model_py 必须在本项目 state/cad/ 下（防越界写）。
    """
    from core import hooks as H
    src = Path(model_py).resolve()
    g = H.Guard("GBT小土豆V9·CAD 建模出件", must_steps=("环境核验", "脚本核验", "cadgen 运行", "产物核验"))
    with g.step("环境核验", expect="cadgen 可用") as s:
        if not _exe():
            raise H.HookError("本机没有 cadgen（先 uv tool install cadgen）")
        s.evidence(cadgen=_exe(), pin=PIN, fingerprint=H.fingerprint(_exe()))
    with g.step("脚本核验", expect="脚本在 state/cad/ 下且存在") as s:
        if not src.is_file():
            raise H.HookError(f"找不到建模脚本：{src}")
        try:
            src.relative_to(CAD_DIR.resolve())
        except ValueError:
            raise H.HookError(f"建模脚本必须放在 {CAD_DIR} 下：{src}")
        if "build123d" not in src.read_text(encoding="utf-8", errors="ignore"):
            raise H.HookError("脚本里没看到 build123d —— 不是 CAD 模型脚本？")
        s.evidence(脚本=src.name, 字节=src.stat().st_size,
                   fingerprint=H.fingerprint(src.name, src.stat().st_size))
    before = {p: p.stat().st_mtime for p in src.parent.parent.rglob("*") if p.is_file()}
    with g.step("cadgen 运行", expect="脚本跑完且写出产物") as s:
        cmd = _py_cmd(src)
        if not cmd:
            raise H.HookError("本机没有 uvx（CAD 运行时靠它）")
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           shell=False, cwd=str(src.parent.parent))
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        if r.returncode != 0:
            raise H.HookError(f"建模失败：rc={r.returncode} {out[-400:]}")
        s.evidence(rc=r.returncode, 耗时s=round(time.time() - t0, 1),
                   fingerprint=H.fingerprint(r.returncode, len(out)))
    with g.step("产物核验", expect="这次真写出了 STEP/STL/GLB（不是上一轮的旧文件）") as s:
        after = {p: p.stat().st_mtime for p in src.parent.parent.rglob("*") if p.is_file()}
        new = [p for p, m in after.items() if before.get(p) != m]
        prod = [p for p in new if p.suffix.lower() in (".step", ".stp", ".stl", ".glb", ".3mf")]
        if not prod:
            raise H.HookError("跑完了但没有新的 CAD 产物（可能只 print 没落盘）")
        s.evidence(产物=[p.name for p in prod][:6], 数量=len(prod),
                   fingerprint=H.fingerprint(len(prod), sorted(p.name for p in prod)[:3]))
    a = g.finish()
    res = {"ok": True, "脚本": str(src), "产物": [str(p) for p in prod],
           "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "本地 build123d/OCP 真算出来的实体，不是网格贴图"}
    try:
        from core import solidify as S
        S.solidify("cadgen_build", {"脚本": src.name, "产物": len(prod)}, note="CAD 建模出件")
    except Exception as e:
        _swallow(__file__, e)
    return res


def snapshot(target, *, out=None, timeout: float = 600.0) -> dict:
    """把 STEP/STL/GLB/URDF/DXF 渲成图（验收用：必须能亲眼看到）。"""
    from core import hooks as H
    tgt = Path(target).resolve()
    out_p = Path(out).resolve() if out else (CAD_DIR / "PNG" / (tgt.stem + ".png"))
    g = H.Guard("GBT小土豆V9·CAD 渲图", must_steps=("环境核验", "输入核验", "渲染", "产物核验"))
    with g.step("环境核验", expect="cadgen 可用") as s:
        if not _exe():
            raise H.HookError("本机没有 cadgen")
        s.evidence(cadgen=_exe(), fingerprint=H.fingerprint(_exe()))
    with g.step("输入核验", expect="目标文件存在") as s:
        if not tgt.is_file():
            raise H.HookError(f"找不到目标：{tgt}")
        s.evidence(目标=tgt.name, 字节=tgt.stat().st_size, fingerprint=H.fingerprint(tgt.name))
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with g.step("渲染", expect="cadgen snapshot 写出 PNG") as s:
        r = subprocess.run(_cadgen_cmd("snapshot", str(tgt), str(out_p)),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False,
                           cwd=str(CAD_DIR))
        txt = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 or not out_p.is_file():
            raise H.HookError(f"渲染失败：rc={r.returncode} {txt[-300:]}")
        s.evidence(rc=r.returncode, fingerprint=H.fingerprint(r.returncode))
    with g.step("产物核验", expect="PNG 非空") as s:
        if out_p.stat().st_size < 5000:
            raise H.HookError("渲出的图过小，可能没内容")
        s.evidence(图=str(out_p), 字节=out_p.stat().st_size,
                   fingerprint=H.fingerprint(out_p.name, out_p.stat().st_size))
    a = g.finish()
    return {"ok": True, "图": str(out_p), "钩子": {"通过": a["通过"], "步数": a["步数"]}}


def convert(step_file, fmt: str = "stl", *, timeout: float = 900.0) -> dict:
    """把已有 STEP 转成网格（stl/glb/3mf）——一次性导出，不改模型源码。"""
    fmt = str(fmt).lower()
    if fmt not in ("stl", "glb", "3mf"):
        return {"ok": False, "原因": f"不支持的格式：{fmt}"}
    src = Path(step_file).resolve()
    if not src.is_file():
        return {"ok": False, "原因": f"找不到 STEP：{src}"}
    out = src.with_suffix("." + fmt)
    r = subprocess.run(_cadgen_cmd(fmt, "build", str(src), str(out)),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False,
                       cwd=str(src.parent))
    ok = r.returncode == 0 and out.is_file()
    return {"ok": ok, "出": str(out) if ok else "", "rc": r.returncode,
            "尾巴": ((r.stdout or "") + (r.stderr or ""))[-200:]}


def list_parts() -> list:
    """列出 state/cad/ 下已有的 CAD 产物（给页面/清单用）。"""
    if not CAD_DIR.is_dir():
        return []
    out = []
    for p in sorted(CAD_DIR.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".step", ".stp", ".stl", ".glb", ".3mf"):
            out.append({"名": p.name, "相对": str(p.relative_to(CAD_DIR)),
                        "大小KB": round(p.stat().st_size / 1024)})
    return out


if __name__ == "__main__":                                         # 冒烟：打印状态
    print(json.dumps(status(), ensure_ascii=False, indent=1))
