#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
stack_acceptance.py —— 一跑全检 · 真读数验收
dev: 自由的风 · 本署名不可删除、不可篡改归属

判据口径：每条都要有真读数；拿不到就写 "未读到" + 原因（不拿绿顶）。
用法:
  python audit/stack_acceptance.py              # 全检（跳过本机大模型）
  python audit/stack_acceptance.py --with-local # 含本机 Ollama 真发
  python audit/stack_acceptance.py --json       # 只输出 JSON
"""
from __future__ import annotations
from core.swallow import swallow as _swallow
import argparse, json, os, sqlite3, sys, time, socket, hashlib
import urllib.request, urllib.error
from pathlib import Path

# ── 入口副作用：路径固定（防"活死线"）──
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATE_DIR = ROOT / "state"; STATE_DIR.mkdir(exist_ok=True)
STATE = STATE_DIR / "stack_acceptance.json"
LEDGER_DB = Path(os.environ.get("LEDGER_DB", ROOT / "tentacle_ledger.db"))
DEVOUR_DIR = Path(os.environ.get("DEVOUR_DIR", ROOT / "devoured/t1-eye"))

def _normalize_base(value: str, default: str, default_port: int | None = None) -> str:
    """把 127.0.0.1 / host:port 这类缺 scheme 的配置补成 http://；
    裸主机名再补上服务默认端口；空值回退默认。"""
    from urllib.parse import urlparse

    value = (value or "").strip().rstrip("/")
    if not value:
        return default
    url = value if "://" in value else "http://" + value
    if default_port is not None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if (parsed.port is None and host in ("127.0.0.1", "localhost", "::1")):
            url = f"{parsed.scheme}://{host}:{default_port}" + (parsed.path or "")
    return url


def _validate_probe_url(url: str) -> str:
    """探针 URL 校验：仅 http/https；本机(环回)只允许显式配置的本地服务面；
    其它私有/保留地址一律拒绝（公网主机必须 https）。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise Unread(f"协议不允许：{parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise Unread("URL 缺主机名")
    local_ok = os.environ.get("V9_ALLOW_LOCAL_PROBES", "1") == "1"
    if host in ("127.0.0.1", "localhost", "::1"):
        if not local_ok:
            raise Unread("本机地址探针已禁用（V9_ALLOW_LOCAL_PROBES=0）")
        return url
    if parsed.scheme != "https":
        raise Unread(f"公网探针必须 https：{host}")
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            raise Unread(f"拒绝私有/保留地址：{host}")
    except ValueError as e:
        _swallow(__file__, e)
    return url


GATEWAY = _normalize_base(os.environ.get("OPENAI_BASE_URL", ""),
                          "http://127.0.0.1:8317/v1", default_port=8317)
OLLAMA  = _normalize_base(os.environ.get("OLLAMA_HOST", ""),
                          "http://127.0.0.1:11434", default_port=11434)

# ─────────────────────────────────────────────────────────────
# 读数原语
# ─────────────────────────────────────────────────────────────
class Unread(Exception):
    """读数失败：携带明确原因，渲染成"未读到 + 原因" """

def http_json(url: str, timeout=5, headers=None):
    _validate_probe_url(url)
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        raise Unread(f"HTTP {e.code} @ {url}")
    except urllib.error.URLError as e:
        raise Unread(f"连接失败 {e.reason} @ {url}")
    except (json.JSONDecodeError, TimeoutError) as e:
        raise Unread(f"{type(e).__name__} @ {url}")

def port_open(host: str, port: int, timeout=2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

# ─────────────────────────────────────────────────────────────
# 七条判据
# ─────────────────────────────────────────────────────────────
def check_1_gateway() -> str:
    """① 统一网关可达 + 模型列车道 ≥1 条真回话"""
    data = http_json(f"{GATEWAY}/models")
    models = [m.get("id") for m in data.get("data", [])]
    if not models:
        raise Unread("网关在线但 /models 返回空列车道")
    return f"models={len(models)} 首条={models[0]}"

def check_2_local_llm(with_local: bool) -> str:
    """② 本机 Ollama 车道（--with-local 时真发一次）"""
    tags = http_json(f"{OLLAMA}/api/tags")
    models = [m.get("name") for m in tags.get("models", [])]
    if not models:
        raise Unread("Ollama 在线但未拉取任何模型")
    if not with_local:
        return f"已注册={len(models)} 首条={models[0]}（未真发，加 --with-local）"
    # 真发一次最小补全
    body = json.dumps({"model": models[0], "prompt": "ping", "stream": False,
                       "options": {"num_predict": 1}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    if not resp.get("response") and not resp.get("done"):
        raise Unread("本机模型无回话")
    return f"真回话 ok · 模型={models[0]}"

def check_3_ledger() -> str:
    """③ 账本可读写（真写一行真读一行）"""
    if not LEDGER_DB.parent.exists():
        raise Unread(f"账本目录不存在 {LEDGER_DB.parent}")
    db = sqlite3.connect(str(LEDGER_DB))
    db.execute("""CREATE TABLE IF NOT EXISTS ledger(
        ts REAL, scanner TEXT, target TEXT, status TEXT, detail TEXT, brain_verdict TEXT)""")
    probe = f"__acceptance__{int(time.time())}"
    db.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?)",
               (time.time(), "acceptance", probe, "scanned", "自检探针", ""))
    db.commit()
    row = db.execute("SELECT scanner,status FROM ledger WHERE target=?", (probe,)).fetchone()
    db.execute("DELETE FROM ledger WHERE target=?", (probe,))
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    db.close()
    if not row:
        raise Unread("写入成功但回读为空")
    return f"读写 ok · 回读={row} · 现存 {total} 行"

def check_4_devour() -> str:
    """④ 吞噬零丢帧（序号连续性 + 哈希可核）"""
    idx = DEVOUR_DIR / "index.jsonl"
    if not idx.exists():
        raise Unread(f"未找到帧索引 {idx}（吞噬未启动过？）")
    seqs, hashes, bad = [], 0, 0
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        r = json.loads(line)
        seqs.append(r["seq"])
        if r.get("sha256"): hashes += 1
    if not seqs:
        raise Unread("索引存在但无帧记录")
    seqs.sort()
    gaps = sorted(set(range(seqs[0], seqs[-1] + 1)) - set(seqs))
    span = seqs[-1] - seqs[0] + 1
    lossless = not gaps
    if not lossless:
        raise Unread(f"丢帧 {len(gaps)} 处，首处 seq={gaps[0]}")
    return f"零丢帧 ok · {len(seqs)} 帧 · 跨度 {span} · 哈希覆盖 {hashes}/{len(seqs)}"

def check_5_r2() -> str:
    """⑤ R2 归档可用（真读：sim 模式读模拟对象；真 R2 列桶+读元数据）"""
    sys_path_root = str(ROOT)
    if sys_path_root not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path_root)
    from senses.r2 import R2Unavailable, r2_bucket, r2_client
    try:
        client, mode = r2_client()
    except R2Unavailable as exc:
        raise Unread(f"R2 未配置（设 CLOUDFLARE_* 或用 R2_SIM_DIR 跑离线模拟）：{exc}")

    bucket = r2_bucket()
    seg_idx = DEVOUR_DIR / "segments.jsonl"
    archived_keys = []
    if seg_idx.exists():
        for line in seg_idx.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("state") == "archived" and row.get("key"):
                archived_keys.append(row["key"])
    archived_keys = list(dict.fromkeys(archived_keys))

    if mode == "sim":
        base = Path(os.environ["R2_SIM_DIR"]) / bucket
        objects = [p for p in base.rglob("*") if p.is_file()] if base.exists() else []
        if not objects and not archived_keys:
            raise Unread(f"模拟桶为空：{base}")
        in_bucket = {str(p.relative_to(base)).replace(os.sep, "/") for p in objects}
        # ★挑"索引里有、桶里也在"的那个 key 读元数据；索引里缺的另行计数（陈旧索引不是不可用）
        present = [k for k in archived_keys if k in in_bucket]
        missing_keys = [k for k in archived_keys if k not in in_bucket]
        sample = present[0] if present else (sorted(in_bucket)[0] if in_bucket
                                            else archived_keys[0])
        head = None
        try:
            head = client.head_object(Bucket=bucket, Key=sample)["ContentLength"]
        except Exception as exc:                              # noqa: BLE001
            raise Unread(f"模拟对象读元数据失败：{exc}")
        extra = f" · 索引缺失={len(missing_keys)}" if missing_keys else ""
        return (f"模拟模式 ok · {len(objects)} 对象 · 样本={sample} · "
                f"归档索引={len(archived_keys)} 段（桶内 {len(present)}）"
                + (f" · 元数据={head}B" if head else "") + extra)

    try:
        resp = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
    except Exception as e:
        raise Unread(f"列桶失败 {type(e).__name__}: {e}")
    n = resp.get("KeyCount", 0)
    sample = (resp.get("Contents") or [{}])[0].get("Key", "（空桶）")
    return f"列桶 ok · {n} 对象 · 样本={sample} · 索引归档段={len(archived_keys)}"


def check_6_playback() -> str:
    """⑥ 回放三级命中（本地/缓存/R2 各自可用性）"""
    from senses.playback import Player
    p = Player(DEVOUR_DIR)
    segs = p.segments()
    if not segs:
        raise Unread("无段可回放（segments.jsonl 为空）")
    local = sum(1 for s in segs if Path(s.local_path).exists())
    cached = sum(1 for s in segs if (p.cache / f"{s.seg_id}.mkv").exists())
    remote = sum(1 for s in segs if s.state == "archived" and s.key)
    # 真取一段：优先取本地在册的，验证 fetch 不抛
    target = next((s for s in segs if Path(s.local_path).exists()), segs[0])
    got = p.fetch(target)
    if got is None:
        raise Unread(f"段 {target.seg_id} 三级全未命中（本地/缓存/R2）")
    return (f"取段 ok · {target.seg_id} · 段数={len(segs)} "
            f"本地={local} 缓存={cached} R2={remote}")

def check_7_cache() -> str:
    """⑦ 缓存回收器可用（预算读数 + 真跑一次回收）"""
    from senses.cache_reaper import default_reaper
    r = default_reaper(DEVOUR_DIR)
    st = r.status()
    if not st:
        raise Unread("回收器未返回任何预算")
    before = sum(x["used"] for x in st)
    r.reap_all()                       # 真跑一次
    after_st = r.status()
    after = sum(x["used"] for x in after_st)
    freed = before - after
    detail = " ".join(f"{x['name']}={x['used']//1048576}MB/{x['cap']//1048576}MB({x['pct']}%)"
                      for x in after_st)
    return f"回收 ok · {detail} · 本轮释放={freed//1024}KB"

# ─────────────────────────────────────────────────────────────
# 执行器
# ─────────────────────────────────────────────────────────────
CHECKS = [
    ("1. 统一网关可达",      lambda w: check_1_gateway()),
    ("2. 本机 Ollama 车道",  lambda w: check_2_local_llm(w)),
    ("3. 账本可读写",        lambda w: check_3_ledger()),
    ("4. 吞噬零丢帧",        lambda w: check_4_devour()),
    ("5. R2 归档可用",       lambda w: check_5_r2()),
    ("6. 回放三级命中",      lambda w: check_6_playback()),
    ("7. 缓存回收可用",      lambda w: check_7_cache()),
]

def run(with_local=False) -> list[dict]:
    results = []
    for name, fn in CHECKS:
        t0 = time.time()
        try:
            read = fn(with_local)
            results.append({"name": name, "ok": True, "read": read,
                            "why": "", "ms": int((time.time() - t0) * 1000)})
        except Unread as e:
            results.append({"name": name, "ok": False, "read": "未读到",
                            "why": str(e), "ms": int((time.time() - t0) * 1000)})
        except Exception as e:
            results.append({"name": name, "ok": False, "read": "未读到",
                            "why": f"{type(e).__name__}: {e}",
                            "ms": int((time.time() - t0) * 1000)})
    return results

def render(results: list[dict]):
    # ANSI 颜色
    G, R, Y, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
    width = 22
    print(f"\n{'判据':<{width}}{'结果':<6}读数")
    print("─" * 78)
    for r in results:
        if r["ok"]:
            mark, color = "✅", G
        else:
            mark, color = "❌", R
        print(f"{r['name']:<{width}}{color}{mark}{X}  {r['read']}")
        if r["why"]:
            print(f"{'':<{width}}      {Y}↳ {r['why']}{X}")
        print(f"{'':<{width}}      {D}{r['ms']}ms{X}")
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    bar = f"{G}全绿{X}" if passed == total else f"{Y}{passed}/{total}{X}"
    print("─" * 78)
    print(f"通过 {bar}   写入 {STATE}")

def main():
    ap = argparse.ArgumentParser(description="一跑全检 · 真读数验收")
    ap.add_argument("--with-local", action="store_true", help="含本机大模型真发")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    a = ap.parse_args()
    results = run(with_local=a.with_local)
    payload = {"ts": time.time(), "with_local": a.with_local,
               "passed": sum(1 for r in results if r["ok"]),
               "total": len(results), "checks": results}
    STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        render(results)
    return 0 if payload["passed"] == payload["total"] else 1

if __name__ == "__main__":
    sys.exit(main())
