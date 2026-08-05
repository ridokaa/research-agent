# Rencana Perbaikan — Over-Redaction: `keyword`/`key` Keliru Disamarkan `***` di Trace

> **Dokumen rencana untuk agen eksekutor.** Memperbaiki redaksi rahasia yang **terlalu agresif**
> (false-positive) sehingga field non-rahasia seperti `keyword` dan `key` ikut ditulis `***` di
> log/trace, menyulitkan debugging. Audit branch `retrofit/chat-completions-adapter` (2026-08-04).
> Patuhi `docs/PROMPT_PLAN.md` §1.6 (rahasia tetap wajib teredaksi).

---

## 1. Ringkasan

Di trace/log, argumen tool `keyword` muncul sebagai `'***'` (mis. `arguments={'days':7,'keyword':'***'}`)
dan field `key` pada `memory_write` jadi `key='***'`, padahal keduanya **bukan rahasia**. Ini
akibat logika redaksi di `app/logging/tracer.py` memakai **pencocokan substring** `"key"` — sehingga
`keyword` (mengandung "key") keliru dianggap sensitif. Nama masalah: **over-redaction /
false-positive redaction**. **Bukan bug fungsional** (tool tetap menerima nilai asli; redaksi hanya
pada payload log), tetapi membuat trace sulit dibaca. Perbaikan: cocokkan **nama field yang tepat**,
bukan substring, tanpa melemahkan redaksi rahasia asli.

---

## 2. Gejala & Bukti

- Trace: `keyword: '***'`, `memory_write key='***'`; sementara `query: 'keuangan'` **tidak** tersamar.
- **Log-only, bukan eksekusi:** tool tetap memakai nilai asli — hasil `get_topics` mengembalikan
  konten finansial Indonesia (OJK/BI), bukti keyword `keuangan` benar-benar terkirim ke Verosight.
- Redaksi hanya diterapkan pada payload trace: `app/logging/tracer.py:253`
  `safe_payload = _redact(payload or {})`.

---

## 3. Akar Masalah (kode)

`app/logging/tracer.py`:
- `_REDACT_KEYS` (baris ~53-67): daftar kunci sensitif (`api_key`, `apikey`, `authorization`,
  `token`, `access_token`, `refresh_token`, `password`, `secret_key`, `private_key`, `x-api-key`, …).
- `_redact()` (baris ~86-116), kondisi `is_secret_key` (baris ~99-110):
  ```python
  is_secret_key = (
      _REDACT_PATTERN.fullmatch(k_lower)                     # (A) exact — benar
      or (
          ("token" in k_lower or "auth" in k_lower or "key" in k_lower)   # (B) SUBSTRING — biang masalah
          and not k_lower.endswith("tokens")
          and "token_in" not in k_lower
          and "token_out" not in k_lower
          and k_lower != "tokens"
      )
  )
  ```
  Bagian **(B)** meredaksi **semua** field yang *mengandung* `"key"`/`"token"`/`"auth"`. Karena
  `"key" in "keyword"` → `keyword` disamarkan; `key` juga (mengandung "key").

**Titik kedua (opsional):** baris ~93 `val = pat.sub("[REDACTED]", val)` meredaksi **nilai string**
yang memuat kata seperti `token`/`authorization` di mana pun — berpotensi over-redaksi teks biasa
(mis. jawaban yang menyebut kata "token"). Bukan penyebab kasus ini, tapi layak ditinjau.

---

## 4. Rencana Perbaikan

### TASK 1 — Cocokkan nama field secara presisi (akar utama)
**File:** `app/logging/tracer.py`
**Perubahan pada kondisi `is_secret_key` (baris ~99-110):** ganti pencocokan substring (B) dengan
pencocokan **tepat / berbatas**:
- **Pertahankan (A)** `_REDACT_PATTERN.fullmatch(k_lower)` (exact match daftar `_REDACT_KEYS`).
- **Ganti (B)** menjadi pola **suffix/prefix aman**, mis. redaksi bila:
  - `k_lower.endswith(("_key", "_token", "_secret", "_password"))`, atau
  - `k_lower.startswith(("x-api", "api_key", "access_token", "refresh_token"))`.
- **Allowlist eksplisit** (jangan pernah redaksi): `{"keyword", "keywords", "key", "tokens",
  "token_in", "token_out", "query"}` — cek allowlist **sebelum** memutuskan redaksi.

Contoh hasil yang diharapkan:
| Field | Sekarang | Sesudah |
|---|---|---|
| `keyword` | `***` ❌ | nilai asli ✅ |
| `key` (map/memory) | `***` ❌ | nilai asli ✅ |
| `api_key` / `x-api-key` | `***` | `***` (tetap) |
| `access_token` / `authorization` / `password` | `***` | `***` (tetap) |
| `token_in` / `token_out` / `tokens` | tidak | tidak (tetap) |

**DoD:** `keyword`/`key` tampil apa adanya; seluruh kunci sensitif nyata tetap `***`.

### TASK 2 — (Opsional) Tinjau redaksi nilai-string (baris ~93)
Batasi `pat.sub("[REDACTED]", val)` agar tidak menyamarkan teks biasa yang kebetulan memuat kata
`token`/`authorization`. Opsi: hanya terapkan pada nilai dari **kunci** yang memang sensitif, atau
hanya jika nilai menyerupai kredensial (mis. cocok pola `sk-…`, `vlt_live_…`, JWT). **Jangan**
melemahkan redaksi kredensial asli.
**DoD:** kredensial di nilai string tetap tersamar; teks jawaban biasa tidak lagi ter-`[REDACTED]`
tanpa alasan.

---

## 5. Strategi Pengujian (deterministik, tanpa jaringan — §1.6)

Unit test langsung pada `_redact()`:
- **Harus TIDAK diredaksi:** `{"keyword":"keuangan"}`, `{"key":"get_volume:1:1"}`,
  `{"keywords":[...]}`, `{"query":"x"}`, `{"token_in":10,"token_out":5,"tokens":3}`.
- **Harus TETAP diredaksi:** `{"api_key":"sk-..."}`, `{"x-api-key":"vlt_live_..."}`,
  `{"authorization":"Bearer ..."}`, `{"access_token":"..."}`, `{"refresh_token":"..."}`,
  `{"password":"..."}`, `{"secret_key":"..."}`.
- **Nested:** struktur bersarang (dict dalam list dalam dict) → aturan konsisten di semua kedalaman.
- (Bila TASK 2) nilai string berisi `sk-…`/`vlt_live_…` → tersamar; kalimat biasa berisi kata
  "token" → tidak.

---

## 6. Verifikasi Akhir

- [ ] Jalankan query yang memakai keyword → trace `GET /v1/runs/{id}/trace` menampilkan
      `keyword: '<nilai asli>'`, bukan `***`; `memory_write key=` menampilkan key asli.
- [ ] Trace/log **tidak** memuat `api_key`/`x-api-key`/`authorization`/token dalam bentuk jelas
      (tetap `***`). Uji khusus: kirim header/argumen berisi kunci → pastikan tersamar.
- [ ] Test unit `_redact` (§5) lulus semua.
- [ ] Regresi: `tool_invocations`/`run_steps` tetap tersimpan; tak ada perubahan perilaku eksekusi.

---

## 7. Risiko & Catatan

- **RS1 — Jangan melemahkan keamanan.** Tujuan hanya menghentikan **false-positive**; seluruh kunci
  di `_REDACT_KEYS` dan varian `*_key`/`*_token`/`Authorization` **wajib tetap** tersamar. Uji §6
  bullet kedua adalah pengaman.
- **RS2 — Konsistensi dua fungsi.** `_REDACT_PATTERN` dipakai untuk key-match **dan** value-sub
  (baris 93). Perubahan TASK 1 di jalur key jangan sampai mengubah perilaku value yang masih
  diinginkan (TASK 2 menanganinya terpisah).
- **RS3 — Prioritas rendah.** Ini bukan bug fungsional (log-only). Aman dikerjakan kapan saja;
  manfaat utama = trace lebih terbaca untuk debugging (mis. melihat keyword yang dipakai agen).
- **Rujukan:** `docs/PROMPT_PLAN.md` (PROMPT 2 & §1.6 — redaksi rahasia wajib),
  `docs/catatan/jawaban-terpotong-postman.md` (konteks trace).
- **Batasan tugas:** dokumen rencana; eksekusi (edit `tracer.py`, uji, commit) pada tahap terpisah.
