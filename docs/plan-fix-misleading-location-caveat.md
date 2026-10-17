# [NO-GEO-CAVEAT] Plan — Cegah Caveat Lokasi yang Menyesatkan (Bagian B, prompt-only)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: menghentikan model menulis caveat seperti
> *"diperlukan data tambahan dengan filter lokasi"* di jawaban akhir, seolah Verosight punya/butuh filter
> geografis. **Terbukti (dump schema live 32 tool, 2026-08-10): Verosight TIDAK punya parameter geografis
> apa pun** (`location`/`country`/`region`/`province`/`city`) — jadi caveat itu menyiratkan fitur yang tak
> pernah ada. Perbaikan **murni `system.md`** (prompt) → **tidak menyentuh `planner.py`** (file yang sedang
> dikoreksi user) → **100% reversibel via git**. Audit branch `retrofit/chat-completions-adapter`
> (2026-08-10). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Masalah (dari run minimax-m2.7-highspeed, 2026-08-10)

Query: *"Apakah topik perang sering dibicarakan akhir-akhir ini?"* (tanpa menyebut lokasi). Jawaban akhir
**setia & kaya** (volume + sentimen + pemicu), TAPI menutup dengan **Keterbatasan** yang menyesatkan:
> *"Data ini mencakup semua platform tanpa filter lokasi, sehingga konten dari luar Indonesia (Timur Tengah,
> Ukraina) juga termasuk. Untuk konteks spesifik Indonesia, diperlukan data tambahan dengan filter lokasi."*

**Kenapa menyesatkan (TERBUKTI dari schema live):** dump 32 tool Verosight menunjukkan **tidak ada satu pun
parameter geografis**. Filter yang benar-benar ada: `keyword`/`query` (teks), `platform` (kanal sosial —
x/instagram/tiktok/…, **bukan** negara), `media_type`, `date_from`/`date_to`/`days`, `min_likes`,
`exclude_keywords`/`exclude_profiles`. Selain itu, dataset Verosight **sudah bersumber dari media sosial
Indonesia** (akun KompasTV, Metro_TV, konten Timnas). Konten "luar negeri" yang muncul = **warganet Indonesia
membicarakan perang global**, karena kata **"perang" itu generik** — bukan bocornya data non-Indonesia, dan
bukan pula keterbatasan tool. Caveat "perlu filter lokasi" menuduh fitur yang tak ada.

---

## 2. Jalur yang menghasilkan caveat (verifikasi terhadap `loop.py`/`planner.py`)

- Jawaban final di run itu datang lewat **mode sintesis** (`iteration=4`, `synthesis_mode=True`, `tools=0`),
  lalu lolos self-check → `is_final=True`.
- **Mode sintesis mengirim `instructions` = `build_system_prompt` (system.md)** (`loop.py:200`, diteruskan
  ke pemanggilan LLM `loop.py:236`), dengan `llm_input` diganti `build_synthesis_input` (`loop.py:211`).
  → **Guidance baru di `system.md` PASTI sampai ke mode sintesis** — inilah titik ungkit yang benar.
- Instruksi "sebutkan keterbatasan" yang memicu kecenderungan caveat ada di `planner.py:165`
  (`build_synthesis_input`) & `planner.py:152` (`build_fallback_prompt`). **Keduanya SENGAJA tidak disentuh**
  (file `planner.py` sedang dikoreksi user); guidance `system.md` cukup untuk **membatasi cara** model
  menuliskan keterbatasan (melarang caveat lokasi palsu) tanpa mengubah `planner.py`.

---

## 3. Desain — SATU perubahan di `app/agent/prompts/system.md`

Tambah **satu butir** di bagian "Panduan memakai tool pencarian" (mis. setelah baris 21 atau baris 26):

> - **Verosight TIDAK punya filter geografis** — tak ada parameter `location`/`country`/`region`/`province`;
>   `platform` adalah kanal sosial (x/instagram/tiktok/…), **bukan** negara. Datanya **sudah** bersumber dari
>   media sosial Indonesia. Karena itu, di **jawaban akhir JANGAN** menulis "diperlukan data dengan filter
>   lokasi" atau menyiratkan ada filter lokasi yang hilang/rusak. Bila hasil memuat konteks luar negeri (mis.
>   perang di Timur Tengah/Ukraina), jelaskan itu sebagai **percakapan warganet tentang topik yang sering
>   berskala global** (kata kuncinya generik) — **bukan** keterbatasan tool. Penyempitan ke konteks Indonesia
>   dilakukan lewat makna `keyword`/`semantic_search`, **bukan** filter lokasi.

**Selaras dengan panduan lokasi yang sudah ada:** baris 17 & 21 (teruskan lokasi sebagai `keyword`/`query`;
ulangi dengan keyword lokasi lebih eksplisit) tetap valid — butir baru **tidak menghapusnya**, hanya
menegaskan bahwa penyempitan lokasi lewat MAKNA teks, dan melarang framing "filter lokasi" di jawaban.

---

## 4. Konsistensi dengan perubahan `planner.py` (WAJIB dibaca)

1. **Tidak menyentuh `planner.py`.** Perbaikan hanya di `system.md`. Aman dikerjakan **paralel** dengan
   koreksi `build_reflection_input` (baris 103–122) yang sedang Anda lakukan; keduanya **ortogonal**.
2. **Prasyarat: `planner.py` harus bisa di-*import*.** Saat ini `planner.py:118` masih `SyntaxError`
   (`return {11`). Selama itu belum diperbaiki (lihat `docs/plan-fix-planner-reflection-errors.md`),
   **aplikasi tak bisa start** → gerbang uji plan ini **tak bisa dijalankan**. Jadi urutan: **terapkan
   plan planner-reflection-errors DULU**, baru plan ini.
3. **Klausa "sebutkan keterbatasan" di `planner.py:152` & `:165` tetap ada** dan sengaja tidak diubah;
   `system.md` meng-*override* cara penulisannya. Bila kelak Anda ingin memperkuat langsung di string itu,
   itu **menyentuh `planner.py`** → tandai OPSIONAL & minta konfirmasi Anda dulu.

---

## 5. Tahapan (SATU tahap)

1. Terapkan butir baru di `system.md` (§3).
2. (Opsional, cegah regresi terhapus) tambah test string-presence di `tests/`:
   `assert "TIDAK punya filter geografis" in Planner()._system_template` (atau frasa stabil pilihan).
3. `./.venv/bin/python -m pytest tests/ --ignore=tests/test_llm_live.py -q` → **hijau**
   (mensyaratkan prasyarat §4.2 sudah beres).
4. **Commit** (tanpa push kecuali diminta). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

**Gerbang uji (≥3 run agen nyata):**
- Ulangi query pemicu ("apakah perang sering dibicarakan?") + ≥2 query bertema global lain (mis. "nuklir",
  "pemilu AS").
- **Kriteria lulus:** jawaban akhir **tidak** memuat "filter lokasi"/"diperlukan data dengan filter lokasi"
  atau klaim adanya filter geografis; bila ada konten luar negeri, dibingkai sebagai percakapan warganet
  soal topik global — **bukan** keterbatasan tool. **Kesetiaan tetap** (volume/sentimen/pemicu tetap dipakai).
- **Revert-if-worse:** bila jawaban jadi kaku/menyesatkan arah lain, atau kesetiaan turun →
  `git checkout <baseline> -- app/agent/prompts/system.md`.

---

## 6. Verifikasi Akhir

- [ ] `system.md` memuat butir "Verosight TIDAK punya filter geografis …".
- [ ] Butir lokasi lama (baris 17, 21) tetap ada & tak bertentangan.
- [ ] `planner.py` **tidak** diubah oleh plan ini.
- [ ] Suite hijau (setelah prasyarat syntax fix).
- [ ] Gerbang uji ≥3 run: caveat "filter lokasi" hilang; kesetiaan tak turun.
- [ ] Rahasia teredaksi; commit (tanpa push kecuali diminta).

---

## 7. Risiko & Batasan (jujur)

- **RS1 — Perilaku prompt, bukan logika.** Tak terkunci unit test perilaku; kontrolnya gerbang uji ≥3 run.
  Test string-presence hanya menjaga butir tak terhapus, bukan efek pada model.
- **RS2 — Jalur fallback tak tercakup.** `build_fallback_prompt` dipakai dengan **instruksi hardcoded**
  (`loop.py:508`, "sebutkan keterbatasannya"), **bukan** `system.md`. Jadi bila jawaban keluar via jalur
  fallback (status exhausted/timeout — jarang), caveat masih mungkin muncul. **Cakupan penuh (opsional):**
  tambah klausa yang sama ke instruksi hardcoded `loop.py:508` — ini menyentuh `loop.py` (bukan `planner.py`),
  tetapi tandai OPSIONAL karena fallback adalah jalur terdegradasi & bukan sumber masalah di run ini.
- **RS3 — Prasyarat syntax fix.** Lihat §4.2 — plan ini tak bisa diuji sebelum `planner.py` di-*import*-kan.
- **RS4 — Model tetap bisa membandel.** Nudge prompt tak menjamin 100%; ≥3 run memantau. Jika masih bocor,
  pertimbangkan reinforcement di self-check `finalize.md` (menolak jawaban yang mengklaim filter lokasi) —
  di luar cakupan minimal plan ini.
- **RS5 — Akar = keterbatasan data-source.** Verosight memang tak punya geo-filter; plan ini hanya
  memperbaiki **cara model membingkainya**, bukan menambah kemampuan lokasi (itu masalah Bagian A yang
  terpisah: dorong `semantic_search` + lokasi-di-query).
- **Rujukan:** `app/agent/prompts/system.md:17,21,26`, `app/agent/loop.py:200,211,236,504-508`,
  `app/agent/planner.py:152,165`, `docs/plan-fix-planner-reflection-errors.md`,
  `docs/plan-percobaan-model-berbeda.md:23`, `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit `system.md` + test + uji ≥3 run + commit) pada tahap
  terpisah.
