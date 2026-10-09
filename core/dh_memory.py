# core/dh_memory.py —— 数字人的框架记忆：把整套使用知识存进她的脑子（可检索、可追源）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「数字人要把整个框架的使用知识都存入她的记忆，启动之后一步一步教用户怎么做；
#   用户和她交互只需要把架构给她，就没用户什么事了。」
# 口径：知识**全部从盘上真件抽取**（不编），每条带来源，可检索；她答不出就说"没存到"，不许编。
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "state" / "dh_memory"
UNITS = DIR / "units.jsonl"
LEDGER = DIR / "ledger.jsonl"


def _log(rec: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def _unit(cat: str, title: str, body: str, src: str) -> dict:
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "类别": cat, "标题": title,
            "正文": body[:600], "来源": src}


def collect() -> list:
    """从真件抽知识：能力总览/逐能力文档/页面注册/能力登记/操作绑定/模块蓝图/硬规定。"""
    import sys
    sys.path.insert(0, str(ROOT))
    units = []
    # ① 文档
    for rel in ("docs/她的原生能力.md", "docs/能力总览.md", "docs/安装与运行.md", "README.md", "CONTRIBUTING.md"):
        p = ROOT / rel
        if p.is_file():
            units.append(_unit("文档", rel, p.read_text(encoding="utf-8", errors="replace")[:600], rel))
    for p in sorted((ROOT / "docs" / "capabilities").glob("*.md")):
        units.append(_unit("能力细节", p.stem, p.read_text(encoding="utf-8", errors="replace")[:600],
                           str(p.relative_to(ROOT))))
    # ② 页面
    try:
        from core.page_registry import PAGES
        for pg in PAGES:
            units.append(_unit("页面", "%s %s" % (pg.图标, pg.标题),
                               "路由 %s｜分组 %s｜%s｜接口 %s" % (pg.路由, pg.分组, pg.一句说明,
                                                              ", ".join(pg.接口) or "无"),
                               "core/page_registry.py"))
    except Exception as e:  # noqa: BLE001
        units.append(_unit("页面", "读页面注册失败", type(e).__name__, "core/page_registry.py"))
    # ③ 能力 + 验收器
    try:
        from core import delivery_gate as DG
        for name, cat, verifier in DG.CAPABILITIES:
            units.append(_unit("能力", name, "验收器 %s（跑通即闭环）" % verifier,
                               "core/delivery_gate.py"))
    except Exception as e:  # noqa: BLE001
        units.append(_unit("能力", "读能力登记失败", type(e).__name__, "core/delivery_gate.py"))
    # ④ 怎么调
    try:
        from core import operation_bindings as OB
        for b in list(getattr(OB, "BINDINGS", ()) or ()):
            units.append(_unit("怎么调", str(b.get("动作", ""))[:60],
                               "入口 %s｜输入 %s｜产物 %s｜判据 %s" % (b.get("触手"), b.get("输入"),
                                                                   b.get("产物"), b.get("验收")),
                               "core/operation_bindings.py"))
    except Exception as e:  # noqa: BLE001
        units.append(_unit("怎么调", "读操作绑定失败", type(e).__name__, "core/operation_bindings.py"))
    # ⑤ 模块蓝图
    try:
        from core import modular_deploy as MD
        for mod, spec in MD.BLUEPRINT.items():
            units.append(_unit("模块", mod,
                               "实现 %s｜面板 %s｜验收 %s｜依赖 %s" % (", ".join(spec.get("实现", [])),
                                                                   ", ".join(spec.get("面板", [])),
                                                                   ", ".join(spec.get("验收", [])),
                                                                   spec.get("依赖") or "无"),
                               "core/modular_deploy.py"))
    except Exception as e:  # noqa: BLE001
        units.append(_unit("模块", "读模块蓝图失败", type(e).__name__, "core/modular_deploy.py"))
    # ⑥ 硬规定
    for cat, title, body, src in (
        ("硬规定", "模块闭环", "任何项目开工即拆模块；每模块必须有独立验收器；未闭环不许合入", "core/modular_deploy.py"),
        ("硬规定", "视觉钉死", "动手前必须有新鲜取景+决策记录；缺一步拒动（眼→脑→手→验）", "core/senses_gate.py"),
        ("硬规定", "双向绑定", "每页代码绑一根触手；覆盖 100%；有未绑定=盲区拦停", "core/file_binding.py"),
        ("硬规定", "报警追根因", "红/黄警必须有根因/处置/证据；缺一项算未闭环", "tools/daily_triage.py"),
        ("硬规定", "不许求用户", "框架内不得出现求助出口；唯一例外是主人自己的身份登入", "core/no_begging.py"),
        ("硬规定", "停机闸", "只有部署完成(带证据)/真需用户操作/用户要求停止 才能停", "core/stop_policy.py"),
    ):
        units.append(_unit(cat, title, body, src))
    return units


def ingest() -> dict:
    """把知识灌进她的记忆（幂等：按 标题+来源 去重）。"""
    units = collect()
    DIR.mkdir(parents=True, exist_ok=True)
    old = []
    if UNITS.is_file():
        for line in UNITS.read_text(encoding="utf-8").splitlines():
            try:
                old.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    seen = {(u.get("标题"), u.get("来源")) for u in old}
    fresh = [u for u in units if (u["标题"], u["来源"]) not in seen]
    with UNITS.open("a", encoding="utf-8") as f:
        for u in fresh:
            f.write(json.dumps(u, ensure_ascii=False) + chr(10))
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "ingest",
           "抽到": len(units), "新增": len(fresh), "存量": len(old) + len(fresh)}
    _log(rec)
    return rec


def units() -> list:
    if not UNITS.is_file():
        return []
    out = []
    for line in UNITS.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def search(q: str, *, limit: int = 8) -> dict:
    """检索她的记忆；查不到就如实说没存到（不许编）。"""
    low = (q or "").strip().lower()
    if not low:
        return {"ok": False, "原因": "给个关键词"}
    hits = [u for u in units() if low in json.dumps(u, ensure_ascii=False).lower()][:limit]
    return {"ok": bool(hits), "问": q, "命中": len(hits), "条目": hits,
            "判": "有依据" if hits else "记忆里没存到（如实说，不编）"}


def stats() -> dict:
    us = units()
    by = {}
    for u in us:
        by[u.get("类别", "?")] = by.get(u.get("类别", "?"), 0) + 1
    return {"记忆条数": len(us), "分类": by, "文件": str(UNITS.relative_to(ROOT)),
            "口径": "全部从盘上真件抽取、带来源；答不出就承认没存到"}


__all__ = ["DIR", "UNITS", "collect", "ingest", "units", "search", "stats", "LEDGER"]
