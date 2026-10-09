# tools/cloud_quota_probe.py —— 云插件额度可读性探针（能读到什么/读不到什么/缺什么权限）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 为什么要有它：主人问过「她是无限制大模型对吧？」—— 这种问题**必须用读数回答**，
#   不能凭印象说"免费无限"。本探针逐个试权威口，把真实 HTTP 结果摊出来：
#   读得到就报数字，读不到就报"缺什么权限"，**绝不把读不到说成没有限额、也绝不编额度**。
# 用法：python tools/cloud_quota_probe.py    退出码 0=探针跑完（不代表额度无限）
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROBES = (("账号订阅/计划", "/subscriptions"),
          ("Workers AI 用量", "/ai/usage"),
          ("账号成员", "/members"))


def main() -> int:
    print("== 云插件额度可读性探针 @", ROOT, "==")
    from core import cloud_runner as CR
    c = CR.credentials()
    print("  凭据来源：%s · 掩码 %s" % (c["来源"] or "（无）", c["掩码"]))
    acct = CR.account_id().get("account") or ""
    if not c.get("token") or not acct:
        print("  ❌ 没有凭据/账号 ⇒ 什么都读不到（这不等于「额度无限」）")
        return 0
    import json
    import urllib.error
    import urllib.request
    base = "https://api.cloudflare.com/client/v4/accounts/" + acct
    for name, path in PROBES:
        url = base + path
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + c["token"]})
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
                print("  ✅ %-14s HTTP %s  %s" % (name, r.status, body[:120]))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            why = "缺权限（令牌没这一项）" if e.code in (401, 403) else "路由/标识不对"
            print("  ❌ %-14s HTTP %s  %s  ← %s" % (name, e.code, body[:90], why))
        except Exception as exc:                             # noqa: BLE001
            print("  ❌ %-14s 连不上：%s" % (name, type(exc).__name__))
    print("\n口径：读不到额度就如实说读不到；**不把读不到说成无限**。要读额度需要带 Workers AI/计费读权限的 API Token。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())