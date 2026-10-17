# Rencana Penyelesaian — 500 Internal Server Error pada `POST /v1/agent/query` & Tools Tidak Terbaca

> **Dokumen rencana untuk agen eksekutor.** Berisi diagnosis, hipotesis akar masalah (dengan
> rujukan `file:baris`), langkah perbaikan, dan verifikasi. Audit dilakukan pada branch
> `retrofit/chat-completions-adapter` (2026-08-03). Patuhi aturan global `docs/PROMPT_PLAN.md`
> §1.6 (tanpa mock, transparansi error).

---

## 1. Gejala

- Di Postman, `POST {{baseUrl}}/v1/agent/query` mengembalikan **HTTP 500** setelah **7.82 s**
  dengan body ter-generalisasi:
  ```json
  { "error": { "code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred." },
    "meta": { "request_id": "req-..." } }
  ```
- Saat kadang "berhasil", **tools tidak terpakai / tidak terbaca** dari Postman, padahal saat diuji
  lewat agen lain (mis. CLI/Antigravity) tools bisa berjalan.

---

## 2. Temuan Akar Masalah (grounded di kode)

### 2.1 Error asli DISEMBUNYIKAN, tetapi ADA di log server
`app/api/exceptions.py:60-69` — `generic_exception_handler` memanggil
`logger.exception("unhandled.exception", ...)` sebelum mengembalikan pesan generik. **Traceback
lengkap tercetak ke konsol uvicorn dan ke `LOG_FILE` (`./logs/agent.jsonl`).** Pesan di Postman
sengaja disamarkan; sumber sebenarnya harus dibaca dari log (lihat §4 Langkah 1).

### 2.2 `AgentLoop.run` tidak menangkap exception non-LLM → jadi 500
`app/agent/loop.py:163` membuka blok `try:` yang **hanya** dipasangkan dengan `finally:` di
`app/agent/loop.py:458` — **tidak ada `except`**. Hanya kegagalan LLM di dalam blok-dalam yang
ditangani (`except LLMError` / `except Exception` sekitar baris 219-265, yang mengembalikan
`RunResult(status="failed")`). Sementara itu, panggilan **persistensi DB** dan `tracer.flush()`
berada **di luar** blok-dalam:
- di dalam loop: `persist_execution_memory_snapshot(...)` & `conv_history.persist_iteration(...)`
  (≈ `loop.py:351-358`, `414-421`),
- di akhir run: `run_repo.update_status(...)`, `conv_history.persist_iteration(...)`,
  `tracer.flush()` (`loop.py:481-496`),
- di awal (sebelum `try`): pembuatan conversation & `run_repo.create(...)` (`loop.py:97-117`).

Jika salah satu **raise**, exception itu tidak tertangkap → naik ke `query_agent`
(`app/api/routes/agent.py:50`) → `generic_exception_handler` → **500 tersamar**.

### 2.3 Kenapa "tools tidak terbaca" — ini GEJALA dari 500, bukan bug terpisah
`app/db/session.py:66-84` — `get_session` melakukan `await session.commit()` **hanya sekali** di
akhir request; bila terjadi exception → `await session.rollback()`. Semua repo hanya `flush()`,
tidak pernah `commit()`. Artinya seluruh run berjalan dalam **satu transaksi** yang di-commit di
ujung request. Konsekuensi:
- Saat `run()` melempar exception (§2.2) → dependency `get_session` me-**rollback seluruh
  transaksi** → baris `runs`, `run_steps`, dan `tool_invocations` **hilang semua**.
- Sehingga `GET /v1/runs/{id}/tools` kosong dan run seakan "tidak memakai tools", padahal tool
  mungkin sudah dieksekusi di memori. **Perbaiki §2.2 dulu; kemungkinan besar masalah tools ikut
  hilang.**

### 2.4 Kenapa jalur non-API (CLI/Antigravity) tetap jalan & bisa tools
`scripts/simulate.py:112` menjalankan `AgentLoop()` **tanpa `session`**. Dengan `self.session is
None`, seluruh cabang persistensi DB dilewati (`loop.py:97`, `351`, `481`). Jadi jalur itu tidak
pernah menyentuh DB → tidak kena error persistensi → tools berjalan normal. **API selalu
menyuntik `session`** (`agent.py:50` → `AgentLoop(session=session)`), jadi hanya jalur Postman/API
yang mengeksekusi kode persistensi yang bermasalah. Asimetri ini konsisten dengan gejala.

---

## 3. Hipotesis Sumber Exception (diurutkan; konfirmasi via traceback)

Durasi **7.82 s** menandakan panggilan LLM **berhasil** (kemungkinan 2 kali: jawaban + self-check),
sehingga kegagalan paling mungkin terjadi **saat persistensi di akhir run**, bukan di awal.

- **H1 — Migrasi belum diterapkan / schema drift.** Tabel/kolom tidak ada di DB yang dipakai API
  (mis. belum `make migrate`, atau DB berbeda dari `.env`). Flush pertama yang menyentuh kolom
  hilang → error. *Cek:* jalankan migrasi; bandingkan `DATABASE_URL` runtime dengan DB yang
  benar-benar dipakai. (Model ORM sudah punya kolom lengkap — `app/db/models.py:126-165` — jadi
  ini soal DB fisik, bukan definisi model.)
- **H2 — Serialisasi JSONB gagal.** Kolom `run_steps.payload`, `tool_invocations.arguments/result`,
  `execution_memory.value` bertipe `JSONB` (`models.py:193,226-227`). Bila payload memuat objek
  non-JSON-serializable (datetime, set, objek pydantic, bytes), insert JSONB via SQLAlchemy bisa
  gagal (serializer default tidak memakai `default=str`). *Cek:* traceback bertipe
  `TypeError: Object of type ... is not JSON serializable` atau error asyncpg encode.
- **H3 — Sesi dalam keadaan gagal (PendingRollbackError).** Satu error di-`flush()` lebih awal
  menaruh `AsyncSession` ke state gagal; setiap operasi berikutnya (`update_status`, `flush`)
  melempar `PendingRollbackError`. *Cek:* traceback menyebut `PendingRollbackError` /
  `InvalidRequestError`.
- **H4 — Bootstrap conversation bermasalah.** Blok `loop.py:102-112` memanggil `conv_repo.create()`
  (membuat conversation ber-id acak) lalu menambah `Conversation(id=conv_id)` dan `flush()`.
  Berpotensi `IntegrityError` (FK/duplikat) atau efek samping transaksi. *Cek:* traceback
  `IntegrityError` seputar `conversations`.
- **H5 — Koneksi DB gagal saat runtime.** Postgres mati / `DATABASE_URL` salah / pool gagal.
  Biasanya cepat (bukan 7.82 s), jadi prioritas rendah, tetapi tetap diperiksa via
  `GET /health/deps`.

> Jangan menebak; **traceback dari §4 Langkah 1 menentukan hipotesis mana yang benar.**

---

## 4. Langkah Diagnostik (WAJIB dulu — jangan langsung menambal)

**Langkah 1 — Ambil error asli.**
- Jalankan server dengan log terlihat: `make dev` (uvicorn). Ulangi request Postman yang 500.
- Baca konsol uvicorn / `./logs/agent.jsonl` pada event `unhandled.exception` — ambil **tipe
  exception + baris terdalam di `app/...`**. Ini menentukan H1–H5.
  ```bash
  tail -n 200 ./logs/agent.jsonl | grep -i "unhandled.exception\|Traceback\|Error" 
  ```

**Langkah 2 — Pastikan DB & migrasi.**
```bash
docker compose up -d          # Postgres rag_db
make migrate                  # terapkan skema
curl -s http://localhost:8000/health/deps   # db harus "ok"/"connected"
```
Bandingkan `DATABASE_URL` di `.env` dengan DB yang benar-benar dijalankan.

**Langkah 3 — Konfirmasi tools termuat di proses server (untuk P2).**
```bash
curl -s http://localhost:8000/v1/tools | jq 'length, .[].name'   # harus > 0
```
Periksa `VEROSIGHT_MODE` dan `VEROSIGHT_ENABLED` di `.env`. Bila kosong, registry mungkin hanya
berisi tool lokal — bukan bug 500, tapi relevan untuk "tools tidak terpakai".

**Langkah 4 — Reproduksi tanpa DB untuk isolasi.**
```bash
python scripts/simulate.py ask "Apa yang sedang tren di Indonesia minggu lalu?"
```
Jika sukses & memanggil tool di sini tapi 500 di API → mengonfirmasi bahwa akar masalah ada di
**jalur persistensi DB** (§2.2/§2.3), bukan di LLM/tools.

---

## 5. Rencana Perbaikan

### TASK 1 — Jadikan kegagalan run TIDAK menjadi 500 tak-tertangkap (prioritas tertinggi)
**File:** `app/agent/loop.py`
**Perubahan:** tambahkan `except Exception` pada blok `try` besar (yang kini `try/finally` di
`loop.py:163`/`458`), meniru pola penanganan `LLMError` yang sudah ada:
- log via `structlog` + `tracer.event("error", ...)` dengan `ErrorReport(component=...)`,
- set `state.status = "failed"`,
- **coba** `run_repo.update_status(status="failed", error=<pesan>)` dalam `try/except` sendiri
  agar kegagalan persistensi tidak menutupi error asli,
- kembalikan `RunResult(status="failed", answer=<pesan ErrorReport>, ...)` — sehingga API membalas
  **200 dengan status "failed"** (bukan 500 tersamar), sesuai desain transparansi §1.6.

**DoD:** memicu error persistensi/tool apa pun tidak lagi menghasilkan HTTP 500; response memuat
`status:"failed"` dan pesan yang menyebut komponen + tahap.

### TASK 2 — Bungkus persistensi agar tidak menjatuhkan run & tidak me-rollback seluruh jejak
**File:** `app/agent/loop.py`, dan/atau `app/db/session.py`
**Perubahan (pilih sesuai temuan Langkah 1):**
- Bungkus tiap panggilan `persist_execution_memory_snapshot`, `persist_iteration`,
  `run_repo.update_status`, `tracer.flush()` dengan `try/except` yang mencatat error tetapi tidak
  membatalkan pengembalian `RunResult`.
- Pertimbangkan **commit bertahap** (mis. commit setelah `run_repo.create` dan setelah tiap
  iterasi) agar jejak (run, run_steps, tool_invocations) tidak hilang saat error terjadi
  belakangan. Catatan: ubah pola commit dengan hati-hati agar tetap konsisten dengan
  `get_session` (yang commit di akhir request).

**DoD:** bila persistensi sebagian gagal, run tetap mengembalikan jawaban/status terakhir; jejak yang
sudah tertulis tidak seluruhnya hilang; `GET /v1/runs/{id}/tools` menampilkan tool yang berjalan.

### TASK 3 — Perbaiki akar sesuai hipotesis terkonfirmasi
- **Jika H1:** dokumentasikan & pastikan `make migrate` menjadi prasyarat; tambahkan cek di
  `GET /health/deps` yang lebih tegas (opsional).
- **Jika H2 (JSONB):** pastikan seluruh payload JSONB **JSON-safe** sebelum insert. Opsi:
  normalisasi payload di `RunTracer`/repo (mis. `json.loads(json.dumps(payload, default=str))`),
  atau set `json_serializer=lambda v: json.dumps(v, default=str)` pada engine
  (`app/db/session.py::init_engine`). Pilih yang paling terlokalisasi.
- **Jika H3:** setelah error pertama, lakukan `session.rollback()` terkontrol sebelum menulis baris
  "failed"; atau isolasi penulisan status-gagal di sesi/transaksi terpisah.
- **Jika H4:** sederhanakan bootstrap conversation (`loop.py:102-112`) — hindari `conv_repo.create()`
  ber-id acak; cukup buat `Conversation(id=conv_id)` bila belum ada (upsert/`get`-then-create),
  tangani `IntegrityError`.
- **Jika H5:** perbaiki `DATABASE_URL`/Docker; bukan perubahan kode aplikasi.

**DoD:** request Postman yang sama mengembalikan **200** dengan `status:"success"` (atau "failed"
yang jujur bila memang layanan gagal), tanpa 500 tersamar.

### TASK 4 — Verifikasi tools benar-benar dialirkan ke Responses API (P2, setelah P1 beres)
**File (audit):** `app/agent/loop.py:207-218`, `app/tools/registry.py:157-176`
- Cek trace `GET /v1/runs/{run_id}/trace` pada event `llm_request` → pastikan `tools > 0`
  (registry mengirim `to_responses_tools()`).
- Bila `tools=0`: registry kosong di proses server → periksa auto-discovery lifespan
  (`app/main.py:46-53`) dan `VEROSIGHT_MODE`.
- Bila `tools>0` tetapi model tidak memanggilnya: itu **perilaku model** untuk query tsb, bukan bug
  500 — tetap sah. Uji dengan query yang jelas butuh tool (mis. "tren minggu ini") dan periksa
  `function_calls` di trace.

**DoD:** trace menunjukkan tool ditawarkan; untuk query berbasis data, `tool_invocations` terisi dan
`GET /v1/runs/{id}/tools` menampilkannya.

### TASK 5 (opsional) — Buka error asli di mode lokal
**File:** `app/api/exceptions.py`
- Saat `APP_ENV=="local"`, sertakan detail exception asli (tipe + pesan) pada
  `generic_exception_handler` agar debugging via Postman tidak buta. Di non-local tetap generik.
**DoD:** di lokal, 500 (jika masih ada) menampilkan pesan yang berguna, bukan hanya kalimat generik.

---

## 6. Verifikasi Akhir

- [ ] Traceback asli teridentifikasi dari log; hipotesis (H1–H5) terkonfirmasi.
- [ ] `POST /v1/agent/query` mengembalikan **200** (`status` success/failed yang jujur), bukan 500
      tersamar, untuk query normal.
- [ ] Kegagalan internal muncul sebagai `RunResult(status="failed")` + `ErrorReport`
      (component+stage) di response/trace — sesuai §1.6.
- [ ] `GET /v1/runs/{run_id}` dan `/tools` menampilkan run beserta tool_invocations (jejak tidak
      ter-rollback).
- [ ] `GET /v1/tools` di server menampilkan tool (>0) dan sesuai `VEROSIGHT_MODE`.
- [ ] Trace `llm_request` menunjukkan `tools>0`; query berbasis data memicu `function_calls`.
- [ ] `python scripts/simulate.py ask ...` tetap berjalan (regresi jalur non-DB).
- [ ] Rahasia (`api_key`/`authorization`) tetap teredaksi di log/trace/response.

---

## 7. Catatan Implementasi

- **Urutan wajib:** Diagnostik (§4) → TASK 1 (stop the bleeding: no unhandled 500) → TASK 3 (akar) →
  TASK 2/4 (jejak & tools) → TASK 5 (opsional).
- **Prinsip §1.6:** setiap kegagalan dilaporkan apa adanya (komponen + tahap), tanpa fallback data
  palsu. TASK 1 justru menegakkan prinsip ini: run gagal ≠ 500 buram.
- **Jangan** menyembunyikan error dengan menonaktifkan persistensi begitu saja; tujuannya membuat
  kegagalan **terlihat dan terkelola**, serta menjaga jejak audit.
- **Rujukan:** kontrak internal & aturan global `docs/PROMPT_PLAN.md` (§1.3–§1.6); struktur endpoint
  `docs/plan-postman.md`; sinkronisasi model `docs/Plan-perubahan-model.md`.
- **Batasan:** dokumen ini rencana; perbaikan kode, migrasi, dan commit dilakukan pada tahap
  eksekusi oleh agen/developer.
```
