# Rencana Perubahan Cara Kerja — Metadata Resmi Verosight → OpenAI Responses API

> **Dokumen perencanaan implementasi. Bukan implementasi.**
> Tidak ada perubahan kode, commit, atau penghapusan file yang dilakukan oleh dokumen ini.
> Semua nama file, fungsi, dan baris yang dirujuk berasal dari audit codebase pada branch
> `retrofit/chat-completions-adapter` (tanggal 2026-08-03).

---

## 1. Ringkasan Perubahan

Saat ini project mendefinisikan metadata Tool Verosight **dua kali**: (a) secara manual sebagai
tool REST lokal (`app/tools/verosight/*.py`, memakai `@register_tool` + model pydantic), dan
(b) secara otomatis dari server **MCP Verosight** (`app/mcp/adapter.py`, membaca `list_tools()`
resmi). Selain itu, hasil Tool/MCP **dikecilkan di sisi aplikasi** (truncate 16.000 karakter,
ringkasan post maks 5, pemangkasan `sample_*`) sebelum diteruskan ke model.

Perubahan yang direncanakan:

1. **Metadata Tool & MCP hanya bersumber dari metadata resmi Verosight** (via MCP `list_tools()`
   dan/atau endpoint manifest resmi Verosight bila tersedia). Hentikan duplikasi metadata manual.
2. **Metadata resmi tersebut diteruskan apa adanya ke OpenAI Responses API** sebagai definisi tool
   (format FLAT), lewat jalur `registry.to_responses_tools()` yang sudah ada.
3. **Seluruh data hasil Tool/MCP diteruskan tanpa pembatasan dari sisi aplikasi** — tanpa
   truncate, tanpa limit jumlah, tanpa ringkasan otomatis. Pembatasan hanya boleh datang dari
   Tool/API sumber (mis. `limit`/`cursor` milik Verosight).

Tujuan akhirnya: satu sumber kebenaran metadata (Verosight), integrasi bersih ke OpenAI Responses
API, dan pass-through data yang utuh.

---

## 2. Latar Belakang

- Project (`agentic-loop`) adalah agen riset berbasis loop: menerima pertanyaan, memanggil LLM
  dengan daftar tool, mengeksekusi tool (lokal/Verosight REST/MCP), lalu menyusun jawaban akhir.
- LLM diakses lewat **OpenAI Responses API** (`LLM_API_STYLE=responses`,
  `LLM_MODEL=gpt-4o-mini`, `LLM_BASE_URL=https://api.openai.com/v1`). Client juga mendukung gaya
  `chat_completions` (retrofit), tetapi gaya aktif saat ini adalah `responses`.
- Verosight menyediakan data sosial melalui **dua kanal**: REST API (`https://api.verosight.com`)
  dan **server MCP** (SSE, `https://api.verosight.com/v1/mcp/sse`). Server MCP mengekspos ~20 tool
  lengkap dengan `name`, `description`, dan `inputSchema` — inilah **metadata resmi** yang dimaksud.
- `VEROSIGHT_MODE` (`rest` | `mcp` | `both`) menentukan sumber tool yang aktif. Saat ini `.env`
  memakai `rest`, sehingga jalur metadata manual yang aktif.

---

## 3. Tujuan

**Tujuan fungsional**

- T1. Definisi tool yang dikirim ke OpenAI Responses API berasal dari metadata resmi Verosight,
  bukan dari deklarasi manual di dalam project.
- T2. Menambah/menghapus tool Verosight tidak lagi memerlukan penulisan ulang metadata di project;
  cukup mengikuti apa yang diekspos Verosight.
- T3. Seluruh isi data hasil Tool/MCP diteruskan utuh ke pemanggil/model (tanpa reduksi aplikasi).

**Tujuan non-fungsional**

- T4. Arsitektur tetap **style-agnostic**: perbedaan gaya API hanya diserap di `app/llm/`.
- T5. Transparansi error dipertahankan (`ErrorReport` dengan `component` + `stage`, aturan §1.6
  `docs/PROMPT_PLAN.md`). Tanpa mock, tanpa fallback data palsu.
- T6. Perubahan dapat didemonstrasikan penuh secara lokal (API + Postman).

---

## 4. Kondisi Arsitektur Saat Ini

### 4.1 Registrasi & metadata tool

- **Registry** (`app/tools/registry.py`): `ToolRegistry` menyimpan `BaseTool` per nama.
  `@register_tool` (dekorator) membungkus fungsi Python jadi `FunctionTool` dan menurunkan
  JSON Schema dari signature. Auto-discovery (`discover()`) meng-import semua modul di `app/tools/`.
- **ToolSpec** (`app/tools/base.py`): `name, description, parameters (JSON Schema), source
  ("local"|"mcp"|"verosight"), cost_credits, timeout_s, tags, available`.
- **Metadata manual Verosight REST** (`app/tools/verosight/search.py`, `analytics.py`, dst.):
  setiap tool mendeklarasikan `name`, `description`, `cost_credits`, dan `parameters` dari model
  pydantic (mis. `VerosightSearchPostsInput`) secara **manual** → **inilah duplikasi yang ingin
  dihapus**.

### 4.2 Metadata MCP (sudah "resmi")

- `app/mcp/client.py` (`MCPClient`): koneksi SSE/stdio ke server MCP, `list_tools()`, `call_tool()`,
  reconnect + backoff.
- `app/mcp/adapter.py` (`mcp_tool_to_spec`): mengubah `mcp.types.Tool` resmi
  (`name`, `description`, `inputSchema`) → `ToolSpec(source="mcp")`, dengan prefiks nama
  `"{alias}__{tool}"` (mis. `verosight__get_sentiment`). **Ini sudah memakai metadata resmi.**
- `app/mcp/manager.py` (`MCPManager`): baca konfigurasi server, `connect_all()` mendaftarkan tool
  MCP ke registry saat startup, cache 15 menit, dan degradasi anggun bila server mati
  (tool ditandai `available=False`).

### 4.3 Pemilihan sumber tool

- `ToolRegistry._is_tool_active()` memakai `VEROSIGHT_MODE`:
  - `rest` → tool `source="verosight"` aktif, tool `source="mcp"` dinonaktifkan.
  - `mcp` → tool `source="mcp"` aktif, tool `source="verosight"` dinonaktifkan.
  - `both` → keduanya aktif, deskripsi diberi prefiks `[REST]`/`[MCP]`.

### 4.4 Integrasi ke OpenAI Responses API

- `registry.to_responses_tools()` → daftar tool **FLAT**:
  `{"type":"function","name","description","parameters"}` (sesuai Responses API).
- `app/agent/loop.py` memanggil `registry.to_tools(api_style=settings.LLM_API_STYLE, ...)` lalu
  `llm.respond(input=..., instructions=..., tools=..., previous_response_id=...)`.
- `app/llm/client.py` (`LLMClient`): POST `{LLM_BASE_URL}/responses`, parsing
  `response.output[]` (`type="message"` → teks; `type="function_call"` → tool call),
  state antar-iterasi via `previous_response_id` + `store=true`.

### 4.5 Titik reduksi data di sisi aplikasi (relevan untuk Requirement #4)

| # | Lokasi | Perilaku reduksi saat ini |
|---|---|---|
| R1 | `app/tools/executor.py` → `_truncate_result()` (dipanggil baris 274) | Memotong data > **16.000 karakter**; untuk list/dict memangkas item dan menambah penanda `[dipotong, N item dari M]`; untuk string dipotong |
| R2 | `app/tools/verosight/helpers.py` → `summarize_search_posts_response()` | Membatasi post ke **max_posts=5**, hanya menyimpan sebagian field; (blok pemotongan `content` di-comment, namun cabang `continue` membuat `content` string terlewati) |
| R3 | `app/tools/verosight/helpers.py` → `summarize_verosight_response()` | Membuang `media_urls` kosong; memotong list `sample_*` ke **maks 5** |
| R4 | `app/tools/verosight/*.py` | Tool REST mengembalikan `data=summarized`, menyimpan `raw_data=full_envelope` (data penuh tidak diteruskan ke model) |
| R5 | `app/agent/state.py` → `get_context_block(max_tokens=2000)` & `summary=raw_str[:200]` | Blok memori untuk prompt dibatasi token & ringkasan dipotong 200 char |
| R6 | `app/agent/loop.py` (≈ baris 333–338) | Output tool yang diumpankan balik ke model diambil dari `res.data` (sudah tereduksi) |

> Catatan: R1 juga aktif pada hasil MCP, karena `executor.execute()` memanggil `_truncate_result()`
> untuk **semua** tool, termasuk `source="mcp"`.

---

## 5. Permasalahan yang Ingin Diselesaikan

- **P1 — Duplikasi metadata.** Metadata tool Verosight ditulis manual (REST) padahal metadata
  resmi sudah tersedia dari MCP `list_tools()`. Dua sumber → rawan tidak sinkron, boros
  pemeliharaan, dan bertentangan dengan permintaan "tanpa duplikasi manual".
- **P2 — Sumber kebenaran tidak jelas.** Saat `VEROSIGHT_MODE=rest`, metadata yang dipakai adalah
  versi manual; saat `mcp`, versi resmi. Perilaku berbeda tergantung konfigurasi.
- **P3 — Reduksi data oleh aplikasi.** Hasil Tool/MCP dipangkas sebelum sampai ke model/pemanggil
  (R1–R6), melanggar kebutuhan "teruskan seluruh data".
- **P4 — Metadata resmi belum tentu mulus untuk Responses API.** `inputSchema` MCP kadang berisi
  konstruk JSON Schema yang perlu divalidasi agar diterima Responses API (mis. `$defs`,
  `additionalProperties`, tipe union). Perlu langkah normalisasi non-destruktif.

---

## 6. Arsitektur yang Diusulkan

**Prinsip:** *Verosight adalah satu-satunya sumber metadata tool. Aplikasi hanya menjadi jembatan
(pass-through) antara metadata/hasil Verosight dan OpenAI Responses API.*

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         SUMBER METADATA RESMI (Verosight)                    │
│   MCP list_tools()  (name, description, inputSchema)   [PRIMER, sudah ada]   │
│   + (opsional) endpoint manifest/OpenAPI REST Verosight [bila tersedia]      │
└───────────────┬────────────────────────────────────────────────────────────┘
                │  ingest saat startup (MCPManager.connect_all)
                ▼
      ToolSpec(source="mcp")  ── disimpan di ToolRegistry (tanpa metadata manual)
                │
                │  registry.to_responses_tools()  → FLAT tool defs
                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        OpenAI Responses API (gpt-4o-mini)                     │
│   POST /responses  { input, instructions, tools=[FLAT metadata resmi], ... } │
│   ← output[]: message (teks) | function_call (name, call_id, arguments)      │
└───────────────┬────────────────────────────────────────────────────────────┘
                │  function_call → executor
                ▼
   Executor → MCPTool.run() → MCP call_tool()  → hasil UTUH (tanpa reduksi app)
                │
                ▼
   function_call_output (output = seluruh isi hasil)  → iterasi berikutnya
```

**Keputusan kunci:**

- **Mode default `VEROSIGHT_MODE=mcp`.** Jadikan MCP sumber metadata & eksekusi utama, karena
  metadata resminya sudah lengkap dan otomatis. Jalur REST manual tidak lagi menjadi sumber
  metadata untuk model.
- **Metadata tidak ditulis ulang.** `ToolSpec` untuk Verosight diturunkan dari `list_tools()`
  (dan/atau manifest resmi), lalu diserialisasi langsung ke format Responses API.
- **Pass-through data penuh.** Titik R1–R6 dinonaktifkan/di-bypass untuk hasil Tool/MCP, sehingga
  seluruh isi diteruskan. Data penuh tetap disimpan ke `tool_invocations.result` untuk audit.

**Yang dipertahankan (tidak diubah):**

- Kerangka loop agen, tracing, DB, kebijakan stop, dan abstraksi `LLMClient` (Responses API).
- Mekanisme `registry.to_responses_tools()` (sudah menghasilkan format FLAT yang benar).
- Transparansi error (`ErrorReport`) dan aturan tanpa-mock.
- Jalur MCP `adapter.py`/`manager.py`/`client.py` sebagai pembawa metadata resmi (disempurnakan,
  bukan diganti).

---

## 7. Alur Integrasi Verosight → OpenAI Responses API

1. **Startup / ingest metadata.** `MCPManager.connect_all()` connect ke server MCP Verosight,
   memanggil `list_tools()` (metadata resmi), mengubah tiap `Tool` → `ToolSpec(source="mcp")`
   via `mcp_tool_to_spec()`, dan mendaftarkannya ke `ToolRegistry`. (Opsional: bila Verosight
   menyediakan endpoint manifest REST resmi, tambahkan ingester paralel — lihat §18 Asumsi A2.)
2. **Normalisasi schema (non-destruktif).** Sebelum dikirim ke Responses API, `inputSchema` resmi
   divalidasi/disesuaikan agar diterima (mis. memastikan root `type:"object"`, menangani `$defs`).
   Ini **tidak mengubah makna** metadata, hanya kompatibilitas format.
3. **Serialisasi ke Responses API.** `registry.to_responses_tools()` menghasilkan daftar FLAT
   `{"type":"function","name","description","parameters"}` dari `ToolSpec` resmi.
4. **Request.** `loop.py` → `llm.respond(input, instructions, tools=<FLAT resmi>,
   previous_response_id=...)` → `LLMClient` POST `{LLM_BASE_URL}/responses`.
5. **Response.** `LLMClient` mem-parse `response.output[]`:
   - `type="message"` → teks jawaban (`output_text`).
   - `type="function_call"` → `FunctionCall(call_id, name, arguments_dict)`.
6. **Eksekusi tool** (bila ada `function_call`) — lihat §8.
7. **Umpan balik hasil.** Hasil tool **utuh** dibungkus sebagai
   `function_call_output {call_id, output}` dan dikirim di iterasi berikutnya (dengan
   `previous_response_id`).
8. **Selesai.** Saat model mengembalikan teks tanpa `function_call` dan lolos self-check, jawaban
   final dikembalikan ke aplikasi via `POST /v1/agent/query`.

---

## 8. Alur Pemanggilan Tool

1. Model mengembalikan `function_call(name, call_id, arguments)` (name bisa berprefiks
   `verosight__...` untuk tool MCP).
2. `loop.py` melakukan dedup cache (`ExecutionMemory.is_cached`) untuk mencegah pemanggilan
   identik berulang, lalu memanggil `ToolExecutor.execute_parallel()` untuk tool yang belum dicache.
3. `ToolExecutor.execute()`:
   - `registry.get(name)` → tool aktif (dipilih oleh `VEROSIGHT_MODE`).
   - Validasi `arguments` terhadap `spec.parameters` (JSON Schema resmi). Argumen invalid →
     `ToolResult(status="error")` yang informatif (bukan exception).
   - `tool.run(**args)` dengan timeout + retry transient.
   - Normalisasi status kosong (`empty`).
   - **Perubahan:** **lewati** `_truncate_result()` untuk hasil Tool/MCP (pass-through penuh).
   - Catat ke `tool_invocations` (data penuh, dengan redaksi rahasia).
4. `loop.py` membungkus hasil **utuh** sebagai `function_call_output` dan menuliskannya ke
   `ExecutionMemory` + DB. Data yang diumpankan balik ke model = isi lengkap hasil tool.

---

## 9. Alur Pemanggilan MCP

1. Tool MCP terdaftar sebagai `MCPTool` (`app/mcp/adapter.py`) dengan metadata resmi dari
   `list_tools()`.
2. Saat model memanggil `verosight__<tool>`, `MCPTool.run(**kwargs)`:
   - Cek `spec.available` (server online?). Jika offline → `ToolResult(status="error")` jelas.
   - `MCPClient.call_tool(original_name, arguments)` (reconnect + backoff otomatis).
   - `mcp_result_to_tool_result()` mengubah `CallToolResult` (content blocks / `structured_content`)
     → `ToolResult`. **Perubahan:** pastikan seluruh `content`/`structured_content` dipertahankan
     apa adanya (tidak digabung-parsial atau dipangkas), dan biarkan `_truncate_result` di-bypass
     di executor.
3. Hasil MCP **utuh** mengalir sama seperti §8 langkah 4.

> Perhatikan: metadata & hasil MCP sudah "resmi". Fokus perubahan MCP adalah **memastikan
> pass-through penuh** dan menjadikannya sumber metadata utama, bukan menulis ulang apa pun.

---

## 10. Komponen yang Perlu Diubah

| Komponen | Perubahan yang direncanakan | Alasan |
|---|---|---|
| Konfigurasi (`.env`, `app/config.py`) | Default `VEROSIGHT_MODE=mcp`; tambah flag eksplisit non-reduksi, mis. `TOOL_RESULT_PASSTHROUGH=true` (default true) untuk mematikan truncation/summarization sisi app | Menjadikan metadata resmi sumber utama; mengontrol pass-through |
| `app/tools/executor.py` | Bypass `_truncate_result()` saat pass-through aktif; tetap simpan data penuh ke `tool_invocations` | Requirement #4 (tanpa truncate/limit) |
| `app/tools/verosight/helpers.py` | Tidak dipakai untuk mereduksi hasil; `summarize_*` di-bypass (atau hanya untuk pembersihan non-lossy bila diperlukan Verosight) | Requirement #4 (tanpa ringkasan/limit) |
| `app/tools/verosight/*.py` (REST) | Berhenti menjadi sumber metadata untuk model saat mode `mcp`; kembalikan `data` penuh (bukan `summarized`) bila REST tetap dipakai | Hilangkan duplikasi metadata & reduksi |
| `app/mcp/adapter.py` | Tambah normalisasi non-destruktif `inputSchema` → skema valid Responses API; pastikan `mcp_result_to_tool_result` mempertahankan seluruh konten | Kompatibilitas & pass-through |
| `app/mcp/manager.py` | (Opsional) tambah ingester manifest REST resmi bila Verosight menyediakannya | Sumber metadata resmi tambahan |
| `app/agent/loop.py` | Pastikan `function_call_output` membawa isi hasil penuh; hindari mengecilkan `output_val` | Pass-through ke model |
| `app/agent/state.py` | Naikkan/longgarkan batas `get_context_block`/`summary` **atau** pisahkan "data untuk model" (penuh) dari "ringkasan untuk trace" | Requirement #4, sambil menjaga konteks tetap terkelola |
| `app/config.py` (LLM) | Selaraskan `LLM_MAX_OUTPUT_TOKENS` dengan batas gpt-4o-mini (≤16.384) — lihat `docs/Plan-perubahan-model.md` | Konsistensi Responses API |

> **Catatan penting konteks LLM:** meneruskan seluruh data akan **memperbesar token input**.
> Ini keputusan yang diminta secara eksplisit (Requirement #4). Bagian §16 Risiko membahas
> mitigasi yang **tidak** melanggar aturan (mis. paginasi milik Verosight, bukan pemotongan app).

---

## 11. Daftar File yang Kemungkinan Terdampak

- `.env`, `.env.example` — default `VEROSIGHT_MODE=mcp`, flag pass-through, penyelarasan token.
- `app/config.py` — flag/Settings baru (mis. `TOOL_RESULT_PASSTHROUGH`), default mode.
- `app/tools/executor.py` — bypass truncation untuk hasil Tool/MCP.
- `app/tools/verosight/helpers.py` — nonaktifkan reduksi (atau jadikan opsional non-lossy).
- `app/tools/verosight/search.py`, `analytics.py`, (`profiles.py` bila ada) — pass-through data;
  peran metadata dikurangi saat mode `mcp`.
- `app/mcp/adapter.py` — normalisasi schema resmi + pass-through hasil.
- `app/mcp/manager.py` — (opsional) ingester manifest resmi; pastikan registrasi tool MCP.
- `app/tools/registry.py` — pastikan `to_responses_tools()` memakai schema resmi apa adanya.
- `app/agent/loop.py` — umpan balik hasil penuh ke model.
- `app/agent/state.py` — pemisahan data-penuh vs ringkasan-trace / pelonggaran batas.
- `tests/` — test pass-through (tanpa mock), test ingest metadata resmi, test round-trip Responses.
- `docs/` — dokumen ini; rujuk `docs/Plan-perubahan-model.md` dan `docs/PROMPT_PLAN.md`.
- `scripts/dump_mcp_tools.py`, `scripts/simulate.py` — verifikasi metadata & demo lokal.

> Daftar ini adalah *kemungkinan terdampak* berdasarkan audit; developer wajib mengonfirmasi saat
> implementasi. Tidak semua file pasti berubah.

---

## 12. Tahapan Implementasi

**Tahap 0 — Konfirmasi sumber metadata resmi.**
Verifikasi bahwa MCP `list_tools()` Verosight mengembalikan `name`/`description`/`inputSchema`
lengkap (jalankan `python scripts/dump_mcp_tools.py`). Konfirmasi apakah Verosight juga
menyediakan endpoint manifest/OpenAPI REST resmi (Asumsi A2). Dokumentasikan temuan.

**Tahap 1 — Jadikan metadata MCP sumber utama.**
Set `VEROSIGHT_MODE=mcp` sebagai default. Pastikan `registry.to_responses_tools()` menghasilkan
definisi FLAT dari `ToolSpec(source="mcp")` yang berasal dari `list_tools()`. Tidak ada metadata
manual yang dikirim ke model.

**Tahap 2 — Normalisasi schema resmi (non-destruktif).**
Tambahkan langkah validasi/penyesuaian `inputSchema` di `adapter.py` agar diterima Responses API
(root `object`, resolusi `$defs`, dsb.) tanpa mengubah makna parameter.

**Tahap 3 — Pass-through hasil penuh.**
Bypass `_truncate_result()` dan `summarize_*` untuk hasil Tool/MCP (kendali via
`TOOL_RESULT_PASSTHROUGH`). Pastikan `function_call_output` di `loop.py` membawa isi utuh.

**Tahap 4 — Pisahkan data-model vs ringkasan-trace.**
Di `state.py`, simpan nilai penuh untuk umpan balik model; ringkasan singkat tetap boleh dipakai
**hanya** untuk tampilan trace (bukan untuk data yang dikirim ke model).

**Tahap 5 — Uji & demo lokal.**
Test pass-through, ingest metadata, round-trip Responses API; lalu demo via API + Postman (§15).

**Tahap 6 — Bersih-bersih.**
Tandai jalur metadata manual REST sebagai deprecated/dinonaktifkan untuk model, atau
pertahankan REST **hanya** sebagai kanal eksekusi cadangan (mode `both`) tanpa menjadi sumber
metadata.

---

## 13. Strategi Migrasi

- **Bertahap & dapat dibalik lewat konfigurasi.** Peralihan dikendalikan `VEROSIGHT_MODE` dan
  `TOOL_RESULT_PASSTHROUGH`. Bila terjadi masalah, kembali ke perilaku lama tanpa rollback kode.
- **Tidak menghapus kode REST dulu.** Jalur REST manual dibiarkan ada (dinonaktifkan sebagai
  sumber metadata) hingga jalur MCP terbukti stabil. Ini menghindari kehilangan kemampuan
  fallback eksekusi saat MCP server bermasalah.
- **Kompatibilitas kontrak internal.** `ToolSpec`/`ToolResult`/`TraceEvent` tidak diubah
  strukturnya, sehingga loop/tracer/DB tetap kompatibel.
- **Urutan aman:** aktifkan sumber metadata resmi (Tahap 1–2) lebih dulu, verifikasi, baru
  aktifkan pass-through penuh (Tahap 3–4). Dua perubahan besar tidak digulirkan sekaligus.

---

## 14. Strategi Pengujian

Mengikuti aturan §1.6 (`docs/PROMPT_PLAN.md`): **tanpa mock/HTTP-mock**; logika murni diuji
langsung, jalur nyata diuji sebagai test `live` (skip bila key tak ada).

**Test logika murni (tanpa jaringan):**

- Serialisasi: `ToolSpec` (dari `inputSchema` contoh yang dibangun dalam test) →
  `to_responses_tools()` menghasilkan format FLAT valid.
- Normalisasi schema resmi: input `inputSchema` dengan `$defs`/union → skema yang diterima
  Responses API, **tanpa** kehilangan properti.
- Pass-through: `executor` dengan `TOOL_RESULT_PASSTHROUGH=true` **tidak** memangkas
  list/string besar; penanda `[dipotong ...]` tidak muncul; `tool_invocations.result` = data penuh.
- `mcp_result_to_tool_result()`: seluruh `content`/`structured_content` dipertahankan.

**Test live (marker `live`):**

- Ingest metadata: connect MCP Verosight → `list_tools()` mengembalikan tool; `GET /v1/tools`
  menampilkan tool ber-`source="mcp"` dengan schema resmi.
- Round-trip Responses API (gpt-4o-mini): model memanggil satu tool MCP → hasil **utuh**
  diumpankan balik → jawaban final. Verifikasi tidak ada pemotongan sisi app.
- Error transparan: key salah → `ErrorReport(component="llm"/"verosight", stage=...)`; server MCP
  mati → tool `available=false`, startup tetap sukses.

**Verifikasi non-fungsional:**

- Rahasia (`api_key`/`authorization`) tetap teredaksi di log/trace/response.
- Regresi: mode `rest` dan gaya `chat_completions` tetap berfungsi bila dikonfigurasi.

---

## 15. Demo Lokal menggunakan API dan Postman

### 15.1 Menjalankan project

```bash
# 1. Siapkan .env (VEROSIGHT_MODE=mcp, LLM_API_STYLE=responses, LLM_MODEL=gpt-4o-mini,
#    LLM_API_KEY & VEROSIGHT_API_KEY terisi asli)
# 2. Database
docker compose up -d          # Postgres rag_db
make migrate                  # migrasi skema
# 3. Jalankan API lokal
make dev                      # uvicorn app.main:app --reload (default http://localhost:8000)
```

### 15.2 Verifikasi metadata resmi termuat

```bash
python scripts/dump_mcp_tools.py     # cetak hasil registry.to_responses_tools() (metadata resmi)
curl http://localhost:8000/health/deps
curl http://localhost:8000/v1/tools  # harus menampilkan tool source="mcp" (verosight__...)
```

### 15.3 Endpoint yang tersedia

| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/health` | Cek dasar `{"status":"ok"}` |
| GET | `/health/deps` | Status DB, LLM, MCP |
| GET | `/v1/tools` | Daftar tool aktif (name, source, description, cost, available) |
| POST | `/v1/agent/query` | Jalankan agen; balas **hanya** jawaban final + metadata run |
| POST | `/v1/agent/query/stream` | SSE token jawaban akhir saja |
| GET | `/v1/runs/{run_id}` | Metadata run + jawaban |
| GET | `/v1/runs/{run_id}/trace` | Seluruh TraceEvent (proses berpikir) |
| GET | `/v1/runs/{run_id}/trace?format=text` | Trace pohon terbaca |
| GET | `/v1/runs/{run_id}/tools` | Daftar `tool_invocations` (berisi data hasil penuh) |
| GET | `/v1/conversations`, `/v1/conversations/{id}` | Riwayat percakapan |

### 15.4 Contoh request Postman — `POST /v1/agent/query`

- **Method/URL:** `POST http://localhost:8000/v1/agent/query`
- **Headers:** `Content-Type: application/json`
- **Body (raw JSON):**

```json
{
  "query": "Apa yang sedang tren di Verosight minggu ini?",
  "conversation_id": null,
  "options": { "max_iterations": 8, "credits_budget": 100 }
}
```

### 15.5 Contoh response yang diharapkan (`/v1/agent/query`)

```json
{
  "answer": "Tiga topik teratas minggu ini adalah ... (jawaban ringkas ke pengguna)",
  "run_id": "987e6543-e89b-12d3-a456-426614174999",
  "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "success",
  "iterations": 2,
  "latency_ms": 1450
}
```

> Response ke pengguna **tidak** berisi proses berpikir (keputusan desain yang dikunci).

### 15.6 Membuktikan pass-through data penuh

Panggil `GET /v1/runs/{run_id}/tools` (atau `/trace`) setelah menjalankan query yang memicu tool.
Verifikasi bahwa `tool_invocations.result` memuat **seluruh** isi hasil (mis. seluruh daftar post /
seluruh isi teks), **tanpa** penanda `[dipotong ...]` dan tanpa pemotongan `sample_*`/`max_posts`.
Contoh request Postman: `GET http://localhost:8000/v1/runs/{run_id}/tools`.

---

## 16. Risiko

- **RS1 — Ledakan token / biaya.** Meneruskan seluruh data memperbesar input token OpenAI
  (gpt-4o-mini) dan biaya. Ini konsekuensi Requirement #4. **Mitigasi yang diizinkan:** gunakan
  paginasi/`limit` milik **Verosight** (bukan pemotongan aplikasi); atur `credits_budget` &
  `max_iterations`; pertimbangkan model berkonteks lebih besar bila perlu.
- **RS2 — Melebihi context window model.** Payload sangat besar bisa melampaui batas input model
  → error dari OpenAI. **Mitigasi:** tangkap error `response_parse`/400 secara transparan
  (`ErrorReport`), sarankan pemakai mempersempit query lewat parameter Verosight resmi.
- **RS3 — Schema resmi tak langsung kompatibel** dengan Responses API (P4). **Mitigasi:**
  normalisasi non-destruktif (Tahap 2) + test schema.
- **RS4 — Ketergantungan pada ketersediaan MCP server.** Bila MCP mati, metadata resmi tak
  termuat. **Mitigasi:** degradasi anggun yang sudah ada (`available=false`) + opsi mode `both`
  agar REST menjadi kanal eksekusi cadangan (bukan sumber metadata).
- **RS5 — Kehilangan pembersihan yang dulu berguna** (mis. HTML mentah pada `content`).
  **Mitigasi:** bila diperlukan, izinkan pembersihan **non-lossy** yang tidak mengurangi jumlah
  item/isi bermakna; secara default, teruskan apa adanya.

---

## 17. Asumsi

- **A1.** MCP `list_tools()` Verosight adalah **metadata resmi** yang sah untuk dipakai sebagai
  definisi tool Responses API. *Alasan:* kode `app/mcp/` sudah mengonsumsinya dan `PROMPT_PLAN.md`
  PROMPT 6 mendaftarkan ~20 tool MCP resmi. Perlu diverifikasi via `scripts/dump_mcp_tools.py`.
- **A2.** Verosight **mungkin** juga menyediakan endpoint manifest/OpenAPI REST resmi. *Alasan:*
  belum ada bukti endpoint tersebut di codebase; karena itu jalur REST manifest bersifat
  **opsional** dan hanya diaktifkan bila dikonfirmasi ada. Jika tidak ada, MCP `list_tools()`
  menjadi satu-satunya sumber resmi — tetap memenuhi tujuan tanpa metadata manual.
- **A3.** "Seluruh data diteruskan" berarti tanpa reduksi **oleh aplikasi**; pembatasan yang
  berasal dari parameter Tool/API Verosight (mis. `limit`, `cursor`) tetap sah. *Alasan:*
  eksplisit pada Requirement #4.
- **A4.** Gaya API aktif adalah `responses` (OpenAI). *Alasan:* `.env` saat ini. Dukungan
  `chat_completions` dipertahankan sebagai kemampuan konfigurasi, bukan fokus perubahan ini.
- **A5.** Konsumen API sanggup menerima payload hasil yang besar. *Alasan:* konsekuensi langsung
  Requirement #4; dibahas di §16.

---

## 18. Catatan Implementasi

- **Jaga arsitektur style-agnostic.** Seluruh perbedaan gaya API tetap di `app/llm/`. Jangan
  menambah `if style == ...` di `app/agent/` atau `app/tools/`.
- **Satu sumber metadata.** Jangan menulis ulang metadata Verosight secara manual selama metadata
  resmi (MCP `list_tools()`/manifest) dapat dipakai. Jalur REST manual, bila dipertahankan, hanya
  sebagai kanal eksekusi — bukan sumber metadata untuk model.
- **Pass-through, bukan penghapusan pengaman.** Nonaktifkan reduksi via flag konfigurasi
  (`TOOL_RESULT_PASSTHROUGH`), sehingga perilaku dapat diaudit dan dibalik. Data penuh tetap
  masuk `tool_invocations.result` untuk trace/audit.
- **Transparansi error dipertahankan.** Setiap kegagalan LLM/Verosight/MCP muncul sebagai
  `ErrorReport` dengan `component` + `stage`; tanpa fallback data palsu; rahasia diredaksi.
- **Selaraskan konfigurasi LLM.** `LLM_MAX_OUTPUT_TOKENS` harus ≤ 16.384 untuk gpt-4o-mini
  (lihat `docs/Plan-perubahan-model.md`), agar payload besar tidak memicu error konfigurasi.
- **Rujukan silang:** aturan global & kontrak internal ada di `docs/PROMPT_PLAN.md` (§1.3–§1.6),
  sinkronisasi model OpenAI ada di `docs/Plan-perubahan-model.md`.
- **Batasan tugas ini:** dokumen perencanaan saja. Implementasi, commit, dan penghapusan file
  dilakukan pada tahap terpisah oleh developer.
```
