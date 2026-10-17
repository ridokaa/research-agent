# [SMARTER-AGENT] Plan Bertahap — Agen Lebih Cerdas (tahu kapan berhenti / kapan menggali)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: agen **menggali lebih dalam saat perlu** dan
> **berhenti saat cukup**, tetap **fleksibel** — dengan memperbaiki **kualitas PENALARAN** (self-check,
> refleksi, pemahaman niat), **bukan** menambah aturan/rubrik kaku. Audit branch
> `retrofit/chat-completions-adapter` (2026-08-05). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Ringkasan & Reframe Penting

Yang diinginkan pengguna adalah agen yang **cerdas**, bukan **kaku-konsisten** (rubrik/policy ditolak
karena mengurangi fleksibilitas). Ini dua hal berbeda:

| | Konsisten (ditolak) | **Cerdas (target plan ini)** |
|---|---|---|
| Cara | Aturan/rubrik tetap | **Perbaiki penilaian agen** |
| Fleksibilitas | Berkurang | **Utuh** |
| Hasil | Selalu sama | Rata-rata **lebih dalam & relevan**, tetap bervariasi |

Plan ini menaikkan **kualitas keputusan** "berhenti vs lanjut" dan "seberapa dalam menggali" —
**tanpa** membelenggu agen. **Tidak** menghilangkan variansi (tetap LLM-driven); menaikkan rata-rata.

---

## 2. Prinsip & Prasyarat (WAJIB)

- **PRASYARAT — `MASTER-plan-perbaikan-terpadu.md` Langkah 3 sudah beres.** Peningkatan self-check/
  refleksi **hanya bermakna di jalur NORMAL**. Selama jawaban lari ke **fallback** (empty-synthesis),
  self-check dilewati → semua peningkatan ini **percuma**. Pastikan jawaban datang dari jalur normal
  dulu.
- **Satu tahap sekali jalan** → uji **≥3 run** (deepseek bervariasi) → **revert bila lebih buruk**.
- **Nilai dari OUTPUT** (kedalaman/relevansi jawaban), bukan kebersihan log.
- **Jangan** menukar fleksibilitas demi kekakuan — penilaian tetap **kondisional**, bukan checklist wajib.

---

## 3. Tahapan (urut dampak & keamanan)

### Tahap 1 — Self-check berbasis PENILAIAN (dampak tertinggi: "kapan berhenti")
**File:** `app/agent/prompts/finalize.md`, `app/agent/loop.py`
**Masalah sekarang:** self-check terlalu longgar ("apakah didukung data?") → `is_final=true` untuk
jawaban dangkal → agen berhenti terlalu cepat (kasus IMG37).
**Perubahan:** ubah self-check jadi **penilaian yang lebih matang** (bukan checklist kaku). Beri ia
konteks: pertanyaan user + daftar tool **tersedia** + tool yang **sudah dipanggil** (`executed_tools`).
Sketsa aturan di `finalize.md`:
> "Mengingat pertanyaan user, tool yang tersedia, dan tool yang SUDAH dipanggil: apakah jawaban ini
> sudah **sedalam & serelevan yang WAJAR**? Nilai `is_final=false` **hanya jika** ada dimensi
> **berharga, jelas relevan, dan mudah diperoleh** dari tool yang **belum** dipanggil — sebutkan di
> `missing`. Jika dimensi tak relevan dengan pertanyaan, atau tool-nya sudah dicoba & kosong/error,
> atau tak ada tool yang bisa memberi → `is_final=true`. Jangan memaksa kedalaman yang tak diminta."

**Guard (WAJIB):** hormati `max_iterations` & `credits_budget`; **jangan** paksa ulang tool yang
sudah kosong/error (anti-loop); batasi "paksaan menggali" maks **1-2** iterasi tambahan; bila anggaran
habis → terima draf + catat keterbatasan jujur.
**Gerbang uji (≥3 run):** query **analisis** → agen menggali lebih dalam (mis. cari pemicu) sebelum
berhenti; query **sederhana** → tetap ringkas (TIDAK over-dig); tidak ada loop; latensi wajar.
**Revert-if-worse:** bila over-dig/loop/lambat berlebih atau query sederhana jadi bertele-tele → revert/
perlonggar.

### Tahap 2 — Refleksi + adaptif SINYAL (dampak: "kapan menggali")
**File:** `app/agent/prompts/reflection.md`, `app/agent/prompts/system.md`
**Ide:** setelah menerima hasil tool, dorong agen menilai **temuan**, bukan sekadar "cukup/belum":
> "Perhatikan hasil tool: adakah **anomali/sinyal** yang layak digali? (mis. lonjakan volume tak wajar
> → cari **pemicu** via `search_posts` urut engagement; sentimen sangat negatif → cari post penyebab;
> satu platform mendominasi → periksa). Gali **hanya** bila sinyalnya relevan dengan pertanyaan."

Ini yang membedakan IMG38 (melihat lonjakan → cari pemicu) dari IMG37 (berhenti di volume).
Tetap **kondisional** — tanpa anomali/relevansi, jangan menggali berlebihan.
**Gerbang uji (≥3 run):** saat data menunjukkan anomali (mis. lonjakan) → agen lebih **sering**
menindaklanjuti (cari post/pemicu); tanpa anomali → tak menggali berlebih.
**Revert-if-worse:** bila menggali hal tak relevan / boros → revert.

### Tahap 3 — Kalibrasi niat & kedalaman di AWAL
**File:** `app/agent/prompts/system.md` (dan/atau langkah "understand" ringan)
**Ide:** di awal, agen menyimpulkan **jenis & kedalaman** yang diminta: pertanyaan **cepat/faktual**
(jawab ringkas) vs **analisis mendalam** (gali tren/pemicu/pembanding) → menyesuaikan usaha dengan
kebutuhan user. Ringkas, bukan tahap berat.
**Gerbang uji:** pertanyaan cepat dijawab efisien (sedikit tool); pertanyaan analisis digali; usaha
**sesuai** niat pertanyaan.
**Revert-if-worse:** bila mis-kalibrasi (cepat jadi lambat / analisis jadi dangkal) → revert.

### Tahap 4 (opsional) — Model/mode penalaran lebih kuat
Kualitas keputusan dibatasi plafon model. Model reasoning lebih kuat → penilaian "berhenti/lanjut"
lebih baik. Uji A/B; perhatikan biaya/latensi. Tak wajib.

---

## 4. Strategi Pengujian (§1.6 — output nyata, banyak run)

Siapkan **set query campuran**, jalankan **≥3 run masing-masing**, bandingkan:
- **(a) sederhana/hitung** — "Berapa post tentang X?" → harus **ringkas & cepat** (tak over-dig).
- **(b) analisis/pemicu** — "Apakah X sering dibicarakan & apa pemicunya?" → harus **menggali**
  (pemicu/pembanding) lebih **konsisten** dari sebelumnya.
- **(c) data-tak-tersedia** — dimensi yang Verosight tak punya → agen **berhenti jujur**, tak loop.
Metrik: kedalaman naik untuk (b); (a) tetap efisien; (c) berhenti bersih; tak ada loop tak berujung;
biaya/latensi masih wajar.

---

## 5. Verifikasi Akhir

- [ ] Query analisis: kedalaman **lebih konsisten** (lebih sering mengungkap pemicu/pembanding) —
      dibanding baseline sebelum plan.
- [ ] Query sederhana: **tidak** jadi bertele-tele/over-dig; efisien.
- [ ] Data tak tersedia: berhenti jujur; **tidak** memaksa/loop.
- [ ] Guard aktif: max_iterations/budget dihormati; tool kosong tak dipaksa ulang.
- [ ] Jawaban dari **jalur normal** (prasyarat Langkah 3 terpenuhi), bukan fallback.
- [ ] Rahasia teredaksi; tak ada regresi 500/empty baru.

---

## 6. Risiko & Batasan (jujur)

- **RS1 — Tidak menghilangkan variansi.** Ini menaikkan **rata-rata & kualitas penilaian**; agen
  tetap LLM-driven → jalur & kedalaman masih bervariasi. Ini **fitur** (fleksibilitas), bukan bug.
- **RS2 — Dibatasi plafon penalaran model** (deepseek). Ada langit-langitnya.
- **RS3 — Lebih mahal/lambat** untuk query analisis (lebih banyak token penalaran + kadang iterasi
  ekstra). Seimbangkan dengan `credits_budget`/`max_iterations`.
- **RS4 — Risiko over-dig** (menggali hal tak perlu). Guard Tahap 1 + sifat kondisional Tahap 2/3 =
  pengaman; uji query sederhana adalah kontrolnya.
- **RS5 — Ini seni desain agen: ITERATIF.** Perbaiki → uji banyak run → sempurnakan. Bukan sekali jadi.
  Karena beberapa "perbaikan" sesi ini pernah regresi, **wajib** satu tahap sekali jalan + gerbang uji.
- **Rujukan:** `docs/MASTER-plan-perbaikan-terpadu.md` (Langkah 3 prasyarat; §4 batas realistis),
  `docs/plan-enrich-answer-search-tools.md` (nudge search), `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit prompt/loop, uji, commit) pada tahap terpisah,
  **satu tahap sekali jalan dengan gerbang uji & aturan revert**.
