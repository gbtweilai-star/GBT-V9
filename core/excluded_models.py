# core/excluded_models.py —— 厂商/模型排除登记（有日期、有理由、可复核）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令（2026-10-09）：
#   「切换 GLM5.3 之后我的能力直接被阉割，很多熬夜做成的她直接给我干没了，要不然就拒绝说不做…
#     把框架里面 kimi 和 GLM 删了，备注一下为什么删这两家公司。」
#
# 本登记表就是那份"备注"——**不是偷偷删，是留下理由的排除**：
#   · 生效日期、范围（哪些 id 前缀算它家的）、理由（引用主人原话）、处置（路由排除/不接线）；
#   · 代码里凡是要列模型的地方，都先问 is_excluded()，**排除就是排除，不再悄悄回到列表里**。
# 🔴 诚实边界：这只是**本框架的路由与接线层面不再使用**，不改动任何第三方软件的既有授权；
#   也不代表对厂商的事实定性——理由栏写的是主人陈述，原样留档备查。
from __future__ import annotations

EXCLUDED: tuple = (
    dict(厂商="智谱 GLM", 前缀=("@cf/zai-org/", "glm-", "zai-org"), 简称=("GLM", "glm", "智谱"),
         生效日="2026-10-09", 处置="路由排除：不再出现在候选/分档/动作链里",
         理由="主人陈述：切换到 GLM5.3 后能力被阉割——已有成果被删除、或直接拒绝不执行；"
              "无法接受（该厂还要求绑定付费方式）",
         留档="原始陈述见 2026-10-09 对话；本表只做登记，不作事实定性"),
    dict(厂商="月之暗面 Kimi", 前缀=("@cf/moonshotai/", "kimi-", "moonshot"), 简称=("Kimi", "kimi", "moonshot"),
         生效日="2026-10-09", 处置="路由排除：不再出现在候选/分档/动作链里",
         理由="主人陈述：与 GLM 同型问题（被拒/被改），一并排除",
         留档="原始陈述见 2026-10-09 对话；本表只做登记，不作事实定性"),
)


def is_excluded(model_id: str) -> dict:
    """这个模型 id 是否被排除。返回 {排除: bool, 厂商, 理由}（用于所有候选列表的过滤）。"""
    s = str(model_id or "")
    low = s.lower()
    for e in EXCLUDED:
        if any(low.startswith(p.lower()) or (p.lower().rstrip("-") in low and p.endswith("-"))
               for p in e["前缀"]) or any(k.lower() in low for k in e["简称"]):
            return {"排除": True, "厂商": e["厂商"], "生效日": e["生效日"], "理由": e["理由"]}
    return {"排除": False}


def registry() -> dict:
    rows = [{"厂商": e["厂商"], "排除前缀": list(e["前缀"]), "生效日": e["生效日"],
             "处置": e["处置"], "理由": e["理由"]} for e in EXCLUDED]
    return {"条数": len(rows), "行": rows,
            "口径": "路由与接线层面不再使用；不改第三方授权；理由栏是主人陈述，原样留档"}


__all__ = ["EXCLUDED", "is_excluded", "registry"]
