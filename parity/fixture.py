# parity/fixture.py —— 本地 HTTP fixture，只绑 127.0.0.1
import asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = """<html><body>
<div class="item">alpha</div><div class="item">beta</div>
<div class="item">gamma</div></body></html>"""

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

def start_fixture(token: str):
    srv = HTTPServer(("127.0.0.1", 0), _H)          # 随机端口
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{srv.server_port}"

# ── 守卫：测试期间拦截一切非 loopback 连接 ──
import socket

_original_connect = socket.socket.connect

def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else str(address)
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError(f"离线验收禁止外连: {host}")     # ★越界即失败
    return _original_connect(self, address)

def install_network_guard():
    socket.socket.connect = _guarded_connect
