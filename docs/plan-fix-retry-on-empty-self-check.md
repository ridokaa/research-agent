# [RETRY-EMPTY] Plan — Retry-on-Empty Lewat Self-Check (menutup sisa empty acak provider)

> **Dokumen rencana untuk agen eksekutor (bukan implementasi kode).** Tujuan: menutup **sisa** respons
> kosong non-deterministik dari provider (deepseek/9Router) yang **masih lolos** setelah Tahap 2
> (right-size). Caranya: **retry panggilan yang SAMA saat respons kosong**, sehingga (a) empty pulih
> otomatis di mana pun (iter-1 gather maupun sintesis), dan (b) hasil pulihannya **tetap melewati
> self-check** (tak lagi lewat jalur `build_fallback_prompt` yang melewatinya). Audit branch
> `retrofit/chat-completions-adapter` (2026-08-05). Patuhi `docs/PROMPT_PLAN.md` §1.6 (no-mock,
> transparansi error, `ErrorReport` component+stage).

---

## 1. Ringkasan & Akar Masalah (TERBUKTI dari bukti log)

Setelah Tahap 2 (right-size), **mayoritas** run sintesis langsung menghasilkan teks & lewat self-check
(`"Final answer verified and produced"` — run prabowo/nuklir). Namun **sisa** empty acak masih lolos:

| Kejadian | Panggilan | `input_tokens` | Hasil | Akibat |
|---|---|---|---|---|
| iter-1 empty (MBG, `14e5cbe7`) | gather, `tools=32` | 6.976 (kecil) | **EMPTY** | memori kosong → **tak ada retry** → `build_fallback_prompt` juga empty → **`status=success` palsu** dgn non-jawaban |
| sintesis empty (MBG, `559efb89`) | sintesis, `tools=0` | 2.646 (kecil) | **EMPTY** | retry `build_fallback_prompt` → teks → **`"synthesis retry"` (self-check DILEWATI)** |
| sintesis OK (prabowo) | sintesis, `tools=0` | 3.748 | teks | self-check jalan ✅ |
| sintesis OK (nuklir) | sintesis, `tools=0` | 3.888 | teks | self-check jalan ✅ |

**Bukti non-deterministik:** `2.646` empty, tetapi `3.748` & `3.888` yang **lebih besar** berhasil; retry
`1.796` berhasil. **Tak ada ambang ukuran** → penyebab = **flakiness provider** (deepseek/9Router sesekali
mengembalikan kosong secara acak), **bukan** ukuran, **bukan** regresi Tahap 2.

**Dua celah kode saat ini (`loop.py:508-546`):**
1. **Retry hanya aktif bila `state.execution_memory` ada isinya** → iter-1 empty (memori kosong) tak
   di-retry sama sekali.
2. **Retry memakai `build_fallback_prompt`** yang **melewati self-check** → jawaban hasil retry muncul
   sebagai `"synthesis retry"`, tanpa lapisan kecerdasan (self-check/refleksi).
3. **Konsekuensi transparansi:** iter-1 empty berujung `answer='Tidak dapat memperoleh jawaban lengkap...'`
   dengan **`status=success`** — melaporkan sukses untuk non-jawaban (melanggar semangat §1.6).

**Inti perbaikan:** karena pasca-Tahap-2 **semua** panggilan sudah kecil, empty apa pun kini **transien &
aman di-retry**. Retry **panggilan yang SAMA** di titik `respond` utama menutup iter-1 **dan** sintesis
sekaligus — dan karena memakai variabel `response` yang sama, hasil pulihannya **mengalir alami ke
self-check** (`loop.py:428`) tanpa menduplikasi logika. **Bukan** menghilangkan empty (itu di sisi
provider), melainkan membuatnya **pulih otomatis & tak terlihat**.

---

## 2. Prinsip & Prasyarat (WAJIB)

- **Prasyarat terpenuhi:** Tahap 1+2 (`synthesis_mode` + right-size sintesis) **sudah terpasang & terbukti**
  (`loop.py:233-238`, `loop.py:259`). Plan ini **melengkapi**, menutup sisa empty acak.
- **Retry = pemulihan flake transien**, sejalan pola yang sudah dipakai (`session_not_found`). **Bukan**
  menyembunyikan kegagalan nyata.
- **Satu tahap sekali jalan** → uji **≥3 run** → **revert bila lebih buruk**.
- **JANGAN hapus jaring pengaman** yang lebih dalam: retry `build_fallback_prompt` (`loop.py:513-538`) &
  fallback finalization (`loop.py:549-576`) **dipertahankan** sebagai lapisan paling akhir.
- **§1.6:** tanpa mock; bila benar-benar tak ada jawaban & tak ada tool jalan → **`status=failed` +
  `ErrorReport`**, bukan `success` palsu.

---

## 3. Desain: retry di titik `respond` utama (satu titik, menutup semua)

Panggilan LLM utama ada di **satu tempat** (`loop.py:261`, `response = await llm.respond(...)`) — dilalui
**baik** iter-1 gather (`tools=32`) **maupun** sintesis (`synthesis_mode`, `tools=None`). Menambahkan
retry-on-empty di sini menutup **kedua** kasus dengan satu mekanisme:

```
response = await llm.respond(...)              # panggilan utama
attempts = 0
while response kosong (function_calls==0 AND not output_text) and attempts < MAX_EMPTY_RETRY:
    attempts += 1
    tracer.event("thinking", f"Respons kosong (flake provider); retry {attempts}/{MAX_EMPTY_RETRY}")
    response = await llm.respond(...)           # ulangi panggilan yang SAMA
```

**Efek elegan:**
- iter-1 empty → di-retry → model memilih tool → alur normal (eksekusi tool).
- Sintesis empty → di-retry → menghasilkan teks → **mengalir ke `loop.py:428` → self-check JALAN**
  (karena `response` sama; tak perlu menduplikasi self-check ke jalur retry).
- `build_fallback_prompt` (`loop.py:513+`) tetap ada, tapi kini **jarang** tersentuh — hanya bila provider
  empty **berkali-kali** berturut (sangat jarang).

**Guard (WAJIB):** `MAX_EMPTY_RETRY` kecil (mis. **2**, jadi maks 3 percobaan) agar tak loop/latensi
membengkak bila provider empty persisten. Empty datang cepat (~200ms) → retry murah. Log tiap percobaan
(transparansi). **Jangan** retry bila ada `function_calls` atau ada teks (bukan empty).

---

## 4. Tahapan (SATU tahap sekali jalan)

### Tahap 1 — Retry-on-empty di panggilan `respond` utama (INTI)
**File:** `app/agent/loop.py`
**Perubahan:** setelah `response = await llm.respond(...)` (`loop.py:261`), tambah loop retry seperti §3.
`MAX_EMPTY_RETRY = 2` (boleh via `settings` bila ingin dapat dikonfigurasi). Retry memakai **argumen yang
sama** (`input=llm_input`, `tools=effective_tools`, dst.) — termasuk `synthesis_mode` yang berlaku.
**Efek:** empty iter-1 & sintesis pulih otomatis; hasil sintesis pulih **lewat self-check**.
**Guard:** `MAX_EMPTY_RETRY` kecil; hanya saat benar-benar kosong; log tiap attempt.
**Gerbang uji (≥3 run, keyword campuran termasuk "MBG" yang sempat empty):**
- Bila terjadi empty, log memuat `"retry N/2"` lalu panggilan berhasil berisi.
- Jawaban sintesis muncul lewat **`"Final answer verified and produced"`** (self-check), **bukan**
  `"synthesis retry"`.
- Tak ada lagi `status=success` dengan non-jawaban `"Tidak dapat memperoleh..."`.
**Revert-if-worse:** bila latensi membengkak (provider empty beruntun) atau muncul loop → kecilkan
`MAX_EMPTY_RETRY` atau revert.

### Tahap 2 — Status jujur saat benar-benar kosong
**File:** `app/agent/loop.py`
**Perubahan:** pada fallback finalization (`loop.py:549-576`), bila setelah semua upaya **tak ada jawaban
nyata DAN tak ada tool yang pernah sukses** (`execution_memory` kosong), set **`status=failed`** dengan
`ErrorReport(component="llm", stage="empty_response", message="Provider mengembalikan respons kosong berulang")`
alih-alih `status=success` + string generik. Bila ADA data tool terkumpul tapi jawaban tipis → tetap
`success` dengan keterbatasan jujur (perilaku sekarang).
**Gerbang uji:** kasus provider empty-total → API mengembalikan error transparan (bukan sukses palsu);
kasus normal → tak berubah.
**Revert-if-worse:** bila kasus wajar salah ter-flag `failed` → perketat kondisi (hanya bila
`execution_memory` benar-benar kosong).

---

## 5. Strategi Pengujian (§1.6 — output nyata, banyak run)

Jalankan **≥3 run** per keyword pada set campuran (sertakan "MBG" yang non-deterministik):
- **(a)** run normal → sintesis lewat self-check (`"Final answer verified and produced"`).
- **(b)** run yang kebetulan empty → log menunjukkan `"retry"` lalu pulih **lewat self-check**, bukan
  `"synthesis retry"`.
- **(c)** provider empty persisten (bila bisa direproduksi) → `status=failed` transparan, bukan sukses palsu.
Metrik: proporsi `"synthesis retry"` **turun** mendekati nol; `"Final answer verified and produced"`
**naik**; tak ada `success` non-jawaban; latensi tetap wajar.

---

## 6. Verifikasi Akhir

- [ ] Empty (iter-1 & sintesis) **pulih otomatis** via retry (≥3 run, termasuk "MBG").
- [ ] Jawaban sintesis pulihan muncul lewat **self-check** (`"Final answer verified and produced"`), bukan
      `"synthesis retry"`.
- [ ] Tak ada `status=success` dengan non-jawaban; empty-total → `status=failed` + `ErrorReport`.
- [ ] `MAX_EMPTY_RETRY` menjaga latensi; tak ada loop tak berujung.
- [ ] Jaring `build_fallback_prompt` & fallback finalization **masih ada** sebagai lapisan akhir.
- [ ] Tak ada eksekusi tool ganda (retry hanya saat kosong, bukan saat ada `function_calls`).
- [ ] Rahasia teredaksi; error transparan (`ErrorReport` component+stage).

---

## 7. Risiko & Batasan (jujur)

- **RS1 — Empty di sisi provider TAK bisa dihilangkan.** Retry hanya membuatnya **pulih otomatis & tak
  terlihat**. Bila provider empty **persisten** (jarang), retry akhirnya menyerah → status jujur `failed`.
- **RS2 — Latensi ekstra saat empty.** Tiap retry = 1 panggilan tambahan (~200ms-beberapa detik). Dijaga
  `MAX_EMPTY_RETRY` kecil. Kasus umum (tak empty) tak terpengaruh.
- **RS3 — Delikat: logika loop.** Perubahan loop sesi ini pernah regresi. **Wajib** satu tahap sekali jalan
  + gerbang uji ≥3 run + revert-if-worse. Kerjakan Tahap 1 dulu, verifikasi, baru Tahap 2.
- **RS4 — Bukan pengganti kecerdasan.** Ini menutup empty; kualitas "kapan menggali/berhenti" tetap domain
  `plan-agen-lebih-cerdas.md` (kini efektif karena self-check konsisten berjalan).
- **Rujukan:** `docs/plan-fix-sintesis-tools-none-proaktif.md` (Tahap 1+2 prasyarat),
  `docs/plan-agen-lebih-cerdas.md` (self-check/refleksi), `docs/MASTER-plan-perbaikan-terpadu.md`,
  `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit `loop.py`, uji, commit) pada tahap terpisah, **satu
  tahap sekali jalan dengan gerbang uji & aturan revert**.
