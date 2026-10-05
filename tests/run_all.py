# tests/run_all.py —— 无 pytest 时的轻量执行器
import sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.test_e2e as T

def main():
    tests = [(n, f) for n, f in vars(T).items()
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            import inspect
            params = inspect.signature(fn).parameters
            # 简易夹具注入
            kwargs = {}
            import tempfile
            from audit.ledger import Ledger
            tmp = tempfile.mkdtemp()
            if "ledger" in params: kwargs["ledger"] = Ledger(db=f"{tmp}/l.db")
            if "tmp_path" in params: kwargs["tmp_path"] = Path(tmp)
            if "workdir" in params:
                d = Path(tmp) / "proj"; d.mkdir(); kwargs["workdir"] = d
            if "monkeypatch" in params:
                class MP:
                    def setitem(self, d, k, v): d[k] = v
                kwargs["monkeypatch"] = MP()
            fn(**kwargs)
            print(f"✅ {name}"); passed += 1
        except Exception:
            print(f"❌ {name}"); traceback.print_exc(); failed += 1
    print(f"\n通过 {passed} / 失败 {failed} / 共 {len(tests)}")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
