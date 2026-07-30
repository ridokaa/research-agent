# Agentic Loop

> Agentic Loop adalah sistem agen riset otonom yang bekerja dalam iterasi (reason-act loop), mengeksekusi berbagai perkakas (*tools*), dan berhenti secara independen ketika telah memperoleh serta memverifikasi jawaban final.

---

## 1. Arsitektur Sistem

Berikut adalah alur eksekusi komponen dari request pengguna hingga pengembalian jawaban final (sesuai bagian 1.1 PROMPT_PLAN.md):

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
    │     Membangun system prompt + riwayat + memory item      │
    │                                                          │
    │  2. LLMClient.respond() (Responses API)                  │
    │     Mengirim payload ke OpenAI / LLM Provider            │
    │                                                          │
    │  3. Handle Response                                      │
    │     ├─ function_calls ──► ToolExecutor.execute_tools()  │
    │     │                     ├─ Dedup & Cache check         │
    │     │                     ├─ Parallel execution          │
    │     │                     └─ Truncate & Save memory      │
    │     │
    │     └─ text response  ──► Reflection & Finalize Self-Check
    │                           ├─ is_final = True  ──► STOP   │
    │                           └─ is_final = False ──► REPEAT │
    │                                                          │
    │  4. Evaluasi Policy (Max Iterations / Credits / Timeout) │
    └──────────────────────────────────────────────────────────┘
    │
    ▼
[RunResult] ──► HANYA mengembalikan answer + metadata run
                 (Detail trace & thinking dapat diakses via GET /v1/runs/{id}/trace)
```

---

## 2. Cara Setup & Menjalankan Project

### 2.1. Persyaratan Sistem
- Docker & Docker Compose
- Python 3.11+
- virtualenv (atau `.venv`)

### 2.2. Setup Lingkungan & Secrets
Copy `.env.example` ke `.env` dan isi API key milik Anda:
```bash
cp .env.example .env
# Edit .env: atur LLM_API_KEY dan VEROSIGHT_API_KEY
```

### 2.3. Menjalankan Docker Service
Jalankan container PostgreSQL (`rag_db`):
```bash
docker compose up -d
# Atau menggunakan Makefile:
make up
```

### 2.4. Instalasi Dependency & Migrasi Database
Aktifkan virtualenv dan jalankan migrasi database Alembic:
```bash
source .venv/bin/activate
pip install -r requirements.txt  # atau make install

# Jalankan migrasi schema ke PostgreSQL
alembic upgrade head
# Atau menggunakan Makefile:
make migrate
```

### 2.5. Menjalankan Server FastAPI
Jalankan server pengembangan FastAPI dengan hot-reload:
```bash
uvicorn app.main:app --reload --port 8000
# Atau menggunakan Makefile:
make dev
```
Akses Swagger UI dokumentasi OpenAPI di: `http://localhost:8000/docs`

---

## 3. Menjalankan Simulasi (CLI & Viewer)

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

### 3.4. Debug Timeline Viewer (HTML)
Buka peramban untuk melihat timeline visual interaktif (hanya aktif jika `APP_ENV=local`):
```text
http://localhost:8000/debug/runs/<run_id>
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
