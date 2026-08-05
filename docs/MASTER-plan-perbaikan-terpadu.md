# [MASTER] Roadmap Perbaikan Terpadu — Urutan Bertahap + Gerbang Uji + Anti-Pola

> **Dokumen MASTER untuk agen eksekutor.** Menggantikan pendekatan "jalankan plan satu per satu"
> yang terbukti **saling merusak** (churn TASK 1 mematikan recovery; enrich TASK memicu under-call).
> Bukan "ubah semua sekaligus" — melainkan **satu urutan aman** dengan **gerbang verifikasi** dan
> **aturan revert** di tiap langkah. Audit branch `retrofit/chat-completions-adapter` (2026-08-05).
> Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 0. Prinsip Kerja (WAJIB dibaca dulu)

1. **Satu langkah sekali jalan.** Terapkan **satu** langkah, lalu **uji**, baru lanjut. Jangan
   menumpuk beberapa perubahan sekaligus.
2. **Uji ≥3 run per langkah** (deepseek non-deterministik — 1 run tidak cukup). Bandingkan dengan
   baseline langkah sebelumnya.
3. **Aturan revert:** bila sebuah langkah membuat hasil **lebih buruk** dari baseline → **REVERT
   langkah itu** sebelum lanjut. Jangan menumpuk perbaikan di atas regresi.
4. **Ukur "lebih baik/buruk" dari OUTPUT nyata:** jawaban Postman (kelengkapan/relevansi) + status
   run, **bukan** dari kebersihan log. **Jangan tukar kualitas jawaban demi log yang bersih.**

---

## 1. Anti-Pola (pelajaran mahal sesi ini — JANGAN diulang)

| Anti-pola | Kenapa buruk | Aturan |
|---|---|---|
| Menghapus/mengurangi `reconnect+retry` MCP | Pada Verosight SSE, **retry = recovery**. Tanpa itu tool gagal permanen → jawaban buruk. | **Pertahankan retry.** Kurangi *pemicu* (timeout), bukan retry. |
| Menambah instruksi ke `system.md` | Prompt jadi bloat & **berbenturan** (kaya vs efisien) → deepseek under-call. | **Harmoniskan**, bukan tambah. Prompt pendek & konsisten. |
| **Menawarkan tools saat panggilan SINTESIS final** | deepseek bingung (panggil tool / jawab?) → balas **kosong** → fallback (bahkan pada payload kecil). | Saat model balas kosong, **retry `tools=none`** (Langkah 3). Fallback melewati self-check → memblokir 4b. |
| Menjejalkan data tool mentah penuh ke model | Konteks besar (~45k) memperparah empty & boros. | **Right-size** (buang noise, simpan fakta) untuk konteks model; data penuh tetap di storage. |
| Menyimpulkan dari 1 run | deepseek bervariasi run-to-run. | **Uji ≥3 run** sebelum menilai. |
| Mengerjakan 4a/4b sebelum Langkah 3 | Jawaban lari ke fallback → self-check tak jalan → perbaikan **percuma**. | **Urutan wajib:** Langkah 3 (unblocker) → 4a → 4b. |
| Self-check di jalur retry `tools=none` **membuang draft** saat `is_final=false` | Draft bagus dibuang → generic fallback (lebih buruk); sinyal "not final" percuma (tak bisa panggil tool). | **Terima draft retry** apa adanya; taruh "force tool" (4b) di jalur NORMAL (KOREKSI 3.1). |
| Menurunkan level log demi "bersih" | Menyembunyikan masalah nyata. | `session_not_found` tetap `warning` (jujur). |

---

## 2. Inventaris Masalah & Status

| # | Masalah | Status | Ditangani di Langkah |
|---|---|---|---|
| A | 500 (RunResult status, CancelledError), config deepseek, over-redaction, urutan pesan chat | ✅ **Selesai** | — |
| B | Churn/recovery rusak akibat churn TASK 1 | 🔴 **Regresi — revert dulu** | Langkah 1 |
| C | Tool analytics lambat (`get_topics` timeout 30s) | 🟡 Pending | Langkah 2 |
| D | **Empty-synthesis → fallback** (tools ditawarkan saat sintesis; juga konteks besar) + "(terpotong)" | 🔴 **UNBLOCKER** — blokir 4a/4b | **Langkah 3 (retry `tools=none`)** |
| E | Pemilihan tool tipis; agen menyangkal keterbatasan yang bisa diatasi tool | 🔴 **Regresi via enrich** | Langkah 4a (rata-rata) + **4b (jaminan)** |
| F | `session_not_found` (race SSE) | ⚪ **Tak bisa client-side** | Langkah 5 (eskalasi) |
| G | Variansi deepseek | ⚪ Tak bisa dihilangkan | Diterima (uji ≥3 run) |

---

## 3. Urutan Eksekusi (dengan gerbang uji)

### Langkah 1 — PULIHKAN baseline: revert churn TASK 1 (WAJIB, pertama)
- **Aksi:** kembalikan `app/mcp/client.py` ke perilaku **`reconnect+retry`** pada error tool
  (batalkan perubahan "jangan close/retry"). Detail & konteks: `docs/plan-fix-mcp-session-churn-mitigation.md`
  (versi terkoreksi — TASK 1 = *pertahankan* retry).
- **Gerbang uji (≥3 run, query campuran mis. "nuklir", "prabowo"):** semua run `200 OK`, tool
  **pulih** (tak ada tool gagal permanen), jawaban kembali **kaya** seperti sebelum regresi.
- **Revert-if-worse:** bila masih buruk, hentikan & laporkan — jangan lanjut.
- **Ekspektasi:** `session_not_found` (warning) **muncul lagi** — itu normal (harga recovery).

### Langkah 2 — Kurangi pemicu error: naikkan timeout tool lambat (AMAN)
- **Aksi:** naikkan `timeout_s` per-call MCP untuk analytics lambat (`get_topics`,
  `get_topic_sentiment`, `get_sentiment_trend`), mis. **30 → 60-90 dtk**; jaga di bawah
  `AGENT_RUN_TIMEOUT_S` (180). Ref: `docs/plan-fix-mcp-session-churn-mitigation.md` TASK 2.
- **Gerbang uji:** `get_topics`/`get_topic_sentiment` **sukses** (bukan timeout); jumlah
  `call_tool_error` turun; latensi masih wajar (<~120s).
- **Catatan:** ini mengurangi *pemicu* churn **tanpa** menyentuh retry. Jangan sekalian mematikan retry.

### Langkah 3 — FIX empty-synthesis (UNBLOCKER — WAJIB sebelum 4a/4b)
> **Penemuan 2026-08-05 (kritis):** jawaban sering datang dari **fallback** karena sintesis balas
> **kosong** ("LLM returned empty response → breaking to finalizer"). Fallback **melewati self-check
> (4b)** → **semua** perbaikan richness (4a/4b) **percuma** sampai ini beres. Langkah 3 adalah
> **prasyarat** Langkah 4a/4b. Terjadi **bahkan pada payload kecil** (bukan sekadar soal ukuran).

> **⚠️ KOREKSI 3.1 (2026-08-05, dari run "dokter") — retry BERHASIL tapi draft DIBUANG.** Setelah
> implementasi awal, ditemukan cacat interaksi: retry `tools=none` **menghasilkan draft bagus**,
> **lalu** kode menjalankan **self-check** di jalur retry; bila self-check `is_final=false`, kode
> **tidak menerima draft** (`loop.py:523` hanya `break` bila `is_final=True`) → jatuh ke "breaking to
> finalizer" → **draft dibuang → generic fallback** (lebih buruk). Padahal di jalur retry `tools=none`
> → **tak bisa** memanggil tool lagi, jadi sinyal "not final" **percuma**. Wajib dikoreksi (aksi #2 & #3).

- **Akar empty response:** panggilan sintesis masih **menawarkan tools** (`tools=32`) → deepseek
  bingung ("panggil tool lagi atau jawab?") → balas **kosong**. Bukti: panggilan fallback berhasil
  justru karena `tools=0`.
- **Aksi UTAMA (KODE — unblocker):** di `app/agent/loop.py`, saat model balas **kosong** (no tool
  calls, no text), **JANGAN langsung ke fallback**. **Retry sintesis dengan tools DIMATIKAN**
  (`tool_choice="none"` atau `tools=None`) → paksa deepseek menjawab dari konteks yang **sudah**
  terkumpul, di **jalur NORMAL** (yang menjalankan self-check 4b). Ini penyebab utama, bukan ukuran.
- **Aksi #2 (KODE — WAJIB, dari KOREKSI 3.1): jalur retry TERIMA draft-nya.** Di `app/agent/loop.py`,
  bila retry `tools=none` menghasilkan `output_text`, jadikan itu `final_answer` dan `break` —
  **JANGAN** menjalankan self-check yang bisa membuangnya, dan **JANGAN** jatuh ke generic fallback.
  Draft (data nyata) **selalu ≥** generic fallback. (Self-check di jalur ini percuma: `tools=none` →
  tak bisa memanggil tool lagi.)
- **Aksi #3 (KODE): pindahkan "force missing tool" (4b) ke jalur NORMAL, bukan jalur retry.** Logika
  "tolak keterbatasan yang bisa diatasi tool" hanya bermakna saat **tools masih tersedia** (respons
  teks di iterasi biasa) → paksa iterasi lagi memanggil tool yang kurang **sebelum** empty-synthesis.
  Di jalur retry (`tools=none`) mustahil, jadi jangan taruh di sana.
- **Aksi PELENGKAP (right-sizing — untuk kasus konteks besar):** kirim ke model **semua FAKTA**
  (`by_day`, agregat, nama+deskripsi topik) tetapi **buang NOISE** (isi post mentah/`media_urls`/field
  redundan); data penuh tetap di `tool_invocations.result`. Terapkan **juga pada sintesis PERTAMA**
  (bukan hanya retry) — bukti run "dokter": 7 tool → **132k token** → empty. Ref:
  `docs/plan-fix-synthesis-right-sizing.md`.
- **Gerbang uji (≥3 run):** jawaban dari **jalur NORMAL / retry-draft** (trace **TIDAK** memuat
  `Fallback answer generated`); draft retry **diterima** walau self-check `is_final=false`; memuat
  **semua** `by_day`; **tanpa** `*(terpotong)*`; `/tools` tetap data penuh.
- **Revert-if-worse:** bila fakta hilang / jawaban memburuk / loop tak berujung → revert.

### Langkah 4a — Harmoniskan prompt (menaikkan RATA-RATA, bukan jaminan)
- **Aksi:** rapikan `app/agent/prompts/system.md` — **hilangkan benturan** antara dorongan "kaya"
  (pakai `search_posts`/`semantic_search`, sentimen, baseline) vs "efisien/minimalis" ("utamakan
  tool murah", "hindari data berlebih"). Buat panduan **pendek, konsisten, kondisional yang jelas**.
  Ref: `docs/plan-enrich-answer-search-tools.md` (dengan koreksi: **harmonisasi, bukan penambahan**).
- **Gerbang uji (≥3 run, WAJIB banyak run karena varian):** rata-rata jawaban **≥** kualitas
  Langkah 3; **tidak** turun ke "hanya volume".
- **Revert-if-worse:** bila under-call kembali / prompt malah memperburuk → **revert ke prompt Langkah 3**.
- **Batas jujur:** prompt hanya menaikkan *rata-rata* — **tidak menjamin** kelengkapan tiap run
  (terbukti: tiap run menutupi dimensi berbeda). Jaminan ada di **Langkah 4b**.

### Langkah 4b — Self-check TOLAK "keterbatasan yang bisa diatasi tool" (JAMINAN — di KODE, Opsi A)
> **PRASYARAT: Langkah 3 sudah beres.** Self-check hanya jalan di **jalur NORMAL**; selama sintesis
> balas kosong → fallback, langkah ini **TIDAK PERNAH dijalankan**. Jangan kerjakan 4b sebelum
> Langkah 3 lulus gerbang uji.
- **Masalah yang diselesaikan:** jawaban sering menyatakan keterbatasan (mis. *"tidak tersedia
  sentimen / platform / konten"*) **padahal** tool yang bisa mengatasinya (`get_sentiment`,
  `search_posts`/`semantic_search`, `get_volume`) **belum dipanggil**. Ini keterbatasan yang
  diciptakan agen sendiri karena berhenti terlalu cepat — dan **prompt tak bisa menutupnya secara
  andal**.
- **Aksi (KODE, bukan prompt):** ubah langkah **self-check/finalize** (`app/agent/loop.py` +
  `app/agent/prompts/finalize.md`) agar `is_final=false` bila draf jawaban menyatakan keterbatasan
  yang **dapat diatasi tool yang belum dipanggil pada run ini**; set `missing` = tool tersebut →
  agen **wajib** memanggilnya sebelum finalisasi.
  - **Implementasi:** (1) beri self-check daftar tool yang **sudah** dipanggil (dari
    `execution_memory`) + daftar tool tersedia; (2) tambah aturan di `finalize.md`: *"Jika jawaban
    menyebut keterbatasan (sentimen/platform/konten/pembanding) yang bisa diperoleh dari tool yang
    BELUM dipanggil → `is_final=false`, `missing`=<nama tool>."* (3) Opsional lebih deterministik:
    deteksi frasa keterbatasan di draf + petakan ke tool belum-dipanggil di kode (lebih andal,
    lebih brittle).
  - **Guard anti-loop (WAJIB):** hormati `max_iterations`, `credits_budget`, dan no-progress
    detector yang sudah ada; **jangan** memaksa mengulang tool yang sudah **gagal/kosong**; jangan
    memaksa tool berbayar tanpa batas. Bila tool sudah dicoba & tetap tak memberi data → keterbatasan
    itu **sah** dan boleh final.
- **Gerbang uji (≥3 run):** jawaban **tidak lagi menyangkal** keterbatasan yang bisa diatasi; agen
  memanggil sentimen/konten/platform bila relevan sebelum menjawab; **tidak ada loop tak berujung**;
  biaya/latensi masih wajar.
- **Revert-if-worse:** bila memicu iterasi berlebih/loop atau biaya melonjak → revert / perlonggar
  aturan (mis. batasi hanya pada 1-2 tool inti seperti `get_sentiment`).
- **Catatan:** ini yang benar-benar menjawab keluhan "keterbatasan padahal ada tool-nya" — karena,
  tidak seperti prompt, ia **memaksa** di level kontrol (loop), bukan menyarankan.

### Langkah 5 — Eskalasi Verosight (di luar kode)
- **Aksi:** minta Verosight menyediakan endpoint **Streamable HTTP**. Ini **satu-satunya** cara
  `session_not_found` benar-benar nol. Ref: `docs/catatan/verosight-mcp-sse-only.md`,
  `docs/MCP-CHANGE_migrasi-clientside-fastmcp.md` §4A. Kode sudah siap (`VEROSIGHT_MCP_TRANSPORT=streamable_http`).

---

## 4. Target Realistis (kalibrasi ekspektasi)

- ✅ **Bisa dicapai:** run `200 OK`; tool pulih dari error transient; jawaban lengkap-fakta tanpa
  "(terpotong)". Dengan **Langkah 4b**, agen **memaksa** memakai tool yang relevan (sentimen/konten/
  platform) alih-alih menyangkalnya → kelengkapan jauh lebih **konsisten** (bukan sekadar rata-rata).
- ⚪ **TIDAK bisa dicapai (client-side):** `session_not_found` nol (butuh Verosight Streamable HTTP).
  Catatan: keterbatasan yang **memang sah** (tool sudah dicoba tapi datanya tak ada) tetap muncul —
  itu benar & jujur, bukan target untuk dihapus.
- ❌ **Jangan dikejar:** log yang benar-benar bersih dengan mengorbankan recovery/kualitas.

---

## 5. Ringkasan Urutan (satu tampilan)

```
Langkah 1: REVERT churn TASK 1 (pulihkan recovery)      ── gate: jawaban kaya lagi
   ↓ (uji ≥3 run, revert bila buruk)
Langkah 2: Naikkan timeout tool lambat                  ── gate: get_topics sukses
   ↓
Langkah 3: FIX empty-synthesis (retry tools=none)       ── UNBLOCKER: gate jawaban dari JALUR NORMAL
   ↓  (tanpa ini, 4a/4b percuma — jawaban lari ke fallback)  (bukan fallback), semua fakta, tanpa terpotong
Langkah 4a: Harmoniskan prompt (naikkan rata-rata)      ── gate: jawaban ≥ Langkah 3, tak under-call
   ↓
Langkah 4b: Self-check tolak keterbatasan-bisa-diatasi  ── gate: agen wajib pakai tool relevan,
            (KODE — JAMINAN, Opsi A)                        tak menyangkal; tanpa loop tak berujung
   ↓
Langkah 5: Eskalasi Verosight Streamable HTTP (eksternal)
```

---

## 6. Catatan & Rujukan

- **Dokumen ini = orkestrator.** "Cara" tiap langkah ada di plan detail; yang baru di sini adalah
  **urutan aman + gerbang uji + anti-pola** yang mencegah plan saling merusak.
- **Plan detail:** `plan-fix-mcp-session-churn-mitigation.md` (Langkah 1-2, versi terkoreksi),
  `plan-fix-synthesis-right-sizing.md` (Langkah 3), `plan-enrich-answer-search-tools.md` (Langkah 4a,
  dengan koreksi harmonisasi), `catatan/verosight-mcp-sse-only.md` (Langkah 5). **Langkah 4b
  (self-check kode)** dirumuskan di dokumen ini — belum ada plan detail terpisah.
- **Superseded/riwayat:** `plan-fix-empty-synthesis-and-fallback.md`, `plan-fix-truncated-answer-fallback-summary.md`.
- **Aturan global:** `docs/PROMPT_PLAN.md` §1.6 & §6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit kode/prompt, uji, commit) pada tahap terpisah,
  **satu langkah sekali jalan dengan gerbang uji**.
