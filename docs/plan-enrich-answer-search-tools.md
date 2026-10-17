# Rencana — Perkaya Cakupan Data Jawaban (Tool Pencarian + Multi-Keyword)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: mengatasi "keterbatasan data" yang bersifat
> **strategi query agen** — bukan error, bukan batasan pipeline. Mendorong agen memakai tool
> pencarian (konten posting) dan query beberapa keyword/pembanding, via penguatan system prompt.
> Audit branch `retrofit/chat-completions-adapter` (2026-08-04). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Ringkasan

Jawaban agen sering menyatakan keterbatasan seperti *"hanya satu kata kunci"*, *"tidak tersedia
konten posting"*, *"tidak ada data pembanding"*. Ini **bukan error** (tool sukses, data valid) dan
**bukan** batasan pipeline — melainkan **cara agen bertanya**: ia hanya memanggil tool **agregat**
(`get_keyword_volume`/`get_sentiment`/`get_topics`) untuk **satu** keyword, tanpa memakai tool
**pencarian konten** (`search_posts`/`semantic_search`) dan tanpa pembanding. Plan ini memperbaiki
**perilaku agen** (prompting) agar jawabannya lebih kaya bila pertanyaan menuntutnya.

**Lapisan:** perilaku agen / prompting. Berbeda dari `docs/plan-fix-synthesis-right-sizing.md`
(pipeline/konteks). Keduanya sinergis (lihat §7).

---

## 2. Bukti (log run 2026-08-04)

- Tool yang dipanggil: `get_keyword_volume`, `get_sentiment`, `get_topics` — semua **agregat**
  (angka & nama topik), untuk keyword tunggal `"nuklir"`. Semua `status=success`.
- `answer` (jujur): *"Data hanya mencakup satu kata kunci … Tidak tersedia konten posting … Tidak
  ada data pembanding dengan topik lain."*
- Tool **tersedia tapi tak dipakai:** `verosight__search_posts`, `verosight__semantic_search`
  (mengembalikan konten posting).

---

## 3. Akar Masalah (perilaku, bukan bug)

- **Tool agregat vs tool konten:** analytics (`get_*`) memberi **angka/topik**, bukan **konten
  posting**. Untuk konteks isi/contoh nyata, perlu `search_posts`/`semantic_search`.
- **Query tunggal tanpa baseline:** label "sering" butuh **pembanding** (baseline/keyword lain) agar
  bermakna relatif. Agen tak melakukannya.
- **Prompt generik:** `app/agent/prompts/system.md` tidak mengarahkan agen memilih tool konten atau
  membuat pembanding saat pertanyaan menuntutnya.

---

## 4. Rencana Perbaikan

### TASK 1 — Panduan pemilihan tool konten (system prompt)
**File:** `app/agent/prompts/system.md`
**Perubahan:** tambah panduan (tanpa menghapus aturan yang ada), mis.:
> - Bila pertanyaan menuntut **contoh/konteks/isi** pembicaraan (bukan sekadar angka), **panggil
>   `search_posts`/`semantic_search`** untuk mengambil konten posting nyata — jangan hanya tool
>   agregat.
> - Gunakan tool agregat (`get_keyword_volume`/`get_sentiment`/`get_topics`) untuk volume/tren/
>   sentimen; lengkapi dengan konten dari tool pencarian bila relevan.

**DoD:** untuk pertanyaan berbasis konteks, trace menunjukkan `search_posts`/`semantic_search` dipanggil.

### TASK 2 — Dorong pembanding/multi-keyword bila "sering/lebih" tersirat
**File:** `app/agent/prompts/system.md`
**Perubahan:** panduan: bila pertanyaan menilai **frekuensi relatif** ("apakah X sering
dibicarakan?"), pertimbangkan **baseline** — mis. bandingkan volume X dengan rata-rata/keyword lain,
atau nyatakan eksplisit bila tak ada pembanding.
**DoD:** jawaban menyertakan pembanding bila tersedia, atau menyatakan keterbatasan pembanding secara
sadar (bukan implisit).

### TASK 3 — (Opsional) Dokumentasikan peran tiap tool
**File:** deskripsi tool (dari Verosight, jangan tulis ulang) / catatan internal
**Perubahan:** ringkas mana tool **agregat** vs **konten** sebagai acuan; jangan mengubah metadata
resmi Verosight (`list_tools()`), cukup panduan di prompt.

> Prioritas: **TASK 1** (tool konten) → **TASK 2** (pembanding) → **TASK 3** (opsional).

---

## 5. Strategi Pengujian (§1.6)

- **Live (marker `live`):** pertanyaan berbasis konteks (mis. "apa yang dibicarakan orang tentang
  nuklir?") → trace menunjukkan `search_posts`/`semantic_search` dipanggil, jawaban memuat konten
  posting nyata.
- **Live:** pertanyaan frekuensi relatif → jawaban menyertakan pembanding atau menyatakan
  keterbatasan pembanding secara eksplisit.
- **Regresi:** pertanyaan agregat murni (volume/sentimen) tetap dijawab efisien tanpa tool berlebih.

---

## 6. Verifikasi Akhir

- [ ] Pertanyaan konteks → `search_posts`/`semantic_search` dipakai; jawaban memuat konten nyata.
- [ ] Pertanyaan "sering/lebih" → ada pembanding atau pernyataan keterbatasan yang sadar.
- [ ] Tidak boros: pertanyaan sederhana tidak memanggil tool berlebihan (jaga kredit/latensi).
- [ ] Metadata tool tetap dari Verosight (tak ada deskripsi manual).

---

## 7. Risiko & Sinergi

- **RS1 — Lebih banyak tool = lebih banyak data = konteks membengkak** → memperbesar risiko
  empty-synthesis. **Kerjakan bersama** `docs/plan-fix-synthesis-right-sizing.md` (right-sizing
  konteks) agar data konten yang lebih kaya tetap muat di model.
- **RS2 — Biaya/kredit & latensi** naik (tool pencarian berbayar, tool analytics lambat). Batasi
  lewat `credits_budget`/`max_iterations` dan `limit` tool.
- **RS3 — Jangan memaksa tool bila tak perlu** — panduan bersifat kondisional (saat pertanyaan
  menuntut konten/pembanding), bukan selalu.
- **Rujukan:** `docs/plan-fix-synthesis-right-sizing.md` (sinergi konteks),
  `docs/plan-percobaan-model-berbeda.md` (penguatan prompt lokasi/keyword), `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit `system.md`, uji, commit) pada tahap terpisah.
