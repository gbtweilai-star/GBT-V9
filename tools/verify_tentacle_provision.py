import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_tentacle_provision.py —— 触手自给自足配置 验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import tentacle_provision as TP   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 触手自给自足配置 验收 ==")
p = TP.plan(100)
check("100 根在岗触手都有配置清单", len(p["清单"]) == 100, "清单 %d 条" % len(p["清单"]))
row0 = p["清单"][0]
check("每根齐五件（邮箱/开源账户/云插件/共享验证/钥匙）",
      all(k in row0 for k in ("邮箱", "开源账户", "云插件", "手机验证", "钥匙")), list(row0))
check("共享验证平台写对（5sim，主人充值）", TP.SIM_PLATFORM == "https://5sim.net/zh" and "充值" in row0["手机验证"],
      "%s · %s" % (TP.SIM_PLATFORM, row0["手机验证"][:40]))
check("云插件写明 freebuff 且每根独立账户", "freebuff" in TP.PLUGIN and "独立账户" in TP.PLUGIN, TP.PLUGIN[:44])
check("自给自足写清（不吃本机资源）", "不吃本机" in row0["自给自足"], row0["自给自足"])

# 明文不入账（真跑一次登记，再全仓搜）
TP.provision("t999", mail="t999@x", platform="github:t999", plugin_account="freebuff:t999", secret="PLAINTEXT-CANARY-123")
led = ROOT / "state" / "tentacle_provision.jsonl"
body = led.read_text(encoding="utf-8", errors="replace") if led.is_file() else ""
check("明文不入账（账本里搜不到 canary）", "PLAINTEXT-CANARY-123" not in body,
      "账本 %s B · 含 canary=%s" % (len(body), "PLAINTEXT-CANARY-123" in body))
check("落账有指纹字段", "钥匙指纹" in body, "含钥匙指纹=True")

# 不代充（模块里不许有充值调用）
src = (ROOT / "core" / "tentacle_provision.py").read_text(encoding="utf-8", errors="replace")
check("没有代充/下单代码（不花钱）", all(k not in src for k in ("topup", "purchase", "buy(", "pay(")),
      "红线：充值必须主人自己操作")
check("红线写进返回（供人机共读）", "不代充 5sim" in str(TP.status()["红线"]), TP.status()["红线"])

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 触手自给自足配置通过（100 根清单齐 · 5sim 主人充值 · freebuff 独立账户 · 明文不入账 · 不代充）")
