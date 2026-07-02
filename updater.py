"""Auto-update ganci-print-puller via ``git pull --rebase --autostash``.

Alur (identik dengan sortir-ganci/core/updater.py, disesuaikan struktur flat):

1. ``gui.bat`` / ``start.bat`` memanggil ``python updater.py`` SEBELUM start app.
2. Updater ``git fetch origin main`` lalu bandingkan SHA HEAD lokal vs origin/main.
3. Jika beda → ``git pull --rebase --autostash`` lalu tulis flag ``.last_update.json``.
4. ``gui.py`` saat startup memanggil :func:`consume_update_flag` untuk tampilkan
   notifikasi "Berhasil update vX → vY" (one-shot), lalu hapus flag.

Tombol "Perbarui" di GUI juga memanggil :func:`check_and_update` langsung (manual).

Update bersifat silent auto-apply: notifikasi muncul SETELAH sukses, bukan sebelum.

Edge cases:
- Tidak ada internet → ``git fetch`` gagal → status ``no-internet``, app tetap lanjut.
- Bukan git repo (download zip) → status ``no-git``, app tetap lanjut.
- Konflik rebase → ``git rebase --abort`` defensif agar working tree tidak terjebak.
- config.json TIDAK terganggu: gitignored, --autostash tak menyentuhnya.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


def _repo_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


UPDATE_FLAG_FILE = os.path.join(_repo_root(), ".last_update.json")
_VERSION_FILE = os.path.join(_repo_root(), "version.py")


def _read_version() -> str:
    """Baca ``__version__`` langsung dari version.py (fresh dari disk).

    Tidak ``import version`` supaya nilai SESUDAH ``git pull`` terbaca akurat
    (modul yang sudah di-import tidak refleksikan file baru tanpa reload).
    """
    try:
        with open(_VERSION_FILE, "r", encoding="utf-8") as f:
            m = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", f.read())
            return m.group(1) if m else "?"
    except OSError:
        return "?"


def _git(*args) -> Tuple[int, str, str]:
    """Jalankan ``git <args>`` di repo root. Return ``(rc, stdout, stderr)`` stripped."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except OSError as e:
        return 1, "", str(e)


@dataclass
class UpdateResult:
    """``status`` ∈ {updated, current, no-internet, no-git, error}.

    ``old_version`` & ``new_version`` selalu terisi (sama bila tak ada perubahan).
    """

    status: str
    old_version: str
    new_version: str
    message: str = ""


def check_and_update() -> UpdateResult:
    """Cek update di GitHub & apply jika ada. TIDAK pernah raise (report via status)."""
    old_version = _read_version()

    rc, _, _ = _git("rev-parse", "--git-dir")
    if rc != 0:
        return UpdateResult("no-git", old_version, old_version, "Bukan git repo / git tak terinstall")

    rc, _, err = _git("fetch", "origin", "main")
    if rc != 0:
        return UpdateResult("no-internet", old_version, old_version, err or "git fetch gagal")

    rc1, local_head, _ = _git("rev-parse", "HEAD")
    rc2, remote_head, _ = _git("rev-parse", "origin/main")
    if rc1 != 0 or rc2 != 0:
        return UpdateResult("error", old_version, old_version, "Gagal baca SHA HEAD lokal/remote")

    if local_head == remote_head:
        return UpdateResult("current", old_version, old_version, "Sudah versi terbaru")

    rc, out, err = _git("pull", "--rebase", "--autostash", "origin", "main")
    if rc != 0:
        _git("rebase", "--abort")  # defensif — aman meski tak ada rebase in-progress
        return UpdateResult("error", old_version, old_version, err or out or "git pull gagal")

    return UpdateResult("updated", old_version, _read_version(), out)


def write_update_flag(result: UpdateResult) -> None:
    """Tulis flag file untuk dikonsumsi GUI sebagai notifikasi 'Update Berhasil'."""
    payload = {
        "old_version": result.old_version,
        "new_version": result.new_version,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        with open(UPDATE_FLAG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def consume_update_flag() -> Optional[dict]:
    """Baca & hapus flag update (one-shot). Return dict atau None."""
    if not os.path.exists(UPDATE_FLAG_FILE):
        return None
    try:
        with open(UPDATE_FLAG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        try:
            os.remove(UPDATE_FLAG_FILE)
        except OSError:
            pass
    return data


def main() -> int:
    """CLI entry (dipanggil run.bat sebelum GUI). Selalu exit 0 supaya app lanjut."""
    print("=" * 55)
    print("  Memeriksa update dari GitHub...")
    print("=" * 55)

    result = check_and_update()

    if result.status == "updated":
        print(f"  [OK] Update berhasil: {result.old_version} -> {result.new_version}")
        write_update_flag(result)
    elif result.status == "current":
        print(f"  [OK] Sudah versi terbaru ({result.old_version})")
    elif result.status == "no-internet":
        print(f"  [SKIP] Tidak bisa konek GitHub. Lanjut versi lokal ({result.old_version}).")
    elif result.status == "no-git":
        print(f"  [SKIP] Git tak tersedia. Lanjut versi lokal ({result.old_version}).")
    else:
        print(f"  [ERROR] {result.message}. Lanjut versi lokal ({result.old_version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
