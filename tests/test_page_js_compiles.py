# tests/test_page_js_compiles.py —— 页面内嵌 JS 必须真的能编译
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要有这条（2026-10-07 真事故）：
#   页面的 JS 写在 Python 的三引号字符串里。我在 JS 里写 `onclick="mood(\''+g+'\')"`
#   想转义单引号，但 Python 三引号里 `\'` 就是 `'`（反斜杠被吃掉），发出去的 JS 成了
#   `mood(''+g+'')` —— **整块 <script> 编译失败**，于是数字人、话筒条、人格条、开机扫描
#   全部静默失效，页面看起来"加载了但什么都没发生"，控制台也不会给你任何提示。
#   教训：页面 JS 必须用真 JS 引擎编译一遍；"字符串里有没有这个函数"根本查不出来。
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ("panel/voice_page.py", "panel/digital_human_page.py")


def _js_blobs(path: Path) -> list:
    """抽出页面里的 JS 块：JS = \"\"\"...\"\"\" / JS = '''...'''。"""
    s = path.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'JS\s*=\s*(\"\"\"|\'\'\')(.*?)\1', s, re.S):
        out.append(m.group(2))
    return out


def test_page_js_is_extractable():
    blobs = _js_blobs(ROOT / "panel/voice_page.py")
    assert blobs, "voice_page.py 里没找到 JS 块"
    for b in blobs:
        assert len(b) > 200, "JS 块太短，可能是抽错了"


@pytest.mark.skipif(shutil.which("node") is None, reason="本机没有 node，跳过语法编译检查")
@pytest.mark.parametrize("rel", PAGES)
def test_page_js_compiles_with_real_engine(rel):
    """用 node 真编译一遍（--check）：语法错就失败，不让页面静默死掉。

    页面若不用 `JS = \"\"\"` 这种写法（自己拼 HTML），本用例自动跳过（不误报）。
    """
    blobs = _js_blobs(ROOT / rel)
    if not blobs:
        pytest.skip(f"{rel} 未用 JS = 三引号块的形式")
    for i, b in enumerate(blobs):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(b)
            tmp = fh.name
        try:
            r = subprocess.run(["node", "--check", tmp], capture_output=True,
                               text=True, timeout=60, shell=False)
        finally:
            Path(tmp).unlink(missing_ok=True)
        assert r.returncode == 0, f"{rel} 第 {i+1} 个 JS 块编译失败：\n{r.stderr[:600]}"


def test_no_empty_string_concatenation_in_inline_onclick():
    """内联 onclick 里**不允许出现空串拼接**（`mood(''+g+'')`）。

    真事故：JS 里想转义引号写成 `\\'`，但 Python 三引号会把它吃成 `'`，
    发出去就成了 `mood(''+g+'')` → 整块脚本编译失败、页面静默死掉。
    正确做法：data-属性传参（onclick="mood(this.dataset.g)"）。
    注：这里只查这个明确信号；语法的最终判据交给上面的真引擎编译。
    """
    bad = []
    for rel in PAGES:
        s = (ROOT / rel).read_text(encoding="utf-8")
        for m in re.finditer(r'onclick="([^"]*)"', s):
            if "''" in m.group(1):
                bad.append((rel, m.group(1)[:60]))
    assert not bad, f"内联 onclick 里有空串拼接：{bad[:4]}"


def test_voice_page_declares_required_hooks():
    """语音页必须真的定义并调用这些钩子（数字人/话筒/嗓子/人格/扫描）。"""
    s = (ROOT / "panel/voice_page.py").read_text(encoding="utf-8")
    for need in ("async function rig(", "async function boot(", "function micstat(",
                 "async function voicestat(", "async function pgstat(",
                 "async function mood(", "async function reactSet("):
        assert need in s, f"语音页缺 {need}"
    assert "micstat(false); voicestat(); pgstat();" in s, "状态条没在启动时拉取"
