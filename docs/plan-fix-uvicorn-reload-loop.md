# [RELOAD-LOOP-FIX] Plan — Hentikan Restart Loop uvicorn `--reload` (Opsi A: batasi watch ke `app/`)

> **Dokumen rencana untuk agen eksekutor / untuk diterapkan user sendiri.** Analisis log run
> (model `minimax-m2.7-highspeed`, 2026-08-10) menemukan **server dev restart tanpa henti** SETELAH run
> agen sukses. Penyebab: `make dev` menjalankan `uvicorn ... --reload` **tanpa batas direktori**, sehingga
> WatchFiles mengawasi **seluruh cwd termasuk `.venv/`**; setiap file `site-packages` yang tersentuh (oleh
> indexer OS / editor-LSP / proses `uv`/`pip` latar) memicu restart, dan tiap restart meng-init ulang MCP.
> Perbaikan = **1 baris di `Makefile`** (batasi watch ke `app/`) → **risiko sangat rendah, 100% reversibel
> via git**. Audit branch `retrofit/chat-completions-adapter` (2026-08-10). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Bukti dari log (TERBUKTI, run 2026-08-10 15:27–15:30)

### Fakta A — Agent loop TIDAK loop; ia selesai normal
- Iterasi 1 (15:27:42): pilih 3 tool (`get_keyword_volume`, `get_sentiment`, `search_posts`) → semua `status=success`.
- Iterasi 2 (15:28:12): `synthesis_mode=True`, `tools=0` → jawaban → self-check.
- **15:28:49: `event_type=final_answer is_final=True` → `run_end status=success iterations=2 latency_ms=67112`
  → `http.request.completed status_code=200`.** Run beres 2 iterasi, 67 detik.

→ Loop **bukan** di sisi agen. `max_iterations=12` tak pernah tercapai; tak ada re-gather berulang.

### Fakta B — Yang muter = auto-reload uvicorn mengawasi `.venv/`
`Makefile:15` (target `dev`): `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`.
`--reload` **tanpa `--reload-dir`** → WatchFiles mengawasi cwd rekursif, **termasuk `.venv/`**. Runtun restart:
```
15:28:04  WatchFiles detected changes in .venv/.../key_value/.../filetree/store.py   (SAAT run masih jalan)
15:28:49  run selesai → shutdown → restart (proc 72915 → 72977)
15:29:10  WatchFiles ... fastmcp/.../oauth_proxy/consent.py            → restart (72977 → 72988)
15:29:43  WatchFiles ... pip/_internal/.../resolver.py                 → restart (72988 → 73033)
15:30:30  WatchFiles ... beartype/_data/...                            → restart
15:30:46  WatchFiles ... openai/resources/fine_tuning/.../permissions.py → restart
```

### Fakta C — File yang "berubah" tersebar acak di banyak paket tak berhubungan
`key_value`, `fastmcp`, `pip`, `beartype`, `openai` — **tak ada satu jalur kode `app/` pun** yang menyentuh
kelima paket ini. → yang mengubah mtime **bukan aplikasi**, melainkan aktivitas filesystem di `.venv`
(indexer macOS / editor-LSP / `uv`/`pip` latar / penulisan cache). Karena watcher mengawasi `.venv`,
**tiap sentuhan → restart** → cascade tak berhenti.

### Fakta D — Warning MCP di tiap startup = GEJALA, bukan penyebab
`mcp.session_not_found_delay_detected` → `session_init_retry_success attempt=2 status=202` →
`mcp.server_connected tools=32` **berulang di setiap startup** karena tiap restart meng-init ulang MCP.
Ini race propagasi sesi SSE Verosight yang **non-fatal & auto-recover** (lihat [[verosight-mcp-sse-only]]).
Bukan sumber loop.

### Risiko nyata yang ditimbulkan
Reload pertama (15:28:04) menyambar **di tengah run**. Kali ini kebetulan tepat setelah tool terakhir, jadi
run tetap selesai — tapi timing sedikit berbeda bisa **memotong run agen di tengah** (kehilangan progres,
sesi MCP putus, jawaban gagal). Jadi ini bukan sekadar berisik: ia **mengancam kestabilan run**.

---

## 2. Desain — SATU perubahan di `Makefile` (target `dev`)

Batasi watcher hanya ke direktori kode aplikasi `app/`, sehingga `.venv/` (dan sumber noise lain) tak
lagi memicu reload.

**DARI (`Makefile:14-15`):**
```makefile
dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
**MENJADI:**
```makefile
dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app
```

**Kenapa `--reload-dir app` (bukan `--reload-exclude`):** whitelist ("hanya awasi `app/`") lebih tegas &
minim daripada blacklist ("awasi semua kecuali `.venv`") — tak perlu menebak semua path noise. Reload tetap
bekerja untuk perubahan kode Anda di `app/`; hanya berhenti bereaksi terhadap `.venv`.

> **Catatan:** jika di masa depan ada kode di luar `app/` yang ingin memicu reload (mis. modul di root),
> tambahkan `--reload-dir <dir>` lagi. Saat ini seluruh kode aplikasi ada di `app/`.

### (Opsional, direkomendasikan) target run tanpa reload untuk uji agen panjang
Agar run agen 60-detik+ **tak pernah** berisiko dipotong watcher, tambah target terpisah:
```makefile
run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000
```
dan daftarkan di `.PHONY` (baris 1). Gunakan `make run` saat menguji agen; `make dev` untuk iterasi kode.
**Status: OPSIONAL** — tak wajib untuk memutus loop, tapi menghilangkan risiko "reload mid-run" sepenuhnya.

---

## 3. Konsistensi dengan pekerjaan lain (WAJIB dibaca)

1. **Tidak menyentuh kode aplikasi sama sekali** — hanya `Makefile`. Ortogonal dengan `planner.py`
   (yang Anda koreksi) dan perubahan prompt `system.md`/`reflection.md`. Aman paralel.
2. **Tidak menyembunyikan isu lain.** Warning MCP `session_not_found` tetap muncul **sekali** saat startup
   normal (memang begitu, non-fatal). Plan ini hanya menghentikan **pengulangannya** akibat restart. Ia
   **bukan** perbaikan untuk churn sesi MCP itu sendiri (lihat `docs/plan-fix-mcp-session-churn-mitigation.md`
   bila memang mau ditangani terpisah).
3. **Tidak menutupi bug run agen** — karena run agen memang sudah sukses; plan ini murni soal server dev.

---

## 4. Tahapan (SATU tahap)

1. Terapkan perubahan `Makefile:15` (§2). (Opsional: tambah target `run` + daftarkan di `.PHONY`.)
2. Jalankan `make dev`; pastikan startup normal (`Application startup complete`).
3. **Commit** (tanpa push kecuali diminta). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
   Ingat: `.env` gitignored; kecualikan `AgenticTools/` dari commit.

**Gerbang uji (≥3 run agen nyata):**
- Jalankan `make dev`, lalu kirim ≥3 query agen (mis. "apakah prabowo sering dibicarakan?", + 2 query lain),
  masing-masing **menunggu run selesai** dan **diamkan server 2–3 menit** setelahnya.
- **Kriteria lulus:**
  - **TIDAK ada** baris `WatchFiles detected changes in '.venv/...'. Reloading...` selama & setelah run.
  - **TIDAK ada** siklus `shutdown.begin → Started server process → startup.complete` yang berulang tanpa
    Anda mengubah kode.
  - Warning `mcp.session_not_found` hanya muncul **sekali** (startup awal), tak berulang tiap menit.
  - Reload **masih bekerja** saat Anda sengaja menyimpan perubahan di file `app/` (uji: edit komentar di
    `app/main.py` → harus memicu **satu** reload).

**Revert-if-worse:**
`git checkout <baseline> -- Makefile` — bila reload jadi tak memicu untuk perubahan `app/` yang sah, atau
ada efek tak terduga pada workflow dev.

---

## 5. Verifikasi Akhir

- [ ] `Makefile` target `dev` memuat `--reload-dir app`.
- [ ] (Opsional) target `run` tanpa `--reload` ada & terdaftar di `.PHONY`.
- [ ] `make dev` startup normal.
- [ ] Gerbang uji ≥3 run: tak ada reload dari `.venv`; tak ada restart berulang; MCP warning sekali saja;
      reload tetap jalan untuk edit di `app/`.
- [ ] Tak ada perubahan kode aplikasi; commit (tanpa push kecuali diminta).

---

## 6. Risiko & Batasan (jujur)

- **RS1 — Sumber sentuhan `.venv` tak dihilangkan, hanya diabaikan.** Plan ini tak menghentikan proses yang
  menyentuh file `site-packages` (indexer/editor/`uv`); ia hanya membuat watcher berhenti peduli. Itu sudah
  cukup memutus loop. Bila ingin tahu pelakunya, pantau `fs_usage`/`lsof` saat idle — **di luar cakupan** dan
  tak perlu untuk perbaikan.
- **RS2 — Perilaku tooling dev, bukan logika app.** Tak ada unit test yang mengunci konten `Makefile`;
  kontrolnya = gerbang uji manual ≥3 run. Tak ada test suite yang berubah.
- **RS3 — Kode di luar `app/` tak lagi memicu reload.** Bila kelak ada modul aplikasi di root repo, tambahkan
  `--reload-dir` untuknya. Saat ini seluruh kode ada di `app/` → tak ada regresi.
- **RS4 — Bukan perbaikan churn sesi MCP.** Warning `session_not_found` saat startup tetap ada (by design,
  non-fatal). Penanganan churn sesi = isu terpisah ([[verosight-mcp-sse-only]],
  `docs/plan-fix-mcp-session-churn-mitigation.md`).
- **RS5 — Bocor token asing minimax tak tersentuh.** Jawaban di run ini masih memuat `话题`/`居高不下`
  (leakage minimax) — masalah berbeda (Option B language-sanitizer), bukan bagian plan loop ini.
- **RS6 — Reversibel penuh** via `git checkout Makefile`; tak ada migrasi/skema.
- **Rujukan:** `Makefile:1,14-15`, `app/main.py:153`, log run 2026-08-10 (gambar 1–4),
  memori [[agent-loop-reliability-state]], [[verosight-mcp-sse-only]], `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit `Makefile` + uji ≥3 run + commit) pada tahap terpisah.
