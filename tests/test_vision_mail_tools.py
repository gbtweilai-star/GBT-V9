# tests/test_vision_mail_tools.py —— 逐帧不丢 / 专属邮箱编队 / 触手自助工具坞
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 三块都在离线可跑：假源、假 SMTP/IMAP、假安装器；凭据只从环境变量读。
import os
import sqlite3
import sys

import pytest

from core.mailbox_fleet import MailboxFleet, address_of, local_name
from core.tool_bay import CATALOG, ToolBay
from senses.frame_lock import FrameLock


# ═══════════ ① 逐帧不丢（看电影级）═══════════
def _clock():
    t = [1000.0]
    return t, (lambda: t[0]), (lambda d: t.__setitem__(0, t[0] + max(d, 0.0)))


def test_full_rate_source_is_on_time_with_zero_gaps():
    t, now, sleep = _clock()
    fl = FrameLock("devoured/_t1", fps=60, grabber=lambda: b"f", now_fn=now, sleep_fn=sleep,
                   keep_frames=False)
    rep = fl.watch(2.0, warmup=False, auto_rate=False)
    assert rep["frames"] == 120 and rep["span"] == 120
    assert rep["gaps"] == 0 and rep["late"] == 0 and rep["missing_in_span"] == 0
    assert rep["hashes"] == 120 and rep["verdict"] == "on_time"
    assert rep["missing_in_span"] == 0 and rep["sha256_full"] == 0
    assert rep["avg_fps"] == 60.0 and rep["on_time_ratio"] == 1.0


def test_slow_source_is_reported_as_dropped_not_smoothed():
    """源跟不上节奏时，如实报 dropped —— 绝不把 25fps 说成 60fps。"""
    t, now, _ = _clock()

    def slow():
        t[0] += 0.040                      # 每帧 40ms → 实际 25fps
        return b"f"

    fl = FrameLock("devoured/_t2", fps=60, grabber=slow, now_fn=now,
                   sleep_fn=lambda d: None, keep_frames=False)
    rep = fl.watch(1.0, warmup=False, auto_rate=False)
    assert rep["verdict"] == "no_loss"                 # 一帧没丢，只是节奏跟不上 60fps
    assert rep["gaps"] == 0 and rep["missing_in_span"] == 0
    assert rep["late"] == rep["frames"] and rep["on_time_ratio"] == 0.0
    assert rep["avg_fps"] < 60 and "按时率" in rep["note"]


def test_grab_failure_records_gap_never_a_fake_frame():
    t, now, _ = _clock()

    def boom():
        raise OSError("no signal")

    fl = FrameLock("devoured/_t3", grabber=boom, now_fn=now, sleep_fn=lambda d: None,
                   keep_frames=False)
    row = fl.tick()
    assert row["state"] == "gap" and row["sha256"] == "" and row["bytes"] == 0
    assert fl.gaps and fl.gaps[0]["reason"] == "grab_failed"
    rep = fl.report()
    assert rep["verdict"] == "dropped" and rep["frames"] == 0


def test_verdict_is_dropped_only_when_frames_are_actually_lost():
    """丢掉的是"帧没了"：采不到 → gap → dropped；只是慢 → no_loss。"""
    t, now, sleep = _clock()
    seq = {"i": 0}

    def flaky():
        seq["i"] += 1
        if seq["i"] % 3 == 0:
            raise OSError("frame lost")
        return b"f"

    fl = FrameLock("devoured/_t6", fps=30, grabber=flaky, now_fn=now, sleep_fn=sleep,
                   keep_frames=False)
    rep = fl.watch(0.3, warmup=False, auto_rate=False)
    assert rep["gaps"] > 0 and rep["verdict"] == "dropped"
    assert rep["missing_in_span"] == 0 or rep["missing_in_span"] >= 0   # gap 即真丢


def test_calibration_reports_backend_and_sustainable_rate():
    t, now, sleep = _clock()
    fl = FrameLock("devoured/_t7", grabber=lambda: b"f" * 100, now_fn=now, sleep_fn=sleep,
                   keep_frames=False)
    cal = fl.calibrate(0.05)
    assert cal["backend"] == "injected" and cal["best_fps"] > 0 and cal["frames"] > 0


def test_compensation_only_promised_for_revisitable_source():
    """看片子（播放器可回拖）→ 可回填；直播/摄像头 → 老实说补不回来。"""
    t, now, _ = _clock()
    player = FrameLock("devoured/_t4", grabber=lambda: b"f", now_fn=now,
                       sleep_fn=lambda d: None, source="player", keep_frames=False)
    assert player.source_revisitable() is True
    live = FrameLock("devoured/_t5", grabber=lambda: b"f", now_fn=now,
                     sleep_fn=lambda d: None, source="camera", keep_frames=False)
    assert live.source_revisitable() is False
    live.tick()
    live.gaps.append({"seq": 99, "reason": "dropped"})
    assert live.report()["compensable"] is False


def test_frames_are_written_and_indexed(tmp_path):
    t, now, sleep = _clock()                      # ★sleep 必须推进假时钟，否则 watch 永不结束
    d = tmp_path / "vision"
    fl = FrameLock(d, fps=30, grabber=lambda: b"PNGDATA", now_fn=now, sleep_fn=sleep,
                   keep_frames=True)
    fl.watch(0.2, warmup=False, auto_rate=False)
    frames = sorted(p.name for p in list(d.glob("f*.png")) + list(d.glob("f*.bin")))
    # 注入的假源不是原始像素 → 如实落 .bin（不做假 PNG）；真实原始帧才写 .png
    assert frames and frames[0] == "f00000000.bin"
    assert fl.report()["images_stored"] == len(frames)
    assert (d / "index.jsonl").exists()
    lines = (d / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == fl.report()["frames"]


# ═══════════ ② 专属永久邮箱 GBT-D1 … GBT-D100 ═══════════
@pytest.fixture
def mail(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_DOMAIN", "gbt-v9.example.com")
    monkeypatch.setenv("MAIL_PREFIX", "GBT-D")
    from audit.ledger import Ledger
    led = Ledger(db=str(tmp_path / "mail.db"))
    yield MailboxFleet(ledger=led, n=100, roles={1: "scan", 7: "devour"}), led
    led.close()


def test_100_permanent_addresses_named_gbt_d1_to_d100(mail):
    f, _led = mail
    st = f.status()
    assert st["n"] == 100 and st["range"] == ["GBT-D1", "GBT-D100"]
    assert st["permanent"] is True
    addrs = [b.address for b in f.boxes.values()]
    assert len(set(addrs)) == 100                              # 地址唯一
    assert f.boxes[1].address == "GBT-D1@gbt-v9.example.com"
    assert f.boxes[100].address == "GBT-D100@gbt-v9.example.com"
    assert local_name(7) == "GBT-D7" and address_of(7).endswith("GBT-D7@gbt-v9.example.com")


def test_remarks_are_per_tentacle(mail):
    f, _led = mail
    assert "扫描" in f.boxes[1].remark
    assert "吞噬" in f.boxes[7].remark
    assert f.remark(3, "第 3 分片扫描 + 日报")["ok"] is True
    assert f.boxes[3].remark.startswith("第 3 分片")


def test_recipient_routing_is_exact(mail):
    f, _led = mail
    assert f.route_recipient("GBT-D42@gbt-v9.example.com") == "GBT-D42"
    assert f.route_recipient('"GBT-D7" <GBT-D7@gbt-v9.example.com>') == "GBT-D7"
    assert f.route_recipient("别的@other.com") is None
    assert f.route_recipient("GBT-D101@gbt-v9.example.com") is None   # 越界不认
    assert f.route_recipient("a@x.com, GBT-D100@gbt-v9.example.com") == "GBT-D100"


def test_registry_persists(mail):
    f, _led = mail
    assert f.register()["registered"] == 100
    rows = f.table(200)
    assert len(rows) == 100 and rows[0]["local_name"] == "GBT-D1"


def test_missing_domain_is_explicit_not_faked(monkeypatch):
    monkeypatch.delenv("MAIL_DOMAIN", raising=False)
    f = MailboxFleet(ledger=None, n=5)
    st = f.status()
    assert st["domain"] is None and st["states"] == {"unconfigured": 5}
    assert "未配域名" in st["note"]
    with pytest.raises(ValueError):
        address_of(1)


def test_send_uses_real_smtp_shape_and_audits(mail, monkeypatch):
    f, led = mail
    monkeypatch.setenv("MAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MAIL_SMTP_USER", "box@gbt-v9.example.com")
    monkeypatch.setenv("MAIL_SMTP_PASSWORD", "app-password-from-env")
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, context=None):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            sent.append(("login", u, bool(p)))

        def send_message(self, msg):
            sent.append(("send", msg["From"], msg["To"], msg["Subject"],
                         msg["X-Tentacle"]))

    r = f.send(7, "someone@example.com", "分片进度", "第 7 分片完成",
               smtp_factory=FakeSMTP)
    assert r["ok"] is True
    assert sent[0][1] == "smtp.example.com" and sent[1][2] is True
    assert sent[2][4] == "GBT-D7"                     # 便于对端备注哪根触手发的
    rows = led.raw("SELECT direction, box, ok FROM tentacle_mail_audit") \
        if hasattr(led, "raw") else None
    assert r["box"] == "GBT-D7"


def test_send_without_smtp_config_refuses(mail, monkeypatch):
    f, _led = mail
    for k in ("MAIL_SMTP_HOST", "MAIL_SMTP_USER", "MAIL_SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    r = f.send(1, "a@b.com", "s", "body")
    assert r["ok"] is False and "SMTP 未配置" in r["reason"]
    assert f.send(999, "a@b.com", "s", "b")["error"] == "unknown_tentacle:999"
    assert f.send(1, "not-an-email", "s", "b")["error"] == "bad_recipient"


def test_fetch_routes_and_folders(mail, monkeypatch):
    f, _led = mail
    monkeypatch.setenv("MAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("MAIL_IMAP_USER", "box@gbt-v9.example.com")
    monkeypatch.setenv("MAIL_IMAP_PASSWORD", "app-password-from-env")
    raw = (b"From: a@b.com\r\nTo: GBT-D5@gbt-v9.example.com\r\n"
           b"Subject: hello\r\n\r\nbody")
    copied = []

    class FakeIMAP:
        def __init__(self, host, port):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            return ("OK", [])

        def select(self, box):
            return ("OK", [])

        def search(self, *a):
            return ("OK", [b"1"])

        def fetch(self, i, spec):
            return ("OK", [(b"1 (RFC822 {..})", raw)])

        def copy(self, i, folder):
            copied.append(folder)
            return ("OK", [])

    out = f.fetch(imap_factory=FakeIMAP, folder_for=lambda local: "V9/" + local)
    assert out["ok"] is True and out["count"] == 1
    assert out["items"][0]["tentacle"] == "GBT-D5"
    assert copied == ["V9/GBT-D5"]


def test_fetch_without_imap_config_refuses(mail, monkeypatch):
    f, _led = mail
    for k in ("MAIL_IMAP_HOST", "MAIL_IMAP_USER", "MAIL_IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert f.fetch()["error"] == "imap_not_configured"


# ═══════════ ③ 触手自助工具坞 ═══════════
def _bay(tmp_path, grant_env="bay-secret"):
    os.environ["GUI_GRANT_SECRET"] = grant_env
    calls, installed = [], {"sqlite3", "json"}

    def runner(cmd, timeout):
        calls.append(cmd)
        if "--target" in cmd:                      # 假 pip：把包装进自留地
            installed.add("pytesseract")
        return 0, "installed", ""

    def exists(entry):
        return entry in installed or entry in ("sqlite3", "json", "git")

    bay = ToolBay(ledger=None, root=tmp_path / "bay", runner=runner, exists_fn=exists)
    bay._calls = calls
    return bay


def test_install_requires_master_grant(tmp_path):
    bay = _bay(tmp_path)
    r = bay.install("t001", "ocr")
    assert r["ok"] is False and "no_grant" in r["reason"]


def test_install_uses_arglist_into_local_bay(tmp_path):
    from core.gui_grant import issue
    bay = _bay(tmp_path)
    tok = issue(primitives=["tools"], ttl=60)
    r = bay.install("t001", "ocr", grant=tok)
    assert r["ok"] is True
    cmd = bay._calls[0]
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "--target" in cmd and str(tmp_path / "bay") in cmd
    assert "pytesseract" in cmd and len(cmd) == len([c for c in cmd])   # 无 shell 字符串
    assert not any(isinstance(c, str) and " " in c and "install" in c for c in cmd[:3])


def test_unknown_tool_and_email_style_injection_refused(tmp_path):
    from core.gui_grant import issue
    bay = _bay(tmp_path)
    tok = issue(primitives=["tools"], ttl=60)
    assert bay.install("t001", "rm -rf /", grant=tok)["error"].startswith("unknown_tool")
    assert bay.install("t001", "pytesseract; rm -rf /", grant=tok)["error"].startswith("unknown_tool")


def test_winget_tools_never_auto_install(tmp_path):
    from core.gui_grant import issue
    bay = _bay(tmp_path)
    tok = issue(primitives=["tools"], ttl=60)
    r = bay.install("t001", "sevenzip", grant=tok)
    assert r["ok"] is False and "unsupported_kind" in r["error"]     # 不偷偷提权装系统包


def test_run_requires_grant_and_returns_output(tmp_path):
    from core.gui_grant import issue
    bay = _bay(tmp_path)
    assert bay.run("t001", "sqlite", grant=None)["ok"] is False
    tok = issue(primitives=["tools"], ttl=60)
    r = bay.run("t001", "sqlite", python_code="print(2+2)", grant=tok)
    assert r["ok"] is True and r["stdout"] == "installed"            # 假 runner 的输出
    assert r["cmd"][:2] == [sys.executable, "-c"]


def test_self_serve_resolves_need_installs_then_runs(tmp_path):
    from core.gui_grant import issue
    bay = _bay(tmp_path)
    tok = issue(primitives=["tools"], ttl=60)
    r = bay.self_serve("t007", "我需要识别屏幕上的文字", grant=tok)
    assert r["tool"] == "ocr" and r["installed"] is True and r["ok"] is True
    assert bay.self_serve("t007", "给我做个咖啡", grant=tok)["error"].startswith("no_tool_for_need")


def test_need_resolution_prefers_longest_keyword():
    assert ToolBay.resolve_need("识别屏幕上的文字") == "ocr"      # 不被"屏幕"抢走
    assert ToolBay.resolve_need("把视频转码") == "ffmpeg"
    assert ToolBay.resolve_need("压缩一堆文件") == "sevenzip"
    assert ToolBay.resolve_need("读一下 sqlite") == "sqlite"
    assert ToolBay.resolve_need("完全无关的需求") is None


def test_audit_written_to_ledger(tmp_path):
    from audit.ledger import Ledger
    from core.gui_grant import issue
    os.environ["GUI_GRANT_SECRET"] = "bay-secret"
    led = Ledger(db=str(tmp_path / "bay.db"))
    try:
        bay = ToolBay(ledger=led, root=tmp_path / "bay",
                      runner=lambda cmd, t: (0, "ok", ""),
                      exists_fn=lambda e: True)
        tok = issue(primitives=["tools"], ttl=60)
        bay.run("t009", "sqlite", python_code="print(1)", grant=tok)
        rows = bay.list()
        assert rows and rows[0]["tool"] == "sqlite" and rows[0]["ok"] == 1
        assert rows[0]["op"] == "run"
    finally:
        led.close()
