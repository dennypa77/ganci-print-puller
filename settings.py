"""UI sederhana (Tkinter, stdlib) untuk mengisi config.json.

Operator tidak perlu edit JSON manual: isi form, klik "Test Koneksi" untuk
memastikan jwt_secret benar, lalu "Simpan".

Jalankan: python settings.py   (atau settings.bat)
"""

from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from version import __version__

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS = {
    "vps_db_url": "https://db.erp-hog.com",
    "jwt_secret": "",
    "jwt_role": "service_role",
    "master_folder": "",
    "hot_folder": "",
    "bridge_port": 8767,
}


def load_existing() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


class SettingsApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"Setting — ganci-print-puller v{__version__}")
        root.resizable(False, False)
        cfg = load_existing()

        self.var_url = tk.StringVar(value=cfg["vps_db_url"])
        self.var_secret = tk.StringVar(value=cfg["jwt_secret"])
        self.var_role = tk.StringVar(value=cfg["jwt_role"])
        self.var_master = tk.StringVar(value=cfg["master_folder"])
        self.var_hot = tk.StringVar(value=cfg["hot_folder"])
        self.var_port = tk.StringVar(value=str(cfg["bridge_port"]))
        self.var_status = tk.StringVar(value="")

        pad = {"padx": 8, "pady": 5}
        frm = ttk.Frame(root, padding=14)
        frm.grid(row=0, column=0, sticky="nsew")

        r = 0
        ttk.Label(frm, text="URL Database (PostgREST)").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.var_url, width=46).grid(
            row=r, column=1, columnspan=2, sticky="we", **pad
        )

        r += 1
        ttk.Label(frm, text="JWT Secret").grid(row=r, column=0, sticky="w", **pad)
        self.ent_secret = ttk.Entry(frm, textvariable=self.var_secret, width=46, show="•")
        self.ent_secret.grid(row=r, column=1, sticky="we", **pad)
        self.show_secret = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm, text="Tampilkan", variable=self.show_secret, command=self._toggle_secret
        ).grid(row=r, column=2, sticky="w", **pad)

        r += 1
        ttk.Label(frm, text="(= VPS_DB_JWT_SECRET dari erp .env / VPS)", foreground="#888").grid(
            row=r, column=1, columnspan=2, sticky="w", padx=8
        )

        r += 1
        ttk.Label(frm, text="Role").grid(row=r, column=0, sticky="w", **pad)
        ttk.Combobox(
            frm,
            textvariable=self.var_role,
            values=["service_role", "authenticated", "anon"],
            width=20,
            state="readonly",
        ).grid(row=r, column=1, sticky="w", **pad)

        r += 1
        ttk.Label(frm, text="Folder Master .cdr").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.var_master, width=46).grid(row=r, column=1, sticky="we", **pad)
        ttk.Button(frm, text="Pilih…", command=lambda: self._browse(self.var_master)).grid(
            row=r, column=2, **pad
        )

        r += 1
        ttk.Label(frm, text="Folder Hasil (Hot)").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.var_hot, width=46).grid(row=r, column=1, sticky="we", **pad)
        ttk.Button(frm, text="Pilih…", command=lambda: self._browse(self.var_hot)).grid(
            row=r, column=2, **pad
        )

        r += 1
        ttk.Label(frm, text="Port Bridge").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.var_port, width=10).grid(row=r, column=1, sticky="w", **pad)

        r += 1
        ttk.Separator(frm, orient="horizontal").grid(
            row=r, column=0, columnspan=3, sticky="we", pady=8
        )

        r += 1
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=3, sticky="we", **pad)
        self.btn_test = ttk.Button(btns, text="Test Koneksi", command=self._test)
        self.btn_test.pack(side="left")
        ttk.Button(btns, text="Simpan", command=self._save).pack(side="right")

        r += 1
        self.status_label = ttk.Label(
            frm, textvariable=self.var_status, foreground="#2563eb", wraplength=520
        )
        self.status_label.grid(row=r, column=0, columnspan=3, sticky="w", **pad)

    def _toggle_secret(self) -> None:
        self.ent_secret.config(show="" if self.show_secret.get() else "•")

    def _browse(self, var: tk.StringVar) -> None:
        initial = var.get() if os.path.isdir(var.get()) else os.path.expanduser("~")
        chosen = filedialog.askdirectory(initialdir=initial)
        if chosen:
            var.set(os.path.normpath(chosen))

    def _collect(self) -> dict:
        try:
            port = int(self.var_port.get().strip())
        except ValueError:
            port = 8767
        return {
            "vps_db_url": self.var_url.get().strip(),
            "jwt_secret": self.var_secret.get().strip(),
            "jwt_role": self.var_role.get().strip() or "service_role",
            "master_folder": self.var_master.get().strip(),
            "hot_folder": self.var_hot.get().strip(),
            "bridge_port": port,
        }

    def _test(self) -> None:
        cfg = self._collect()
        if not cfg["vps_db_url"] or not cfg["jwt_secret"]:
            self.var_status.set("URL dan JWT Secret wajib diisi dulu untuk tes.")
            return
        self.btn_test.config(state="disabled")
        self.var_status.set("Menguji koneksi…")

        def work() -> None:
            try:
                from erp_client import ErpClient

                client = ErpClient(cfg["vps_db_url"], cfg["jwt_secret"], cfg["jwt_role"])
                jobs = client.fetch_active_print_jobs()
                msg = f"✓ Berhasil. JWT diterima. {len(jobs)} job sedang diproses (in_progress)."
                color = "#16a34a"
            except Exception as e:  # noqa: BLE001
                msg = f"✗ Gagal: {e}"
                color = "#dc2626"
            self.root.after(0, lambda: self._test_done(msg, color))

        threading.Thread(target=work, daemon=True).start()

    def _test_done(self, msg: str, color: str) -> None:
        self.var_status.set(msg)
        self.status_label.config(foreground=color)
        self.btn_test.config(state="normal")

    def _save(self) -> None:
        cfg = self._collect()
        missing = [
            k for k in ("vps_db_url", "jwt_secret", "master_folder", "hot_folder") if not cfg[k]
        ]
        if missing:
            messagebox.showwarning("Belum lengkap", "Field wajib kosong: " + ", ".join(missing))
            return
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Gagal simpan", str(e))
            return
        self.var_status.set(f"✓ Tersimpan ke {CONFIG_PATH}")
        messagebox.showinfo("Tersimpan", "config.json berhasil disimpan.")


def main() -> None:
    root = tk.Tk()
    SettingsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
