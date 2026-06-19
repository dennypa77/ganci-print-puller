"""Bridge HTTP lokal agar tombol di ERP bisa memicu pull+salin .cdr.

Web hanya MEMICU; data ditarik sendiri oleh tool ini dari PostgREST.

Endpoint:
  GET  /health  -> {"ok": true, "service": "ganci-print-puller"}
  POST /pull    -> jalankan run_pull(), balas ringkasan JSON

CORS: hanya origin ERP (prod/staging) + localhost.

Jalankan: python server.py   (atau server.bat). Biarkan jendela terbuka.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from main import load_config, run_pull
from version import __version__

ALLOWED_ORIGINS = {
    "https://erp-hog.com",
    "https://www.erp-hog.com",
    "https://staging.erp-hog.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}

# Cegah dua pull jalan bersamaan (operator klik dobel).
_pull_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = f"ganci-print-puller/{__version__}"

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print(f"[bridge] {self.address_string()} {fmt % args}", flush=True)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] == "/health":
            self._json(200, {"ok": True, "service": "ganci-print-puller", "version": __version__})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/pull":
            self._json(404, {"ok": False, "error": "not found"})
            return
        if not _pull_lock.acquire(blocking=False):
            self._json(409, {"ok": False, "error": "Pull lain sedang berjalan."})
            return
        try:
            cfg = load_config()
            summary = run_pull(cfg, emit=lambda m: print(m, flush=True))
            self._json(200, summary)
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)})
        finally:
            _pull_lock.release()


def main() -> None:
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    cfg = load_config()
    port = int(cfg.get("bridge_port", 8767))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ganci-print-puller bridge aktif di http://127.0.0.1:{port}")
    print("Endpoint: GET /health, POST /pull. Tekan Ctrl+C untuk berhenti.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
