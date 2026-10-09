# tools/gen_capability_docs.py —— 逐项能力细节文档生成（GBT小土豆V9）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「每一项能力都要把能力的细节介绍清楚哈。」
# 从**登记表现算**（不手写、不过期）：delivery_gate（能力+验收器）· operation_bindings（怎么调/产物/判据）·
# page_registry（页面/接口/资源）· modular_deploy（模块三件）· hacker_brain_v9（黑客能力位）。
# 每条落 docs/capabilities/<NN>-<id>.md，另出 index.md。
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "capabilities"


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in str(s))[:48]


def collect() -> list:
    rows = []
    from core import delivery_gate as DG
    from core import operation_bindings as OB
    from core.page_registry import PAGES
    from core import modular_deploy as MD
    from core import hacker_brain_v9 as HB

    binds = list(getattr(OB, "BINDINGS", ()) or ())
    for name, cat, verifier in DG.CAPABILITIES:
        key = name.split("（")[0]
        hit = next((b for b in binds if key[:4] in str(b.get("动作", ""))), None)
        rows.append({
            "id": _safe(name), "能力": name, "类目": cat, "验收器": verifier,
            "动作": (hit or {}).get("动作", ""), "入口": (hit or {}).get("触手", ""),
            "输入": (hit or {}).get("输入", ""), "产物": (hit or {}).get("产物", ""),
            "判据": (hit or {}).get("验收", ""), "备注": (hit or {}).get("备注", ""),
            "状态": (hit or {}).get("状态", "未登记绑定"),
        })
    # 黑客能力位（V9 专属）单独成页
    for c in HB.CAPS:
        rows.append({"id": "hacker-" + c["能力位"], "能力": "黑客·%s" % c["能力位"], "类目": "黑客(安全域)",
                     "验收器": "tools/verify_hacker_brain_v9.py", "动作": c["干什么"],
                     "入口": "core.hacker_brain_v9（%s）" % HB.API["base_url"],
                     "输入": "自然语言指令（授权范围内）", "产物": "分析/结论 + 落账",
                     "判据": "有输出 · 不拒答 · 连贯（三判缺一不算通）", "备注": "门：%s" % c["门"],
                     "状态": "已部署·待钥匙（abliteration 401 已验端点可达）"})
    return rows


def render(r: dict, pages: list) -> str:
    rel_pages = [p for p in pages if any(a and a in " ".join(p.接口) for a in [r["入口"][:12]])][:3]
    L = ["# %s" % r["能力"], "",
         "- **类目**：%s" % r["类目"],
         "- **归属**：GBT小土豆V9",
         "- **独立验收器**：`%s`" % r["验收器"],
         "- **登记状态**：%s" % r["状态"], "",
         "## 它到底干什么（细节）", "", r["动作"] or "（见下方入口与判据）", "",
         "## 怎么调（真入口）", "", "`%s`" % (r["入口"] or "—"), "",
         "## 输入", "", r["输入"] or "—", "",
         "## 产物 / 落点", "", r["产物"] or "—", "",
         "## 判据（怎么算干完）", "", r["判据"] or "—", "",
         "## 实测读数 / 备注", "", r["备注"] or "—", "",
         "## 门 / 红线", "",
         "- 六类现实危险动作（转账·支付·删除·对外发布·隐私载体·不可回滚）**只走主人授权**；",
         "- AI **不许给自己发授权**；本能力若挂 external_llm/filesystem_write 等作用域，需主人在终端授权。", ""]
    if rel_pages:
        L += ["## 相关页面", ""] + ["- [%s %s](%s) —— %s" % (p.图标, p.标题, p.路由, p.一句说明) for p in rel_pages] + [""]
    L += ["---", "_本文由 tools/gen_capability_docs.py 从登记表现算（导出 %s）_" % time.strftime("%Y-%m-%d %H:%M")]
    return chr(10).join(L) + chr(10)


def main() -> int:
    rows = collect()
    from core.page_registry import PAGES
    OUT.mkdir(parents=True, exist_ok=True)
    idx = ["# 能力细节总览（GBT小土豆V9）", "",
           "> 现算导出：能力 **%d** 项 · 页面 **%d** 个 · 每项含 定位/细节/入口/输入/产物/判据/实测/门" % (len(rows), len(PAGES)), "",
           "| # | 能力 | 类目 | 独立验收器 | 登记状态 |", "|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        (OUT / ("%02d-%s.md" % (i, r["id"]))).write_text(render(r, list(PAGES)), encoding="utf-8")
        idx.append("| %d | [%s](%02d-%s.md) | %s | `%s` | %s |"
                   % (i, r["能力"], i, r["id"], r["类目"], r["验收器"], r["状态"]))
    (OUT / "index.md").write_text(chr(10).join(idx) + chr(10), encoding="utf-8")
    print("导出能力文档 %d 篇 + index → %s" % (len(rows), OUT.relative_to(ROOT)))
    print("示例条目:", rows[0]["能力"], "|", rows[0]["验收器"])
    print("含黑客能力位 %d 个" % sum(1 for r in rows if r["类目"].startswith("黑客")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
