# core/mailbox_fleet.py —— 触手专属邮箱编队：GBT-D1 … GBT-D100（永久地址 + 备注）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径（主人 2026-10-06）：每根触手都有自己**专属且永久**的邮箱，编号 GBT-D1…GBT-D100，
# 便于备注"这根触手是谁、负责什么"。触手自己收发自己那一格的信，互不串台。
#
# 两种落地模式（都真跑，不许假装开通）：
#   · catchall（默认，最省事）：一个域名 + 一个收件箱，100 个地址都是 <域名> 的收信规则，
#     按收件人（To:) 路由到各自触手的信箱格 —— 地址永久、无需 100 个账号。
#   · dedicated：提供商支持 API 建邮箱/规则时逐号开通（凭据只从环境变量读）。
#
# 纪律：邮箱凭据（授权码/API token）只从环境变量或密钥服务读取，源码零字面量；
#       发信/收信都落审计（who、to、subject 摘要，不含正文与凭据）。
import email
import imaplib
import json
import os
import re
import smtplib
import ssl
import time
from dataclasses import dataclass, asdict, field
from email.message import EmailMessage

from senses.sqldialect import txn
from core.swallow import swallow as _swallow

DEFAULT_PREFIX = os.environ.get("MAIL_PREFIX", "GBT-D")
DEFAULT_DOMAIN = os.environ.get("MAIL_DOMAIN", "")          # 例：example.com（必须你自备）
DEFAULT_MODE = os.environ.get("MAIL_MODE", "catchall")      # catchall | dedicated
ADDR_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# 触手用途备注（主人可改；编号 → 职责，一眼看得懂这根触手在干嘛）
ROLE_REMARKS = {
    "scan": "扫描/交叉互扫", "devour": "吞噬能采集归档", "voice": "听说（TTS/ASR）",
    "guard": "安全守卫/见证核对", "code": "编程与装配", "memory": "记忆与知识库",
    "mesh": "总线与调度",
}


def local_name(n: int) -> str:
    """GBT-D1 … GBT-D100（不补零，便于口头与备注）。"""
    return f"{os.environ.get('MAIL_PREFIX', DEFAULT_PREFIX)}{int(n)}"


def address_of(n: int, domain: str | None = None) -> str:
    d = domain or os.environ.get("MAIL_DOMAIN", DEFAULT_DOMAIN)
    if not d:
        raise ValueError("MAIL_DOMAIN 未配置：专属永久邮箱需要一个你自己的域名"
                         "（catchall 模式：把该域名收信指向一个收件箱即可）")
    return f"{local_name(n)}@{d}"


@dataclass
class Mailbox:
    n: int
    local: str
    address: str
    mode: str = DEFAULT_MODE
    remark: str = ""
    role: str = ""
    state: str = "planned"          # planned | active | suspended
    created_at: str = ""
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


class MailboxFleet:
    """100 个专属永久地址的登记与收发。收信按收件人路由到各自触手。"""

    def __init__(self, ledger=None, *, n=100, domain=None, mode=None,
                 prefix=None, start=1, remarks=None, roles=None):
        self.led = ledger
        self.n = int(n)
        self.start = int(start)
        # ★环境变量在**调用时**读：部署脚本常先 import 后设 env，导入期常量会读空
        self.domain = domain if domain is not None else os.environ.get(
            "MAIL_DOMAIN", DEFAULT_DOMAIN)
        self.mode = (mode or os.environ.get("MAIL_MODE", DEFAULT_MODE)).lower()
        self.prefix = prefix or os.environ.get("MAIL_PREFIX", DEFAULT_PREFIX)
        self.boxes: dict[int, Mailbox] = {}
        self._init_table()
        self.build(remarks=remarks, roles=roles)          # 装配即编队，不留空壳

    # ── 编队装配：编号连续、地址唯一（重复即报错，不静默覆盖）──
    def build(self, remarks: dict | None = None, roles: dict | None = None) -> dict:
        remarks = remarks or {}
        roles = roles or {}
        seen: set = set()
        self.boxes.clear()
        for i in range(self.start, self.start + self.n):
            local = f"{self.prefix}{i}"
            try:
                addr = f"{local}@{self.domain}" if self.domain else ""
            except Exception:                                 # noqa: BLE001
                addr = ""
            if addr and addr in seen:
                raise ValueError(f"地址重复：{addr}")
            seen.add(addr)
            role = roles.get(i, "")
            self.boxes[i] = Mailbox(
                n=i, local=local, address=addr, mode=self.mode,
                remark=remarks.get(i) or remarks.get(local) or ROLE_REMARKS.get(role, ""),
                role=role if isinstance(role, str) else "",
                state="planned" if addr else "unconfigured",
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return self.status()

    def _init_table(self):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS tentacle_mailbox ("
                            "local_name TEXT PRIMARY KEY, address TEXT, domain TEXT,"
                            " mode TEXT, role TEXT, remark TEXT, state TEXT,"
                            " created_at TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS tentacle_mail_audit ("
                            "msg_id TEXT, direction TEXT, box TEXT, peer TEXT,"
                            " subject TEXT, ok INTEGER, detail TEXT, at TEXT)")
                # ★ 2026-10-09 主人要求「邮箱内容必须用户可以看到她到底有没有做」⇒ 正文单独留痕
                cur.execute("CREATE TABLE IF NOT EXISTS tentacle_mail_body ("
                            "msg_id TEXT PRIMARY KEY, direction TEXT, box TEXT, peer TEXT,"
                            " subject TEXT, body TEXT, ok INTEGER, detail TEXT, at TEXT)")
        except Exception as e:
            _swallow(__file__, e)


    def register(self) -> dict:
        """把编队写进登记表（幂等）。未配域名 → 只登记编号与备注，明确标 unconfigured。"""
        if self.led is None:
            return {"registered": 0, "reason": "no_ledger"}
        n = 0
        for box in self.boxes.values():
            try:
                with txn(self.led) as cur:
                    cur.execute("INSERT INTO tentacle_mailbox (local_name, address,"
                                " domain, mode, role, remark, state, created_at)"
                                " VALUES (?,?,?,?,?,?,?,?)"
                                " ON CONFLICT (local_name) DO UPDATE SET"
                                " address=EXCLUDED.address, mode=EXCLUDED.mode,"
                                " role=EXCLUDED.role, remark=EXCLUDED.remark,"
                                " state=EXCLUDED.state, domain=EXCLUDED.domain",
                                (box.local, box.address, self.domain or "", box.mode,
                                 box.role, box.remark, box.state, box.created_at))
                n += 1
            except Exception:
                continue
        return {"registered": n, "mode": self.mode, "domain": self.domain or None}

    # ── 收信路由：一个收件箱 → 100 个触手格 ──
    def route_recipient(self, to_header: str) -> str | None:
        """按收件人取出触手编号（返回 local_name）。不是本编队的地址 → None。"""
        raw = str(to_header or "")
        addrs = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", raw)
        for a in addrs:
            local = a.split("@", 1)[0]
            if local.startswith(self.prefix):
                try:
                    i = int(local[len(self.prefix):])
                except ValueError:
                    continue
                if self.start <= i < self.start + self.n:
                    return local
        return None

    def _audit(self, direction, box, peer, subject, ok, detail="", body=""):
        if self.led is None:
            return
        # ★ 正文留痕（页面要用它给主人看「到底做没做」）
        mid = os.urandom(8).hex()
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO tentacle_mail_audit (msg_id, direction, box,"
                            " peer, subject, ok, detail, at) VALUES (?,?,?,?,?,?,?,?)",
                            (mid, direction, box, peer,
                             (subject or "")[:120], 1 if ok else 0, detail[:200], at))
                # 同一封的**正文**落 tentacle_mail_body（页面直接展示）
                cur.execute("INSERT OR REPLACE INTO tentacle_mail_body (msg_id, direction, box,"
                            " peer, subject, body, ok, detail, at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (mid, direction, box, peer, (subject or "")[:200], (body or "")[:4000],
                             1 if ok else 0, (detail or "")[:300], at))
        except Exception as e:
            _swallow(__file__, e)


    # ── 发信（真实 SMTP；凭据只从环境变量读）──
    def _smtp_conf(self) -> dict:
        conf = {"host": os.environ.get("MAIL_SMTP_HOST", ""),
                "port": int(os.environ.get("MAIL_SMTP_PORT", "465")),
                "user": os.environ.get("MAIL_SMTP_USER", ""),
                "password": os.environ.get("MAIL_SMTP_PASSWORD", ""),
                "ssl": os.environ.get("MAIL_SMTP_SSL", "1") != "0"}
        return conf

    def mail_available(self) -> tuple[bool, str]:
        c = self._smtp_conf()
        if not c["host"] or not c["user"] or not c["password"]:
            return False, ("SMTP 未配置：设 MAIL_SMTP_HOST/PORT/USER/PASSWORD"
                           "（授权码走环境变量，源码零凭据）")
        return True, "ok"

    def send(self, tentacle_n: int, to: str, subject: str, body: str, *,
             smtp_factory=None, dry_run=False) -> dict:
        box = self.boxes.get(int(tentacle_n))
        if box is None:
            return {"ok": False, "error": f"unknown_tentacle:{tentacle_n}"}
        if not ADDR_RE.match(str(to or "")):
            return {"ok": False, "error": "bad_recipient"}
        ok_avail, why = self.mail_available()
        record = {"box": box.local, "to": to, "subject": subject, "mode": self.mode}
        if not ok_avail or dry_run:
            self._audit("send", box.local, to, subject, False, why if not ok_avail else "dry_run", body=body)
            return {"ok": False, "reason": why if not ok_avail else "dry_run", **record}
        msg = EmailMessage()
        msg["From"] = box.address or self._smtp_conf()["user"]
        msg["To"] = to
        msg["Subject"] = subject
        msg["X-Tentacle"] = box.local                 # 便于对端备注"哪根触手发的"
        msg.set_content(body)
        conf = self._smtp_conf()
        try:
            factory = smtp_factory or (smtplib.SMTP_SSL if conf["ssl"] else smtplib.SMTP)
            if conf["ssl"]:
                with factory(conf["host"], conf["port"],
                             context=ssl.create_default_context()) as s:
                    s.login(conf["user"], conf["password"])
                    s.send_message(msg)
            else:
                with factory(conf["host"], conf["port"]) as s:
                    s.starttls(context=ssl.create_default_context())
                    s.login(conf["user"], conf["password"])
                    s.send_message(msg)
        except Exception as exc:                              # noqa: BLE001
            self._audit("send", box.local, to, subject, False, type(exc).__name__, body=body)
            return {"ok": False, "error": f"smtp_error:{type(exc).__name__}", **record}
        self._audit("send", box.local, to, subject, True, "sent", body=body)
        return {"ok": True, "sent": True, **record}

    # ── 收信（真实 IMAP；按收件人分派到触手格）──
    def _imap_conf(self) -> dict:
        return {"host": os.environ.get("MAIL_IMAP_HOST", ""),
                "port": int(os.environ.get("MAIL_IMAP_PORT", "993")),
                "user": os.environ.get("MAIL_IMAP_USER", ""),
                "password": os.environ.get("MAIL_IMAP_PASSWORD", "")}

    def fetch(self, *, limit=20, mailbox="INBOX", folder_for=None,
              imap_factory=None, since=None) -> dict:
        """拉信并按 To: 分派。folder_for(local_name) 返回该触手的分拣文件夹（可选）。"""
        conf = self._imap_conf()
        if not conf["host"] or not conf["user"] or not conf["password"]:
            return {"ok": False, "error": "imap_not_configured",
                    "hint": "设 MAIL_IMAP_HOST/PORT/USER/PASSWORD"}
        factory = imap_factory or imaplib.IMAP4_SSL
        try:
            with factory(conf["host"], conf["port"]) as M:
                M.login(conf["user"], conf["password"])
                M.select(mailbox)
                crit = "(UNSEEN)" if not since else f'(SINCE "{since}")'
                typ, data = M.search(None, crit)
                ids = (data[0].split() if data and data[0] else [])[-int(limit):]
                out = []
                for i in ids:
                    typ, raw = M.fetch(i, "(RFC822)")
                    if not raw or not raw[0]:
                        continue
                    msg = email.message_from_bytes(raw[0][1])
                    local = self.route_recipient(msg.get("To"))
                    item = {"seq": i.decode(), "to": msg.get("To"),
                            "from": msg.get("From"), "subject": msg.get("Subject"),
                            "tentacle": local}
                    self._audit("recv", local or "-", msg.get("From") or "",
                                msg.get("Subject") or "", bool(local),
                                "routed" if local else "unrouted")
                    if local and folder_for:
                        try:
                            M.copy(i, folder_for(local))     # 分拣到该触手专用文件夹
                        except Exception as e:
                            _swallow(__file__, e)
                    out.append(item)
                return {"ok": True, "count": len(out), "items": out}
        except Exception as exc:                              # noqa: BLE001
            return {"ok": False, "error": f"imap_error:{type(exc).__name__}"}

    # ── 状态 ──
    def messages(self, *, limit: int = 200, box: str = "", direction: str = "") -> list:
        """**带正文**的邮件留痕（页面直接展示，主人可核对）。"""
        if self.led is None:
            return []
        try:
            with txn(self.led) as cur:
                q = ("SELECT msg_id, direction, box, peer, subject, body, ok, detail, at"
                     " FROM tentacle_mail_body WHERE 1=1")
                a: list = []
                if box:
                    q += " AND box=?"
                    a.append(box)
                if direction:
                    q += " AND direction=?"
                    a.append(direction)
                q += " ORDER BY at DESC LIMIT ?"
                a.append(int(limit))
                cur.execute(q, a)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            return []

    def status(self) -> dict:
        by_state: dict = {}
        for b in self.boxes.values():
            by_state[b.state] = by_state.get(b.state, 0) + 1
        return {"n": len(self.boxes), "mode": self.mode, "domain": self.domain or None,
                "prefix": self.prefix, "range": [local_name(self.start),
                                                 local_name(self.start + self.n - 1)],
                "sample": [self.boxes[self.start].as_dict(),
                           self.boxes[self.start + self.n - 1].as_dict()]
                if self.boxes else [],
                "states": by_state, "permanent": True,
                "note": ("地址永久：编号固定、不回收、不重排（备注按编号写）"
                         if self.domain else
                         "未配域名：已登记编号与备注；设 MAIL_DOMAIN 后即为永久地址")}

    def remark(self, tentacle_n: int, text: str) -> dict:
        box = self.boxes.get(int(tentacle_n))
        if box is None:
            return {"ok": False, "error": f"unknown_tentacle:{tentacle_n}"}
        box.remark = str(text)[:200]
        self.register()
        return {"ok": True, "box": box.local, "remark": box.remark}

    def table(self, limit=100) -> list:
        if self.led is None:
            return []
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT local_name, address, mode, role, remark, state"
                            " FROM tentacle_mailbox ORDER BY CAST(SUBSTR(local_name,"
                            " LENGTH(?) + 1) AS INTEGER) LIMIT ?",
                            (self.prefix, int(limit)))
                cols = [d[0] for d in (cur.description or [])]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            return []


__all__ = ["MailboxFleet", "Mailbox", "local_name", "address_of",
           "ROLE_REMARKS", "DEFAULT_PREFIX"]
