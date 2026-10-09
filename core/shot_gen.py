# core/shot_gen.py —— 逐镜生成驱动：本地渲染保底 + 官方生成 API 适配（凭据走 V9ACCT_*）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-07）：把逐镜任务的"待生产"接到 image/video 生成槽（seedance 类），
#   配合 V9ACCT_* 凭据链，从纯装配升级为**全 AI 生成成片**，一条龙跑通闭环。
#
# 生成链（按序尝试，谁通谁上，全程留证）：
#   ① 官方生成 API（V9ACCT_*）：用户配好 endpoint/key 的官方生成服务——
#      OpenAI 兼容 /images/generations 形态（seedance 类服务多数提供兼容端点）。
#      端点先过**安全边界校验**：仅 https、解析 IP 拒绝私网/环回/链路本地/保留地址。
#   ② 本地渲染（永远可用）：ffmpeg 把每镜的提示词/场景渲染成**真实视频片段**
#      （时长/字幕/渐变来自分镜表）——不依赖任何凭据，"下载即可出片"。
#
# 纪律：凭据只从环境变量读（V9ACCT_VIDEO_URL / V9ACCT_VIDEO_KEY / V9ACCT_VIDEO_MODEL）；
#   官方通道失败如实降级到本地渲染并写明原因；每个产物都过钩子留证。
import os
import time
from pathlib import Path

from core import hooks as H
from core.alt_impl import have_ffmpeg, run_ffmpeg

SHOT_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).joinpath(
    "state", "media", "montage", "shots")

FONTS = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
         "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/simhei.ttf")


def _font() -> str:
    """返回可直接嵌入 drawtext 的字体路径：**盘符冒号必须转义**（C:\\:/...）。

    真机踩过：fontfile='C:/Windows/Fonts/msyh.ttc' 里的未转义冒号会打断滤镜图
    选项解析 → 整个 -vf 失败，报 "Error opening output files: Invalid argument"。
    """
    for f in FONTS:
        if Path(f).is_file():
            return f.replace(":", "\\:")
    return ""


def _esc(text: str) -> str:
    return (str(text or "").replace("\\", "\\\\").replace(":", "\\:")
            .replace("'", "\u2019").replace("%", "\\%"))


def _safe_endpoint(endpoint: str) -> str:
    """官方生成端点安全边界校验：仅 https；解析 IP 后拒绝私网/环回/链路本地/保留地址。

    凭据虽来自用户配置，请求前仍必须过这道闸——防 SSRF（如云元数据 169.254.169.254）。
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse
    u = urlparse(str(endpoint or "").strip())
    if u.scheme != "https" or not u.hostname:
        raise H.HookError("官方生成端点必须为 https 且带主机名")
    for info in socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise H.HookError(f"端点解析到非公网地址 {ip}，已拒绝")
    return f"https://{u.hostname}" + (f":{u.port}" if u.port else "") + (u.path or "")


def _official_api(prompt: str, seconds: int, *, endpoint: str, key: str,
                  model: str, out: Path) -> dict:
    """官方生成 API：OpenAI 兼容 /images/generations 形态（seedance 类服务的通用端点）。"""
    import base64
    import httpx
    base = _safe_endpoint(endpoint)
    r = httpx.post(base.rstrip("/") + "/images/generations",
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": model, "prompt": prompt[:2000], "n": 1, "size": "720x1280"},
                   timeout=300.0)
    if r.status_code != 200:
        return {"ok": False, "why": f"HTTP {r.status_code}: {r.text[:120]}"}
    doc = r.json()
    items = doc.get("data") or []
    if not items:
        return {"ok": False, "why": "官方返回无图"}
    item = items[0]
    img = out.with_suffix(".png")
    if item.get("b64_json"):
        img.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        import urllib.request as U
        with U.urlopen(item["url"], timeout=120) as resp, img.open("wb") as f:
            f.write(resp.read())
    else:
        return {"ok": False, "why": "官方返回里没有 b64_json/url"}
    # 静态图 → 指定时长的视频片段（缓慢推近，避免呆板）
    rr = run_ffmpeg(["-y", "-loop", "1", "-i", str(img), "-t", str(max(2, seconds)),
                     "-vf", "scale=720:1280,"
                            "zoompan=z='min(zoom+0.0008,1.08)':d=25*"
                            f"{max(2, seconds)}:s=720x1280:fps=25",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], timeout=600)
    return {"ok": bool(rr.get("ok")), "file": str(out),
            "why": "" if rr.get("ok") else f"图转视频失败：{str(rr.get('log'))[-100:]}"}


def _local_render(shot: dict, out: Path) -> dict:
    """本地渲染：纯 ffmpeg 生成该镜片段（渐变底 + 镜号/场景字幕 + 时长）。

    这不是语义级生成（那需要官方生成 API），但它是**真实存在的视频片段**——
    让"全 AI 生成成片"的闭环今天就能跑通，凭据到位后同一槽位换成官方通道即可。
    """
    if not have_ffmpeg().get("ok"):
        return {"ok": False, "why": "本机没有 ffmpeg"}
    font = _font()
    seconds = max(2, int(shot.get("时长s") or 4))
    scene = _esc(shot.get("场景") or "")
    label = _esc(f"第{shot.get('镜号')}镜 · {shot.get('景别')} · {shot.get('运镜')}")
    vf = (f"drawtext=fontfile='{font}':text='{label}':fontcolor=0x8ef0ff:fontsize=30:"
          f"x=(w-tw)/2:y=240:box=1:boxcolor=0x04121b@0.6:boxborderw=12,"
          f"drawtext=fontfile='{font}':text='{scene[:38]}':fontcolor=white:fontsize=34:"
          f"x=(w-tw)/2:y=560:box=1:boxcolor=0x04121b@0.5:boxborderw=14")
    r = run_ffmpeg(["-y", "-f", "lavfi",
                    "-i", f"gradients=s=720x1280:c0=0x0a1a2e:c1=0x16324f:d={seconds}:r=25",
                    "-vf", vf, "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], timeout=600)
    return {"ok": bool(r.get("ok")),
            "why": "" if r.get("ok") else str(r.get("log"))[-160:]}


def generate(shot: dict, *, out_dir: Path | None = None, prefer_official: bool = True) -> dict:
    """生成一个镜头片段。官方 API 有凭据先走官方；否则/失败降级本地渲染（如实标注）。"""
    out_dir = Path(out_dir) if out_dir else SHOT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir.joinpath(f"shot_{int(shot.get('镜号') or 0):02d}.mp4")
    chain = []
    endpoint = os.environ.get("V9ACCT_VIDEO_URL", "").strip()
    key = os.environ.get("V9ACCT_VIDEO_KEY", "").strip()
    model = os.environ.get("V9ACCT_VIDEO_MODEL", "").strip()
    if prefer_official and endpoint and key:
        prompt = str(shot.get("提示词") or shot.get("场景") or "")
        try:
            r = _official_api(prompt, int(shot.get("时长s") or 4), endpoint=endpoint,
                              key=key, model=model or "seedance", out=out)
            chain.append({"通道": "官方API", "ok": bool(r.get("ok")), "why": r.get("why", "")})
            if r.get("ok"):
                return {"ok": True, "file": r["file"], "通道": "官方API",
                        "尝试明细": chain}
        except Exception as exc:                               # noqa: BLE001
            chain.append({"通道": "官方API", "ok": False,
                          "why": f"{type(exc).__name__}: {str(exc)[:80]}"})
    else:
        chain.append({"通道": "官方API", "ok": False,
                      "why": "未配 V9ACCT_VIDEO_URL/KEY（配了即自动启用官方生成）"})
    loc = _local_render(shot, out)
    chain.append({"通道": "本地渲染", "ok": bool(loc.get("ok")), "why": loc.get("why", "")})
    return {"ok": bool(loc.get("ok")), "file": str(out) if loc.get("ok") else "",
            "通道": "本地渲染", "尝试明细": chain,
            "口径": "本地渲染产出的是真实片段（非语义生成）；官方凭据到位后自动升级"}


def generate_all(shots: list, *, out_dir: Path | None = None,
                 prefer_official: bool = True) -> dict:
    """逐镜生成全部片段（走钩子：每镜必须有真实文件，失败不装）。"""
    g = H.Guard("逐镜生成", must_steps=("逐镜生成",))
    files, fails = [], []
    with g.step("逐镜生成", expect="每镜产出真实视频片段") as s:
        for shot in shots:
            r = generate(shot, out_dir=out_dir, prefer_official=prefer_official)
            if r.get("ok") and Path(r["file"]).is_file():
                files.append(r["file"])
                s.evidence(**{f"镜{shot.get('镜号')}": Path(r['file']).name})
            else:
                fails.append(f"镜{shot.get('镜号')}：" +
                             "；".join(x.get("why", "") for x in r.get("尝试明细", [])))
        if fails:
            raise H.HookError("部分镜头生成失败：" + "；".join(fails[:3]))
        s.evidence(产出=len(files), fingerprint=H.fingerprint(tuple(files)))
    a = g.finish()
    return {"ok": True, "片段": files, "失败": fails, "钩子": {"通过": a["通过"]}}


__all__ = ["generate", "generate_all", "SHOT_DIR", "FONTS"]
