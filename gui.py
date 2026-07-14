"""GUI utama ganci-print-puller (CustomTkinter).

Dua tab:
  - Pengaturan: isi URL/JWT/folder + Test Koneksi + Simpan (config.json).
  - Eksekusi & Log: "Tarik Desain & Salin" → gandakan .cdr sebanyak pcs + manifest.

Jalankan: python gui.py  (atau gui.bat)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from tkinter import filedialog, messagebox

import customtkinter as ctk

import updater
import version
from erp_client import ErpClient
from main import run_pull_claim, worker_name
from settings import CONFIG_PATH, load_existing

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

REQUIRED = ("vps_db_url", "jwt_secret", "master_folder", "hot_folder")


class PullerApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Ganci Print Puller — ERP  v{version.__version__}")
        # Lebar cukup supaya semua tombol baris progres (termasuk Reset) langsung
        # kelihatan tanpa perlu melebarkan jendela manual.
        self.geometry("1000x640")
        self.minsize(940, 560)
        self.cfg = load_existing()

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=12)
        self._build_settings(self.tabs.add("Pengaturan"))
        self._build_run(self.tabs.add("Eksekusi & Log"))
        self.tabs.set("Eksekusi & Log" if all(self.cfg.get(k) for k in REQUIRED) else "Pengaturan")

        # Notifikasi one-shot bila baru saja auto-update saat launch (via updater.py).
        self.after(400, self._notify_if_updated)

    # ---------------------------------------------------------------- settings
    def _row(self, parent, label: str):
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", padx=12, pady=5)
        ctk.CTkLabel(frame, text=label, width=160, anchor="w").pack(side="left", padx=(10, 6))
        return frame

    def _build_settings(self, p) -> None:
        ctk.CTkLabel(
            p, text="Pengaturan Koneksi & Folder", font=("Segoe UI", 16, "bold")
        ).pack(pady=(12, 2))

        f = self._row(p, "URL Database")
        self.e_url = ctk.CTkEntry(f)
        self.e_url.pack(side="left", fill="x", expand=True, padx=10)
        self.e_url.insert(0, self.cfg.get("vps_db_url", ""))

        f = self._row(p, "JWT Secret")
        self.e_secret = ctk.CTkEntry(f, show="•")
        self.e_secret.pack(side="left", fill="x", expand=True, padx=10)
        self.e_secret.insert(0, self.cfg.get("jwt_secret", ""))
        self.show_secret = ctk.CTkCheckBox(f, text="Lihat", width=58, command=self._toggle_secret)
        self.show_secret.pack(side="left", padx=6)

        f = self._row(p, "Role")
        self.e_role = ctk.CTkOptionMenu(f, values=["service_role", "authenticated", "anon"])
        self.e_role.set(self.cfg.get("jwt_role", "service_role"))
        self.e_role.pack(side="left", padx=10)

        f = self._row(p, "Folder Master .cdr")
        self.e_master = ctk.CTkEntry(f)
        self.e_master.pack(side="left", fill="x", expand=True, padx=10)
        self.e_master.insert(0, self.cfg.get("master_folder", ""))
        ctk.CTkButton(f, text="Pilih", width=68, command=lambda: self._browse(self.e_master)).pack(
            side="left", padx=6
        )

        f = self._row(p, "Folder Hasil (Hot)")
        self.e_hot = ctk.CTkEntry(f)
        self.e_hot.pack(side="left", fill="x", expand=True, padx=10)
        self.e_hot.insert(0, self.cfg.get("hot_folder", ""))
        ctk.CTkButton(f, text="Pilih", width=68, command=lambda: self._browse(self.e_hot)).pack(
            side="left", padx=6
        )

        f = self._row(p, "Port Bridge")
        self.e_port = ctk.CTkEntry(f, width=100)
        self.e_port.pack(side="left", padx=10)
        self.e_port.insert(0, str(self.cfg.get("bridge_port", 8767)))

        f = self._row(p, "Nama Komputer")
        self.e_worker = ctk.CTkEntry(f)
        self.e_worker.pack(side="left", fill="x", expand=True, padx=10)
        self.e_worker.insert(0, self.cfg.get("worker_name", ""))
        ctk.CTkLabel(f, text="(penanda 'ditarik oleh'; kosong = otomatis)", anchor="w").pack(
            side="left", padx=6
        )

        btns = ctk.CTkFrame(p)
        btns.pack(fill="x", padx=12, pady=12)
        ctk.CTkButton(btns, text="Test Koneksi", command=self._test).pack(side="left", padx=6)
        ctk.CTkButton(
            btns, text="Simpan", fg_color="#16a34a", hover_color="#15803d", command=self._save
        ).pack(side="right", padx=6)
        self.lbl_status = ctk.CTkLabel(p, text="", anchor="w")
        self.lbl_status.pack(fill="x", padx=16)

    def _toggle_secret(self) -> None:
        self.e_secret.configure(show="" if self.show_secret.get() else "•")

    def _browse(self, entry) -> None:
        d = filedialog.askdirectory()
        if d:
            entry.delete(0, "end")
            entry.insert(0, os.path.normpath(d))

    def _collect(self) -> dict:
        def _int(s, default):
            try:
                return int(str(s).strip())
            except ValueError:
                return default

        return {
            "vps_db_url": self.e_url.get().strip(),
            "jwt_secret": self.e_secret.get().strip(),
            "jwt_role": self.e_role.get().strip() or "service_role",
            "master_folder": self.e_master.get().strip(),
            "hot_folder": self.e_hot.get().strip(),
            "bridge_port": _int(self.e_port.get(), 8767),
            "worker_name": self.e_worker.get().strip(),
        }

    def _save(self) -> None:
        cfg = self._collect()
        miss = [k for k in REQUIRED if not cfg[k]]
        if miss:
            self.lbl_status.configure(
                text="Wajib diisi: " + ", ".join(miss), text_color="#dc2626"
            )
            return
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        self.cfg = cfg
        self.lbl_status.configure(text="✓ Tersimpan ke config.json", text_color="#16a34a")

    def _test(self) -> None:
        cfg = self._collect()
        if not cfg["vps_db_url"] or not cfg["jwt_secret"]:
            self.lbl_status.configure(text="URL & JWT wajib diisi untuk tes", text_color="#dc2626")
            return
        self.lbl_status.configure(text="Menguji koneksi…", text_color="#2563eb")

        def work() -> None:
            try:
                client = ErpClient(cfg["vps_db_url"], cfg["jwt_secret"], cfg["jwt_role"])
                n = len(client.fetch_active_print_jobs())
                self.after(
                    0,
                    lambda: self.lbl_status.configure(
                        text=f"✓ Terhubung. {n} job sedang diproses (in_progress).",
                        text_color="#16a34a",
                    ),
                )
            except Exception as e:  # noqa: BLE001
                err = str(e)
                self.after(
                    0, lambda: self.lbl_status.configure(text=f"✗ {err}", text_color="#dc2626")
                )

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------- run
    def _build_run(self, p) -> None:
        # Baris aksi tarik.
        top = ctk.CTkFrame(p)
        top.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(top, text="Tarik", anchor="w").pack(side="left", padx=(10, 4))
        self.e_charms = ctk.CTkEntry(top, width=70)
        self.e_charms.pack(side="left")
        self.e_charms.insert(0, "50")
        ctk.CTkLabel(top, text="charm", anchor="w").pack(side="left", padx=(4, 10))
        self.btn_run = ctk.CTkButton(
            top,
            text="⬇  Tarik Sebagian",
            height=44,
            font=("Segoe UI", 14, "bold"),
            command=self._run_partial,
        )
        self.btn_run.pack(side="left", padx=6)
        self.btn_run_all = ctk.CTkButton(
            top,
            text="⬇⬇  Tarik Semua Sisa",
            height=44,
            fg_color="#0e7490",
            hover_color="#155e75",
            command=self._run_all_remaining,
        )
        self.btn_run_all.pack(side="left", padx=6)
        self.btn_update = ctk.CTkButton(
            top,
            text="🔄  Perbarui",
            height=44,
            width=100,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._check_update,
        )
        self.btn_update.pack(side="right", padx=6)

        # Baris progres batch (lintas-komputer).
        prog = ctk.CTkFrame(p)
        prog.pack(fill="x", padx=12, pady=(0, 6))
        self.lbl_progress = ctk.CTkLabel(
            prog, text="Progres batch: —", font=("Segoe UI", 13, "bold"), anchor="w"
        )
        self.lbl_progress.pack(side="left", padx=(10, 8))
        self.bar_progress = ctk.CTkProgressBar(prog, width=220)
        self.bar_progress.set(0)
        self.bar_progress.pack(side="left", padx=8)
        ctk.CTkButton(prog, text="↻ Refresh", width=84, command=self._refresh_progress).pack(
            side="left", padx=6
        )
        ctk.CTkButton(
            prog,
            text="Buka Folder",
            width=100,
            fg_color="#6b7280",
            hover_color="#4b5563",
            command=self._open_output,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            prog,
            text="↺ Reset Tarik Ulang",
            width=150,
            fg_color="#b91c1c",
            hover_color="#991b1b",
            command=self._reset_pull,
        ).pack(side="right", padx=6)

        self.lbl_summary = ctk.CTkLabel(
            p,
            text="Isi jumlah charm lalu 'Tarik Sebagian' — banyak komputer bisa bagi beban tanpa dobel.",
            font=("Segoe UI", 13),
        )
        self.lbl_summary.pack(fill="x", padx=16, pady=(0, 6))

        self.log = ctk.CTkTextbox(p, font=("Consolas", 12))
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Muat progres awal (sekali) kalau setting lengkap.
        if all(self.cfg.get(k) for k in REQUIRED):
            self.after(600, self._refresh_progress)

    def _emit(self, msg: str) -> None:
        self.after(0, lambda: (self.log.insert("end", msg + "\n"), self.log.see("end")))

    def _open_output(self) -> None:
        hot = self._collect()["hot_folder"]
        if hot and os.path.isdir(hot):
            os.startfile(hot)  # noqa: SLF001 — Windows-only, sesuai target operator
        else:
            self._emit("Folder hasil belum ada / belum di-set (cek tab Pengaturan).")

    # ---------------------------------------------------------------- update
    def _notify_if_updated(self) -> None:
        """Tampilkan notifikasi sekali kalau updater.py baru saja auto-update."""
        info = updater.consume_update_flag()
        if info:
            self.lbl_summary.configure(
                text=f"✓ Aplikasi diperbarui {info['old_version']} → {info['new_version']}.",
                text_color="#16a34a",
            )

    def _restart(self) -> None:
        """Restart proses Python dengan argumen yang sama (muat kode terbaru)."""
        os.execl(sys.executable, sys.executable, *sys.argv)

    def _check_update(self) -> None:
        """Cek & apply update manual (tombol Perbarui) di thread background."""
        self.btn_update.configure(state="disabled", text="Mengecek…")
        self.lbl_summary.configure(text="Memeriksa update dari GitHub…", text_color="#2563eb")

        def work() -> None:
            res = updater.check_and_update()
            self.after(0, lambda: self._after_update(res))

        threading.Thread(target=work, daemon=True).start()

    def _after_update(self, res: "updater.UpdateResult") -> None:
        self.btn_update.configure(state="normal", text="🔄  Perbarui")
        if res.status == "updated":
            updater.write_update_flag(res)
            self.lbl_summary.configure(
                text=f"✓ Update {res.old_version} → {res.new_version}. Restart aplikasi…",
                text_color="#16a34a",
            )
            if messagebox.askyesno(
                "Update Berhasil",
                f"Aplikasi diperbarui {res.old_version} → {res.new_version}.\n"
                "Restart sekarang untuk memuat versi baru?",
            ):
                self._restart()
        elif res.status == "current":
            self.lbl_summary.configure(
                text=f"✓ Sudah versi terbaru ({res.old_version}).", text_color="#16a34a"
            )
        elif res.status == "no-internet":
            self.lbl_summary.configure(
                text="✗ Tidak bisa konek GitHub. Cek koneksi internet.", text_color="#dc2626"
            )
        elif res.status == "no-git":
            self.lbl_summary.configure(
                text="✗ Bukan git repo — update otomatis tak tersedia (salin manual).",
                text_color="#dc2626",
            )
        else:
            self.lbl_summary.configure(text=f"✗ Gagal update: {res.message}", text_color="#dc2626")

    def _set_progress(self, prog: dict) -> None:
        total = int(prog.get("total_charms") or 0)
        pulled = int(prog.get("pulled_charms") or 0)
        sisa = max(0, total - pulled)
        frac = (pulled / total) if total > 0 else 0
        pct = round(frac * 100)
        self.bar_progress.set(frac)
        if total == 0:
            txt = "Progres batch: belum ada batch in_progress"
            color = "#6b7280"
        elif sisa == 0:
            txt = f"Progres batch: {pulled}/{total} charm — SELESAI 100% ✓"
            color = "#16a34a"
        else:
            txt = f"Progres batch: {pulled}/{total} charm ({pct}%) · sisa {sisa}"
            color = "#2563eb"
        self.lbl_progress.configure(text=txt, text_color=color)

    def _refresh_progress(self) -> None:
        cfg = self._collect()
        if not cfg.get("vps_db_url") or not cfg.get("jwt_secret"):
            return

        def work() -> None:
            try:
                prog = ErpClient(cfg["vps_db_url"], cfg["jwt_secret"], cfg["jwt_role"]).fetch_pull_progress()
                self.after(0, lambda: self._set_progress(prog))
            except Exception as e:  # noqa: BLE001
                err = str(e)
                self.after(0, lambda: self.lbl_progress.configure(text=f"Progres: ✗ {err}", text_color="#dc2626"))

        threading.Thread(target=work, daemon=True).start()

    def _reset_pull(self) -> None:
        """Reset status-tarik: semua charm batch in_progress bisa ditarik ULANG dari
        awal. Untuk pemulihan saat ada kesalahan pull. Ambil dulu jumlah charm-nya
        supaya konfirmasi jelas (mis. 'reset 100 charm')."""
        cfg = self._collect()
        if not cfg.get("vps_db_url") or not cfg.get("jwt_secret"):
            self._emit("Setting belum lengkap — buka tab Pengaturan.")
            return

        def work() -> None:
            try:
                prog = ErpClient(
                    cfg["vps_db_url"], cfg["jwt_secret"], cfg["jwt_role"]
                ).fetch_pull_progress()
            except Exception as e:  # noqa: BLE001
                err = str(e)
                self._emit(f"GAGAL cek progres untuk reset: {err}")
                return
            total = prog["total_charms"]
            pulled = prog["pulled_charms"]

            def confirm_and_reset() -> None:
                if total == 0:
                    messagebox.showinfo(
                        "Reset Tarik Ulang",
                        "Belum ada batch in_progress — tidak ada yang perlu di-reset.",
                    )
                    return
                if messagebox.askyesno(
                    "Reset Tarik Ulang",
                    f"Batch sekarang: {total} charm, {pulled} sudah ditarik.\n\n"
                    f"Reset agar SEMUA {total} charm bisa DITARIK ULANG dari awal?\n\n"
                    "Pakai kalau ada kesalahan pull. Status job di web (pending/"
                    "in_progress/done) TIDAK berubah.",
                ):
                    self._do_reset(cfg, total)

            self.after(0, confirm_and_reset)

        threading.Thread(target=work, daemon=True).start()

    def _do_reset(self, cfg: dict, total: int) -> None:
        def work() -> None:
            try:
                ErpClient(cfg["vps_db_url"], cfg["jwt_secret"], cfg["jwt_role"]).reset_pull()
                self._emit(f"✓ Reset selesai — {total} charm kini bisa ditarik ulang dari awal.")
                self.after(0, self._refresh_progress)
            except Exception as e:  # noqa: BLE001
                err = str(e)
                self._emit(f"GAGAL reset: {err}")

        threading.Thread(target=work, daemon=True).start()

    def _run_partial(self) -> None:
        try:
            target = int(self.e_charms.get().strip())
        except ValueError:
            target = 0
        if target <= 0:
            self._emit("Isi jumlah charm (angka > 0) dulu.")
            return
        self._do_pull(target)

    def _run_all_remaining(self) -> None:
        # Target sangat besar → klaim semua sisa yang belum ditarik.
        self._do_pull(10_000_000)

    def _do_pull(self, target: int) -> None:
        cfg = self._collect()
        miss = [k for k in REQUIRED if not cfg[k]]
        if miss:
            self._emit("Setting belum lengkap: " + ", ".join(miss) + " — buka tab Pengaturan.")
            self.tabs.set("Pengaturan")
            return
        self.btn_run.configure(state="disabled")
        self.btn_run_all.configure(state="disabled")
        self.log.delete("1.0", "end")
        self.lbl_summary.configure(text="Mengklaim & menyalin…", text_color="#2563eb")

        def work() -> None:
            try:
                summary = run_pull_claim(cfg, target, emit=self._emit, worker=worker_name(cfg))
                prog = summary.get("progress")
                self.after(
                    0,
                    lambda: (
                        self.lbl_summary.configure(
                            text=summary["message"],
                            text_color="#16a34a" if not summary.get("fails") else "#d97706",
                        ),
                        self._set_progress(prog) if prog else None,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                err = str(e)
                tb = traceback.format_exc()
                self._emit(f"GAGAL: {err}")
                self._emit(tb.rstrip())
                self.after(
                    0,
                    lambda: self.lbl_summary.configure(text=f"GAGAL: {err}", text_color="#dc2626"),
                )
            finally:
                self.after(
                    0,
                    lambda: (
                        self.btn_run.configure(state="normal"),
                        self.btn_run_all.configure(state="normal"),
                    ),
                )

        threading.Thread(target=work, daemon=True).start()


def main() -> None:
    PullerApp().mainloop()


if __name__ == "__main__":
    main()
