# tools/gen_cognition_doc.py —— 由登记表生成《认知系统架构》文档（与代码同源，永不跑偏）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import io
from pathlib import Path

from core.cognition_system import GROUPS, MODULES, EDGES, FLOW, validate, mermaid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "认知系统架构.md"


def build() -> str:
    v = validate()
    live = sum(1 for m in MODULES if not m.planned)
    L: list[str] = []
    L.append("# GBT小土豆V9 · 认知系统架构（50 模块 / 6 组 / 9 步主流程）\n")
    L.append("> 本文由 `tools/gen_cognition_doc.py` 从 `core/cognition_system.py` 生成，与代码同源；")
    L.append(f"> 校验：模块 {v['modules']} · 组 {v['groups']} · 悬空输入 {len(v['dangling_inputs'])} · "
             f"悬空边 {len(v['dangling_edges'])} · ok={v['ok']} · "
             f"已接代码 {live} 项 / planned {len(v['planned'])} 项\n")

    L.append("\n## 一、分组与职责边界\n")
    L.append("| 组 | 职责（为什么存在） | 模块数 | 成员 |")
    L.append("|---|---|---|---|")
    for g, meta in GROUPS.items():
        ms = [m for m in MODULES if m.group == g]
        L.append(f"| **{meta['cn']}** | {meta['job']} | {len(ms)} | "
                 + "、".join(m.id for m in ms) + " |")

    L.append("\n## 二、模块清单（职责 / 输入 → 输出 / 实现落点）\n")
    L.append("| 模块 | 组 | 职责 | 输入 → 输出 | 实现落点 |")
    L.append("|---|---|---|---|---|")
    for m in MODULES:
        anchor = m.impl or "**planned（未实现，明说）**"
        io_ = "、".join(m.inputs) + " → " + "、".join(m.outputs)
        tag = "（状态类）" if m.state else ""
        L.append(f"| {m.id}{tag} | {GROUPS[m.group]['cn']} | {m.duty} | {io_} | `{anchor}` |")
    L.append("\n> 仍属 planned 的模块：" + ("、".join(v["planned"]) or "无")
             + "（这些是「还没有独立实现」的认知能力，按纪律标注，不冒充）。\n")

    L.append("\n## 三、模块间数据流（谁喂谁）\n")
    L.append("```mermaid")
    L.append(mermaid())
    L.append("```\n")
    L.append("主干边清单：\n")
    L.append("```")
    for a, b in EDGES:
        L.append(f"{a} → {b}")
    L.append("```\n")

    L.append("\n## 四、协作主流程（一次真实任务怎么走）\n")
    for f in FLOW:
        L.append(f"**{f.step}. {f.phase}** — 参与：{'、'.join(f.modules)}")
        L.append(f"> {f.what}\n")

    L.append("\n## 五、触手吸收（每根触手可调用全部 50 项）\n")
    L.append("`core/cognition_system.TentacleGrasp` 把 50 个模块登记为**本体全部能力**："
             "每根触手（含 100 根编队）各持一份完整清单，`can(触手, 模块)` 可核对；"
             "调用执法由 `core/commander.py::call_as` 与 `core/tentacle_fleet.py` 的指挥官工单闸门承担。\n")

    L.append("\n## 六、触手永久记忆（Obsidian 接入）\n")
    L.append("`body/obsidian.py`：库从 `%APPDATA%\\obsidian\\obsidian.json` 自动发现，")
    L.append("**排除 V8 与 Sandbox**；写入落在命名空间子目录 `GBT小土豆V9-触手记忆/触手/GBT-D{n}.md`，")
    L.append("绝不铺在库根；主脑全库可读可检索；触手记忆为**追加式**（只增不改）。")
    L.append("路径经 `normpath` + 前缀断言，`../` 越界一律拒。\n")
    return "\n".join(L)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = build()
    OUT.write_text(text, encoding="utf-8")
    print(f"已生成 {OUT}（{len(text)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
