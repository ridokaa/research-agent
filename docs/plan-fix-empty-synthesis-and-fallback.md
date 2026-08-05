# Rencana Perbaikan — Respons Kosong saat Sintesis (chat mode) & Fallback yang Membuang Data

> **⚠️ SUPERSEDED (2026-08-04).** Digantikan oleh **`docs/plan-fix-synthesis-right-sizing.md`**,
> yang mengonsolidasikan masalah ini beserta penyebab **baru** (ukuran konteks ~45k token) dan
> masalah "(terpotong)". Dokumen ini disimpan sebagai riwayat: TASK A1 (urutan pesan chat) **sudah
> diterapkan** (`app/agent/loop.py:289`); penyebab tersisa (ukuran konteks) ditangani di dokumen
> pengganti. **Untuk implementasi, ikuti dokumen pengganti.**

> **Dokumen rencana untuk agen eksekutor.** Dua bug yang membuat agen **membuang data yang sudah
> berhasil dikumpulkannya sendiri** lalu menjawab "tidak ada data". Audit pada branch
> `retrofit/chat-completions-adapter` (2026-08-03). Patuhi aturan global `docs/PROMPT_PLAN.md`
> §1.6 (tanpa mock, transparansi error) dan arsitektur **style-agnostic** (perbedaan gaya API
> hanya di `app/llm/`).

---

## 1. Ringkasan

Untuk query *"Apakah topik perang sering dibicarakan akhir-akhir ini?"* (run `3d484b7b...`),
agen **berhasil** memanggil 3 tool Verosight via MCP dan mendapat data nyata — **tetapi**
jawaban akhirnya menyatakan *"belum ada data tool yang berhasil diperoleh"* dan menyarankan
Google Trends/NewsAPI. **MCP tidak bermasalah.** Ada dua bug di sisi loop/LLM chat:

- **Bug A:** saat sintesis (iterasi lanjutan) di gaya `chat_completions`, riwayat yang dikirim
  ulang **tidak menyertakan pesan `assistant` ber-`tool_calls`** sebelum hasil tool → urutan
  pesan tidak valid → deepseek membalas **kosong** → loop masuk fallback.
- **Bug B:** fallback finalizer **tidak setia pada `execution_memory`** — menyangkal data yang
  sudah ada di memori dan menjawab "tidak ada data".

---

## 2. Bukti (dari trace run `3d484b7b`)

MCP & tool bekerja benar (iterasi 1):

| Tool (source=mcp) | Argumen | Status |
|---|---|---|
| `verosight__get_keyword_volume` | `keyword="perang", days=30` | success — `by_day` (mis. 2026-07-04: 78 post) |
| `verosight__get_sentiment` | `query="perang", days=14` | success — total 100, positif 63 |
| `verosight__semantic_search` | `query="perang konflik militer"` | success — daftar post |

Iterasi 2 (sintesis) gagal:
```
[iter 2] context_built   memory_items_count=3          ← data ADA di memori
[iter 2] llm_response
[iter 2] thinking  "LLM returned empty response (no tool calls, no output text); breaking to finalizer"
[iter 2] llm_request (fallback) → final_answer 917 chars  ← "tidak ada data"
```

---

## 3. Akar Masalah A — urutan pesan chat tidak valid saat sintesis

Terkonfirmasi di kode:
- `app/agent/planner.py` (jalur `store=false`, iterasi lanjutan): membangun
  `history_items = [{role:"user", user_query}] + pending_inputs` lalu `compact_input`.
- `app/agent/loop.py:310,345`: `pending_inputs` **hanya** berisi item `function_call_output`
  (via `to_function_call_output`). **Tidak ada** pesan `assistant` ber-`tool_calls`.
- `app/llm/schemas.py::to_chat_messages` (baris 109): item `function_call_output` → pesan
  `{"role":"tool","tool_call_id",...}`. Pesan `assistant` ber-`tool_calls` hanya dibuat dari item
  bertipe `"function_call"` atau `"message"+tool_calls` — yang **tidak ada** di `pending_inputs`.

Akibatnya array pesan ke deepseek = **`[system, user, tool, tool, tool]`** — pesan `role:"tool"`
tanpa `assistant` ber-`tool_calls` yang mendahuluinya. Ini **melanggar kontrak Chat Completions**
(setiap `tool` harus mengikuti `assistant` yang memuat `tool_calls` dengan `id` yang cocok).
9Router/deepseek meresponsnya dengan **turn kosong** — lebih sering kambuh saat **banyak tool
paralel** (3 hasil tool di sini). *(Di gaya `responses`, `function_call_output` saja sah karena
state/urutan ditangani berbeda — jadi perbaikan harus diserap di `app/llm/`, bukan mengubah loop
jadi sadar-gaya.)*

---

## 4. Akar Masalah B — fallback finalizer menyangkal data yang ada

- `app/agent/loop.py:431-445`: finalizer memanggil
  `mem_block = state.execution_memory.get_context_block(...)` lalu
  `planner.build_fallback_prompt(user_query, mem_block)` → `llm.respond(...)`.
- Meski `memory_items_count=3`, jawaban fallback menyatakan "tidak ada data". Artinya salah satu:
  (i) `mem_block` sampai **kosong** ke prompt (bug `get_context_block`/perakitan), atau
  (ii) `mem_block` terisi tetapi **model mengabaikannya** (framing prompt fallback + respons
  kosong sebelumnya membuat model menyimpulkan tak ada data).
- Finalizer **wajib** menyusun jawaban dari data yang sudah terkumpul; menyangkal data yang ada
  melanggar prinsip "jangan mengarang, tapi juga jangan membuang fakta yang terverifikasi".

---

## 5. Investigasi (WAJIB dulu — jangan menambal buta)

**I1 — Rekam array `messages` yang dikirim saat sintesis gagal.**
Tambahkan logging sementara (atau inspeksi payload `llm_request` di trace) untuk mencetak `messages`
yang dibangun `to_chat_messages` di iterasi 2. Konfirmasi urutannya
`[system, user, tool, ...]` tanpa `assistant(tool_calls)`. (Membuktikan Bug A.)

**I2 — Cek nilai `mem_block` di titik fallback.**
Log `len(mem_block)` dan isinya sebelum `build_fallback_prompt`. Tentukan apakah B disebabkan
(i) mem_block kosong atau (ii) diabaikan model. (Menentukan sub-fix B.)

**I3 — Reproduksi deterministik** query multi-tool (mis. "perang" / "nuklir") dengan
`store:false` untuk memicu ulang.

---

## 6. Rencana Perbaikan

### TASK A1 — Sisipkan pesan `assistant` ber-`tool_calls` di serialisasi chat (akar A)
**File:** `app/llm/schemas.py` (dan/atau `app/agent/loop.py` untuk representasi netral)
**Tujuan:** array pesan chat harus valid: setiap kelompok hasil tool didahului satu pesan
`assistant` dengan `tool_calls` yang `id`-nya cocok dengan `tool_call_id` hasil.
**Opsi implementasi (jaga style-agnostic — serap di `app/llm/`):**
- **Opsi 1 (disarankan):** perkaya representasi netral. Saat model meminta tool, loop menyimpan
  daftar `function_call` (id, name, arguments). Di iterasi lanjutan, sertakan item netral
  `type="function_call"` (assistant request) **sebelum** `function_call_output` untuk tiap tool.
  `to_chat_messages` sudah bisa merender `function_call` → `assistant.tool_calls`, dan
  `function_call_output` → `tool`. Untuk gaya `responses`, item `function_call` juga sah.
- **Opsi 2:** di `to_chat_messages`, saat menemui rentetan `function_call_output` tanpa
  `assistant` pendahulu, **rekonstruksi** satu pesan `assistant` ber-`tool_calls` dari metadata
  panggilan (butuh name/arguments — jadi loop tetap harus meneruskannya; murni dari output saja
  tidak cukup).
**DoD:** array pesan chat untuk iterasi lanjutan = `[system, user, assistant(tool_calls),
tool, tool, tool, ...]` yang valid; deepseek tidak lagi membalas kosong pada kasus multi-tool.

### TASK A2 — Tangani respons kosong dengan retry sintesis berbasis memori (jaring pengaman)
**File:** `app/agent/loop.py`
**Tujuan:** bila LLM tetap membalas kosong saat sintesis, jangan langsung menyerah ke fallback
generik; lakukan **satu** panggilan sintesis eksplisit yang memuat `execution_memory` (mirip
fallback yang benar di TASK B1). Ini juga menutup kambuhnya bila A1 belum sempurna.
**DoD:** respons kosong + memori berisi data → agen tetap menghasilkan jawaban berbasis data.

### TASK B1 — Finalizer/fallback WAJIB berbasis `execution_memory` (akar B)
**File:** `app/agent/loop.py`, `app/agent/planner.py`
**Perubahan:**
- Pastikan `mem_block` benar-benar terisi saat ada item sukses (perbaiki `get_context_block` bila
  I2 menunjukkan kosong; pertimbangkan sertakan `value`, bukan hanya `summary` ringkas, agar angka
  konkret ikut).
- Perkuat `build_fallback_prompt` (`app/agent/prompts/*` atau string di planner) agar **eksplisit**:
  "Berikut data tool yang SUDAH terkumpul: {data}. Susun jawaban berdasarkan data ini. JANGAN
  menyatakan tidak ada data bila data di atas tidak kosong."
- Bila `execution_memory` punya item sukses, status akhir **tidak boleh** menjadi jawaban
  "tidak ada data".
**DoD:** untuk run dengan tool sukses, jawaban akhir merangkum data nyata (volume/sentimen/contoh),
bukan menyangkalnya.

> Prioritas: **A1** (hentikan respons kosong) → **B1** (finalizer setia data) → **A2** (jaring
> pengaman) → I1/I2 mendahului semua.

---

## 7. Strategi Pengujian (deterministik, tanpa mock jaringan — §1.6)

- **T-A1 (serialisasi):** beri urutan netral `[user, function_call(x3), function_call_output(x3)]`
  → `to_chat_messages` menghasilkan `[system?, user, assistant(tool_calls x3), tool x3]` dengan
  `tool_call_id` cocok. Assert tidak ada `tool` tanpa `assistant` pendahulu.
- **T-A2/B1 (orkestrasi):** `AgentLoop` dengan LLM stub in-proc yang (1) di iterasi 1 meminta tool
  lokal deterministik, (2) di iterasi 2 mengembalikan **respons kosong**. Assert: jawaban akhir
  **memuat** angka dari hasil tool (dari execution memory), status `success`, bukan "tidak ada data".
- **T-B1 (memori):** `get_context_block` dengan 3 item sukses (iter 1, current_iter 2)
  mengembalikan blok **non-kosong** memuat nilai kunci.
- **Live (marker `live`, opsional):** query "perang"/"nuklir" ke deepseek `store:false` → jawaban
  merangkum data, bukan menyangkal.

---

## 8. Verifikasi Akhir

- [ ] I1 mengonfirmasi urutan pesan lama cacat; setelah A1, urutan valid (`assistant(tool_calls)`
      sebelum `tool`).
- [ ] Query multi-tool (perang/nuklir) `store:false` → deepseek **tidak** balas kosong saat sintesis.
- [ ] Jawaban akhir untuk run dengan tool sukses **merangkum data nyata**, bukan "tidak ada data".
- [ ] `execution_memory` yang berisi item sukses tidak pernah menghasilkan jawaban penyangkalan.
- [ ] Regresi gaya `responses` (OpenAI): serialisasi & alur tetap benar (item `function_call`
      juga sah di responses).
- [ ] MCP tetap terpakai benar (tidak ada perubahan di jalur MCP); rahasia teredaksi.

---

## 9. Risiko & Catatan

- **RS1 — Style-agnostic.** Perbaikan serialisasi harus di `app/llm/` + representasi netral;
  jangan menaruh `if style==...` di loop. Item `function_call` sah untuk kedua gaya.
- **RS2 — Kompaksi riwayat.** `compact_input(keep_last_n=10)` bisa memotong pasangan
  `assistant(tool_calls)`+`tool`; pastikan kompaksi tidak memisahkan pasangan (jaga integritas
  pasangan saat memangkas).
- **RS3 — Overlap dokumen.** Bug "respons kosong → status running → 500" dibahas di
  `docs/plan-fix-runresult-status-500.md`; plan ini melengkapinya dengan **penyebab** respons
  kosong (A) dan **kesetiaan fallback pada data** (B). Kerjakan selaras.
- **RS4 — Verosight bukan sumber cuaca/geo keras** (batasan yang sudah diketahui) — tak relevan di
  sini karena datanya memang berhasil terkumpul.
- **Batasan tugas:** dokumen rencana; eksekusi (edit kode, uji, commit) pada tahap terpisah.

---

## 10. Jawaban Ringkas atas Pertanyaan Pemicu

- **Verosight punya tool untuk pertanyaan itu?** Ya — dan agen memakainya (keyword_volume,
  sentiment, semantic_search).
- **Sistem sudah pakai MCP dengan benar?** Ya — 3 tool MCP dipanggil, semua sukses dengan data
  nyata. Kegagalan ada di **sintesis/finalisasi** (Bug A & B), bukan di MCP.
