# core/octop_bridge.py —— Octop ↔ V9 能力桥：真读 Octop 目录，一对一双向绑到 100 根触手
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人 2026-10-06：要把 Octop 的**所有 AI 能力与职业**精准接到触手上，1:1 双向绑定，并出
#   可视化页面 + 连接状态图表 + 总能力面板与指标。
#
# 诚实纪律：
#   · 这里读的是 Octop **安装目录里的真实目录数据**（divisions.json / experts/library / plugins/bundled），
#     不是我自己编的能力名；读不到就如实报路径缺失。
#   · 我们**不执行 Octop 的代码**（它的模块需要 octop_harness，硬 import 会炸）；只读它的目录与清单。
#   · 绑定仍是"两个方向各一行"（t2c / c2t），与云插件、数据库编队同一套口径。
from core.swallow import swallow as _swallow
import json
import os
import time
import uuid
from pathlib import Path

from senses.sqldialect import txn

# 候选根：已安装的 APP → 项目内的打包资源
ROOT_CANDIDATES = (
    r"C:\Users\ADMIN\AppData\Local\Programs\GBT小土豆V9\resources\octop\portable",
    r"C:\Users\ADMIN\AppData\Local\Programs\MuseWork\resources",
    str(Path(__file__).resolve().parents[1] / "desktop" / "resources" / "octop" / "portable"),
)
PKG_REL = Path("packages") / "octop"


def octop_root() -> dict:
    """找到 Octop 的 portable 根（真路径），找不到就如实说没有。"""
    for c in ROOT_CANDIDATES:
        p = Path(c)
        if (p / PKG_REL).is_dir():
            return {"root": str(p), "pkg": str(p / PKG_REL), "found": True}
    return {"root": "", "pkg": "", "found": False}


def _dirs(p: Path) -> list:
    if not p.is_dir():
        return []
    return sorted(d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith("_"))


def catalog() -> dict:
    """Octop 真实能力目录 + V9 自己的工具面。全部读文件，不执行它的代码。"""
    r = octop_root()
    if not r["found"]:
        return {"found": False, "reason": "找不到 Octop portable 根", "candidates": ROOT_CANDIDATES}
    pkg = Path(r["pkg"])
    out: dict = {"found": True, "root": r["root"]}

    # ① 智能体分域（19 域）+ 域内智能体（真文件：library/zh/<域>/*.md）
    div_file = pkg / "infra" / "agents" / "subagents" / "library" / "zh" / "divisions.json"
    lib_dir = div_file.parent
    divisions = []
    try:
        meta = json.loads(div_file.read_text(encoding="utf-8")).get("divisions") or {}
        for key, v in meta.items():
            label = (v or {}).get("label") or key
            agents = sorted(f.stem for f in (lib_dir / key).glob("*.md"))                 if (lib_dir / key).is_dir() else []
            divisions.append({"id": key, "name": label, "agents": agents})
    except Exception as exc:                                   # noqa: BLE001
        out["divisions_error"] = f"{type(exc).__name__}: {exc}"
    out["divisions"] = divisions

    # ② 专家包 / 内置插件 / 技能
    out["experts"] = _dirs(pkg / "infra" / "agents" / "experts" / "library")
    out["plugins_bundled"] = _dirs(pkg / "infra" / "agents" / "plugins" / "bundled")
    out["skills"] = [p.stem for p in sorted((pkg / "infra" / "skills").glob("*.py"))
                     if p.stem not in ("__init__",)]

    # ③ V9 自己的工具面（我们播种的插件）
    v9_tools = []
    try:
        seed = Path(__file__).resolve().parents[1] / "desktop" / "resources" / "octop-seed" \
            / "plugins" / "gbt-potato-v9" / "main.py"
        import re
        v9_tools = sorted(set(re.findall(r'ctx\.tool\("([a-z0-9_]+)"', seed.read_text(encoding="utf-8"))))
    except Exception as e:
        _swallow(__file__, e)
    out["v9_tools"] = v9_tools
    out["counts"] = {
        "divisions": len(divisions),
        "agents": sum(len(d["agents"]) for d in divisions),
        "experts": len(out["experts"]),
        "plugins": len(out["plugins_bundled"]),
        "skills": len(out["skills"]),
        "v9_tools": len(v9_tools),
    }
    out["counts"]["total_capabilities"] = sum(
        out["counts"][k] for k in ("agents", "experts", "plugins", "skills", "v9_tools"))
    return out


def page_ids() -> list:
    """Octop 的**原生页面**也要作为能力位参与绑定（主人 2026-10-08：每一页都要登记）。

    为什么把页面算能力：页面本来就是"可被操控/可被内嵌/可被校验"的能力位——
    触手要能代你打开、能读到它、能验它是不是真在跑。以前只绑了 agent/expert/
    plugin/skill/v9tool，64 个原生页面一个都没绑（漏配）。
    """
    try:
        from core import octop_fusion as OF
        return [f"page:{p[0]}" for p in OF.PAGES]
    except Exception:                                          # noqa: BLE001
        return []


def capability_ids() -> list:
    """把 Octop 的能力拍平成 1:1 可绑的 id（域/专家/插件/技能/V9 工具 + 原生页面）。"""
    c = catalog()
    if not c.get("found"):
        return []
    ids = []
    for d in c["divisions"]:
        for a in d["agents"]:
            ids.append(f"agent:{d['id']}/{a}")
    ids += [f"expert:{x}" for x in c["experts"]]
    ids += [f"plugin:{x}" for x in c["plugins_bundled"]]
    ids += [f"skill:{x}" for x in c["skills"]]
    ids += [f"v9tool:{x}" for x in c["v9_tools"]]
    ids += page_ids()
    return ids


def coverage() -> dict:
    """覆盖度守卫：有没有"没绑上"的能力或页面（防遗漏，主人明确要求一一对齐）。

    读真表，不猜：把 capability_ids() 与 octop_binding 里的 c2t 目标做差集。
    """
    import sqlite3
    from pathlib import Path as _P
    db = _P(__file__).resolve().parent.parent / "data" / "gbt_v9.sqlite3"
    want = set(capability_ids())
    got = set()
    if db.is_file():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                got = {r[0] for r in con.execute(
                    "SELECT DISTINCT tentacle FROM octop_binding WHERE direction='c2t'"
                ).fetchall()}
            finally:
                con.close()
        except Exception as e:
            _swallow(__file__, e)
    pages = set(page_ids())
    missing = sorted(want - got)
    return {"应绑能力": len(want), "已绑": len(want & got), "缺": missing,
            "缺数量": len(missing),
            "页面应绑": len(pages), "页面已绑": len(pages & got),
            "页面缺": sorted(pages - got)[:10],
            "齐不齐": not missing}


def families() -> dict:
    """按族汇总数量（页面图表用）。"""
    c = catalog()
    if not c.get("found"):
        return {}
    return {"Octop 智能体分域": c["counts"]["divisions"],
            "Octop 智能体": c["counts"]["agents"],
            "Octop 专家包": c["counts"]["experts"],
            "Octop 内置插件": c["counts"]["plugins"],
            "Octop 技能模块": c["counts"]["skills"],
            "V9 工具（播种插件）": c["counts"]["v9_tools"]}


class OctopBridge:
    """一对一绑定：每项 Octop/V9 能力 ↔ 每根触手（两方向各一行）。"""

    def __init__(self, ledger=None, *, n_tentacles: int = 100):
        self.led, self.n = ledger, int(n_tentacles)
        self._init()

    def tentacle_ids(self) -> list:
        return [f"t{i:03d}" for i in range(1, self.n + 1)]

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _init(self):
        """同步侧建表（只为纯 sqlite 同步账本兜底；异步适配器走 ainit）。

        这里**不动 `_inited` 旗标**：旗标只由 ainit 掌握，避免同步路径"看似成功"
        导致异步侧跳过必要的建表。
        """
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS octop_binding ("
                            "bind_id TEXT PRIMARY KEY, tentacle TEXT, capability TEXT,"
                            " direction TEXT, state TEXT, at TEXT)")
        except Exception as e:
            _swallow(__file__, e)

    async def ainit(self):
        """建表（幂等）。**只在实例生命周期内真正执行一次**。

        病因（2026-10-07 真机踩到）：早先每次 `abind()` 都无条件重跑本方法，
        满配 `abind_all()` = 346 能力 × 100 触手 × 2 方向 = 34,600 次
        `CREATE TABLE IF NOT EXISTS` + 开事务，实测卡到 5 分钟以上跑不完。
        表结构固定，没必要反复建；用 `_inited` 缓存，跑一次即可。
        """
        if self.led is None or getattr(self, "_inited", False):
            return
        async with self.led.transaction():
            await self.led.execute("CREATE TABLE IF NOT EXISTS octop_binding ("
                                   "bind_id TEXT PRIMARY KEY, tentacle TEXT,"
                                   " capability TEXT, direction TEXT, state TEXT, at TEXT)")
        self._inited = True

    async def abind(self, tentacle: str, capability: str, *,
                    skip_existing: bool = True) -> dict:
        """一对一绑定（两方向）。**幂等**：已存在的方向不再重复插入。

        为什么要幂等：重复跑 bind_all 会不断插重复行，用量统计就虚高
        （真机踩到过：满配理论 67800 行，实际涨到 18 万行）。这里按
        (触手, 能力, 方向) 先查后插。
        """
        if capability not in capability_ids():
            return {"ok": False, "reason": f"未知能力：{capability}"}
        if tentacle not in self.tentacle_ids():
            return {"ok": False, "reason": f"未知触手：{tentacle}"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        added = []
        for direction, a, b in (("t2c", tentacle, capability), ("c2t", capability, tentacle)):
            if skip_existing:
                got = await self.led.fetch_all(
                    "SELECT bind_id FROM octop_binding WHERE tentacle=? AND capability=? AND direction=? LIMIT 1",
                    (a, b, direction))
                if got:
                    continue                              # 已绑过：跳过（幂等）
            await self.led.execute(
                "INSERT INTO octop_binding (bind_id, tentacle, capability, direction,"
                " state, at) VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], a, b, direction, "bound", self._now()))
            added.append(direction)
        return {"ok": True, "tentacle": tentacle, "capability": capability,
                "directions": ["t2c", "c2t"], "bidirectional": True,
                "added": added, "already": 2 - len(added)}

    async def adedupe(self) -> dict:
        """清理历史重复行：同一 (触手, 能力, 方向) 只留最早一行。"""
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        before = await self.led.fetch_all("SELECT COUNT(*) AS n FROM octop_binding")
        await self.led.execute(
            "DELETE FROM octop_binding WHERE rowid NOT IN (SELECT MIN(rowid) FROM octop_binding GROUP BY tentacle, capability, direction)")
        after = await self.led.fetch_all("SELECT COUNT(*) AS n FROM octop_binding")
        b = (before[0]["n"] if before else None)
        a = (after[0]["n"] if after else None)
        return {"ok": True, "rows_before": b, "rows_after": a,
                "removed": (b - a) if (b is not None and a is not None) else None}

    async def abind_all(self, *, tentacles: int | None = None) -> dict:
        n = int(tentacles or self.n)
        caps = capability_ids()
        bound, failed = 0, []
        for t in self.tentacle_ids()[:n]:
            for c in caps:
                r = await self.abind(t, c)
                if r.get("ok"):
                    bound += 1
                else:
                    failed.append({"t": t, "cap": c, "why": r.get("reason")})
        return {"ok": not failed, "bound_pairs": bound, "rows": bound * 2,
                "tentacles": n, "capabilities": len(caps), "failed": failed[:5]}

    async def astate(self) -> dict:
        out = {"capabilities": len(capability_ids()), "tentacles": self.n,
               "bindings": 0, "caps_bound": 0, "tentacles_bound": 0,
               "symmetric": True, "expected_pairs": None}
        out["expected_pairs"] = out["capabilities"] * self.n
        if self.led is None:
            return {**out, "reason": "no_ledger"}
        await self.ainit()
        rows = await self.led.fetch_all(
            "SELECT tentacle, capability, direction FROM octop_binding")
        t2c, c2t = set(), set()
        for r in rows or []:
            (t2c if r["direction"] == "t2c" else c2t).add((r["tentacle"], r["capability"]))
        out["bindings"] = len(t2c)
        out["caps_bound"] = len({c for _, c in t2c})
        out["tentacles_bound"] = len({t for t, _ in t2c})
        missing = [x for x in t2c if (x[1], x[0]) not in c2t]
        out["asymmetric"] = missing[:3]
        out["symmetric"] = not missing
        out["coverage"] = (round(len(t2c) / out["expected_pairs"], 4)
                           if out["expected_pairs"] else None)
        return out


__all__ = ["octop_root", "catalog", "capability_ids", "families", "OctopBridge"]
