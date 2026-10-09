# core/film_themes.py —— 港式僵尸 · 三个自动化主题
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「以香港僵尸电影来做三个自动化的主题，做出高质量的作品算成功。」
#
# 一个"主题"= 一套可复用的**参数包**（灯光 · 调色 · 音乐 · 节奏 · 字幕 · 片头尾 · 剧本模板），
# 交给影视线引擎（film_studio.run_pipeline）就能全自动出片 —— 不用人插手。
# 三个主题都落在港式僵尸片的正统语汇上：义庄/打更/纸符/铜钱剑/桃木剑/糯米/霓虹雨夜/铁闸。
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "themes" / "ledger.jsonl"

THEMES: dict = {
    "午夜义庄": dict(
        名="午夜义庄", 副标="港式僵尸 · 冷绿月光 · 纸符自燃", 调色="港式冷绿", 音乐="恐怖",
        每镜秒=2.8, 景="义庄", 霓虹色=(150, 186, 220),   # 恐怖片节奏：慢镜压气
        灯=dict(名="月冷", 主光=2.4, 主光色=(186, 214, 200), 辅光=1.0, 轮廓=3.6,
                轮廓色=(140, 186, 220), 环境=0.42,
                主光角=(58, 0, -34), 轮廓角=(116, 0, 198)),
        字幕=dict(字号=46, 颜色=(226, 236, 230), 描边=3),
        剧本="三更鼓响，义庄的门自己开了。棺盖横着挪了半尺，纸符无火自燃。铜钱剑在墙上抖，打更人屏住气。僵尸一起一落，跳过后院那口井。",
    ),
    "僵尸道长": dict(
        名="僵尸道长", 副标="港式僵尸 · 油灯暖黄 · 桃木剑压灵", 调色="义庄暖黄", 音乐="恐怖",
        每镜秒=2.4, 景="道堂", 霓虹色=(255, 176, 110),
        灯=dict(名="油灯", 主光=3.6, 主光色=(255, 212, 146), 辅光=1.6, 轮廓=1.9,
                轮廓色=(255, 176, 110), 环境=0.58,
                主光角=(54, 0, -30), 轮廓角=(118, 0, 196)),
        字幕=dict(字号=46, 颜色=(255, 238, 214), 描边=3),
        剧本="道长把黄符贴上棺盖，糯米撒成一圈。尸气顶着符纸往上鼓，桃木剑压住它的天灵。一口黑血喷在门槛上，僵尸终于不动了。",
    ),
    "都市尸潮": dict(
        名="都市尸潮", 副标="港式僵尸 · 霓虹冷蓝 · 雨夜铁闸", 调色="霓虹冷蓝", 音乐="恐怖",
        每镜秒=2.0, 景="街景", 霓虹色=(255, 118, 198),   # 追逃节奏：快切
        灯=dict(名="霓虹", 主光=2.6, 主光色=(168, 208, 255), 辅光=0.9, 轮廓=3.8,
                轮廓色=(255, 118, 198), 环境=0.46,
                主光角=(62, 0, -38), 轮廓角=(112, 0, 202)),
        字幕=dict(字号=46, 颜色=(228, 238, 255), 描边=3),
        剧本="雨夜的港岛，霓虹照着一排低头的人。手电扫过去，那些脸是青的。铁闸落下的那一刻，整条街都在跳。天亮之前，别回头。",
    ),
}


def themes() -> list:
    return [dict(键=k, 名=v["名"], 副标=v["副标"], 调色=v["调色"], 音乐=v["音乐"], 每镜秒=v["每镜秒"],
                 主光色=v["灯"]["主光色"], 轮廓色=v["灯"]["轮廓色"], 剧本字数=len(v["剧本"]))
            for k, v in THEMES.items()]


def produce(key: str, script: str = "", title: str = "", out: str = "") -> dict:
    """**全自动出一支**：给定主题名（或键），走影视线引擎出片 → 后期统一 → 落台账。"""
    from core import film_studio as FS
    t = THEMES.get(key) or next((v for v in THEMES.values() if v["名"] == key), None)
    if not t:
        return {"ok": False, "error": "没有这个主题: %s" % key, "可选": list(THEMES)}
    sc = script or t["剧本"]
    ti = title or t["名"]
    out = out or ("僵尸-%s.mp4" % t["名"])
    t0 = time.time()
    r = FS.run_pipeline(sc, title=ti, out=out, theme=t, verbose=False)
    film = r.get("成片") or {}
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "主题": t["名"], "副标": t["副标"],
           "调色": t["调色"], "音乐": t["音乐"], "每镜秒": t["每镜秒"],
           "灯": t["灯"], "脚本字数": len(sc), "ok": bool(r.get("ok")), "trace": r.get("trace"),
           "成片": film.get("文件"), "时长": film.get("秒"), "镜数": film.get("镜数"),
           "分辨率": film.get("分辨率"), "亮度均值": film.get("亮度均值"),
           "响度LUFS": film.get("响度LUFS"), "质检红灯": film.get("质检") or [],
           "耗时秒": round(time.time() - t0, 1)}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return {"ok": bool(r.get("ok")), **rec, "台账": str(LEDGER.relative_to(ROOT))}


def produce_all(keys=None) -> dict:
    keys = keys or list(THEMES)
    rows = [produce(k) for k in keys]
    return {"主题数": len(rows), "成功": sum(1 for r in rows if r.get("ok")), "结果": rows}


def history(limit: int = 20) -> dict:
    if not LEDGER.is_file():
        return {"条数": 0, "行": []}
    rows = [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {"条数": len(rows), "行": rows[-limit:]}


__all__ = ["THEMES", "themes", "produce", "produce_all", "history", "LEDGER"]
