# core/loop_graph.py —— 闭环流程图（n8n 风格节点图：圆角节点 + 折线箭头 + 成功/失败分支 + 虚线依赖）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 参考：主人给的节点图样式（触发 → Agent → 分支 成功/失败 + 虚线连到模型/记忆/工具节点）。
# 这里把 8 条闭环画成同样的结构：
#   ① 触发节点（指挥/按键）→ ② 验证器中心节点 → ③ 每条闭环一个节点（按状态着色）
#   → ④ 分支汇聚到「成功 / 失败」两个结果节点；⑤ 验证器下方虚线连到依赖节点（云槽/库槽/触手/ffmpeg…）
# 纪律：全部内联 SVG，不引任何外部资源（无 <image href>、无 CDN）；颜色取设计令牌。
import time

from skills.ui_design import TOKENS


def _c(k: str) -> str:
    return TOKENS["color"].get(k, "#888888")


def _esc(t) -> str:
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _status_color(state: str) -> str:
    s = str(state or "")
    if s.startswith("已跑通"):
        return _c("ok")
    if s == "阻塞":
        return _c("bad")
    if s == "无法确认" or s.startswith("未验"):
        return _c("muted")
    return _c("warn")


DEPS = (
    ("云插件槽", "☁"), ("数据库槽", "▤"), ("触手编队", "❋"),
    ("本机 ffmpeg", "⚙"), ("蓝牙适配器", "❖"), ("授权闸门", "⛨"),
)


def graph(*, verify: dict | None = None) -> dict:
    """闭环流程图数据：节点 + 连线（折线）+ 分支 + 虚线依赖。"""
    if verify is None:
        try:
            from core import loop_verifier as LV
            verify = LV.summary()
        except Exception as exc:                              # noqa: BLE001
            verify = {"闭环": [], "就绪度": None, "阻塞清单": [],
                      "错误": type(exc).__name__}
    loops = verify.get("闭环") or []
    nodes, edges = [], []

    # ① 触发
    nodes.append({"id": "trigger", "标题": "指挥 / 按键", "副标题": "12 个内置按键 · 真验一次",
                  "图标": "⚡", "状态": "触发", "颜色": _c("accent"), "kind": "trigger",
                  "层": 0})
    # ② 验证器
    ok_n = verify.get("已跑通") or 0
    nodes.append({"id": "verifier", "标题": "闭环验证器", "图标": "⛭",
                  "副标题": f"就绪度 {verify.get('就绪度')}% · {ok_n}/{len(loops)} 已跑通",
                  "状态": "运行中", "颜色": _c("accent_2"), "kind": "hub", "层": 1})
    edges.append({"from": "trigger", "to": "verifier", "kind": "flow"})

    # ③ 每条闭环一个节点
    for i, x in enumerate(loops):
        nid = f"loop{i}"
        nodes.append({"id": nid, "标题": x.get("闭环"), "副标题": x.get("状态"),
                      "图标": "✔" if str(x.get("状态")).startswith("已跑通") else
                              ("✖" if x.get("状态") == "阻塞" else "…"),
                      "状态": x.get("状态"), "颜色": _status_color(x.get("状态")),
                      "kind": "loop", "层": 2, "阻塞": x.get("阻塞") or "",
                      "解锁": x.get("解锁") or "", "证据": x.get("证据") or {}})
        edges.append({"from": "verifier", "to": nid, "kind": "fan"})

    # ④ 分支结果
    nodes.append({"id": "success", "标题": "成功", "副标题": "闭环转绿 · 可上线",
                  "图标": "✓", "状态": f"{ok_n} 条", "颜色": _c("ok"),
                  "kind": "result", "层": 3})
    block = verify.get("阻塞") or 0
    nodes.append({"id": "failure", "标题": "失败 / 阻塞", "副标题": "带根因与解锁动作",
                  "图标": "!", "状态": f"{block} 条", "颜色": _c("bad"),
                  "kind": "result", "层": 3})
    for i, x in enumerate(loops):
        good = str(x.get("状态")).startswith("已跑通")
        edges.append({"from": f"loop{i}", "to": "success" if good else "failure",
                      "kind": "branch", "颜色": _c("ok") if good else _c("bad")})

    # ⑤ 虚线依赖
    for j, (name, icon) in enumerate(DEPS):
        nid = f"dep{j}"
        nodes.append({"id": nid, "标题": name, "图标": icon, "副标题": "",
                      "状态": "依赖", "颜色": _c("muted"), "kind": "dep", "层": 4})
        edges.append({"from": "verifier", "to": nid, "kind": "dep"})

    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "nodes": nodes, "edges": edges, "verify": verify,
            "就绪度": verify.get("就绪度"), "阻塞清单": verify.get("阻塞清单") or []}


# ═══════════ 布局 + 内联 SVG ═══════════
W, H = 1080, 720
COL = {0: 40, 1: 250, 2: 560, 3: 880, 4: 250}
NODE_W = {0: 150, 1: 220, 2: 300, 3: 150, 4: 150}
NODE_H = 58


def _layout(g: dict) -> dict:
    """按层分列、列内均分（确定性布局，不依赖随机/浏览器）。"""
    pos = {}
    by_layer: dict = {}
    for n in g["nodes"]:
        by_layer.setdefault(n["层"], []).append(n)
    for layer, nodes in by_layer.items():
        n = len(nodes)
        if layer == 4:                                        # 依赖节点横排在底部
            y0 = H - 150
            for i, node in enumerate(nodes):
                x = 60 + i * 165
                pos[node["id"]] = (x, y0)
            continue
        span = (H - 170) if layer in (2,) else (H - 300)
        top = 60 if layer in (2,) else 120
        step = span / max(1, n) if n > 1 else 0
        for i, node in enumerate(nodes):
            y = top + (i * step if n > 1 else span / 2 - NODE_H / 2)
            pos[node["id"]] = (COL[layer], int(y))
    return pos


def _elbow(x1, y1, x2, y2) -> str:
    """折线（正交）路径：出右 → 中折 → 入左，像流程图那样拐直角。"""
    mid = x1 + max(24, (x2 - x1) // 2)
    return f"M {x1} {y1} H {mid} V {y2} H {x2}"


def svg(g: dict | None = None) -> str:
    """把流程图渲染成内联 SVG（无外部资源；中文用系统字体）。"""
    g = g or graph()
    pos = _layout(g)
    nodes = {n["id"]: n for n in g["nodes"]}
    parts = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" height="auto" role="img" '
        f'aria-label="闭环流程图" style="background:transparent;font-family:'
        f'-apple-system,Segoe UI,Microsoft YaHei,sans-serif">',
        '<defs>',
        f'<marker id="arw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" '
        f'fill="{_c("muted")}"/></marker>',
        f'<marker id="arwok" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" '
        f'fill="{_c("ok")}"/></marker>',
        f'<marker id="arwbad" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" '
        f'fill="{_c("bad")}"/></marker>',
        f'<filter id="soft" x="-20%" y="-20%" width="140%" height="140%">'
        f'<feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#000" '
        f'flood-opacity="0.45"/></filter>',
        '</defs>',
    ]
    # 连线（先画线，节点压在上面）
    for e in g["edges"]:
        a, b = pos.get(e["from"]), pos.get(e["to"])
        if not a or not b:
            continue
        ax = a[0] + NODE_W.get(nodes[e["from"]]["层"], 150)
        ay = a[1] + NODE_H / 2
        bx, by = b[0], b[1] + NODE_H / 2
        kind = e.get("kind")
        if kind == "dep":                                     # 虚线：验证器 → 依赖
            x1, y1 = a[0] + NODE_W.get(nodes[e["from"]]["层"], 220) / 2, a[1] + NODE_H
            x2, y2 = b[0] + 75, b[1]
            d = f"M {x1} {y1} V {y1 + 26} H {x2} V {y2}"
            parts.append(f'<path d="{d}" fill="none" stroke="{_c("border")}" '
                         f'stroke-width="1.4" stroke-dasharray="5 5"/>')
            continue
        color = e.get("颜色", _c("hairline"))
        marker = "arwok" if e.get("颜色") == _c("ok") else (
            "arwbad" if e.get("颜色") == _c("bad") else "arw")
        d = (f"M {ax} {ay} H {bx}" if kind == "flow"
             else _elbow(ax, ay, bx, by))
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" '
                     f'marker-end="url(#{marker})" opacity="0.95"/>')
    # 节点
    for n in g["nodes"]:
        p = pos.get(n["id"])
        if not p:
            continue
        x, y = p
        w = NODE_W.get(n["层"], 150)
        dash = ' stroke-dasharray="6 4"' if n["kind"] == "dep" else ""
        parts.append(
            f'<g filter="url(#soft)"><rect x="{x}" y="{y}" width="{w}" height="{NODE_H}" '
            f'rx="12" fill="rgba(13,21,34,.92)" stroke="{n["颜色"]}" stroke-width="1.6"{dash}/>'
            f'</g>'
            f'<text x="{x + 14}" y="{y + 24}" font-size="14" fill="{n["颜色"]}">'
            f'{_esc(n["图标"])}</text>'
            f'<text x="{x + 34}" y="{y + 24}" font-size="13.5" fill="{_c("text")}">'
            f'{_esc(n["标题"])[:30]}</text>'
            f'<text x="{x + 14}" y="{y + 44}" font-size="11.5" fill="{_c("muted")}">'
            f'{_esc(n["副标题"])[:34]}</text>')
    # 图例
    lx, ly = 60, 18
    for i, (label, key) in enumerate((("已跑通", "ok"), ("部分", "warn"),
                                      ("阻塞", "bad"), ("依赖/未验", "muted"))):
        cx = lx + i * 150
        parts.append(f'<circle cx="{cx}" cy="{ly}" r="5" fill="{_c(key)}"/>'
                     f'<text x="{cx + 12}" y="{ly + 4}" font-size="12" '
                     f'fill="{_c("muted")}">{label}</text>')
    parts.append(f'<text x="{W - 300}" y="{ly + 4}" font-size="12" fill="{_c("muted")}">'
                 f'校验时间 {_esc(g.get("at", ""))} · 就绪度 {g.get("就绪度")}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def status(*, verify: dict | None = None) -> dict:
    """给面板：图数据 + 内联 SVG（一次调用拿全）。

    verify 可由调用方传入**已经算好的**闭环状态：面板上"闭环清单"与"流程图"要的
    本来就是同一份数据，不传就会算两遍（真机实测多花约 8 秒）。
    """
    g = graph(verify=verify)
    return {"graph": g, "svg": svg(g), "就绪度": g.get("就绪度"),
            "节点数": len(g["nodes"]), "连线数": len(g["edges"]),
            "阻塞清单": g.get("阻塞清单")}


__all__ = ["graph", "svg", "status", "DEPS"]
