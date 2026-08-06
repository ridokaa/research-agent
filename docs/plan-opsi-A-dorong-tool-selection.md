# [OPSI-A'] Plan v2 — Dorong Pemilihan Tool + Self-Check BERSYARAT (prompt-only, 100% reversibel)

> **Dokumen rencana untuk agen eksekutor. VERSI 2** — menyempurnakan Opsi A v1 yang sudah dijalankan &
> di-revert. Tujuan: membuat model **memanggil dimensi yang benar** (volume + sentimen + pemicu) untuk
> pertanyaan **signifikansi/analisis**, TANPA memaksa dimensi ekstra pada pertanyaan **faktual sederhana**.
> **Hanya perubahan teks prompt** (`system.md`, `finalize.md`) → **100% reversibel via git**, tak menyentuh
> kode/struktur. Audit branch `retrofit/chat-completions-adapter` (2026-08-06). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Masalah & Hasil Eksekusi v1 (BUKTI)

**Konteks awal (run `79c3bd74`):** gpt-5.6-terra memilih hanya tool volume, melewatkan `get_sentiment` &
`search_posts`; self-check meloloskan jawaban tipis.

**Hasil Opsi A v1 (dijalankan agen, 2026-08-06) — SEBAGIAN BESAR BERHASIL:**
- ✅ Run 1 & 2 ("apakah nuklir sering dibicarakan"): model memanggil **3 dimensi** (volume + sentimen +
  `search_posts` sort=like_count) → jawaban **kaya** memuat **pemicu spesifik** (pidato Prabowo soal nuklir
  Iran & siaran terputus) + sentimen. **Ini target yang selama ini dikejar — tercapai.**
- ✅ Run 3 (IKN): saat 1 tool 400, **self-check memicu refleksi** (`is_final=false`) → gather ulang. Bekerja.
- ❌ **Efek samping (satu-satunya):** pertanyaan **faktual sederhana** ("berapa volume bbm minggu ini?") →
  `finalize.md` v1 (tanpa syarat) **menolak** jawaban ringkas yang benar → memaksa `search_posts` hingga
  iter-8 (`exhausted`). **Over-gather.**

**Akar efek samping:** aturan `finalize.md` v1 menuntut sentimen/search_posts untuk **SEMUA** pertanyaan,
termasuk faktual sederhana. **Perbaikan v2: buat aturan itu BERSYARAT** — hanya untuk pertanyaan
signifikansi/analisis; kecualikan faktual sederhana. Simpan kemenangan, buang over-gather.

**Sifat & batas:** tetap menyetir model, tanpa jaminan sempurna. Bila v2 masih perlu tuning tanpa henti /
tetap over-gather → itu bukti tegas untuk **Jalur B (model lebih mampu)**. Perubahan ini **reversibel penuh**.

---

## 2. Desain (2 perubahan teks)

**Perubahan 1 — `app/agent/prompts/system.md`: resep eksplisit untuk pertanyaan signifikansi.**
Tambah panduan konkret (di bagian "Cara kerja" atau "Panduan memakai tool pencarian"):
> "**Pertanyaan signifikansi topik** (mis. 'apakah X sering dibicarakan?', 'seberapa penting/viral X?'):
> jawaban memadai umumnya butuh TIGA dimensi — (1) **volume/tren** (`get_keyword_volume`), (2)
> **sentimen/nada** (`get_sentiment`), (3) **pemicu/substansi** (`search_posts` dengan `sort=like_count`).
> Panggil ketiganya **paralel di awal**, kecuali salah satu jelas tak relevan. **Jangan berhenti hanya pada
> volume.**"

**Perubahan 2 — `app/agent/prompts/finalize.md`: self-check menuntut dimensi kunci, TAPI BERSYARAT (KUNCI v2).**
Tambah poin ke kriteria — **dengan pengecualian eksplisit untuk pertanyaan faktual sederhana** (inilah
perbaikan atas efek samping v1):
> "**KLASIFIKASIKAN dulu jenis pertanyaan:**
> - **Faktual sederhana** (mis. 'berapa volume/jumlah X?', 'kapan puncaknya?') → jangan menuntut dimensi
>   ekstra. Bila angka yang diminta sudah ada di data → **`is_final=true`**. JANGAN paksa sentimen/search_posts.
> - **Signifikansi/analisis/opini** (mis. 'apakah X sering dibicarakan?', 'seberapa penting/viral X?',
>   'bagaimana tanggapan publik?') → bila `get_sentiment` ATAU `search_posts`/`semantic_search` termasuk tool
>   yang BELUM dipanggil dan jelas relevan, set **`is_final=false`** dan sebut tool itu di `missing`."

Guard anti-loop yang sudah ada (`missing in executed_tools → is_final=true`; iterasi mepet) mencegah loop.
**Perubahan 1 (`system.md`) sudah menyasar hanya "pertanyaan signifikansi topik" — biarkan; tak menyebabkan
over-gather karena resepnya kondisional.**

---

## 3. Tahapan (eksplisit: baseline → terapkan → uji → revert-if-worse)

### Tahap 0 — Baseline aman (WAJIB sebelum mulai)
- Pastikan working tree bersih / commit dulu kondisi sekarang sebagai titik kembali. Catat hash baseline
  (mis. `git rev-parse HEAD`). Suite penuh **hijau** (`./.venv/bin/python -m pytest tests/ --ignore=tests/test_llm_live.py -q`).

### Tahap 1 — Terapkan 2 perubahan prompt
- Edit `system.md` (Perubahan 1) + `finalize.md` (Perubahan 2). **Commit** (tanpa push).
- Catatan: tak ada unit test perilaku (ini output LLM) — test struktural yang ada tetap harus **hijau**
  (perubahan prompt tak boleh memecahkannya; jalankan suite untuk memastikan).

### Tahap 2 — Uji ≥3 run (gerbang perilaku) — WAJIB mencakup DUA jenis pertanyaan
- **(a) Signifikansi** (mis. "apakah nuklir sering dibicarakan akhir-akhir ini?"): model memanggil
  `get_sentiment` **dan** `search_posts`; jawaban memuat **sentimen** + **pemicu** (mis. pidato Prabowo).
- **(b) Faktual sederhana** (mis. "berapa volume percakapan bbm minggu ini?"): jawaban **ringkas & cepat**
  (1-2 iterasi); self-check **`is_final=true`** setelah angka diperoleh. **TIDAK boleh over-gather** /
  memaksa `search_posts` / `exhausted`. ← Ini kontrol utama perbaikan v2.
- **Pantau:** latensi/biaya wajar; tak loop.

### Tahap 3 — Putuskan
- **Membaik & konsisten (≥3 run)** → simpan; commit sebagai checkpoint.
- **Lebih buruk / membingungkan / over-gather** → **REVERT:**
  `git checkout <baseline_hash> -- app/agent/prompts/system.md app/agent/prompts/finalize.md` → commit revert.
  Perilaku kembali persis, nol sisa.
- **Model tetap abai (tak ada perubahan)** → revert (atau biarkan), dan ambil kesimpulan: **lever prompt
  mentok → lanjut Jalur B (evaluasi/ganti model)**.

---

## 4. Verifikasi Akhir

- [ ] Untuk query signifikansi: `get_sentiment` & `search_posts` dipanggil (≥3 run).
- [ ] Jawaban memuat sentimen + pemicu (tak lagi volume-saja).
- [ ] Self-check menolak jawaban yang melewatkan dimensi relevan (log `reflection` saat kurang).
- [ ] Pertanyaan sederhana **tidak** over-gather; tak ada loop; latensi wajar.
- [ ] Suite struktural tetap **hijau**; rahasia teredaksi.
- [ ] Bila gagal: revert bersih via `git checkout` file prompt → perilaku semula.

---

## 5. Risiko & Batasan (jujur)

- **RS1 — Tanpa jaminan (plafon prompt).** `system.md` sudah menyuruh hal serupa & diabaikan. Versi lebih
  tegas MUNGKIN membantu, tapi bila model tetap abai → itu **sinyal Jalur B**, bukan tambah tweak lagi.
- **RS2 — Trade-off fleksibilitas.** Resep "panggil 3 dimensi" mendekati rubrik yang dulu Anda tolak; ia
  menaikkan konsistensi tapi sedikit mengurangi keleluasaan model. "kecuali jelas tak relevan" menjaga
  sebagian fleksibilitas. Bila terasa terlalu kaku → revert.
- **RS3 — Over-gather (TERJADI di v1, DITANGANI di v2).** v1 memaksa dimensi untuk semua pertanyaan →
  pertanyaan sederhana "bbm" over-gather sampai `exhausted`. v2 memakai `finalize.md` **bersyarat**
  (klasifikasi faktual vs signifikansi). Gerbang uji (b) adalah kontrol wajibnya. Bila v2 MASIH over-gather
  pada (b) → berarti model gagal mengklasifikasi jenis pertanyaan → **sinyal kuat Jalur B**.
- **RS4 — Reversibilitas.** 100% via git (prompt-only). Test struktural (synthesis/CT1/tanggal) melindungi
  kode inti — perubahan prompt tak bisa meregresinya.
- **Rujukan:** `docs/plan-agen-lebih-cerdas.md`, `docs/plan-fix-platform-overconstraint-dead-tool.md`,
  memori `agent-loop-reliability-state`, `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit prompt, uji ≥3 run, commit/revert) pada tahap terpisah.
