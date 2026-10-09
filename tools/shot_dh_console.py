# tools/shot_dh_console.py —— 给数字人交互台拍验收图（playwright + 假接口，不依赖面板在跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要它：视觉改造不能靠嘴说「好看了」——本仓的规矩是**渲染取证**。
#   这里用 playwright 把页面渲出来、用 route 拦掉 /api/** 喂真读数（骨架图是真的，
#   数字是编好的样本并标注），再截图；人（或 read_image）看图判定。
# 用法：python tools/shot_dh_console.py [输出png]
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def payload(url: str) -> dict:
    """按真实端点形状喂样本（骨架 SVG 用真件现算）。"""
    from core import avatar_rig as AR
    if "/api/avatar/state" in url:
        st = AR.speak_state("wave", 0.35, speaking=False)     # 真骨架真动作
        return {**st, "关节数": len(st["angles"])}
    if "/api/avatar/status" in url:
        return AR.status()
    if "/api/brain/metacog" in url:
        return {"ok": True, "自我陈述": "我是 GBT小土豆V9。我记得 128 件事，其中 42% 被用过；"
                                      "答不上来的比例是 18%——那是真读数，不是估的。",
                "校准": {"记忆数": 128, "被用到过": 54, "用到率": 0.42, "热层占比": 0.31,
                         "问过": 57, "答不上来": 10, "未答率": 0.175},
                "计数": {"ask": 57, "ask_miss": 10},
                "该做的": ["把『触手专业』接进派活口", "补上盲区→查资料的闭环"],
                "口径": "样本数据（截图用）"}
    if "/api/digital-human/witness" in url:
        return {"valid_count": 3, "required": 3, "status": "ok", "stale": False,
                "observed_at": "2026-10-08T16:50:00Z", "safe_sentence": "三条见证都在，证据等级 A/A/B。",
                "states": {"w1": {"evidence_level": "A", "vote_eligible": True},
                           "w2": {"evidence_level": "A", "vote_eligible": True},
                           "w3": {"evidence_level": "B", "vote_eligible": True}}}
    if "/api/fleet/status" in url:
        return {"config": {"n": 100, "commander": "GBT小土豆V9", "mode": "auto",
                            "own_key_tentacles": 2, "distinct_key_ids": 3,
                            "same_key": False, "distinct_models": 2},
                "drives": {"drives": 42, "tentacles_used": 7}}
    if "/api/setup/status" in url:
        if SETUP_MODE:
            return {"需要配置": True, "必须先配": ["OPENAI_API_KEY", "GUI_GRANT_SECRET"],
                    "行": [{"键": "OPENAI_API_KEY", "说明": "主脑/触手的 LLM 通道", "已配": False, "掩码": ""},
                           {"键": "GUI_GRANT_SECRET", "说明": "授权令牌签名密钥（跨进程要同一把）", "已配": False, "掩码": ""},
                           {"键": "OPENAI_BASE_URL", "说明": "网关地址（默认本机 8317）", "已配": False, "掩码": ""}],
                    "文件": "state/keys.env", "口径": "不回显原文"}
        return {"需要配置": False, "必须先配": [], "行": [], "文件": "state/keys.env"}
    if "/api/voice/emotion" in url:
        return {"mood": "平", "intensity": 0.4, "style": "台湾腔", "recent_lines": []}
    if "/api/digital-human/ask" in url:
        return {"safe_sentence": "吞噬能：最近一段 12 帧，零丢帧。", "stale": False,
                "unknown_reason": "", "facts": {"frames": 12, "gaps": 0}}
    return {"ok": True}


SETUP_MODE = "--setup" in sys.argv


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    look = "real" if "--look=real" in sys.argv else "anim"
    if SETUP_MODE:
        look = "setup"
    out = (Path(args[0]) if args else
           ROOT / "state" / "preview" /
           ("dh_console.png" if look == "anim"
            else ("dh_setup.png" if look == "setup" else "dh_console_real.png")))
    out.parent.mkdir(parents=True, exist_ok=True)
    from panel.dh_console import render
    from skills.ui_design import inject
    html = inject(render(), "/digital-human/console")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 940}, device_scale_factor=1)
        # ★必须**拦整个文档**并给页面一个真 origin：set_content() 的原点是 about:blank，
        #   页面里的相对 URL（/api/...）会直接 `Failed to parse URL` —— 第一版截图就是这么白渲的。
        HOST = "http://dh-console.local"

        import urllib.parse as _up
        RENDER = ROOT / "state" / "tripo" / "render"

        def _file(u):
            """动作件/形象图要发**真字节**，否则 <video>/<img> 收到 JSON 就是空的。"""
            q = _up.parse_qs(_up.urlparse(u).query)
            n = (q.get("name") or [""])[0]
            p = RENDER / n
            try:
                p.resolve().relative_to(RENDER.resolve())
            except ValueError:
                return None
            return p if p.is_file() else None

        def _route(route):
            u = route.request.url
            if u.rstrip("/") == HOST:
                return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                     body=html)
            if "/api/tripo/anim" in u or "/api/tripo/frame" in u:
                p = _file(u)
                if p is None:
                    return route.fulfill(status=404, body="")
                mt = ("video/webm" if p.suffix == ".webm" else
                      ("video/mp4" if p.suffix == ".mp4" else "image/png"))
                return route.fulfill(status=200, content_type=mt, body=p.read_bytes())
            if "/api/" in u:
                if "/stream" in u:
                    return route.fulfill(status=200, content_type="text/event-stream", body="")
                return route.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(payload(u), ensure_ascii=False))
            return route.fulfill(status=204, body="")

        pg.route("**/*", _route)
        pg.goto(HOST + "/", wait_until="load")
        pg.wait_for_timeout(900)
        if look == "real":            # 切到写实静帧档再拍
            pg.evaluate("() => { const s=document.getElementById('look'); if(!s) return;"
                        " s.value='real'; s.dispatchEvent(new Event('change')); }")
            pg.wait_for_timeout(900)
        # 让 <video> 真出一帧（autoplay 在无头里不一定开跑）：手动 seek 一下
        try:
            pg.evaluate("() => { const v=document.getElementById('anim'); if(!v) return;"
                        " v.muted=true; v.play&&v.play(); v.currentTime=0.45; }")
        except Exception as e:
            _swallow(__file__, e)
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(out))
        b.close()
    print("截图:", out, out.stat().st_size if out.exists() else 0, "字节")
    return 0 if out.exists() and out.stat().st_size > 20000 else 1


if __name__ == "__main__":
    raise SystemExit(main())
