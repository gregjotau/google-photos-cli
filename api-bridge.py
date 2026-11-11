#!/usr/bin/env python3
"""Local-only broker. Google authentication remains in the signed-in Photos page."""

import http.server, json, secrets, threading, queue
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
KEY = secrets.token_urlsafe(24)
pending = queue.Queue()
results = {}
lock = threading.Lock()
available = threading.Condition(lock)


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, data, status=200, ctype="application/json"):
        body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "https://photos.google.com")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.reply({})

    def auth(self):
        origin = self.headers.get("Origin")
        if origin and origin != "https://photos.google.com":
            return False
        return secrets.compare_digest(
            parse_qs(urlparse(self.path).query).get("key", [""])[0], KEY
        )

    def do_GET(self):
        if not self.auth():
            return self.reply({"error": "unauthorized"}, 403)
        path = urlparse(self.path).path
        if path == "/bootstrap":
            code = (ROOT / "api-bridge.js").read_text().replace("__BRIDGE_KEY__", KEY)
            return self.reply(code, ctype="text/javascript")
        if path == "/next":
            try:
                task = pending.get(timeout=20)
            except queue.Empty:
                task = None
            return self.reply(task)
        if path.startswith("/result/"):
            taskid = path.rsplit("/", 1)[1]
            # Wake immediately when the browser replies, instead of creating
            # dozens of short-lived polling requests for every in-flight RPC.
            with available:
                available.wait_for(lambda: taskid in results, timeout=20)
                result = results.pop(taskid, None)
            return self.reply(result)
        self.reply({"error": "not found"}, 404)

    def do_POST(self):
        if not self.auth():
            return self.reply({"error": "unauthorized"}, 403)
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if urlparse(self.path).path == "/enqueue":
            taskid = secrets.token_hex(12)
            data["id"] = taskid
            pending.put(data)
            return self.reply({"id": taskid})
        if urlparse(self.path).path == "/result":
            with available:
                results[data["id"]] = data
                available.notify_all()
            return self.reply({"ok": True})
        self.reply({"error": "not found"}, 404)


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), H)
    info = ROOT / ".bridge.json"
    info.write_text(json.dumps({"url": "http://127.0.0.1:8765", "key": KEY}))
    info.chmod(0o600)
    print(
        "Broker ready on loopback :8765. Session key stored privately in .bridge.json",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
