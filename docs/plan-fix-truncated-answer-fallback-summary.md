# Rencana Perbaikan — Jawaban Masih "(terpotong)" Meski `TOOL_RESULT_PASSTHROUGH=true`

> **⚠️ SUPERSEDED (2026-08-04).** Digantikan oleh **`docs/plan-fix-synthesis-right-sizing.md`**,
> yang mengonsolidasikan masalah "(terpotong)" ini bersama akar empty-synthesis (ukuran konteks).
> Prinsip perbaikannya sama — "buang noise, simpan semua fakta (`by_day`/agregat/topik)" — kini
> dirumuskan lengkap di dokumen pengganti. Dokumen ini disimpan sebagai riwayat. **Untuk
> implementasi, ikuti dokumen pengganti.**

> **Dokumen rencana untuk agen eksekutor.** Menjelaskan mengapa jawaban akhir masih menandai
> data `*(terpotong)*` walau data tool sebenarnya lengkap, dan cara memperbaikinya. Audit pada
> branch `retrofit/chat-completions-adapter` (2026-08-04), berbasis run nyata
> `9fe0d2e8-1821-4592-a64b-6707f6caf3f8`. Patuhi aturan global `docs/PROMPT_PLAN.md` §1.6 dan
> arsitektur **style-agnostic**.

---

## 1. Ringkasan

Untuk query *"Apakah topik perang sering dibicarakan akhir-akhir ini?"*, jawaban 200 OK tetapi
memuat sel `*(terpotong)*` (mis. 30 & 31 Juli) dan menyebut *"beberapa baris data terpotong di log
eksekusi"*. **Data tool sebenarnya LENGKAP** (passthrough bekerja). Pemotongan terjadi di lapisan
lain: jawaban diproduksi oleh **fallback finalizer** (karena LLM balas kosong saat sintesis
normal), dan fallback memakai **ringkasan 200 karakter per item** dari execution memory
(`get_context_block` + `summary[:200]`) — bukan data tool penuh. Pemicu jalur fallback: hasil tool
`get_topics` yang sangat besar (~142 KB) membuat sintesis normal gagal (respons kosong).

---

## 2. Bukti (run `9fe0d2e8`)

- **Data tool lengkap** (`GET /v1/runs/{id}/tools`): `truncated_flag=False`, tanggal 30 & 31 Juli
  **ada** di hasil mentah `verosight__get_volume` & `get_sentiment_trend`. `get_topics` = **142.550
  karakter**.
- **Jawaban lewat fallback** (trace iterasi 2):
  ```
  [iter 2] llm_response
  [iter 2] thinking  "LLM returned empty response (no tool calls, no output text); breaking to finalizer"
  [iter 2] llm_request (fallback) → final_answer 2227 chars
  ```
- **Ringkasan memori terpotong** (trace `memory_write`): `summary` dipotong di ~200 char di tengah
  data, mis. `...by_day':[{'date':'2026-07-31','posts':895,'comments':59},{'date':'2026-08-01','posts':621,'commen` ← terputus.

---

## 3. Akar Masalah

**A. Titik pemotongan yang tersisa (bukan dikendalikan `TOOL_RESULT_PASSTHROUGH`):**
- `app/agent/state.py:124` — `summary = raw_str[:200]` memotong ringkasan tiap item memori ke 200 char.
- `app/agent/state.py:150` — `get_context_block(max_tokens=2000, ...)` membatasi total blok konteks.
- `app/agent/loop.py` (fallback finalizer, ~baris 433) & self-check finalize (~baris 368) memakai
  `get_context_block(max_tokens=2000)` → model fallback hanya melihat ringkasan terpotong →
  mengisi nilai yang hilang dengan `*(terpotong)*`.

> `TOOL_RESULT_PASSTHROUGH` hanya mematikan `_truncate_result` untuk `function_call_output` yang
> diumpankan balik. Ringkasan execution-memory adalah **jalur truncation terpisah**.

**B. Pemicu jalur fallback (respons kosong saat sintesis normal):**
- `get_topics` mengembalikan ~142 KB (20 topik lengkap dengan `top_profiles`/`top_posts`). Saat 4
  hasil tool (termasuk yang 142 KB) dikirim ulang sebagai riwayat chat, konteks membengkak →
  deepseek balas kosong → loop masuk fallback (lihat `docs/plan-fix-empty-synthesis-and-fallback.md`).

---

## 4. Kenapa "biarkan full tanpa dipotong" BUKAN solusi bersih

Menghapus semua pemotongan (ringkasan 200 char + `max_tokens`, memaksa data penuh) memang
menghilangkan `*(terpotong)*`, **tetapi**:
1. **Input membengkak** — `get_topics` 142 KB dikirim tiap iterasi → token input meledak → biaya
   naik & latensi lebih lama.
2. **Risiko melampaui context window** deepseek → provider memotong input dari sisinya / error →
   ironisnya memunculkan "terpotong"/kosong lagi **karena** kebanyakan data.
3. **Sebagian besar noise** — banyak topik `get_topics` tak relevan dengan kueri. Menjejalkan
   semuanya tak menaikkan kualitas sebanding biayanya.

Kesimpulan: perbaikannya bukan "hapus semua batas", melainkan **memperbaiki jalur sintesis +
merampingkan data di hulu + membuat fallback setia pada data**.

---

## 5. Rencana Perbaikan

### TASK 1 — Fallback & self-check pakai data faktual, bukan ringkasan 200 char (akar A)
**File:** `app/agent/state.py`, `app/agent/loop.py`, `app/agent/planner.py`
**Perubahan:**
- Di `MemoryItem`, simpan/rendam **nilai kunci faktual** (angka agregat, `by_day`, dsb.), bukan
  hanya `raw_str[:200]`. Naikkan/lengkapi ringkasan agar tidak memotong array data numerik di tengah.
- Sediakan varian konteks "kaya" untuk finalisasi: fallback (`loop.py` ~433) & self-check
  (`loop.py` ~368) memakai blok yang memuat **data penuh item sukses** (atau `MemoryItem.value`),
  dengan `max_tokens` yang lebih besar & dapat dikonfigurasi — bukan `summary[:200]`.
- Bila memotong tetap perlu (batas token), potong **per-item secara sadar** (mis. ringkas `top_posts`)
  tetapi **pertahankan angka agregat/`by_day` utuh**, dan tandai eksplisit apa yang diringkas.
**DoD:** jawaban fallback untuk data numerik (mis. `by_day`) tidak lagi menandai `*(terpotong)*`
saat data sebenarnya lengkap.

### TASK 2 — Cegah respons kosong akibat payload besar (akar B; sinergi dengan plan empty-synthesis)
**File:** `app/agent/loop.py`, `app/agent/state.py`
**Perubahan:**
- Batasi ukuran `function_call_output` yang dikirim ulang **secara sadar** untuk tool yang sangat
  besar (mis. `get_topics`) agar riwayat chat tidak membengkak — ringkas bagian noise
  (`top_posts`/`top_profiles`) namun pertahankan `name`+`post_count` tiap topik.
- Pastikan jalur **sintesis normal** (yang memakai `function_call_output`) yang menghasilkan
  jawaban, bukan fallback. Rujuk `docs/plan-fix-empty-synthesis-and-fallback.md` (format pesan chat).
**DoD:** query multi-tool dengan output besar tidak lagi jatuh ke fallback; jawaban dari jalur normal.

### TASK 3 — Rampingkan sumber noise di hulu (paling hemat)
**File:** `app/mcp/adapter.py` / dokumentasi tool, atau via system prompt
**Perubahan:** dorong agar `get_topics`/`get_trending` dipanggil dengan `limit` kecil dan/atau
tanpa `top_posts` bila hanya butuh agregat; atau ringkas hasil MCP besar **secara non-lossy pada
angka** sebelum masuk konteks (bukan memotong buta). Ini menjaga konteks ramping **dan** data
relevan tetap utuh — konsisten dengan `docs/plan-perubahan-cara-kerja.md` (batasan dari sumber,
bukan aplikasi).
**DoD:** payload konteks turun drastis tanpa kehilangan angka yang dipakai menjawab.

> Prioritas: **TASK 1** (hentikan "(terpotong)" palsu) → **TASK 3** (rampingkan hulu) →
> **TASK 2** (jalur normal). TASK 1 sendiri menghilangkan gejala yang terlihat.

---

## 6. Strategi Pengujian (deterministik, tanpa mock jaringan — §1.6)

- **T1 (memori faktual):** buat `ExecutionMemory` dengan `ToolResult` berisi `by_day` 7 tanggal →
  `get_context_block`/blok fallback memuat **semua** tanggal + angka, tanpa terpotong di tengah.
- **T2 (fallback setia data):** `AgentLoop` + LLM stub yang (1) iterasi 1 memanggil tool lokal
  deterministik mengembalikan `by_day` 7 hari, (2) iterasi 2 balas kosong → jawaban fallback memuat
  ke-7 tanggal, **tanpa** kata "terpotong".
- **T3 (payload besar tidak menjatuhkan ke fallback):** simulasikan tool result besar → jalur
  normal tetap menghasilkan jawaban (setelah TASK 2).

---

## 7. Verifikasi Akhir

- [ ] Run query "perang" (`store:false`) → jawaban tidak memuat `*(terpotong)*` saat data tool lengkap.
- [ ] `GET /v1/runs/{id}/tools` tetap menyimpan data penuh (`truncated_flag=False`).
- [ ] Trace: jawaban datang dari **sintesis normal**, bukan fallback (setelah TASK 2).
- [ ] Ukuran konteks turun (get_topics tidak lagi 142 KB masuk mentah) tanpa kehilangan angka agregat.
- [ ] Regresi: query kecil tetap benar; tidak ada 500/empty baru; rahasia teredaksi.

---

## 8. Risiko & Catatan

- **RS1 — Trade-off token.** TASK 1 menaikkan konteks fallback; imbangi dengan TASK 3 (rampingkan
  hulu) agar tidak balik menabrak context window.
- **RS2 — Jangan hapus semua batas** (lihat §4): berisiko context-overflow → truncation/empty dari
  sisi provider.
- **RS3 — Style-agnostic:** perubahan di `state`/`loop`/`mcp`; jangan menaruh logika gaya di loop.
- **Rujukan:** `docs/plan-fix-empty-synthesis-and-fallback.md` (respons kosong & fallback),
  `docs/plan-perubahan-cara-kerja.md` (pass-through & batasan dari sumber),
  `docs/PROMPT_PLAN.md` §1.6 & §6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit kode, uji, commit) pada tahap terpisah.
