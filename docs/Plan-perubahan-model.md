# Plan — Sinkronisasi Sistem ke Model OpenAI (`gpt-4o-mini`, Responses API)

> **Untuk agen eksekutor.** Dokumen ini adalah rencana kerja konkret. Ikuti berurutan.
> Setiap task punya: *tujuan*, *file yang disentuh*, *perubahan*, dan *Definition of Done (DoD)*.
> Patuhi aturan global proyek di `docs/PROMPT_PLAN.md` §1.6 (TANPA mock, transparansi error).
> Arsitektur wajib tetap **style-agnostic**: seluruh perbedaan gaya API hanya diserap di `app/llm/`.
> Jangan menambah percabangan `if style == ...` di `app/agent/` atau di luar client.

---

## 0. Konteks

`.env` baru saja diganti agar memakai **OpenAI** langsung:

```dotenv
LLM_API_STYLE=responses
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_STORE=true
```

Sebelumnya konfigurasi mengarah ke gateway 9Router / DeepSeek (`chat_completions`).
Kode di branch ini (`retrofit/chat-completions-adapter`) **sudah** mendukung dua gaya API
(`responses` dan `chat_completions`) melalui satu abstraksi `LLMClient`. Jadi tugas ini
**bukan** menulis ulang client — melainkan **menutup celah sinkronisasi** agar seluruh alur
benar-benar cocok dengan gpt-4o-mini + Responses API, dan agar konfigurasi `.env` benar-benar
terpakai (bukan mati/diam-diam diabaikan).

**Catatan model gpt-4o-mini (Responses API):**
- Mendukung: `temperature`, `store` + `previous_response_id`, tool definisi **FLAT**,
  `text.format = {"type":"json_object"}`.
- Batas output token: **16.384**. Nilai di atas itu → HTTP 400.
- **Bukan** model reasoning → jangan kirim `reasoning.effort` (client sudah benar: hanya
  mengirim bila `LLM_REASONING_EFFORT` terisi; di `.env` kosong ✔).

**Aman:** `.env` tidak di-track git (gitignore). Jangan pernah commit `.env` atau kunci asli.

---

## 1. Temuan Celah Sinkronisasi (hasil audit)

| # | Celah | Dampak pada gpt-4o-mini | Prioritas |
|---|---|---|---|
| A | `LLM_MAX_OUTPUT_TOKENS=32000` di `.env` melebihi cap 16.384 | Jika di-plumb → HTTP 400 dari OpenAI | Tinggi |
| B | `loop.py` memanggil `llm.respond()` **tanpa** `temperature` / `max_output_tokens` | `LLM_TEMPERATURE` & `LLM_MAX_OUTPUT_TOKENS` di `.env` jadi **konfigurasi mati** — perilaku model tak terkontrol | Tinggi |
| C | `tests/test_llm_live.py` meng-hardcode `api_style="chat_completions"` | Jalur `responses` yang kini aktif tidak teruji live | Sedang |
| D | Komentar `.env` masih menyebut "9Router / DeepSeek Chat Completions" | Menyesatkan; bukan bug runtime | Rendah |
| E | `/health/deps` hanya melaporkan `configured (...)`, tidak memeriksa reachability OpenAI | Kesalahan konfigurasi baru ketahuan saat run pertama | Rendah (opsional) |

---

## 2. Task Plan

### TASK 1 — Perbaiki nilai konfigurasi yang tak kompatibel

**Tujuan:** `.env` konsisten dengan batas kemampuan gpt-4o-mini dan tidak menyesatkan.

**File:** `.env`, `.env.example`

**Perubahan:**
1. `LLM_MAX_OUTPUT_TOKENS=32000` → nilai aman ≤ 16384. Rekomendasi **`4096`**
   (cukup untuk jawaban riset panjang; hemat token). Jika memang butuh output panjang,
   maksimum absolut `16384` — jangan lebihi.
2. Perbarui komentar baris `# LLM (9Router / DeepSeek Chat Completions)` menjadi mencerminkan
   OpenAI Responses API, mis. `# LLM (OpenAI — Responses API)`.
3. Biarkan baris `# LLM_MODEL=xkiro/...` sebagai contoh alternatif (comment), tapi pindahkan
   ke bawah nilai aktif agar tidak membingungkan; pastikan hanya **satu** `LLM_MODEL` aktif.
4. Selaraskan `.env.example` bila perlu (nilai default aman, komentar dwi-gaya tetap ada).

**DoD:** `LLM_MAX_OUTPUT_TOKENS ≤ 16384`; tidak ada komentar yang menyebut gateway lain sebagai
gaya aktif; hanya satu `LLM_MODEL` non-komentar.

---

### TASK 2 — Plumb `temperature` & `max_output_tokens` dari settings ke client (celah utama)

**Tujuan:** `LLM_TEMPERATURE` dan `LLM_MAX_OUTPUT_TOKENS` benar-benar diterapkan pada setiap
panggilan LLM. Saat ini ketiga pemanggilan `llm.respond()` di `loop.py` (utama, self-check,
fallback) tidak mengirim keduanya, sehingga konfigurasi `.env` diam-diam diabaikan.

**File:** `app/llm/client.py`, `app/agent/loop.py`

**Perubahan (pendekatan yang disarankan — simpan default di client):**
1. Di `LLMClient.__init__`, terima dan simpan `temperature: float | None = None` dan
   `max_output_tokens: int | None = None` sebagai default instance (`self._temperature`,
   `self._max_output_tokens`).
2. Di `LLMClient.respond(...)`, bila argumen `temperature` / `max_output_tokens` per-panggilan
   `None`, jatuh ke default instance. Teruskan ke `_build_payload`.
3. Di `_build_payload`:
   - Gaya `responses`: isi `payload["max_output_tokens"]` dan `payload["temperature"]` bila ada.
     *(Sudah ada cabangnya — pastikan sumber nilainya default instance, bukan hanya arg.)*
   - Gaya `chat_completions`: petakan ke `max_tokens` + `temperature` (sudah ada).
4. **Guard model reasoning (defensif, tetap style-agnostic):** bila nama model tergolong
   reasoning OpenAI (mis. prefiks `o1`, `o3`, `gpt-5`), **jangan** kirim `temperature`
   (Responses API menolaknya). gpt-4o-mini tidak terpengaruh; guard ini hanya melindungi bila
   `LLM_MODEL` diganti kelak. Implementasikan sebagai helper kecil di client
   (`_supports_temperature(model) -> bool`).
5. Di `app/agent/loop.py`, saat mengonstruksi `LLMClient(...)` (sekitar baris 133), tambahkan:
   ```python
   temperature=settings.LLM_TEMPERATURE,
   max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
   ```
   Dengan default tersimpan di client, ketiga `respond()` (utama, self-check, fallback)
   otomatis memakainya tanpa perlu diubah satu-satu.

**Larangan:** jangan menaruh nilai temperature/token di `app/agent/`; nilai berasal dari
`settings`, diteruskan sekali saat konstruksi client. Loop tetap tidak tahu gaya API.

**DoD:**
- Test logika murni (tanpa jaringan): konstruksi `LLMClient(..., temperature=0.2,
  max_output_tokens=4096)` lalu `_build_payload(...)` untuk gaya `responses` menghasilkan
  `payload["temperature"] == 0.2` dan `payload["max_output_tokens"] == 4096`.
- Test: untuk model reasoning tiruan (mis. `model="o1-mini"`), `temperature` **tidak** muncul
  di payload; `max_output_tokens` tetap muncul.
- Untuk gaya `chat_completions`, nilai ter-map ke `max_tokens`.

---

### TASK 3 — Pastikan default & konstanta konsisten dengan Responses/OpenAI

**Tujuan:** tidak ada sisa hardcode gaya lain pada jalur non-test yang bertentangan dengan
`LLM_API_STYLE`.

**File:** `app/config.py`, `app/agent/loop.py`, `app/llm/client.py` (audit saja)

**Perubahan/audit:**
1. Verifikasi `config.py`: `LLM_API_STYLE` default `responses`, `LLM_BASE_URL` default OpenAI,
   `LLM_MODEL` default `gpt-4o-mini` — **sudah benar**, tidak perlu diubah. Konfirmasi saja.
2. Grep pemakaian `api_style` memastikan selalu bersumber dari `settings.LLM_API_STYLE`
   (loop sudah begitu). Tidak boleh ada literal `"chat_completions"` / `"responses"` yang
   menimpa konfigurasi di jalur runtime (`app/`).
3. `_PLACEHOLDER_KEY = "sk-xxx"` di client cocok dengan default `config.py` — biarkan.

**DoD:** `grep -rn '"chat_completions"\|"responses"' app/` hanya menemukan: definisi Literal di
`config.py`, pemetaan endpoint di `client.py`, dan pembacaan `settings.LLM_API_STYLE`. Tidak ada
override gaya yang mengabaikan konfigurasi.

---

### TASK 4 — Selaraskan test live dengan `LLM_API_STYLE` aktif

**Tujuan:** test live mengikuti gaya dari konfigurasi, dan jalur `responses` (kini aktif) punya
cakupan live.

**File:** `tests/test_llm_live.py` (dan/atau tambahkan `tests/test_llm_live_responses.py`)

**Perubahan:**
1. Ganti hardcode `api_style="chat_completions"` menjadi `api_style=s.LLM_API_STYLE`
   pada fixture `client()` dan pada `test_key_salah_lapor_auth`, sehingga saat `.env` = `responses`
   test menguji jalur yang benar-benar dipakai.
2. Definisi `TOOLS` di test memakai format **nested** (`{"type":"function","function":{...}}`).
   Client menerima nested dan mengonversi untuk gaya responses? **Tidak** — untuk gaya
   `responses`, client menolak tool nested (lihat guard di `client.py` yang me-`raise ValueError`
   bila ada key `"function"`). Karena itu:
   - Jangan berikan tool nested langsung untuk gaya responses. Gunakan
     `registry.to_tools(api_style=s.LLM_API_STYLE, ...)` **atau**
     `LLMClient.build_function_tool(...)` untuk menghasilkan format yang benar per gaya,
     lalu kirim.
   - Alternatif paling bersih: bangun daftar tool dari satu spec netral, lalu serialisasi via
     helper sesuai `s.LLM_API_STYLE`.
3. Pertahankan `pytest.mark.live` agar bisa di-skip di CI saat kunci tidak ada. Jangan
   menambahkan mock/respx (aturan §1.6).

**DoD (jalankan hanya bila `LLM_API_KEY` asli tersedia):**
- Gaya `responses` (gpt-4o-mini): satu panggilan sukses ter-parse (`content` terisi).
- Round-trip tool call: model minta tool → hasil diumpankan balik (`function_call_output`) →
  jawaban final memuat nilai yang diberikan.
- Kunci salah → `LLMError` dengan `report.stage == "auth"`.
- Test tidak crash saat kunci absen (ter-skip rapi).

---

### TASK 5 (opsional, prioritas rendah) — Reachability check OpenAI di `/health/deps`

**Tujuan:** salah konfigurasi (base URL/model/kunci) ketahuan sebelum run pertama, tanpa boros
token.

**File:** `app/api/routes/health.py`

**Perubahan:** tambah pemeriksaan ringan opsional — mis. `GET {LLM_BASE_URL}/models` dengan
timeout pendek (endpoint ini tidak menghabiskan token generasi). Jika 200 → `llm="ok"` dengan
`details["llm"]="reachable (gpt-4o-mini)"`; jika 401 → `llm="error", details=auth`; jika gagal
jaringan → `llm="error"` + pesan. Jangan gagalkan startup; ini hanya endpoint diagnostik.
Bila tak ingin menambah biaya/latensi, **lewati task ini** dan biarkan cek "configured" apa adanya.

**DoD:** `GET /health/deps` dengan kunci valid → `llm: "ok"`; dengan kunci salah → `llm: "error"`
dan pesan menyebut stage auth. Tidak ada perubahan yang menggagalkan startup.

---

### TASK 6 — Verifikasi live end-to-end (gpt-4o-mini)

**Tujuan:** membuktikan seluruh alur agen berjalan nyata dengan model OpenAI baru.

**Prasyarat:** `LLM_API_KEY` asli terisi di `.env` (sudah), DB Postgres jalan (`docker compose up -d`
+ `make migrate`), `VEROSIGHT_API_KEY` asli untuk skenario tool.

**Langkah:**
1. `python scripts/smoke_toolcall.py` (bila skrip ini menguji satu panggilan tool) — pastikan
   memakai `LLM_API_STYLE` dari settings, bukan gaya hardcode.
2. `python scripts/simulate.py ask "pertanyaan umum tanpa tool"` → **1 iterasi**, `status=success`,
   `answer` tidak kosong (menguji jalur teks + self-check JSON mode di Responses API).
3. `python scripts/simulate.py ask "apa yang sedang tren minggu ini?"` → memicu tool Verosight,
   hasil diumpankan balik, jawaban final. Verifikasi rantai `previous_response_id` tercatat di
   trace (`store=true`).
4. Periksa trace: `GET /v1/runs/{run_id}/trace?format=text` menampilkan `llm_request`/`llm_response`
   dengan `model=gpt-4o-mini` dan `response_id` terisi (bukti Responses API + store aktif).
5. Uji kegagalan eksplisit: sementara set `LLM_API_KEY` salah → run `status=failed` dengan
   `ErrorReport(component="llm", stage="auth")` di trace & response. Kembalikan kunci setelahnya.

**DoD:**
- Ketiga skenario menghasilkan `answer` non-kosong dan status yang diharapkan.
- Self-check finalize mengembalikan JSON valid (JSON mode Responses API berfungsi dengan
  gpt-4o-mini — instruksi sudah memuat kata "JSON").
- `max_output_tokens`/`temperature` dari `.env` benar-benar terkirim (cek payload di trace/log,
  redaksi rahasia tetap aktif).
- Tidak ada HTTP 400 akibat token/format; tidak ada `reasoning.effort` terkirim.

---

## 3. Checklist Verifikasi Akhir

- [ ] `LLM_MAX_OUTPUT_TOKENS ≤ 16384` di `.env` (& contoh aman di `.env.example`).
- [ ] `LLM_TEMPERATURE` & `LLM_MAX_OUTPUT_TOKENS` benar-benar terkirim di payload (bukan konfigurasi mati).
- [ ] Guard: `temperature` tidak dikirim untuk model reasoning; `reasoning.effort` tidak dikirim untuk gpt-4o-mini.
- [ ] Loop tetap style-agnostic — nol percabangan gaya di luar `app/llm/`.
- [ ] Test live mengikuti `settings.LLM_API_STYLE`; jalur `responses` teruji (panggilan, round-trip tool, auth error).
- [ ] Komentar `.env` mencerminkan OpenAI Responses API.
- [ ] `simulate ask` menghasilkan trace pohon + jawaban final dengan `model=gpt-4o-mini`, `response_id` terisi.
- [ ] `store=true` + `previous_response_id` terpakai lintas iterasi (terlihat di trace).
- [ ] Kunci salah → `ErrorReport(component="llm", stage="auth")`, bukan crash/mock.
- [ ] Regresi: gaya `chat_completions` tetap berfungsi bila `LLM_API_STYLE` dikembalikan.
- [ ] `.env` tidak pernah di-commit; tidak ada kunci asli masuk git.

---

## 4. Catatan & Batasan

- **Jangan** merombak abstraksi dua-gaya. Branch ini sengaja mendukung `responses` **dan**
  `chat_completions`; perubahan `.env` hanya memilih `responses`. Semua task menjaga agar
  peralihan kembali ke gateway lain tetap mungkin lewat konfigurasi.
- **Hemat token/kredit:** kembangkan & uji sebagian besar logika sebagai test murni (tanpa I/O).
  Jalankan test `live` hanya saat perlu. Verosight tetap `VEROSIGHT_MODE=rest` di `.env` saat ini.
- **Transparansi error (§1.6):** setiap kegagalan LLM harus muncul sebagai `ErrorReport` dengan
  `component` + `stage`, bukan stack trace mentah atau fallback data palsu.
- **Rahasia:** `api_key`/`authorization`/`token` wajib tetap teredaksi di log & trace.
