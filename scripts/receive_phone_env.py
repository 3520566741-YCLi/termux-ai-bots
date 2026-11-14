#!/usr/bin/env python3
"""一次性接收手机（Termux）发来的配置包，存成 runtime/incoming-phone.tgz（权限 600），收到一次就退出。

用法：receive_phone_env.py <nonce> [超时秒数]
手机上执行（家里同一个 Wi-Fi，或 Tailscale）：
  tar czf - -C ~ ai-council/.env ai-claude/.env ai-codex/.env ai-codex/codex_owner.json .config/ai-tools/.env \
    | curl -sS --data-binary @- http://<Mini PC 地址>:8766/<nonce>
- 只接受路径正好是 /<nonce> 的 POST，最大 1 MB；口令不对的请求一律 404；
- 超时（默认 15 分钟）或收到一次后立即关闭；不打印、不记录收到的内容。
"""
import hmac
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / 'runtime' / 'incoming-phone.tgz'
PORT = 8766
HOSTS = ('192.168.50.186', '192.168.50.4', '100.96.36.26')
done = threading.Event()


def handler_for(nonce: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def reply(self, code, text):
            body = text.encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(404, 'not found\n')

        def do_POST(self):
            if not hmac.compare_digest(self.path.strip('/'), nonce) or done.is_set():
                return self.reply(404, 'not found\n')
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 < length <= 1_000_000:
                return self.reply(413, 'too large\n')
            data = self.rfile.read(length)
            OUT.parent.mkdir(parents=True, exist_ok=True)
            old = os.umask(0o077)
            try:
                OUT.write_bytes(data)
            finally:
                os.umask(old)
            done.set()
            self.reply(200, 'OK，Mini PC 已收到配置，可以关掉这个窗口了。\n')
    return Handler


def main() -> int:
    nonce = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 900
    servers = []
    for host in HOSTS:
        try:
            server = ThreadingHTTPServer((host, PORT), handler_for(nonce))
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    print('listening on', ', '.join(f'{s.server_address[0]}:{PORT}' for s in servers), flush=True)
    got = done.wait(timeout)
    for server in servers:
        server.shutdown()
    print('received' if got else 'timeout', flush=True)
    return 0 if got else 1


if __name__ == '__main__':
    sys.exit(main())
