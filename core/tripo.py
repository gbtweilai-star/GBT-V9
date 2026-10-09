# core/tripo.py —— 云上图生3D 能力位：图片 → 3D 模型（贴图/绑骨）→ 本地影棚渲染
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要这一层（主人 2026-10-07："我要一摸一样的，本地跑不动就按照云插件部署"）：
#   本机 torch 是 CPU 版、显卡是 AMD RX 6500M 4GB（无 CUDA），TripoSR/SF3D/Hunyuan3D 这类
#   图生3D 在本地跑不动；Tripo3D 的云 OpenAPI 正是干这个的（图→带 PBR 贴图的网格，还能自动绑骨）。
#   于是把"云上生成 + 本地渲染验收"接成一条链：本模块负责云，core/blender.py 负责本地出图。
#
# 凭据纪律：只从 ~/.tripo/config.json 的 profile 或 TRIPO_API_KEY 环境变量读取；源码/测试不写密钥。
#   注意：环境变量里那个 tcli_ 开头的是**客户端 ID**（会被服务端 401 拒），必须优先用 profile 里的 tsk_。
from core.swallow import swallow as _swallow
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "state" / "tripo"
CFG = Path.home() / ".tripo" / "config.json"
CLI = shutil.which("tripo") or ""
# 两个区域域名：ov=国际(api.tripo3d.ai) / cn=国内(api.tripo3d.com)
#   ★真机踩过：网页账号在 tripo3d.com（国内站）充值，但 CLI 钥匙是国际站 → 两边额度不通用。
#   这里按 profile 里的 region 选域名，并在 401 时自动改试另一个域名。
DOMAINS = {"ov": ("https://api.tripo3d.ai", "api.tripo3d.ai"),
           "cn": ("https://api.tripo3d.com", "api.tripo3d.com")}
API, ALLOW_HOST = DOMAINS["ov"]


def regions() -> list:
    """按 profile 的 region 优先，另一个兜底。"""
    d = _config()
    reg = str(((d.get("profiles") or {}).get(d.get("active_profile") or "default") or {})
              .get("region") or "ov").lower()
    order = [reg] if reg in DOMAINS else []
    for k in DOMAINS:
        if k not in order:
            order.append(k)
    return order


def _config() -> dict:
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}
TEMPLATE = ROOT / "templates" / "blender_asset.tmpl"
HEAD_TEMPLATE = ROOT / "templates" / "blender_headgraft.tmpl"
POLISH_TEMPLATE = ROOT / "templates" / "blender_polish.tmpl"
ANIM_TEMPLATE = ROOT / "templates" / "blender_anim.tmpl"
CYCLES_TEMPLATE = ROOT / "templates" / "blender_cycles.tmpl"


def _profile_key() -> str:
    """本地 profile 里的服务密钥（tsk_ 开头）。读文件，不落日志、不回显。"""
    try:
        d = json.loads(CFG.read_text(encoding="utf-8"))
        prof = (d.get("profiles") or {})
        act = d.get("active_profile") or "default"
        key = (prof.get(act) or {}).get("api_key", "")
        if isinstance(key, str) and key.startswith("tsk_"):
            return key
        for v in prof.values():
            k = (v or {}).get("api_key", "")
            if isinstance(k, str) and k.startswith("tsk_"):
                return k
    except Exception as e:
        _swallow(__file__, e)
    env = os.environ.get("TRIPO_API_KEY", "")
    return env if env.startswith("tsk_") else ""


def _clean_env() -> dict:
    """跑 CLI 用的环境：**去掉** TRIPO_API_KEY，免得它拿 tcli_ 客户端 ID 覆盖 profile 里的密钥。"""
    env = dict(os.environ)
    env.pop("TRIPO_API_KEY", None)
    env.pop("TRIPO_PROFILE", None)
    return env


def _safe_url(path: str, region: str = "ov") -> str:
    """构造并校验请求 URL：只允许 https + 白名单域名 + 解析后非私网/环回/链路本地/保留。

    服务端请求必须做边界校验（协议 + 域名白名单 + DNS 解析后判 IP），避免 SSRF。
    """
    import ipaddress
    import socket
    import urllib.parse
    if not re.fullmatch(r"/v2/openapi/[A-Za-z0-9_/\-]*", path):
        raise ValueError(f"不允许的 API 路径：{path}")
    if region not in DOMAINS:
        region = "ov"
    base, allow = DOMAINS[region]
    u = urllib.parse.urlparse(base + path)
    if u.scheme != "https":
        raise ValueError(f"只允许 https：{u.scheme}")
    host = (u.hostname or "").lower()
    if host != allow:
        raise ValueError(f"域名不在白名单：{host}")
    for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f"解析到内网/保留地址：{ip}")
    return base + path


class _NoRedirect:
    """不跟随任何重定向（防止被引到别处）。"""

    def __init__(self, opener):
        self._opener = opener


def _api(path: str, *, timeout: float = 30.0, region: str | None = None) -> dict:
    """只读 API 调用（余额）。密钥运行时读取，不写进源码。

    先按 profile 的 region 试；401/失败自动改试另一个区域域名（真实踩过：钥匙有区域之分）。
    """
    order = [region] if region in DOMAINS else regions()
    last = {"ok": False, "reason": "未尝试"}
    for r in order:
        got = _api_one(path, timeout=timeout, region=r)
        if got.get("ok"):
            got["区域"] = r
            return got
        last = got
        if got.get("http") not in (401, 403, None):
            return got
    return last


def _api_one(path: str, *, timeout: float = 30.0, region: str = "ov") -> dict:
    import urllib.error
    import urllib.request
    key = _profile_key()
    if not key:
        return {"ok": False, "reason": "本机没有可用的 Tripo 密钥（~/.tripo/config.json 里没有 tsk_ 开头的）"}

    class _NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):                  # 一律不跟随重定向
            return None

    try:
        req = urllib.request.Request(_safe_url(path, region),
                                     headers={"Authorization": f"Bearer {key}"})
        opener = urllib.request.build_opener(_NoRedir)
        with opener.open(req, timeout=timeout) as r:            # noqa: S310  已校验协议/域名/IP
            return {"ok": True, **json.loads(r.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "http": exc.code,
                "reason": exc.read()[:200].decode("utf-8", "replace")}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def status() -> dict:
    """只读状态：CLI 在不在、密钥是否可用、余额多少、能力路线。"""
    out = {"CLI": CLI or "（未找到 tripo 命令）", "配置": str(CFG),
           "密钥来源": "profile" if _profile_key() else "无",
           "路线": "云: 图→带贴图网格(可自动绑骨) → 本地: Blender 影棚渲染验收"}
    bal = _api("/v2/openapi/user/balance")
    if bal.get("ok"):
        d = bal.get("data") or {}
        out["可用"] = True
        out["余额"] = d.get("balance")
        out["冻结"] = d.get("frozen")
    else:
        out["可用"] = False
        out["原因"] = bal.get("reason") or bal.get("http")
    return out


def make(image, *, views: list | None = None, then: str = "texture,rig",
         model: str = "tripo-v3.1", timeout: float = 1500.0,
         out_dir: str | Path | None = None) -> dict:
    """图（可多视图）→ 3D：`tripo make <图...> --then texture,rig`。

    views 给 2~4 张不同视角（正/背/侧）就走多视图，几何更准（兔耳/尾巴这类背面特征才出得来）。
    """
    from core import hooks as H
    g = H.Guard("GBT小土豆V9·云上图生3D", must_steps=("凭据核验", "提交任务", "等待产物", "产物核验"))
    with g.step("凭据核验", expect="本机有可用的 tsk_ 密钥且 CLI 在") as s:
        if not CLI:
            raise H.HookError("本机没有 tripo CLI")
        st = status()
        if not st.get("可用"):
            raise H.HookError(f"Tripo 不可用：{st.get('原因')}")
        s.evidence(余额=st.get("余额"), cli=Path(CLI).name,
                   fingerprint=H.fingerprint(st.get("余额")))
    imgs = [Path(x) for x in (views if views else [image])]
    for x in imgs:
        if not x.is_file():
            raise H.HookError(f"找不到输入图：{x}")
    dest = Path(out_dir) if out_dir else (OUT / "out")
    dest.mkdir(parents=True, exist_ok=True)
    with g.step("提交任务", expect="提交 image_to_model（带贴图/绑骨链路）") as s:
        t0 = time.time()
        r = subprocess.run([CLI, "make", *[str(x) for x in imgs], "--model", model,
                            "--then", then,
                            "-o", str(dest), "--json", "--yes", "--no-open",
                            "--timeout", str(int(timeout))],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False,
                           env=_clean_env())
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        s.evidence(rc=r.returncode, 耗时s=round(time.time() - t0, 1),
                   fingerprint=H.fingerprint(r.returncode, len(imgs)))
        if r.returncode != 0:
            raise H.HookError(f"tripo make 失败：rc={r.returncode} {out[-400:]}")
    with g.step("等待产物", expect="命令返回里给出模型文件") as s:
        s.evidence(tail=out[-200:], fingerprint=H.fingerprint(len(out)))
    # 取件：优先用 CLI 的 --json 输出里的 model_file / output_dir（否则会拿到上一次的旧模型，踩过）
    hit = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and "model_file" in line:
            try:
                hit = json.loads(line)
            except Exception as e:
                _swallow(__file__, e)
    prefer = hit.get("output_dir") or ""
    if hit.get("model_file") and Path(hit["model_file"]).is_file():
        prefer = str(Path(hit["model_file"]).parent)
    pool = Path(prefer) if prefer and Path(prefer).is_dir() else dest
    if not any(pool.rglob("*")):                               # 指定目录是空的就退回最新子目录
        subs = [d for d in dest.rglob("*") if d.is_dir()]
        pool = max(subs, key=lambda d: d.stat().st_mtime) if subs else dest
    files = sorted([p for p in pool.rglob("*") if p.suffix.lower() in
                    (".glb", ".gltf", ".obj", ".fbx", ".png", ".webp")],
                   key=lambda p: p.stat().st_size, reverse=True)
    with g.step("产物核验", expect="至少有一个非空模型文件") as s:
        models = [p for p in files if p.suffix.lower() in (".glb", ".gltf", ".obj", ".fbx")]
        if not models or models[0].stat().st_size < 2000:
            raise H.HookError(f"没有拿到模型文件：{out[-300:]}")
        s.evidence(模型=models[0].name, 大小=models[0].stat().st_size,
                   fingerprint=H.fingerprint(models[0].name, models[0].stat().st_size))
    a = g.finish()
    res = {"ok": True, "模型": str(models[0]), "目录": str(dest),
           "文件": [p.name for p in files[:12]], "原始输出": out[-600:],
           "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "几何/贴图来自云上图生3D；本地只做渲染验收"}
    try:
        from core import solidify as S
        S.solidify("tripo_cloud_3d", {"模型": models[0].name,
                                      "来源图": [x.name for x in imgs],
                                      "链路": then, "模型版本": model},
                   note="云上图生3D（Tripo OpenAPI）：图→贴图网格（可绑骨）")
    except Exception as e:
        _swallow(__file__, e)
    return res


def render_asset(glb: str | Path, *, label: str = "asset", angles: int = 8,
                 res=(512, 768), timeout: float = 900.0, front_offset: float = 0.0,
                 out_dir: str | Path | None = None) -> dict:
    """把云上模型拉进 Blender 影棚出图（透视景深 + 三点光 + 地面影 + AgX + 转台）。"""
    from core import blender as B
    from core import hooks as H
    exe = B.blender_exe()
    g = H.Guard("GBT小土豆V9·云模型影棚渲染", must_steps=("环境核验", "脚本生成",
                                                        "blender 运行", "产物核验"))
    with g.step("环境核验", expect="blender 与 glb 都在") as s:
        src = Path(glb)
        if not exe:
            raise H.HookError("本机没有 blender")
        if not src.is_file():
            raise H.HookError(f"找不到模型：{src}")
        s.evidence(glb=src.name, 大小=src.stat().st_size, fingerprint=H.fingerprint(src.name))
    dest = Path(out_dir) if out_dir else (OUT / "render")
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(label))[:40] or "asset"
    py = dest / f"_asset_{safe}.py"
    with g.step("脚本生成", expect="资产渲染脚本（导入 glb + 影棚）") as s:
        tpl = TEMPLATE.read_text(encoding="utf-8")
        src = Path(glb)
        py.write_text(tpl.replace("__GLB__", str(src.resolve()))
                         .replace("__OUT_DIR__", str(dest.resolve()))
                         .replace("__LABEL__", safe)
                         .replace("__W__", str(res[0]))
                         .replace("__H__", str(res[1]))
                         .replace("__ANGLES__", str(int(angles)))
                         .replace("__FRONT_OFFSET__", str(float(front_offset))),
                      encoding="utf-8")
        s.evidence(脚本=py.name, 角度=angles, 正面校准=front_offset,
                   fingerprint=H.fingerprint(safe, angles, front_offset))
    with g.step("blender 运行", expect="blender 打印 V9_ASSET_OK") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        if "V9_ASSET_OK" not in (r.stdout or ""):
            raise H.HookError(f"渲染失败：rc={r.returncode} "
                              f"{(r.stderr or (r.stdout or ''))[-400:]}")
        s.evidence(rc=r.returncode, fingerprint=H.fingerprint(r.returncode))
    with g.step("产物核验", expect="主图与转台图都非空") as s:
        hero = dest / f"{safe}_hero.png"
        turn = sorted(dest.glob(f"{safe}_turn*.png"))
        if not hero.is_file() or hero.stat().st_size < 2000:
            raise H.HookError("主图没出来")
        if len(turn) < angles:
            raise H.HookError(f"转台不齐：{len(turn)}/{angles}")
        s.evidence(主图=hero.stat().st_size, 转台=len(turn),
                   fingerprint=H.fingerprint(hero.stat().st_size, len(turn)))
    a = g.finish()
    return {"ok": True, "主图": str(hero), "转台": [p.name for p in turn],
            "钩子": {"通过": a["通过"], "步数": a["步数"]}}


def pipeline(image, *, views: list | None = None, label: str = "ref",
             then: str = "texture,rig", angles: int = 8) -> dict:
    """一条龙：图（可多视图）→ 云上 3D → 本地影棚渲染（转台）。"""
    made = make(image, views=views, then=then)
    if not made.get("ok"):
        return made
    shots = render_asset(made["模型"], label=label, angles=angles)
    return {"ok": bool(shots.get("ok")), "生成": made, "渲染": shots}




def graft_head(body: str | Path, head: str | Path, *, label: str = "graft",
               angles: int = 8, res=(600, 900), timeout: float = 1800.0,
               out_dir: str | Path | None = None) -> dict:
    """把"精细头部"接到"已绑骨的身体"上（颈部切开 + 对齐 + 挂头骨），再影棚渲染。

    用途：整身模型是从低分辨率正视图重建的，脸偏软；用原图的大头特写单独生成头部再嫁接，
    脸就细了。挂到身体骨架的头骨上，所以原有绑骨/动作不受影响。
    """
    from core import blender as B
    from core import hooks as H
    exe = B.blender_exe()
    g = H.Guard("GBT小土豆V9·精细头部嫁接", must_steps=("环境核验", "脚本生成",
                                                      "blender 运行", "产物核验"))
    with g.step("环境核验", expect="blender、身体模型、头部模型都在") as s:
        bp, hp = Path(body), Path(head)
        if not exe:
            raise H.HookError("本机没有 blender")
        for x in (bp, hp):
            if not x.is_file():
                raise H.HookError(f"找不到模型：{x}")
        s.evidence(身体=bp.name, 头部=hp.name, fingerprint=H.fingerprint(bp.name, hp.name))
    dest = Path(out_dir) if out_dir else (OUT / "render")
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(label))[:40] or "graft"
    py = dest / f"_graft_{safe}.py"
    with g.step("脚本生成", expect="嫁接脚本（切颈 + 对齐 + 挂头骨 + 影棚）") as s:
        tpl = HEAD_TEMPLATE.read_text(encoding="utf-8")
        py.write_text(tpl.replace("__BODY__", str(Path(body).resolve()))
                         .replace("__HEAD__", str(Path(head).resolve()))
                         .replace("__OUT_DIR__", str(dest.resolve()))
                         .replace("__LABEL__", safe)
                         .replace("__W__", str(res[0]))
                         .replace("__H__", str(res[1]))
                         .replace("__ANGLES__", str(int(angles)))
                         .replace("__FRONT_OFFSET__", str(float(front_offset))),
                      encoding="utf-8")
        s.evidence(脚本=py.name, 角度=angles, 正面校准=front_offset,
                   fingerprint=H.fingerprint(safe, angles, front_offset))
    with g.step("blender 运行", expect="blender 打印 V9_HEADGRAFT_OK") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        if "V9_HEADGRAFT_OK" not in (r.stdout or ""):
            raise H.HookError(f"嫁接渲染失败：rc={r.returncode} "
                              f"{(r.stderr or (r.stdout or ''))[-400:]}")
        s.evidence(rc=r.returncode, fingerprint=H.fingerprint(r.returncode))
    with g.step("产物核验", expect="主图/转台/脸部特写都非空") as s:
        hero = dest / f"{safe}_hero.png"
        turn = sorted(dest.glob(f"{safe}_turn*.png"))
        face = dest / f"{safe}_face.png"
        for f in (hero, face):
            if not f.is_file() or f.stat().st_size < 2000:
                raise H.HookError(f"缺图：{f.name}")
        if len(turn) < angles:
            raise H.HookError(f"转台不齐：{len(turn)}/{angles}")
        s.evidence(主图=hero.stat().st_size, 转台=len(turn), 特写=face.stat().st_size,
                   fingerprint=H.fingerprint(hero.stat().st_size, len(turn)))
    a = g.finish()
    res_out = {"ok": True, "主图": str(hero), "脸": str(face),
               "转台": [p.name for p in turn], "钩子": {"通过": a["通过"], "步数": a["步数"]}}
    try:
        from core import solidify as S
        S.solidify("tripo_head_graft", {"身体": Path(body).name, "头部": Path(head).name,
                                        "标签": safe}, note="精细头部嫁接到已绑骨身体")
    except Exception as e:
        _swallow(__file__, e)
    return res_out




def polish(glb: str | Path, *, label: str = "polished", angles: int = 8,
           res=(720, 1080), front_offset: float = 0.0, do_polish: bool = True,
           stylize: bool = False, timeout: float = 1800.0,
           out_dir: str | Path | None = None) -> dict:
    """对云上模型做**本地**材质与光照精修：皮肤次表面散射 / 布料粗糙度+绒面 / 白件皮革高光，
    配主光+补光+轮廓光 + 渐变背板 + 地面接影，重出转台与脸部特写。贴图原样保留，只改物理参数。
    """
    from core import blender as B
    from core import hooks as H
    exe = B.blender_exe()
    g = H.Guard("GBT小土豆V9·材质与光照精修", must_steps=("环境核验", "材质分类",
                                                      "blender 运行", "产物核验"))
    with g.step("环境核验", expect="blender 与 glb 都在") as s:
        src = Path(glb)
        if not exe:
            raise H.HookError("本机没有 blender")
        if not src.is_file():
            raise H.HookError(f"找不到模型：{src}")
        s.evidence(glb=src.name, 大小=src.stat().st_size, fingerprint=H.fingerprint(src.name))
    dest = Path(out_dir) if out_dir else (OUT / "render")
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(label))[:40] or "polished"
    py = dest / f"_polish_{safe}.py"
    with g.step("材质分类", expect="按贴图平均色 + 部位高度把材质分成皮肤/头发/布料/白件") as s:
        tpl = POLISH_TEMPLATE.read_text(encoding="utf-8")
        py.write_text(tpl.replace("__GLB__", str(Path(glb).resolve()))
                         .replace("__OUT_DIR__", str(dest.resolve()))
                         .replace("__LABEL__", safe)
                         .replace("__W__", str(res[0]))
                         .replace("__H__", str(res[1]))
                         .replace("__ANGLES__", str(int(angles)))
                         .replace("__FRONT_OFFSET__", str(float(front_offset)))
                         .replace("__POLISH__", "True" if do_polish else "False")
                         .replace("__STYLIZE__", "True" if stylize else "False"),
                      encoding="utf-8")
        s.evidence(脚本=py.name, 角度=angles, 正面校准=front_offset,
                   fingerprint=H.fingerprint(safe, angles, front_offset))
    with g.step("blender 运行", expect="blender 打印 V9_POLISH_OK，并把材质分类打进日志") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        out = (r.stdout or "") + chr(10) + (r.stderr or "")
        if "V9_POLISH_OK" not in out:
            raise H.HookError(f"精修渲染失败：rc={r.returncode} {out[-400:]}")
        kinds = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("{'材质'")]
        s.evidence(rc=r.returncode, 分类条数=len(kinds), fingerprint=H.fingerprint(r.returncode, len(kinds)))
    with g.step("产物核验", expect="主图/转台/脸部特写都非空") as s:
        hero = dest / f"{safe}_hero.png"
        face = dest / f"{safe}_face.png"
        turn = sorted(dest.glob(f"{safe}_turn*.png"))
        for f in (hero, face):
            if not f.is_file() or f.stat().st_size < 3000:
                raise H.HookError(f"缺图或过小：{f.name}")
        if len(turn) < angles:
            raise H.HookError(f"转台不齐：{len(turn)}/{angles}")
        s.evidence(主图=hero.stat().st_size, 转台=len(turn), 特写=face.stat().st_size,
                   fingerprint=H.fingerprint(hero.stat().st_size, len(turn)))
    a = g.finish()
    res_out = {"ok": True, "主图": str(hero), "脸": str(face),
               "转台": [p.name for p in turn], "材质分类": kinds,
               "钩子": {"通过": a["通过"], "步数": a["步数"]}}
    try:
        from core import solidify as S
        S.solidify("tripo_material_polish", {"模型": Path(glb).name, "标签": safe,
                                            "分类": len(kinds)},
                   note="云模型本地材质与光照精修")
    except Exception as e:
        _swallow(__file__, e)
    return res_out




def rebuild_four_view(views: list, *, label: str = "fv", front_offset: float = 0.0,
                      then: str = "texture,rig", stylize: bool = True) -> dict:
    """**一条命令跑完**：四视图 → 云上重建（贴图+绑骨）→ 本地材质与风格化精修 → 出转台/特写。

    主人充值后跑这条即可（预估 4 视图重建 30 + 贴图 10 + 绑骨 25 = 65 credits）。
    """
    made = make(None, views=views, then=then)
    if not made.get("ok"):
        return made
    shots = polish(made["模型"], label=label, angles=8, res=(720, 1080),
                   front_offset=front_offset, stylize=stylize)
    return {"ok": bool(shots.get("ok")), "生成": made, "渲染": shots}




def render_anim(glb: str | Path, *, label: str = "idle", action: str = "idle",
                seconds: float = 3.0, fps: int = 24, res=(420, 630),
                front_offset: float = 0.0, timeout: float = 3600.0,
                out_dir: str | Path | None = None) -> dict:
    """**真动作**：驱动已绑骨模型的骨骼逐帧做连续动作（呼吸/微摆/头动/挥手/鞠躬），
    渲成无缝循环的 mp4 + 海报帧。页面用 <video loop autoplay muted> 播。

    与"切图片"的区别：姿势是骨骼算出来的连续函数（首尾同相、无跳帧），不是几张静态图轮播。
    """
    from core import blender as B
    from core import hooks as H
    exe = B.blender_exe()
    g = H.Guard("GBT小土豆V9·数字人连续动作渲染", must_steps=("环境核验", "骨骼在册",
                                                          "blender 运行", "产物核验"))
    with g.step("环境核验", expect="blender 与已绑骨的 glb 都在") as s:
        src = Path(glb)
        if not exe:
            raise H.HookError("本机没有 blender")
        if not src.is_file():
            raise H.HookError(f"找不到模型：{src}")
        s.evidence(glb=src.name, 大小=src.stat().st_size, fingerprint=H.fingerprint(src.name))
    dest = Path(out_dir) if out_dir else (OUT / "render")
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(label))[:40] or "anim"
    mp4 = dest / f"{safe}.webm"
    poster = dest / f"{safe}_poster.png"
    py = dest / f"_anim_{safe}.py"
    with g.step("骨骼在册", expect="脚本里按骨名驱动（Root/Spine/Head/Limb）") as s:
        tpl = ANIM_TEMPLATE.read_text(encoding="utf-8")
        py.write_text(tpl.replace("__GLB__", str(Path(glb).resolve()))
                         .replace("__OUT_MP4__", str(mp4.resolve()))
                         .replace("__OUT_POSTER__", str(poster.resolve()))
                         .replace("__LABEL__", safe)
                         .replace("__W__", str(res[0])).replace("__H__", str(res[1]))
                         .replace("__SECONDS__", str(float(seconds)))
                         .replace("__FPS__", str(int(fps)))
                         .replace("__ACTION__", str(action))
                         .replace("__FRONT_OFFSET__", str(float(front_offset))),
                      encoding="utf-8")
        s.evidence(动作=action, 秒=seconds, 帧率=fps, fingerprint=H.fingerprint(action, seconds, fps))
    with g.step("blender 运行", expect="blender 打完帧并打印 V9_ANIM_OK") as s:
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        out = (r.stdout or "") + chr(10) + (r.stderr or "")
        if "V9_ANIM_OK" not in out:
            raise H.HookError(f"动画渲染失败：rc={r.returncode} {out[-400:]}")
        drove = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("驱动骨骼")]
        s.evidence(rc=r.returncode, 驱动=(drove[0] if drove else ""),
                   fingerprint=H.fingerprint(r.returncode, len(out)))
    with g.step("产物核验", expect="mp4 与海报都非空") as s:
        if not mp4.is_file() or mp4.stat().st_size < 20000:
            raise H.HookError("视频没出来或过小")
        if not poster.is_file() or poster.stat().st_size < 3000:
            raise H.HookError("海报帧没出来")
        s.evidence(mp4MB=round(mp4.stat().st_size/1048576, 2), 海报=poster.stat().st_size,
                   fingerprint=H.fingerprint(mp4.stat().st_size))
    a = g.finish()
    out_res = {"ok": True, "动作": action, "秒": seconds, "帧率": fps,
               "视频": str(mp4), "海报": str(poster), "编码": "WebM/VP9（免版税，Chromium 通用）",
               "钩子": {"通过": a["通过"], "步数": a["步数"]},
               "口径": "骨骼驱动的连续动作（首尾同相无缝循环），不是静态图轮播"}
    try:
        from core import solidify as S
        S.solidify("tripo_anim", {"动作": action, "秒": seconds, "帧率": fps, "视频": mp4.name},
                   note="数字人连续动作（骨骼驱动 → mp4 循环）")
    except Exception as e:
        _swallow(__file__, e)
    return out_res




def render_cycles(glb: str | Path, *, label: str = "c", res=(640, 960), samples: int = 96,
                  front_offset: float = 0.0, shots: str = "hero,front,side,back,face",
                  timeout: float = 5400.0, out_dir: str | Path | None = None) -> dict:
    """**用本地 Blender 的光追引擎（Cycles）出成品图**：真实次表面散射 + 真实全局光 + 自带降噪。

    这是"做到最好"的那一档：慢（CPU 可能十几分钟一张），但皮肤通透、阴影真实、反射真实。
    """
    from core import blender as B
    from core import hooks as H
    exe = B.blender_exe()
    g = H.Guard("GBT小土豆V9·Cycles 成品渲染", must_steps=("环境核验", "脚本生成",
                                                       "blender 运行", "产物核验"))
    with g.step("环境核验", expect="blender 与模型都在") as s:
        src = Path(glb)
        if not exe:
            raise H.HookError("本机没有 blender")
        if not src.is_file():
            raise H.HookError(f"找不到模型：{src}")
        s.evidence(glb=src.name, 大小MB=round(src.stat().st_size/1048576, 1),
                   fingerprint=H.fingerprint(src.name))
    dest = Path(out_dir) if out_dir else (OUT / "render")
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(label))[:40] or "c"
    py = dest / f"_cycles_{safe}.py"
    with g.step("脚本生成", expect="Cycles 脚本（HIP 优先、失败退 CPU）") as s:
        tpl = CYCLES_TEMPLATE.read_text(encoding="utf-8")
        py.write_text(tpl.replace("__GLB__", str(Path(glb).resolve()))
                         .replace("__OUT_DIR__", str(dest.resolve()))
                         .replace("__LABEL__", safe)
                         .replace("__W__", str(res[0])).replace("__H__", str(res[1]))
                         .replace("__SAMPLES__", str(int(samples)))
                         .replace("__FRONT_OFFSET__", str(float(front_offset))),
                      encoding="utf-8")
        s.evidence(脚本=py.name, 采样=samples, 尺寸=f"{res[0]}x{res[1]}",
                   fingerprint=H.fingerprint(samples, res))
    with g.step("blender 运行", expect="Cycles 逐张出图并打印完成行") as s:
        env = dict(__import__("os").environ)
        env["V9_CYCLES_SHOTS"] = shots
        r = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(py)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False, env=env)
        out = (r.stdout or "") + chr(10) + (r.stderr or "")
        if "V9_CYCLES_OK" not in out:
            raise H.HookError(f"Cycles 渲染失败：rc={r.returncode} {out[-400:]}")
        done = [ln.strip() for ln in out.splitlines() if ln.startswith("cycles 完成")]
        dev = [ln.strip() for ln in out.splitlines() if ln.startswith("Cycles 设备")]
        s.evidence(完成=done, 设备=(dev[0] if dev else ""), fingerprint=H.fingerprint(len(done)))
    with g.step("产物核验", expect="每张都非空") as s:
        pngs = sorted(dest.glob(f"{safe}_c_*.png"))
        if not pngs:
            raise H.HookError("没有出图")
        small = [p.name for p in pngs if p.stat().st_size < 20000]
        if small:
            raise H.HookError(f"有图过小：{small}")
        s.evidence(张数=len(pngs), 总MB=round(sum(p.stat().st_size for p in pngs)/1048576, 1),
                   fingerprint=H.fingerprint(len(pngs)))
    a = g.finish()
    res_out = {"ok": True, "引擎": "Cycles（光追）", "图": [p.name for p in pngs],
               "钩子": {"通过": a["通过"], "步数": a["步数"]},
               "口径": "本地光追成品图：真实次表面散射 + 全局光 + 降噪"}
    try:
        from core import solidify as S
        S.solidify("tripo_cycles", {"模型": Path(glb).name, "张数": len(pngs),
                                    "采样": samples, "标签": safe},
                   note="Cycles 光追成品渲染（本地 Blender）")
    except Exception as e:
        _swallow(__file__, e)
    return res_out


__all__ = ["status", "make", "render_asset", "graft_head", "polish",
           "render_anim", "render_cycles", "rebuild_four_view", "pipeline", "OUT"]
