# ganci-print-puller

Tool kecil (standalone) yang **menarik antrian print Gantungan Kunci langsung dari
ERP** (`db.erp-hog.com` / PostgREST) lalu **menyalin file desain `.cdr` di komputer
operator** — pengganti alur lama (sortir Python + Google Sheet).

Repo ini **terpisah** dan tidak mengubah apa pun di repo ERP (`heavyobjectgroup`).
Sibling dari [`stiker-print-puller`](../stiker-print-puller); strukturnya sama tapi
disederhanakan untuk `.cdr` (vektor, tanpa ekstraksi halaman PDF).

## Apa yang dilakukan

1. `GET gk_print_jobs?status=eq.in_progress` dari PostgREST (read-only) — yaitu batch
   yang **sudah diklaim** operator di halaman web **Operator Print GK**.
2. Tiap job sudah merujuk ke **charm L individual** (mis. `GK-ATM-0003271-L`) karena
   bundle (BS=10, M/S=5) sudah di-expand di ERP (Proses Produksi).
3. Untuk tiap SKU charm: cari `<sku>.cdr` di `master_folder` (folder lokal — boleh
   Google Drive yang ter-sync), salin **SATU** file ke `hot_folder`.
4. Tulis **manifest** (`manifest_<ts>.csv`: SKU, Nama, Jumlah Pcs, File) + log.

Operator membuka tiap `.cdr` sekali di CorelDRAW lalu mengatur jumlah salinan sesuai
manifest. Pencetakan/layout tidak ditangani tool ini.

> Status job tetap dipegang operator lewat web; tool hanya menandai `pulled_at`.
> **Output dipisah per BATCH**: tiap batch punya sub-folder sendiri di hot folder
> (nama = `batch_code`, mis. `<hot>/2026-07-08-B4/`). **Tidak ada auto-hapus** —
> file lama dibiarkan, hapus manual kalau perlu.

## Setup

```bat
pip install -r requirements.txt
```

Lalu jalankan **`settings.bat`** (atau `gui.bat` → tab Pengaturan): isi URL + JWT
Secret + pilih **Folder Master .cdr** & **Folder Hasil** lewat tombol **Pilih…**,
klik **Test Koneksi**, lalu **Simpan**. `config.json` dibuat otomatis (gitignored).

| Field | Isi |
|---|---|
| `vps_db_url` | `https://db.erp-hog.com` |
| `jwt_secret` | nilai **`VPS_DB_JWT_SECRET`** (sama dengan `PGRST_JWT_SECRET` di VPS). **Rahasia.** |
| `jwt_role` | `service_role` (default) |
| `master_folder` | folder berisi file `.cdr` master (dinamai per-SKU, mis. `GK-ATM-0003271-L.cdr`) |
| `hot_folder` | folder output hasil salin |
| `bridge_port` | port bridge lokal (default `8767` — beda dari stiker `8766`) |

## Jalankan

**A. GUI (disarankan)** — `gui.bat`: dua tab (Pengaturan + Eksekusi & Log).
- Isi **jumlah charm** lalu **"Tarik Sebagian"** — klaim N charm berikutnya yang
  belum ditarik (desain UTUH, jadi total bisa sedikit > N). Atau **"Tarik Semua
  Sisa"** untuk ambil semua yang belum ditarik.
- **Progres batch** (bar + `X/Y charm · sisa Z`) menandakan berapa yang sudah
  ditarik lintas-komputer. **↻ Refresh** untuk memperbarui, **Reset Tarik** untuk
  menganggap semua belum ditarik (pemulihan).
- Isi **Nama Komputer** di Pengaturan (penanda "ditarik oleh"; kosong = hostname).

### Tarik SEBAGIAN (banyak komputer)

Beberapa komputer print bisa membagi satu batch tanpa dobel: mis. 100 charm →
KOMP-1 klik "Tarik 50" (dapat charm 1-50), KOMP-2 klik "Tarik 50" (sisa 51-100).
Klaim **atomik** di DB (`FOR UPDATE SKIP LOCKED`) — dua komputer tak akan menarik
job yang sama. Status **JOB** (pending/in_progress/done) tetap dipegang web; tool
hanya menandai kolom `pulled_at` (status-tarik). Batch di-*klaim ulang* di web →
status-tarik otomatis reset.

**B. CLI** — `python main.py` (atau `start.bat`): sekali jalan.

**C. Bridge (dipicu tombol ERP)** — `server.bat` (atau `python server.py`): biarkan
terbuka. Di halaman **Operator Print GK** ERP, tombol **"Tarik Desain (.cdr)"** memicu
`POST http://127.0.0.1:8767/pull`.

| Endpoint | Guna |
|---|---|
| `GET /health` | cek bridge hidup (dipakai tombol sebelum kirim) |
| `POST /pull` | jalankan pull+salin, balas ringkasan JSON |

## Update aplikasi

Aplikasi ini **auto-update** dari GitHub (`git pull`), asal komputer operator
meng-**clone** repo ini (bukan salin manual) dan punya `git` di PATH.

- **Saat launch** — `gui.bat` / `start.bat` menjalankan `updater.py` dulu: kalau ada
  versi baru di `origin/main`, otomatis `git pull --rebase --autostash` (config.json
  aman, gitignored). Notifikasi "Aplikasi diperbarui vX → vY" muncul di GUI.
- **Manual** — tombol **"🔄 Perbarui"** di tab Eksekusi & Log: cek + apply update, lalu
  tawarkan restart.

Kalau bukan git repo / tanpa internet, app tetap jalan dengan versi lokal.

Pertama kali di komputer operator:

```bat
git clone https://github.com/dennypa77/ganci-print-puller.git
cd ganci-print-puller
pip install -r requirements.txt
```

## Catatan teknis

- **Dependency minim**: JWT di-mint pakai stdlib (`hmac`/`hashlib`), HTTP `urllib`,
  manifest `csv`. `customtkinter` hanya untuk GUI — CLI/bridge jalan tanpanya.
- Algoritma JWT identik dengan `erp-frontend/src/lib/server/vpsDbJwt.ts` (HS256,
  body `{iat, exp, role}`). PostgREST verifikasi dengan secret yang sama.
- Lookup file: nama file `.cdr` = SKU charm L persis (`GK-ATM-0003271-L.cdr`), dengan
  alias otomatis ATM↔ANM (folder master kadang pakai salah satu prefix).
