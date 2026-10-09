# body/obsidian.py —— Obsidian 接入：主脑全库可读写 + 每根触手的永久记忆落库
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人 2026-10-06 口径：把 100 根配置好 LLM 的触手接上独立 Obsidian（桌面那个快捷方式），
#   ① 主脑可以随时读写里面所有东西；② 触手的**永久记忆**存在里面。
#
# 三条纪律（写进代码，不靠人记）：
#   · 路径只在**库根之内**（resolve + 前缀断言），任何 ../ 越界请求直接拒；
#   · **不碰 V8 的库**（主人铁律：V8/V9 不混）；沙箱库也不碰；
#   · V9 的东西一律落在命名空间子目录里，绝不把笔记铺在人家库根上。
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

OBSIDIAN_JSON = Path(os.environ.get("OBSIDIAN_JSON",
                                    str(Path(os.environ.get("APPDATA", "")) /
                                        "obsidian" / "obsidian.json")))
NAMESPACE = os.environ.get("V9_VAULT_NAMESPACE", "GBT小土豆V9-触手记忆")
# 排除名单：按**路径段**精确匹配（不再用裸 "v8" 匹配整条路径，避免误伤目录名）
EXCLUDE_MARKERS = tuple(os.environ.get("V9_VAULT_EXCLUDE",
                                       "GBT小土豆V8,Obsidian Sandbox,Sandbox").split(","))


@dataclass
class VaultInfo:
    key: str
    path: str
    ts: int = 0
    open_now: bool = False
    excluded: str = ""            # 非空 = 被排除的原因

    def as_dict(self) -> dict:
        return {"key": self.key, "path": self.path, "ts": self.ts,
                "open": self.open_now, "excluded": self.excluded}


def discover_vaults(*, config=None) -> list[VaultInfo]:
    """从 Obsidian 配置里发现全部库，并标注哪些被排除（不静默跳过）。"""
    cfg_path = Path(config) if config else OBSIDIAN_JSON
    out: list[VaultInfo] = []
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return []
    for key, v in (data.get("vaults") or {}).items():
        p = str(v.get("path") or "")
        info = VaultInfo(key=key, path=p, ts=int(v.get("ts") or 0),
                         open_now=bool(v.get("open")))
        segs = [s.lower() for s in re.split(r"[\\/]+", p) if s]
        for m in EXCLUDE_MARKERS:
            m = (m or "").strip().lower()
            if not m:
                continue
            if any(m in s for s in segs):                  # ★按段匹配：只看目录名
                info.excluded = f"命中排除词「{m}」（V8/沙箱不碰）"
                break
        out.append(info)
    return sorted(out, key=lambda i: i.ts, reverse=True)


def pick_vault(*, prefer: str | None = None, config=None) -> dict:
    """选一个可写的库：V9_VAULT_DIR > 参数 > 自动（最新且未被排除且目录存在）。"""
    env = os.environ.get("V9_VAULT_DIR") or prefer
    if env:
        return {"root": str(Path(env)), "source": "explicit",
                "reason": "V9_VAULT_DIR/参数指定"}
    cands = discover_vaults(config=config)
    usable = [c for c in cands if not c.excluded and c.path and Path(c.path).is_dir()]
    if not usable:
        return {"root": "", "source": "none",
                "reason": "没有可用库（全部被排除或路径不存在）",
                "all": [c.as_dict() for c in cands]}
    chosen = usable[0]
    return {"root": chosen.path, "source": "auto", "key": chosen.key,
            "reason": f"最新可用的库（{len(usable)} 个候选，已排除 "
                      f"{[c.path for c in cands if c.excluded]}）",
            "all": [c.as_dict() for c in cands]}


class VaultError(RuntimeError):
    pass


class ObsidianVault:
    """库读写。所有路径都必须在库根之内（含命名空间子目录）。"""

    def __init__(self, root: str, *, namespace: str = NAMESPACE,
                 subdir: str = "", now_fn=time.time):
        if not root:
            raise VaultError("没有可用的 Obsidian 库（先设 V9_VAULT_DIR 或确认 obsidian.json）")
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise VaultError(f"库目录不存在：{self.root}")
        self.ns = str(namespace).strip("/\\")
        self.subdir = str(subdir).strip("/\\")
        self._now = now_fn

    # ── 边界：只允许库根之内（normpath 先把 ../ 折叠掉，再断言前缀）──
    def _resolve(self, rel: str) -> Path:
        parts = [p for p in (self.ns, self.subdir, str(rel or "")) if p]
        joined = self.root.joinpath(*parts) if parts else self.root
        # ★必须 normpath：否则 "ns/../../x" 这种串仍以 root 开头，看似合规实则越界
        t = Path(os.path.normpath(str(joined)))
        s, r = str(t), str(self.root)
        if not (s == r or s.startswith(r + os.sep)):
            raise VaultError(f"路径越界，拒绝：{rel}")
        return t

    def path_of(self, rel: str) -> Path:
        return self._resolve(rel)

    # ── 读 ──
    def read(self, rel: str) -> str | None:
        p = self._resolve(rel)
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    def _ns_root(self) -> Path:
        return Path(os.path.normpath(str(self.root.joinpath(
            *[p for p in (self.ns, self.subdir) if p]))))

    def list_files(self, prefix: str = "", *, limit: int = 2000) -> list[str]:
        """返回**相对命名空间根**的路径（与 read/append 同一口径，能直接回读）。"""
        base = self._resolve(prefix) if prefix else (self._ns_root()
                                                     if (self.ns or self.subdir)
                                                     else self.root)
        if not base.exists():
            return []
        ns_root = self._ns_root() if (self.ns or self.subdir) else self.root
        out = []
        for p in sorted(base.rglob("*.md")):
            try:
                out.append(str(p.relative_to(ns_root)).replace(os.sep, "/"))
            except Exception:                                  # noqa: BLE001
                continue
            if len(out) >= limit:
                break
        return out

    # ── 写 ──
    def write(self, rel: str, text: str, *, frontmatter: dict | None = None) -> dict:
        p = self._resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        body = text
        if frontmatter:
            fm = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}"
                           for k, v in frontmatter.items())
            body = f"---\n{fm}\n---\n\n{text}"
        p.write_text(body, encoding="utf-8")
        return {"ok": True, "path": str(p), "bytes": len(body.encode("utf-8"))}

    def append(self, rel: str, text: str, *, ensure_header: str | None = None) -> dict:
        """追加（永久记忆就该是追加式的：不覆盖历史）。"""
        p = self._resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        new = not p.exists()
        with open(p, "a", encoding="utf-8") as f:
            if new and ensure_header:
                f.write(ensure_header.rstrip() + "\n\n")
            f.write(text.rstrip() + "\n")
        return {"ok": True, "path": str(p), "created": new,
                "bytes": p.stat().st_size}

    def search(self, query: str, *, prefix: str = "", limit: int = 50) -> list[dict]:
        q = str(query or "").strip()
        if not q:
            return []
        hits = []
        for rel in self.list_files(prefix):
            txt = self.read(rel) or ""
            if q.lower() in txt.lower():
                idx = txt.lower().find(q.lower())
                hits.append({"note": rel,
                             "excerpt": txt[max(0, idx - 60):idx + 120].replace("\n", " ")})
            if len(hits) >= limit:
                break
        return hits


class TentacleMemory:
    """每根触手一份永久记忆笔记：frontmatter 记档案，正文追加式记事实。

    主脑（本体）可读全部：`read_all()` / `search_all()`。
    """

    def __init__(self, vault: ObsidianVault, *, prefix: str = "GBY-D"):
        self.v = vault
        self.prefix = os.environ.get("MAIL_PREFIX", "GBT-D")

    def note_name(self, tentacle: str) -> str:
        t = str(tentacle)
        tid = t if t.startswith(self.prefix) else f"{self.prefix}{t}"
        return f"触手/{tid}.md"

    def _header(self, tid: str, role: str = "", key_id: str = "") -> str:
        return (f"# {tid} · 永久记忆\n\n"
                f"> 触手 {tid}（角色 {role or '-'}）的永久记忆。追加式，只增不改。\n"
                f"> 主脑可读写；统一密钥指纹 {key_id or '-'}。\n")

    def remember(self, tentacle: str, key: str, value, *, role: str = "",
                 key_id: str = "", at: str | None = None) -> dict:
        tid = self.note_name(tentacle).split("/")[-1].removesuffix(".md")
        stamp = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        line = (f"- `{stamp}` **{key}**："
                f"{value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}")
        return self.v.append(self.note_name(tentacle), line,
                             ensure_header=self._header(tid, role, key_id))

    def recall(self, tentacle: str, *, limit: int = 50) -> dict:
        txt = self.v.read(self.note_name(tentacle))
        if txt is None:
            return {"tentacle": tentacle, "found": False, "entries": []}
        lines = [l for l in txt.splitlines() if l.startswith("- `")]
        return {"tentacle": tentacle, "found": True, "entries": lines[-int(limit):],
                "count": len(lines)}

    def read_all(self) -> dict:
        files = self.v.list_files("触手")
        return {"notes": len(files), "files": files[:200]}

    def search_all(self, query: str, limit: int = 50) -> list[dict]:
        return self.v.search(query, prefix="触手", limit=limit)

    def agent_digest(self, tentacle: str) -> str:
        """给触手自己看的一页摘要（开工前读它）。"""
        r = self.recall(tentacle, limit=8)
        head = f"# 记忆摘要 · {tentacle}\n\n"
        if not r["found"]:
            return head + "（还没有记忆：这是它的第一页）\n"
        return head + "\n".join(r["entries"]) + f"\n\n（共 {r['count']} 条）\n"


class VaultMemoryStore:
    """MemoryStore 的 Obsidian 后端：与 core/isolated_bus.MemoryStore 同签名，可无缝替换。

    命名空间隔离仍在（每根触手各一份笔记），但**永久**：重启、换进程都还在。
    """

    def __init__(self, vault: ObsidianVault, *, prefix: str = "GBY-D"):
        self.mem = TentacleMemory(vault, prefix=prefix)

    def remember(self, tid, key, value):
        self.mem.remember(tid, key, value)
        return {"ok": True, "backend": "obsidian"}

    def recall(self, tid, key=None):
        r = self.mem.recall(tid)
        if not r["found"]:
            return None
        if key is None:
            return r["entries"]
        for line in reversed(r["entries"]):
            if f"**{key}**" in line:
                return line
        return None


def status() -> dict:
    """接线状态：选中的库、排除清单、命名空间、触手笔记数（主脑开箱即用）。"""
    pick = pick_vault()
    out = {"vault": pick, "namespace": NAMESPACE, "exclude": list(EXCLUDE_MARKERS)}
    try:
        v = ObsidianVault(pick["root"])
        tm = TentacleMemory(v)
        out["notes"] = len(v.list_files("触手"))
        out["readable"] = True
    except Exception as exc:                                   # noqa: BLE001
        out["readable"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


__all__ = ["discover_vaults", "pick_vault", "ObsidianVault", "TentacleMemory",
           "VaultMemoryStore", "VaultError", "status", "NAMESPACE"]
