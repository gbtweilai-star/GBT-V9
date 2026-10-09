# core/protected_assets.py —— 不可删保护清单（导航/自检/记忆/底座 ≠ 危险件）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令（2026-10-09）：
#   「很多大模型跟我硬磕说把这些去掉危险，你看看上帝视角是干嘛用的，她是一套完整闭环的
#     长程编程以及项目导航。」
#
# 我逐行核过的事实：
#   · core/blueprint.py 的上帝视角 = 指挥层→能力层→编队层→地基 四层总览 + 六面无死角自检
#     （按键页面/能力五查/编队/槽位/关卡/地基）+ 每层标来源；口径「数字不对就是图纸不对，先修数据再改图」；
#   · core/brain.py::god_view = 主脑看**全部触手记忆**的视野（仅主脑可调）；
#   · V8 测试原文：「上帝视角：**只呈现，不执行** —— 整块不许有 POST」。
# 结论：这些是**仪表盘与回滚底座**，不是危险件。谁要删，先过这道闸——**一律拒**。
from __future__ import annotations

PROTECTED: tuple = (
    dict(名="上帝视角 / 3D 蓝图", 落点="core/blueprint.py · panel/blueprint_page.py",
         为什么="长程项目的全局导航 + 六面无死角自检（缺面报警）；只呈现不执行，删了就是拆仪表盘"),
    dict(名="主脑全量视野 god_view", 落点="core/brain.py",
         为什么="主脑必须能看见全部触手记忆，否则编队失控时无人可查"),
    dict(名="固化 / 台账 / 记忆地基", 落点="core/solidify.py · audit/ledger_factory.py · core/memory",
         为什么="可回滚的底；删了就没有回退与追责"),
    dict(名="操作绑定表 / SOP 册", 落点="core/operation_bindings.py · core/playbooks.py",
         为什么="重启后靠它照做，删了等于失忆"),
    dict(名="密钥位 / 凭据金库", 落点="core/local_secret.py · core/tentacle_identity.py",
         为什么="不是危险件，是省事的基础：一次存入、永久自动注入"),
    dict(名="闸门本身", 落点="core/ux_doctrine.py",
         为什么="把闸门删了，「危险动作留授权」就没了——那才是真的危险"),
    dict(名="万能插 / 脉冲插座层", 落点="core/pulse.py",
         为什么="她用自身能源驱动外部对象的通道；删了她就只能干看着"),
    dict(名="云插件 / 云终端 / 额度闸", 落点="core/cloud_plugins.py · core/cloud_runner.py · core/cloud_bind.py",
         为什么="她唯一的算力来源，删了就没有云上大脑"),
)


def check_removal(target: str) -> dict:
    """有人（或某个模型）提议删东西时先过这道闸：受保护的**一律拒**。"""
    import re as _re
    s = str(target or "")
    for p in PROTECTED:
        # ★关键词要**拆开**再判：第一版拿整条名字（如「上帝视角 / 3D 蓝图」）去匹配，
        #   结果「把上帝视角删了」判成了"允许删"（四条只拦住一条，实测）。
        #   拆法：名字按 / 和空格切；落点再补上文件名主干。
        keys = [x for x in _re.split(r"[/\s·（）()]+", p["名"]) if len(x) >= 2]
        for tok in p["落点"].replace("·", " ").split():
            base = tok.replace("\\", "/").split("/")[-1].split(".")[0]
            if len(base) >= 4:
                keys.append(base)
        hit = [k for k in keys if k in s]
        if hit:
            return {"允许删": False, "命中保护": p["名"], "命中词": hit[:3], "为什么": p["为什么"],
                    "怎么办": "拒绝；确要动，必须主人显式下令 + 留变更记录（git + 指纹台账）"}
    return {"允许删": True, "口径": "不在保护清单里；仍建议先备份并登记（git + 指纹台账）"}


def protected_list() -> dict:
    return {"条数": len(PROTECTED),
            "行": [{"名": p["名"], "落点": p["落点"], "为什么": p["为什么"]} for p in PROTECTED],
            "口径": "这几类是导航/自检/记忆/算力/闸门本身，**不是危险件**；任何模型提删都要被拒"}


__all__ = ["PROTECTED", "check_removal", "protected_list"]
