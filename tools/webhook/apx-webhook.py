#!/usr/bin/env python3
"""Gitee webhook receiver for ai-passport-simulator auto-deploy.

Listens on 0.0.0.0:9000. Gitee sends POST /webhook with the shared
secret in the X-Gitee-Token header; on a push to main this spawns
/opt/apx-deploy.sh detached and answers 200 immediately.
"""
import hashlib
import hmac
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET_FILE = "/opt/apx-webhook.secret"
DEPLOY_SCRIPT = "/opt/apx-deploy.sh"
LOG_FILE = "/opt/apx-webhook-deploy.log"
LISTEN_PORT = 9000


def load_secret() -> bytes:
    with open(SECRET_FILE, "rb") as fh:
        return fh.read().strip()


SECRET = load_secret()


class Handler(BaseHTTPRequestHandler):
    server_version = "apx-webhook/1.0"

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # health probe
        self._reply(200, {"service": "apx-webhook", "status": "ok"})

    def do_POST(self):
        if self.path != "/webhook":
            self._reply(404, {"error": "not found"})
            return
        token = self.headers.get("X-Gitee-Token", "")
        if not hmac.compare_digest(token.encode(), SECRET):
            self._reply(403, {"error": "invalid token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._reply(400, {"error": "bad json"})
            return
        ref = payload.get("ref", "")
        if ref != "refs/heads/main":
            self._reply(200, {"ok": True, "ignored": ref or "no ref"})
            return
        subprocess.Popen(
            ["/bin/bash", "-c",
             f"nohup {DEPLOY_SCRIPT} >> {LOG_FILE} 2>&1 < /dev/null &"],
            start_new_session=True,
        )
        self._reply(200, {"ok": True, "deploy": "triggered"})

    def log_message(self, fmt, *args):  # keep systemd journal tidy
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"apx-webhook listening on :{LISTEN_PORT}", flush=True)
    server.serve_forever()
