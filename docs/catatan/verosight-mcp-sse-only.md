# Referensi — Verosight MCP hanya SSE (`session_not_found` tidak bisa diperbaiki dari klien)

Catatan referensi hasil investigasi (2026-08-04). Simpan sebagai acuan agar tidak menyelidiki
ulang.

## Masalah

Di terminal muncul berulang saat menjalankan tool MCP:
```
warning mcp.session_not_found_delay_detected  "Server session propagation pending. Retrying POST..."
        url='https://api.verosight.com/v1/mcp/message?sessionId=...'
info    mcp.session_init_retry_success attempt=2 status=202
```
Muncul **sebelum tiap** panggilan tool, dan tool berjalan **serial** (karena `asyncio.Lock` per
sesi). Nama masalah: **race propagasi sesi SSE** (`session_not_found`).

## Akar masalah

Transport **SSE** memakai pola dua-endpoint: buka stream (`GET /v1/mcp/sse`) untuk dapat
`sessionId`, lalu POST pesan ke `/v1/mcp/message?sessionId=...`. Ada race: POST tiba sebelum server
Verosight selesai mendaftarkan sesi → 404 "session not found". Kode meng-retry (`_SessionInitRetryTransport`)
hingga sukses. **Race ini di sisi server Verosight, bukan di klien.**

## Bukti probe (2026-08-04) — Verosight SSE-only

Probe HTTP mentah ke kandidat endpoint MCP:

| Endpoint | POST | GET |
|---|---|---|
| `…/v1/mcp` (Streamable HTTP) | **404** | **404** |
| `…/v1/mcp/http` (Streamable HTTP) | **404** | **404** |
| `…/v1/mcp/sse` (SSE) | 404 | **200 · `text/event-stream`** |

Probe `fastmcp.StreamableHttpTransport` ke `/v1/mcp`, `/v1/mcp/http` → `McpError('Session terminated')`
(karena endpoint 404). **Kesimpulan: Verosight hanya menyediakan transport SSE; TIDAK ada
Streamable HTTP.**

## Konsekuensi

- **`session_not_found` TIDAK dapat dihilangkan dari sisi klien** (FastMCP maupun SDK `mcp` lama)
  selama transport = SSE. Migrasi ke FastMCP (sudah dilakukan, `fastmcp 3.4.5`, `app/mcp/client.py`
  memakai `fastmcp.Client`) **tidak** menolong karena jalur Streamable HTTP tak tersedia.
- Revert ke MCP lama juga tidak menolong (sama-sama SSE + retry).
- **Non-fatal:** run tetap `200 OK`, tiap tool `status=success`, `truncated=False`, retry selalu
  `session_init_retry_success`. Yang tersisa hanya **latensi** (retry + serial) dan **noise log**.

## Penyelesaian

1. **Eskalasi ke Verosight (satu-satunya fix nyata):** minta endpoint **Streamable HTTP**
   (penerus SSE). Kode sudah siap: set `VEROSIGHT_MCP_TRANSPORT=streamable_http` + URL-nya, restart →
   warning hilang, `_SessionInitRetryTransport` & `Lock` bisa dibuang, tool bisa paralel.
2. **Sementara:** terima warning (non-fatal); opsional turunkan level log
   `_SessionInitRetryTransport` dari `warning` → `debug` untuk kurangi noise.
3. **(Opsional)** session-pool beberapa sesi SSE untuk paralelisme; tiap sesi tetap kena race init
   sekali (manfaat terbatas).

## Catatan tambahan — tool analytics lambat (server-side)

Bukti log 2026-08-04: `verosight__get_topics` dan `verosight__get_topic_sentiment` **timeout di 30
dtk** walau dipanggil dengan `limit:10`. Karena mengecilkan `limit` tidak menolong, **kelambatan
ada di komputasi server Verosight**, bukan ukuran payload. Implikasi:
- Perbaikannya = **naikkan timeout per-call** untuk tool analytics ini (mis. 30 → 60-90 dtk),
  **bukan** mengecilkan payload. Lihat `docs/plan-fix-mcp-session-churn-mitigation.md` TASK 2.
- Timeout tool ini juga **memicu churn sesi** (klien menutup+reconnect pada setiap error → re-race
  `session_not_found`). Perbaikan churn = jangan tutup sesi saat timeout **tool** (TASK 1 plan yang sama).
- Keputusan: level log `session_not_found` **tetap `warning`** (lebih jujur), tidak diturunkan ke debug.

## Rujukan
- Plan migrasi & penanganan: `docs/MCP-CHANGE_migrasi-clientside-fastmcp.md` (§4A.2 = kondisi saat ini).
- Mitigasi churn & timeout: `docs/plan-fix-mcp-session-churn-mitigation.md`.
- Rollback: `docs/Old-MCP_revert-ke-mcp-awal.md`.
- Config: `app/config.py` (`VEROSIGHT_MCP_TRANSPORT`, `VEROSIGHT_MCP_SSE_URL`); klien: `app/mcp/client.py`.
