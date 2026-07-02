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
import re
import shutil
from typing import Iterable

CDR_EXT = ".cdr"

# Aturan bundle GK (diadaptasi dari sortir-ganci/core/sku_utils.py):
#   L  = 1 charm (tunggal)
#   S  = 5 charm, M = 5 charm, BS = 10 charm (bundle)
# Bundle di-EXPAND ke tiap desain anggota lalu dicari file <desain>-<ukuran>.cdr.
ATURAN_BUNDLE = {"S": 5, "M": 5, "BS": 10}
UKURAN_VALID = ("L", "S", "M", "BS")
UKURAN_BUNDLE_CAPABLE = ("S", "M", "BS")


def parse_dynamic_sku(sku_bundle_dasar: str) -> list[str]:
    """Baca range bundle → daftar SKU dasar (tanpa suffix ukuran), pad 7 digit.

    Mendukung `GK-ATM-SET-81-90`, `GK-ATM-81-90`, dan `ATM-81-90`.
    `GK-ATM-SET-5521-5525` → ['GK-ATM-0005521', ..., 'GK-ATM-0005525'].
    Return [] kalau pola range tidak cocok.
    """
    m = re.search(r"(?:GK-)?(.*?)-(\d+)-(\d+)$", sku_bundle_dasar, re.IGNORECASE)
    if not m:
        return []
    kategori = re.sub(r"-?SET-?", "", m.group(1), flags=re.IGNORECASE).strip("-")
    start, end = int(m.group(2)), int(m.group(3))
    if end < start:
        return []
    return [f"GK-{kategori}-{str(i).zfill(7)}" for i in range(start, end + 1)]


def extract_ukuran(sku: str) -> str:
    """Suffix ukuran (uppercase) dari SKU; '' bila bukan L/S/M/BS."""
    u = str(sku).split("-")[-1].upper()
    return u if u in UKURAN_VALID else ""


def resolve_cdr_keys(sku: str) -> list[str]:
    """SKU pesanan (bundle/tunggal) → daftar KEY file '<base>-<ukuran>' (lowercase).

    - L (atau bukan S/M/BS): item tunggal walau namanya mengandung 'SET'.
    - S/M/BS: bundle → expand range (SET atau range polos), potong sesuai
      ATURAN_BUNDLE (S/M=5, BS=10). Kalau bukan range → item tunggal S/M/BS.
    Meniru resolve_bundle() sortir-ganci (jalur tanpa database).
    """
    s = str(sku).strip()
    if not s:
        return []
    ukuran = extract_ukuran(s)
    if not ukuran:
        return [s.lower()]  # format tak dikenal → coba apa adanya
    sku_bundle_dasar = s.rsplit("-", 1)[0]

    bases: list[str]
    is_bundle = False
    if "-SET-" in s.upper() and ukuran in UKURAN_BUNDLE_CAPABLE:
        is_bundle = True
        bases = parse_dynamic_sku(sku_bundle_dasar)
    elif ukuran in UKURAN_BUNDLE_CAPABLE:
        dyn = parse_dynamic_sku(sku_bundle_dasar)
        if dyn:
            is_bundle = True
            bases = dyn
        else:
            bases = [sku_bundle_dasar]  # S/M/BS tunggal (bukan range)
    else:
        bases = [sku_bundle_dasar]  # L tunggal

    if is_bundle and ukuran in ATURAN_BUNDLE:
        bases = bases[: ATURAN_BUNDLE[ukuran]]
    if not bases:
        return []
    return [f"{b}-{ukuran}".lower() for b in bases]


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
