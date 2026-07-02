"""Klien PostgREST read-only untuk db.erp-hog.com (antrian print Gantungan Kunci).

Stdlib-only: mint JWT HS256 dengan VPS_DB_JWT_SECRET yang sama dengan
PGRST_JWT_SECRET di VPS, lalu GET ke PostgREST.

PENTING: modul ini TIDAK PERNAH menulis ke DB. Status print job (pending →
in_progress → done) tetap dipegang operator lewat halaman web Operator Print GK.
Tool ini hanya MEMBACA antrian `in_progress` (batch yang sudah diklaim) untuk
disalin file .cdr-nya secara lokal.

Algoritma JWT dijaga identik dengan:
  erp-frontend/src/lib/server/vpsDbJwt.ts
(header {alg:HS256,typ:JWT}, body {iat, exp, role}, base64url, HMAC-SHA256).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _b64url(raw: bytes) -> str:
    """Base64url tanpa padding (sama dengan Buffer.toString('base64url'))."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_jwt(secret: str, role: str = "service_role", ttl_seconds: int = 3600) -> str:
    """Buat JWT HS256 yang divalidasi PostgREST di VPS.

    PostgREST menghitung ulang signature atas string `header.body` yang diterima,
    jadi urutan key / formatting tidak harus identik dengan versi Node.
    """
    now = int(time.time())
    header = _b64url(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    )
    body = _b64url(
        json.dumps(
            {"iat": now, "exp": now + ttl_seconds, "role": role},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{body}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url(sig)}"


class ErpClient:
    """Pembaca antrian print Gantungan Kunci dari PostgREST self-host."""

    def __init__(
        self,
        base_url: str,
        jwt_secret: str,
        jwt_role: str = "service_role",
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.jwt_secret = jwt_secret
        self.jwt_role = jwt_role
        self.timeout = timeout

    def _get(self, path: str, query: dict[str, str]) -> Any:
        token = mint_jwt(self.jwt_secret, self.jwt_role)
        url = f"{self.base_url}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"PostgREST {e.code} saat GET {path}: {detail}") from e

    def fetch_active_print_jobs(self) -> list[dict[str, Any]]:
        """Ambil gk_print_jobs yang SEDANG DIPROSES (status='in_progress') — yaitu
        batch yang sudah DIKLAIM operator di Operator Print GK. BUKAN semua 'pending'.

        Catatan: gk_print_jobs merujuk ke item PESANAN apa adanya — bisa charm L
        tunggal (mis. 'GK-ATM-0003271-L') ATAU bundle (mis. 'GK-ATM-SET-5521-5525-M').
        Bundle TIDAK di-expand di ERP; puller yang meng-expand ke desain anggota di
        ukurannya (lihat duplicate.resolve_cdr_keys) lalu cari <desain>-<ukuran>.cdr.

        Bentuk tiap baris:
          {
            "id": "...",
            "jumlah_pcs_target": 10,
            "item": {"sku": "GK-ATM-SET-5521-5525-M", "name": "..."} | None
          }
        """
        query = {
            "status": "eq.in_progress",
            "select": "id,jumlah_pcs_target,item:items(sku,name)",
            "order": "created_at.asc",
        }
        rows = self._get("gk_print_jobs", query)
        return rows if isinstance(rows, list) else []

    # Alias kompatibilitas (tab Setting memanggil ini saat Test Koneksi).
    fetch_pending_print_jobs = fetch_active_print_jobs
