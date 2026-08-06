# [KEYWORD-400] Plan — Keyword Tunggal untuk Verosight (hindari 400 multi-kata)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: mencegah kegagalan `HTTP 400` saat model memakai
> keyword **multi-kata/ber-spasi** yang ditolak Verosight, dengan mengarahkan model memakai **keyword
> tunggal/inti**. Audit branch `retrofit/chat-completions-adapter` (2026-08-06). Patuhi
> `docs/PROMPT_PLAN.md` §1.6. **Disiplin anti-regresi:** perubahan prompt = uji ≥3 run + reversibel via git;
> perubahan kode = test + commit.

---

## 1. Akar Masalah (TERBUKTI via probe langsung MCP, 2026-08-06)

| Keyword | Hasil |
|---|---|
| `"IKN Indonesia"` (2 kata, spasi) | ❌ `HTTP 400 Bad Request` |
| `"ibu kota nusantara"` (3 kata) | ❌ `HTTP 400` |
| `"IKN"` / `"ikn"` / `"nuklir"` (1 kata) | ✅ data kaya (`"IKN"` → 437 post/hari, 9 juta views) |

**Verosight menolak keyword ber-spasi/multi-kata.** Pada run `4f838bc8` (query "IKN Indonesia"), model
memilih `keyword="IKN Indonesia"` → ketiga tool (`get_keyword_volume`, `get_sentiment`, `search_posts`)
kena 400 → jawaban jujur "tidak dapat diverifikasi". **Datanya ADA** (`"IKN"` sangat aktif); yang salah
hanya **format keyword** yang model pilih (meniru frasa pertanyaan). Bukan bug LLM/kode kita. Lihat memori
`verosight-keyword-and-tool-quirks`.

---

## 2. Tahapan (SATU tahap sekali jalan)

### Tahap 1 — Panduan prompt: keyword tunggal (INTI, prompt-only, reversibel)
**File:** `app/agent/prompts/system.md`.
Tambah panduan (di "Panduan memakai tool pencarian"):
> "Untuk parameter `keyword`/`query` Verosight: gunakan **satu kata/entitas inti**, mis. `IKN` (bukan
> `IKN Indonesia`), `nuklir` (bukan `senjata nuklir`). Keyword **multi-kata/ber-spasi ditolak Verosight
> (HTTP 400)**. Ekstrak kata kunci utama dari pertanyaan pengguna sebelum memanggil tool."
- **Gerbang uji (≥3 run):** query multi-kata (mis. "apakah IKN Indonesia sering dibicarakan?", "topik ibu
  kota nusantara") → model memanggil tool dengan keyword **tunggal** (`IKN`) → `data` sukses (bukan 400) →
  jawaban kaya. Query satu kata tetap jalan seperti biasa.
- **Revert-if-worse:** bila keyword tunggal terlalu luas / kehilangan makna untuk sebagian query → longgarkan
  atau revert via `git checkout ... system.md`.

### Tahap 2 (opsional) — Recovery deterministik: retry keyword sederhana bila semua tool 400
**File:** `app/agent/loop.py` dan/atau `app/agent/prompts/reflection.md`.
- **Alasan:** Tahap 1 bergantung kepatuhan model. Backstop: bila **semua** tool untuk satu keyword
  mengembalikan error 400, picu **refleksi** yang menyarankan mengulang dengan keyword disederhanakan
  (kata pertama / entitas inti), atau normalisasi di executor (ambil token paling menonjol).
- **Caveat (RS):** "kata mana yang dipertahankan" itu **heuristik** — bisa salah pilih. Karena itu opsional
  & sesudah Tahap 1 terbukti kurang.
- **Gerbang uji:** query multi-kata yang model tetap paksakan multi-kata → sistem menyederhanakan → data
  sukses. **Revert-if-worse:** bila salah menyederhanakan → perketat/hapus.

### Tahap 3 (opsional) — Observability: tool_result cerminkan is_error
**File:** `app/tools/executor.py` (atau tempat event `tool_result` dibuat).
- **Masalah:** event `Executed tool → success` tetap muncul walau konten MCP `is_error=True` (Verosight 400).
  `memory_write` sudah benar `status='error'`, jadi agen fungsional tahu gagal — tapi log menyesatkan.
- **Perubahan:** bila `CallToolResult.is_error` (atau konten memuat pola error API) → set `status=error`
  pada event `tool_result` juga. **Test:** unit test kecil menegaskan tool yang kontennya error tercatat
  `status=error` di event, bukan hanya di memory.

---

## 3. Verifikasi Akhir

- [ ] Query multi-kata → model pakai keyword tunggal → tool sukses (bukan 400) → jawaban kaya (≥3 run).
- [ ] Query satu kata tetap jalan; tak ada regresi.
- [ ] (Tahap 2 bila dikerjakan) semua-400 memicu recovery keyword sederhana.
- [ ] (Tahap 3 bila dikerjakan) event `tool_result` = `status=error` saat konten MCP error; suite hijau.
- [ ] Reversibel: revert prompt via `git checkout`; rahasia teredaksi.

---

## 4. Risiko & Batasan (jujur)

- **RS1 — Plafon prompt.** Model bisa tetap memakai keyword multi-kata. Bila sering diabaikan → Tahap 2
  (deterministik) atau sinyal keterbatasan model.
- **RS2 — Kehilangan spesifisitas.** `"IKN"` lebih luas dari `"IKN Indonesia"`, tapi Verosight mencocokkan
  kata kunci apa adanya — keyword tunggal justru yang berhasil. Untuk query yang butuh frasa spesifik,
  substansi tetap didapat via `search_posts` (konten post).
- **RS3 — Tahap 2 heuristik.** Menyederhanakan keyword = menebak kata inti; bisa salah. Opsional & hati-hati.
- **RS4 — Ini quirk VEROSIGHT (data source), bukan LLM.** Berlaku untuk model apa pun (termasuk bila
  pindah ke Claude/Jalur B). Perbaikan ini tetap relevan lintas-model.
- **Rujukan:** memori `verosight-keyword-and-tool-quirks`, `docs/plan-fix-platform-overconstraint-dead-tool.md`,
  `docs/plan-fix-tanggal-otomatis-sistem.md`, `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit prompt/kode, uji ≥3 run, commit) pada tahap terpisah.
