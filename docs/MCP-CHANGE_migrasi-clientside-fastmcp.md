# [MCP-CHANGE] Migrasi Klien MCP → client-side FastMCP (server tetap Verosight)

> **⚠️ Ini perubahan pada lapisan MCP.** Dokumen rencana untuk agen eksekutor.
> Tujuan: mengganti **pustaka klien** MCP dari SDK `mcp` mentah menjadi **`fastmcp.Client`**,
> sambil **tetap** memakai server MCP milik Verosight dan **tetap** memakai metadata resmi
> (nama/deskripsi/schema) dari Verosight — tanpa menulis deskripsi tool sendiri.
> Rencana rollback: **`docs/Old-MCP_revert-ke-mcp-awal.md`**.
> Audit branch `retrofit/chat-completions-adapter` (2026-08-04). Patuhi `docs/PROMPT_PLAN.md`
> §1.6 & arsitektur **style-agnostic**.

---

## 1. Ringkasan

Klien MCP saat ini (`app/mcp/client.py`) memakai SDK `mcp` secara manual (`ClientSession` +
`sse_client` + `AsyncExitStack`) dengan workaround buatan sendiri: `_SessionInitRetryTransport`
(untuk 404 "session not found" pada SSE) dan `asyncio.Lock` per klien (serialisasi panggilan).
Rencana ini menggantinya dengan **`fastmcp.Client`** yang lebih ergonomis dan, **jika Verosight
mendukung Streamable HTTP**, memindahkan transport dari SSE → Streamable HTTP untuk
**menghilangkan `session_not_found`** dan memungkinkan **paralelisme sejati** (buang Lock).

**Prinsip yang tidak boleh dilanggar:** tool tetap berasal dari **`list_tools()` server Verosight**;
`ToolSpec` (name/description/parameters) diturunkan dari sana seperti sekarang — **tidak** ada
deskripsi tool yang ditulis manual.

---

## 2. Kondisi Saat Ini (yang disentuh)

- `app/mcp/client.py` — `MCPClient` (SDK `mcp`): `_SessionInitRetryTransport` (baris 18),
  `_lock`/`lock` (baris 111-116), `connect()` (135 `sse_client`), `list_tools()` (168),
  `call_tool()` (196, kembalikan `mcp.types.CallToolResult`), `close()` (225).
- `app/mcp/adapter.py` — `mcp_tool_to_spec(tool, alias)` (baris 29; baca `input_schema`/`inputSchema`
  → `ToolSpec` resmi), `_normalize_responses_schema` (14), `mcp_result_to_tool_result`, `MCPTool`.
- `app/mcp/manager.py` — `MCPManager.get_server_configs()` (default Verosight: `transport="sse"`,
  `url=VEROSIGHT_MCP_SSE_URL`, header `X-API-Key`), `connect_all()` (cache 15 mnt, register ke
  `ToolRegistry`, degradasi anggun via `VEROSIGHT_MCP_KNOWN_TOOLS`).
- `app/config.py` — `VEROSIGHT_MCP_SSE_URL`, `VEROSIGHT_MODE=mcp`, `VEROSIGHT_API_KEY`,
  `MCP_SERVERS_JSON`.
- Dependensi: **`fastmcp` belum terpasang** (perlu ditambah).

---

## 3. Yang TETAP vs BERUBAH

### 3.1 TETAP (jangan diubah)
- **Sumber & isi metadata tool** — tetap dari `list_tools()` Verosight; `mcp_tool_to_spec`
  tetap menurunkan `name`/`description`/`parameters` dari `Tool` resmi. **Tidak** menulis deskripsi
  sendiri. (Ini permintaan eksplisit pengguna.)
- **Kontrak internal** `ToolSpec`/`ToolResult`, prefiks nama `verosight__{tool}`, `source="mcp"`.
- **`MCPManager`** sebagai orkestrator: konfigurasi server, cache, register ke registry, degradasi
  anggun saat server offline.
- **Loop/agent/registry** — tidak disentuh (style-agnostic).
- **`VEROSIGHT_MODE=mcp`** sebagai mode aktif.

### 3.2 BERUBAH
- **Isi `MCPClient`** — dari SDK `mcp` manual → `fastmcp.Client` (connect/list_tools/call_tool/close).
- **Transport** — SSE → **Streamable HTTP** *(bila Verosight mendukung — lihat §4)*.
- **Workaround** — bila Streamable HTTP: **buang** `_SessionInitRetryTransport` dan `asyncio.Lock`
  (tidak diperlukan). Bila tetap SSE: pertahankan seperlunya.
- **Konversi hasil** — `mcp_result_to_tool_result` disesuaikan dengan tipe hasil FastMCP.
- **Dependensi** — tambah `fastmcp` di `pyproject.toml`.

---

## 4. Pertanyaan Penentu — Transport Verosight (Tahap 0, WAJIB dulu)

Manfaat terbesar (hilangnya `session_not_found` + paralelisme) hanya didapat bila Verosight
mendukung **Streamable HTTP**. URL sekarang `…/v1/mcp/sse` = SSE.

**Cek dulu:**
1. Coba endpoint Streamable HTTP (mis. `…/v1/mcp` atau `…/v1/mcp/http`) dengan `fastmcp.Client`
   (`StreamableHttpTransport`) + header `X-API-Key`; panggil `list_tools()`.
2. Atau tanyakan dokumentasi Verosight transport apa yang tersedia.

- **Streamable HTTP tersedia** → **WAJIB pakai transport ini.** Inilah yang benar-benar
  menyelesaikan masalah race sesi SSE (lihat §4A): `session_not_found` hilang & panggilan paralel.
- **Hanya SSE** → migrasi FastMCP tetap dilakukan, TAPI masalah race sesi SSE `session_not_found` **tidak dapat dihilangkan
  sepenuhnya dari sisi klien** (race ada di server Verosight). Terapkan mitigasi SSE (§4A.2) **dan
  eskalasi ke Verosight** untuk endpoint Streamable HTTP.

**Probe transport (konkret) — jalankan sebelum memutuskan:**
```python
# uji cepat di luar app; sesuaikan API dengan versi fastmcp terpasang
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
async def probe(url):
    async with Client(StreamableHttpTransport(url, headers={"X-API-Key": "<key>"})) as c:
        print(len(await c.list_tools()), "tools via streamable-http:", url)
for u in ["https://api.verosight.com/v1/mcp", "https://api.verosight.com/v1/mcp/http"]:
    try: asyncio.run(probe(u))
    except Exception as e: print("gagal", u, repr(e))
```
Jika salah satu URL mengembalikan daftar tool → **Streamable HTTP tersedia** (pakai §4A.1).

---

## 4A. Penanganan Eksplisit — Race Sesi SSE `session_not_found` (+ Eksekusi Serial)

Log klien SSE saat ini memperlihatkan tiga gejala berikut; plan ini menanganinya sebagai berikut:

| Gejala (log MCP-SSE saat ini) | Penyebab | Penanganan oleh plan |
|---|---|---|
| `mcp.session_not_found_delay_detected` + retry **sebelum tiap call** | Race propagasi sesi **SSE** (POST tiba sebelum sesi terdaftar di server Verosight) | **Streamable HTTP → HILANG** (tak ada tarian buka-SSE → POST-`sessionId`). SSE-only → **mitigasi §4A.2** (tak bisa dihapus penuh dari klien). |
| Tool berjalan **serial** (get_volume `28s` → get_sentiment `33s` → get_topics `55s`) | `asyncio.Lock` per sesi (SSE tak aman diakses konkuren) | **Streamable HTTP → buang Lock → paralel sejati** (tiap request independen). SSE-only → opsi **session-pool** (§4A.2). |
| `get_topics` **29 detik** | Sebagian retry sesi, sebagian **payload 142 KB** | Bagian retry-sesi teratasi di atas. **Payload besar BUKAN masalah klien MCP** → rujuk `docs/plan-fix-truncated-answer-fallback-summary.md` (batasi `limit`/`top_posts` di argumen tool). |

### 4A.1 Jalur utama (Streamable HTTP) — MENGHILANGKAN masalah
Bila Tahap 0 menemukan Streamable HTTP: pakai `StreamableHttpTransport`, lalu **buang**
`_SessionInitRetryTransport` **dan** `asyncio.Lock`. Hasil yang diharapkan: log satu run **bersih
dari** `session_not_found`, ketiga tool berjalan **paralel**, latensi turun signifikan. Inilah
kondisi lulus utama "menghilangkan race sesi SSE `session_not_found`".

### 4A.2 Jalur mitigasi (SSE-only) — MEMPERKECIL, bukan menghilangkan
Bila Verosight hanya SSE (race ada di server, tak bisa dihapus dari klien), kurangi dampaknya:
- **Sesi persisten + warm-up:** connect sekali di startup lalu segera `list_tools()` untuk memaksa
  sesi terbentuk sebelum call pertama; **jangan** reconnect per panggilan.
- **Retry transparan:** pertahankan perilaku retry `session_not_found` (port
  `_SessionInitRetryTransport` atau hook retry FastMCP) agar tetap tidak fatal.
- **Paralelisme via session-pool (opsional):** buka N sesi SSE (mis. 3) sehingga tiap tool paralel
  memakai sesinya sendiri, alih-alih satu sesi + Lock. Trade-off: tiap sesi tetap kena race init sekali.
- **Eskalasi (penting):** minta Verosight menyediakan endpoint **Streamable HTTP** — satu-satunya
  cara menghapus `session_not_found` sepenuhnya. Dokumentasikan bahwa tanpa itu, gejala berkurang
  tetapi tidak nol.

> **Kesimpulan jujur:** plan ini **menangani** masalah race sesi SSE `session_not_found` **secara penuh** bila Streamable
> HTTP tersedia (§4A.1); bila SSE-only, ia **memperkecil**-nya (§4A.2) dan menetapkan eskalasi ke
> Verosight sebagai jalur untuk penyelesaian penuh. FastMCP saja di atas SSE **tidak** menjamin
> hilangnya race — karena itu Tahap 0 (probe transport) menjadi penentu.

---

## 5. Tahapan Implementasi

**Tahap 1 — Tambah dependensi.** `pyproject.toml`: tambah `fastmcp`. `uv sync`/`pip install`.
Verifikasi versi terpasang dan API-nya (API FastMCP berbeda antar-versi — **cocokkan dengan versi
terpasang**, jangan berasumsi).

**Tahap 2 — Tulis ulang `MCPClient` di atas `fastmcp.Client`.**
- Konstruksi transport dari config:
  - Streamable HTTP: `StreamableHttpTransport(url, headers={"X-API-Key": api_key})`.
  - SSE (fallback): `SSETransport(url, headers={"X-API-Key": api_key})`.
- `client = fastmcp.Client(transport)`; kelola lifecycle via `async with client:` atau
  `await client.__aenter__()/__aexit__()` yang dibungkus method `connect()`/`close()` agar
  antarmuka `MCPClient` (`connect/list_tools/call_tool/close`) **tetap sama** untuk pemanggil.
- `list_tools()` → kembalikan objek `Tool` (name/description/inputSchema) — **format sama** seperti
  SDK, sehingga `mcp_tool_to_spec` **tidak perlu berubah**.
- `call_tool(name, args, timeout=...)` → gunakan `timeout` per-panggilan FastMCP (**hilangkan**
  `asyncio.wait_for` di executor untuk MCP — mencegah kambuhnya bug `CancelledError`).
- Reconnect/backoff: pakai kemampuan FastMCP atau bungkus tipis; jaga perilaku "reconnect saat
  error" seperti sekarang.

**Tahap 3 — Sesuaikan `mcp_result_to_tool_result` (adapter).**
- Hasil FastMCP `call_tool` memberi `.content` (blok teks), `.structured_content`, dan sering
  `.data` (sudah ter-deserialisasi), plus `.is_error`. Petakan ke `ToolResult` seperti sekarang:
  utamakan `structured_content`/`data`; fallback ke gabungan teks; `is_error` → `status="error"`.
- Pertahankan normalisasi status kosong → `empty`.

**Tahap 4 — Sederhanakan `MCPManager` & config.**
- `get_server_configs()`: pilih transport dari config. Tambah `VEROSIGHT_MCP_URL` +
  `VEROSIGHT_MCP_TRANSPORT` (`streamable_http`|`sse`) di `app/config.py` & `.env(.example)`;
  pertahankan `VEROSIGHT_MCP_SSE_URL` untuk kompatibilitas mundur.
- Bila Streamable HTTP: **buang** `asyncio.Lock` (paralelisme aman) dan `_SessionInitRetryTransport`.
- Pertahankan cache 15 mnt, register ke registry, dan degradasi anggun
  (`VEROSIGHT_MCP_KNOWN_TOOLS`) saat server offline.

**Tahap 5 — Bersihkan.** Hapus `_SessionInitRetryTransport` & `httpx2` bila tak terpakai lagi
(khusus jalur Streamable HTTP). Pastikan tak ada sisa import SDK `mcp` yang tidak dipakai
(kecuali `mcp.types.Tool` bila masih dipakai adapter).

---

## 6. Penegasan Metadata (permintaan pengguna)

- Tool **tetap** di-*ingest* dari `list_tools()` server Verosight. `ToolSpec.description` &
  `.parameters` **berasal dari Verosight**, bukan tulisan tangan.
- `GET /v1/tools` **harus identik** (nama, deskripsi, schema, jumlah ~20 tool `verosight__…`)
  sebelum vs sesudah migrasi. Ini kriteria lulus utama (§8).

---

## 7. Strategi Pengujian (§1.6 — tanpa mock jaringan)

- **Logika murni:** `mcp_result_to_tool_result` dengan objek hasil FastMCP contoh (dibangun dalam
  test) → `ToolResult` benar (success/empty/error, structured vs text).
- **Live (marker `live`):** connect ke Verosight via FastMCP → `list_tools()` mengembalikan ~20
  tool; satu `call_tool` sukses; `GET /v1/tools` identik dengan baseline.
- **Regresi transport:** bila Streamable HTTP → tidak ada `session_not_found` di log; bila SSE →
  perilaku setara sebelumnya.
- **Paralelisme (bila Streamable HTTP):** 3 tool `parallel_tools=true` berjalan konkuren tanpa
  error, lebih cepat dari serial.

---

## 8. Verifikasi Akhir

- [ ] `GET /v1/tools` **identik** (metadata Verosight tak berubah; tak ada deskripsi manual).
- [ ] Satu run `POST /v1/agent/query` sukses memakai tool Verosight via FastMCP.

**Target khusus — race sesi SSE `session_not_found` + eksekusi tool serial (kriteria lulus utama):**
- [ ] **(Jalur Streamable HTTP — §4A.1):** log satu run multi-tool **tidak memuat** satu pun
      `mcp.session_not_found_delay_detected`; `asyncio.Lock` & `_SessionInitRetryTransport`
      **terhapus**; ketiga tool (`get_volume`/`get_sentiment`/`get_topics`) berjalan **paralel**
      (bukan serial); total latensi tool turun signifikan dibanding baseline SSE saat ini.
- [ ] **(Jalur SSE-only — §4A.2):** frekuensi `session_not_found` **berkurang** (sesi persisten +
      warm-up) dan tetap tidak fatal; **terdokumentasi** bahwa penghapusan penuh butuh Streamable
      HTTP (tiket/eskalasi ke Verosight dibuat).
- [ ] Latensi `get_topics` yang tersisa dikenali sebagai **payload besar** (bukan MCP) dan dirujuk
      ke `docs/plan-fix-truncated-answer-fallback-summary.md` — di luar cakupan plan ini.
- [ ] Tidak ada `asyncio.wait_for` membungkus panggilan MCP (pakai timeout per-call FastMCP).
- [ ] Degradasi anggun tetap: server MCP mati → startup sukses, tool `available=false`.
- [ ] Rahasia (`X-API-Key`) tetap teredaksi di log/trace.
- [ ] Rollback teruji: `docs/Old-MCP_revert-ke-mcp-awal.md` mengembalikan perilaku lama.

---

## 9. Risiko & Catatan

- **RS1 — Verosight mungkin SSE-only.** Bila begitu, manfaat utama (session/paralelisme) tak
  didapat; migrasi tinggal soal ergonomi. Putuskan di Tahap 0.
- **RS2 — FastMCP di atas anyio yang sama.** Semantik pembatalan tak hilang ajaib; kuncinya
  memakai timeout per-call FastMCP, bukan `asyncio.wait_for`.
- **RS3 — Perbedaan API antar-versi FastMCP.** Cocokkan dengan versi terpasang; jangan berasumsi.
- **RS4 — Tidak menyelesaikan** masalah "(terpotong)"/respons kosong (app/LLM-side) — lihat
  `docs/plan-fix-truncated-answer-fallback-summary.md` & `docs/plan-fix-empty-synthesis-and-fallback.md`.
- **Rujukan:** `docs/PROMPT_PLAN.md` (§1.3 kontrak, §1.6, §6), `docs/plan-perubahan-cara-kerja.md`
  (metadata resmi), **`docs/Old-MCP_revert-ke-mcp-awal.md`** (rollback).
- **Batasan tugas:** dokumen rencana; eksekusi (edit kode, uji, commit) pada tahap terpisah.
