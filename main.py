"""Tarik antrian print GK dari ERP lalu salin file .cdr secara lokal.

Output DIPISAH per BATCH: tiap batch punya sub-folder sendiri di dalam hot folder
(nama = batch_code, mis. <hot>/2026-07-08-B4/). TIDAK ada auto-hapus — file lama
dibiarkan (hapus manual).

Dua mode:
  * FULL   — `run_pull`: ambil SEMUA job in_progress, salin semua. Perilaku 1
             komputer. Tidak menandai status-tarik di DB.
  * KLAIM  — `run_pull_claim`: klaim N charm BERIKUTNYA yang belum ditarik (atomik)
             → banyak komputer bagi beban tanpa tumpang tindih; menandai pulled_at.

Keduanya TIDAK mengubah status JOB (pending/in_progress/done) — dipegang web.
"""

from __future__ import annotations

import json
import os
import re
import socket
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


def worker_name(cfg: dict) -> str:
    """Nama komputer untuk penanda 'ditarik oleh' (config.worker_name atau hostname)."""
    w = (cfg.get("worker_name") or "").strip()
    if w:
        return w
    try:
        return socket.gethostname() or "komputer"
    except Exception:  # noqa: BLE001
        return "komputer"


def _client(cfg: dict) -> ErpClient:
    return ErpClient(
        base_url=cfg["vps_db_url"],
        jwt_secret=cfg["jwt_secret"],
        jwt_role=cfg.get("jwt_role", "service_role"),
    )


def _safe_folder(name: str) -> str:
    """Nama sub-folder aman untuk Windows (buang karakter ilegal)."""
    s = re.sub(r'[<>:"/\\|?*]', "_", str(name or "tanpa-batch")).strip().rstrip(".")
    return s or "tanpa-batch"


def _expand_jobs(
    jobs_norm: list[dict], emit: Callable[[str], None], warnings: list[str], fails: list[str]
) -> dict[str, dict]:
    """jobs_norm = [{sku, name, pcs}] → by_sku (expand bundle→anggota, konsolidasi)."""
    by_sku: dict[str, dict] = {}
    for j in jobs_norm:
        sku = (j.get("sku") or "").strip()
        name = j.get("name") or "(tanpa nama)"
        pcs = int(j.get("pcs") or 0)
        if not sku:
            fails.append("job tanpa SKU item — dilewati.")
            continue
        if pcs <= 0:
            warnings.append(f"[{sku}] jumlah_pcs_target={pcs} → dilewati.")
            continue
        keys = duplicate.resolve_cdr_keys(sku)
        if not keys:
            fails.append(f"SKU {sku}: gagal resolve komponen bundle (format range tak terbaca).")
            continue
        for k in keys:
            ent = by_sku.setdefault(k, {"sku": k.upper(), "name": name, "pcs": 0, "src": sku})
            ent["pcs"] += pcs
    return by_sku


def _copy_group(
    by_sku: dict[str, dict],
    index: dict[str, str],
    dest_folder: str,
    emit: Callable[[str], None],
    warnings: list[str],
    fails: list[str],
) -> dict[str, Any]:
    """Salin 1 .cdr per key ke dest_folder + manifest. TIDAK menghapus apa pun."""
    os.makedirs(dest_folder, exist_ok=True)
    manifest_rows: list[dict] = []
    total_pcs = 0
    ok_designs = 0
    for idx, (key, ent) in enumerate(sorted(by_sku.items()), start=1):
        disp = ent["sku"]
        src = ent.get("src", disp)
        origin = "" if src.upper() == disp else f" [bundle {src}]"
        found = duplicate.find_cdr(index, key)
        if not found:
            fails.append(f"SKU {disp}{origin}: file '{disp}.cdr' TIDAK ADA di folder master.")
            continue
        try:
            dest = duplicate.copy_cdr(found, dest_folder)
            manifest_rows.append(
                {"sku": disp, "name": ent["name"], "pcs": ent["pcs"], "file": os.path.basename(dest)}
            )
            total_pcs += ent["pcs"]
            ok_designs += 1
            emit(f"  [{idx:03d}] {disp}{origin} → {ent['pcs']} pcs → {os.path.basename(dest)}")
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc().strip().replace("\n", "\n      ")
            fails.append(
                f"SKU {disp}{origin}: gagal salin '{os.path.basename(found)}' "
                f"— {type(e).__name__}: {e}\n      Trace: {tb}"
            )
    manifest_path = None
    if manifest_rows:
        ts = time.strftime("%Y-%m-%d-%H-%M-%S")
        manifest_path = duplicate.write_manifest(dest_folder, manifest_rows, ts)
        emit(
            f"Manifest: {os.path.basename(manifest_path)} ({ok_designs} desain) "
            f"di {os.path.basename(dest_folder)}/."
        )
    return {"ok_designs": ok_designs, "total_pcs": total_pcs, "manifest": manifest_path}


def _pull_grouped(
    jobs_by_batch: dict[str, list[dict]], cfg: dict, emit: Callable[[str], None]
) -> dict[str, Any]:
    """Salin per BATCH → sub-folder <hot>/<batch_code>/. Index master dibangun sekali.
    Tanpa auto-hapus."""
    master_folder = cfg["master_folder"]
    hot_folder = cfg["hot_folder"]
    if not os.path.isdir(master_folder):
        raise FileNotFoundError(f"FOLDER MASTER tidak ada: {master_folder}")
    os.makedirs(hot_folder, exist_ok=True)
    emit("Membuat index file master .cdr...")
    index = duplicate.build_file_index(master_folder)
    emit(f"Index siap: {len(index)} entri .cdr.")

    warnings: list[str] = []
    fails: list[str] = []
    total_ok = 0
    total_pcs = 0
    folders: list[str] = []
    for bc, jobs_norm in jobs_by_batch.items():
        folder = _safe_folder(bc)
        dest = os.path.join(hot_folder, folder)
        emit(f"— Batch '{bc}' → folder {folder}/ —")
        by_sku = _expand_jobs(jobs_norm, emit, warnings, fails)
        res = _copy_group(by_sku, index, dest, emit, warnings, fails)
        total_ok += res["ok_designs"]
        total_pcs += res["total_pcs"]
        folders.append(folder)
    return {
        "ok_designs": total_ok,
        "total_pcs": total_pcs,
        "batches": len(folders),
        "folders": folders,
        "warnings": warnings,
        "fails": fails,
        "hot_folder": hot_folder,
    }


def run_pull(cfg: dict, emit: Callable[[str], None] = print) -> dict[str, Any]:
    """FULL: tarik SEMUA job in_progress → salin per folder batch (tanpa auto-hapus)."""
    emit("Mengambil antrian print GK 'sedang diproses' (in_progress) dari ERP...")
    jobs = _client(cfg).fetch_active_print_jobs()
    emit(f"Dapat {len(jobs)} job in_progress.")
    if not jobs:
        return {
            "ok": True, "ok_designs": 0, "total_pcs": 0, "batches": 0, "folders": [],
            "warnings": [], "fails": [], "hot_folder": cfg["hot_folder"],
            "message": "Tidak ada job in_progress. Klaim/Mulai batch dulu di Operator Print GK.",
        }
    by_batch: dict[str, list[dict]] = {}
    for j in jobs:
        item = j.get("item") or {}
        batch = j.get("batch") or {}
        bc = (batch.get("batch_code") if isinstance(batch, dict) else None) or "tanpa-batch"
        by_batch.setdefault(bc, []).append(
            {"sku": item.get("sku"), "name": item.get("name"), "pcs": j.get("jumlah_pcs_target")}
        )
    res = _pull_grouped(by_batch, cfg, emit)
    msg = f"{res['ok_designs']} desain disalin ke {res['batches']} folder batch → {res['total_pcs']} pcs."
    if res["fails"]:
        msg += f" {len(res['fails'])} gagal (cek log)."
    return {"ok": True, "message": msg, **res}


def run_pull_claim(
    cfg: dict, target_charms: int, emit: Callable[[str], None] = print, worker: str | None = None
) -> dict[str, Any]:
    """KLAIM: ambil N charm BERIKUTNYA yang belum ditarik → salin per folder batch."""
    wk = worker or worker_name(cfg)
    target = max(1, int(target_charms))
    emit(f"[{wk}] Mengklaim {target} charm berikutnya dari antrian (belum ditarik)...")
    client = _client(cfg)
    claimed = client.claim_jobs(target, wk)
    if not claimed:
        prog = client.fetch_pull_progress()
        done = prog["total_charms"] > 0 and prog["pulled_charms"] >= prog["total_charms"]
        return {
            "ok": True, "claimed_jobs": 0, "ok_designs": 0, "total_pcs": 0, "batches": 0,
            "folders": [], "warnings": [], "fails": [], "hot_folder": cfg["hot_folder"],
            "progress": prog,
            "message": (
                "Semua charm sudah ditarik (100%). Tidak ada yang tersisa."
                if done
                else "Tidak ada charm untuk ditarik. Pastikan batch sudah diklaim (in_progress) di web."
            ),
        }
    claimed_charms = sum(int(c.get("charms") or 0) for c in claimed)
    by_batch: dict[str, list[dict]] = {}
    for c in claimed:
        bc = (c.get("batch_code") or "tanpa-batch")
        by_batch.setdefault(bc, []).append(
            {"sku": c.get("sku"), "name": c.get("item_name"), "pcs": c.get("jumlah_pcs_target")}
        )
    emit(
        f"[{wk}] Diklaim {len(claimed)} job (~{claimed_charms} charm) di {len(by_batch)} batch. "
        "Menyalin .cdr..."
    )
    res = _pull_grouped(by_batch, cfg, emit)
    prog = client.fetch_pull_progress()
    sisa = max(0, prog["total_charms"] - prog["pulled_charms"])
    msg = (
        f"Ditarik {len(claimed)} job (~{claimed_charms} charm) → {res['ok_designs']} desain di "
        f"{res['batches']} folder batch. Progres batch: {prog['pulled_charms']}/{prog['total_charms']} "
        f"(sisa {sisa})."
    )
    if res["fails"]:
        msg += f" {len(res['fails'])} gagal (cek log)."
    return {
        "ok": True, "claimed_jobs": len(claimed), "claimed_charms": claimed_charms,
        "progress": prog, "message": msg, **res,
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
