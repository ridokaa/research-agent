# [OPSI-A] Plan — Dorong Pemilihan Tool + Perketat Self-Check (prompt-only, 100% reversibel)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: membuat model **memanggil dimensi yang benar**
> (volume + sentimen + pemicu) untuk pertanyaan signifikansi topik, dan membuat **self-check menolak**
> jawaban yang melewatkannya. **Hanya perubahan teks prompt** (`system.md`, `finalize.md`) →
> **100% reversibel via git**, tak menyentuh kode/struktur. Upaya murah TERAKHIR sebelum keputusan model
> (Jalur B). Audit branch `retrofit/chat-completions-adapter` (2026-08-06). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Masalah (dari run `79c3bd74`, 2026-08-06)

Setelah fix platform + tool mati berhasil, model gpt-5.6-terra **tetap** memilih hanya tool volume
(`get_keyword_volume` x2 + `get_volume`), **melewatkan** `get_sentiment` & `search_posts` (yang TERBUKTI
mengembalikan pemicu viral). Self-check (CT1) **meloloskan** jawaban tipis ini (`is_final=True`).

`system.md` **sudah** menyuruh "utamakan `search_posts`" (baris 18) & memakai sentimen — **model
mengabaikannya**. Jadi panduan yang ada **kurang tegas/spesifik** untuk pola "apakah X sering dibicarakan".
Ini penilaian model; satu-satunya lever prompt-only = **buat panduan lebih eksplisit & jadikan self-check
lebih menuntut**.

**Sifat & batas:** ini menyetir model, **tanpa jaminan** (model bisa tetap abai). Bila gagal → itu bukti
tegas untuk **Jalur B (model lebih mampu)**. Perubahan ini **reversibel penuh**.

---

## 2. Desain (2 perubahan teks)

**Perubahan 1 — `app/agent/prompts/system.md`: resep eksplisit untuk pertanyaan signifikansi.**
Tambah panduan konkret (di bagian "Cara kerja" atau "Panduan memakai tool pencarian"):
> "**Pertanyaan signifikansi topik** (mis. 'apakah X sering dibicarakan?', 'seberapa penting/viral X?'):
> jawaban memadai umumnya butuh TIGA dimensi — (1) **volume/tren** (`get_keyword_volume`), (2)
> **sentimen/nada** (`get_sentiment`), (3) **pemicu/substansi** (`search_posts` dengan `sort=like_count`).
> Panggil ketiganya **paralel di awal**, kecuali salah satu jelas tak relevan. **Jangan berhenti hanya pada
> volume.**"

**Perubahan 2 — `app/agent/prompts/finalize.md`: self-check menuntut dimensi kunci.**
Tambah satu poin ke kriteria (tetap kondisional, memakai daftar tool BELUM dipanggil yang sudah tersedia
sejak CT1):
> "Untuk pertanyaan tentang topik/isu/opini publik: bila `get_sentiment` ATAU `search_posts`/`semantic_search`
> termasuk tool yang BELUM dipanggil dan jelas relevan dengan pertanyaan, set `is_final=false` dan sebut tool
> itu di `missing`."

Guard anti-loop yang sudah ada (`missing in executed_tools → is_final=true`; iterasi mepet) mencegah loop.

---

## 3. Tahapan (eksplisit: baseline → terapkan → uji → revert-if-worse)

### Tahap 0 — Baseline aman (WAJIB sebelum mulai)
- Pastikan working tree bersih / commit dulu kondisi sekarang sebagai titik kembali. Catat hash baseline
  (mis. `git rev-parse HEAD`). Suite penuh **hijau** (`./.venv/bin/python -m pytest tests/ --ignore=tests/test_llm_live.py -q`).

### Tahap 1 — Terapkan 2 perubahan prompt
- Edit `system.md` (Perubahan 1) + `finalize.md` (Perubahan 2). **Commit** (tanpa push).
- Catatan: tak ada unit test perilaku (ini output LLM) — test struktural yang ada tetap harus **hijau**
  (perubahan prompt tak boleh memecahkannya; jalankan suite untuk memastikan).

### Tahap 2 — Uji ≥3 run (gerbang perilaku)
- Query campuran, termasuk "apakah nuklir sering dibicarakan akhir-akhir ini?".
- **Kriteria BERHASIL:** model memanggil `get_sentiment` **dan** `search_posts` (bukan hanya volume);
  jawaban memuat **sentimen** + **pemicu/substansi** (mis. pidato Prabowo viral). Bila kurang, self-check
  memicu refleksi (`is_final=false`, log `reflection`) lalu gather ulang.
- **Pantau:** tak over-gather untuk pertanyaan sederhana; latensi/biaya wajar; tak loop.

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
- **RS3 — Risiko over-gather.** Menuntut 3 dimensi bisa membuat pertanyaan sederhana jadi boros. Gerbang uji
  (b) query sederhana adalah kontrolnya.
- **RS4 — Reversibilitas.** 100% via git (prompt-only). Test struktural (synthesis/CT1/tanggal) melindungi
  kode inti — perubahan prompt tak bisa meregresinya.
- **Rujukan:** `docs/plan-agen-lebih-cerdas.md`, `docs/plan-fix-platform-overconstraint-dead-tool.md`,
  memori `agent-loop-reliability-state`, `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit prompt, uji ≥3 run, commit/revert) pada tahap terpisah.
