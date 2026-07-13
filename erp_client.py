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

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        token = mint_jwt(self.jwt_secret, self.jwt_role)
        h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if extra:
            h.update(extra)
        return h

    def _get(self, path: str, query: dict[str, str]) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"PostgREST {e.code} saat GET {path}: {detail}") from e

    def _send(self, method: str, path: str, body: Any) -> Any:
        """POST/PATCH JSON ke PostgREST (untuk RPC + update status-tarik)."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=self._headers({"Content-Type": "application/json"}),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"PostgREST {e.code} saat {method} {path}: {detail}") from e

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
            "select": "id,jumlah_pcs_target,item:items(sku,name),batch:gk_order_batches(batch_code)",
            "order": "created_at.asc",
        }
        rows = self._get("gk_print_jobs", query)
        return rows if isinstance(rows, list) else []

    # Alias kompatibilitas (tab Setting memanggil ini saat Test Koneksi).
    fetch_pending_print_jobs = fetch_active_print_jobs

    # ── Tarik SEBAGIAN + progres lintas-komputer (menulis status-tarik ke DB) ──

    def claim_jobs(self, target_charms: int, worker: str) -> list[dict[str, Any]]:
        """Klaim N charm berikutnya yang BELUM ditarik (atomik di DB, aman rebutan).

        Balas daftar job yang berhasil diklaim komputer ini:
          [{job_id, sku, item_name, jumlah_pcs_target, charms}, ...]
        Kosong = tidak ada charm baru (mungkin sudah 100% atau belum ada batch
        in_progress). Menarik desain UTUH → total charm bisa sedikit > target.
        """
        rows = self._send(
            "POST",
            "rpc/claim_gk_print_jobs",
            {"p_target_charms": int(target_charms), "p_worker": str(worker)[:120]},
        )
        return rows if isinstance(rows, list) else []

    def fetch_pull_progress(self) -> dict[str, int]:
        """Progres tarik semua job in_progress: {total_jobs, pulled_jobs,
        total_charms, pulled_charms}."""
        rows = self._send("POST", "rpc/gk_pull_progress", {})
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            r = rows[0]
            return {
                "total_jobs": int(r.get("total_jobs") or 0),
                "pulled_jobs": int(r.get("pulled_jobs") or 0),
                "total_charms": int(r.get("total_charms") or 0),
                "pulled_charms": int(r.get("pulled_charms") or 0),
            }
        return {"total_jobs": 0, "pulled_jobs": 0, "total_charms": 0, "pulled_charms": 0}

    def reset_pull(self) -> None:
        """Reset status-tarik SEMUA job in_progress (pulled_at→null). Untuk pemulihan
        (mis. mau tarik ulang dari awal). TIDAK mengubah status job."""
        self._send(
            "PATCH",
            "gk_print_jobs?status=eq.in_progress",
            {"pulled_at": None, "pulled_by": None},
        )
