"""Tarik antrian print GK 'in_progress' dari ERP lalu salin file .cdr secara lokal.

Alur:
  1. Baca config.json.
  2. GET gk_print_jobs (status=in_progress) dari PostgREST VPS → read-only.
     (Sejak WS-A bundle expansion, tiap job = 1 charm L individual.)
  3. Konsolidasi per SKU charm L (jumlahkan pcs kalau ada job ganda).
  4. Untuk tiap charm: cari <sku>.cdr di master folder → salin SEKALI ke hot folder.
  5. Tulis MANIFEST (CSV: SKU, Nama, Jumlah Pcs) + log ke hot folder.

Tidak menulis apa pun ke DB — status job tetap dipegang operator via web.

Dipakai dua arah:
  * CLI   : `python main.py` (atau start.bat).
  * Bridge: server.py meng-import `run_pull()` untuk dipicu tombol di ERP.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Any, Callable

import duplicate
from erp_client import ErpClient

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
REQUIRED_KEYS = ["vps_db_url", "jwt_secret", "master_folder", "hot_folder"]


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            "config.json tidak ditemukan. Jalankan settings.py / gui.py untuk "
            "membuatnya, atau salin config.example.json → config.json."
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise ValueError(f"config.json kurang field: {', '.join(missing)}")
    return cfg


def run_pull(cfg: dict, emit: Callable[[str], None] = print) -> dict[str, Any]:
    """Tarik semua job in_progress lalu salin .cdr-nya (1 file per desain) + manifest.

    Returns ringkasan: {ok, total_jobs, ok_designs, total_pcs, warnings[], fails[],
    hot_folder, manifest, message}. Tidak melempar untuk error per-job; melempar
    hanya untuk error fatal (config / folder / koneksi).
    """
    master_folder = cfg["master_folder"]
    hot_folder = cfg["hot_folder"]
    log_dir = os.path.join(hot_folder, "log")
    os.makedirs(hot_folder, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    if not os.path.isdir(master_folder):
        raise FileNotFoundError(f"FOLDER MASTER tidak ada: {master_folder}")

    emit("Mengambil antrian print GK 'sedang diproses' (in_progress) dari ERP...")
    client = ErpClient(
        base_url=cfg["vps_db_url"],
        jwt_secret=cfg["jwt_secret"],
        jwt_role=cfg.get("jwt_role", "service_role"),
    )
    jobs = client.fetch_active_print_jobs()
    emit(f"Dapat {len(jobs)} job sedang diproses (in_progress).")

    warnings: list[str] = []
    fails: list[str] = []

    def fail(detail: str) -> None:
        fails.append(detail)
        emit(f"  [GAGAL] {detail}")

    if not jobs:
        return {
            "ok": True,
            "total_jobs": 0,
            "ok_designs": 0,
            "total_pcs": 0,
            "warnings": warnings,
            "fails": fails,
            "hot_folder": hot_folder,
            "manifest": None,
            "message": (
                "Tidak ada job in_progress. Klaim/Mulai batch dulu di halaman "
                "Operator Print GK, baru tarik desain."
            ),
        }

    # Konsolidasi per SKU charm L (jumlahkan pcs kalau ada job ganda untuk item sama).
    by_sku: dict[str, dict] = {}
    for job in jobs:
        item = job.get("item") or {}
        sku = (item.get("sku") or "").strip()
        name = item.get("name") or "(tanpa nama)"
        pcs = int(job.get("jumlah_pcs_target") or 0)
        if not sku:
            fail(f"job {job.get('id') or '?'}: tidak punya SKU item — dilewati.")
            continue
        if pcs <= 0:
            warnings.append(f"[{sku}] jumlah_pcs_target={pcs} → dilewati.")
            continue
        ent = by_sku.setdefault(sku, {"sku": sku, "name": name, "pcs": 0})
        ent["pcs"] += pcs

    emit("Membuat index file master .cdr...")
    index = duplicate.build_file_index(master_folder)
    emit(f"Index siap: {len(index)} entri .cdr di folder master.")

    # AUTO-BERSIH (selalu): hapus .cdr + manifest lama supaya hot folder HANYA berisi
    # hasil tarik TERBARU. Dilakukan setelah dipastikan ada job (pull kosong tidak
    # menghapus apa pun).
    removed = duplicate.clear_hotfolder(hot_folder)
    emit(f"Auto-bersih: {removed} file lama dihapus — folder kini hanya batch terbaru.")

    manifest_rows: list[dict] = []
    total_pcs = 0
    ok_designs = 0

    for idx, (sku, ent) in enumerate(sorted(by_sku.items()), start=1):
        found = duplicate.find_cdr(index, sku)
        if not found:
            fail(
                f"SKU {sku} ({ent['name']}): file '{sku}.cdr' TIDAK ADA di folder "
                f"master ({master_folder})."
            )
            continue
        try:
            dest = duplicate.copy_cdr(found, hot_folder)
            manifest_rows.append(
                {"sku": sku, "name": ent["name"], "pcs": ent["pcs"], "file": os.path.basename(dest)}
            )
            total_pcs += ent["pcs"]
            ok_designs += 1
            emit(f"  [{idx:03d}] {sku} → {ent['pcs']} pcs → {os.path.basename(dest)}")
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc().strip().replace("\n", "\n      ")
            fail(
                f"SKU {sku} ({ent['name']}): gagal salin '{os.path.basename(found)}' "
                f"— {type(e).__name__}: {e}\n      Trace: {tb}"
            )

    ts = time.strftime("%Y-%m-%d-%H-%M-%S")
    manifest_path = None
    if manifest_rows:
        manifest_path = duplicate.write_manifest(hot_folder, manifest_rows, ts)
        emit(f"Manifest ditulis: {os.path.basename(manifest_path)} ({ok_designs} desain).")

    # Log run ke file.
    log_path = os.path.join(log_dir, f"run_{ts}.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(f"Run {ts}\n")
        fh.write(
            f"Job in_progress: {len(jobs)} | desain unik disalin: {ok_designs} | "
            f"total pcs: {total_pcs}\n\n"
        )
        if warnings:
            fh.write("PERINGATAN:\n" + "\n".join(warnings) + "\n\n")
        if fails:
            fh.write("GAGAL:\n" + "\n".join(fails) + "\n")

    msg = f"{ok_designs} desain (.cdr) disalin → {total_pcs} pcs total."
    if fails:
        msg += f" {len(fails)} gagal (cek log)."
    return {
        "ok": True,
        "total_jobs": len(jobs),
        "ok_designs": ok_designs,
        "total_pcs": total_pcs,
        "warnings": warnings,
        "fails": fails,
        "hot_folder": hot_folder,
        "manifest": manifest_path,
        "message": msg,
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    try:
        cfg = load_config()
    except (FileNotFoundError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    try:
        summary = run_pull(cfg, emit=lambda m: print(m, flush=True))
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        return 1

    print("")
    print(f"SELESAI. {summary['message']}")
    print(f"  -> {summary['hot_folder']}")
    for f in summary["fails"]:
        print(f"  GAGAL - {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
