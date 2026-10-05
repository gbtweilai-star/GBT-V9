# tools/brand_scan.py —— 三层零残留扫描
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 用法: python tools/brand_scan.py <dashboard/dist> <http://127.0.0.1:8088>
import sys, re, json, zipfile
from pathlib import Path

BRAND_WORDS = [r"Octop", r"OCTOP", r"octop"]        # 大小写变体都查

# 内部层白名单：这些出现是正常的，不算 UI 泄漏
ALLOWLIST = [
    r"octop:", r"OCTOP_", r"\.octop", r"octop\.db", r"/api/",
    r"octop-harness", r"octop-memory", r"octop-gateway", r"octop-browser",
    r"src/octop", r"prefixCls", r'className', r"data-", r"__octop",
]


def _visible_hits(text: str):
    """返回(命中, 是否命中白名单)"""
    hits = []
    for w in BRAND_WORDS:
        for m in re.finditer(w, text):
            ctx = text[max(0, m.start()-60):m.end()+60]
            allowed = any(re.search(a, ctx) for a in ALLOWLIST)
            hits.append({"word": m.group(0), "ctx": ctx.replace("\n", " "),
                         "internal": allowed})
    return hits


# ── 第 1 层：构建产物（含 sourcemap）──
def scan_build(dist: Path):
    findings = []
    exts = {".html", ".js", ".css", ".json", ".map", ".svg", ".txt", ".webmanifest"}
    for p in dist.rglob("*"):
        if p.suffix.lower() not in exts: continue
        try: text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception: continue
        for h in _visible_hits(text):
            h["where"] = str(p.relative_to(dist))
            h["layer"] = "build"
            findings.append(h)
    return findings


# ── 第 2 层：运行时 DOM（Playwright）──
def scan_dom(url, token=None):
    from playwright.sync_api import sync_playwright
    findings = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        if token:
            pg.context.add_cookies([{"name": "token", "value": token,
                                     "url": url}])
        pg.goto(url, wait_until="networkidle", timeout=60000)
        # 标题
        t = pg.title()
        for h in _visible_hits(t):
            h.update(layer="dom", where="document.title"); findings.append(h)
        # 可见文本
        body = pg.inner_text("body")
        for h in _visible_hits(body):
            h.update(layer="dom", where="visible-text"); findings.append(h)
        # 可见属性（alt/aria-label/placeholder/title）
        for sel in ["img", "[aria-label]", "[placeholder]", "[title]"]:
            for el in pg.query_selector_all(sel):
                for attr in ("alt", "aria-label", "placeholder", "title"):
                    v = el.get_attribute(attr)
                    if v:
                        for h in _visible_hits(v):
                            h.update(layer="dom", where=f"{sel}[{attr}]")
                            findings.append(h)
        b.close()
    return findings


# ── 第 3 层：接口响应（扫 UI 真正调用的 API）──
def scan_api(url, endpoints, token=None):
    import httpx
    findings = []
    h = {"Authorization": f"Bearer {token}"} if token else {}
    for ep in endpoints:
        try:
            r = httpx.get(url.rstrip("/") + ep, headers=h, timeout=10)
            for m in _visible_hits(r.text):
                m.update(layer="api", where=ep); findings.append(m)
        except Exception as e:
            findings.append({"layer": "api", "where": ep, "error": str(e)})
    return findings


def report(findings):
    visible = [f for f in findings if not f.get("internal")]
    internal = [f for f in findings if f.get("internal")]
    print(f"\n=== 零残留报告 ===")
    print(f"可见残留(必须清零): {len(visible)}")
    for f in visible[:40]:
        print(f"  ✗ [{f['layer']}/{f.get('where')}] {f['word']} … {f.get('ctx','')[:80]}")
    print(f"内部命中(白名单，可保留): {len(internal)}")
    print("\n判定:", "✅ 通过" if not visible else "❌ 仍有可见残留，APP 上还会露 Octop")
    return {"visible": visible, "internal": internal, "pass": not visible}


if __name__ == "__main__":
    dist, url = Path(sys.argv[1]), sys.argv[2]
    allf = scan_build(dist) + scan_dom(url) + scan_api(
        url, ["/api/openapi.json", "/api/branding", "/api/settings"])
    json.dump(report(allf), open("brand_scan.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
