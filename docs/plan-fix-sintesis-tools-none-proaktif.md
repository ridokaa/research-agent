# [SYNTH-RIGHT-SIZE] Plan — Right-size Konteks Sintesis Proaktif (buka kunci jalur cerdas)

> **Dokumen rencana untuk agen eksekutor (bukan implementasi kode).** Tujuan: menghentikan iter-sintesis
> selalu **empty** dengan **mengecilkan konteks SEBELUM panggilan sintesis** (bukan sesudah, via retry),
> sehingga model **menghasilkan teks** → **self-check + refleksi** (yang SUDAH terpasang dari
> `plan-agen-lebih-cerdas.md`) **benar-benar berjalan**. Audit branch
> `retrofit/chat-completions-adapter` (2026-08-05). Patuhi `docs/PROMPT_PLAN.md` §1.6 (no-mock,
> transparansi error, `ErrorReport` component+stage).
>
> **STATUS (2026-08-05):** Tahap 1 (`tools=None` saat sintesis) **SUDAH diterapkan** (`loop.py:249`
> `effective_tools = None if synthesis_mode ...`). Gerbang uji Tahap 1 membuktikan **empty BUKAN karena
> tools** → **Tahap 2 (right-size) kini WAJIB & terbukti**, bukan lagi opsional. Lihat §1.

---

## 1. Ringkasan & Akar Masalah (TERBUKTI dari bukti log)

**Hipotesis awal (SALAH):** empty disebabkan `tools=32` yang ditawarkan saat sintesis. Tahap 1 sudah
membuat panggilan sintesis `tools=0`, **namun tetap empty**. Jadi tools **bukan** penyebabnya.

**Penyebab sebenarnya (TERBUKTI): ukuran KONTEKS INPUT.** Run `6cfb9b1f` (2026-08-05 15:43), dua panggilan
**sama-sama `tools=0`**, model & setelan identik:

| Panggilan | `tools` | `input_tokens` | Hasil |
|---|---|---|---|
| Sintesis iter-2 | **0** | **45.746** | `output_tokens=0` → **EMPTY** |
| Retry (right-sized) | **0** | **1.473** | `output_tokens=699` → **SUKSES** |

Satu-satunya perbedaan = **ukuran konteks (45.746 vs 1.473 token)**. Kesimpulan:

> **deepseek-v4-pro via 9Router diam-diam mengembalikan respons kosong bila input terlalu besar (~46k token).**
> Kecilkan ke ~1,5k → berhasil. **Tools tidak relevan.**

**Kenapa konteks 46k?** `TOOL_RESULT_PASSTHROUGH=true` menaruh **hasil tool penuh** (post mentah, media, dll.)
ke riwayat pesan; panggilan sintesis (`build_request`) mewarisi semuanya.

**Konsekuensi (dua masalah sekaligus):**
1. **Empty → selalu lewat retry.** Karena `loop.py:415` mensyaratkan model **mengembalikan teks**, dan
   sintesis selalu empty, **blok self-check/refleksi DILEWATI** → jawaban datang dari `empty synthesis
   retry` (`loop.py:494+`) yang tak menjalankan self-check. Inilah "kadang kurang cerdas".
2. **Jawaban under-synthesize.** Retry pakai `build_fallback_prompt` sederhana; pada satu run model
   **mengabaikan** sentimen (`28/18/54`) & topik pemicu (`"Prabowo cut nuclear speech"`) yang **sudah
   diambil**, lalu **salah** menulis *"tanpa rincian sentimen"* / *"pemicu tidak tersedia"*.

**Inti perbaikan (Tahap 2): right-size konteks SEBELUM panggilan sintesis** — meniru resep retry yang
**terbukti** (1.473 token), tapi **proaktif** (panggilan pertama) dan **diarahkan lewat self-check**. Efek:
sintesis tak pernah empty → tak perlu retry/fallback → self-check/refleksi **hidup** → lebih cerdas.

**Bukan** tentang menghilangkan variansi atau menambah rubrik kaku. Ini membuka jalur cerdas yang sudah ada.

---

## 2. Prinsip & Prasyarat (WAJIB)

- **Prasyarat sudah terpenuhi:** self-check (`loop.py:414-468`), refleksi (`loop.py:470-479`), prompt
  `finalize.md`/`reflection.md`/`system.md` **sudah terpasang** (`plan-agen-lebih-cerdas.md` dijalankan).
  Plan ini adalah **pembuka kuncinya** — bukan menggantikannya.
- **Satu tahap sekali jalan** → uji **≥3 run** (deepseek bervariasi) → **revert bila lebih buruk**.
- **JANGAN hapus jaring pengaman.** `empty synthesis retry` (`loop.py:494-532`) **DIPERTAHANKAN** sebagai
  jaring terakhir. Plan ini mengurangi ketergantungan padanya, bukan menghapusnya.
- **Nilai dari OUTPUT** (apakah self-check kini berjalan; apakah jawaban memakai SELURUH data yang diambil),
  bukan kebersihan log. Warning `session_not_found`/`call_tool_error attempt=1` **non-fatal** — abaikan.
- **§1.6:** tanpa mock; error tetap transparan lewat `ErrorReport(component, stage)`; jangan menyembunyikan
  kegagalan jadi jawaban palsu.

---

## 3. Desain: mode "gather" vs "synthesize" (alternasi) + konteks right-sized

Dua bagian, keduanya diperlukan:

**(a) Alternasi mode (Tahap 1 — SUDAH TERPASANG):**
- **GATHER (tool ON):** iterasi awal & iterasi setelah refleksi — model boleh memanggil tool.
- **SYNTHESIZE (`tools=None`):** iterasi **tepat setelah** tool dieksekusi — masuk `loop.py:415`.

**(b) Konteks right-sized saat SYNTHESIZE (Tahap 2 — INTI, belum terpasang):** pada iterasi SYNTHESIZE,
`llm_input` **tidak** boleh berisi riwayat pesan penuh (~46k token → empty). Bangun dari
`get_context_block(rich=True, max_tokens≈4000)` (FAKTA disimpan, NOISE dibuang) → ~1,5-4k token → model
**menghasilkan teks** → self-check berjalan.

Siklusnya tetap fleksibel: GATHER → SYNTHESIZE(kecil) → (bila `is_final=false`) refleksi → GATHER →
SYNTHESIZE(kecil) → ... Dijaga `max_iterations`, `no_progress_count`, dan guard anti-loop self-check.

**Catatan tentang 409 (terpisah):** 409 pada keyword "keracunan" terjadi di panggilan gather berat
(`tools=32`). Itu diurus Tahap 3 (retry/log 409), **bukan** oleh right-sizing sintesis. Jangan campur.

---

## 4. Tahapan (urut dampak & keamanan — SATU tahap sekali jalan)

### Tahap 1 — `tools=None` pada iterasi sintesis  ✅ SUDAH DITERAPKAN
**File:** `app/agent/loop.py` **Status:** selesai (`loop.py:249` `effective_tools = None if synthesis_mode`).
Flag `synthesis_mode` di-set True setelah eksekusi tool, False setelah refleksi.
**Hasil gerbang uji (run `6cfb9b1f`):** iter sintesis kini `tools=0` **tetapi MASIH empty** pada
`input_tokens=45746`. Sesuai aturan revert Tahap 1: *"bila masih empty → penyebabnya ukuran token → JANGAN
revert; lanjut Tahap 2."* → **Tahap 1 dipertahankan** (tetap berguna: payload sintesis lebih ringan &
mencegah model salah memanggil tool saat menyintesis), tetapi **tidak cukup sendirian**.

### Tahap 2 — Right-size konteks sintesis (INTI — WAJIB, terbukti diperlukan)
**File:** `app/agent/loop.py` (+ opsional `app/agent/planner.py`)
**Terbukti:** panggilan sintesis (`tools=0`, `input_tokens=45746`) → EMPTY; retry (`tools=0`,
`input_tokens=1473`) → SUKSES. Penyebab pasti = **ukuran konteks** (`TOOL_RESULT_PASSTHROUGH=true` menaruh
hasil tool penuh di riwayat). Solusinya: **pindahkan right-sizing dari retry (reaktif) ke panggilan sintesis
(proaktif).**
**Perubahan:**
1. Saat `synthesis_mode=True`, **jangan** pakai `llm_input` dari `build_request` (riwayat penuh ~46k token).
   Bangun input sintesis dari `state.execution_memory.get_context_block(max_tokens≈4000, rich=True)`
   (FAKTA disimpan, NOISE dibuang via `prune_noise_from_data`; data mentah tetap utuh di
   `tool_invocations.result`). Ini **resep yang sama** dengan retry yang sudah terbukti sukses (1.473 token).
2. **Arahkan output-nya lewat blok self-check** `loop.py:414-468` — JANGAN lewat `build_fallback_prompt`
   (yang melewati self-check). Boleh pakai prompt sintesis yang menekankan "gunakan SELURUH data" +
   "sebutkan keterbatasan hanya bila benar-benar tak ada di data".
3. **Pertahankan** jaring `empty synthesis retry` (`loop.py:494-532`) sebagai pengaman terakhir.

**Efek yang diharapkan:** sintesis pertama = konteks kecil → **menghasilkan teks** (tak empty) → masuk
`loop.py:415` → **self-check + refleksi hidup** → tak perlu retry/fallback. Ini sekaligus mengatasi keluhan
"kadang kurang cerdas" (self-check selama ini tak pernah aktif karena selalu empty→retry).

**Gerbang uji (≥3 run, keyword campuran mis. "nuklir", "keracunan", "MBG"):**
- iter sintesis **menghasilkan teks** (`has_text=True`), `input_tokens` **kecil** (~1-4k), **bukan** empty.
- Log memuat `final_answer "Final answer verified and produced"` (jalur self-check, `loop.py:465`) —
  **bukan** `"Final answer generated via synthesis retry"`.
- Jawaban memakai **seluruh** data yang diambil (mis. sebut sentimen & topik pemicu bila diambil), tak lagi
  salah menulis "tidak tersedia".
**Revert-if-worse:** bila jawaban jadi tipis (kehilangan fakta) → longgarkan
`max_tokens` atau revert ke Tahap 1 saja.

### Tahap 3 (opsional) — Retry 409 & log body (independen, aman)
**File:** `app/llm/client.py`
Untuk sisa 409 yang mungkin muncul di panggilan gather: tambah `409` ke `_RETRYABLE_STATUS`
(`client.py:63`) dan ke set yang mem-log body (`client.py:478`) agar 409 transien sembuh sendiri &
alasan aslinya terlihat. Kecil, terlokalisir, tak menyentuh loop. Boleh dikerjakan kapan saja.

---

## 5. Strategi Pengujian (§1.6 — output nyata, banyak run)

Jalankan **≥3 run** per keyword pada set campuran; bandingkan **sebelum vs sesudah**:
- **(a) analisis** ("Apakah X sering dibicarakan & apa pemicunya?") → sintesis via **jalur normal**
  (self-check berjalan), jawaban memakai volume + sentimen + topik pemicu bila diambil.
- **(b) sederhana/faktual** ("Berapa post tentang X?") → tetap ringkas, tak over-dig, tak loop.
- **(c) data-tak-tersedia** → berhenti jujur; keterbatasan yang disebut **benar** (bukan menyangkal data
  yang sebenarnya ada).
Metrik: proporsi jawaban dari `"Final answer verified and produced"` (jalur cerdas) **naik**; dari
`"synthesis retry"` **turun**; tak ada 409 di iter sintesis; latensi wajar; tak ada regresi 500/empty baru.

---

## 6. Verifikasi Akhir

- [ ] Iter sintesis menghasilkan **teks**, bukan empty (≥3 run, beberapa keyword).
- [ ] Log memuat `"Final answer verified and produced"` → **self-check benar-benar berjalan**.
- [ ] Jawaban memakai **seluruh** data yang berhasil diambil; tak ada disclaimer palsu "tidak tersedia".
- [ ] Tak ada 409 pada iter sintesis; tak ada 500 baru.
- [ ] Jaring `empty retry` **masih ada** (uji: bila sengaja dikosongkan, tetap tertangani).
- [ ] Guard aktif: `max_iterations`/`no_progress`/override self-check dihormati; tak ada loop tak berujung.
- [ ] Rahasia teredaksi; error tetap transparan (`ErrorReport` component+stage).

---

## 7. Risiko & Batasan (jujur)

- **RS1 — Ukuran token = penyebab empty (TERBUKTI, bukan lagi dugaan).** `tools=0` @ 45.746 token → empty;
  `tools=0` @ 1.473 token → sukses. Tahap 1 (`tools=None`) sudah terpasang tapi **tak cukup**; Tahap 2
  (right-size) adalah perbaikan sebenarnya. Ambang persis empty tak diketahui — right-size ke ~1-4k aman.
- **RS2 — Alternasi gather/synthesize menambah iterasi** pada kasus yang perlu menggali lagi (refleksi).
  Dijaga `max_iterations`/`no_progress`. Pantau latensi.
- **RS3 — Disclaimer palsu mungkin belum 100% hilang.** Sebagian adalah plafon sintesis deepseek
  (mengabaikan blok data kecil). Perbaikan ini **perlu** (agar self-check menilai & bisa menolak jawaban
  tipis), tapi bukan jaminan mutlak. Iteratif.
- **RS4 — Delikat: ini logika loop.** Beberapa perubahan loop sesi ini pernah regresi. Tahap 1 sudah beres;
  **kerjakan Tahap 2 sendirian**, uji ≥3 run, revert-if-worse. Jangan gabung dengan perubahan loop lain.
- **Rujukan:** `docs/plan-agen-lebih-cerdas.md` (self-check/refleksi yang dibuka kunci-nya),
  `docs/MASTER-plan-perbaikan-terpadu.md` (Langkah 3 empty-synthesis), `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit `loop.py`, uji, commit) pada tahap terpisah, **satu
  tahap sekali jalan dengan gerbang uji & aturan revert**.
