# Jawaban Terpotong di Postman — Penyebab & Cara Mengatasinya

Catatan tentang alasan-alasan yang membuat jawaban (answer) dari API
**Agentic Loop** terlihat terpotong/tidak lengkap ketika dijalankan lewat
Postman, serta cara memeriksanya.

## Ringkasan Penyebab

Jawaban bisa terpotong karena dua kelompok besar:

1. **Pembatasan di sisi server/project** — jawaban memang berhenti sebelum
   selesai (token limit, timeout, hasil tool dipotong, fallback).
2. **Pembatasan di sisi Postman** — jawaban sebenarnya lengkap, tapi tampilan
   Postman memotongnya (preview, limit body, SSE).

## 1. Pembatasan di Sisi Server

### a. Limit token output LLM (`LLM_MAX_OUTPUT_TOKENS`)
- Default `2048` token (app/config.py:54).
- Jika model butuh lebih banyak token untuk menjawab lengkap, jawaban
  dipotong pada batas itu. `finish_reason` menjadi `max_output_tokens`
  (app/llm/schemas.py:358-359).
- **Cek**: pada trace / `incomplete_reason` bernilai `max_output_tokens`,
  atau status run `exhausted`.
- **Solusi**: naikkan `LLM_MAX_OUTPUT_TOKENS` di `.env`.

### b. Timeout run (`AGENT_RUN_TIMEOUT_S`)
- Default `180` detik (app/config.py:71).
- Jika run terlalu lama (banyak iterasi / tool lambat), loop dihentikan dan
  jawaban dihasilkan dari mekanisme fallback — biasanya pendek/sebagian.
- **Cek**: `latency_ms` pada respons mendekati/melampaui batas timeout.
- **Solusi**: naikkan timeout, atau kurangi `max_iterations`/`credits_budget`.

### c. Iterasi atau budget habis (`exhausted`)
- Jika `max_iterations` (default 8) atau `credits_budget` (default 100) habis
  sebelum jawaban final lolos self-check, fallback dipakai.
- Fallback memakai memori tool yang sudah ada, dan jika memori tidak cukup
  hanya mengambil `mem_block[:300]` (app/agent/loop.py:456) — jawaban jadi
  pendek/terpotong.
- **Solusi**: tambah budget/iterasi, atau perbaiki prompt agar butuh lebih
  sedikit tool call.

### d. Hasil tool dipotong (`_truncate_result`)
- Hasil tool lebih dari `16000` karakter dipotong dan ditandai
  `[dipotong, N item dari M]` (app/tools/executor.py:48-97).
- LLM menjawab hanya berdasarkan data yang tersisa; jika bagian penting
  terpotong, jawaban akhir tampak tidak lengkap.
- **Cek**: lihat hasil tool pada endpoint `/v1/runs/:run_id/tools` — ada marker
  `truncated: true` / `[dipotong ...]`.

### e. Kompaksi konteks saat `store: false`
- Pada mode `store: false`, riwayat percakapan di-compact ke 10 item terakhir
  (`compact_input`, app/llm/schemas.py:367-404). Konteks awal dihapus.
- Jawaban bisa kehilangan informasi dari langkah awal → tampak terpotong/kurang.
- **Cek**: pastikan `options.store` dan `LLM_STORE`.

### f. Streaming SSE berhenti di tengah
- Endpoint `/v1/agent/query/stream` memecah jawaban per kata dan mengirim
  `data: <kata>` lalu `data: [DONE]` (app/api/routes/agent.py:90-96).
- Jika run gagal/timeout di tengah, tidak ada `[DONE]` dan body yang tertangkap
  Postman hanya sampai kata terakhir yang sempat dikirim → terlihat terpotong.
- **Cek**: apakah body stream diakhiri `data: [DONE]`. Jika tidak, run gagal.

## 2. Pembatasan di Sisi Postman (Bukan Bug)

Jawaban mungkin sebenarnya lengkap, tapi **tampilan** Postman memotongnya:

- **Preview vs Raw**: tab *Pretty/Preview* sering membatasi teks. Pilih tab
  **Raw** untuk melihat teks lengkap.
- **Body terlalu besar**: respons besar hanya ditampilkan sebagian di panel.
  Klik ikon **"Save Response"** untuk melihat/men-download body penuh.
- **SSE**: Postman tidak men-stream real-time di panel utama; body SSE
  biasanya hanya terlihat setelah permintaan selesai, atau lewat **Console**
  (kecilkan panah) jika respons belum selesai. Pastikan melihat setelah
  `[DONE]` muncul.
- **Tabel/pagination di list**: endpoint `GET /v1/runs` dan
  `/v1/conversations` memakai `limit=20&offset=0` — data lebih dari 20 item
  tidak muncul sekaligus, bukan karena terpotong.

## Langkah Diagnosis di Postman

1. **Lihat status code**: 200 tapi body pendek → cek penyebab server (a-e).
2. **Cek field `status`** di respons JSON: `success` normal; `exhausted` /
   `timeout` / `failed` → jawaban pendek/fallback.
3. **Cek `latency_ms`**: mendekati batas timeout → kemungkinan terpotong.
4. **Pergi ke `/v1/runs/:run_id/tools`** → cek apakah ada hasil tool yang
   `truncated` / `[dipotong ...]`.
5. **Lihat trace** `/v1/runs/:run_id/trace` → cari `finish_reason` /
   `incomplete_reason` = `max_output_tokens`.
6. **Streaming**: pastikan body diakhiri `data: [DONE]`.
7. **Tampilan**: buka tab **Raw** atau **Save Response** untuk memastikan body
   bukan hanya terpotong oleh UI Postman.

## Referensi Kode

- `app/config.py:54-56,71-72` — `LLM_MAX_OUTPUT_TOKENS`, `LLM_STORE`, timeout.
- `app/llm/schemas.py:358-359` — `finish_reason` / `incomplete_reason`.
- `app/agent/loop.py:440-467` — fallback finalization saat budget/iterasi habis.
- `app/tools/executor.py:48-97` — `_truncate_result` (hasil tool dipotong).
- `app/llm/schemas.py:367-404` — `compact_input` (konteks di-compact).
- `app/api/routes/agent.py:81-106` — streaming SSE + `[DONE]`.
- `docs/postman/Agentic_Loop_API.postman_collection.json` — koleksi Postman
  (endpoint Runs/Conversations dengan `limit=20`).
