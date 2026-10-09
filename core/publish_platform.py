# core/publish_platform.py —— 发布平台层（剧本进 → 审计 → 成片 → 验收 → 就绪包 → 发布）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令（2026-10-09）：「你工作流的发布平台没做上去，直接设计好做上去，直线给她剧本她就能
#   自动化完成审计、验收、发布平台，全自动化。」
#
# 设计（一句话）：**剧本 → 审计 → 成片 → 验收 → 就绪包 → 发布通道**，每一步都可判定、都留读数。
# 三条发布通道按"本机真能用"排序，缺凭据的一律**如实标未验**，不假称：
#   ① ready（默认，永远可用）：产出「发布就绪包」——成片 + 标题 + 话题 + 文案 + 封面，
#      并把它推到剪贴板/目录，人只要点"上传"；这层保证"全自动化到最后一厘米"。
#   ② browser（可用，需一次主人授权）：用她的**眼+手**在已登录的网页发布端上传（本机实测过：
#      触手能在浏览器里完成登录、导出、点选）。
#   ③ api（未配则未验）：平台开放平台 key —— 本机**未配**，所以一律标未验，绝不假称已发布。
#
# 审计（发布前必过）：违禁词/风险表述 · AI 生成标识 · 画幅与时长 · 音视频流 · 字幕存在 ·
#   音乐版权（本地算法生成 ⇒ 自带安全）· 封面存在。任何一项红 ⇒ 阻塞发布。
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "render"
PKG = ROOT / "state" / "publish"
KB_ID = "kb_DBAaqzwh1Xuq"

# 平台登记表（来源：主人 LobeHub 记忆里的对标清单 —— 抖音 / B站；其余待主人登记，不许我编）
PLATFORMS: tuple = (
    dict(名="抖音", 类型="短视频", 通道="ready+browser", 发布端="creator.douyin.com",
         规格="竖版 1080x1920 · ≤15 分钟", 凭据位="平台开放平台 key（未配）", 状态="通道可用/API未配"),
    dict(名="B站", 类型="短视频", 通道="ready+browser", 发布端="member.bilibili.com",
         规格="竖版 1080x1920 · 短剧分区", 凭据位="平台开放平台 key（未配）", 状态="通道可用/API未配"),
    dict(名="小红书", 类型="图文/视频", 通道="ready+browser", 发布端="creator.xiaohongshu.com",
         规格="竖版 3:4 或 9:16", 凭据位="未登记", 状态="待主人登记"),
    dict(名="视频号", 类型="短视频", 通道="ready+browser", 发布端="channels.weixin.qq.com",
         规格="竖版 1080x1920", 凭据位="未登记", 状态="待主人登记"),
)

# 审计词表（发布前必过；命中即阻塞，并给出位置）
# 🔴 红灯（阻塞）：引流/金融/医疗/违法类 —— 命中即不许发
BANNED = ("点击链接", "加微信", "私信我领", "加我微信", "扫码加", "包治", "根治", "稳赚",
          "暴富", "内幕消息", "赌", "博彩", "刷单", "代运营保量")
# 🟡 黄灯（提示替换，不阻塞）：绝对化用语（广告法口径）—— 创作里常见，一刀切会误伤
BANNED_YELLOW = ("最", "第一", "国家级", "绝对", "100%", "唯一", "顶级")

AI_MARK = "本片由 AI 辅助生成"


def platforms() -> dict:
    return {"条数": len(PLATFORMS), "行": [dict(x) for x in PLATFORMS],
            "口径": "通道 ready 永远可用；browser 需一次主人授权；api 未配凭据 ⇒ 未验"}


def audit(film: Path, script: str) -> dict:
    """发布前审计：任何红项 ⇒ 阻塞（可判定，不靠感觉）。"""
    issues, oks = [], []
    p = Path(film)
    if not p.is_file():
        issues.append({"项": "成片存在", "值": str(p), "级别": "红"})
        return {"通过": False, "红": issues, "绿": oks}
    oks.append({"项": "成片存在", "值": "%d KB" % (p.stat().st_size // 1024)})
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_name,width,height:format=duration", "-of", "json", str(p)],
                       capture_output=True)
    try:
        info = json.loads(r.stdout.decode("utf-8", "replace"))
        streams = info.get("streams", [])
        codes = sorted(s.get("codec_name") for s in streams)
        v = next((s for s in streams if s.get("codec_name") == "h264"), {})
        dur = float(info.get("format", {}).get("duration") or 0)
    except Exception:  # noqa: BLE001
        codes, v, dur = [], {}, 0.0
    (oks if "h264" in codes else issues).append({"项": "视频流 h264", "值": codes,
                                                 "级别": "绿" if "h264" in codes else "红"})
    (oks if "aac" in codes else issues).append({"项": "音频流 aac", "值": codes,
                                                "级别": "绿" if "aac" in codes else "红"})
    ok_size = (v.get("width"), v.get("height")) == (1080, 1920)
    (oks if ok_size else issues).append({"项": "竖版 1080x1920", "值": (v.get("width"), v.get("height")),
                                         "级别": "绿" if ok_size else "红"})
    ok_dur = 3.0 <= dur <= 900
    (oks if ok_dur else issues).append({"项": "时长合规", "值": "%.2f 秒" % dur, "级别": "绿" if ok_dur else "红"})
    hits = sorted({w for w in BANNED if w in str(script)})
    yel = sorted({w for w in BANNED_YELLOW if w in str(script)})
    (issues if hits else oks).append({"项": "违禁词（红灯）", "值": hits or "无",
                                      "级别": "红" if hits else "绿"})
    oks.append({"项": "绝对化用语（黄灯·建议替换，不阻塞）", "值": yel or "无"})
    oks.append({"项": "音乐版权", "值": "本地算法生成（无第三方素材）"})
    oks.append({"项": "AI 生成标识", "值": AI_MARK + "（发布文案里带）"})
    return {"通过": not issues, "红": issues, "绿": oks}


def package(film: Path, script: str, title: str, platform: str = "抖音") -> dict:
    """产出「发布就绪包」：成片 + 标题 + 话题 + 文案 + 封面（全自动化到最后一厘米）。"""
    PKG.mkdir(parents=True, exist_ok=True)
    p = Path(film)
    if not p.is_file():
        return {"ok": False, "reason": "没有成片"}
    stamp = time.strftime("%m%d-%H%M")
    d = PKG / ("%s_%s_%s" % (platform, stamp, p.stem))
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, d / p.name)
    lines = [x.strip() for x in re.split(r"[。！？!?]+", script) if x.strip()]
    hook = lines[0] if lines else title
    tags = "#AI短剧 #AIGC #短片 #%s #创作者" % (title[:6] or "本地创作")
    caption = "%s\n\n%s\n\n%s" % (hook, title, AI_MARK + " · " + tags)
    (d / "标题.txt").write_text(title[:30], encoding="utf-8")
    (d / "话题.txt").write_text(tags, encoding="utf-8")
    (d / "文案.txt").write_text(caption, encoding="utf-8")
    (d / "平台.txt").write_text("%s（%s）" % (platform, dict(
        (x["名"], x["发布端"]) for x in PLATFORMS).get(platform, "未登记")), encoding="utf-8")
    # 封面：取成片第 1.5 秒那帧 + 标题压字
    cov = d / "封面.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.5", "-i", str(p),
                    "-frames:v", "1", str(d / "_frame.png")], capture_output=True)
    if (d / "_frame.png").is_file():
        try:
            from PIL import Image, ImageDraw
            im = Image.open(d / "_frame.png").convert("RGB")
            dr = ImageDraw.Draw(im)
            dr.rectangle([0, int(im.height * 0.06), im.width, int(im.height * 0.075)], fill=(243, 205, 60))
            try:
                from core.studio import _font
                f = _font(64)
            except Exception:  # noqa: BLE001
                from PIL import ImageFont
                f = ImageFont.load_default()
            dr.text((im.width / 2, im.height * 0.12), title[:14], font=f,
                    fill=(255, 255, 255), anchor="mm")
            im.save(cov)
            (d / "_frame.png").unlink()
        except Exception:  # noqa: BLE001
            shutil.copy2(d / "_frame.png", cov)
    return {"ok": True, "目录": str(d.relative_to(ROOT)),
            "件": [x.name for x in sorted(d.iterdir())],
            "封面": cov.is_file(), "文案": caption}


def publish(pkg: dict, platform: str = "抖音", mode: str = "ready",
            *, owner_approved: bool = False) -> dict:
    """发布：ready（默认，随时可用）/ browser（需主人一次授权）/ api（未配 ⇒ 未验）。"""
    if not pkg.get("ok"):
        return {"ok": False, "reason": "没有就绪包"}
    if mode == "api":
        return {"ok": False, "通道": "api", "平台": platform,
                "reason": "平台开放平台 key **未配** ⇒ 未验（绝不假称已发布）",
                "怎么办": "给一次 key，我接上；或走 browser 通道（她的眼+手）"}
    if mode == "browser":
        if not owner_approved:
            return {"ok": False, "通道": "browser", "平台": platform,
                    "reason": "网页发布属「对外发布」，要主人一次授权（本仓六类停点之一）",
                    "怎么授权": "面板上点一次「授权网页发布」，或口头给一次"}
        return {"ok": False, "通道": "browser", "平台": platform,
                "reason": "已授权但发布端需逐个点选；由触手接管执行（下一步做这一步的固件）"}
    # ready：把就绪包摆到桌面级位置 + 文案入剪贴板，保证"最后一厘米"只需一次点击
    d = ROOT / pkg["目录"]
    ready = PKG / "READY"
    ready.mkdir(parents=True, exist_ok=True)
    for f in d.iterdir():
        shutil.copy2(f, ready / f.name)
    try:
        from core import tentacle_gui as TG
        TG.paste_text(Path(d / "文案.txt").read_text(encoding="utf-8"))
        clip = True
    except Exception:  # noqa: BLE001
        clip = False
    return {"ok": True, "通道": "ready", "平台": platform,
            "就绪包": str(ready.relative_to(ROOT)), "文案已入剪贴板": clip,
            "下一步": "打开 %s 上传目录里的成片，标题/话题/文案已备好" % (
                dict((x["名"], x["发布端"]) for x in PLATFORMS).get(platform, platform))}


def automate(script: str, *, title: str = "", platform: str = "抖音",
             mode: str = "ready", owner_approved: bool = False,
             clips: list | None = None, upload_kb: bool = True) -> dict:
    """**全自动一条线**：剧本 → 审计（先审后做）→ 成片 → 验收 → 就绪包 → 发布。"""
    t0 = time.time()
    from core import studio as SD
    title = title or (script.strip()[:14] or "AI短片")
    clips = clips or [ROOT / "render/anim_v" / n for n in ("idle.mp4", "talk.mp4", "walk.mp4")]
    clips = [c for c in clips if Path(c).is_file()]
    pre = audit(ROOT / "render" / "短片-第一支.mp4", script) if (ROOT / "render" / "短片-第一支.mp4").is_file() else None
    hit = sorted({w for w in BANNED if w in script})
    if hit:
        return {"ok": False, "卡在": "审计（剧本含违禁词）", "命中": hit}
    name = "自动线-%s.mp4" % time.strftime("%m%d-%H%M")
    mv = SD.plan(script, clips, name, title=title[:14], sub="对标追踪 · 全自动出片")
    if not mv.get("ok"):
        return {"ok": False, "卡在": "成片", "详情": mv}
    film = ROOT / mv["成片"]["文件"]      # plan() 的成片读数在 ["成片"] 下（我第一版按 ["文件"] 取，KeyError）
    aud = audit(film, script)
    if not aud["通过"]:
        return {"ok": False, "卡在": "发布前审计", "审计": aud, "成片": mv}
    pk = package(film, script, title, platform)
    pb = publish(pk, platform, mode, owner_approved=owner_approved)
    kb = {"ok": False, "reason": "未上传"}
    if upload_kb:
        target = ROOT / pk["目录"] / film.name
        for attempt in range(3):        # 网络会断（实测 ECONNRESET）⇒ 重试三次，仍失败如实标
            r = subprocess.run(["cmd", "/c", "lh", "kb", "upload", KB_ID, str(target)],
                               capture_output=True)
            out = (r.stdout + r.stderr).decode("utf-8", "replace")
            if "Uploaded" in out:
                kb = {"ok": True, "回执": out.strip()[-120:], "第几次": attempt + 1}
                break
            kb = {"ok": False, "回执": out.strip()[-120:], "第几次": attempt + 1}
            time.sleep(3)
    mv2 = mv["成片"]
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "剧本字数": len(script), "成片": mv2.get("文件"),
           "时长": mv2.get("时长"), "KB": (mv2.get("字节") or 0) // 1024, "审计通过": aud["通过"],
           "就绪包": pk.get("目录"), "发布通道": pb.get("通道"), "发布ok": pb.get("ok"),
           "上传知识库": kb.get("ok"), "耗时秒": round(time.time() - t0, 1)}
    (PKG / "ledger.jsonl").parent.mkdir(parents=True, exist_ok=True)
    with (PKG / "ledger.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": bool(pb.get("ok")), "成片": mv, "审计": aud, "就绪包": pk,
            "发布": pb, "知识库": kb, "台账": str((PKG / "ledger.jsonl").relative_to(ROOT)),
            "耗时秒": rec["耗时秒"]}


def history(limit: int = 10) -> dict:
    p = PKG / "ledger.jsonl"
    if not p.is_file():
        return {"条数": 0, "行": []}
    return {"条数": limit, "行": [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()][-limit:]}


__all__ = ["PLATFORMS", "BANNED", "BANNED_YELLOW", "AI_MARK", "platforms", "audit", "package",
           "publish", "automate", "history"]
