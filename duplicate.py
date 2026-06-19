"""Salin file desain .cdr (CorelDRAW) berdasarkan antrian print GK dari ERP.

Berbeda dengan stiker (PDF, ekstraksi halaman + N salinan), GK memakai file .cdr
vektor: SATU file per desain disalin ke hot folder, lalu jumlah produksi ditulis
ke MANIFEST (CSV). Operator buka tiap .cdr sekali di CorelDRAW lalu atur jumlah
sesuai manifest. Tidak ada dependency eksternal (stdlib saja).

Logika index + alias ATM↔ANM diadaptasi dari build_file_index() milik
sortir-ganci/sortir_desain.py.
"""

from __future__ import annotations

import csv
import os
import shutil
from typing import Iterable

CDR_EXT = ".cdr"


def build_file_index(master_folder: str) -> dict[str, str]:
    """Map nama-file-tanpa-ekstensi (lowercase) → path .cdr.

    Sekaligus daftarkan alias ATM↔ANM (folder master kadang pakai salah satu
    prefix). Walk rekursif sekali di awal untuk lookup O(1).
    """
    index: dict[str, str] = {}
    for root, _dirs, files in os.walk(master_folder):
        for fn in files:
            if not fn.lower().endswith(CDR_EXT):
                continue
            name, _ext = os.path.splitext(fn)
            key = name.lower()
            full = os.path.join(root, fn)
            index.setdefault(key, full)
            if "gk-atm-" in key:
                index.setdefault(key.replace("gk-atm-", "gk-anm-", 1), full)
            elif "gk-anm-" in key:
                index.setdefault(key.replace("gk-anm-", "gk-atm-", 1), full)
    return index


def find_cdr(index: dict[str, str], sku: str | None) -> str | None:
    """Cari path .cdr untuk SKU charm L (mis. 'GK-ATM-0003271-L' → <...>.cdr)."""
    if not sku:
        return None
    return index.get(sku.strip().lower())


def clear_hotfolder(hot_folder: str) -> int:
    """Hapus .cdr + manifest lama di hot folder (abaikan subfolder 'log').

    Returns jumlah file yang dihapus.
    """
    removed = 0
    for name in os.listdir(hot_folder):
        full = os.path.join(hot_folder, name)
        if os.path.isdir(full):
            continue
        low = name.lower()
        if low.endswith(CDR_EXT) or low.startswith("manifest"):
            try:
                os.remove(full)
                removed += 1
            except OSError:
                pass
    return removed


def copy_cdr(src_path: str, hot_folder: str) -> str:
    """Salin SATU file .cdr ke hot folder (overwrite kalau sudah ada). Return path."""
    dest = os.path.join(hot_folder, os.path.basename(src_path))
    shutil.copy2(src_path, dest)
    return dest


def write_manifest(hot_folder: str, rows: Iterable[dict], ts: str) -> str:
    """Tulis manifest CSV (SKU, Nama, Jumlah Pcs, File) ke hot folder. Return path.

    CSV stdlib (buka langsung di Excel) — tanpa dependency openpyxl/pandas.
    """
    path = os.path.join(hot_folder, f"manifest_{ts}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SKU", "Nama", "Jumlah Pcs", "File CDR"])
        for r in rows:
            w.writerow([r.get("sku", ""), r.get("name", ""), r.get("pcs", 0), r.get("file", "")])
    return path
