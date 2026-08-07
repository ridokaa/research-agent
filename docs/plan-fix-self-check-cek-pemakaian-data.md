# [USE-CHECK] Plan — Self-Check Cek PEMAKAIAN Data (Opsi A, prompt-only, reversibel)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: menutup sisa **omisi kesetiaan** — model memanggil
> tool, data berhasil masuk memori, tapi jawaban **tak menyebut/menyangkalnya** (mis. run IKN mengabaikan
> sentimen 56/5/39 & substansi post). Solusi: self-check tak hanya cek *"tool DIPANGGIL?"* tapi juga
> *"data DIPAKAI?"* → bila tidak, **susun ulang** jawaban dari data yang sudah ada. **Hanya perubahan teks
> prompt** (`finalize.md`, `reflection.md`) → **100% reversibel via git**. Audit branch
> `retrofit/chat-completions-adapter` (2026-08-07). Patuhi `docs/PROMPT_PLAN.md` §1.6.
>
> **Konteks:** dijalankan pada model **`xkiro/minimax/minimax-m3`** yang lebih patuh & lebih setia dari
> gpt-5.6-terra (mode gagalnya = omisi diam-diam, bukan penyangkalan palsu) → self-check ini punya **peluang
> lebih besar berhasil** (validator = model yang lebih setia).

---

## 1. Masalah (dari 4 run minimax-m3, 2026-08-07)

- Run MBG & nuklir: **SETIA** (pakai volume + sentimen + pemicu). ✅
- Run IKN (`days=30`): **omisi** — sentimen (56/5/39) & post substansi **diambil sukses** tapi **tak
  disebut** di jawaban; fokus hanya volume/engagement. Bukan penyangkalan palsu (lebih ringan dari
  gpt-5.6-terra), tapi tetap kurang lengkap.

**Kenapa self-check sekarang tak menangkap:** `finalize.md:9` hanya menuntut tool **BELUM dipanggil**.
Untuk IKN, sentimen/search_posts **SUDAH dipanggil** → self-check lolos `is_final=true` walau datanya tak
dipakai. Yang perlu ditambah: cek **pemakaian**, bukan pemanggilan.

---

## 2. Desain (2 perubahan teks) + kendala guard anti-loop

**Perubahan 1 — `app/agent/prompts/finalize.md`: tambah cek PEMAKAIAN data.**
Tambah poin setelah baris 9:
> "1c. **Cek PEMAKAIAN data (bukan hanya pemanggilan).** Bila `get_sentiment` ATAU `search_posts`/
> `semantic_search` **SUDAH dipanggil** dan datanya **ADA** di '--- Data Tool Terkait ---' di bawah, TAPI
> draf jawaban **tidak menyebut** data itu (angka sentimen / isi post) atau **menyangkalnya** ('data tidak
> tersedia' padahal ada) → set **`is_final=false`**, **`missing`=null**, dan tulis di `reason` PERSIS:
> *'Data [sentimen/pemicu] sudah ada di memori tapi belum dipakai — susun ulang jawaban dengan
> memasukkannya.'* **JANGAN** isi `missing` dengan tool yang sudah dipanggil."

**KENAPA `missing`=null (kritis):** guard anti-loop di `loop.py` memaksa `is_final=True` bila
`missing in executed_tools`. Kalau kita isi `missing='verosight__get_sentiment'` (yang sudah dipanggil),
guard akan **membatalkan** penolakan kita. Dengan `missing=null`, penolakan lolos & refleksi tetap
diinjeksi (via `reason`), tanpa menyentuh kode loop.

**Perubahan 2 — `app/agent/prompts/reflection.md`: tangani kasus "susun ulang dari data yang ada".**
Tambah bullet **di ATAS** daftar langkah:
> "- **Jika alasan di atas menyebut 'data sudah ada tapi belum dipakai'**: JANGAN panggil tool apa pun.
> Susun **ULANG** jawaban final memakai data yang SUDAH ada di memori (sebutkan angka sentimen & isi/pemicu
> post secara eksplisit). Ini bukan menggali baru, melainkan memakai yang sudah ada."

---

## 3. Tahapan (SATU tahap sekali jalan)

### Tahap 1 — Prompt-only (INTI)
Terapkan Perubahan 1 & 2. **Commit** (tanpa push). Suite struktural harus tetap **hijau** (perubahan
prompt tak boleh memecahkannya; jalankan `pytest`).
**Gerbang uji (≥3 run):**
- **(a) Kasus omisi** ("apakah IKN sering dibicarakan?", yang cenderung `days=30`): self-check mendeteksi
  sentimen/pemicu tak dipakai → `reflection` muncul (log `event=reflection`, reason 'data sudah ada...') →
  iterasi berikut **menyusun ulang** jawaban yang kini **menyebut sentimen & pemicu**.
- **(b) Kasus sudah setia** (MBG, nuklir): tak ada penolakan tambahan (jawaban sudah pakai semua) →
  `is_final=true` langsung, tak over-loop.
- **(c) Faktual sederhana** ("berapa volume X?"): tetap `is_final=true` ringkas, tak dipaksa dimensi ekstra.
**Revert-if-worse:** bila loop berputar / over-gather / latensi membengkak →
`git checkout <baseline> -- app/agent/prompts/finalize.md app/agent/prompts/reflection.md`.

### Tahap 2 (opsional) — bila Tahap 1 kurang
**Alasan:** setelah refleksi, `synthesis_mode=False` (`loop.py:~494`) → iterasi ulang jadi mode **gather**
(tool ditawarkan). Model patuh (minimax) mestinya menyusun ulang teks, tapi bisa saja malah memanggil tool
lagi. Bila terjadi: ubah `loop.py` agar refleksi bertipe "susun ulang" men-set `synthesis_mode=True`
(sintesis kecil, `tools=None`) — kode kecil + test. Kerjakan HANYA bila Tahap 1 terbukti kurang.

---

## 4. Verifikasi Akhir

- [ ] Kasus omisi (IKN) → self-check menolak → jawaban ulang menyebut **sentimen + pemicu** (≥3 run).
- [ ] Kasus sudah-setia & faktual sederhana → tak over-loop, tetap efisien.
- [ ] Log memuat `event=reflection` dengan reason 'data sudah ada...' saat omisi terdeteksi.
- [ ] `missing=null` untuk kasus pemakaian (guard anti-loop tak membatalkan penolakan).
- [ ] Suite struktural tetap **hijau**; reversibel via `git checkout`; rahasia teredaksi.

---

## 5. Risiko & Batasan (jujur)

- **RS1 — Bergantung penilaian model (2 sisi).** Self-check harus (a) mendeteksi omisi sebagai validator,
  dan (b) menyusun ulang dengan benar. Peluang lebih besar di minimax-m3 (lebih patuh & setia) daripada
  gpt-5.6-terra, TAPI **tak dijamin**. Bila minimax pun sering meloloskan → itu batas prompt.
- **RS2 — Iterasi/latensi ekstra** saat menyusun ulang. Dijaga `max_iterations` & `no_progress_count`
  (finalisasi paksa setelah 3 iterasi tanpa progres). Bounded, tapi pantau.
- **RS3 — Re-sintesis di mode gather** (Tahap 1) — model mungkin memanggil tool lagi alih-alih menyusun
  ulang. Bila terjadi → Tahap 2.
- **RS4 — Risiko loop** bila model terus mengomisi (self-check terus menolak). Guard iterasi/no-progress
  memutusnya, tapi bisa boros. Uji ≥3 run adalah kontrolnya.
- **RS5 — Trade-off:** menuntut pemakaian data mendekati rubrik; jaga agar tak over-strict pada pertanyaan
  faktual (klasifikasi di finalize.md sudah membantu). Bila terasa kaku → revert.
- **Rujukan:** `docs/catatan/kesetiaan-sintesis.md` (masalah inti), `docs/plan-opsi-A-dorong-tool-selection.md`
  (self-check bersyarat yang sudah ada), `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit 2 prompt, uji ≥3 run, commit/revert) pada tahap
  terpisah.
