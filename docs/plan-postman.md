# Rencana Implementasi — Postman Collection & Environment untuk `agentic-loop`

> **Dokumen perencanaan implementasi. Bukan implementasi.**
> Dokumen ini TIDAK membuat file Postman Collection (`.json`) maupun Postman Environment.
> Semua endpoint, schema, dan perilaku di bawah adalah hasil audit codebase pada branch
> `retrofit/chat-completions-adapter` (tanggal 2026-08-03). Referensi lokasi memakai
> `path:baris` agar dapat diverifikasi ulang.

---

## 1. Ringkasan

`agentic-loop` adalah aplikasi **FastAPI** (app factory di `app/main.py::create_app`). Router
tidak berada di `routers/` melainkan di **`app/api/routes/`**, dan schema Pydantic berada di
**`app/schemas/`** (bukan `schemas.py` tunggal). Aplikasi mengekspos **12 operasi endpoint** di
6 router: `agent`, `runs`, `conversations`, `tools`, `health`, `metrics`.

Temuan penting yang membentuk seluruh strategi Postman:

- **Tidak ada autentikasi inbound.** Tidak ada OAuth2, JWT, API-Key, atau session pada endpoint
  aplikasi. Kunci rahasia (`LLM_API_KEY`, `VEROSIGHT_API_KEY`) hanya dipakai **server-side**
  (outbound ke OpenAI/Verosight) dan **tidak pernah** dikirim oleh klien. → Postman **tidak**
  memerlukan header `Authorization`, dan Environment **tidak** butuh `access_token`/`api_key`/
  `bearer_token`/`session_id` (lihat §6 & §9).
- **Semua request/response berbasis JSON**, kecuali: `POST /v1/agent/query/stream`
  (`text/event-stream`), `GET /v1/runs/{run_id}/trace?format=text` (`text/plain`), dan
  `GET /metrics` (format teks Prometheus).
- **Tidak ada** `UploadFile`, `Form`, `File`, atau `WebSocket` di seluruh codebase (diverifikasi
  via grep — nihil).
- **Bentuk error konsisten** dari exception handler global:
  `{"error": {"code", "message"}, "meta": {"request_id"}}` (`app/api/exceptions.py`).
- **Rate limit** hanya pada `POST /v1/agent/query*` (HTTP 429) — `app/api/middleware.py:30`.
- **Header `X-Request-ID`** diterima (opsional) dan selalu dikembalikan di response
  (`app/api/middleware.py:59`).

---

## 2. Tujuan

- Menyediakan panduan lengkap untuk membuat **Postman Collection v2.1** yang mencakup seluruh
  12 operasi endpoint, terorganisir per fitur, dengan contoh payload dan contoh response.
- Menyediakan panduan pembuatan **Postman Environment** minimal (`baseUrl`) plus variabel dinamis
  yang benar-benar ditemukan (`run_id`, `conversation_id`).
- Menyediakan **Saved Examples** (sukses + varian error yang benar-benar mungkin terjadi).
- Menyediakan **Testing Flow** dan rencana **automation** (Test Script untuk menangkap `run_id`/
  `conversation_id`) — tanpa autentikasi karena memang tidak ada.
- Dokumen dapat dieksekusi developer lain **tanpa** perlu menganalisis ulang codebase.

---

## 3. Analisis Struktur Backend

### 3.1 Lokasi sebenarnya (berbeda dari template tugas)

| Diminta di tugas | Lokasi sebenarnya di project |
|---|---|
| `main.py` | `app/main.py` (app factory + lifespan + registrasi router/middleware) |
| `schemas.py` | `app/schemas/` — `agent.py`, `trace.py`, `errors.py`, `__init__.py` |
| `routers/` | `app/api/routes/` — `agent.py`, `runs.py`, `conversations.py`, `tools.py`, `health.py`, `metrics.py` |

### 3.2 Routing

- App factory `create_app()` (`app/main.py:93`) mendaftarkan 6 router via `_register_routes()`
  (`app/main.py:136`). Prefiks & tags:
  - `agent` → prefix `/v1/agent`, tags `["agent"]`
  - `runs` → prefix `/v1/runs`, tags `["runs"]`
  - `conversations` → prefix `/v1/conversations`, tags `["conversations"]`
  - `tools` → prefix `/v1/tools`, tags `["tools"]`
  - `health` → tanpa prefix, tags `["health"]`
  - `metrics` → tanpa prefix, tags `["observability"]`

### 3.3 Dependency Injection

- Satu-satunya dependency: `Depends(get_session)` (`app/db/session.py`) — menyuntikkan
  `AsyncSession` SQLAlchemy ke endpoint yang mengakses DB (`runs`, `conversations`, `agent`).
- `tools` dan `metrics` tidak memakai DB session. `tools` membaca `get_registry()` langsung.
- **Tidak ada** dependency autentикasi/otorisasi (`Security`, `OAuth2`, `HTTPBearer`,
  `APIKeyHeader`) — diverifikasi nihil.

### 3.4 Authentication / Session / OAuth / JWT / API Key

- **Nihil pada sisi inbound.** Tidak ditemukan skema keamanan FastAPI apa pun.
- Kunci di `app/config.py` (`LLM_API_KEY`, `VEROSIGHT_API_KEY`) adalah kredensial **outbound**
  server → provider, tidak relevan untuk header Postman.
- **Implikasi Postman:** tidak ada endpoint login/logout; auth type Collection = **No Auth**.

### 3.5 Middleware (`app/api/middleware.py`, `app/main.py:110`)

Urutan registrasi (eksekusi FastAPI kebalikannya): `logging_middleware`, `rate_limit_middleware`,
`request_id_middleware`, lalu `CORSMiddleware`.

- `request_id_middleware`: baca header `X-Request-ID` (atau generate), kembalikan di response
  header `X-Request-ID`.
- `rate_limit_middleware`: sliding window per-IP **hanya** untuk path diawali `/v1/agent/query`;
  melebihi `RATE_LIMIT_PER_MINUTE` (default 60) → **429** dengan body
  `{"error":{"code":429,"message":...},"meta":{"request_id":...}}`.
- `logging_middleware`: mencatat method/path/status/durasi (tidak mengubah response).
- CORS: `allow_origins` = `http://localhost:3000/8080`, `http://127.0.0.1:3000/8080`,
  `allow_credentials=True`, methods/headers `*`.

### 3.6 Request Model, Response Model, Schema Pydantic

- `app/schemas/agent.py`: `RunOptions`, `AgentQueryRequest`, `AgentQueryResponse`, `RunResponse`,
  `ConversationResponse`, `RunResult`.
- `app/schemas/trace.py`: `TraceEvent` (dipakai response `/trace`).
- Response model inline dalam router: `ToolInvocationResponse` (`runs.py:21`),
  `ToolInfoResponse` (`tools.py:14`), `HealthResponse` & `HealthDepsResponse` (`health.py:21,25`).
- Detail field lihat §4.

### 3.7 Custom Exception & Response Error (`app/api/exceptions.py`)

Handler global (`register_exception_handlers`, dipasang di `app/main.py:128`):

| Exception | Status | `error.code` | `error.message` |
|---|---|---|---|
| `HTTPException` (mis. 404) | status aslinya | angka status (mis. `"404"`) | `str(detail)` |
| `RequestValidationError` | **422** | `"VALIDATION_ERROR"` | `"Validation error: [...]"` |
| `Exception` (tak tertangani) | **500** | `"INTERNAL_SERVER_ERROR"` | `"An unexpected server error occurred."` |

Semua dibungkus `{"error":{"code","message"},"meta":{"request_id"}}`. **Tidak ada** 401/403 di
mana pun (konsekuensi tidak adanya auth) — lihat §8.

### 3.8 UploadFile / Form / File / WebSocket

- **Tidak digunakan.** Tidak ada endpoint multipart/form-data maupun WebSocket. Seluruh body
  bertipe JSON.

---

## 4. Daftar Endpoint yang Ditemukan

Total: **12 operasi** (2 POST, 10 GET). `Auth` = No Auth untuk semuanya. `Dependency`:
DB = `Depends(get_session)`.

### 4.1 Router `agent` (`/v1/agent`)

**[1] POST `/v1/agent/query`** — *Execute agent query synchronously* (`agent.py:18`)
- Tags: `agent`. Dependency: DB. Rate-limited (429).
- Request Body: `AgentQueryRequest`
  - `query` *(str, wajib, 1–4000 char)*
  - `conversation_id` *(UUID | null, opsional)*
  - `options` *(RunOptions, default)* — `max_iterations=8`, `credits_budget=100`,
    `timeout_s=180.0`, `parallel_tools=true`, `store=true`, `tools_allow: list|null`,
    `tools_deny: list|null`
- Header: `Content-Type: application/json`; opsional `X-Request-ID`.
- Response 200: `AgentQueryResponse` — `answer, run_id, conversation_id, status, iterations,
  latency_ms`.
- Error: 422 (validasi), 429 (rate limit), 500.

**[2] POST `/v1/agent/query/stream`** — *SSE answer streaming* (`agent.py:66`)
- Tags: `agent`. Dependency: DB. Rate-limited (429).
- Request Body: `AgentQueryRequest` (sama seperti [1]).
- Response 200: `text/event-stream` — token jawaban akhir sebagai `data: <chunk>\n\n`, diakhiri
  `data: [DONE]\n\n`. Tidak ada event thinking/tool.
- Error: 422, 429, 500.

### 4.2 Router `runs` (`/v1/runs`)

**[3] GET `/v1/runs`** — *List runs* (`runs.py:38`)
- Query: `limit` *(int, 1–100, default 20)*, `offset` *(int, ≥0, default 0)*,
  `status` *(str | null, filter opsional)*.
- Response 200: `list[RunResponse]`.
- Error: 422 (query invalid), 500.

**[4] GET `/v1/runs/{run_id}`** — *Get run metadata* (`runs.py:68`)
- Path: `run_id` *(UUID)*.
- Response 200: `RunResponse` — `id, conversation_id, user_query, status, final_answer,
  iterations, credits_used, total_tokens, latency_ms, error, started_at, finished_at`.
- Error: 404 (run tidak ada), 422 (UUID invalid), 500.

**[5] GET `/v1/runs/{run_id}/trace`** — *Get full trace* (`runs.py:94`)
- Path: `run_id` *(UUID)*. Query: `format` *(str | null; `text` → text/plain)*.
- Response 200: default `list[TraceEvent]` (JSON); bila `format=text` → `text/plain` (pohon).
  `TraceEvent`: `run_id, seq, iteration, type, summary, payload, duration_ms, created_at`.
- Error: 404, 422, 500.

**[6] GET `/v1/runs/{run_id}/tools`** — *List tool invocations* (`runs.py:136`)
- Path: `run_id` *(UUID)*.
- Response 200: `list[ToolInvocationResponse]` — `id, run_id, step_seq, tool_name, source,
  arguments, result, status, error, duration_ms, credits_used, created_at`.
- Error: 404, 422, 500.

### 4.3 Router `conversations` (`/v1/conversations`)

**[7] GET `/v1/conversations`** — *List conversations* (`conversations.py:16`)
- Query: `limit` *(1–100, default 20)*, `offset` *(≥0, default 0)*.
- Response 200: `list[ConversationResponse]` — `id, title, metadata, created_at, updated_at`.
- Error: 422, 500.

**[8] GET `/v1/conversations/{conversation_id}`** — *Get conversation* (`conversations.py:38`)
- Path: `conversation_id` *(UUID)*.
- Response 200: `ConversationResponse`.
- Error: 404, 422, 500.

### 4.4 Router `tools` (`/v1/tools`)

**[9] GET `/v1/tools`** — *List active tools* (`tools.py:25`)
- Tanpa parameter. Dependency: `get_registry()` (bukan DB).
- Response 200: `list[ToolInfoResponse]` — `name, source, description, cost_credits, available,
  parameters (JSON Schema), timeout_s, tags`.
- Error: 500.

### 4.5 Router `health`

**[10] GET `/health`** — *Basic health check* (`health.py:33`)
- Response 200: `HealthResponse` — `{"status":"ok"}`.

**[11] GET `/health/deps`** — *Dependency health* (`health.py:39`)
- Dependency: DB. Response 200: `HealthDepsResponse` — `status, db, llm, mcp, details`.
- Catatan: mengembalikan 200 dengan `status:"degraded"` bila salah satu dependency bermasalah
  (bukan kode error HTTP).

### 4.6 Router `metrics`

**[12] GET `/metrics`** — *Prometheus metrics* (`metrics.py:10`)
- Response 200: `text/plain` format Prometheus (`generate_latest()`).

> Catatan trailing-slash: `runs`, `conversations`, `tools` mendaftarkan varian `""` dan `"/"`
> (`include_in_schema=False`). Di Postman cukup pakai bentuk tanpa trailing slash.

---

## 5. Strategi Pembuatan Postman Collection (v2.1)

- **Format:** Postman Collection Schema v2.1
  (`https://schema.getpostman.com/json/collection/v2.1.0/collection.json`).
- **Nama Collection:** `Agentic Loop API` (ikuti `FastAPI title` = "Agentic Loop", versi `0.1.0`).
- **Auth level Collection:** `noauth` (tidak ada auth inbound).
- **Variabel dasar URL:** semua request memakai `{{baseUrl}}` sebagai prefiks.
- **Struktur tiap request** (wajib diisi saat implementasi):
  - `name` deskriptif (mis. "Query Agent (sync)").
  - `request.method`, `request.url` (raw = `{{baseUrl}}/v1/...`, plus `host/path/query` terurai).
  - `request.header`: `Content-Type: application/json` untuk POST; `X-Request-ID` opsional
    (deskripsikan sebagai opsional).
  - `request.body` (mode `raw`, `options.raw.language=json`) untuk POST — isi contoh payload.
  - `request.description`: ambil dari `summary`/`description` decorator + daftar
    parameter/response.
  - Path parameter (`:run_id`, `:conversation_id`) dan query parameter (`limit`, `offset`,
    `status`, `format`) dituliskan eksplisit di `url.variable` dan `url.query` (query bonus
    `disabled:true` bila opsional, agar terlihat namun tidak aktif secara default).
  - `response[]`: Saved Examples (lihat §8).
- **Sumber kebenaran:** field payload/response diambil dari §4 (yang berasal dari schema Pydantic),
  bukan ditebak.
- **Contoh payload** minimal untuk `POST /v1/agent/query` & `/stream`:
  ```json
  {
    "query": "Apa yang sedang tren di Verosight minggu ini?",
    "conversation_id": null,
    "options": { "max_iterations": 8, "credits_budget": 100 }
  }
  ```

---

## 6. Strategi Pembuatan Postman Environment

- **Nama Environment:** `Agentic Loop — Local`.
- **Variabel yang WAJIB:**
  - `baseUrl` = `http://localhost:8000` (default server uvicorn; lihat `docs/` & Makefile
    `make dev`).
- **Variabel dinamis yang DITEMUKAN & berguna** (diisi otomatis oleh Test Script, §10):
  - `run_id` — dikembalikan `POST /v1/agent/query`, dipakai path `/v1/runs/{run_id}...`.
  - `conversation_id` — dikembalikan `POST /v1/agent/query`, dipakai `/v1/conversations/{id}` dan
    sebagai input `conversation_id` pada percakapan lanjutan.
- **Variabel yang TIDAK dibuat (beserta alasan):**
  - `access_token`, `refresh_token`, `bearer_token`, `api_key`, `session_id`, `user_id`,
    `workspace_id`, `organization_id` → **tidak ada** dalam codebase; tidak ada auth/session/
    multi-tenant. Membuatnya akan menyalahi aturan "hanya variabel yang ditemukan/dibutuhkan".
- **Catatan keamanan:** kunci `LLM_API_KEY`/`VEROSIGHT_API_KEY` berada di `.env` server, **tidak**
  dimasukkan ke Environment Postman (bukan kredensial klien).

---

## 7. Struktur Folder Collection

Kelompokkan berdasarkan router/fitur nyata (bukan contoh generik pada tugas):

```
Agentic Loop API
├── Agent
│   ├── Query Agent (sync)            POST /v1/agent/query
│   └── Query Agent (SSE stream)      POST /v1/agent/query/stream
├── Runs
│   ├── List Runs                     GET  /v1/runs
│   ├── Get Run                       GET  /v1/runs/{run_id}
│   ├── Get Run Trace (JSON)          GET  /v1/runs/{run_id}/trace
│   ├── Get Run Trace (text)          GET  /v1/runs/{run_id}/trace?format=text
│   └── Get Run Tool Invocations      GET  /v1/runs/{run_id}/tools
├── Conversations
│   ├── List Conversations            GET  /v1/conversations
│   └── Get Conversation              GET  /v1/conversations/{conversation_id}
├── Tools
│   └── List Active Tools             GET  /v1/tools
└── System
    ├── Health                        GET  /health
    ├── Health Deps                   GET  /health/deps
    └── Metrics (Prometheus)          GET  /metrics
```

> "Get Run Trace (text)" bisa dibuat sebagai request terpisah atau satu request dengan query
> `format` yang di-enable — rekomendasi: dua request agar dua Saved Example (JSON & text) jelas.

---

## 8. Strategi Saved Examples

Tugas meminta enam varian contoh (Success, Validation Error, Unauthorized, Forbidden, Not Found,
Internal Server Error). **Petakan hanya yang benar-benar dapat terjadi** pada tiap endpoint —
jangan mengarang response yang tidak mungkin dihasilkan aplikasi.

| Varian | Status | Berlaku pada | Catatan |
|---|---|---|---|
| Success | 200 | semua endpoint | body sesuai response model §4 |
| Validation Error | 422 | endpoint dengan body/query/path bertipe (semua kecuali `/health`, `/metrics`) | `{"error":{"code":"VALIDATION_ERROR","message":"Validation error: [...]"},"meta":{"request_id":"..."}}` |
| Not Found | 404 | endpoint ber-path `{run_id}`/`{conversation_id}` ([4],[5],[6],[8]) | `{"error":{"code":"404","message":"Run '...' not found."},"meta":{...}}` |
| Rate Limited | 429 | **[1], [2]** saja | contoh khusus (lihat bawah) — ini response khusus project |
| Internal Server Error | 500 | semua endpoint | `{"error":{"code":"INTERNAL_SERVER_ERROR","message":"An unexpected server error occurred."},"meta":{...}}` |
| **Unauthorized (401)** | — | **tidak ada** | **N/A**: tidak ada auth. Jangan buat contoh; catat di deskripsi "endpoint tidak memakai autentikasi". |
| **Forbidden (403)** | — | **tidak ada** | **N/A**: idem 401. |

**Response khusus yang WAJIB dibuatkan contoh:**
- **429 Rate Limited** pada `/v1/agent/query*`:
  ```json
  { "error": { "code": 429, "message": "Rate limit exceeded. Maximum 60 requests per minute." },
    "meta": { "request_id": "..." } }
  ```
- **`GET /v1/runs/{run_id}/trace?format=text`** → contoh `text/plain` (pohon trace), berbeda dari
  contoh JSON default.
- **`GET /metrics`** → contoh `text/plain` Prometheus.
- **`POST /v1/agent/query/stream`** → contoh `text/event-stream` (`data: ...` + `data: [DONE]`).
- **`GET /health/deps`** → dua contoh 200: `status:"ok"` dan `status:"degraded"`.

Setiap Saved Example harus mencantumkan header response `X-Request-ID` (selalu ada).

---

## 9. Strategi Authentication

- **Collection-level auth = No Auth.** Aplikasi tidak memiliki lapisan autentikasi inbound
  (tidak ada `OAuth2`, `HTTPBearer`, `APIKeyHeader`, session cookie). Diverifikasi via grep.
- **Tidak ada** endpoint login/logout/refresh untuk diautomasi.
- **Header yang relevan** bukan untuk auth, melainkan:
  - `Content-Type: application/json` (POST).
  - `X-Request-ID` (opsional; untuk korelasi log/trace — dikembalikan di response).
- **Alasan tidak ada Pre-request auth script:** tidak ada token untuk disuntik. Bila di masa
  depan auth ditambahkan, barulah Environment `bearer_token` + Pre-request Script relevan
  (di luar cakupan saat ini).

---

## 10. Strategi Variable

- **Environment variables:** `baseUrl` (statis), `run_id` & `conversation_id` (dinamis).
- **Collection variables:** opsional — `apiVersion` = `v1` bila ingin URL disusun
  `{{baseUrl}}/{{apiVersion}}/...`. Tidak wajib.
- **Automation (Test Script) untuk menangkap ID** — pasang pada request "Query Agent (sync)":
  ```javascript
  // Tests tab — Query Agent (sync)
  const json = pm.response.json();
  if (json.run_id) pm.environment.set("run_id", json.run_id);
  if (json.conversation_id) pm.environment.set("conversation_id", json.conversation_id);
  pm.test("status 200", () => pm.response.to.have.status(200));
  pm.test("answer present", () => pm.expect(json.answer).to.be.a("string").and.not.empty);
  ```
  Dengan ini, request Runs/Conversations otomatis memakai `{{run_id}}` / `{{conversation_id}}`.
- **Pre-request Script:** umumnya **tidak diperlukan** (tanpa auth). Boleh dipakai opsional untuk
  meng-generate `X-Request-ID` unik:
  ```javascript
  pm.request.headers.upsert({ key: "X-Request-ID", value: pm.variables.replaceIn("{{$guid}}") });
  ```
- **Assertion umum** yang disarankan per request: cek status, cek header `X-Request-ID` ada, dan
  (untuk error) cek struktur `error.code`/`error.message`/`meta.request_id`.

---

## 11. Testing Flow

Alur yang sesuai project (tanpa login karena tidak ada auth):

1. **Cek kesehatan** — `GET /health` lalu `GET /health/deps` (pastikan `db`/`llm`/`mcp` sesuai).
2. **Lihat tool aktif** — `GET /v1/tools` (verifikasi tool `source` mcp/verosight/local).
3. **Jalankan agen** — `POST /v1/agent/query` → Test Script menyimpan `run_id` &
   `conversation_id` ke Environment.
4. **Inspeksi hasil run:**
   - `GET /v1/runs/{{run_id}}` → metadata + `final_answer`.
   - `GET /v1/runs/{{run_id}}/trace` (JSON) dan `?format=text` (pohon).
   - `GET /v1/runs/{{run_id}}/tools` → daftar `tool_invocations` (data hasil).
5. **Percakapan lanjutan** — `POST /v1/agent/query` lagi dengan body
   `conversation_id = {{conversation_id}}` untuk menguji riwayat.
6. **Riwayat percakapan** — `GET /v1/conversations` dan `GET /v1/conversations/{{conversation_id}}`.
7. **(Opsional) Streaming** — `POST /v1/agent/query/stream` untuk melihat SSE.
8. **(Opsional) Observability** — `GET /metrics`.
9. **Uji jalur error** — panggil `GET /v1/runs/<uuid-acak>` (404), kirim body kosong ke
   `POST /v1/agent/query` (422), dan (bila mudah) picu 429 dengan menurunkan
   `RATE_LIMIT_PER_MINUTE` lalu spam `POST /v1/agent/query`.

> Tidak ada langkah "Logout" karena tidak ada session/token.

---

## 12. Langkah Implementasi

1. **Buat Environment** `Agentic Loop — Local` dengan `baseUrl=http://localhost:8000`, plus
   placeholder kosong `run_id`, `conversation_id`.
2. **Buat Collection** `Agentic Loop API` (schema v2.1), auth `noauth`, deskripsi ringkas +
   catatan "no inbound auth".
3. **Buat folder** sesuai §7 (Agent, Runs, Conversations, Tools, System).
4. **Tambahkan 12 request** sesuai §4: method, URL `{{baseUrl}}/...`, header, path/query param,
   body contoh (POST), dan deskripsi (dari summary/description + schema).
5. **Tambahkan Saved Examples** per §8 (Success + error yang berlaku; response khusus SSE/text/
   Prometheus/429/health-deps).
6. **Pasang Test Script** penangkap `run_id`/`conversation_id` pada "Query Agent (sync)"; pasang
   assertion status/struktur pada request lain (§10).
7. **(Opsional)** Pre-request Script `X-Request-ID` di level Collection.
8. **Urutkan request** agar mendukung Testing Flow (§11) bila dijalankan via Collection Runner.
9. **Ekspor** Collection (v2.1) dan Environment sebagai file `.json` (mis. ke
   `docs/postman/`) — *dilakukan pada tahap implementasi, bukan sekarang*.
10. **Dokumentasikan** cara import di README/`docs/`.

---

## 13. Validasi Hasil

- **Server jalan:** `docker compose up -d && make migrate && make dev`, cek `GET /health` = 200.
- **Konsistensi dengan OpenAPI:** bandingkan Collection dengan `GET /openapi.json` (atau `/docs`
  di `APP_ENV=local`) — pastikan 12 operasi cocok (path, method, schema). `/docs` & `/redoc`
  hanya aktif saat `APP_ENV=local` (`app/main.py:104`).
- **Collection Runner:** jalankan folder secara berurutan; pastikan `run_id`/`conversation_id`
  terisi otomatis dan request turunan lulus.
- **Cek error paths:** 404 (UUID acak), 422 (body kosong), 429 (rate limit), 500 (bila bisa
  dipicu) menghasilkan bentuk `{"error":{...},"meta":{...}}`.
- **Cek response non-JSON:** `/stream` (event-stream), `/trace?format=text` (text/plain),
  `/metrics` (Prometheus) ter-handle benar di Postman.
- **Lint schema:** validasi file terhadap skema Collection v2.1 (mis. via Postman import atau
  validator) — dilakukan saat implementasi.

---

## 14. Risiko

- **RS1 — Panggilan nyata berbiaya.** `POST /v1/agent/query*` memanggil OpenAI + Verosight asli
  (tanpa mock, aturan §1.6 `docs/PROMPT_PLAN.md`) → memakan token/kredit. Mitigasi: pakai
  `options.credits_budget` kecil & `max_iterations` rendah saat pengujian.
- **RS2 — Prasyarat DB.** Endpoint `runs`/`conversations`/`agent` butuh Postgres siap (`make
  migrate`). Tanpa itu bisa muncul 500. Bukan bug Postman; dokumentasikan prasyarat.
- **RS3 — 401/403 tidak ada.** Bila ekspektasi eksternal menuntut contoh Unauthorized/Forbidden,
  itu tidak dapat dipenuhi jujur karena aplikasi tanpa auth. Ditandai N/A (§8).
- **RS4 — SSE di Postman.** Dukungan `text/event-stream` bervariasi antar versi Postman; contoh
  streaming mungkin tampil sebagai teks utuh, bukan aliran token.
- **RS5 — Rate limit in-memory per-proses.** 429 hanya andal pada satu instance; sulit dipicu bila
  `RATE_LIMIT_PER_MINUTE` tinggi (default 60). Untuk demo, turunkan sementara via `.env`.
- **RS6 — Drift schema.** Bila endpoint berubah setelah dokumen ini, Collection bisa usang.
  Mitigasi: sinkronkan dengan `openapi.json`.

---

## 15. Asumsi

- **A1.** `baseUrl` default `http://localhost:8000` — *alasan:* pola uvicorn/`make dev` standar
  project; tidak ada override port di codebase yang ditemukan. Sesuaikan bila host/port berbeda.
- **A2.** Tidak ada autentikasi inbound — *alasan:* grep atas `OAuth2/HTTPBearer/APIKeyHeader/
  Security/Depends(auth...)` nihil; tidak ada router auth. Ini fakta codebase, bukan tebakan.
- **A3.** Variabel dinamis cukup `run_id` + `conversation_id` — *alasan:* keduanya satu-satunya ID
  yang dihasilkan response dan dipakai kembali di path lain; tidak ada `user_id/workspace_id/…`.
- **A4.** `/docs`, `/redoc`, `/openapi.json` tersedia saat `APP_ENV=local` — *alasan:*
  `app/main.py:104-105` men-disable-nya di non-local. Validasi §13 mengasumsikan mode local.
- **A5.** Contoh error mengikuti bentuk handler global — *alasan:* `app/api/exceptions.py`
  memaksa struktur seragam untuk semua exception.

---

## 16. Catatan Implementasi

- **Jangan** membuat variabel/otentikasi yang tidak ada dasarnya (token/api_key/session) — akan
  menyesatkan pengguna Collection.
- **Sumber kebenaran field** = schema Pydantic di `app/schemas/` dan response model inline di
  router; jangan menebak field.
- **Path FastAPI** memakai `{run_id}`; di Postman gunakan sintaks `:run_id` (path variable) yang
  di-bind ke `{{run_id}}` Environment untuk otomatisasi.
- **Trailing slash:** pakai bentuk tanpa `/` di akhir (varian `"/"` sengaja `include_in_schema=
  False`).
- **Header korelasi:** sertakan `X-Request-ID` sebagai header opsional di semua request dan
  tunjukkan pada Saved Example response header — memudahkan menautkan ke log/trace server.
- **Selaras dokumen lain:** aturan global & kontrak ada di `docs/PROMPT_PLAN.md`; sinkronisasi
  model OpenAI di `docs/Plan-perubahan-model.md`; perubahan cara kerja tool/MCP di
  `docs/plan-perubahan-cara-kerja.md`.
- **Batasan tugas ini:** hanya dokumen perencanaan. Pembuatan file Collection/Environment `.json`,
  perubahan kode, dan commit dilakukan pada tahap terpisah.
```
