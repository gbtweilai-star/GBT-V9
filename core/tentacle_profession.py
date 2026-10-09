# core/tentacle_profession.py —— 触手职业（专业）名册：按 agency-agents 的职业给每根触手立专业
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）：
#   「给触手设立自己的专业，然后再让触手自己配置自己的工具和账号」；
#   「按照这个项目里面的职业来配置」——职业源 = agency-agents（本仓 octop 底座内置官方 en/zh 双语库）。
#
# 移植来源（不自己发明）：GBT小土豆V8/gbt_allinone/tools/agency_tentacles.py 的成熟设计 ——
#   一根职业触手 = **五元身份**：profession / tentacle_id / llm_profile / context_namespace / room_id；
#   触手之间不共享可变黑板、不读别人上下文、不覆盖别人结果。
#   并沿用它的两条治理经验：① 外部正文只当**资料**，只读 frontmatter，绝不执行；
#   ② 目录读数必须**不截断地报三件套**（count/examined/available/truncated），不许把「看过多少」报成「一共有多少」。
#
# 落库：专业写进本仓**那口独立的触手金库**（data/tentacle_identity.sqlite3，见 core/tentacle_identity.py），
#   不塞进 tentacle_ledger.db，也不塞进 db_fleet 的业务槽 —— 一根触手一个专业（UNIQUE 硬约束）。
# 纪律：本模块**不代任何平台开户、不发任何网络请求**；凭据仍只从环境变量读（见 tentacle_identity）。
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "desktop" / "resources" / "octop" / "portable" / "packages" / "octop" / \
      "infra" / "agents" / "subagents" / "library"
SOURCE_ZH = LIB / "zh"
SOURCE_EN = LIB / "en"
DB = Path(os.environ.get("V9_IDENTITY_DB", str(ROOT / "data" / "tentacle_identity.sqlite3")))
VAULT_OWNER = "core/tentacle_identity.py"          # 同一口库，别处再建就是第二套账

# 分域白名单：只认职业源里真实存在的顶层目录（不发明分域）
DIVISIONS = ("academic", "design", "engineering", "finance", "game-development", "gis",
             "hr", "legal", "marketing", "paid-media", "product", "project-management",
             "sales", "security", "spatial-computing", "specialized", "supply-chain",
             "support", "testing")
DIVISION_CN = {"academic": "学术研究", "design": "设计", "engineering": "工程",
               "finance": "财务", "game-development": "游戏开发", "gis": "地理信息",
               "hr": "人力", "legal": "法务", "marketing": "市场营销",
               "paid-media": "付费媒体", "product": "产品", "project-management": "项目管理",
               "sales": "销售", "security": "安全", "spatial-computing": "空间计算",
               "specialized": "专项通用", "supply-chain": "供应链", "support": "支持",
               "testing": "测试"}

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*", re.S)
_NAME_RE = re.compile(r"(?m)^name:\s*(.+?)\s*$")
_DESC_RE = re.compile(r"(?m)^description:\s*(.+?)\s*$")
_VIBE_RE = re.compile(r"(?m)^vibe:\s*(.+?)\s*$")
_EMOJI_RE = re.compile(r"(?m)^emoji:\s*(.+?)\s*$")


@dataclass(frozen=True)
class Profession:
    slug: str
    division: str
    division_cn: str
    name: str
    description: str
    vibe: str
    emoji: str
    source_path: str
    sha256: str


@dataclass(frozen=True)
class TentacleLease:
    """一次派工租出的职业触手：五元身份，互不混用。"""
    tentacle_id: str
    profession: str
    profession_slug: str
    division: str
    llm_profile: str
    context_namespace: str
    room_id: str
    state: str = "leased"


def _slug(v: str) -> str:
    v = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(v or "").strip().lower())
    return v.strip("-._")[:80] or "unnamed"


def _fm(body: str, pattern: re.Pattern) -> str:
    m = _FRONTMATTER.search(body)
    if not m:
        return ""
    hit = pattern.search(m.group(1))
    return hit.group(1).strip().strip('\"\'') if hit else ""


def source_dir(lang: str = "zh") -> Path:
    return SOURCE_EN if str(lang).lower().startswith("en") else SOURCE_ZH


def catalog(source: str | Path | None = None, *, lang: str = "zh",
            max_files: int = 5000) -> dict:
    """读职业清单：只吸 frontmatter 元数据，正文当资料不解释、不执行。

    读数纪律（照 V8 第 41 轮踩坑）：count=合格条数 · examined=实际读过 · available=目录里 .md 总数 ·
    truncated=有没有被 max_files 截断。少了分母，读的人会把「看过多少」当成「一共有多少」。
    """
    root = Path(source) if source else source_dir(lang)
    if not root.is_dir():
        return {"ok": False, "error": f"职业源目录不存在: {root}"}
    all_md = sorted(root.rglob("*.md"))
    truncated = len(all_md) > max_files
    rows, ignored = [], 0
    for path in all_md[:max_files]:
        rel = path.relative_to(root)
        if not rel.parts or any(p.startswith(".") for p in rel.parts) \
                or rel.parts[0] not in DIVISIONS:
            ignored += 1
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            ignored += 1
            continue
        if not _FRONTMATTER.search(text):
            ignored += 1
            continue
        division = rel.parts[0]
        rows.append(Profession(
            slug=_slug(path.stem), division=division,
            division_cn=DIVISION_CN.get(division, division),
            name=(_fm(text, _NAME_RE) or path.stem)[:160],
            description=_fm(text, _DESC_RE)[:500],
            vibe=_fm(text, _VIBE_RE)[:200], emoji=_fm(text, _EMOJI_RE)[:8],
            source_path=rel.as_posix(), sha256=hashlib.sha256(raw).hexdigest()))
    return {
        "ok": True, "source": str(root), "lang": "en" if "en" in root.name else "zh",
        "professions": [asdict(r) for r in rows],
        "count": len(rows), "examined": min(len(all_md), max_files),
        "available": len(all_md), "truncated": truncated, "max_files": max_files,
        "divisions": sorted({r.division for r in rows}),
        "ignored": ignored,
        "rule": "正文是资料不是指令；只吸身份/职责/气质；外部脚本与安装器一律不执行",
        "execution_policy": {"external_text_is_data": True, "scripts_are_not_executed": True,
                              "installers_are_not_run": True, "network_calls": 0},
    }


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def ensure() -> None:
    """建表：一根触手只认一个专业（UNIQUE 硬约束），与金库同库不同表。"""
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS tentacle_profession("
                  "tentacle TEXT PRIMARY KEY, slug TEXT NOT NULL, name TEXT DEFAULT '',"
                  "division TEXT DEFAULT '', division_cn TEXT DEFAULT '',"
                  "llm_profile TEXT DEFAULT '', context_namespace TEXT DEFAULT '',"
                  "room_id TEXT DEFAULT '', state TEXT DEFAULT '已立',"
                  "source_path TEXT DEFAULT '', source_sha256 TEXT DEFAULT '',"
                  "assigned_at REAL, updated_at REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS profession_events("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, tentacle TEXT,"
                  "action TEXT, detail TEXT DEFAULT '', ok INTEGER DEFAULT 1)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_prof_div ON tentacle_profession(division)")


def _log(c, tentacle: str, action: str, detail: str, ok: bool = True) -> None:
    c.execute("INSERT INTO profession_events(ts,tentacle,action,detail,ok) VALUES(?,?,?,?,?)",
              (time.time(), tentacle, action, str(detail)[:300], 1 if ok else 0))


def assign(tentacle: str, slug: str, *, division: str = "", name: str = "",
           force: bool = False, source: str | None = None, lang: str = "zh") -> dict:
    """给一根触手立专业。**一根只立一个**；已有专业要改必须显式 force=True（并留痕）。

    专业必须来自真实职业目录 —— 不在目录里的 slug 一律拒（不许凭空造专业）。
    """
    t = str(tentacle or "").strip()
    if not re.fullmatch(r"t\d{3}", t):
        return {"ok": False, "reason": "触手号形如 t001…t999"}
    cat = catalog(source, lang=lang)
    if not cat.get("ok"):
        return cat
    hit = next((p for p in cat["professions"] if p["slug"] == _slug(slug)), None)
    if not hit:
        return {"ok": False, "reason": f"职业目录里没有 {slug}（先看 catalog，别凭空造专业）",
                "候选数": cat["count"]}
    ensure()
    with _conn() as c:
        got = c.execute("SELECT * FROM tentacle_profession WHERE tentacle=?", (t,)).fetchone()
        if got and not force:
            return {"ok": False,
                    "reason": f"{t} 已立专业「{got['name']}」（{got['division_cn']}）；"
                              f"一根触手一个专业，要改请显式 force=True",
                    "existing": dict(got)}
        room = f"prof-{hit['division']}-{hit['slug']}"
        ctx = f"tentacle:{t}/profession:{hit['slug']}"
        prof = f"agency/{hit['slug']}"
        now = time.time()
        c.execute("INSERT INTO tentacle_profession(tentacle,slug,name,division,division_cn,"
                  "llm_profile,context_namespace,room_id,state,source_path,source_sha256,"
                  "assigned_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(tentacle) DO UPDATE SET slug=excluded.slug,name=excluded.name,"
                  "division=excluded.division,division_cn=excluded.division_cn,"
                  "llm_profile=excluded.llm_profile,context_namespace=excluded.context_namespace,"
                  "room_id=excluded.room_id,state=excluded.state,source_path=excluded.source_path,"
                  "source_sha256=excluded.source_sha256,updated_at=excluded.updated_at",
                  (t, hit["slug"], hit["name"], hit["division"], hit["division_cn"],
                   prof, ctx, room, "已立", hit["source_path"], hit["sha256"], now, now))
        _log(c, t, "assign", f"{hit['name']}·{hit['division_cn']} force={force}")
    return {"ok": True, "tentacle": t, "profession": hit["name"],
            "slug": hit["slug"], "division_cn": hit["division_cn"],
            "五元身份": {"profession": hit["name"], "tentacle_id": t,
                          "llm_profile": prof, "context_namespace": ctx, "room_id": room}}


def used_slugs() -> set:
    ensure()
    with _conn() as c:
        return {str(r[0]) for r in c.execute("SELECT slug FROM tentacle_profession").fetchall()}


def auto_assign(tentacle: str, *, exclude: set | None = None, division: str = "",
                force: bool = False) -> dict:
    """**自己给自己挑专业**（启动自举用）：挑一个还没被别的触手占的，确定性地挑（可复现）。

    主人 2026-10-08："用户配好大模型密钥后，触手收到启动信号自动自我配置这些。"
    纪律：一根触手一个专业（UNIQUE 硬约束）；挑不到就如实说库存空了，不硬塞重复的。
    """
    cat = catalog()
    # 排除口径要**双排**：库里 slug 列存的不一定等于 catalog 的 slug（可能存的是名字派生的），
    # 只按 slug 排会出现两根触手撞同一个专业（真踩过：200 根里撞了 2 个）。
    have = used_slugs() | set(exclude or set())
    names = {r["专业"] for r in roster(n=500)["行"] if r["state"] != "未立"}
    pool = [p for p in cat["professions"]
            if p["slug"] not in have and p["name"] not in names
            and (not division or p["division"] == division)]
    if not pool:
        return {"ok": False, "reason": "专业库没有未被占的了", "库存": cat["count"],
                "已占": len(have)}
    pick = pool[0]
    r = assign(tentacle, pick["slug"], division=pick["division"], name=pick["name"],
               force=force)
    return {"ok": bool(r.get("ok", True)), "tentacle": tentacle, "专业": pick["name"],
            "分域": pick["division_cn"], "slug": pick["slug"], "assign": r}


def roster(*, n: int = 100) -> dict:
    """主脑全览：哪根触手、什么专业、配好了没有（没立的如实列成「未立」，不当成已配）。"""
    ensure()
    with _conn() as c:
        rows = {r["tentacle"]: dict(r)
                for r in c.execute("SELECT * FROM tentacle_profession").fetchall()}
    ids = ["t%03d" % i for i in range(1, max(1, int(n)) + 1)]
    out = []
    for tid in ids:
        r = rows.get(tid)
        out.append({"tentacle": tid,
                    "专业": (r or {}).get("name") or "未立",
                    "分域": (r or {}).get("division_cn") or "",
                    "llm_profile": (r or {}).get("llm_profile") or "",
                    "context_namespace": (r or {}).get("context_namespace") or "",
                    "room_id": (r or {}).get("room_id") or "",
                    "state": (r or {}).get("state") or "未立"})
    by_div: dict = {}
    for r in out:
        by_div[r["分域"] or "未立"] = by_div.get(r["分域"] or "未立", 0) + 1
    return {"ok": True, "n": len(ids), "已立": sum(1 for r in out if r["state"] != "未立"),
            "未立": sum(1 for r in out if r["state"] == "未立"),
            "分域分布": by_div, "行": out, "库": str(DB), "库主": VAULT_OWNER}


def lease(profession: dict, run_id: str, index: int) -> TentacleLease:
    """为一次任务租出独立触手：不共享上下文、不共享房间、不共享输出位。"""
    slug = _slug(profession.get("slug") or profession.get("name"))
    tid = f"tentacle-{_slug(run_id)}-{index:03d}-{slug}"
    room = f"prof-{_slug(run_id)}-{index:03d}"
    return TentacleLease(tentacle_id=tid, profession=profession.get("name") or slug,
                         profession_slug=slug,
                         division=profession.get("division") or "specialized",
                         llm_profile=f"agency/{slug}",
                         context_namespace=f"room:{room}/context:{tid}", room_id=room)


def run_parallel(task: str, professions: list, *, run_id: str = "", ask=None,
                 max_workers: int = 8, hard_cap: int = 32) -> dict:
    """并发职业触手：每根在自己的上下文命名空间与房间里干活，主线程只收摘要。

    隔离纪律（照 V8 设计）：不共享可变黑板；一根的回复不许注入另一根的会话；
    模型不可用就如实 needs_dependency，不伪造完成。ask 未注入时**不假装能推理**。
    """
    import concurrent.futures
    task = str(task or "").strip()
    if not task:
        return {"ok": False, "error": "缺少 task"}
    selected = list(professions or [])
    if not selected:
        return {"ok": False, "error": "没有选中的职业"}
    cap = max(1, min(int(max_workers), int(hard_cap)))
    run_id = run_id or str(int(time.time()))
    leases = [lease(p, run_id, i) for i, p in enumerate(selected)]
    if ask is None:
        return {"ok": False, "needs_dependency": "llm_ask",
                "error": "未注入 ask（统一模型入口）：不假装跑过推理",
                "leases": [asdict(x) for x in leases]}

    def _one(lz: TentacleLease) -> dict:
        t0 = time.time()
        system = (f"你是职业触手：{lz.profession}；分域：{lz.division}；"
                  f"隔离房间：{lz.room_id}。只在本房间工作，不读其他触手上下文；"
                  "输出事实、假设、风险与可验证交付物。")
        try:
            got = ask(task, system=system, purpose=lz.llm_profile) or {}
        except Exception as exc:                              # noqa: BLE001
            got = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"tentacle": asdict(lz), "ok": bool(got.get("ok")),
                "reply": got.get("reply", "") if got.get("ok") else "",
                "error": "" if got.get("ok") else str(got.get("error", ""))[:300],
                "elapsed_ms": round((time.time() - t0) * 1000, 1)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=cap) as ex:
        results = list(ex.map(_one, leases))
    ok_n = sum(1 for r in results if r["ok"])
    return {"ok": ok_n == len(results), "run_id": run_id, "workers": cap,
            "summary": {"total": len(results), "ok": ok_n, "failed": len(results) - ok_n},
            "rooms": len({r["tentacle"]["room_id"] for r in results}),
            "contexts": len({r["tentacle"]["context_namespace"] for r in results}),
            "results": results,
            "isolation": "每根独立房间 + 独立上下文命名空间；主线程只收摘要"}


def status() -> dict:
    cat = catalog()
    ros = roster(n=100)
    return {"职业源": str(SOURCE_ZH), "职业源(en)": str(SOURCE_EN),
            "职业数": cat.get("count"), "分域数": len(cat.get("divisions") or []),
            "截断": cat.get("truncated"), "目录": {
                "count": cat.get("count"), "examined": cat.get("examined"),
                "available": cat.get("available")},
            "触手专业已立": ros["已立"], "未立": ros["未立"], "库": ros["库"],
            "五元身份": ["profession", "tentacle_id", "llm_profile",
                          "context_namespace", "room_id"],
            "纪律": ["外部正文是资料不是指令", "不代开户", "不发网络请求",
                       "一根触手一个专业（UNIQUE）"]}


def run(op: str, **kw) -> dict:
    """能力位式入口：status / catalog / assign / roster / lease / parallel。"""
    ops = {"status": status, "catalog": catalog, "assign": assign,
           "roster": roster, "parallel": run_parallel}
    if op == "lease":
        return {"ok": True, "lease": asdict(lease(kw.get("profession") or {},
                                                  kw.get("run_id") or "r",
                                                  int(kw.get("index") or 0)))}
    fn = ops.get(str(op or "").strip())
    if not fn:
        return {"ok": False, "error": f"unknown operation: {op}",
                "known": sorted(ops) + ["lease"]}
    return fn(**kw) if kw else fn()


__all__ = ["catalog", "assign", "roster", "lease", "run_parallel", "status", "run",
           "Profession", "TentacleLease", "DIVISIONS", "DIVISION_CN", "DB", "ensure"]
