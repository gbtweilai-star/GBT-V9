import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_pages_mail_accounts.py —— 触手邮箱页 / 账户与密钥页 验收
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 触手邮箱页 / 账户与密钥页 验收 ==")
from core.page_registry import PAGES   # noqa: E402
ids = {p.id for p in PAGES}
check("页面已注册：触手邮箱", "tentacle-mail" in ids, sorted(i for i in ids if "tentacle" in i))
check("页面已注册：触手账户与密钥", "tentacle-accounts" in ids, len(PAGES))
pm = next((p for p in PAGES if p.id == "tentacle-mail"), None)
check("邮箱页声明了接口（含 messages/send）", pm and "/api/tentacle-mail/messages" in pm.接口 and
      "/api/tentacle-mail/send" in pm.接口, list(pm.接口) if pm else None)

srv = (ROOT / "panel" / "server.py").read_text(encoding="utf-8")
check("路由已挂进 server", "tentacle_mail_page" in srv and "tentacle_accounts_page" in srv,
      "server.py")
check("两个页面模块存在", (ROOT / "panel" / "tentacle_mail_page.py").is_file() and
      (ROOT / "panel" / "tentacle_accounts_page.py").is_file(), "panel/")

# ① 邮箱：正文必须留痕可读
from audit.ledger_factory import make_ledger     # noqa: E402
from core.mailbox_fleet import MailboxFleet      # noqa: E402
f = MailboxFleet(make_ledger(), n=3)
BODY = "验收正文：主人必须看得见这一整段（她到底做没做）"
f._audit("send", "GBT-D1", "GBT-D2@example.com", "验收主题", False, "SMTP 未配置", body=BODY)
msgs = f.messages(limit=5)
hit = next((m for m in msgs if m.get("subject") == "验收主题"), None)
check("邮件**正文**留痕并可读回", bool(hit) and hit.get("body") == BODY,
      (hit or {}).get("body", "(无)")[:60])

# ② 不许假装发成功
r = f.send(1, "GBT-D2@example.com", "不许假装", "正文", dry_run=True) if "dry_run" in \
    MailboxFleet.send.__code__.co_varnames else f.send(1, "GBT-D2@example.com", "不许假装", "正文")
check("未配 SMTP 时如实报失败（不假装成功）", r.get("ok") is False and "SMTP" in str(r.get("reason", "")),
      str(r.get("reason"))[:70])

# ③ 账户与密钥页：四服务位 + 只回指纹
from panel import tentacle_accounts_page as TA   # noqa: E402
d = TA._rows(5)
check("四服务位齐（邮箱/云插件/数据库/开源仓库）",
      d.get("服务位") == ["邮箱", "云插件", "数据库", "开源仓库"], d.get("服务位"))
row = (d.get("触手") or [{}])[0]
check("每根触手都有服务位与钥匙读数", bool(row.get("服务位")) and "钥匙" in row,
      list((row.get("服务位") or {}).keys()))
fp = (row.get("钥匙") or {}).get("指纹") or ""
check("密钥**只回指纹**（不回显原文）", len(fp) <= 24, "指纹字段长度 %d" % len(fp))
check("密钥总览四项齐", set(d.get("密钥总览") or {}) >= {"有独立钥匙", "有钥匙但非独立", "没有钥匙", "共"},
      d.get("密钥总览"))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 邮箱页与账户密钥页通过（正文可读回 · 不假装发成功 · 四服务位 · 只回指纹）")
