# core/cloud_plugins.py —— Cloudflare Workers AI 插件中枢（10 族 × 10 槽 = 100）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人纠偏（2026-10-06）：插件 = **Cloudflare Workers AI 模型**（无服务器 + GPU 加速，带启用开关）。
#   100 个按 10 个一组排布；每根触手(1..100)与每个插件**互相双向绑定**；触手 1 能连任意插件；
#   任意插件之间也互通（全互通网格）。
#
# 诚实纪律：
#   · 槽位只放官方目录真实存在的模型名（清单 2026-10-06 拉自 developers.cloudflare.com/workers-ai/models）；
#     某族官方不足 10 个 → 显式 reserved 槽（写明原因），**绝不编造 @cf/ id**；
#   · 拿不准的 id 留 None + needs_id=True，用 sync_from_snapshot() 与你自己 curl 下来的目录核对；
#   · 本模块**不持有任何凭据、不发任何网络请求**（拿目录这步交给你，比我们替你保管 token 安全）。
from core.swallow import swallow as _swallow
import json
import os
import time
import uuid
from pathlib import Path

from senses.sqldialect import txn

GROUPS: tuple = ("text-generation", "embedding", "image-generation", "vision",
                 "asr", "tts", "translation", "classification", "code", "guard")
GROUP_CN: dict = {"text-generation": "文本生成", "embedding": "向量嵌入",
                  "image-generation": "图像生成", "vision": "图像理解",
                  "asr": "语音识别", "tts": "语音合成", "translation": "翻译",
                  "classification": "分类与重排", "code": "代码", "guard": "安全守卫"}

# 每族真实模型（slug, 中文名, @cf/ id 或 None）。不足 10 个的族由 _pad() 自动补 reserved 槽。
_REAL: dict = {
    "text-generation": [
        ("llama-3.2-1b-instruct", "小模型快答", "@cf/meta/llama-3.2-1b-instruct"),
        ("llama-3.2-3b-instruct", "轻量对话", "@cf/meta/llama-3.2-3b-instruct"),
        ("llama-3.1-8b-instruct-fp8", "主力对话", "@cf/meta/llama-3.1-8b-instruct-fp8"),
        ("llama-3.3-70b-instruct-fp8-fast", "大模型快版", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        ("llama-4-scout-17b-16e-instruct", "MoE 主力", "@cf/meta/llama-4-scout-17b-16e-instruct"),
        ("mistral-small-3.1-24b-instruct", "均衡对话", "@cf/mistralai/mistral-small-3.1-24b-instruct"),
        ("qwen3-30b-a3b-fp8", "中文 MoE", "@cf/qwen/qwen3-30b-a3b-fp8"),
        ("qwq-32b", "推理专精", "@cf/qwen/qwq-32b"),
        ("deepseek-r1-distill-qwen-32b", "蒸馏推理", "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"),
        ("gpt-oss-20b", "开源 GPT", "@cf/openai/gpt-oss-20b"),
    ],
    "embedding": [
        ("bge-small-en-v1.5", "小嵌入", "@cf/baai/bge-small-en-v1.5"),
        ("bge-base-en-v1.5", "基准嵌入", "@cf/baai/bge-base-en-v1.5"),
        ("bge-large-en-v1.5", "大嵌入", "@cf/baai/bge-large-en-v1.5"),
        ("bge-m3", "多语嵌入", "@cf/baai/bge-m3"),
        ("embeddinggemma-300m", "轻量多语嵌入", "@cf/google/embeddinggemma-300m"),
        ("plamo-embedding-1b", "日英嵌入", "@cf/pfnet/plamo-embedding-1b"),
        ("qwen3-embedding-0.6b", "中文嵌入", "@cf/qwen/qwen3-embedding-0.6b"),
    ],
    "image-generation": [
        ("flux-1-schnell", "快速出图", "@cf/black-forest-labs/flux-1-schnell"),
        ("flux-2-dev", "出图主力", "@cf/black-forest-labs/flux-2-dev"),
        ("flux-2-klein-4b", "出图小版", "@cf/black-forest-labs/flux-2-klein-4b"),
        ("flux-2-klein-9b", "出图中版", "@cf/black-forest-labs/flux-2-klein-9b"),
        ("dreamshaper-8-lcm", "快速写实", "@cf/lykon/dreamshaper-8-lcm"),
        ("lucid-origin", "创意出图", "@cf/leonardo/lucid-origin"),
        ("phoenix-1.0", "艺术出图", "@cf/leonardo/phoenix-1.0"),
        ("stable-diffusion-v1-5-inpainting", "局部重绘", "@cf/runwayml/stable-diffusion-v1-5-inpainting"),
        ("stable-diffusion-xl-base-1.0", "SDXL 基准", "@cf/stabilityai/stable-diffusion-xl-base-1.0"),
        ("stable-diffusion-xl-lightning", "SDXL 闪电", "@cf/bytedance/stable-diffusion-xl-lightning"),
    ],
    "vision": [
        ("llava-1.5-7b-hf", "图问答", "@cf/llava-hf/llava-1.5-7b-hf"),
        ("moondream3.1-9B-A2B", "轻量看图", "@cf/moondream/moondream3.1-9B-A2B"),
        ("llama-3.2-11b-vision-instruct", "看图对话", "@cf/meta/llama-3.2-11b-vision-instruct"),
    ],
    "asr": [
        ("whisper", "通用转写", "@cf/openai/whisper"),
        ("whisper-large-v3-turbo", "高速转写", "@cf/openai/whisper-large-v3-turbo"),
        ("whisper-tiny-en", "极速英文", "@cf/openai/whisper-tiny-en"),
        ("nova-3", "高精度转写", "@cf/deepgram/nova-3"),
        ("flux", "实时转写（Deepgram，与出图 flux 同名不同物）", "@cf/deepgram/flux"),
    ],
    "tts": [
        ("aura-1", "自然女声（可做文静口音基底）", "@cf/deepgram/aura-1"),
        ("aura-2-en", "英文女声", "@cf/deepgram/aura-2-en"),
        ("aura-2-es", "西语女声", "@cf/deepgram/aura-2-es"),
        ("melotts", "多语轻量", "@cf/myshell-ai/melotts"),
    ],
    "translation": [
        ("indictrans2-en-indic-1B", "英印互译", "@cf/ai4bharat/indictrans2-en-indic-1B"),
        ("m2m100-1.2b", "多语互译", "@cf/meta/m2m100-1.2b"),
    ],
    "classification": [
        ("bge-reranker-base", "重排器", "@cf/baai/bge-reranker-base"),
        ("distilbert-sst-2-int8", "情感分类", "@cf/huggingface/distilbert-sst-2-int8"),
        ("resnet-50", "图像分类", "@cf/microsoft/resnet-50"),
    ],
    "code": [
        ("qwen2.5-coder-32b-instruct", "代码主力", "@cf/qwen/qwen2.5-coder-32b-instruct"),
        ("kimi-k2.7-code", "代码专精", "@cf/moonshotai/kimi-k2.7-code"),
    ],
    "guard": [
        ("llama-guard-3-8b", "内容守卫", "@cf/meta/llama-guard-3-8b"),
        ("smart-turn-v2", "轮次判断", "@cf/pipecat-ai/smart-turn-v2"),
    ],
}
RESERVED = ("reserved", "预留槽（官方该族不足 10 个，留待新增）", None)


def _pad(items: list) -> list:
    """补齐到 10 槽：不足的显式 reserved（不编模型）。"""
    return list(items) + [RESERVED] * max(0, 10 - len(items))


CATALOG: dict = {g: _pad(_REAL[g]) for g in GROUPS}
CATALOG_SOURCE = "developers.cloudflare.com/workers-ai/models（2026-10-06 现拉）"

# ── 官方目录更新 → 自动补位 ──
# 官方目录在增长（每族模型会变多）。reserved 槽的补位走**快照文件**（不发任何请求）：
#   1) 你用文档里的 curl（带 CLOUDFLARE_ACCOUNT_ID）把 /ai/models/search 导出成
#      state/cf_official_models.json（官方原始返回，含 task.name 与 model 名）；
#   2) align_from_snapshot() 按 task→族映射，把官方**新增的真实模型**填进 reserved 槽；
#   3) 永不编造 id：官方目录没有就继续 reserved；面板/启动采样会定期检查快照变化。
_OVERLAY: dict = {}            # 族 -> [(slug, 中文名, cf_id)]，启动时从 state 读
_ALIGN_SOURCE = ""
ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent


def _task_family(task: str) -> str | None:
    t = str(task or "").lower()
    pairs = (("text generation", "text-generation"), ("text embeddings", "embedding"),
             ("image generation", "image-generation"), ("image-to-text", "vision"),
             ("text-to-speech", "tts"), ("speech-to-text", "asr"),
             ("automatic speech recognition", "asr"), ("translation", "translation"),
             ("text classification", "classification"), ("code generation", "code"),
             ("moderation", "guard"))
    for k, fam in pairs:
        if k in t:
            return fam
    return None


def align_from_snapshot(snapshot: dict) -> dict:
    """用官方目录快照自动补位：只填**官方真实存在**的 id，reserved 才允许被填。

    snapshot 形如 Cloudflare /ai/models/search 的原始返回：
      {"result": [{"name": "@cf/meta/llama-3.1-8b-instruct", "task": {"name": "Text Generation"}}, …]}
    幂等：已经在用的 id 不会重复占槽；官方目录没有的族继续 reserved（不编造）。
    """
    global _OVERLAY, _ALIGN_SOURCE
    got = (snapshot or {}).get("result") or (snapshot or {}).get("models") or []
    if not got:
        return {"ok": False, "reason": "快照里没有模型行（确认导出的是 /ai/models/search 返回）"}
    added, skipped = [], []
    for m in got:
        name = str((m or {}).get("name") or "").strip()
        task = str((((m or {}).get("task") or {}).get("name")) or "").strip()
        fam = _task_family(task)
        if not name or not fam:
            skipped.append(name or "?"); continue
        used = {slug for slug, _cn, cid in _REAL.get(fam, [])}
        dup = {slug for slug, _cn, _cid in _OVERLAY.get(fam, [])}
        if name in used or name in dup:
            skipped.append(name); continue          # 幂等：重复导入不堆积
        _OVERLAY.setdefault(fam, []).append(
            (name, f"官方目录新增（{task}）", name))
        added.append(f"{fam}:{name}")
    added, candidates = [], []
    if added is not None:
        pass
    # 逐族补位：族内还有 reserved 槽 → 填；族已满 10 槽 → 列为"换装候选"（换不换由主脑决策）
    for fam, items in _OVERLAY.items():
        base = list(_REAL.get(fam) or [])
        merged = list(base)
        res_i = [i for i, t in enumerate(merged) if t[0] == "reserved"]
        for idx, (slug, cn, cid) in enumerate(items):
            if res_i:
                i = res_i.pop(0)
                merged[i] = (slug, cn, cid)
                added.append(f"{fam}:{slug}")
            else:
                candidates.append({"族": fam, "官方新模型": slug,
                                   "说明": "该族已满 10 槽 → 换装候选（替换谁由主脑决策）"})
    if added:
        for fam in {f for f, _i in [(x.split(":")[0], None) for x in added]}:
            base = list(_REAL.get(fam) or [])
            merged = list(base)
            ov = _OVERLAY.get(fam) or []
            res_i = [i for i, t in enumerate(merged) if t[0] == "reserved"]
            for (slug, cn, cid), i in zip(ov, res_i):
                if i < len(merged):
                    merged[i] = (slug, cn, cid)
            CATALOG[fam] = _pad(merged)
        global PLUGIN_IDS
        PLUGIN_IDS = tuple(plugin_ids())
    try:
        from core import deploy_ledger as DL
        DL.record("deploy", "cloud_plugins:align",
                  detail=f"官方目录补位：新增 {len(added)} 个真实 id（跳过 {len(skipped)}）",
                  before="", after=json.dumps(added[:8], ensure_ascii=False), ok=bool(added))
    except Exception as e:
        _swallow(__file__, e)
    reg = registry()
    return {"ok": True, "新增": len(added), "跳过": len(skipped),
            "补位明细": added[:12], "换装候选": candidates,
            "现在": {"slots_real": reg["slots_real"],
                     "slots_reserved": reg["slots_reserved"],
                     "ids_confirmed": reg["ids_confirmed"]},
            "口径": "只填官方目录真实 id；官方没有的族继续 reserved（绝不编造）"}


def check_and_align(*, force: bool = False) -> dict:
    """事件驱动补位：state/cf_official_models.json 出现或更新时自动对齐一次。"""
    snap = ROOT.joinpath("state", "cf_official_models.json")
    if not snap.is_file():
        return {"ok": True, "align": False,
                "reason": "还没有官方目录快照（按模块头部的 curl 导出后放到 state/ 即自动补位）"}
    stamp = snap.stat().st_mtime
    mark = ROOT.joinpath("state", "cf_align_state.json")
    if not force and mark.is_file():
        try:
            last = json.loads(mark.read_text(encoding="utf-8") or "{}").get("snapshot_mtime")
            if last == stamp:
                return {"ok": True, "align": False, "reason": "快照未变化"}
        except (json.JSONDecodeError, OSError) as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

    try:
        snap_data = json.loads(snap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:             # noqa: BLE001
        return {"ok": False, "reason": f"快照解析失败：{type(exc).__name__}"}
    r = align_from_snapshot(snap_data)
    try:
        mark_p = ROOT.joinpath("state", "cf_align_state.json")
        mark_p.parent.mkdir(parents=True, exist_ok=True)
        mark_p.write_text(json.dumps({"snapshot_mtime": stamp, "at": time.time()},
                                     ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        from core import swallow as _sw; _sw.swallow(__file__, e)

    return {"ok": True, "align": True, "结果": r}


def plugin_key(group: str, slug: str, slot: int) -> str:
    return f"{group}:{slug}#{slot}"


def plugin_ids() -> list:
    return [plugin_key(g, s, i) for g in GROUPS
            for i, (s, _cn, _cid) in enumerate(CATALOG[g], 1)]


PLUGIN_IDS: tuple = tuple(plugin_ids())


def registry(*, enabled_map: dict | None = None) -> dict:
    em = enabled_map or {}
    out = []
    for gi, g in enumerate(GROUPS, 1):
        for si, (slug, cn, cid) in enumerate(CATALOG[g], 1):
            key = plugin_key(g, slug, si)
            reserved = slug == "reserved"
            out.append({"key": key, "group": g, "group_cn": GROUP_CN[g], "group_index": gi,
                        "slot": si, "global_index": (gi - 1) * 10 + si, "slug": slug,
                        "cn": cn, "duty": f"{GROUP_CN[g]}：{cn}", "cf_id": cid,
                        "needs_id": (cid is None and not reserved), "reserved": reserved,
                        "enabled": bool(em.get(key, 0)) if em else (not reserved),
                        "state": "reserved" if reserved else ("ready" if cid else "ready·id待核")})
    live = [p for p in out if not p["reserved"]]
    return {"count": len(out), "groups": {g: GROUP_CN[g] for g in GROUPS}, "per_group": 10,
            "slots_real": len(live), "slots_reserved": len(out) - len(live),
            "ids_confirmed": sum(1 for p in live if p["cf_id"]),
            "ids_unverified": sum(1 for p in live if not p["cf_id"]),
            "source": CATALOG_SOURCE, "plugins": out}


def validate() -> dict:
    ids = plugin_ids()
    dup = sorted({i for i in ids if ids.count(i) > 1})
    bad = [g for g in GROUPS if len(CATALOG[g]) != 10]
    return {"total": len(ids), "groups": len(CATALOG),
            "per_group": {g: len(v) for g, v in CATALOG.items()},
            "exactly_100": len(ids) == 100 and len(CATALOG) == 10,
            "all_groups_of_ten": not bad, "duplicate_keys": dup,
            "group_names_ok": set(CATALOG) == set(GROUPS),
            "ok": len(ids) == 100 and len(CATALOG) == 10 and not bad and not dup
                  and set(CATALOG) == set(GROUPS)}


def mesh_size() -> int:
    n = len(PLUGIN_IDS)
    return n * (n - 1) // 2


def mesh_edges() -> list:
    keys = list(PLUGIN_IDS)
    return [(a, b) for i, a in enumerate(keys) for b in keys[i + 1:]]


def mesh_view(*, sample: int = 40) -> dict:
    edges = mesh_edges()
    intra = [e for e in edges if e[0].split(":")[0] == e[1].split(":")[0]]
    return {"edge_total": len(edges), "expected": mesh_size(),
            "intra_group_edges": len(intra), "cross_group_edges": len(edges) - len(intra),
            "sample": [{"from": a, "to": b} for a, b in edges[:sample]],
            "note": "全互通：任意插件直连任意插件；页面画采样，数字是精确值"}


def reachable(a: str, b: str) -> bool:
    if a == b:
        return False
    return (a in PLUGIN_IDS and b in PLUGIN_IDS) or \
           (a.startswith("t") and b in PLUGIN_IDS) or (b.startswith("t") and a in PLUGIN_IDS)


class _AsyncHubMixin:
    """异步通道 mixin：面板的身体库是异步端口（transaction() + await execute/fetch_all）。

    与 CloudHub 的同步方法（扫描账本 _tx）并存：CLI/测试走同步，面板走异步。
    注意：之前这段曾被误插进函数体（缩进合法但不是类成员），故独立成 mixin，避免再次踩坑。
    """

    async def ainit(self):
        if self.led is None:
            return
        async with self.led.transaction():
            await self.led.execute("CREATE TABLE IF NOT EXISTS cloud_plugin_state ("
                                   "plugin TEXT PRIMARY KEY, enabled INTEGER, updated_at TEXT)")
            await self.led.execute("CREATE TABLE IF NOT EXISTS cloud_binding ("
                                   "bind_id TEXT PRIMARY KEY, tentacle TEXT, plugin TEXT,"
                                   " direction TEXT, resource TEXT, state TEXT, at TEXT)")

    async def aenabled_map(self) -> dict:
        if self.led is None:
            return {}
        rows = await self.led.fetch_all("SELECT plugin, enabled FROM cloud_plugin_state")
        return {r["plugin"]: int(r["enabled"] or 0) for r in rows or []}

    async def atoggle(self, plugin: str, on: bool | None = None) -> dict:
        if plugin not in PLUGIN_IDS:
            return {"ok": False, "reason": f"未知插件：{plugin}"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        em = await self.aenabled_map()
        new = (not bool(em.get(plugin))) if on is None else bool(on)
        await self.led.execute(
            "INSERT INTO cloud_plugin_state (plugin, enabled, updated_at) VALUES (?,?,?)"
            " ON CONFLICT (plugin) DO UPDATE SET enabled=EXCLUDED.enabled,"
            " updated_at=EXCLUDED.updated_at", (plugin, 1 if new else 0, self._now()))
        return {"ok": True, "plugin": plugin, "enabled": new}

    async def abind(self, tentacle: str, plugin: str, *, resource: str = "default",
                    skip_existing: bool = True) -> dict:
        """一对一绑定（两方向）。**幂等**：已存在的方向不再重复插入。

        重复跑 bind_all 曾把行数顶到理论上限的数倍（用量虚高）；这里先查后插。
        """
        if plugin not in PLUGIN_IDS:
            return {"ok": False, "reason": f"未知插件：{plugin}"}
        if tentacle not in self.tentacle_ids():
            return {"ok": False, "reason": f"未知触手：{tentacle}（范围 t001..t{self.n:03d}）"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        added = []
        for direction, a, b in (("t2p", tentacle, plugin), ("p2t", plugin, tentacle)):
            if skip_existing:
                got = await self.led.fetch_all(
                    "SELECT bind_id FROM cloud_binding WHERE tentacle=? AND plugin=? AND direction=? LIMIT 1",
                    (a, b, direction))
                if got:
                    continue
            await self.led.execute(
                "INSERT INTO cloud_binding (bind_id, tentacle, plugin, direction,"
                " resource, state, at) VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], a, b, direction, resource, "bound", self._now()))
            added.append(direction)
        return {"ok": True, "tentacle": tentacle, "plugin": plugin, "resource": resource,
                "directions": ["t2p", "p2t"], "bidirectional": True,
                "added": added, "already": 2 - len(added)}

    async def adedupe(self) -> dict:
        """清理历史重复行：同一 (触手, 插件, 方向) 只留最早一行。"""
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        before = await self.led.fetch_all("SELECT COUNT(*) AS n FROM cloud_binding")
        await self.led.execute(
            "DELETE FROM cloud_binding WHERE rowid NOT IN (SELECT MIN(rowid) FROM cloud_binding GROUP BY tentacle, plugin, direction)")
        after = await self.led.fetch_all("SELECT COUNT(*) AS n FROM cloud_binding")
        b = (before[0]["n"] if before else None)
        a = (after[0]["n"] if after else None)
        return {"ok": True, "rows_before": b, "rows_after": a,
                "removed": (b - a) if (b is not None and a is not None) else None}

    async def aunbind(self, tentacle: str, plugin: str) -> dict:
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        await self.led.execute("DELETE FROM cloud_binding WHERE (tentacle=? AND plugin=?)"
                               " OR (tentacle=? AND plugin=?)",
                               (tentacle, plugin, plugin, tentacle))
        rows = await self.led.fetch_all(
            "SELECT COUNT(*) AS n FROM cloud_binding WHERE (tentacle=? AND plugin=?)"
            " OR (tentacle=? AND plugin=?)", (tentacle, plugin, plugin, tentacle))
        left = int((rows or [{"n": 0}])[0]["n"] or 0)
        return {"ok": left == 0, "left": left, "tentacle": tentacle, "plugin": plugin}

    async def astate(self) -> dict:
        out = {"bindings": 0, "plugins_bound": 0, "tentacles_bound": 0, "by_plugin": {},
               "by_tentacle": {}, "symmetric": True, "enabled": 0}
        if self.led is None:
            return {**out, "reason": "no_ledger"}
        await self.ainit()
        rows = await self.led.fetch_all(
            "SELECT tentacle, plugin, direction FROM cloud_binding")
        t2p, p2t = set(), set()
        for r in rows or []:
            d, a, b = r["direction"], r["tentacle"], r["plugin"]
            (t2p if d == "t2p" else p2t).add((a, b))
        for t, p in t2p:
            out["by_plugin"][p] = out["by_plugin"].get(p, 0) + 1
            out["by_tentacle"][t] = out["by_tentacle"].get(t, 0) + 1
        out.update({"bindings": len(t2p), "plugins_bound": len(out["by_plugin"]),
                    "tentacles_bound": len(out["by_tentacle"])})
        missing = [x for x in t2p if (x[1], x[0]) not in p2t]
        out["asymmetric"], out["symmetric"] = missing[:5], not missing
        em = await self.aenabled_map()
        out["enabled"] = sum(1 for k in PLUGIN_IDS if em.get(k))
        return out

    async def abind_all(self, *, tentacles: int | None = None) -> dict:
        n = int(tentacles or self.n)
        bound, failed = 0, []
        for t in self.tentacle_ids()[:n]:
            for p in PLUGIN_IDS:
                r = await self.abind(t, p)
                if r.get("ok"):
                    bound += 1
                else:
                    failed.append({"t": t, "p": p, "why": r.get("reason")})
        return {"ok": not failed, "bound_pairs": bound, "rows": bound * 2,
                "tentacles": n, "plugins": len(PLUGIN_IDS), "failed": failed[:5]}


# ═══════════ 每个云插件的独立 IP 出口（公共代理池，凭据只从环境变量读）═══════════
# 主人 2026-10-06：每个云插件要有自己的 IP 隔离，用那种公共（共享）出口池。
EGRESS_POOL: tuple = tuple(f"eg-{i:03d}" for i in range(1, 101))     # 100 个出口位，与插件 1:1
EGRESS_ENV = "V9_EGRESS_PROXY_TEMPLATE"      # 例：http://user:pass@proxy-{n}.pool.example:8080
EGRESS_DIRECT = "direct"


def egress_of(plugin: str) -> str:
    """该插件的专属出口位（1:1 分配，互不共用 IP）。"""
    if plugin not in PLUGIN_IDS:
        return ""
    return EGRESS_POOL[PLUGIN_IDS.index(plugin) % len(EGRESS_POOL)]


def egress_proxy(plugin: str) -> str:
    """把出口位套进模板拿到代理串（真值只来自环境变量；没配就是直连）。

    {n} 会被替换成两位序号（01..100）。凭据绝不出现在源码里。
    """
    tpl = (os.environ.get(EGRESS_ENV) or "").strip()
    if not tpl:
        return ""
    eg = egress_of(plugin)
    if not eg:
        return ""
    n = eg.split("-")[-1]
    return tpl.replace("{n}", str(int(n))).replace("{eg}", eg)


def egress_report() -> dict:
    """出口隔离现状：池大小、是否配了公共代理模板、每插件出口（1:1）。"""
    tpl = (os.environ.get(EGRESS_ENV) or "").strip()
    out = {"pool": len(EGRESS_POOL), "plugins": len(PLUGIN_IDS),
           "mode": ("proxy" if tpl else EGRESS_DIRECT),
           "isolated": bool(tpl), "env": EGRESS_ENV,
           "note": ("每个插件一个专属出口位（1:1，不共用 IP）；代理串只从 "
                    f"{EGRESS_ENV} 读，模板里 {EGRESS_DIRECT} == 未配置"),
           "assign": [{"plugin": p, "egress": egress_of(p),
                       "proxied": bool(tpl)} for p in PLUGIN_IDS[:12]]}
    if not tpl:
        out["howto"] = (f"设 {EGRESS_ENV}=http://user:pass@proxy-{{n}}.你的公共池:8080 "
                        "即可让 100 个插件各自走独立出口（{n} 会替换成 01..100）")
    return out


# ═══════════ 内部共享：插件 ↔ 插件 的双向互绑（外表隔离，内部全通）═══════════
# 主人口径：每个插件**外表隔离**（各自独立 IP 出口），但**内部全部互相双向互绑共享**；
#   触手 1 可随意与 2..100 号插件双向互绑共享 —— 共享必须落成"两个方向各一行"。
# 注意：DDL 一律**内联字面量**（不许存变量再 execute），绑定值一律参数化。


class _ShareMixin:
    """插件↔插件双向互绑共享（同步通道：扫描账本 _tx）。"""

    def _share_init(self):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS cloud_share ("
                            "bond_id TEXT PRIMARY KEY, a TEXT, b TEXT, direction TEXT,"
                            " resource TEXT, state TEXT, at TEXT)")
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)


    def share_bond(self, a: str, b: str, *, resource: str = "shared") -> dict:
        """两个插件**双向互绑共享**：a→b 与 b→a 各一行（同一共享域）。"""
        if a not in PLUGIN_IDS or b not in PLUGIN_IDS:
            return {"ok": False, "reason": "未知插件"}
        if a == b:
            return {"ok": False, "reason": "不能和自己互绑"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        self._share_init()
        try:
            with txn(self.led) as cur:
                for direction, x, y in (("a2b", a, b), ("b2a", b, a)):
                    cur.execute("INSERT INTO cloud_share (bond_id, a, b, direction,"
                                " resource, state, at) VALUES (?,?,?,?,?,?,?)",
                                (uuid.uuid4().hex[:12], x, y, direction, resource,
                                 "shared", self._now()))
        except Exception as exc:                               # noqa: BLE001
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "a": a, "b": b, "resource": resource,
                "directions": ["a2b", "b2a"], "bidirectional": True}

    def share_all(self) -> dict:
        """一键内部互绑：全部 C(100,2)=4950 对，每对两行 = 9900 行。"""
        n = 0
        for i, a in enumerate(PLUGIN_IDS):
            for b in PLUGIN_IDS[i + 1:]:
                if self.share_bond(a, b).get("ok"):
                    n += 1
        return {"ok": True, "shared_pairs": n, "rows": n * 2,
                "expected_pairs": mesh_size()}

    def share_state(self) -> dict:
        if self.led is None:
            return {"shared_pairs": 0, "symmetric": True, "reason": "no_ledger"}
        self._share_init()
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT a, b, direction FROM cloud_share")
                rows = cur.fetchall()
        except Exception as exc:                               # noqa: BLE001
            return {"shared_pairs": 0, "symmetric": True,
                    "reason": f"{type(exc).__name__}: {exc}"}
        ab, ba = set(), set()
        for x, y, d in rows or []:
            (ab if d == "a2b" else ba).add((x, y))
        missing = [p for p in ab if (p[1], p[0]) not in ba]
        return {"shared_pairs": len(ab), "rows": len(ab) + len(ba),
                "expected_pairs": mesh_size(), "asymmetric": missing[:3],
                "symmetric": not missing}


class _AsyncShareMixin:
    """插件↔插件双向互绑共享（异步通道：面板身体库）。"""

    async def _ashare_init(self):
        if self.led is None:
            return
        async with self.led.transaction():
            await self.led.execute("CREATE TABLE IF NOT EXISTS cloud_share ("
                                   "bond_id TEXT PRIMARY KEY, a TEXT, b TEXT,"
                                   " direction TEXT, resource TEXT, state TEXT, at TEXT)")

    async def ashare_bond(self, a: str, b: str, *, resource: str = "shared") -> dict:
        if a not in PLUGIN_IDS or b not in PLUGIN_IDS:
            return {"ok": False, "reason": "未知插件"}
        if a == b:
            return {"ok": False, "reason": "不能和自己互绑"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self._ashare_init()
        for direction, x, y in (("a2b", a, b), ("b2a", b, a)):
            await self.led.execute(
                "INSERT INTO cloud_share (bond_id, a, b, direction, resource, state, at)"
                " VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], x, y, direction, resource, "shared", self._now()))
        return {"ok": True, "a": a, "b": b, "resource": resource,
                "directions": ["a2b", "b2a"], "bidirectional": True}

    async def ashare_all(self) -> dict:
        n = 0
        for i, a in enumerate(PLUGIN_IDS):
            for b in PLUGIN_IDS[i + 1:]:
                if (await self.ashare_bond(a, b)).get("ok"):
                    n += 1
        return {"ok": True, "shared_pairs": n, "rows": n * 2,
                "expected_pairs": mesh_size()}

    async def ashare_state(self) -> dict:
        if self.led is None:
            return {"shared_pairs": 0, "symmetric": True, "reason": "no_ledger"}
        await self._ashare_init()
        rows = await self.led.fetch_all("SELECT a, b, direction FROM cloud_share")
        ab, ba = set(), set()
        for r in rows or []:
            (ab if r["direction"] == "a2b" else ba).add((r["a"], r["b"]))
        missing = [p for p in ab if (p[1], p[0]) not in ba]
        return {"shared_pairs": len(ab), "rows": len(ab) + len(ba),
                "expected_pairs": mesh_size(), "asymmetric": missing[:3],
                "symmetric": not missing}


def share_registry_note() -> dict:
    return {"outer": "每个插件外表隔离（各自独立 IP 出口）",
            "inner": "内部全部双向互绑共享：任意两插件互绑，触手 1 可与 2..100 全部双向共享",
            "pairs": mesh_size(), "rows": mesh_size() * 2}


class CloudHub(_AsyncHubMixin, _ShareMixin, _AsyncShareMixin):
    """持久层：启用开关（面板那个 switch）+ 触手双向绑定 + 审计。"""

    def __init__(self, ledger=None, *, n_tentacles: int = 100):
        self.led, self.n = ledger, int(n_tentacles)
        self._init()

    def tentacle_ids(self) -> list:
        return [f"t{i:03d}" for i in range(1, self.n + 1)]

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _init(self):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS cloud_plugin_state ("
                            "plugin TEXT PRIMARY KEY, enabled INTEGER, updated_at TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS cloud_binding ("
                            "bind_id TEXT PRIMARY KEY, tentacle TEXT, plugin TEXT,"
                            " direction TEXT, resource TEXT, state TEXT, at TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS cloud_link_audit ("
                            "audit_id TEXT PRIMARY KEY, action TEXT, detail TEXT, at TEXT)")
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)


    def _log(self, action, detail):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO cloud_link_audit (audit_id, action, detail, at)"
                            " VALUES (?,?,?,?)",
                            (uuid.uuid4().hex[:12], action,
                             json.dumps(detail, ensure_ascii=False)[:600], self._now()))
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)


    def enabled_map(self) -> dict:
        if self.led is None:
            return {}
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT plugin, enabled FROM cloud_plugin_state")
                return {p: int(e) for p, e in cur.fetchall()}
        except Exception:                                      # noqa: BLE001
            return {}

    def toggle(self, plugin: str, on: bool | None = None) -> dict:
        if plugin not in PLUGIN_IDS:
            return {"ok": False, "reason": f"未知插件：{plugin}"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        new = (not bool(self.enabled_map().get(plugin))) if on is None else bool(on)
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO cloud_plugin_state (plugin, enabled, updated_at)"
                            " VALUES (?,?,?) ON CONFLICT (plugin) DO UPDATE SET"
                            " enabled=EXCLUDED.enabled, updated_at=EXCLUDED.updated_at",
                            (plugin, 1 if new else 0, self._now()))
        except Exception as exc:                               # noqa: BLE001
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        self._log("toggle", {"plugin": plugin, "enabled": new})
        return {"ok": True, "plugin": plugin, "enabled": new}

    def bind(self, tentacle: str, plugin: str, *, resource: str = "default") -> dict:
        if plugin not in PLUGIN_IDS:
            return {"ok": False, "reason": f"未知插件：{plugin}"}
        if tentacle not in self.tentacle_ids():
            return {"ok": False, "reason": f"未知触手：{tentacle}（范围 t001..t{self.n:03d}）"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        try:
            with txn(self.led) as cur:
                for direction, a, b in (("t2p", tentacle, plugin), ("p2t", plugin, tentacle)):
                    cur.execute("INSERT INTO cloud_binding (bind_id, tentacle, plugin,"
                                " direction, resource, state, at) VALUES (?,?,?,?,?,?,?)",
                                (uuid.uuid4().hex[:12], a, b, direction, resource,
                                 "bound", self._now()))
        except Exception as exc:                               # noqa: BLE001
            return {"ok": False, "reason": f"绑定写入失败：{type(exc).__name__}"}
        self._log("bind", {"tentacle": tentacle, "plugin": plugin, "resource": resource,
                           "directions": ["t2p", "p2t"]})
        return {"ok": True, "tentacle": tentacle, "plugin": plugin, "resource": resource,
                "directions": ["t2p", "p2t"], "bidirectional": True}

    def unbind(self, tentacle: str, plugin: str) -> dict:
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        with txn(self.led) as cur:
            for _ in range(1):
                cur.execute("DELETE FROM cloud_binding WHERE (tentacle=? AND plugin=?)"
                            " OR (tentacle=? AND plugin=?)",
                            (tentacle, plugin, plugin, tentacle))
                cur.execute("SELECT COUNT(*) FROM cloud_binding WHERE (tentacle=? AND plugin=?)"
                            " OR (tentacle=? AND plugin=?)",
                            (tentacle, plugin, plugin, tentacle))
                left = int(cur.fetchone()[0])
        self._log("unbind", {"tentacle": tentacle, "plugin": plugin})
        return {"ok": left == 0, "left": left, "tentacle": tentacle, "plugin": plugin}

    def bind_all(self, *, tentacles: int | None = None) -> dict:
        n = int(tentacles or self.n)
        bound, failed = 0, []
        for t in self.tentacle_ids()[:n]:
            for p in PLUGIN_IDS:
                r = self.bind(t, p)
                if r.get("ok"):
                    bound += 1
                else:
                    failed.append({"t": t, "p": p, "why": r.get("reason")})
        self._log("bind_all", {"tentacles": n, "bound": bound, "failed": len(failed)})
        return {"ok": not failed, "bound_pairs": bound, "rows": bound * 2,
                "tentacles": n, "plugins": len(PLUGIN_IDS), "failed": failed[:5]}

    def state(self) -> dict:
        out = {"bindings": 0, "plugins_bound": 0, "tentacles_bound": 0, "by_plugin": {},
               "by_tentacle": {}, "symmetric": True, "enabled": 0}
        if self.led is None:
            return {**out, "reason": "no_ledger"}
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT tentacle, plugin, direction FROM cloud_binding")
                rows = cur.fetchall()
        except Exception as exc:                               # noqa: BLE001
            return {**out, "reason": f"{type(exc).__name__}: {exc}"}
        t2p, p2t = set(), set()
        for a, b, d in rows or []:
            (t2p if d == "t2p" else p2t).add((a, b))
        for t, p in t2p:
            out["by_plugin"][p] = out["by_plugin"].get(p, 0) + 1
            out["by_tentacle"][t] = out["by_tentacle"].get(t, 0) + 1
        out["bindings"] = len(t2p)
        out["plugins_bound"] = len(out["by_plugin"])
        out["tentacles_bound"] = len(out["by_tentacle"])
        missing = [pair for pair in t2p if (pair[1], pair[0]) not in p2t]
        out["asymmetric"] = missing[:5]
        out["symmetric"] = not missing
        out["enabled"] = sum(1 for k in PLUGIN_IDS if self.enabled_map().get(k))
        return out


# ═══════════ 目录核对（零出网：用你自己 curl 下来的快照）═══════════
def sync_from_snapshot(path: str | Path | None = None) -> dict:
    """把本表 id 与你导出的官方目录快照核对（**本模块不发任何请求**）。

    导出快照（你来跑，token 不经过我们）：
      curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \\
        "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/ai/models/search?per_page=1000" \\
        -o state/cf_models.json
    支持两种格式：API 原始 JSON（取 result[].name）或纯文本每行一个模型名。
    """
    p = Path(path or "state/cf_models.json")
    if not p.exists():
        return {"ok": False, "reason": f"快照不存在：{p}",
                "howto": "curl -s -H \"Authorization: Bearer $CLOUDFLARE_API_TOKEN\" "
                         "\"https://api.cloudflare.com/client/v4/accounts/"
                         "$CLOUDFLARE_ACCOUNT_ID/ai/models/search?per_page=1000\" "
                         "-o state/cf_models.json"}
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
        try:
            data = json.loads(raw)
            live = {m.get("name") for m in (data.get("result") or []) if m.get("name")}
        except json.JSONDecodeError:
            live = {ln.strip() for ln in raw.splitlines() if ln.strip()}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "reason": f"快照读不了：{type(exc).__name__}"}
    known = {pl["cf_id"] for pl in registry()["plugins"] if pl["cf_id"]}
    return {"ok": True, "snapshot": str(p), "api_models": len(live),
            "confirmed_ids": sorted(known & live)[:60],
            "unknown_in_snapshot": sorted(live - known)[:60],
            "missing_from_snapshot": sorted(known - live)[:60],
            "note": "以快照为准；把确认的 id 填进 _REAL 第三列即可解除 needs_id"}


    # ═══ 异步通道：面板的身体库是异步端口（transaction() + await execute/fetch_all）═══
    # 同步通道（_tx）留给 CLI/测试用的扫描账本；两条路都必须能跑，否则面板点绑定就 500/400。
    async def ainit(self):
        if self.led is None:
            return
        async with self.led.transaction():
            await self.led.execute("CREATE TABLE IF NOT EXISTS cloud_plugin_state ("
                                   "plugin TEXT PRIMARY KEY, enabled INTEGER, updated_at TEXT)")
            await self.led.execute("CREATE TABLE IF NOT EXISTS cloud_binding ("
                                   "bind_id TEXT PRIMARY KEY, tentacle TEXT, plugin TEXT,"
                                   " direction TEXT, resource TEXT, state TEXT, at TEXT)")
            await self.led.execute("CREATE TABLE IF NOT EXISTS cloud_link_audit ("
                                   "audit_id TEXT PRIMARY KEY, action TEXT, detail TEXT, at TEXT)")

    async def aenabled_map(self) -> dict:
        if self.led is None:
            return {}
        rows = await self.led.fetch_all(
            "SELECT plugin, enabled FROM cloud_plugin_state")
        return {r["plugin"]: int(r["enabled"] or 0) for r in rows or []}

    async def atoggle(self, plugin: str, on: bool | None = None) -> dict:
        if plugin not in PLUGIN_IDS:
            return {"ok": False, "reason": f"未知插件：{plugin}"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        em = await self.aenabled_map()
        new = (not bool(em.get(plugin))) if on is None else bool(on)
        await self.led.execute(
            "INSERT INTO cloud_plugin_state (plugin, enabled, updated_at) VALUES (?,?,?)"
            " ON CONFLICT (plugin) DO UPDATE SET enabled=EXCLUDED.enabled,"
            " updated_at=EXCLUDED.updated_at", (plugin, 1 if new else 0, self._now()))
        return {"ok": True, "plugin": plugin, "enabled": new}

    async def abind(self, tentacle: str, plugin: str, *, resource: str = "default",
                    skip_existing: bool = True) -> dict:
        """一对一绑定（两方向）。**幂等**：已存在的方向不再重复插入。

        重复跑 bind_all 曾把行数顶到理论上限的数倍（用量虚高）；这里先查后插。
        """
        if plugin not in PLUGIN_IDS:
            return {"ok": False, "reason": f"未知插件：{plugin}"}
        if tentacle not in self.tentacle_ids():
            return {"ok": False, "reason": f"未知触手：{tentacle}（范围 t001..t{self.n:03d}）"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        added = []
        for direction, a, b in (("t2p", tentacle, plugin), ("p2t", plugin, tentacle)):
            if skip_existing:
                got = await self.led.fetch_all(
                    "SELECT bind_id FROM cloud_binding WHERE tentacle=? AND plugin=? AND direction=? LIMIT 1",
                    (a, b, direction))
                if got:
                    continue
            await self.led.execute(
                "INSERT INTO cloud_binding (bind_id, tentacle, plugin, direction,"
                " resource, state, at) VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], a, b, direction, resource, "bound", self._now()))
            added.append(direction)
        return {"ok": True, "tentacle": tentacle, "plugin": plugin, "resource": resource,
                "directions": ["t2p", "p2t"], "bidirectional": True,
                "added": added, "already": 2 - len(added)}

    async def adedupe(self) -> dict:
        """清理历史重复行：同一 (触手, 插件, 方向) 只留最早一行。"""
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        before = await self.led.fetch_all("SELECT COUNT(*) AS n FROM cloud_binding")
        await self.led.execute(
            "DELETE FROM cloud_binding WHERE rowid NOT IN (SELECT MIN(rowid) FROM cloud_binding GROUP BY tentacle, plugin, direction)")
        after = await self.led.fetch_all("SELECT COUNT(*) AS n FROM cloud_binding")
        b = (before[0]["n"] if before else None)
        a = (after[0]["n"] if after else None)
        return {"ok": True, "rows_before": b, "rows_after": a,
                "removed": (b - a) if (b is not None and a is not None) else None}

    async def aunbind(self, tentacle: str, plugin: str) -> dict:
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        await self.led.execute("DELETE FROM cloud_binding WHERE (tentacle=? AND plugin=?)"
                               " OR (tentacle=? AND plugin=?)",
                               (tentacle, plugin, plugin, tentacle))
        rows = await self.led.fetch_all(
            "SELECT COUNT(*) AS n FROM cloud_binding WHERE (tentacle=? AND plugin=?)"
            " OR (tentacle=? AND plugin=?)", (tentacle, plugin, plugin, tentacle))
        left = int((rows or [{"n": 0}])[0]["n"] or 0)
        return {"ok": left == 0, "left": left, "tentacle": tentacle, "plugin": plugin}

    async def astate(self) -> dict:
        out = {"bindings": 0, "plugins_bound": 0, "tentacles_bound": 0, "by_plugin": {},
               "by_tentacle": {}, "symmetric": True, "enabled": 0}
        if self.led is None:
            return {**out, "reason": "no_ledger"}
        await self.ainit()
        rows = await self.led.fetch_all(
            "SELECT tentacle, plugin, direction FROM cloud_binding")
        t2p, p2t = set(), set()
        for r in rows or []:
            d, a, b = r["direction"], r["tentacle"], r["plugin"]
            (t2p if d == "t2p" else p2t).add((a, b))
        for t, p in t2p:
            out["by_plugin"][p] = out["by_plugin"].get(p, 0) + 1
            out["by_tentacle"][t] = out["by_tentacle"].get(t, 0) + 1
        out.update({"bindings": len(t2p), "plugins_bound": len(out["by_plugin"]),
                    "tentacles_bound": len(out["by_tentacle"])})
        missing = [x for x in t2p if (x[1], x[0]) not in p2t]
        out["asymmetric"], out["symmetric"] = missing[:5], not missing
        em = await self.aenabled_map()
        out["enabled"] = sum(1 for k in PLUGIN_IDS if em.get(k))
        return out

    async def abind_all(self, *, tentacles: int | None = None) -> dict:
        n = int(tentacles or self.n)
        bound, failed = 0, []
        for t in self.tentacle_ids()[:n]:
            for p in PLUGIN_IDS:
                r = await self.abind(t, p)
                if r.get("ok"):
                    bound += 1
                else:
                    failed.append({"t": t, "p": p, "why": r.get("reason")})
        return {"ok": not failed, "bound_pairs": bound, "rows": bound * 2,
                "tentacles": n, "plugins": len(PLUGIN_IDS), "failed": failed[:5]}


__all__ = ["GROUPS", "GROUP_CN", "CATALOG", "PLUGIN_IDS", "CATALOG_SOURCE", "registry",
           "validate", "mesh_edges", "mesh_size", "mesh_view", "reachable", "CloudHub",
           "sync_from_snapshot", "plugin_key", "plugin_ids"]
