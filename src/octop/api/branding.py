# src/octop/api/branding.py —— 后端品牌注入（只加只读接口，不动任何内部标识）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 注意：文件名/包路径保持 octop，只有"返回值"是品牌。绝不改 /api 前缀。
import os
from fastapi import APIRouter

router = APIRouter()

BRAND = {
    "name": os.environ.get("APP_BRAND_NAME", "GBT小土豆V9"),
    "short": os.environ.get("APP_BRAND_SHORT", "小土豆V9"),
    "slogan": "万物皆可插 · 万物皆可控",
    "theme": {"primary": "#39d0ff", "bg": "#0b1020"},
}

@router.get("/branding")          # 最终路径是 /api/branding，不改既有路由
def branding():
    return BRAND
