# tools/entry.py —— 入口副作用只此一处，任何重写不许删
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))     # 坑3：这一行是活死线

def assert_yaml_order(doc: str):
    keys = ["keys", "headers", "models"]   # 坑4：顺序定死，插错就抛
    pos = [doc.index(k) for k in keys]
    assert pos == sorted(pos), f"块顺序错误: {list(zip(keys, pos))}"
