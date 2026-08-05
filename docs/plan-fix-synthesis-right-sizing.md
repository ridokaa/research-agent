# Rencana Perbaikan — Right-Sizing Konteks Sintesis (Jalur Normal, Lengkap-Fakta, Tanpa "(terpotong)")

> **Dokumen rencana untuk agen eksekutor.** Tujuan: jawaban lahir dari **jalur sintesis NORMAL**
> (bukan fallback), tetap **lengkap secara fakta** dan **tanpa `*(terpotong)*`**, dengan
> "right-sizing" konteks yang dikirim ke model. Audit branch `retrofit/chat-completions-adapter`
> (2026-08-04). Patuhi `docs/PROMPT_PLAN.md` §1.6.
>
> **Konsolidasi:** dokumen ini **menggantikan** `docs/plan-fix-empty-synthesis-and-fallback.md`
> (penyebab lama: urutan pesan — sudah diperbaiki) dan `docs/plan-fix-truncated-answer-fallback-summary.md`
> (masalah "(terpotong)" dari ringkasan fallback). Keduanya ditandai *Superseded*.

---

## 1. Ringkasan

Untuk query multi-tool, deepseek sering membalas **kosong** saat sintesis → loop jatuh ke
**fallback** yang memakai ringkasan memori terpotong → jawaban dari jalur non-ideal (dan pernah
memuat `*(terpotong)*`). Akar **terbaru** = **ukuran konteks** (bukan lagi urutan pesan, yang sudah
diperbaiki). Solusi: **right-sizing** — kirim ke model **semua FAKTA** (agregat, `by_day`, nama+deskripsi
topik) tetapi **buang NOISE** (isi post mentah `top_posts`, `media_urls`, field redundan). Data
penuh **tetap** di `tool_invocations.result`. Hasil: jalur normal berhasil, jawaban lengkap-fakta,
tanpa "(terpotong)".

---

## 2. Bukti (log run 2026-08-04, `run_id=99da93d4…`)

```
LLM response ... function_calls=0 has_text=False input_tokens=45718 output_tokens=0
LLM returned empty response (no tool calls, no output text); breaking to finalizer
LLM request ... tools=0            ← fallback
Fallback answer generated (status=success)
```
- `input_tokens=45718` → konteks ~45k token (didominasi hasil `get_topics` penuh) → deepseek kosong.
- Ketiga tool **sukses** (`truncated=False`); datanya lengkap di storage — masalahnya **bukan** data
  hilang, melainkan **terlalu besar untuk model**.
- Fix urutan pesan (pesan `assistant` ber-`tool_calls`) **sudah** diterapkan (`app/agent/loop.py:289`)
  → jadi penyebab tersisa = **ukuran**, bukan format.

---

## 3. Akar Masalah (berlapis)

1. **Ukuran konteks (BARU, utama):** dengan `TOOL_RESULT_PASSTHROUGH=true`, seluruh hasil tool
   mentah (terutama `get_topics` + `top_posts`) masuk konteks sintesis → ~45k token → deepseek
   membalas kosong.
2. **Fallback tidak setia fakta / "(terpotong)":** fallback memakai
   `state.execution_memory.get_context_block()` yang merender `summary = raw_str[:200]`
   (`app/agent/state.py:124`) → **memotong array `by_day` di tengah** → model menandai
   `*(terpotong)*` untuk tanggal yang tidak terlihat.
3. **Urutan pesan (LAMA, sudah beres):** dulu hasil tool dikirim tanpa pesan `assistant`
   ber-`tool_calls`. Sudah diperbaiki (`loop.py:289`). Dipertahankan sebagai penjaga regresi.

---

## 4. Prinsip: "Buang Noise, Simpan Fakta" (kunci agar tetap lengkap & tanpa terpotong)

| Dibuang dari konteks model (NOISE) | Dipertahankan (FAKTA) |
|---|---|
| Isi `top_posts`/`top_profiles` mentah, `media_urls`, field redundan/kosong | **Semua** entri `by_day` (tiap tanggal + post/likes/comments/shares/views) |
| Teks post individual yang tidak diminta | **Semua** angka agregat (sentimen total/positif/negatif/netral) |
| | **Semua** nama topik + deskripsi singkat |

- Ini **berbeda** dari ringkasan 200-char yang memotong fakta (penyebab "(terpotong)"). Di sini
  fakta **utuh**; hanya bulk konten mentah yang dibuang → **muat di model & tanpa "(terpotong)"**.
- **Data penuh tetap** di `tool_invocations.result` (audit via `GET /v1/runs/{id}/tools`) — right-sizing
  **hanya** menyentuh yang dikirim ke model, bukan yang disimpan.

---

## 5. Rencana Perbaikan

### TASK 1 — Right-size hasil tool yang masuk konteks sintesis (akar #1)
**File:** `app/agent/loop.py` (pembungkusan `function_call_output`), `app/mcp/adapter.py` (opsional)
**Perubahan:** saat menyusun output tool untuk diumpankan ke model (bukan untuk storage), buang
noise (top_posts/media/field redundan) sambil mempertahankan seluruh fakta (§4). Data penuh tetap
ditulis ke `tool_invocations.result`. Terapkan hanya untuk hasil besar (mis. `get_topics`).
**DoD:** `input_tokens` sintesis turun jauh di bawah ambang empty (mis. <20k); semua fakta tetap ada.

### TASK 2 — Perbaiki `get_context_block`/memory agar fakta utuh (akar #2, hilangkan "(terpotong)")
**File:** `app/agent/state.py`
**Perubahan:** jangan render `summary = raw_str[:200]` yang memotong `by_day`. Simpan/rakit **nilai
faktual** (agregat + seluruh `by_day` + nama topik) untuk blok konteks fallback/self-check; naikkan
`max_tokens` seperlunya. Jika perlu memangkas karena token, potong **noise per-item**, **jangan**
memotong array `by_day`/angka.
**DoD:** blok memori memuat semua tanggal `by_day`; tidak ada pemotongan fakta → tidak ada `*(terpotong)*`.

### TASK 3 — Retry sintesis dengan konteks ramping SEBELUM fallback (akar #1)
**File:** `app/agent/loop.py`
**Perubahan:** bila sintesis normal membalas **kosong**, **ulangi sekali** panggilan sintesis dengan
konteks yang sudah di-right-size (TASK 1/2) — bukan langsung ke fallback generik. Jawaban tetap lahir
dari jalur sintesis (lewat self-check `is_final`).
**DoD:** respons kosong tidak lagi langsung → fallback; jawaban dari jalur normal berbasis fakta.

### TASK 4 — (Hulu, komplementer) kecilkan payload `get_topics` di argumen tool
**File:** `app/agent/prompts/system.md` / argumen default
**Perubahan:** dorong `get_topics`/`get_trending` dipanggil dengan `limit` kecil dan tanpa `top_posts`
bila hanya butuh agregat → mengurangi token di sumber. (Catatan: ini soal **ukuran token**; untuk
**timeout** lihat `docs/plan-fix-mcp-session-churn-mitigation.md` TASK 2 — masalah berbeda.)
**DoD:** payload `get_topics` inheren lebih ramping tanpa kehilangan nama/deskripsi topik.

### TASK 5 — Penjaga regresi: urutan pesan chat (sudah beres)
**File:** `app/agent/loop.py`
Pertahankan penyisipan pesan `assistant` ber-`tool_calls` (`loop.py:289`). Tambahkan test agar tak
regres.

> Prioritas: **TASK 1 + TASK 2** (muat di model + fakta utuh) → **TASK 3** (jalur normal) →
> **TASK 4** (hulu) → **TASK 5** (regresi).

---

## 6. Jaminan yang Diminta Pengguna (lengkap-fakta, tanpa terpotong)

- **Data tersimpan (`/tools`):** penuh, tak tersentuh. ✅
- **`answer`:** tetap sintesis bahasa alami (by design) — untuk data mentah gunakan `/tools`.
- **Kelengkapan fakta di `answer`:** semua `by_day`/agregat/topik ada → tidak ada `*(terpotong)*`. ✅
- **Yang tidak mungkin (batas model):** menjejalkan seluruh byte mentah (45k+) ke model → balas
  kosong. Right-sizing menghindari ini **tanpa** mengorbankan fakta.

---

## 7. Strategi Pengujian (deterministik — §1.6)

- **T1:** hasil tool dengan `by_day` 30 tanggal + `top_posts` besar → fungsi right-size (TASK 1)
  membuang `top_posts` tetapi **mempertahankan seluruh 30 `by_day`** + agregat.
- **T2:** `get_context_block` dengan 30 `by_day` → blok memuat **semua** tanggal (tak ada potong tengah).
- **T3 (orkestrasi):** stub LLM yang balas kosong pada input besar, lalu balas teks pada input ramping
  → loop me-retry dengan konteks ramping (TASK 3) dan menghasilkan jawaban memuat semua fakta;
  **bukan** fallback generik.
- **T4:** `tool_invocations.result` tetap = data penuh (right-size tak menyentuh storage).

---

## 8. Verifikasi Akhir

- [ ] Run multi-tool → jawaban dari **jalur normal** (trace: bukan `breaking to finalizer`).
- [ ] `input_tokens` sintesis turun jauh (<20k) — tidak lagi memicu empty.
- [ ] `answer` memuat **semua** fakta (semua tanggal `by_day`, agregat, topik) — **tanpa** `*(terpotong)*`.
- [ ] `GET /v1/runs/{id}/tools` tetap menampilkan data penuh (`truncated=False`).
- [ ] Regresi: urutan pesan chat tetap valid; query kecil tetap benar.

---

## 9. Risiko & Catatan

- **RS1 — Jangan buang fakta.** Batas tegas: right-size hanya membuang noise (post mentah/media/
  field redundan). Uji T1/T2 adalah pengaman agar `by_day`/agregat/topik tak ikut terpotong.
- **RS2 — Passthrough storage tak berubah.** Right-sizing eksklusif untuk konteks model.
- **RS3 — Ambang empty bergantung model/gateway.** Target `input_tokens` aman (<20k) empiris;
  sesuaikan bila deepseek/9Router berubah.
- **Rujukan:** `docs/PROMPT_PLAN.md` §1.6 & §6; `docs/plan-perubahan-cara-kerja.md` (pass-through &
  batasan dari sumber); `docs/plan-enrich-answer-search-tools.md` (memperkaya data — sinergi:
  data lebih banyak → right-sizing makin penting).
- **Superseded oleh dokumen ini:** `docs/plan-fix-empty-synthesis-and-fallback.md`,
  `docs/plan-fix-truncated-answer-fallback-summary.md`.
- **Batasan tugas:** dokumen rencana; eksekusi (edit kode, uji, commit) pada tahap terpisah.
