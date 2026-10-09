# panel/native_page.py —— 她的原生能力页（把 docs/她的原生能力.md 端给用户看）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from skills.ui_design import inject

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent


@router.get("/native", response_class=HTMLResponse)
async def native_page() -> HTMLResponse:
    doc = ROOT / "docs" / "她的原生能力.md"
    src = doc.read_text(encoding="utf-8", errors="replace") if doc.is_file() else "（缺 docs/她的原生能力.md）"
    esc = (src.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    html = ("<!doctype html><meta charset=utf-8><title>她的原生能力</title>"
            "<style>body{margin:0;background:#0b0e14;color:#e6edf3;font:14px/1.7 system-ui,'Microsoft YaHei'}"
            ".wrap{max-width:1000px;margin:0 auto;padding:20px 24px}"
            "pre{white-space:pre-wrap;word-break:break-word;background:#0f131c;border:1px solid #1e2635;"
            "border-radius:12px;padding:16px 18px;font:13px/1.75 Consolas,monospace}</style>"
            "<div class=wrap><h3>她的原生能力（触手 · 万能插 · 穿透扫描 · 脑子）</h3>"
            "<pre>" + esc + "</pre></div>")
    return inject(html, "/native")


@router.get("/api/native/raw")
async def native_raw() -> dict:
    doc = ROOT / "docs" / "她的原生能力.md"
    return {"文件": str(doc), "字节": doc.stat().st_size if doc.is_file() else 0,
            "原文": doc.read_text(encoding="utf-8", errors="replace") if doc.is_file() else ""}
