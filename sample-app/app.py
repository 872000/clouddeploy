"""Tiny sample web service for clouddeploy (stdlib only, no dependencies).

Responds with JSON on ``/`` and a plain ``ok`` on ``/healthz``. The message
comes from the ``MESSAGE`` environment variable, so the deployed container
proves the config's env vars actually arrive.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - stdlib handler naming
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/":
            self._send_json(200, {
                "message": os.environ.get("MESSAGE", "Hello from clouddeploy"),
                "app": os.environ.get("APP_NAME", "hello-cloud"),
            })
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, *args):  # keep stdout clean
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"hello-cloud listening on port {port}")
    server.serve_forever()
