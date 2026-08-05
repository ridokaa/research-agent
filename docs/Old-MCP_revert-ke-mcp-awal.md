# [Old MCP] Kembalikan Proyek ke Klien MCP Awal (SDK `mcp` + SSE)

> **⚠️ Ini perubahan pada lapisan MCP — rencana ROLLBACK.** Dokumen rencana untuk agen eksekutor.
> Tujuan: mengembalikan proyek ke **klien MCP awal** (SDK `mcp` mentah + transport SSE), dengan
> **pengaturan yang sama seperti sebelumnya**, membatalkan migrasi FastMCP
> (`docs/MCP-CHANGE_migrasi-clientside-fastmcp.md`). Audit branch
> `retrofit/chat-completions-adapter` (2026-08-04). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Ringkasan

Bila migrasi ke `fastmcp.Client` menimbulkan masalah (mis. Verosight ternyata SSE-only, regresi,
atau perilaku tak diinginkan), plan ini mengembalikan lapisan MCP ke **kondisi awal** yang sudah
teruji: SDK `mcp` (`ClientSession` + `sse_client`) dengan `_SessionInitRetryTransport` dan
`asyncio.Lock`, transport **SSE** ke `VEROSIGHT_MCP_SSE_URL`, mode `VEROSIGHT_MODE=mcp`. Tool tetap
di-*ingest* dari `list_tools()` Verosight (metadata resmi, tanpa deskripsi manual).

---

## 2. Definisi "MCP Awal" (baseline yang dipulihkan)

Kondisi MCP awal = keadaan file & config **sebelum** migrasi FastMCP:

- `app/mcp/client.py` — `MCPClient` berbasis SDK `mcp`:
  `from mcp import ClientSession`, `from mcp.client.sse import sse_client`,
  `from mcp.client.stdio import ...`; kelas `_SessionInitRetryTransport` (retry 404
  "session not found"); `asyncio.Lock` per klien; method `connect()`/`list_tools()`/`call_tool()`
  (kembalikan `mcp.types.CallToolResult`)/`close()`.
- `app/mcp/adapter.py` — `mcp_tool_to_spec`, `_normalize_responses_schema`,
  `mcp_result_to_tool_result` (memproses `CallToolResult`/content blocks), `MCPTool`.
- `app/mcp/manager.py` — `MCPManager`: `get_server_configs()` default Verosight `transport="sse"`,
  `url=VEROSIGHT_MCP_SSE_URL`, header `X-API-Key`; `connect_all()` cache 15 mnt + register ke
  registry + degradasi anggun (`VEROSIGHT_MCP_KNOWN_TOOLS`).
- `app/config.py` / `.env` — `VEROSIGHT_MCP_SSE_URL=https://api.verosight.com/v1/mcp/sse`,
  `VEROSIGHT_MODE=mcp`, `VEROSIGHT_ENABLED=true`, `VEROSIGHT_API_KEY=<key>`, `MCP_SERVERS_JSON=""`.
- Dependensi: **tanpa** `fastmcp`.

> **Referensi git:** kondisi awal ini ada pada commit terakhir sebelum migrasi FastMCP. Sebelum
> memulai migrasi, catat hash-nya (mis. `git rev-parse HEAD` → simpan sebagai
> `OLD_MCP_BASELINE`). Plan ini memakainya untuk pemulihan presisi.

---

## 3. Cara Revert (pilih salah satu)

### Opsi A — Revert commit migrasi (paling bersih, bila migrasi 1 commit)
```bash
git revert <hash_commit_migrasi_fastmcp>     # membuat commit pembalik, riwayat tetap utuh
```
Bila migrasi tersebar di beberapa commit, revert berurutan (terbaru → terlama) atau gunakan Opsi B.

### Opsi B — Pulihkan file MCP dari baseline (bedah, tak menyentuh perubahan lain)
```bash
# Kembalikan HANYA lapisan MCP + config ke baseline OLD_MCP:
git checkout <OLD_MCP_BASELINE> -- app/mcp/ app/config.py .env.example pyproject.toml
# (.env bukan file tracked — kembalikan nilainya manual di §4)
```
Lalu lepas dependensi FastMCP bila tak dipakai lagi (`pyproject.toml` → hapus `fastmcp`; `uv sync`).

### Opsi C — Manual (bila tanpa git baseline)
Kembalikan isi `app/mcp/client.py`/`adapter.py`/`manager.py` ke bentuk SDK `mcp` (SSE +
`_SessionInitRetryTransport` + `Lock`) sesuai §2, dan config §4. Gunakan hanya bila A/B tak mungkin.

---

## 4. Pengaturan yang Dikembalikan (sama seperti sebelumnya)

`.env` (dan `.env.example`) — bagian Verosight/MCP:
```dotenv
VEROSIGHT_ENABLED=true
VEROSIGHT_MODE=mcp
VEROSIGHT_API_KEY=<kunci Verosight Anda>
VEROSIGHT_MCP_SSE_URL=https://api.verosight.com/v1/mcp/sse
MCP_SERVERS_JSON=
```
- Hapus variabel yang ditambahkan migrasi (mis. `VEROSIGHT_MCP_URL`, `VEROSIGHT_MCP_TRANSPORT`)
  bila ada, agar tak ada sisa konfigurasi Streamable HTTP.
- `LLM_*` dan konfigurasi lain **tidak** diubah oleh plan ini.

---

## 5. Verifikasi Akhir (perilaku = kondisi awal)

- [ ] `app/mcp/` kembali memakai SDK `mcp` (`ClientSession`/`sse_client`); tak ada import `fastmcp`.
- [ ] `pip show fastmcp` tidak lagi diperlukan (dependensi dilepas bila diinginkan).
- [ ] `make dev` → startup sukses; `GET /health/deps` → `mcp: ok`.
- [ ] `GET /v1/tools` menampilkan ~20 tool `verosight__…` dengan **metadata Verosight** (identik
      dengan sebelum migrasi — deskripsi tetap dari `list_tools()`, bukan manual).
- [ ] Satu run `POST /v1/agent/query` sukses memakai tool Verosight.
- [ ] **Diharapkan muncul kembali:** warning `mcp.session_not_found_delay_detected` + retry sukses,
      dan tool berjalan **serial** (karena `Lock`) — ini normal untuk MCP awal (bukan regresi).
- [ ] Rahasia (`X-API-Key`) tetap teredaksi.

---

## 6. Risiko & Catatan

- **RS1 — `.env` tak di-track git** → nilai Verosight/MCP harus dikembalikan manual (§4).
- **RS2 — Kehilangan manfaat migrasi** (bila sempat dapat): `session_not_found` & serial-Lock
  kembali. Ini konsekuensi yang disengaja dari rollback.
- **RS3 — Konsistensi dependensi:** bila FastMCP dilepas, pastikan tak ada modul lain yang masih
  meng-import-nya.
- **RS4 — Catat baseline lebih awal.** Simpan `OLD_MCP_BASELINE` **sebelum** migrasi agar Opsi A/B
  presisi; tanpa itu, revert jadi manual (Opsi C) dan rawan luput.
- **Rujukan:** `docs/MCP-CHANGE_migrasi-clientside-fastmcp.md` (perubahan yang dibalik),
  `docs/PROMPT_PLAN.md` (§1.3 kontrak MCP, §6 changelog).
- **Batasan tugas:** dokumen rencana; eksekusi (revert kode/config, uji, commit) pada tahap terpisah.
