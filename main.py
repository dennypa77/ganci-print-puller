"""Tarik antrian print GK dari ERP lalu salin file .cdr secara lokal.

Dua mode:
  * FULL   — `run_pull`: ambil SEMUA job in_progress, bersihkan hot folder, salin
             semua .cdr. Cocok untuk 1 komputer (perilaku lama). Tidak menandai
             status-tarik di DB.
  * KLAIM  — `run_pull_claim`: klaim N charm BERIKUTNYA yang belum ditarik (atomik
             di DB) → banyak komputer bisa bagi beban tanpa tumpang tindih. Menulis
             status-tarik (pulled_at) supaya progres lintas-komputer akurat.
             TIDAK membersihkan hot folder (menumpuk, karena ditarik bertahap).

Keduanya TIDAK mengubah status JOB (pending/in_progress/done) — itu tetap dipegang
operator via web. Mode KLAIM hanya menandai kolom pulled_at (status-tarik).
"""

from __future__ import annotations

import json
import os
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


def _expand_jobs(
    jobs_norm: list[dict], emit: Callable[[str], None], warnings: list[str], fails: list[str]
) -> dict[str, dict]:
    """jobs_norm = [{sku, name, pcs}] → by_sku (expand bundle→anggota, konsolidasi).

    Bundle (S/M/BS) → N desain anggota di ukuran itu (mis. SET-5521-5525-M →
    GK-ATM-0005521-M .. -0005525-M). Tunggal (L) → 1 key. pcs berlaku per anggota.
    """
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


def _copy_by_sku(
    by_sku: dict[str, dict],
    cfg: dict,
    emit: Callable[[str], None],
    warnings: list[str],
    fails: list[str],
    clear_first: bool,
) -> dict[str, Any]:
    """Salin 1 .cdr per key ke hot folder + tulis manifest. Return ringkasan salin."""
    master_folder = cfg["master_folder"]
    hot_folder = cfg["hot_folder"]
    log_dir = os.path.join(hot_folder, "log")
    os.makedirs(hot_folder, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    if not os.path.isdir(master_folder):
        raise FileNotFoundError(f"FOLDER MASTER tidak ada: {master_folder}")

    emit("Membuat index file master .cdr...")
    index = duplicate.build_file_index(master_folder)
    emit(f"Index siap: {len(index)} entri .cdr di folder master.")

    if clear_first:
        removed = duplicate.clear_hotfolder(hot_folder)
        emit(f"Auto-bersih: {removed} file lama dihapus — folder kini hanya batch terbaru.")

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
            dest = duplicate.copy_cdr(found, hot_folder)
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

    ts = time.strftime("%Y-%m-%d-%H-%M-%S")
    manifest_path = None
    if manifest_rows:
        manifest_path = duplicate.write_manifest(hot_folder, manifest_rows, ts)
        emit(f"Manifest ditulis: {os.path.basename(manifest_path)} ({ok_designs} desain).")

    log_path = os.path.join(log_dir, f"run_{ts}.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(f"Run {ts}\n")
        fh.write(f"Desain unik disalin: {ok_designs} | total pcs: {total_pcs}\n\n")
        if warnings:
            fh.write("PERINGATAN:\n" + "\n".join(warnings) + "\n\n")
        if fails:
            fh.write("GAGAL:\n" + "\n".join(fails) + "\n")

    return {
        "ok_designs": ok_designs,
        "total_pcs": total_pcs,
        "manifest": manifest_path,
        "hot_folder": hot_folder,
    }


def run_pull(cfg: dict, emit: Callable[[str], None] = print) -> dict[str, Any]:
    """FULL (lama): tarik SEMUA job in_progress, bersihkan hot folder, salin semua.
    Tidak menandai status-tarik. Cocok untuk 1 komputer."""
    emit("Mengambil antrian print GK 'sedang diproses' (in_progress) dari ERP...")
    jobs = _client(cfg).fetch_active_print_jobs()
    emit(f"Dapat {len(jobs)} job sedang diproses (in_progress).")
    if not jobs:
        return {
            "ok": True, "total_jobs": 0, "ok_designs": 0, "total_pcs": 0,
            "warnings": [], "fails": [], "hot_folder": cfg["hot_folder"], "manifest": None,
            "message": "Tidak ada job in_progress. Klaim/Mulai batch dulu di Operator Print GK.",
        }
    jobs_norm = [
        {"sku": (j.get("item") or {}).get("sku"), "name": (j.get("item") or {}).get("name"),
         "pcs": j.get("jumlah_pcs_target")}
        for j in jobs
    ]
    warnings: list[str] = []
    fails: list[str] = []
    by_sku = _expand_jobs(jobs_norm, emit, warnings, fails)
    res = _copy_by_sku(by_sku, cfg, emit, warnings, fails, clear_first=True)
    msg = f"{res['ok_designs']} desain (.cdr) disalin → {res['total_pcs']} pcs total."
    if fails:
        msg += f" {len(fails)} gagal (cek log)."
    return {"ok": True, "total_jobs": len(jobs), "warnings": warnings, "fails": fails, "message": msg, **res}


def run_pull_claim(
    cfg: dict, target_charms: int, emit: Callable[[str], None] = print, worker: str | None = None
) -> dict[str, Any]:
    """KLAIM: ambil N charm BERIKUTNYA yang belum ditarik komputer mana pun (atomik),
    lalu salin .cdr-nya (TANPA bersihkan hot folder — menumpuk). Balas ringkasan +
    progres terbaru."""
    wk = worker or worker_name(cfg)
    target = max(1, int(target_charms))
    emit(f"[{wk}] Mengklaim {target} charm berikutnya dari antrian (belum ditarik)...")
    client = _client(cfg)
    claimed = client.claim_jobs(target, wk)
    if not claimed:
        prog = client.fetch_pull_progress()
        done = prog["total_charms"] > 0 and prog["pulled_charms"] >= prog["total_charms"]
        return {
            "ok": True, "claimed_jobs": 0, "ok_designs": 0, "total_pcs": 0,
            "warnings": [], "fails": [], "hot_folder": cfg["hot_folder"], "manifest": None,
            "progress": prog,
            "message": (
                "Semua charm sudah ditarik (100%). Tidak ada yang tersisa."
                if done
                else "Tidak ada charm untuk ditarik. Pastikan batch sudah diklaim (in_progress) di web."
            ),
        }
    claimed_charms = sum(int(c.get("charms") or 0) for c in claimed)
    emit(f"[{wk}] Diklaim {len(claimed)} job (~{claimed_charms} charm). Menyalin .cdr...")
    jobs_norm = [
        {"sku": c.get("sku"), "name": c.get("item_name"), "pcs": c.get("jumlah_pcs_target")}
        for c in claimed
    ]
    warnings: list[str] = []
    fails: list[str] = []
    by_sku = _expand_jobs(jobs_norm, emit, warnings, fails)
    res = _copy_by_sku(by_sku, cfg, emit, warnings, fails, clear_first=False)
    prog = client.fetch_pull_progress()
    msg = (
        f"Ditarik {len(claimed)} job (~{claimed_charms} charm) → {res['ok_designs']} desain disalin. "
        f"Progres batch: {prog['pulled_charms']}/{prog['total_charms']} charm ditarik "
        f"(sisa {max(0, prog['total_charms'] - prog['pulled_charms'])})."
    )
    if fails:
        msg += f" {len(fails)} gagal (cek log)."
    return {
        "ok": True, "claimed_jobs": len(claimed), "claimed_charms": claimed_charms,
        "warnings": warnings, "fails": fails, "progress": prog, "message": msg, **res,
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
