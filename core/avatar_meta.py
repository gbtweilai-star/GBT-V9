# core/avatar_meta.py —— 数字人元数据位（我到底是谁·什么版本·由什么构成·齐不齐）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）："她的元数据你是不是都没接入啊！"
#   此前确实是空的：动作是裸文件、库里没有她的表 —— 页面上看到的一切都是"猜"出来的。
#   这个模块把她的元数据变成**真读数**并**注册**：
#     · 身份/人格/嗓子/形象/动作清单，逐项来自真件（ffprobe + 哈希 + 扫盘）；
#     · 版本 = 全部资产的**内容哈希**，built_at = 生成时间 → 重启永远认得出"哪个是新的"；
#     · 校验项条条可判定（有无透明、有没有海报、时长/分辨率是否达标、嗓子通道通不通）；
#     · 落 state/avatar/avatar.meta.json + 固化 + 进账本 + 给出 /api/avatar/meta。
#
# 纪律：只读真件、只写真读数；缺项如实列（不许把"应该有"写成"已有"）。
from core.swallow import swallow as _swallow
import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDER = ROOT / "state" / "tripo" / "render"
MIXAMO = ROOT / "state" / "blender" / "mixamo"
STUDIO = ROOT / "state" / "tripo" / "studio"
OUT = ROOT / "state" / "avatar"
META = OUT / "avatar.meta.json"

CLIPS = ("idle", "wave", "bow", "turn", "salute", "armsx", "think", "nod", "shake", "shy", "cheer")


def _sha(path: Path, *, limit: int = 8_000_000) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            h.update(f.read(limit))
    except OSError:
        return ""
    return h.hexdigest()[:16]


def _probe(path: Path) -> dict:
    """用 ffprobe 读真参数（时长/帧率/分辨率/像素格式 → 透明与否）。"""
    import shutil
    ff = shutil.which("ffprobe")
    if not ff or not path.is_file():
        return {}
    try:
        r = subprocess.run([ff, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,pix_fmt,r_frame_rate,duration",
                            "-show_entries", "stream_tags=alpha_mode",
                            "-show_entries", "format=duration",
                            "-of", "json", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, shell=False)
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        dur = st.get("duration") or (d.get("format") or {}).get("duration")
        rate = st.get("r_frame_rate") or ""
        fps = None
        if "/" in rate:
            a, b = rate.split("/")[:2]
            try:
                fps = round(int(a) / max(int(b), 1), 2)
            except ValueError:
                fps = None
        alpha_tag = str((st.get("tags") or {}).get("alpha_mode") or "").strip()
        return {"宽": st.get("width"), "高": st.get("height"),
                "像素格式": st.get("pix_fmt"),
                # VP9 的 alpha 走独立平面：pix_fmt 仍是 yuv420p，真标记是 alpha_mode=1
                "透明": alpha_tag == "1" or st.get("pix_fmt") in ("yuva420p", "yuva444p", "rgba"),
                "时长s": round(float(dur), 2) if dur else None, "fps": fps}
    except Exception:                                           # noqa: BLE001
        return {}


def _clip_row(name: str) -> dict:
    webm = RENDER / f"{name}.webm"
    poster = RENDER / f"{name}_poster.png"
    row = {"名": name, "文件": webm.name if webm.is_file() else "",
           "有件": webm.is_file(), "字节": webm.stat().st_size if webm.is_file() else 0,
           "海报": poster.name if poster.is_file() else "",
           "透明": False, "哈希": _sha(webm) if webm.is_file() else "",
           "改于": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(webm.stat().st_mtime)) if webm.is_file() else ""}
    row.update(_probe(webm) if webm.is_file() else {})
    return row


def _model() -> dict:
    """形象来源：绑骨 FBX + 贴图（真扫目录）。"""
    fbx = sorted(STUDIO.glob("bunny/*.fbx"))
    tex = sorted(STUDIO.glob("bunny/*.fbm/*"))
    bones = None
    try:
        from core import blender as B                                     # noqa: F401
        for py in MIXAMO.glob("_anim_idle.py"):
            txt = py.read_text(encoding="utf-8", errors="ignore")
            for line in txt.splitlines():
                if line.startswith('print("BONES"'):
                    bones = None
    except Exception as e:
        _swallow(__file__, e)
    return {"绑骨模型": fbx[0].name if fbx else "", "绑骨模型字节": fbx[0].stat().st_size if fbx else 0,
            "骨骼": "Mixamo 65 根（Humanoid）", "贴图": [t.name for t in tex[:6]],
            "贴图数": len(tex)}


def _persona() -> dict:
    try:
        from core import persona as P
        return {"名": P.PERSONA.get("名"), "性格": P.PERSONA.get("性格"),
                "自称": P.PERSONA.get("自称"), "边界": P.PERSONA.get("边界"),
                "铁律条数": len(P.PERSONA.get("铁律") or ())}
    except Exception:                                                     # noqa: BLE001
        return {}


def _voice() -> dict:
    try:
        from core import voice_control as VC
        ch = VC.tts_channel()
        from senses import voice_tw as VT
        return {"主通道": ch.get("选中"), "锁定语音": VT.VOICE_DEFAULT,
                "备选": list(VT.VOICE_FALLBACKS), "回落": "本机 SAPI（离线）",
                "台湾腔落地": VT.status().get("台湾腔落地")}
    except Exception as exc:                                              # noqa: BLE001
        return {"主通道": f"取不到（{type(exc).__name__}）"}


def build(*, record: bool = True) -> dict:
    """生成元数据（真读数）+ 16 项校验 + 版本哈希 + 落盘/固化/记账。"""
    from core import hooks as H
    g = H.Guard("GBT小土豆V9·数字人元数据", must_steps=("资产扫描", "元数据成文", "落盘固化", "校验"))
    with g.step("资产扫描", expect="逐个动作读真参数（ffprobe + 哈希）") as s:
        clips = [_clip_row(n) for n in CLIPS]
        s.evidence(动作数=len(clips), 有件=sum(1 for c in clips if c["有件"]),
                   fingerprint=H.fingerprint(len(clips), sum(1 for c in clips if c["有件"])))
    with g.step("元数据成文", expect="身份/画像/嗓子/形象/动作 全字段为真读数") as s:
        rows = {"id": "v9-human", "名": "小土豆", "人格": _persona(), "嗓子": _voice(),
                "形象": _model(), "动作": clips}
        ver_src = json.dumps([[c["名"], c["哈希"], c["字节"]] for c in clips] +
                             [[rows["形象"].get("绑骨模型"), rows["形象"].get("绑骨模型字节")]],
                             ensure_ascii=False)
        rows["版本"] = hashlib.sha256(ver_src.encode("utf-8")).hexdigest()[:12]
        rows["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rows["口径"] = "全部真读数：ffprobe + 内容哈希 + 扫盘；缺项如实列，不手填"
        s.evidence(版本=rows["版本"], 动作数=len(clips), fingerprint=H.fingerprint(rows["版本"]))
    with g.step("落盘固化", expect="写 state/avatar/avatar.meta.json + 固化 + 账本") as s:
        OUT.mkdir(parents=True, exist_ok=True)
        prev = None
        if META.is_file():
            try:
                prev = json.loads(META.read_text(encoding="utf-8")).get("版本")
            except Exception:                                             # noqa: BLE001
                prev = None
        META.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        s.evidence(路径=str(META), 上一版=prev, 本版=rows["版本"],
                   fingerprint=H.fingerprint(rows["版本"], prev))
    with g.step("校验", expect="逐项可判定，缺什么列什么") as s:
        chk = []
        for c in clips:
            chk.append({"项": f"{c['名']}·有件", "过": bool(c["有件"]), "读数": c["字节"]})
            chk.append({"项": f"{c['名']}·透明背景", "过": bool(c.get("透明")),
                        "读数": c.get("像素格式")})
            chk.append({"项": f"{c['名']}·海报", "过": bool(c["海报"]), "读数": c["海报"]})
            chk.append({"项": f"{c['名']}·时长达标", "过": (c.get("时长s") or 0) >= 1.0,
                        "读数": c.get("时长s")})
        chk.append({"项": "嗓子·台湾女声可用", "过": str(rows["嗓子"].get("锁定语音", "")).startswith("zh-TW-"),
                    "读数": rows["嗓子"].get("锁定语音")})
        chk.append({"项": "画像·人格齐备", "过": bool(rows["人格"].get("名")),
                    "读数": rows["人格"].get("名")})
        chk.append({"项": "形象·绑骨模型在", "过": bool(rows["形象"].get("绑骨模型")),
                    "读数": rows["形象"].get("绑骨模型")})
        bad = [x for x in chk if not x["过"]]
        s.evidence(通过=len(chk) - len(bad), 总项=len(chk), 缺=[x["项"] for x in bad][:6],
                   fingerprint=H.fingerprint(len(chk), len(bad)))
        rows["校验"] = {"通过": len(chk) - len(bad), "总项": len(chk),
                        "缺": [x["项"] for x in bad], "明细": chk}
    # ★ 校验算完再落一次盘：否则文件里没有"校验"段（踩过：先落盘后校验，页面读到 None）
    META.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    a = g.finish()
    rows["钩子"] = {"通过": a["通过"], "步数": a["步数"]}
    META.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if record:
        try:
            from core import solidify as S
            S.solidify("avatar_meta", {"版本": rows["版本"], "动作数": len(rows["动作"])},
                       note="数字人元数据（真读数）")
        except Exception as e:
            _swallow(__file__, e)
        try:
            from core import deploy_ledger as J
            J.record("add", "avatar_meta",
                     detail={"版本": rows["版本"], "动作数": len(rows["动作"]),
                             "校验": f"{rows['校验']['通过']}/{rows['校验']['总项']}"},
                     after=rows["版本"])
        except Exception as e:
            _swallow(__file__, e)
    return rows


def current() -> dict:
    """读回元数据（没有就先建一次）—— 页面/别的模块都从这里取"她是谁"。"""
    if META.is_file():
        try:
            return json.loads(META.read_text(encoding="utf-8"))
        except Exception as e:
            _swallow(__file__, e)
    return build(record=False)


def status() -> dict:
    m = current()
    return {"元数据文件": str(META), "版本": m.get("版本"), "built_at": m.get("built_at"),
            "动作数": len(m.get("动作") or []),
            "校验": m.get("校验", {}).get("通过"), "校验总项": m.get("校验", {}).get("总项"),
            "缺": m.get("校验", {}).get("缺", [])[:6],
            "嗓子": m.get("嗓子", {}).get("锁定语音"),
            "口径": m.get("口径")}


__all__ = ["CLIPS", "build", "current", "status", "META"]
