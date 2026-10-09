# tools/run_panel.py —— 面板启动器（显式定根 + 定口 + 可复现）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要专门一个启动器（真机踩过两次）：
#   ① 直接 `python -m panel.server` 会**按当前工作目录**解析 `panel` 包 —— 在别的目录起就会
#      跑成另一份代码（表现为：新加的路由全 404、旧 bug 还在，看着像"改了没生效"）；
#   ② 用 `-c "..."` 起会被引号/空格拆坏（PowerShell Start-Process 的坑）。
# 所以：**chdir 到本仓 + 把本仓塞进 sys.path + 显式端口**，三者都钉死。
#
# 用法：python tools/run_panel.py            # 默认 8800
#       PANEL_PORT=8765 python tools/run_panel.py
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

PORT = int(os.environ.get("PANEL_PORT", "8800"))


def main() -> int:
    from panel.server import app
    import uvicorn
    print("[run_panel] root=%s port=%d" % (ROOT, PORT), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
