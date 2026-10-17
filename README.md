# 🔎 research-agent

**Agen riset otonom berbasis LLM.** Ajukan pertanyaan dalam bahasa natural → agen merencanakan, memanggil *tools* secara berulang (reason–act loop), memeriksa jawabannya sendiri, lalu **berhenti otomatis** saat jawaban final sudah terverifikasi. Pengguna hanya menerima jawaban bersih; seluruh langkah berpikir & pemanggilan tool tersimpan sebagai *trace* di PostgreSQL.

Saat ini sumber datanya adalah **Verosight** (analitik media sosial: sentimen, trending, topik, postingan & komentar) yang terhubung lewat **MCP** atau **REST**. Inti agennya generik: tool lain cukup ditambahkan tanpa mengubah loop.

```text
"Berapa sentimen publik soal program Makan Bergizi Gratis minggu ini?"
        │
        ▼  agen memanggil verosight_sentiment → verosight_trending → … → self-check
        ▼
{"answer": "...ringkasan jawaban final...", "run_id": "...", "status": "success", "iterations": 3}
```

> 🔀 Project terkait: [**trending-topic-mapper**](https://github.com/ridokaa/trending-topic-mapper) (notebook klasifikasi topik) · [**bbm-sentiment-api**](https://github.com/ridokaa/bbm-sentiment-api) (API sentimen BBM).

---

## ✨ Fitur Utama

| Fitur | Keterangan |
|---|---|
| **Agentic loop** | Planner → LLM → eksekusi tool → refleksi & *self-check* → ulangi / berhenti |
| **Dua gaya API LLM** | `LLM_API_STYLE=responses` (OpenAI Responses API) atau `chat_completions` (9Router, DeepSeek, provider OpenAI-compatible) |
| **Tool auto-discovery** | Tool lokal via dekorator `@register_tool`; tool Verosight di-*ingest* otomatis dari server MCP (`VEROSIGHT_MODE=mcp \| rest \| both`) |
| **Guardrail** | Batas iterasi, waktu, dan kredit per run; deduplikasi & cache panggilan tool; rate limit per IP |
| **Jawaban bersih** | `POST /v1/agent/query` hanya mengembalikan jawaban + metadata; *trace* lengkap via `GET /v1/runs/{id}/trace` |
| **Observabilitas** | Trace per langkah di PostgreSQL, log JSONL, metrik Prometheus, CLI simulasi & replay |
| **Transparansi error** | Tanpa data *mock*: kegagalan tool/LLM dilaporkan apa adanya (`ErrorReport`), bukan dikarang |

---

## 1. Arsitektur Sistem

```text
Pengguna (Query)
    │
    ▼
[FastAPI Application] ──► Validasi query & rate limit per IP
    │
    ▼
[AgentLoop] ──► Inisialisasi State, Tracer, & Repository DB
    │
    ├────────────────────── AGENTIC LOOP ──────────────────────┐
    │                                                          │
    │  1. Planner.build_request()                              │
    │     System prompt + riwayat percakapan + memory item     │
    │                                                          │
    │  2. LLMClient.respond()                                  │
    │     Responses API atau Chat Completions (LLM_API_STYLE)  │
    │                                                          │
    │  3. Handle Response                                      │
    │     ├─ tool calls ──► ToolExecutor.execute_tools()       │
    │     │                 ├─ Dedup & cache check             │
    │     │                 ├─ Eksekusi paralel                │
    │     │                 │   (tool lokal / Verosight MCP)   │
    │     │                 └─ Simpan hasil ke memory          │
    │     │                                                    │
    │     └─ teks jawaban ──► Reflection & finalize self-check │
    │                         ├─ is_final = True  ──► STOP     │
    │                         └─ is_final = False ──► ULANGI   │
    │                                                          │
    │  4. Evaluasi Policy (max iterasi / kredit / timeout)     │
    └──────────────────────────────────────────────────────────┘
    │
    ▼
[RunResult] ──► HANYA answer + metadata run
                (trace & proses berpikir via GET /v1/runs/{id}/trace)
```

### Tools bawaan

| Sumber | Tool |
|---|---|
| Lokal | `calculator`, `current_time`, `db_query` |
| Verosight | `verosight_sentiment`, `verosight_sentiment_trend`, `verosight_trending`, `verosight_topics`, `verosight_search_posts`, `verosight_list_posts`, `verosight_list_comments` |

Pada `VEROSIGHT_MODE=mcp`, daftar tool Verosight diambil langsung dari `list_tools()` server MCP sehingga bisa bertambah mengikuti Verosight.

---

## 2. Cara Setup & Menjalankan Project

### 2.1. Persyaratan Sistem
- Docker & Docker Compose (untuk PostgreSQL)
- Python 3.11+
- API key LLM (OpenAI atau provider OpenAI-compatible) dan API key Verosight

### 2.2. Setup Lingkungan & Secrets
```bash
git clone https://github.com/ridokaa/research-agent.git
cd research-agent
cp .env.example .env
# Edit .env: isi LLM_API_KEY dan VEROSIGHT_API_KEY
```

Konfigurasi penting di `.env`:

| Variabel | Default | Keterangan |
|---|---|---|
| `LLM_API_STYLE` | `responses` | `responses` (OpenAI) atau `chat_completions` (9Router / DeepSeek / OpenAI-compatible) |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | OpenAI, `gpt-4o-mini` | Endpoint & model LLM |
| `AGENT_MAX_ITERATIONS` | `8` | Batas putaran loop per run |
| `AGENT_RUN_TIMEOUT_S` | `180` | Batas waktu per run (detik) |
| `AGENT_CREDITS_BUDGET` | `100` | Batas kredit tool per run |
| `TOOL_RESULT_PASSTHROUGH` | `true` | `true` = hasil tool dikirim utuh ke LLM; `false` = dipotong/diringkas dulu |
| `VEROSIGHT_MODE` | `mcp` | `mcp` (tool dari server MCP), `rest` (wrapper REST lokal), atau `both` |
| `VEROSIGHT_ENABLED` | `true` | Matikan untuk menjalankan agen tanpa Verosight |

### 2.3. Menjalankan PostgreSQL
```bash
docker compose up -d      # atau: make up
```

### 2.4. Instalasi Dependency & Migrasi Database
```bash
python3 -m venv .venv
source .venv/bin/activate
make install              # pip install -e ".[dev]" (pakai uv jika tersedia)
make migrate              # alembic upgrade head
```

### 2.5. Menjalankan Server FastAPI
```bash
make dev                  # uvicorn app.main:app --reload --port 8000
```
Swagger UI: `http://localhost:8000/docs`

### 2.6. Endpoint API

| Method | Endpoint | Fungsi |
|---|---|---|
| `POST` | `/v1/agent/query` | Jalankan agen, kembalikan jawaban final + metadata |
| `POST` | `/v1/agent/query/stream` | Sama, jawaban di-*stream* via SSE |
| `GET` | `/v1/runs`, `/v1/runs/{run_id}` | Daftar & detail run |
| `GET` | `/v1/runs/{run_id}/trace` | Trace lengkap langkah demi langkah |
| `GET` | `/v1/runs/{run_id}/tools` | Daftar pemanggilan tool dalam run |
| `GET` | `/v1/conversations`, `/v1/conversations/{id}` | Riwayat percakapan |
| `GET` | `/v1/tools` | Tool yang terdaftar & aktif |
| `GET` | `/health`, `/health/deps` | Status server; status DB, LLM, dan MCP |
| `GET` | `/metrics` | Metrik Prometheus |

```bash
curl -X POST http://localhost:8000/v1/agent/query \
     -H "Content-Type: application/json" \
     -d '{"query": "Apa topik yang sedang trending soal banjir 7 hari terakhir?"}'
```

Kirim ulang `conversation_id` dari respons sebelumnya untuk melanjutkan percakapan.

---

## 3. Menjalankan Simulasi (CLI)

Sistem menyediakan Typer CLI di `scripts/simulate.py` untuk menguji agen secara live tanpa browser/frontend.

### 3.1. Simulasi Pertanyaan Langsung (`simulate ask`)
```bash
python scripts/simulate.py ask "Berapa sentimen publik soal program Makan Bergizi Gratis?"
```
*CLI akan mencetak pohon trace eksekusi berwarna secara real-time ke terminal.*

### 3.2. Simulasi Seluruh Skenario (`simulate scenarios`)
Menjalankan seluruh suite skenario end-to-end (termasuk skenario satu-tool, multi-tool, hasil kosong, dan error handling):
```bash
python scripts/simulate.py scenarios
```
Untuk hanya menjalankan skenario yang ditandai `@pytest.mark.live`:
```bash
python scripts/simulate.py scenarios --live-only
```

### 3.3. Replay Run berdasarkan ID (`simulate replay`)
Render ulang trace eksekusi run masa lalu dari database:
```bash
python scripts/simulate.py replay <run_id>
```

### 3.4. Melihat Trace via API
Trace lengkap (langkah berpikir, panggilan tool, hasil) dari run mana pun:
```bash
curl http://localhost:8000/v1/runs/<run_id>/trace | jq .
```

---

## 4. Observabilitas & Cara Membaca Log

### 4.1. Membaca Structured Logs (JSONL)
Log agen ditulis dalam format JSON Lines ke `logs/agent.jsonl`. Anda dapat memantau log eksekusi real-time menggunakan `tail` dan `jq`:

```bash
tail -f logs/agent.jsonl | jq .
```

Filter log khusus event panggilan tool atau error:
```bash
tail -f logs/agent.jsonl | jq 'select(.event_type == "tool_result" or .event_type == "error")'
```

### 4.2. Prometheus Metrics (`GET /metrics`)
Metrik observabilitas Prometheus diekspos di endpoint `GET /metrics`:

| Nama Metrik | Tipe | Deskripsi |
|---|---|---|
| `runs_total{status}` | Counter | Total eksekusi run per status (`success`, `failed`, `exhausted`, `timeout`) |
| `iterations_histogram` | Histogram | Distribusi jumlah iterasi per run |
| `tool_calls_total{tool,status}` | Counter | Jumlah panggilan tool per status (`success`, `error`, `empty`) |
| `credits_used_total` | Counter | Total kredit tool yang dikonsumsi |
| `llm_tokens_total` | Counter | Total token LLM yang digunakan |

Tes metrik via curl:
```bash
curl http://localhost:8000/metrics
```

---

## 5. Cara Menambah Tool Baru

Menambah tool baru sangat sederhana. Cukup buat file `.py` baru di dalam direktori `app/tools/` (atau direktori pembantu di bawahnya) dan gunakan dekorator `@register_tool`. Tool akan **terdeteksi secara otomatis** oleh `ToolRegistry` saat server dinyalakan tanpa mengedit kode inti agen.

Berikut adalah contoh lengkap file pembuat tool baru (**< 30 baris kode**):

```python
# app/tools/builtin/weather.py
from __future__ import annotations
from typing import Any
from app.tools.registry import register_tool

@register_tool(
    name="get_weather",
    description="Mendapatkan perkiraan cuaca terkini untuk nama kota tertentu.",
    source="local",
    cost_credits=1,
    timeout_s=5.0,
    tags=["weather", "info"],
)
async def get_weather(city: str) -> dict[str, Any]:
    """Mengambil data cuaca berdasarkan nama kota."""
    # Contoh fungsi eksekusi tool
    return {
        "city": city,
        "temperature_celsius": 28.5,
        "condition": "Cerah Berawan",
        "humidity_percent": 75,
    }
```

---

## 6. Architecture Decision Records (ADR)

Keputusan arsitektur mendasar dicatat dalam direktori `docs/adr/`:
- [`0001-only-final-answer-in-query-response.md`](docs/adr/0001-only-final-answer-in-query-response.md): Alasan pengembalian jawaban bersih tanpa proses berpikir pada endpoint `/v1/agent/query`.
- [`0002-tool-call-deduplication.md`](docs/adr/0002-tool-call-deduplication.md): Strategi deduplikasi & caching hasil tool untuk mencegah loop tak berujung.
- [`0003-mcp-and-rest-separation.md`](docs/adr/0003-mcp-and-rest-separation.md): Pemisahan integrasi dinamis MCP dan wrapper REST lokal.

---

## 7. Postman Collection & Environment

Postman Collection (v2.1) dan Environment telah disediakan di direktori [`docs/postman/`](docs/postman/):

1. **Import File ke Postman:**
   - [Postman Collection](docs/postman/Agentic_Loop_API.postman_collection.json) (`Agentic_Loop_API.postman_collection.json`)
   - [Postman Environment](docs/postman/Agentic_Loop_Local.postman_environment.json) (`Agentic_Loop_Local.postman_environment.json`)
2. **Pilih Environment:** Aktifkan environment **`Agentic Loop — Local`** (`baseUrl: http://localhost:8000`).
3. **Fitur Otomatisasi:** Request `POST /v1/agent/query` secara otomatis mengekstrak `run_id` dan `conversation_id` dari response JSON dan menyimpannya ke Environment untuk digunakan di endpoint `Runs` dan `Conversations`.

